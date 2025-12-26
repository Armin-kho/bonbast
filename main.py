import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, time as dtime
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv
from zoneinfo import ZoneInfo

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ChatMemberHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from bonbast_client import BonbastClient
from storage import Storage

TZ = ZoneInfo("Asia/Tehran")
LOG = logging.getLogger("bonbast-bot")


# ---------------- Catalog ----------------

@dataclass(frozen=True)
class Item:
    code: str
    fa: str
    emoji: str
    sell_key: str
    buy_key: Optional[str] = None
    category: str = "cur"  # cur|coin|metal


CURRENCIES: List[Item] = [
    Item("usd", "دلار آمریکا", "💵", "usd1", "usd2", "cur"),
    Item("eur", "یورو", "💶", "eur1", "eur2", "cur"),
    Item("gbp", "پوند انگلیس", "💷", "gbp1", "gbp2", "cur"),

    Item("chf", "فرانک سوئیس", "🇨🇭", "chf1", "chf2", "cur"),
    Item("cad", "دلار کانادا", "🇨🇦", "cad1", "cad2", "cur"),
    Item("aud", "دلار استرالیا", "🇦🇺", "aud1", "aud2", "cur"),
    Item("sek", "کرون سوئد", "🇸🇪", "sek1", "sek2", "cur"),
    Item("nok", "کرون نروژ", "🇳🇴", "nok1", "nok2", "cur"),
    Item("rub", "روبل روسیه", "🇷🇺", "rub1", "rub2", "cur"),
    Item("thb", "بات تایلند", "🇹🇭", "thb1", "thb2", "cur"),
    Item("sgd", "دلار سنگاپور", "🇸🇬", "sgd1", "sgd2", "cur"),
    Item("hkd", "دلار هنگ‌کنگ", "🇭🇰", "hkd1", "hkd2", "cur"),
    Item("azn", "منات آذربایجان", "🇦🇿", "azn1", "azn2", "cur"),
    Item("amd", "درام ارمنستان", "🇦🇲", "amd1", "amd2", "cur"),
    Item("dkk", "کرون دانمارک", "🇩🇰", "dkk1", "dkk2", "cur"),
    Item("aed", "درهم امارات", "🇦🇪", "aed1", "aed2", "cur"),
    Item("jpy", "ین ژاپن", "🇯🇵", "jpy1", "jpy2", "cur"),
    Item("try", "لیر ترکیه", "🇹🇷", "try1", "try2", "cur"),
    Item("cny", "یوان چین", "🇨🇳", "cny1", "cny2", "cur"),
    Item("sar", "ریال عربستان", "🇸🇦", "sar1", "sar2", "cur"),
    Item("inr", "روپیه هند", "🇮🇳", "inr1", "inr2", "cur"),
    Item("myr", "رینگیت مالزی", "🇲🇾", "myr1", "myr2", "cur"),
    Item("afn", "افغانی افغانستان", "🇦🇫", "afn1", "afn2", "cur"),
    Item("kwd", "دینار کویت", "🇰🇼", "kwd1", "kwd2", "cur"),
    Item("iqd", "دینار عراق", "🇮🇶", "iqd1", "iqd2", "cur"),
    Item("bhd", "دینار بحرین", "🇧🇭", "bhd1", "bhd2", "cur"),
    Item("omr", "ریال عمان", "🇴🇲", "omr1", "omr2", "cur"),
    Item("qar", "ریال قطر", "🇶🇦", "qar1", "qar2", "cur"),
]

COINS: List[Item] = [
    Item("azadi", "سکه آزادی", "🪙", "azadi1", "azadi12", "coin"),
    Item("emami", "سکه امامی", "🪙", "emami1", "emami12", "coin"),
    Item("nim", "نیم سکه", "🪙", "azadi1_2", "azadi1_22", "coin"),
    Item("rob", "ربع سکه", "🪙", "azadi1_4", "azadi1_42", "coin"),
    Item("gerami", "سکه گرمی", "🪙", "azadi1g", "azadi1g2", "coin"),
]

METALS: List[Item] = [
    Item("gold18", "طلا ۱۸ عیار", "⚜️", "gol18", None, "metal"),
    Item("mithqal", "طلا مثقال", "⚜️", "mithqal", None, "metal"),
    Item("ounce", "طلا اونس", "🌍", "ounce", None, "metal"),
    Item("btc", "بیت‌کوین", "₿", "bitcoin", None, "metal"),
]

CATALOG: Dict[str, Item] = {i.code: i for i in (CURRENCIES + COINS + METALS)}
CAT_BY_CAT: Dict[str, List[Item]] = {"cur": CURRENCIES, "coin": COINS, "metal": METALS}


# ---------------- Config helpers ----------------

