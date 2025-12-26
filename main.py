import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import pytz
from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
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

from bonbast_client import BonbastClient
from storage import Storage


TEHRAN_TZ = pytz.timezone("Asia/Tehran")
SEP = "_______________________"


def parse_admin_ids(s: str) -> List[int]:
    out: List[int] = []
    for part in re.split(r"[,\s]+", (s or "").strip()):
        if not part:
            continue
        try:
            out.append(int(part))
        except Exception:
            pass
    return sorted(set(out))


def is_admin(update: Update, admin_ids: List[int]) -> bool:
    u = update.effective_user
    return bool(u and u.id in admin_ids)


@dataclass(frozen=True)
class Item:
    code: str               # e.g. USD
    kind: str               # currency | coin | metal
    fa: str                 # Persian label
    emoji: str              # emoji
    sell_key: str           # json key for sell
    buy_key: str            # json key for buy
    money_emoji: bool = False   # USD/EUR/GBP special money emoji


# ---- All items shown on bonbast main page ----
ITEMS: Dict[str, Item] = {
    # Currencies (Sell/Buy keys are ids from the page: usd1/usd2 etc)
    "USD": Item("USD", "currency", "دلار آمریکا", "💵", "usd1", "usd2", money_emoji=True),
    "EUR": Item("EUR", "currency", "یورو", "💶", "eur1", "eur2", money_emoji=True),
    "GBP": Item("GBP", "currency", "پوند انگلیس", "💷", "gbp1", "gbp2", money_emoji=True),

    "CHF": Item("CHF", "currency", "فرانک سوئیس", "🇨🇭", "chf1", "chf2"),
    "CAD": Item("CAD", "currency", "دلار کانادا", "🇨🇦", "cad1", "cad2"),
    "AUD": Item("AUD", "currency", "دلار استرالیا", "🇦🇺", "aud1", "aud2"),
    "SEK": Item("SEK", "currency", "کرون سوئد", "🇸🇪", "sek1", "sek2"),
    "NOK": Item("NOK", "currency", "کرون نروژ", "🇳🇴", "nok1", "nok2"),
    "RUB": Item("RUB", "currency", "روبل روسیه", "🇷🇺", "rub1", "rub2"),
    "THB": Item("THB", "currency", "بات تایلند", "🇹🇭", "thb1", "thb2"),
    "SGD": Item("SGD", "currency", "دلار سنگاپور", "🇸🇬", "sgd1", "sgd2"),
    "HKD": Item("HKD", "currency", "دلار هنگ‌کنگ", "🇭🇰", "hkd1", "hkd2"),
    "AZN": Item("AZN", "currency", "منات آذربایجان", "🇦🇿", "azn1", "azn2"),
    "AMD": Item("AMD", "currency", "درام ارمنستان", "🇦🇲", "amd1", "amd2"),

    "DKK": Item("DKK", "currency", "کرون دانمارک", "🇩🇰", "dkk1", "dkk2"),
    "AED": Item("AED", "currency", "درهم امارات", "🇦🇪", "aed1", "aed2"),
    "JPY": Item("JPY", "currency", "ین ژاپن", "🇯🇵", "jpy1", "jpy2"),
    "TRY": Item("TRY", "currency", "لیر ترکیه", "🇹🇷", "try1", "try2"),
    "CNY": Item("CNY", "currency", "یوان چین", "🇨🇳", "cny1", "cny2"),
    "SAR": Item("SAR", "currency", "ریال عربستان", "🇸🇦", "sar1", "sar2"),
    "INR": Item("INR", "currency", "روپیه هند", "🇮🇳", "inr1", "inr2"),
    "MYR": Item("MYR", "currency", "رینگیت مالزی", "🇲🇾", "myr1", "myr2"),
    "AFN": Item("AFN", "currency", "افغانی افغانستان", "🇦🇫", "afn1", "afn2"),
    "KWD": Item("KWD", "currency", "دینار کویت", "🇰🇼", "kwd1", "kwd2"),
    "IQD": Item("IQD", "currency", "دینار عراق", "🇮🇶", "iqd1", "iqd2"),
    "BHD": Item("BHD", "currency", "دینار بحرین", "🇧🇭", "bhd1", "bhd2"),
    "OMR": Item("OMR", "currency", "ریال عمان", "🇴🇲", "omr1", "omr2"),
    "QAR": Item("QAR", "currency", "ریال قطر", "🇶🇦", "qar1", "qar2"),

    # Coins (sell/buy ids)
    "EMAMI": Item("EMAMI", "coin", "امامی", "🪙", "emami1", "emami12"),
    "AZADI": Item("AZADI", "coin", "آزادی", "🪙", "azadi1", "azadi12"),
    "NIM": Item("NIM", "coin", "نیم", "🪙", "azadi1_2", "azadi1_22"),
    "ROB": Item("ROB", "coin", "ربع", "🪙", "azadi1_4", "azadi1_42"),
    "GERAMI": Item("GERAMI", "coin", "گرمی", "🪙", "azadi1g", "azadi1g2"),

    # Metals + BTC
    "GOL_MITHQAL": Item("GOL_MITHQAL", "metal", "طلا مثقال", "⚜️", "mithqal", "mithqal"),
    "GOL_GRAM": Item("GOL_GRAM", "metal", "طلا گرمی", "⚜️", "gol18", "gol18"),
    "GOL_OUNCE": Item("GOL_OUNCE", "metal", "طلا اونس", "🌍", "ounce", "ounce"),
    "BTC": Item("BTC", "metal", "بیت‌کوین", "₿", "bitcoin", "bitcoin"),
}

