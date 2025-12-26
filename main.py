import asyncio
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple

import pytz
from dotenv import load_dotenv
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.constants import ChatType
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    ChatMemberHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from bonbast_client import (
    BonbastClient,
    ITEMS,
    CURRENCY_KEYS_ORDER,
    COIN_KEYS_ORDER,
    METAL_KEYS_ORDER,
    ItemDef,
)
from storage import Storage

RLM = "\u200F"
TEHRAN_TZ = pytz.timezone("Asia/Tehran")


def parse_admin_ids(s: str) -> List[int]:
    parts = re.split(r"[,\s]+", (s or "").strip())
    out = []
    for p in parts:
        if p.strip().isdigit():
            out.append(int(p.strip()))
    return out


def default_config() -> Dict[str, Any]:
    return {
        "auto_send": False,
        "interval_min": 5,
        "quiet": [],  # list of ["HH:MM","HH:MM"]
        "only_on_change": False,
        "threshold": 0.0,
        "mode": "sell",  # sell | buy
        "send_mode": "post",  # post | edit
        "show_arrows": True,
        "selected": {
            "currencies": ["USD", "EUR", "GBP"],
            "coins": ["EMAMI", "AZADI", "NIM", "ROB", "GERAMI"],
            "metals": ["MITHQAL", "GOL18", "OUNCE", "BTC"],
        },
        "triggers": [],  # empty => any selected triggers
    }


HELP_TEXT = (
    "راهنما (Help)\n\n"
    "✅ /panel\n"
    "پنل مدیریت را باز می‌کند. ابتدا یک چت (گروه/کانال) را انتخاب کنید.\n\n"
    "✅ وضعیت (تایید/لغو)\n"
    "تا وقتی تایید نکنید، بات در آن چت هیچ پیام خودکاری ارسال نمی‌کند.\n\n"
    "✅ ارسال خودکار\n"
    "اگر فعال باشد، بات طبق Interval پیام می‌فرستد.\n\n"
    "✅ زمان‌بندی (Interval)\n"
    "مثال: اگر 5 دقیقه باشد، ارسال‌ها دقیقاً روی 12:00، 12:05، 12:10 ... انجام می‌شود.\n\n"
    "✅ ساعات سکوت\n"
    "مثال: 23:00-07:30 یا چند بازه: 12:00-13:00,23:00-07:30\n\n"
    "✅ فقط در صورت تغییر\n"
    "اگر فعال باشد فقط وقتی تغییر رخ دهد ارسال می‌کند.\n\n"
    "✅ Triggers\n"
    "مشخص می‌کنید تغییر کدام آیتم‌ها ارسال را فعال کند (مثلاً فقط USD).\n\n"
    "✅ Sell/Buy\n"
    "قیمت Sell یا Buy را انتخاب می‌کنید.\n\n"
    "✅ Threshold\n"
    "حداقل مقدار تغییر برای ارسال (مثال: 100 برای ارزها). 0 یعنی هر تغییر.\n\n"
    "✅ حالت ارسال\n"
    "Post: هر بار پیام جدید\n"
    "Edit: یک پیام را ویرایش می‌کند (برای جلوگیری از اسپم)\n\n"
    "✅ ارسال فوری (Send now)\n"
    "همین الان پیام را ارسال می‌کند.\n\n"
    "✅ Export config\n"
    "تنظیمات همان چت را به صورت JSON می‌فرستد.\n"
)


def is_admin(update: Update, admin_ids: List[int]) -> bool:
    uid = update.effective_user.id if update.effective_user else None
    return uid in admin_ids


def fmt_status(approved: bool) -> str:
    return "✅ تایید" if approved else "⏳ نیاز به تایید"


def fmt_toggle(on: bool) -> str:
    return "✅" if on else "❌"