def default_config() -> Dict[str, Any]:
    return {
        "auto_send": False,
        "interval_min": 5,
        "quiet": "",  # e.g. "23:00-08:00"
        "only_if_changed": False,
        "sellbuy": "sell",  # sell|buy
        "threshold": 0,  # abs threshold (toman). 0 disables
        "selected": {
            "cur": [i.code for i in CURRENCIES],
            "coin": [i.code for i in COINS],
            "metal": [i.code for i in METALS],
        },
        # If empty -> triggers == all selected
        "triggers": {"cur": [], "coin": [], "metal": []},
    }


def is_admin(user_id: int, admin_ids: List[int]) -> bool:
    return user_id in admin_ids


def parse_quiet(s: str) -> Optional[Tuple[dtime, dtime]]:
    # "HH:MM-HH:MM"
    m = re.match(r"^\s*(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})\s*$", s)
    if not m:
        return None
    h1, m1, h2, m2 = map(int, m.groups())
    if not (0 <= h1 <= 23 and 0 <= h2 <= 23 and 0 <= m1 <= 59 and 0 <= m2 <= 59):
        return None
    return dtime(h1, m1), dtime(h2, m2)


def in_quiet(now: datetime, quiet: str) -> bool:
    if not quiet:
        return False
    parsed = parse_quiet(quiet)
    if not parsed:
        return False
    start, end = parsed
    t = now.time()
    if start == end:
        return False
    if start < end:
        return start <= t < end
    # wraps midnight
    return t >= start or t < end


def to_number(v: Any) -> Optional[float]:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        s = v.strip().replace(",", "")
        if s == "":
            return None
        try:
            return float(s)
        except ValueError:
            return None
    return None


def fmt_value(v: Any) -> str:
    n = to_number(v)
    if n is None:
        return "-"
    # decide int vs float display
    if abs(n - round(n)) < 1e-9 and abs(n) >= 100:
        return f"{int(round(n)):,}"
    # keep 2 decimals for smaller/float values (ounce, btc)
    return f"{n:,.2f}".rstrip("0").rstrip(".")


def build_lines(section_items: List[Item], cfg: Dict[str, Any], data: Dict[str, Any], last: Dict[str, float]) -> Tuple[List[str], Dict[str, float], bool]:
    """
    Returns (lines, new_last_values, any_triggered_change)
    """
    sellbuy = cfg.get("sellbuy", "sell")
    threshold = float(cfg.get("threshold", 0) or 0)

    selected_codes = cfg.get("selected", {}).get(section_items[0].category, [])
    trigger_codes = cfg.get("triggers", {}).get(section_items[0].category, []) or selected_codes

    lines: List[str] = []
    new_last: Dict[str, float] = dict(last)
    changed = False

    for it in section_items:
        if it.code not in selected_codes:
            continue

        key = it.sell_key if sellbuy == "sell" or not it.buy_key else it.buy_key
        raw = data.get(key)
        num = to_number(raw)
        value_str = fmt_value(raw)

        arrow = ""
        if num is not None and it.code in last:
            prev = last[it.code]
            if num > prev + 1e-9:
                arrow = " ▲"
            elif num < prev - 1e-9:
                arrow = " 🔻"

            if it.code in trigger_codes:
                if threshold <= 0:
                    if abs(num - prev) > 1e-9:
                        changed = True
                else:
                    if abs(num - prev) >= threshold:
                        changed = True

        # store last value (even if unchanged) to keep comparisons fresh
        if num is not None:
            new_last[it.code] = num

        # RTL mark at line start improves layout in mixed RTL+numbers
        rtl = "\u200f"
        lines.append(f"{rtl}{it.emoji} {it.fa} : {value_str}{arrow}")

    return lines, new_last, changed


def now_slot_tehran(now: datetime) -> str:
    return now.strftime("%Y/%m/%d %H:%M")


# ---------------- UI keyboards ----------------

def kb_chat_list(chats: List[Dict[str, Any]]) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    for ch in chats:
        icon = "✅" if ch["approved"] else "⏳"
        title = ch["title"] or str(ch["chat_id"])
        rows.append([InlineKeyboardButton(f"{icon} {title}", callback_data=f"sel|{ch['chat_id']}")])
    rows.append([InlineKeyboardButton("🔄 Refresh", callback_data="refresh|0")])
    return InlineKeyboardMarkup(rows)