DEFAULT_CURRENCY_ORDER = [
    "USD", "EUR", "GBP",
    "CHF", "CAD", "CNY", "AED", "TRY",
    "KWD", "BHD", "IQD", "RUB",
    "AUD", "SEK", "NOK", "THB", "SGD", "HKD", "AZN", "AMD", "DKK",
    "SAR", "INR", "MYR", "AFN", "OMR", "QAR", "JPY",
]


def default_config() -> Dict[str, Any]:
    return {
        "approved": False,
        "auto_send": False,
        "interval_min": 5,
        "quiet": [],  # list of ["HH:MM","HH:MM"]
        "only_on_change": False,
        "threshold": 0.0,
        "triggers": [],  # if empty => uses selected items
        "mode": "sell",  # sell|buy
        "send_mode": "post",  # post|edit
        "selected": {
            "currencies": DEFAULT_CURRENCY_ORDER.copy(),
            "coins": ["EMAMI", "AZADI", "NIM", "ROB", "GERAMI"],
            "metals": ["GOL_MITHQAL", "GOL_GRAM", "GOL_OUNCE", "BTC"],
        },
    }


def fmt_int_like(v: Any) -> str:
    if v is None:
        return ""
    s = str(v).strip()
    return s


def arrow(sign: str) -> str:
    # sign: up/down/same
    if sign == "up":
        return " ▲"
    if sign == "down":
        return " 🔻"
    return ""


class DataCache:
    def __init__(self) -> None:
        self.prev: Dict[str, float] = {}
        self.signs: Dict[str, str] = {}

    def update(self, json_data: Dict[str, Any], mode: str) -> None:
        for code, item in ITEMS.items():
            v = BonbastClient.get_value_float(item, json_data, mode)
            if v is None:
                continue
            old = self.prev.get(code)
            if old is None:
                self.signs[code] = "same"
            else:
                if v > old:
                    self.signs[code] = "up"
                elif v < old:
                    self.signs[code] = "down"
                else:
                    self.signs[code] = "same"
            self.prev[code] = v


def in_quiet(now_hm: str, quiet: List[List[str]]) -> bool:
    # quiet: [["23:00","07:30"], ["12:00","13:00"]]
    def to_min(hm: str) -> int:
        h, m = hm.split(":")
        return int(h) * 60 + int(m)

    nowm = to_min(now_hm)
    for a, b in quiet or []:
        am, bm = to_min(a), to_min(b)
        if am == bm:
            continue
        if am < bm:
            if am <= nowm < bm:
                return True
        else:
            # wraps midnight
            if nowm >= am or nowm < bm:
                return True
    return False