def in_quiet(now_hm: str, quiet_ranges: List[List[str]]) -> bool:
    # now_hm is "HH:MM"
    h, m = map(int, now_hm.split(":"))
    now_min = h * 60 + m

    for r in quiet_ranges:
        if len(r) != 2:
            continue
        sh, sm = map(int, r[0].split(":"))
        eh, em = map(int, r[1].split(":"))
        smin = sh * 60 + sm
        emin = eh * 60 + em
        if smin <= emin:
            if smin <= now_min < emin:
                return True
        else:
            # crosses midnight
            if now_min >= smin or now_min < emin:
                return True
    return False


def parse_quiet_ranges(text: str) -> List[List[str]]:
    # "23:00-07:30,12:00-13:00"
    out: List[List[str]] = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" not in part:
            continue
        a, b = [x.strip() for x in part.split("-", 1)]
        if re.match(r"^\d{1,2}:\d{2}$", a) and re.match(r"^\d{1,2}:\d{2}$", b):
            ah, am = a.split(":")
            bh, bm = b.split(":")
            out.append([f"{int(ah):02d}:{am}", f"{int(bh):02d}:{bm}"])
    return out


class DataCache:
    def __init__(self) -> None:
        self.json_data: Optional[Dict[str, Any]] = None
        self.signs: Dict[str, int] = {}  # item-key => -1/0/+1
        self.prev_values: Dict[str, float] = {}
        self.first: bool = True

    def update(self, json_data: Dict[str, Any], mode: str) -> None:
        signs: Dict[str, int] = {}
        new_prev: Dict[str, float] = dict(self.prev_values)

        for key, item in ITEMS.items():
            v = BonbastClient.get_value_float(item, json_data, mode)
            if v is None:
                continue
            if key in self.prev_values:
                pv = self.prev_values[key]
                if v > pv:
                    signs[key] = 1
                elif v < pv:
                    signs[key] = -1
                else:
                    signs[key] = 0
            new_prev[key] = v

        self.json_data = json_data
        self.signs = signs
        self.prev_values = new_prev


def build_panel_keyboard(chat: Dict[str, Any]) -> InlineKeyboardMarkup:
    cfg = chat["config"]
    approved = chat["approved"]

    auto = bool(cfg.get("auto_send", False))
    onlychg = bool(cfg.get("only_on_change", False))
    mode = cfg.get("mode", "sell")
    send_mode = cfg.get("send_mode", "post")
    show_arrows = bool(cfg.get("show_arrows", True))

    rows = [
        [
            InlineKeyboardButton(f"{fmt_status(approved)}", callback_data=f"chat:approve:{chat['chat_id']}"),
            InlineKeyboardButton(f"ارسال خودکار: {fmt_toggle(auto)}", callback_data=f"chat:auto:{chat['chat_id']}"),
        ],
        [
            InlineKeyboardButton("انتخاب ارزها", callback_data=f"pick:curr:{chat['chat_id']}:0"),
            InlineKeyboardButton("انتخاب سکه‌ها", callback_data=f"pick:coin:{chat['chat_id']}:0"),
        ],
        [
            InlineKeyboardButton("طلا / بیتکوین", callback_data=f"pick:metal:{chat['chat_id']}:0"),
        ],
        [
            InlineKeyboardButton("زمان‌بندی (Interval)", callback_data=f"set:interval:{chat['chat_id']}"),
            InlineKeyboardButton("ساعات سکوت", callback_data=f"set:quiet:{chat['chat_id']}"),
        ],
        [
            InlineKeyboardButton(f"فقط در صورت تغییر: {fmt_toggle(onlychg)}", callback_data=f"chat:onlychg:{chat['chat_id']}"),
            InlineKeyboardButton("تریگرها (Triggers)", callback_data=f"pick:trig:{chat['chat_id']}:0"),
        ],
        [
            InlineKeyboardButton(f"حالت ارسال: {send_mode.upper()}", callback_data=f"chat:sendmode:{chat['chat_id']}"),
            InlineKeyboardButton(f"Sell/Buy: {mode.upper()}", callback_data=f"chat:mode:{chat['chat_id']}"),
        ],
        [
            InlineKeyboardButton(f"Threshold", callback_data=f"set:threshold:{chat['chat_id']}"),
            InlineKeyboardButton("ارسال فوری (Send now)", callback_data=f"sendnow:{chat['chat_id']}"),
        ],
        [
            InlineKeyboardButton("Export config", callback_data=f"export:{chat['chat_id']}"),
            InlineKeyboardButton(f"فلش‌ها: {fmt_toggle(show_arrows)}", callback_data=f"chat:arrows:{chat['chat_id']}"),
        ],
        [
            InlineKeyboardButton("Help", callback_data="help"),
            InlineKeyboardButton("⬅️ برگشت", callback_data="panel:back"),
        ],
    ]
    return InlineKeyboardMarkup(rows)