def kb_main(chat_id: int, approved: bool, cfg: Dict[str, Any]) -> InlineKeyboardMarkup:
    auto = "✅ فعال" if cfg.get("auto_send") else "❌ غیرفعال"
    ap = "✅ تایید شده" if approved else "⏳ نیاز به تایید"

    rows = [
        [InlineKeyboardButton(f"وضعیت : {ap}", callback_data=f"approve|{chat_id}")],
        [InlineKeyboardButton(f"ارسال خودکار : {auto}", callback_data=f"auto|{chat_id}")],
        [
            InlineKeyboardButton("انتخاب ارزها", callback_data=f"menu|{chat_id}|cur"),
            InlineKeyboardButton("انتخاب سکه‌ها", callback_data=f"menu|{chat_id}|coin"),
        ],
        [
            InlineKeyboardButton("طلا / بیت‌کوین", callback_data=f"menu|{chat_id}|metal"),
        ],
        [
            InlineKeyboardButton("زمانبندی (Interval)", callback_data=f"menu|{chat_id}|interval"),
            InlineKeyboardButton("ساعات سکوت", callback_data=f"menu|{chat_id}|quiet"),
        ],
        [
            InlineKeyboardButton("فقط در صورت تغییر", callback_data=f"toggle|{chat_id}|only"),
            InlineKeyboardButton("تریگرها", callback_data=f"menu|{chat_id}|triggers"),
        ],
        [
            InlineKeyboardButton("Sell/Buy", callback_data=f"menu|{chat_id}|sellbuy"),
            InlineKeyboardButton("Threshold", callback_data=f"menu|{chat_id}|threshold"),
        ],
        [
            InlineKeyboardButton("ارسال فوری (Send now)", callback_data=f"sendnow|{chat_id}"),
            InlineKeyboardButton("تست ارسال", callback_data=f"test|{chat_id}"),
        ],
        [
            InlineKeyboardButton("Export config", callback_data=f"export|{chat_id}"),
            InlineKeyboardButton("Help", callback_data="help|0"),
        ],
        [
            InlineKeyboardButton("⬅️ Back to chats", callback_data="back|0"),
        ],
    ]
    return InlineKeyboardMarkup(rows)