def parse_quiet_ranges(text: str) -> List[List[str]]:
    # "23:00-07:30,12:00-13:00"
    out: List[List[str]] = []
    t = text.strip()
    if not t:
        return out
    parts = [p.strip() for p in t.split(",") if p.strip()]
    for p in parts:
        if "-" not in p:
            continue
        a, b = [x.strip() for x in p.split("-", 1)]
        if not re.match(r"^\d{2}:\d{2}$", a) or not re.match(r"^\d{2}:\d{2}$", b):
            continue
        out.append([a, b])
    return out


def build_message(cfg: Dict[str, Any], json_data: Dict[str, Any], signs: Dict[str, str], first_post_no_arrow: bool) -> str:
    mode = cfg.get("mode", "sell")
    selected = cfg.get("selected", {})
    cur = selected.get("currencies", []) or DEFAULT_CURRENCY_ORDER
    coins = selected.get("coins", [])
    metals = selected.get("metals", [])

    lines: List[str] = []

    # currencies
    for code in cur:
        item = ITEMS.get(code)
        if not item:
            continue
        price = fmt_int_like(json_data.get(item.sell_key if mode == "sell" else item.buy_key))
        if not price:
            continue
        s = "" if first_post_no_arrow else arrow(signs.get(code, "same"))
        # emoji already correct: USD/EUR/GBP money emoji; others flag
        lines.append(f"{item.emoji} {item.fa} {price}{s}")

    # coins section
    if coins:
        lines.append(SEP)
        for code in coins:
            item = ITEMS.get(code)
            if not item:
                continue
            price = fmt_int_like(json_data.get(item.sell_key if mode == "sell" else item.buy_key))
            if not price:
                continue
            s = "" if first_post_no_arrow else arrow(signs.get(code, "same"))
            lines.append(f"{item.emoji} {item.fa} {price}{s}")

    # metals/BTC section
    if metals:
        lines.append(SEP)
        for code in metals:
            item = ITEMS.get(code)
            if not item:
                continue
            price = fmt_int_like(json_data.get(item.sell_key))
            if not price:
                continue
            s = "" if first_post_no_arrow else arrow(signs.get(code, "same"))
            lines.append(f"{item.emoji} {item.fa} {price}{s}")

    # date/time (from bonbast json)
    # year/month/day/hour/minute are in json (Tehran time)
    try:
        y = str(json_data.get("year", "")).strip()
        mo = str(json_data.get("month", "")).strip().zfill(2)
        d = str(json_data.get("day", "")).strip().zfill(2)
        hh = str(json_data.get("hour", "")).strip().zfill(2)
        mm = str(json_data.get("minute", "")).strip().zfill(2)
        if y and mo and d and hh and mm:
            lines.append(SEP)
            lines.append(f"{y}/{mo}/{d} - {hh}:{mm}")
    except Exception:
        pass

    return "\n".join(lines).strip()


# ---------- UI / Keyboards ----------

def kb_chat_list(chats: List[Dict[str, Any]]) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    for rec in chats:
        ap = "✅" if rec["approved"] else "⏳"
        auto = "🟢" if rec["config"].get("auto_send") else "🔴"
        title = rec["title"]
        rows.append([InlineKeyboardButton(f"{ap}{auto} {title}", callback_data=f"chat:{rec['chat_id']}")])
    return InlineKeyboardMarkup(rows or [[InlineKeyboardButton("No chats yet — add bot to a group/channel first", callback_data="noop")]])