def build_chat_list_keyboard(chats: List[Dict[str, Any]]) -> InlineKeyboardMarkup:
    rows = []
    for c in chats:
        status_icon = "✅" if c["approved"] else "⏳"
        title = c["title"]
        rows.append([InlineKeyboardButton(f"{status_icon} {title}", callback_data=f"panel:select:{c['chat_id']}")])
    return InlineKeyboardMarkup(rows or [[InlineKeyboardButton("هیچ چتی نیست", callback_data="noop")]])


def build_picker(kind: str, chat_id: int, cfg: Dict[str, Any], page: int) -> InlineKeyboardMarkup:
    selected = cfg.get("selected", {})
    chosen: List[str] = list(selected.get({"curr": "currencies", "coin": "coins", "metal": "metals", "trig": "triggers"}[kind], []))

    if kind == "curr":
        keys = CURRENCY_KEYS_ORDER
        per_page = 14  # 7 rows x 2
    elif kind == "coin":
        keys = COIN_KEYS_ORDER
        per_page = 10
    elif kind == "metal":
        keys = METAL_KEYS_ORDER
        per_page = 10
    else:  # triggers: choose among currently selected items (flatten)
        cur = selected.get("currencies", [])
        coin = selected.get("coins", [])
        metal = selected.get("metals", [])
        keys = list(cur) + list(coin) + list(metal)
        per_page = 14

    total_pages = max(1, (len(keys) + per_page - 1) // per_page)
    page = max(0, min(page, total_pages - 1))
    start = page * per_page
    chunk = keys[start : start + per_page]

    buttons: List[List[InlineKeyboardButton]] = []
    row: List[InlineKeyboardButton] = []
    for k in chunk:
        item = ITEMS.get(k)
        if not item:
            continue
        on = k in chosen
        label = f"{item.name_fa} {'✅' if on else '❌'}"
        row.append(InlineKeyboardButton(label, callback_data=f"pick:toggle:{kind}:{chat_id}:{k}:{page}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    nav = []
    if total_pages > 1:
        nav.append(InlineKeyboardButton("◀️", callback_data=f"pick:{kind}:{chat_id}:{page-1}"))
        nav.append(InlineKeyboardButton(f"{page+1}/{total_pages}", callback_data="noop"))
        nav.append(InlineKeyboardButton("▶️", callback_data=f"pick:{kind}:{chat_id}:{page+1}"))
        buttons.append(nav)

    if kind == "curr":
        buttons.append([InlineKeyboardButton("✏️ تنظیم ترتیب (Order)", callback_data=f"set:order:{chat_id}")])

    buttons.append([InlineKeyboardButton("✅ ذخیره", callback_data=f"pick:done:{kind}:{chat_id}")])
    buttons.append([InlineKeyboardButton("⬅️ برگشت", callback_data=f"panel:select:{chat_id}")])
    return InlineKeyboardMarkup(buttons)


def build_message(cfg: Dict[str, Any], json_data: Dict[str, Any], signs: Dict[str, int], first_post_no_arrow: bool) -> str:
    mode = cfg.get("mode", "sell")
    show_arrows = bool(cfg.get("show_arrows", True))

    selected = cfg.get("selected", {})
    curr = selected.get("currencies", [])
    coin = selected.get("coins", [])
    metal = selected.get("metals", [])

    def arrow_for(k: str) -> str:
        if first_post_no_arrow:
            return ""
        if not show_arrows:
            return ""
        s = signs.get(k, 0)
        if s > 0:
            return " ▲"
        if s < 0:
            return " 🔻"
        return ""  # same => empty (as you requested)

    lines: List[str] = []

    # currencies
    for k in curr:
        item = ITEMS.get(k)
        if not item:
            continue
        price = BonbastClient.fmt_value(item, json_data, mode)
        if not price:
            continue
        lines.append(f"{RLM}{item.emoji} {item.name_fa} {price}{arrow_for(k)}")

    lines.append(f"{RLM}_______________________")

    # coins
    for k in coin:
        item = ITEMS.get(k)
        if not item:
            continue
        price = BonbastClient.fmt_value(item, json_data, mode)
        if not price:
            continue
        lines.append(f"{RLM}{item.emoji} {item.name_fa} {price}{arrow_for(k)}")

    lines.append(f"{RLM}_______________________")

    # metals + btc
    for k in metal:
        item = ITEMS.get(k)
        if not item:
            continue
        price = BonbastClient.fmt_value(item, json_data, mode)
        if not price:
            continue
        lines.append(f"{RLM}{item.emoji} {item.name_fa} {price}{arrow_for(k)}")

    lines.append(f"{RLM}_______________________")

    date, t = BonbastClient.extract_datetime(json_data)
    lines.append(f"{RLM}{date} - {t}")
    return "\n".join(lines)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    admin_ids = context.application.bot_data["admin_ids"]
    if not is_admin(update, admin_ids):
        return
    await update.effective_message.reply_text(
        "سلام!\nبرای مدیریت: /panel\nبرای ثبت چت فعلی: /register\nبرای راهنما: /help"
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    admin_ids = context.application.bot_data["admin_ids"]
    if not is_admin(update, admin_ids):
        return
    await update.effective_message.reply_text(HELP_TEXT)


async def cmd_register(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    admin_ids = context.application.bot_data["admin_ids"]
    if not is_admin(update, admin_ids):
        return
    st: Storage = context.application.bot_data["storage"]
    chat = update.effective_chat
    title = chat.title or chat.username or str(chat.id)
    st.upsert_chat(chat.id, title, chat.type)
    # Ensure config exists
    rec = st.get_chat(chat.id)
    cfg = rec["config"] if rec else {}
    if not cfg:
        cfg = default_config()
        st.save_config(chat.id, cfg)
    await update.effective_message.reply_text(f"✅ ثبت شد: {title}\nوضعیت: {fmt_status(rec['approved'] if rec else False)}")


async def cmd_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    admin_ids = context.application.bot_data["admin_ids"]
    if not is_admin(update, admin_ids):
        return
    st: Storage = context.application.bot_data["storage"]
    chats = st.list_chats()
    await update.effective_message.reply_text(
        "Select a chat to manage:",
        reply_markup=build_chat_list_keyboard(chats),
    )


async def on_my_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    st: Storage = context.application.bot_data["storage"]
    admin_ids: List[int] = context.application.bot_data["admin_ids"]
    chat = update.effective_chat
    title = chat.title or chat.username or str(chat.id)

    new_status = update.my_chat_member.new_chat_member.status
    old_status = update.my_chat_member.old_chat_member.status

    # Added
    if old_status in ("left", "kicked") and new_status in ("member", "administrator"):
        st.upsert_chat(chat.id, title, chat.type)
        rec = st.get_chat(chat.id)
        if rec and not rec["config"]:
            st.save_config(chat.id, default_config())

        # Notify admins (in private)
        for aid in admin_ids:
            try:
                await context.bot.send_message(
                    chat_id=aid,
                    text=f"➕ بات به چت جدید اضافه شد:\n{title}\nID: {chat.id}\nبرای تایید/تنظیمات: /panel",
                )
            except Exception:
                pass

    # Removed
    if new_status in ("left", "kicked"):
        logging.getLogger("bonbast-bot").info("Bot removed from chat %s (%s)", title, chat.id)


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    admin_ids = context.application.bot_data["admin_ids"]
    if not is_admin(update, admin_ids):
        return

    st: Storage = context.application.bot_data["storage"]
    data_cache: DataCache = context.application.bot_data["data_cache"]

    data = q.data or ""
    if data == "noop":
        return
    if data == "help":
        await q.message.reply_text(HELP_TEXT)
        return
    if data == "panel:back":
        chats = st.list_chats()
        await q.message.edit_text("Select a chat to manage:", reply_markup=build_chat_list_keyboard(chats))
        return

    if data.startswith("panel:select:"):
        chat_id = int(data.split(":")[-1])
        rec = st.get_chat(chat_id)
        if not rec:
            await q.message.reply_text("چت پیدا نشد.")
            return
        cfg = rec["config"] or default_config()
        if not rec["config"]:
            st.save_config(chat_id, cfg)
        await q.message.edit_text(
            f"Control Panel: {rec['title']} ({chat_id})",
            reply_markup=build_panel_keyboard(rec),
        )
        return

    if data.startswith("chat:approve:"):
        chat_id = int(data.split(":")[-1])
        rec = st.get_chat(chat_id)
        if not rec:
            return
        st.set_approved(chat_id, not rec["approved"])
        rec = st.get_chat(chat_id)
        await q.message.edit_text(
            f"Control Panel: {rec['title']} ({chat_id})",
            reply_markup=build_panel_keyboard(rec),
        )
        return

    if data.startswith("chat:auto:"):
        chat_id = int(data.split(":")[-1])
        rec = st.get_chat(chat_id)
        if not rec:
            return
        cfg = rec["config"] or default_config()
        cfg["auto_send"] = not bool(cfg.get("auto_send", False))
        st.save_config(chat_id, cfg)
        rec = st.get_chat(chat_id)
        await q.message.edit_text(
            f"Control Panel: {rec['title']} ({chat_id})",
            reply_markup=build_panel_keyboard(rec),
        )
        return

    if data.startswith("chat:onlychg:"):
        chat_id = int(data.split(":")[-1])
        rec = st.get_chat(chat_id)
        if not rec:
            return
        cfg = rec["config"] or default_config()
        cfg["only_on_change"] = not bool(cfg.get("only_on_change", False))
        st.save_config(chat_id, cfg)
        rec = st.get_chat(chat_id)
        await q.message.edit_text(
            f"Control Panel: {rec['title']} ({chat_id})",
            reply_markup=build_panel_keyboard(rec),
        )
        return

    if data.startswith("chat:mode:"):
        chat_id = int(data.split(":")[-1])
        rec = st.get_chat(chat_id)
        if not rec:
            return
        cfg = rec["config"] or default_config()
        cfg["mode"] = "buy" if cfg.get("mode") == "sell" else "sell"
        st.save_config(chat_id, cfg)
        rec = st.get_chat(chat_id)
        await q.message.edit_text(
            f"Control Panel: {rec['title']} ({chat_id})",
            reply_markup=build_panel_keyboard(rec),
        )
        return

    if data.startswith("chat:sendmode:"):
        chat_id = int(data.split(":")[-1])
        rec = st.get_chat(chat_id)
        if not rec:
            return
        cfg = rec["config"] or default_config()
        cfg["send_mode"] = "edit" if cfg.get("send_mode") == "post" else "post"
        st.save_config(chat_id, cfg)
        rec = st.get_chat(chat_id)
        await q.message.edit_text(
            f"Control Panel: {rec['title']} ({chat_id})",
            reply_markup=build_panel_keyboard(rec),
        )
        return

    if data.startswith("chat:arrows:"):
        chat_id = int(data.split(":")[-1])
        rec = st.get_chat(chat_id)
        if not rec:
            return
        cfg = rec["config"] or default_config()
        cfg["show_arrows"] = not bool(cfg.get("show_arrows", True))
        st.save_config(chat_id, cfg)
        rec = st.get_chat(chat_id)
        await q.message.edit_text(
            f"Control Panel: {rec['title']} ({chat_id})",
            reply_markup=build_panel_keyboard(rec),
        )
        return

    if data.startswith("export:"):
        chat_id = int(data.split(":")[-1])
        rec = st.get_chat(chat_id)
        if not rec:
            return
        await q.message.reply_text(
            f"Export config for {rec['title']} ({chat_id}):\n```json\n{json.dumps(rec['config'], ensure_ascii=False, indent=2)}\n```",
            parse_mode="Markdown",
        )
        return

    if data.startswith("set:interval:"):
        chat_id = int(data.split(":")[-1])
        context.user_data["awaiting"] = ("interval", chat_id)
        await q.message.reply_text("عدد Interval را به دقیقه بفرستید (مثال: 5 یا 10 یا 15):")
        return

    if data.startswith("set:threshold:"):
        chat_id = int(data.split(":")[-1])
        context.user_data["awaiting"] = ("threshold", chat_id)
        await q.message.reply_text("Threshold را بفرستید (مثال: 100). 0 یعنی هر تغییر:")
        return

    if data.startswith("set:quiet:"):
        chat_id = int(data.split(":")[-1])
        context.user_data["awaiting"] = ("quiet", chat_id)
        await q.message.reply_text("ساعات سکوت را بفرستید. مثال:\n23:00-07:30\nیا چند بازه:\n12:00-13:00,23:00-07:30")
        return

    if data.startswith("set:order:"):
        chat_id = int(data.split(":")[-1])
        context.user_data["awaiting"] = ("order", chat_id)
        await q.message.reply_text("ترتیب ارزها را با کُدها بفرستید (با فاصله). مثال:\nUSD EUR GBP CHF AED")
        return

    # Picker open page: pick:<kind>:<chat_id>:<page>
    if data.startswith("pick:curr:") or data.startswith("pick:coin:") or data.startswith("pick:metal:") or data.startswith("pick:trig:"):
        _, kind, chat_id, page = data.split(":")
        chat_id = int(chat_id)
        page = int(page)
        rec = st.get_chat(chat_id)
        if not rec:
            return
        cfg = rec["config"] or default_config()
        await q.message.edit_text(
            "انتخاب کنید:",
            reply_markup=build_picker(kind, chat_id, cfg, page),
        )
        return

    # Toggle: pick:toggle:<kind>:<chat_id>:<key>:<page>
    if data.startswith("pick:toggle:"):
        _, _, kind, chat_id, key, page = data.split(":")
        chat_id = int(chat_id)
        page = int(page)
        rec = st.get_chat(chat_id)
        if not rec:
            return
        cfg = rec["config"] or default_config()
        sel = cfg.setdefault("selected", {})
        if kind == "trig":
            lst = cfg.setdefault("triggers", [])
        else:
            map_name = {"curr": "currencies", "coin": "coins", "metal": "metals"}[kind]
            lst = sel.setdefault(map_name, [])
        if key in lst:
            lst.remove(key)
        else:
            lst.append(key)
        st.save_config(chat_id, cfg)
        await q.message.edit_text("انتخاب کنید:", reply_markup=build_picker(kind, chat_id, cfg, page))
        return

    # Done: pick:done:<kind>:<chat_id>
    if data.startswith("pick:done:"):
        _, _, kind, chat_id = data.split(":")
        chat_id = int(chat_id)
        rec = st.get_chat(chat_id)
        if not rec:
            return
        await q.message.edit_text(
            f"Control Panel: {rec['title']} ({chat_id})",
            reply_markup=build_panel_keyboard(rec),
        )
        return

    if data.startswith("sendnow:"):
        chat_id = int(data.split(":")[-1])
        rec = st.get_chat(chat_id)
        if not rec:
            return
        await send_to_chat(context.application, chat_id, force=True)
        await q.message.reply_text("✅ ارسال شد.")
        return


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    admin_ids = context.application.bot_data["admin_ids"]
    if not is_admin(update, admin_ids):
        return

    awaiting = context.user_data.get("awaiting")
    if not awaiting:
        return

    st: Storage = context.application.bot_data["storage"]
    kind, chat_id = awaiting
    text = (update.effective_message.text or "").strip()

    rec = st.get_chat(chat_id)
    if not rec:
        context.user_data.pop("awaiting", None)
        return

    cfg = rec["config"] or default_config()

    if kind == "interval":
        try:
            v = int(text)
            if v < 1 or v > 360:
                raise ValueError()
            cfg["interval_min"] = v
            st.save_config(chat_id, cfg)
            await update.effective_message.reply_text("✅ Interval ذخیره شد.")
        except Exception:
            await update.effective_message.reply_text("❌ عدد معتبر نیست. مثال: 5")
    elif kind == "threshold":
        try:
            v = float(text.replace(",", ""))
            if v < 0:
                raise ValueError()
            cfg["threshold"] = v
            st.save_config(chat_id, cfg)
            await update.effective_message.reply_text("✅ Threshold ذخیره شد.")
        except Exception:
            await update.effective_message.reply_text("❌ عدد معتبر نیست. مثال: 100")
    elif kind == "quiet":
        ranges = parse_quiet_ranges(text)
        cfg["quiet"] = ranges
        st.save_config(chat_id, cfg)
        await update.effective_message.reply_text("✅ ساعات سکوت ذخیره شد.")
    elif kind == "order":
        parts = re.split(r"[,\s]+", text.upper().strip())
        parts = [p for p in parts if p]
        valid = [p for p in parts if p in ITEMS and ITEMS[p].kind == "currency"]
        if not valid:
            await update.effective_message.reply_text("❌ کُدها معتبر نیستند. مثال: USD EUR GBP CHF")
        else:
            # Keep only selected currencies but reorder; add missing selected at end
            sel = cfg.setdefault("selected", {}).setdefault("currencies", [])
            new_order = []
            for p in valid:
                if p in sel and p not in new_order:
                    new_order.append(p)
            for p in sel:
                if p not in new_order:
                    new_order.append(p)
            cfg["selected"]["currencies"] = new_order
            st.save_config(chat_id, cfg)
            await update.effective_message.reply_text("✅ ترتیب ارزها ذخیره شد.")
    context.user_data.pop("awaiting", None)


async def send_to_chat(app: Application, chat_id: int, force: bool = False) -> None:
    st: Storage = app.bot_data["storage"]
    client: BonbastClient = app.bot_data["client"]
    cache: DataCache = app.bot_data["data_cache"]

    rec = st.get_chat(chat_id)
    if not rec:
        return

    if not rec["approved"]:
        return

    cfg = rec["config"] or default_config()

    # fetch fresh
    json_data = await client.fetch_json()
    cache.update(json_data, cfg.get("mode", "sell"))

    state = rec["state"] or {}
    last_sent_vals = state.get("last_sent_vals", {})
    last_message_id = state.get("last_message_id")
    first_post_done = bool(state.get("first_post_done", False))

    msg = build_message(cfg, json_data, cache.signs, first_post_no_arrow=(not first_post_done))
    # change detection
    if cfg.get("only_on_change") and not force:
        threshold = float(cfg.get("threshold", 0.0) or 0.0)
        triggers = cfg.get("triggers", [])
        if not triggers:
            # any selected item
            selected = cfg.get("selected", {})
            triggers = list(selected.get("currencies", [])) + list(selected.get("coins", [])) + list(selected.get("metals", []))

        changed = False
        for k in triggers:
            item = ITEMS.get(k)
            if not item:
                continue
            cur = BonbastClient.get_value_float(item, json_data, cfg.get("mode", "sell"))
            prev = last_sent_vals.get(k)
            if cur is None:
                continue
            if prev is None:
                changed = True
                break
            if abs(cur - float(prev)) >= threshold:
                changed = True
                break
        if not changed:
            return

    # save new sent snapshot
    new_sent_vals: Dict[str, Any] = {}
    for k, item in ITEMS.items():
        v = BonbastClient.get_value_float(item, json_data, cfg.get("mode", "sell"))
        if v is not None:
            new_sent_vals[k] = v

    send_mode = cfg.get("send_mode", "post")
    if send_mode == "edit" and last_message_id:
        try:
            await app.bot.edit_message_text(
                chat_id=chat_id,
                message_id=int(last_message_id),
                text=msg,
                disable_web_page_preview=True,
            )
        except Exception:
            sent = await app.bot.send_message(chat_id=chat_id, text=msg, disable_web_page_preview=True)
            last_message_id = sent.message_id
    else:
        sent = await app.bot.send_message(chat_id=chat_id, text=msg, disable_web_page_preview=True)
        last_message_id = sent.message_id

    state["last_sent_vals"] = new_sent_vals
    state["last_message_id"] = last_message_id
    state["first_post_done"] = True
    st.save_state(chat_id, state)


async def sender_loop(app: Application) -> None:
    st: Storage = app.bot_data["storage"]
    client: BonbastClient = app.bot_data["client"]

    while True:
        try:
            chats = st.list_chats()
            now = pytz.utc.localize(__import__("datetime").datetime.utcnow()).astimezone(TEHRAN_TZ)
            now_hm = now.strftime("%H:%M")

            # Which chats are due?
            due: List[int] = []
            for rec in chats:
                if not rec["approved"]:
                    continue
                cfg = rec["config"] or default_config()
                if not cfg.get("auto_send"):
                    continue
                if in_quiet(now_hm, cfg.get("quiet", [])):
                    continue

                interval = int(cfg.get("interval_min", 5) or 5)
                if interval < 1:
                    interval = 1

                # Send only on aligned minutes: minute % interval == 0
                if now.minute % interval != 0:
                    continue

                # Avoid duplicates within the same slot
                slot = now.strftime("%Y%m%d%H%M")
                state = rec["state"] or {}
                if state.get("last_slot") == slot:
                    continue

                # Slight second window to avoid multiple sends
                if now.second > 15:
                    continue

                state["last_slot"] = slot
                st.save_state(rec["chat_id"], state)
                due.append(rec["chat_id"])

            if due:
                # fetch once (fast) so all chats use same values
                json_data = await client.fetch_json()
                # Update cache for arrows using SELL by default; arrows are direction only
                # (each chat message still formats by its own mode)
                app.bot_data["data_cache"].update(json_data, "sell")
                for cid in due:
                    await send_to_chat(app, cid, force=False)

        except Exception as e:
            logging.getLogger("bonbast-bot").exception("sender_loop error: %s", e)

        await asyncio.sleep(3)


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logging.getLogger("bonbast-bot").exception("Unhandled error", exc_info=context.error)


def main() -> None:
    load_dotenv()

    token = os.environ.get("BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit("BOT_TOKEN is missing in .env")

    admin_ids = parse_admin_ids(os.environ.get("ADMIN_IDS", ""))
    if not admin_ids:
        raise SystemExit("ADMIN_IDS is missing in .env (must contain your Telegram numeric user id)")

    db_path = os.environ.get("DB_PATH", os.path.join(os.path.dirname(__file__), "data.db"))
    log_level = os.environ.get("LOG_LEVEL", "INFO").upper()

    logging.basicConfig(level=getattr(logging, log_level, logging.INFO), format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    logger = logging.getLogger("bonbast-bot")
    logger.info("Starting...")

    storage = Storage(db_path)
    client = BonbastClient()
    cache = DataCache()

    async def post_init(app: Application) -> None:
        app.create_task(sender_loop(app))

    app = (
        ApplicationBuilder()
        .token(token)
        .post_init(post_init)
        .build()
    )

    app.bot_data["admin_ids"] = admin_ids
    app.bot_data["storage"] = storage
    app.bot_data["client"] = client
    app.bot_data["data_cache"] = cache

    app.add_error_handler(on_error)

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("register", cmd_register))
    app.add_handler(CommandHandler("panel", cmd_panel))

    app.add_handler(ChatMemberHandler(on_my_chat_member, ChatMemberHandler.MY_CHAT_MEMBER))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), on_text))

    app.run_polling(close_loop=False)


if __name__ == "__main__":
    main()