def kb_items(chat_id: int, cat: str, cfg: Dict[str, Any]) -> InlineKeyboardMarkup:
    items = CAT_BY_CAT[cat]
    selected = cfg.get("selected", {}).get(cat, [])
    rows: List[List[InlineKeyboardButton]] = []
    row: List[InlineKeyboardButton] = []
    for it in items:
        on = it.code in selected
        label = f"{it.fa} {'✅' if on else '❌'}"
        row.append(InlineKeyboardButton(label, callback_data=f"togitem|{chat_id}|{cat}|{it.code}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)

    rows.append([
        InlineKeyboardButton("✅ Select all", callback_data=f"all|{chat_id}|{cat}|1"),
        InlineKeyboardButton("❌ Clear all", callback_data=f"all|{chat_id}|{cat}|0"),
    ])
    rows.append([
        InlineKeyboardButton("🔁 Reset order", callback_data=f"resetorder|{chat_id}|{cat}"),
        InlineKeyboardButton("⬅️ Back", callback_data=f"panel|{chat_id}"),
    ])
    return InlineKeyboardMarkup(rows)


def kb_triggers(chat_id: int, cfg: Dict[str, Any]) -> InlineKeyboardMarkup:
    # choose which category first
    rows = [
        [InlineKeyboardButton("تریگر ارزها", callback_data=f"trigcat|{chat_id}|cur")],
        [InlineKeyboardButton("تریگر سکه‌ها", callback_data=f"trigcat|{chat_id}|coin")],
        [InlineKeyboardButton("تریگر طلا/بیت‌کوین", callback_data=f"trigcat|{chat_id}|metal")],
        [InlineKeyboardButton("⬅️ Back", callback_data=f"panel|{chat_id}")],
    ]
    return InlineKeyboardMarkup(rows)


def kb_trig_items(chat_id: int, cat: str, cfg: Dict[str, Any]) -> InlineKeyboardMarkup:
    selected = cfg.get("selected", {}).get(cat, [])
    triggers = cfg.get("triggers", {}).get(cat, [])
    items = [CATALOG[c] for c in selected if c in CATALOG]

    rows: List[List[InlineKeyboardButton]] = []
    row: List[InlineKeyboardButton] = []
    for it in items:
        on = it.code in triggers
        label = f"{it.fa} {'✅' if on else '❌'}"
        row.append(InlineKeyboardButton(label, callback_data=f"togtrig|{chat_id}|{cat}|{it.code}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)

    rows.append([
        InlineKeyboardButton("✅ همه تریگر شوند", callback_data=f"trigall|{chat_id}|{cat}|1"),
        InlineKeyboardButton("⬜️ تریگر = همه منتخب‌ها", callback_data=f"trigall|{chat_id}|{cat}|0"),
    ])
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data=f"menu|{chat_id}|triggers")])
    return InlineKeyboardMarkup(rows)


def kb_interval(chat_id: int, cfg: Dict[str, Any]) -> InlineKeyboardMarkup:
    cur = int(cfg.get("interval_min", 5) or 5)
    rows = [
        [
            InlineKeyboardButton("5", callback_data=f"setint|{chat_id}|5"),
            InlineKeyboardButton("10", callback_data=f"setint|{chat_id}|10"),
            InlineKeyboardButton("15", callback_data=f"setint|{chat_id}|15"),
        ],
        [
            InlineKeyboardButton("30", callback_data=f"setint|{chat_id}|30"),
            InlineKeyboardButton("60", callback_data=f"setint|{chat_id}|60"),
            InlineKeyboardButton("Custom…", callback_data=f"ask|{chat_id}|interval"),
        ],
        [InlineKeyboardButton(f"فعلی: {cur} دقیقه", callback_data="noop|0")],
        [InlineKeyboardButton("⬅️ Back", callback_data=f"panel|{chat_id}")],
    ]
    return InlineKeyboardMarkup(rows)


def kb_quiet(chat_id: int, cfg: Dict[str, Any]) -> InlineKeyboardMarkup:
    q = cfg.get("quiet", "")
    rows = [
        [InlineKeyboardButton(f"فعلی: {q or 'خاموش'}", callback_data="noop|0")],
        [
            InlineKeyboardButton("Set… (مثال 23:00-08:00)", callback_data=f"ask|{chat_id}|quiet"),
            InlineKeyboardButton("Clear", callback_data=f"clearquiet|{chat_id}"),
        ],
        [InlineKeyboardButton("⬅️ Back", callback_data=f"panel|{chat_id}")],
    ]
    return InlineKeyboardMarkup(rows)


def kb_sellbuy(chat_id: int, cfg: Dict[str, Any]) -> InlineKeyboardMarkup:
    cur = cfg.get("sellbuy", "sell")
    rows = [
        [
            InlineKeyboardButton(f"Sell {'✅' if cur=='sell' else ''}", callback_data=f"setsb|{chat_id}|sell"),
            InlineKeyboardButton(f"Buy {'✅' if cur=='buy' else ''}", callback_data=f"setsb|{chat_id}|buy"),
        ],
        [InlineKeyboardButton("⬅️ Back", callback_data=f"panel|{chat_id}")],
    ]
    return InlineKeyboardMarkup(rows)


def kb_threshold(chat_id: int, cfg: Dict[str, Any]) -> InlineKeyboardMarkup:
    th = cfg.get("threshold", 0) or 0
    rows = [
        [InlineKeyboardButton(f"فعلی: {th}", callback_data="noop|0")],
        [
            InlineKeyboardButton("0 (خاموش)", callback_data=f"setth|{chat_id}|0"),
            InlineKeyboardButton("1000", callback_data=f"setth|{chat_id}|1000"),
            InlineKeyboardButton("5000", callback_data=f"setth|{chat_id}|5000"),
        ],
        [InlineKeyboardButton("Custom…", callback_data=f"ask|{chat_id}|threshold")],
        [InlineKeyboardButton("⬅️ Back", callback_data=f"panel|{chat_id}")],
    ]
    return InlineKeyboardMarkup(rows)


# ---------------- Bot handlers ----------------

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "سلام!\n"
        "برای مدیریت: /panel\n"
        "برای ثبت چت (گروه/کانال): /register (داخل همان چت)\n"
    )
    await update.effective_message.reply_text(text)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = (
        "راهنما:\n"
        "- اول در پی‌وی به ربات /start بدهید (برای اینکه ربات بتواند به شما پیام بدهد)\n"
        "- ربات را به گروه/کانال اضافه کنید (برای کانال بهتر است ادمینش کنید تا بتواند ارسال کند)\n"
        "- سپس /panel → تایید (Approve) کنید\n"
        "- ارسال خودکار را روشن کنید و Interval را تنظیم کنید\n"
        "- اگر «فقط در صورت تغییر» را روشن کنید، فقط وقتی تریگرها تغییر کنند پیام می‌فرستد\n\n"
        "نکته ترتیب:\n"
        "ترتیب نمایش = ترتیب انتخاب شماست. اگر ترتیب جدید می‌خواهید: Reset order → دوباره به ترتیب انتخاب کنید."
    )
    await update.effective_message.reply_text(msg)


async def cmd_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    admin_ids = context.bot_data["ADMIN_IDS"]
    uid = update.effective_user.id if update.effective_user else 0
    if not is_admin(uid, admin_ids):
        await update.effective_message.reply_text("⛔️ دسترسی ندارید.")
        return

    st: Storage = context.bot_data["STORAGE"]
    chats = st.list_chats()
    if not chats:
        await update.effective_message.reply_text("هیچ چتی ثبت نشده. ربات را به گروه/کانال اضافه کنید یا /register بزنید.")
        return
    await update.effective_message.reply_text("Select a chat to manage:", reply_markup=kb_chat_list(chats))