def build_panel_keyboard(rec: Dict[str, Any]) -> InlineKeyboardMarkup:
    cfg = rec["config"] or default_config()
    approved = rec["approved"]
    auto = bool(cfg.get("auto_send"))
    onlychg = bool(cfg.get("only_on_change"))
    mode = cfg.get("mode", "sell")
    send_mode = cfg.get("send_mode", "post")

    status_txt = f"وضعیت: {'✅ تایید' if approved else '⏳ نیاز به تایید'}"
    auto_txt = f"ارسال خودکار: {'✅ فعال' if auto else '❌ غیرفعال'}"
    mode_txt = "Sell" if mode == "sell" else "Buy"
    sendmode_txt = "Post" if send_mode == "post" else "Edit"

    cid = rec["chat_id"]

    rows: List[List[InlineKeyboardButton]] = [
        [InlineKeyboardButton(status_txt, callback_data=f"toggle:approve:{cid}")],
        [InlineKeyboardButton(auto_txt, callback_data=f"toggle:auto:{cid}")],
        [
            InlineKeyboardButton("انتخاب ارزها", callback_data=f"pick:curr:{cid}:0"),
            InlineKeyboardButton("انتخاب سکه‌ها", callback_data=f"pick:coin:{cid}:0"),
        ],
        [InlineKeyboardButton("طلا / بیتکوین", callback_data=f"pick:metal:{cid}:0")],
        [
            InlineKeyboardButton("زمان‌بندی (Interval)", callback_data=f"set:interval:{cid}"),
            InlineKeyboardButton("ساعات سکوت", callback_data=f"set:quiet:{cid}"),
        ],
        [
            InlineKeyboardButton("فقط در صورت تغییر" + (" ✅" if onlychg else " ❌"), callback_data=f"toggle:onlychg:{cid}"),
            InlineKeyboardButton("Triggers", callback_data=f"pick:trig:{cid}:0"),
        ],
        [
            InlineKeyboardButton(f"Sell/Buy: {mode_txt}", callback_data=f"toggle:mode:{cid}"),
            InlineKeyboardButton(f"Send mode: {sendmode_txt}", callback_data=f"toggle:sendmode:{cid}"),
        ],
        [
            InlineKeyboardButton("Threshold", callback_data=f"set:threshold:{cid}"),
            InlineKeyboardButton("ارسال فوری (Send now)", callback_data=f"sendnow:{cid}"),
        ],
        [
            InlineKeyboardButton("ترتیب ارزها", callback_data=f"set:order:{cid}"),
            InlineKeyboardButton("Export config", callback_data=f"export:{cid}"),
        ],
        [InlineKeyboardButton("Help", callback_data="help")],
    ]
    return InlineKeyboardMarkup(rows)


def build_picker(kind: str, chat_id: int, cfg: Dict[str, Any], page: int) -> InlineKeyboardMarkup:
    # kind: curr|coin|metal|trig
    sel = cfg.setdefault("selected", {})
    if kind == "trig":
        selected = set(cfg.get("triggers", []) or [])
        keys = [k for k, it in ITEMS.items() if it.kind in ("currency", "coin", "metal")]
        title = "Triggers"
    else:
        map_name = {"curr": "currencies", "coin": "coins", "metal": "metals"}[kind]
        selected = set(sel.get(map_name, []) or [])
        keys = [k for k, it in ITEMS.items() if it.kind == ("currency" if kind == "curr" else "coin" if kind == "coin" else "metal")]
        title = map_name

    keys.sort()

    per_page = 18
    start = page * per_page
    chunk = keys[start:start + per_page]

    rows: List[List[InlineKeyboardButton]] = []
    # 2 columns like your screenshot
    for i in range(0, len(chunk), 2):
        row: List[InlineKeyboardButton] = []
        for k in chunk[i:i + 2]:
            it = ITEMS[k]
            mark = "✅" if k in selected else "❌"
            row.append(InlineKeyboardButton(f"{it.fa} {mark}", callback_data=f"pick:toggle:{kind}:{chat_id}:{k}:{page}"))
        rows.append(row)

    nav: List[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"pick:{kind}:{chat_id}:{page-1}"))
    nav.append(InlineKeyboardButton("Done", callback_data=f"pick:done:{kind}:{chat_id}"))
    if start + per_page < len(keys):
        nav.append(InlineKeyboardButton("➡️", callback_data=f"pick:{kind}:{chat_id}:{page+1}"))
    rows.append(nav)

    return InlineKeyboardMarkup(rows)


# ---------- Commands ----------

HELP_TEXT = (
    "سلام!\n\n"
    "✅ /register\n"
    "داخل گروه/کانال اجرا کنید تا در لیست مدیریت ثبت شود.\n\n"
    "✅ /panel\n"
    "پنل مدیریت (همه تنظیمات هر چت جداگانه).\n\n"
    "نکته: برای اینکه بات بتواند در کانال پیام بفرستد باید Admin باشد و اجازه Post داشته باشد.\n"
)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text("سلام!\npanel/ برای مدیریت\nregister/ برای ثبت گروه/کانال")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(HELP_TEXT)


async def cmd_register(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    admin_ids = context.application.bot_data["admin_ids"]
    if not is_admin(update, admin_ids):
        return

    st: Storage = context.application.bot_data["storage"]
    chat = update.effective_chat
    title = (chat.title or chat.username or str(chat.id)).strip()
    st.upsert_chat(chat.id, title, chat.type)
    await update.effective_message.reply_text("✅ ثبت شد. وضعیت: ⏳ نیاز به تایید (از /panel تایید کنید).")


async def cmd_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    admin_ids = context.application.bot_data["admin_ids"]
    if not is_admin(update, admin_ids):
        return
    st: Storage = context.application.bot_data["storage"]
    chats = st.list_chats()
    await update.effective_message.reply_text("Select a chat to manage:", reply_markup=kb_chat_list(chats))


# ---------- Updates / Callbacks ----------

async def on_my_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    admin_ids = context.application.bot_data["admin_ids"]
    st: Storage = context.application.bot_data["storage"]
    logger = logging.getLogger("bonbast-bot")

    chat = update.effective_chat
    if not chat:
        return

    new_status = update.my_chat_member.new_chat_member.status
    old_status = update.my_chat_member.old_chat_member.status

    title = (chat.title or chat.username or str(chat.id)).strip()

    if new_status in ("member", "administrator") and old_status in ("kicked", "left"):
        st.upsert_chat(chat.id, title, chat.type)
        logger.info("Bot added to chat %s (%s)", title, chat.id)
        # notify admins
        for aid in admin_ids:
            try:
                await context.bot.send_message(
                    chat_id=aid,
                    text=f"➕ Bot added to: {title}\nID: {chat.id}\n⏳ نیاز به تایید از /panel",
                )
            except Exception:
                pass

    if new_status in ("kicked", "left"):
        logger.info("Bot removed from chat %s (%s)", title, chat.id)
        st.delete_chat(chat.id)


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()

    admin_ids = context.application.bot_data["admin_ids"]
    if update.effective_user and update.effective_user.id not in admin_ids:
        return

    st: Storage = context.application.bot_data["storage"]
    data = q.data or ""

    if data == "noop":
        return

    if data == "help":
        await q.message.reply_text(HELP_TEXT)
        return

    if data.startswith("chat:"):
        chat_id = int(data.split(":")[1])
        rec = st.get_chat(chat_id)
        if not rec:
            await q.message.edit_text("Chat not found.")
            return
        # ensure config exists
        cfg = rec["config"] or default_config()
        if not cfg:
            cfg = default_config()
        st.save_config(chat_id, cfg)
        await q.message.edit_text(f"Control Panel: {rec['title']} ({chat_id})", reply_markup=build_panel_keyboard(rec))
        return

    # Toggles
    if data.startswith("toggle:approve:"):
        chat_id = int(data.split(":")[-1])
        rec = st.get_chat(chat_id)
        if not rec:
            return
        new_val = not rec["approved"]
        st.set_approved(chat_id, new_val)
        rec = st.get_chat(chat_id)  # refresh
        await q.message.edit_text(f"Control Panel: {rec['title']} ({chat_id})", reply_markup=build_panel_keyboard(rec))
        return

    if data.startswith("toggle:auto:"):
        chat_id = int(data.split(":")[-1])
        rec = st.get_chat(chat_id)
        if not rec:
            return
        cfg = rec["config"] or default_config()
        cfg["auto_send"] = not bool(cfg.get("auto_send"))
        st.save_config(chat_id, cfg)
        rec = st.get_chat(chat_id)
        await q.message.edit_text(f"Control Panel: {rec['title']} ({chat_id})", reply_markup=build_panel_keyboard(rec))
        return

    if data.startswith("toggle:onlychg:"):
        chat_id = int(data.split(":")[-1])
        rec = st.get_chat(chat_id)
        if not rec:
            return
        cfg = rec["config"] or default_config()
        cfg["only_on_change"] = not bool(cfg.get("only_on_change"))
        st.save_config(chat_id, cfg)
        rec = st.get_chat(chat_id)
        await q.message.edit_text(f"Control Panel: {rec['title']} ({chat_id})", reply_markup=build_panel_keyboard(rec))
        return

    if data.startswith("toggle:mode:"):
        chat_id = int(data.split(":")[-1])
        rec = st.get_chat(chat_id)
        if not rec:
            return
        cfg = rec["config"] or default_config()
        cfg["mode"] = "buy" if cfg.get("mode") == "sell" else "sell"
        st.save_config(chat_id, cfg)
        rec = st.get_chat(chat_id)
        await q.message.edit_text(f"Control Panel: {rec['title']} ({chat_id})", reply_markup=build_panel_keyboard(rec))
        return

    if data.startswith("toggle:sendmode:"):
        chat_id = int(data.split(":")[-1])
        rec = st.get_chat(chat_id)
        if not rec:
            return
        cfg = rec["config"] or default_config()
        cfg["send_mode"] = "edit" if cfg.get("send_mode") == "post" else "post"
        st.save_config(chat_id, cfg)
        rec = st.get_chat(chat_id)
        await q.message.edit_text(f"Control Panel: {rec['title']} ({chat_id})", reply_markup=build_panel_keyboard(rec))
        return

    # Set inputs
    if data.startswith("set:interval:"):
        chat_id = int(data.split(":")[-1])
        context.user_data["awaiting"] = ("interval", chat_id)
        await q.message.reply_text("Interval را به دقیقه بفرستید.\nمثال: 5")
        return

    if data.startswith("set:threshold:"):
        chat_id = int(data.split(":")[-1])
        context.user_data["awaiting"] = ("threshold", chat_id)
        await q.message.reply_text("Threshold را بفرستید (حداقل مقدار تغییر).\nمثال: 100\n0 یعنی هر تغییر.")
        return

    if data.startswith("set:quiet:"):
        chat_id = int(data.split(":")[-1])
        context.user_data["awaiting"] = ("quiet", chat_id)
        await q.message.reply_text("ساعات سکوت را بفرستید. مثال:\n23:00-07:30\nیا چند بازه:\n12:00-13:00,23:00-07:30\n(خالی = بدون سکوت)")
        return

    if data.startswith("set:order:"):
        chat_id = int(data.split(":")[-1])
        context.user_data["awaiting"] = ("order", chat_id)
        await q.message.reply_text("ترتیب ارزها را با کُدها بفرستید (با فاصله یا کاما).\nمثال:\nUSD EUR GBP CHF AED")
        return

    # Picker pages
    if data.startswith("pick:curr:") or data.startswith("pick:coin:") or data.startswith("pick:metal:") or data.startswith("pick:trig:"):
        _, kind, chat_id, page = data.split(":")
        chat_id = int(chat_id)
        page = int(page)
        rec = st.get_chat(chat_id)
        if not rec:
            return
        cfg = rec["config"] or default_config()
        await q.message.edit_text("انتخاب کنید:", reply_markup=build_picker(kind, chat_id, cfg, page))
        return

    if data.startswith("pick:toggle:"):
        _, _, kind, chat_id, key, page = data.split(":")
        chat_id = int(chat_id)
        page = int(page)
        rec = st.get_chat(chat_id)
        if not rec:
            return
        cfg = rec["config"] or default_config()

        if kind == "trig":
            lst = cfg.setdefault("triggers", [])
        else:
            map_name = {"curr": "currencies", "coin": "coins", "metal": "metals"}[kind]
            lst = cfg.setdefault("selected", {}).setdefault(map_name, [])

        if key in lst:
            lst.remove(key)
        else:
            lst.append(key)

        st.save_config(chat_id, cfg)
        await q.message.edit_text("انتخاب کنید:", reply_markup=build_picker(kind, chat_id, cfg, page))
        return

    if data.startswith("pick:done:"):
        _, _, kind, chat_id = data.split(":")
        chat_id = int(chat_id)
        rec = st.get_chat(chat_id)
        if not rec:
            return
        await q.message.edit_text(f"Control Panel: {rec['title']} ({chat_id})", reply_markup=build_panel_keyboard(rec))
        return

    if data.startswith("export:"):
        chat_id = int(data.split(":")[-1])
        rec = st.get_chat(chat_id)
        if not rec:
            return
        await q.message.reply_text(json.dumps(rec["config"] or {}, ensure_ascii=False, indent=2))
        return

    if data.startswith("sendnow:"):
        chat_id = int(data.split(":")[-1])
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

    try:
        if kind == "interval":
            v = int(text)
            if v < 1 or v > 360:
                raise ValueError()
            cfg["interval_min"] = v
            st.save_config(chat_id, cfg)
            await update.effective_message.reply_text("✅ Interval ذخیره شد.")
        elif kind == "threshold":
            v = float(text.replace(",", ""))
            if v < 0:
                raise ValueError()
            cfg["threshold"] = v
            st.save_config(chat_id, cfg)
            await update.effective_message.reply_text("✅ Threshold ذخیره شد.")
        elif kind == "quiet":
            cfg["quiet"] = parse_quiet_ranges(text)
            st.save_config(chat_id, cfg)
            await update.effective_message.reply_text("✅ ساعات سکوت ذخیره شد.")
        elif kind == "order":
            parts = re.split(r"[,\s]+", text.upper().strip())
            parts = [p for p in parts if p]
            valid = [p for p in parts if p in ITEMS and ITEMS[p].kind == "currency"]
            if not valid:
                await update.effective_message.reply_text("❌ کُدها معتبر نیستند.\nمثال: USD EUR GBP CHF")
            else:
                sel = cfg.setdefault("selected", {}).setdefault("currencies", [])
                new_order: List[str] = []
                for p in valid:
                    if p in sel and p not in new_order:
                        new_order.append(p)
                for p in sel:
                    if p not in new_order:
                        new_order.append(p)
                cfg["selected"]["currencies"] = new_order
                st.save_config(chat_id, cfg)
                await update.effective_message.reply_text("✅ ترتیب ارزها ذخیره شد.")
    finally:
        context.user_data.pop("awaiting", None)


# ---------- Sender ----------

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
    json_data = await client.fetch_json()

    # update arrows based on SELL (direction only)
    cache.update(json_data, "sell")

    state = rec["state"] or {}
    last_sent_vals = state.get("last_sent_vals", {})
    last_message_id = state.get("last_message_id")
    first_post_done = bool(state.get("first_post_done", False))

    msg = build_message(cfg, json_data, cache.signs, first_post_no_arrow=(not first_post_done))

    if cfg.get("only_on_change") and not force:
        threshold = float(cfg.get("threshold", 0.0) or 0.0)
        triggers = cfg.get("triggers", [])
        if not triggers:
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

    # snapshot
    new_sent_vals: Dict[str, Any] = {}
    for k, item in ITEMS.items():
        v = BonbastClient.get_value_float(item, json_data, cfg.get("mode", "sell"))
        if v is not None:
            new_sent_vals[k] = v

    send_mode = cfg.get("send_mode", "post")
    if send_mode == "edit" and last_message_id:
        try:
            await app.bot.edit_message_text(chat_id=chat_id, message_id=int(last_message_id), text=msg, disable_web_page_preview=True)
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
    logger = logging.getLogger("bonbast-bot")

    while True:
        try:
            chats = st.list_chats()
            now = pytz.utc.localize(__import__("datetime").datetime.utcnow()).astimezone(TEHRAN_TZ)
            now_hm = now.strftime("%H:%M")

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

                # aligned minutes (12:00, 12:05, ...)
                if now.minute % interval != 0:
                    continue
                if now.second > 15:
                    continue

                slot = now.strftime("%Y%m%d%H%M")
                state = rec["state"] or {}
                if state.get("last_slot") == slot:
                    continue
                state["last_slot"] = slot
                st.save_state(rec["chat_id"], state)
                due.append(rec["chat_id"])

            if due:
                # one fetch for speed
                json_data = await client.fetch_json()
                app.bot_data["data_cache"].update(json_data, "sell")
                for cid in due:
                    await send_to_chat(app, cid, force=False)

        except Exception as e:
            logger.exception("sender_loop error: %s", e)

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

    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    storage = Storage(db_path)
    client = BonbastClient()
    cache = DataCache()

    async def post_init(app: Application) -> None:
        app.create_task(sender_loop(app))

    app = ApplicationBuilder().token(token).post_init(post_init).build()
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