async def cmd_register(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Must be called inside the target group/supergroup/channel
    chat = update.effective_chat
    if not chat:
        return
    if chat.type == "private":
        await update.effective_message.reply_text("این دستور را داخل گروه/کانال اجرا کنید.")
        return

    st: Storage = context.bot_data["STORAGE"]
    st.upsert_chat(chat.id, chat.title or chat.username or str(chat.id), chat.type)
    await update.effective_message.reply_text("✅ ثبت شد. برای تایید از /panel در پی‌وی استفاده کنید.")


async def on_my_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Detect bot added/removed
    m = update.my_chat_member
    if not m:
        return
    chat = m.chat
    new_status = m.new_chat_member.status
    st: Storage = context.bot_data["STORAGE"]

    if new_status in ("member", "administrator"):
        st.upsert_chat(chat.id, chat.title or chat.username or str(chat.id), chat.type)
        # DM admins (only if they started bot already)
        for aid in context.bot_data["ADMIN_IDS"]:
            try:
                await context.bot.send_message(
                    chat_id=aid,
                    text=f"🆕 Bot added to: {chat.title or chat.id}\nID: {chat.id}\n/panel → approve",
                )
            except Exception:
                pass
    elif new_status in ("left", "kicked"):
        st.remove_chat(chat.id)


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Handle custom inputs requested by buttons
    admin_ids = context.bot_data["ADMIN_IDS"]
    uid = update.effective_user.id if update.effective_user else 0
    if not is_admin(uid, admin_ids):
        return
    if update.effective_chat and update.effective_chat.type != "private":
        return

    pending = context.user_data.get("PENDING")
    if not pending:
        return

    chat_id = int(pending["chat_id"])
    kind = pending["kind"]
    txt = (update.effective_message.text or "").strip()

    st: Storage = context.bot_data["STORAGE"]
    ch = st.get_chat(chat_id)
    if not ch:
        await update.effective_message.reply_text("چت پیدا نشد.")
        context.user_data.pop("PENDING", None)
        return
    cfg = ch["config"] or default_config()

    if kind == "interval":
        try:
            n = int(txt)
            if n < 1 or n > 1440:
                raise ValueError()
            cfg["interval_min"] = n
            st.set_config(chat_id, cfg)
            await update.effective_message.reply_text("✅ Interval ذخیره شد.")
        except Exception:
            await update.effective_message.reply_text("فرمت اشتباه. مثال: 5 یا 10 یا 15")
    elif kind == "quiet":
        if txt == "" or txt.lower() == "off":
            cfg["quiet"] = ""
            st.set_config(chat_id, cfg)
            await update.effective_message.reply_text("✅ ساعات سکوت خاموش شد.")
        else:
            if not parse_quiet(txt):
                await update.effective_message.reply_text("فرمت اشتباه. مثال: 23:00-08:00")
            else:
                cfg["quiet"] = txt
                st.set_config(chat_id, cfg)
                await update.effective_message.reply_text("✅ ساعات سکوت ذخیره شد.")
    elif kind == "threshold":
        try:
            n = int(txt)
            if n < 0:
                raise ValueError()
            cfg["threshold"] = n
            st.set_config(chat_id, cfg)
            await update.effective_message.reply_text("✅ Threshold ذخیره شد.")
        except Exception:
            await update.effective_message.reply_text("عدد صحیح وارد کنید. مثال: 0 یا 1000")
    elif kind == "import":
        try:
            obj = json.loads(txt)
            if not isinstance(obj, dict):
                raise ValueError()
            # only accept known keys
            merged = default_config()
            merged.update({k: obj.get(k, merged.get(k)) for k in merged.keys()})
            st.set_config(chat_id, merged)
            await update.effective_message.reply_text("✅ Import انجام شد.")
        except Exception:
            await update.effective_message.reply_text("JSON نامعتبر است.")
    context.user_data.pop("PENDING", None)

    # Show panel again
    ch2 = st.get_chat(chat_id)
    await update.effective_message.reply_text(
        f"Control Panel: {ch2['title']}",
        reply_markup=kb_main(chat_id, bool(ch2["approved"]), ch2["config"] or default_config()),
    )


async def on_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q:
        return
    await q.answer()

    admin_ids = context.bot_data["ADMIN_IDS"]
    uid = update.effective_user.id if update.effective_user else 0
    if not is_admin(uid, admin_ids):
        await q.edit_message_text("⛔️ دسترسی ندارید.")
        return

    st: Storage = context.bot_data["STORAGE"]
    data = (q.data or "")
    parts = data.split("|")

    action = parts[0]

    if action == "noop":
        return

    if action in ("refresh", "back"):
        chats = st.list_chats()
        await q.edit_message_text("Select a chat to manage:", reply_markup=kb_chat_list(chats))
        return

    if action == "help":
        await q.edit_message_text("برای راهنما /help را بزنید.")
        return

    if action == "sel":
        chat_id = int(parts[1])
        ch = st.get_chat(chat_id)
        if not ch:
            await q.edit_message_text("چت پیدا نشد.")
            return
        cfg = ch["config"] or default_config()
        await q.edit_message_text(f"Control Panel: {ch['title']}", reply_markup=kb_main(chat_id, bool(ch["approved"]), cfg))
        return

    # Everything below needs chat_id
    chat_id = int(parts[1]) if len(parts) > 1 else 0
    ch = st.get_chat(chat_id)
    if not ch:
        await q.edit_message_text("چت پیدا نشد.")
        return
    cfg = ch["config"] or default_config()

    if action == "panel":
        await q.edit_message_text(f"Control Panel: {ch['title']}", reply_markup=kb_main(chat_id, bool(ch["approved"]), cfg))
        return

    if action == "approve":
        st.set_approved(chat_id, not bool(ch["approved"]))
        ch2 = st.get_chat(chat_id)
        await q.edit_message_text(
            f"Control Panel: {ch2['title']}",
            reply_markup=kb_main(chat_id, bool(ch2["approved"]), ch2["config"] or default_config()),
        )
        return

    if action == "auto":
        cfg["auto_send"] = not bool(cfg.get("auto_send"))
        st.set_config(chat_id, cfg)
        ch2 = st.get_chat(chat_id)
        await q.edit_message_text(
            f"Control Panel: {ch2['title']}",
            reply_markup=kb_main(chat_id, bool(ch2["approved"]), ch2["config"] or default_config()),
        )
        return

    if action == "toggle":
        what = parts[2]
        if what == "only":
            cfg["only_if_changed"] = not bool(cfg.get("only_if_changed"))
            st.set_config(chat_id, cfg)
        ch2 = st.get_chat(chat_id)
        await q.edit_message_text(
            f"Control Panel: {ch2['title']}",
            reply_markup=kb_main(chat_id, bool(ch2["approved"]), ch2["config"] or default_config()),
        )
        return

    if action == "menu":
        menu = parts[2]
        if menu in ("cur", "coin", "metal"):
            await q.edit_message_text(f"انتخاب {menu}:", reply_markup=kb_items(chat_id, menu, cfg))
            return
        if menu == "interval":
            await q.edit_message_text("Interval:", reply_markup=kb_interval(chat_id, cfg))
            return
        if menu == "quiet":
            await q.edit_message_text("Quiet hours:", reply_markup=kb_quiet(chat_id, cfg))
            return
        if menu == "sellbuy":
            await q.edit_message_text("Sell/Buy:", reply_markup=kb_sellbuy(chat_id, cfg))
            return
        if menu == "threshold":
            await q.edit_message_text("Threshold:", reply_markup=kb_threshold(chat_id, cfg))
            return
        if menu == "triggers":
            await q.edit_message_text("تریگرها:", reply_markup=kb_triggers(chat_id, cfg))
            return

    if action == "trigcat":
        cat = parts[2]
        await q.edit_message_text(f"تریگر {cat}:", reply_markup=kb_trig_items(chat_id, cat, cfg))
        return

    if action == "togitem":
        cat = parts[2]
        code = parts[3]
        sel = cfg.setdefault("selected", {}).setdefault(cat, [])
        if code in sel:
            sel.remove(code)
        else:
            sel.append(code)  # order = selection order
        st.set_config(chat_id, cfg)
        await q.edit_message_reply_markup(reply_markup=kb_items(chat_id, cat, cfg))
        return

    if action == "resetorder":
        cat = parts[2]
        cfg.setdefault("selected", {})[cat] = []
        st.set_config(chat_id, cfg)
        await q.edit_message_reply_markup(reply_markup=kb_items(chat_id, cat, cfg))
        return

    if action == "all":
        cat = parts[2]
        on = parts[3] == "1"
        cfg.setdefault("selected", {})[cat] = [i.code for i in CAT_BY_CAT[cat]] if on else []
        st.set_config(chat_id, cfg)
        await q.edit_message_reply_markup(reply_markup=kb_items(chat_id, cat, cfg))
        return

    if action == "togtrig":
        cat = parts[2]
        code = parts[3]
        tr = cfg.setdefault("triggers", {}).setdefault(cat, [])
        if code in tr:
            tr.remove(code)
        else:
            tr.append(code)
        st.set_config(chat_id, cfg)
        await q.edit_message_reply_markup(reply_markup=kb_trig_items(chat_id, cat, cfg))
        return

    if action == "trigall":
        cat = parts[2]
        mode = parts[3]  # 1 => all selected ; 0 => empty = all selected implicitly
        if mode == "1":
            cfg.setdefault("triggers", {})[cat] = list(cfg.get("selected", {}).get(cat, []))
        else:
            cfg.setdefault("triggers", {})[cat] = []
        st.set_config(chat_id, cfg)
        await q.edit_message_reply_markup(reply_markup=kb_trig_items(chat_id, cat, cfg))
        return

    if action == "setint":
        n = int(parts[2])
        cfg["interval_min"] = n
        st.set_config(chat_id, cfg)
        await q.edit_message_text("Interval:", reply_markup=kb_interval(chat_id, cfg))
        return

    if action == "clearquiet":
        cfg["quiet"] = ""
        st.set_config(chat_id, cfg)
        await q.edit_message_text("Quiet hours:", reply_markup=kb_quiet(chat_id, cfg))
        return

    if action == "setsb":
        mode = parts[2]
        cfg["sellbuy"] = "buy" if mode == "buy" else "sell"
        st.set_config(chat_id, cfg)
        await q.edit_message_text("Sell/Buy:", reply_markup=kb_sellbuy(chat_id, cfg))
        return

    if action == "setth":
        n = int(parts[2])
        cfg["threshold"] = n
        st.set_config(chat_id, cfg)
        await q.edit_message_text("Threshold:", reply_markup=kb_threshold(chat_id, cfg))
        return

    if action == "ask":
        kind = parts[2]
        # prompt user to type next message
        if kind == "interval":
            context.user_data["PENDING"] = {"chat_id": chat_id, "kind": "interval"}
            await q.edit_message_text("یک عدد دقیقه بفرستید. مثال: 5")
            return
        if kind == "quiet":
            context.user_data["PENDING"] = {"chat_id": chat_id, "kind": "quiet"}
            await q.edit_message_text("فرمت: 23:00-08:00  (یا OFF برای خاموش)")
            return
        if kind == "threshold":
            context.user_data["PENDING"] = {"chat_id": chat_id, "kind": "threshold"}
            await q.edit_message_text("یک عدد (تومان) بفرستید. مثال: 1000  (یا 0 برای خاموش)")
            return

    if action == "export":
        payload = json.dumps(cfg, ensure_ascii=False, indent=2)
        await q.edit_message_text(f"<pre>{payload}</pre>", parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("⬅️ Back", callback_data=f"panel|{chat_id}")],
             [InlineKeyboardButton("Import config…", callback_data=f"import|{chat_id}")]]
        ))
        return

    if action == "import":
        context.user_data["PENDING"] = {"chat_id": chat_id, "kind": "import"}
        await q.edit_message_text("JSON کانفیگ را همینجا Paste کنید:")
        return

    if action in ("test", "sendnow"):
        await q.edit_message_text("در حال ارسال…")
        await send_for_chat(context, chat_id, force=True, test=(action == "test"))
        ch2 = st.get_chat(chat_id)
        await q.message.reply_text(
            f"Control Panel: {ch2['title']}",
            reply_markup=kb_main(chat_id, bool(ch2["approved"]), ch2["config"] or default_config()),
        )
        return


# ---------------- Sending loop ----------------

async def send_for_chat(context: ContextTypes.DEFAULT_TYPE, chat_id: int, force: bool, test: bool = False) -> None:
    st: Storage = context.bot_data["STORAGE"]
    client: BonbastClient = context.bot_data["CLIENT"]

    ch = st.get_chat(chat_id)
    if not ch:
        return
    approved = bool(ch["approved"])
    cfg = ch["config"] or default_config()
    state = ch["state"] or {}

    if not force:
        if not approved or not cfg.get("auto_send"):
            return

    now = datetime.now(TZ)
    if not force and in_quiet(now, cfg.get("quiet", "")):
        return

    data = await client.fetch()

    # date/time from bonbast json if present
    try:
        y = str(data.get("year", "")).strip()
        mo = str(data.get("month", "")).strip()
        d = str(data.get("day", "")).strip()
        hh = str(data.get("hour", "")).strip()
        mm = str(data.get("min", "")).strip()
        if y and mo and d and hh and mm:
            dt_header = f"{y}/{mo}/{d} - {hh}:{mm}"
        else:
            dt_header = now.strftime("%Y/%m/%d - %H:%M")
    except Exception:
        dt_header = now.strftime("%Y/%m/%d - %H:%M")

    last_values = state.get("last_values", {})
    if not isinstance(last_values, dict):
        last_values = {}

    # Build message
    rtl = "\u200f"
    header = f"{rtl}✅ نرخ لحظه‌ای ارز و سکه\n{rtl}📅 {dt_header}\n"

    # For per-category comparisons
    last_cur = {k: float(v) for k, v in last_values.get("cur", {}).items()} if isinstance(last_values.get("cur"), dict) else {}
    last_coin = {k: float(v) for k, v in last_values.get("coin", {}).items()} if isinstance(last_values.get("coin"), dict) else {}
    last_met = {k: float(v) for k, v in last_values.get("metal", {}).items()} if isinstance(last_values.get("metal"), dict) else {}

    cur_lines, new_cur, cur_changed = build_lines(CURRENCIES, cfg, data, last_cur)
    coin_lines, new_coin, coin_changed = build_lines(COINS, cfg, data, last_coin)
    met_lines, new_met, met_changed = build_lines(METALS, cfg, data, last_met)

    # Determine if we should send (only_if_changed)
    should_send = True
    if cfg.get("only_if_changed"):
        should_send = cur_changed or coin_changed or met_changed

    # Always refresh last values so comparisons stay correct
    new_last_values = {
        "cur": new_cur,
        "coin": new_coin,
        "metal": new_met,
    }

    # For scheduled sends: avoid duplicates within same minute slot
    slot = now_slot_tehran(now)
    if not force:
        if state.get("last_slot") == slot:
            return

    state["last_values"] = new_last_values
    state["last_slot"] = slot
    st.set_state(chat_id, state)

    if not should_send and not force:
        return

    parts: List[str] = [header]
    if cur_lines:
        parts.append("\n".join(cur_lines))
    if coin_lines:
        parts.append("_______________________\n" + "\n".join(coin_lines))
    if met_lines:
        parts.append("_______________________\n" + "\n".join(met_lines))

    msg = "\n\n".join(parts).strip()

    target = context.bot_data["ADMIN_IDS"][0] if test else chat_id
    await context.bot.send_message(chat_id=target, text=msg)


async def sender_loop(app: Application) -> None:
    # Align to next minute boundary
    while True:
        now = datetime.now(TZ)
        sleep_s = 60 - now.second - (now.microsecond / 1_000_000)
        if sleep_s < 0.1:
            sleep_s = 0.1
        await asyncio.sleep(sleep_s)

        st: Storage = app.bot_data["STORAGE"]
        chats = st.list_chats()
        if not chats:
            continue

        now = datetime.now(TZ)
        for ch in chats:
            try:
                cfg = ch["config"] or default_config()
                if not ch["approved"] or not cfg.get("auto_send"):
                    continue
                interval = int(cfg.get("interval_min", 5) or 5)
                if interval < 1:
                    interval = 5
                if now.minute % interval != 0:
                    continue
                if in_quiet(now, cfg.get("quiet", "")):
                    continue
                await send_for_chat(app.bot_data["CTX"], ch["chat_id"], force=False)
                await asyncio.sleep(0.2)
            except Exception as e:
                LOG.exception("sender_loop chat error: %s", e)


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    LOG.exception("Unhandled error", exc_info=context.error)


# ---------------- Main ----------------

def parse_admin_ids(s: str) -> List[int]:
    out: List[int] = []
    for part in (s or "").split(","):
        part = part.strip()
        if not part:
            continue
        out.append(int(part))
    return out


def main() -> None:
    load_dotenv()

    token = os.getenv("BOT_TOKEN", "").strip()
    admin_ids_raw = os.getenv("ADMIN_IDS", "").strip()
    db_path = os.getenv("DB_PATH", "").strip() or "/root/bonbast-bot/app/data/bonbast.db"
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()

    logging.basicConfig(level=getattr(logging, log_level, logging.INFO), format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")

    if not token:
        raise SystemExit("BOT_TOKEN is missing in .env")
    if not admin_ids_raw:
        raise SystemExit("ADMIN_IDS is missing in .env (must contain your Telegram numeric user id)")

    admin_ids = parse_admin_ids(admin_ids_raw)

    st = Storage(db_path)
    client = BonbastClient()

    app = Application.builder().token(token).build()

    # Store global objects
    app.bot_data["ADMIN_IDS"] = admin_ids
    app.bot_data["STORAGE"] = st
    app.bot_data["CLIENT"] = client
    # Hack: pass context into sender_loop helper
    app.bot_data["CTX"] = type("X", (), {"bot_data": app.bot_data, "bot": app.bot})()

    # Handlers
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("panel", cmd_panel))
    app.add_handler(CommandHandler("register", cmd_register))
    app.add_handler(ChatMemberHandler(on_my_chat_member, ChatMemberHandler.MY_CHAT_MEMBER))
    app.add_handler(CallbackQueryHandler(on_cb))
    app.add_handler(MessageHandler(filters.TEXT & filters.ChatType.PRIVATE, on_text))
    app.add_error_handler(on_error)

    async def post_init(_: Application) -> None:
        app.create_task(sender_loop(app))

    app.post_init = post_init

    LOG.info("Starting bot…")
    app.run_polling(allowed_updates=Update.ALL_TYPES)  # ensure we get my_chat_member updates


if __name__ == "__main__":
    main()
