from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.constants import ChatType
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
from models import (
    ITEMS_BY_SECTION,
    ITEM_BY_ID,
    SEP,
    build_message,
)
from storage import ChatConfig, Storage

try:
    from zoneinfo import ZoneInfo
    TEHRAN = ZoneInfo("Asia/Tehran")
except Exception:
    TEHRAN = None  # fallback handled below


# ---------- ENV ----------
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
OWNER_IDS = os.environ.get("OWNER_IDS", "").strip()  # comma-separated ints
DB_PATH = os.environ.get("DB_PATH", "bot.db")

if not BOT_TOKEN:
    raise SystemExit("Missing BOT_TOKEN env var.")
if not OWNER_IDS:
    raise SystemExit("Missing OWNER_IDS env var (comma separated).")

OWNER_ID_SET = set()
for x in OWNER_IDS.split(","):
    x = x.strip()
    if x:
        OWNER_ID_SET.add(int(x))


# ---------- GLOBALS ----------
storage = Storage(DB_PATH)
client = BonbastClient()
last_seen_by_key: Dict[str, float | int | None] = {}
# Per-owner session: which chat they are managing in private
owner_session_chat: Dict[int, int] = {}


# ---------- UTIL ----------
def now_tehran() -> datetime:
    if TEHRAN:
        return datetime.now(TEHRAN)
    # Fallback: approximate Tehran as UTC+3:30 (not DST-safe). Prefer ZoneInfo if available.
    return datetime.utcnow() + timedelta(hours=3, minutes=30)

def is_owner(update: Update) -> bool:
    uid = update.effective_user.id if update.effective_user else 0
    return uid in OWNER_ID_SET

def fmt_chat(cfg: ChatConfig) -> str:
    return f"{cfg.title} ({cfg.chat_id})"

def parse_hhmm(s: str) -> Optional[Tuple[int, int]]:
    try:
        hh, mm = s.split(":")
        hh_i, mm_i = int(hh), int(mm)
        if 0 <= hh_i <= 23 and 0 <= mm_i <= 59:
            return hh_i, mm_i
    except Exception:
        return None
    return None

def in_quiet_hours(cfg: ChatConfig, t: datetime) -> bool:
    if not cfg.quiet_start or not cfg.quiet_end:
        return False
    a = parse_hhmm(cfg.quiet_start)
    b = parse_hhmm(cfg.quiet_end)
    if not a or not b:
        return False
    sh, sm = a
    eh, em = b
    start = t.replace(hour=sh, minute=sm, second=0, microsecond=0)
    end = t.replace(hour=eh, minute=em, second=0, microsecond=0)
    if start == end:
        return False
    # if window crosses midnight
    if end < start:
        return t >= start or t < end
    return start <= t < end

def next_aligned_run(from_time: datetime, interval_min: int) -> datetime:
    # Align to exact minute boundaries: 12:00, 12:05, 12:10, ...
    interval_min = max(1, int(interval_min))
    ft = from_time.replace(second=0, microsecond=0)
    m = ft.minute
    add = interval_min - (m % interval_min)
    if add == 0:
        add = interval_min
    return ft + timedelta(minutes=add)

def key_for_item(item_id: str, price_side: str) -> str:
    it = ITEM_BY_ID[item_id]
    if it.section == "markets":
        return it.sell_key
    if price_side == "sell":
        return it.sell_key
    return it.buy_key or it.sell_key

def changed_enough(prev: Optional[float], cur: Optional[float], min_abs: int, min_pct: float) -> bool:
    if prev is None or cur is None:
        return True
    try:
        diff = abs(cur - prev)
        if min_abs and diff >= float(min_abs):
            return True
        if min_pct:
            base = abs(prev) if abs(prev) > 1e-9 else 1.0
            pct = (diff / base) * 100.0
            if pct >= float(min_pct):
                return True
        # if thresholds are 0, any change triggers
        if (min_abs == 0) and (min_pct == 0.0) and (cur != prev):
            return True
        return False
    except Exception:
        return True

async def safe_send_or_edit(
    context: ContextTypes.DEFAULT_TYPE,
    cfg: ChatConfig,
    text: str,
) -> Optional[int]:
    # Returns message_id used (new or edited)
    if cfg.post_mode == "edit" and cfg.last_message_id:
        try:
            await context.bot.edit_message_text(
                chat_id=cfg.chat_id,
                message_id=cfg.last_message_id,
                text=text,
            )
            return cfg.last_message_id
        except Exception:
            pass

    msg = await context.bot.send_message(chat_id=cfg.chat_id, text=text)
    return msg.message_id


# ---------- UI BUILDERS ----------
def btn(text: str, cb: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=cb)

def main_menu_kb(chat_id: int) -> InlineKeyboardMarkup:
    cfg = storage.get_chat(chat_id)
    assert cfg

    enabled_txt = "✅ فعال" if cfg.enabled else "❌ غیرفعال"
    approved_txt = "✅ تایید شده" if cfg.approved else "⏳ نیاز به تایید"

    rows = [
        [btn(f"وضعیت: {approved_txt}", f"noop|{chat_id}")],
        [btn(f"ارسال خودکار: {enabled_txt}", f"toggle_enabled|{chat_id}")],
        [btn("انتخاب ارزها", f"sel|fx|{chat_id}|0"), btn("انتخاب سکه‌ها", f"sel|coins|{chat_id}|0")],
        [btn("طلا / بیت‌کوین", f"sel|markets|{chat_id}|0")],
        [btn("زمان‌بندی (Interval)", f"interval|{chat_id}"), btn("ساعات سکوت", f"quiet|{chat_id}")],
        [btn("فقط در صورت تغییر", f"onlychg|{chat_id}"), btn("تریگرها", f"triggers|{chat_id}")],
        [btn("حالت ارسال", f"postmode|{chat_id}"), btn("Sell/Buy", f"side|{chat_id}")],
        [btn("Threshold", f"th|{chat_id}"), btn("ارسال فوری (Send now)", f"sendnow|{chat_id}")],
        [btn("Export config", f"export|{chat_id}"), btn("Help", f"help|{chat_id}")],
    ]
    return InlineKeyboardMarkup(rows)

def section_kb(section: str, chat_id: int, page: int = 0) -> InlineKeyboardMarkup:
    cfg = storage.get_chat(chat_id)
    assert cfg
    items = ITEMS_BY_SECTION[section]  # type: ignore

    # pagination (2 columns). keep it clean like your screenshot.
    per_page = 16
    start = page * per_page
    end = start + per_page
    page_items = items[start:end]

    selected = set(
        cfg.selected_fx if section == "fx" else cfg.selected_coins if section == "coins" else cfg.selected_markets
    )

    rows: List[List[InlineKeyboardButton]] = []
    for i in range(0, len(page_items), 2):
        row = []
        for it in page_items[i:i+2]:
            mark = "✅" if it.item_id in selected else "❌"
            row.append(btn(f"{it.name_fa} {mark}", f"tog|{section}|{chat_id}|{it.item_id}|{page}"))
        rows.append(row)

    nav_row = []
    if start > 0:
        nav_row.append(btn("◀️", f"sel|{section}|{chat_id}|{page-1}"))
    nav_row.append(btn("بازگشت", f"menu|{chat_id}"))
    if end < len(items):
        nav_row.append(btn("▶️", f"sel|{section}|{chat_id}|{page+1}"))
    rows.append(nav_row)

    rows.append([btn("✅ انتخاب همه", f"all|{section}|{chat_id}|{page}"), btn("🧹 پاک کردن", f"clr|{section}|{chat_id}|{page}")])
    return InlineKeyboardMarkup(rows)

def interval_kb(chat_id: int) -> InlineKeyboardMarkup:
    cfg = storage.get_chat(chat_id); assert cfg
    options = [1, 2, 5, 10, 15, 30, 60, 120]
    rows = []
    row = []
    for v in options:
        label = f"{v}m" + (" ✅" if cfg.interval_min == v else "")
        row.append(btn(label, f"setint|{chat_id}|{v}"))
        if len(row) == 4:
            rows.append(row); row=[]
    if row:
        rows.append(row)
    rows.append([btn("Custom (send number)", f"askint|{chat_id}"), btn("Back", f"menu|{chat_id}")])
    return InlineKeyboardMarkup(rows)

def quiet_kb(chat_id: int) -> InlineKeyboardMarkup:
    cfg = storage.get_chat(chat_id); assert cfg
    cur = f"{cfg.quiet_start}-{cfg.quiet_end}" if cfg.quiet_start and cfg.quiet_end else "خالی"
    rows = [
        [btn(f"اکنون: {cur}", f"noop|{chat_id}")],
        [btn("Clear quiet hours", f"clrquiet|{chat_id}")],
        [btn("Set preset 23:00-07:00", f"setquiet|{chat_id}|23:00|07:00")],
        [btn("Custom (send HH:MM-HH:MM)", f"askquiet|{chat_id}")],
        [btn("Back", f"menu|{chat_id}")],
    ]
    return InlineKeyboardMarkup(rows)

def postmode_kb(chat_id: int) -> InlineKeyboardMarkup:
    cfg = storage.get_chat(chat_id); assert cfg
    return InlineKeyboardMarkup([
        [btn("New message" + (" ✅" if cfg.post_mode=="new" else ""), f"setpm|{chat_id}|new"),
         btn("Edit last" + (" ✅" if cfg.post_mode=="edit" else ""), f"setpm|{chat_id}|edit")],
        [btn("Back", f"menu|{chat_id}")]
    ])

def side_kb(chat_id: int) -> InlineKeyboardMarkup:
    cfg = storage.get_chat(chat_id); assert cfg
    return InlineKeyboardMarkup([
        [btn("Sell" + (" ✅" if cfg.price_side=="sell" else ""), f"setside|{chat_id}|sell"),
         btn("Buy" + (" ✅" if cfg.price_side=="buy" else ""), f"setside|{chat_id}|buy")],
        [btn("Back", f"menu|{chat_id}")]
    ])

def onlychg_kb(chat_id: int) -> InlineKeyboardMarkup:
    cfg = storage.get_chat(chat_id); assert cfg
    return InlineKeyboardMarkup([
        [btn(("✅ روشن" if cfg.only_on_change else "❌ خاموش"), f"togonly|{chat_id}")],
        [btn("Back", f"menu|{chat_id}")]
    ])

def triggers_kb(chat_id: int) -> InlineKeyboardMarkup:
    cfg = storage.get_chat(chat_id); assert cfg

    # triggers default to selected items if empty
    if not cfg.trigger_items:
        cfg.trigger_items = list(dict.fromkeys(cfg.selected_fx + cfg.selected_coins + cfg.selected_markets))
        storage.save(cfg)

    trig = set(cfg.trigger_items)

    # show compact list: first 10 triggers as buttons, and a manage screen per section
    rows = [
        [btn("Manage triggers: ارزها", f"trsel|fx|{chat_id}|0")],
        [btn("Manage triggers: سکه‌ها", f"trsel|coins|{chat_id}|0")],
        [btn("Manage triggers: طلا/BTC", f"trsel|markets|{chat_id}|0")],
        [btn("Use ALL selected items as triggers", f"trallsel|{chat_id}")],
        [btn("Back", f"menu|{chat_id}")]
    ]
    return InlineKeyboardMarkup(rows)

def trigger_section_kb(section: str, chat_id: int, page: int=0) -> InlineKeyboardMarkup:
    cfg = storage.get_chat(chat_id); assert cfg
    items = ITEMS_BY_SECTION[section]  # type: ignore
    trig = set(cfg.trigger_items)

    per_page = 16
    start = page*per_page
    end = start+per_page
    page_items = items[start:end]

    rows=[]
    for i in range(0, len(page_items), 2):
        row=[]
        for it in page_items[i:i+2]:
            mark="✅" if it.item_id in trig else "❌"
            row.append(btn(f"{it.name_fa} {mark}", f"trtog|{section}|{chat_id}|{it.item_id}|{page}"))
        rows.append(row)

    nav=[]
    if start>0: nav.append(btn("◀️", f"trsel|{section}|{chat_id}|{page-1}"))
    nav.append(btn("Back", f"triggers|{chat_id}"))
    if end<len(items): nav.append(btn("▶️", f"trsel|{section}|{chat_id}|{page+1}"))
    rows.append(nav)
    rows.append([btn("✅ Add all in section", f"traddsec|{section}|{chat_id}|{page}"),
                 btn("🧹 Clear section", f"trclrsec|{section}|{chat_id}|{page}")])
    return InlineKeyboardMarkup(rows)

def threshold_kb(chat_id: int) -> InlineKeyboardMarkup:
    cfg = storage.get_chat(chat_id); assert cfg
    abs_opts = [0, 50, 100, 200, 500, 1000, 5000]
    pct_opts = [0.0, 0.1, 0.2, 0.5, 1.0]

    rows = [[btn(f"Abs: {cfg.min_abs_change}t", f"noop|{chat_id}"), btn(f"Pct: {cfg.min_pct_change}%", f"noop|{chat_id}")]]
    row=[]
    for v in abs_opts:
        row.append(btn(f"{v}t" + (" ✅" if cfg.min_abs_change==v else ""), f"setabs|{chat_id}|{v}"))
        if len(row)==4:
            rows.append(row); row=[]
    if row: rows.append(row)

    row=[]
    for v in pct_opts:
        row.append(btn(f"{v}%" + (" ✅" if cfg.min_pct_change==v else ""), f"setpct|{chat_id}|{v}"))
    rows.append(row)
    rows.append([btn("Back", f"menu|{chat_id}")])
    return InlineKeyboardMarkup(rows)


# ---------- SCHEDULER ----------
def schedule_job(app: Application, cfg: ChatConfig) -> None:
    # remove existing job
    job_name = f"post:{cfg.chat_id}"
    for j in app.job_queue.get_jobs_by_name(job_name):
        j.schedule_removal()

    if not (cfg.approved and cfg.enabled):
        return

    nt = next_aligned_run(now_tehran(), cfg.interval_min)
    # run in UTC
    when_utc = nt.astimezone(datetime.utcnow().astimezone().tzinfo) if nt.tzinfo else nt
    app.job_queue.run_once(post_job, when=when_utc, name=job_name, data={"chat_id": cfg.chat_id})


async def post_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = int(context.job.data["chat_id"])
    cfg = storage.get_chat(chat_id)
    if not cfg or not (cfg.approved and cfg.enabled):
        return

    # keep alignment based on scheduled time
    scheduled_utc = context.job.scheduled_run_time
    scheduled_local = scheduled_utc.astimezone(TEHRAN) if TEHRAN else now_tehran().replace(second=0, microsecond=0)

    # quiet hours check based on Tehran time
    if in_quiet_hours(cfg, scheduled_local):
        # schedule next
        next_time = (scheduled_local + timedelta(minutes=cfg.interval_min)).replace(second=0, microsecond=0)
        when_utc = next_time.astimezone(datetime.utcnow().astimezone().tzinfo) if next_time.tzinfo else next_time
        context.job_queue.run_once(post_job, when=when_utc, name=context.job.name, data={"chat_id": chat_id})
        return

    # fetch data
    data = await client.fetch()

    # only-on-change logic
    if cfg.only_on_change:
        triggers = cfg.trigger_items or (cfg.selected_fx + cfg.selected_coins + cfg.selected_markets)
        changed = False
        for it_id in triggers:
            k = key_for_item(it_id, cfg.price_side)
            cur = data.get(k)
            prev = cfg.last_posted.get(k)
            try:
                cur_f = float(cur) if cur is not None else None
                prev_f = float(prev) if prev is not None else None
            except Exception:
                cur_f = None
                prev_f = None
            if changed_enough(prev_f, cur_f, cfg.min_abs_change, cfg.min_pct_change):
                # if thresholds=0 and values equal, ignore
                if prev is None or cur is None or cur != prev:
                    changed = True
                    break
        if not changed:
            # schedule next
            next_time = (scheduled_local + timedelta(minutes=cfg.interval_min)).replace(second=0, microsecond=0)
            when_utc = next_time.astimezone(datetime.utcnow().astimezone().tzinfo) if next_time.tzinfo else next_time
            context.job_queue.run_once(post_job, when=when_utc, name=context.job.name, data={"chat_id": chat_id})
            return

    text = build_message(
        data=data,
        selected_fx=cfg.selected_fx,
        selected_coins=cfg.selected_coins,
        selected_markets=cfg.selected_markets,
        price_side=cfg.price_side,  # sell/buy
        last_seen_by_key=last_seen_by_key,
    )

    mid = await safe_send_or_edit(context, cfg, text)
    if mid:
        cfg.last_message_id = mid

    # update last_posted snapshot (only store keys we use)
    snap = {}
    for it_id in (cfg.selected_fx + cfg.selected_coins + cfg.selected_markets):
        k = key_for_item(it_id, cfg.price_side)
        if k in data:
            snap[k] = data.get(k)
    cfg.last_posted = snap
    storage.save(cfg)

    # schedule next aligned time
    next_time = (scheduled_local + timedelta(minutes=cfg.interval_min)).replace(second=0, microsecond=0)
    when_utc = next_time.astimezone(datetime.utcnow().astimezone().tzinfo) if next_time.tzinfo else next_time
    context.job_queue.run_once(post_job, when=when_utc, name=context.job.name, data={"chat_id": chat_id})


# ---------- HANDLERS ----------
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_owner(update):
        return
    await cmd_panel(update, context)

async def cmd_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_owner(update):
        return

    chat = update.effective_chat
    assert chat

    # if private: pick last session chat or show chooser
    if chat.type == ChatType.PRIVATE:
        chats = storage.list_chats()
        # If owner has a session chat, show its menu
        sid = owner_session_chat.get(update.effective_user.id) if update.effective_user else None
        if sid and storage.get_chat(sid):
            cfg = storage.get_chat(sid); assert cfg
            await update.message.reply_text(f"Managing: {fmt_chat(cfg)}", reply_markup=main_menu_kb(cfg.chat_id))
            return

        # show chooser: approved first, then pending
        approved = [c for c in chats if c.approved]
        pending = [c for c in chats if not c.approved and c.chat_type != ChatType.PRIVATE]
        rows = []
        for c in approved[:20]:
            rows.append([btn(f"✅ {c.title}", f"pick|{c.chat_id}")])
        for c in pending[:20]:
            rows.append([btn(f"⏳ {c.title}", f"pick|{c.chat_id}")])
        if not rows:
            rows = [[btn("No chats yet — add bot to a group/channel first", "noop|0")]]
        await update.message.reply_text("Select a chat to manage:", reply_markup=InlineKeyboardMarkup(rows))
        return

    # non-private: manage this chat
    cfg = storage.upsert_chat(chat.id, chat.title or str(chat.id), chat.type)
    await update.message.reply_text("Control Panel:", reply_markup=main_menu_kb(cfg.chat_id))

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_owner(update):
        return
    text = (
        "Help (Examples)\n\n"
        "1) Approve/deny chats:\n"
        "- Add bot to a channel/group.\n"
        "- Bot PMs you with Approve button.\n\n"
        "2) Interval (aligned):\n"
        "- Set 5m => posts 12:00, 12:05, 12:10 ...\n\n"
        "3) Quiet hours:\n"
        "- Set 23:00-07:00 => no posts in that Tehran-time window.\n\n"
        "4) Only-on-change + triggers:\n"
        "- Enable 'فقط در صورت تغییر'\n"
        "- Set triggers (e.g. USD, Emami)\n"
        "- Optional thresholds: Abs=200t or Pct=0.2%\n\n"
        "5) Post mode:\n"
        "- New message: posts each time\n"
        "- Edit last: keeps one message updated (needs channel edit permission)\n\n"
        "Commands:\n"
        "/panel  open control panel\n"
        "/help   show help\n"
    )
    await update.message.reply_text(text)

async def on_my_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Detect when bot is added somewhere -> create chat row, mark pending, notify owner
    chat = update.effective_chat
    if not chat:
        return

    cfg = storage.upsert_chat(chat.id, chat.title or str(chat.id), chat.type)
    # private chats don't need approval logic
    if chat.type == ChatType.PRIVATE:
        cfg.approved = True
        storage.save(cfg)
        return

    # Keep pending until owner approves
    if not cfg.approved:
        storage.save(cfg)
        for oid in OWNER_ID_SET:
            kb = InlineKeyboardMarkup([
                [btn("✅ Approve", f"approve|{cfg.chat_id}"), btn("❌ Deny", f"deny|{cfg.chat_id}")],
            ])
            try:
                await context.bot.send_message(
                    chat_id=oid,
                    text=f"Bot added to: {fmt_chat(cfg)}\nApprove to allow posting/config.",
                    reply_markup=kb,
                )
            except Exception:
                pass

async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q:
        return
    await q.answer()

    if not is_owner(update):
        return

    parts = (q.data or "").split("|")
    act = parts[0]

    def get_cfg(cid: int) -> ChatConfig:
        cfg = storage.get_chat(cid)
        if not cfg:
            raise RuntimeError("Unknown chat.")
        return cfg

    # chooser
    if act == "pick":
        cid = int(parts[1])
        owner_session_chat[update.effective_user.id] = cid
        cfg = get_cfg(cid)
        await q.edit_message_text(f"Managing: {fmt_chat(cfg)}", reply_markup=main_menu_kb(cid))
        return

    if act == "menu":
        cid = int(parts[1])
        cfg = get_cfg(cid)
        await q.edit_message_text(f"Control Panel: {fmt_chat(cfg)}", reply_markup=main_menu_kb(cid))
        return

    if act == "noop":
        return

    if act == "approve":
        cid = int(parts[1])
        cfg = get_cfg(cid)
        cfg.approved = True
        storage.save(cfg)
        schedule_job(context.application, cfg)
        await q.edit_message_text(f"Approved ✅\n{fmt_chat(cfg)}", reply_markup=main_menu_kb(cid))
        return

    if act == "deny":
        cid = int(parts[1])
        cfg = get_cfg(cid)
        cfg.approved = False
        cfg.enabled = False
        storage.save(cfg)
        schedule_job(context.application, cfg)
        await q.edit_message_text(f"Denied ❌\n{fmt_chat(cfg)}")
        return

    if act == "toggle_enabled":
        cid = int(parts[1])
        cfg = get_cfg(cid)
        if not cfg.approved:
            await q.edit_message_text("This chat is not approved yet.", reply_markup=main_menu_kb(cid))
            return
        cfg.enabled = not cfg.enabled
        storage.save(cfg)
        schedule_job(context.application, cfg)
        await q.edit_message_text(f"Updated ✅\n{fmt_chat(cfg)}", reply_markup=main_menu_kb(cid))
        return

    if act == "sel":
        section = parts[1]
        cid = int(parts[2])
        page = int(parts[3])
        cfg = get_cfg(cid)
        await q.edit_message_text(f"Select {section}:", reply_markup=section_kb(section, cid, page))
        return

    if act == "tog":
        section = parts[1]
        cid = int(parts[2])
        item_id = parts[3]
        page = int(parts[4])
        cfg = get_cfg(cid)

        target = cfg.selected_fx if section == "fx" else cfg.selected_coins if section == "coins" else cfg.selected_markets
        if item_id in target:
            target.remove(item_id)
        else:
            target.append(item_id)

        # If triggers empty, keep it in sync with selection by default
        if not cfg.trigger_items:
            cfg.trigger_items = list(dict.fromkeys(cfg.selected_fx + cfg.selected_coins + cfg.selected_markets))

        storage.save(cfg)
        await q.edit_message_reply_markup(reply_markup=section_kb(section, cid, page))
        return

    if act == "all":
        section = parts[1]
        cid = int(parts[2])
        page = int(parts[3])
        cfg = get_cfg(cid)
        items = ITEMS_BY_SECTION[section]  # type: ignore
        target = cfg.selected_fx if section == "fx" else cfg.selected_coins if section == "coins" else cfg.selected_markets
        target[:] = [it.item_id for it in items]
        storage.save(cfg)
        await q.edit_message_reply_markup(reply_markup=section_kb(section, cid, page))
        return

    if act == "clr":
        section = parts[1]
        cid = int(parts[2])
        page = int(parts[3])
        cfg = get_cfg(cid)
        target = cfg.selected_fx if section == "fx" else cfg.selected_coins if section == "coins" else cfg.selected_markets
        target.clear()
        storage.save(cfg)
        await q.edit_message_reply_markup(reply_markup=section_kb(section, cid, page))
        return

    if act == "interval":
        cid = int(parts[1])
        await q.edit_message_text("Select interval:", reply_markup=interval_kb(cid))
        return

    if act == "setint":
        cid = int(parts[1])
        v = int(parts[2])
        cfg = get_cfg(cid)
        cfg.interval_min = max(1, v)
        storage.save(cfg)
        schedule_job(context.application, cfg)
        await q.edit_message_text("Interval updated ✅", reply_markup=main_menu_kb(cid))
        return

    if act == "askint":
        cid = int(parts[1])
        context.user_data["await_int_for"] = cid
        await q.edit_message_text("Send interval minutes as a number (e.g. 5).")
        return

    if act == "quiet":
        cid = int(parts[1])
        await q.edit_message_text("Quiet hours:", reply_markup=quiet_kb(cid))
        return

    if act == "clrquiet":
        cid = int(parts[1])
        cfg = get_cfg(cid)
        cfg.quiet_start = ""
        cfg.quiet_end = ""
        storage.save(cfg)
        await q.edit_message_text("Quiet hours cleared ✅", reply_markup=main_menu_kb(cid))
        return

    if act == "setquiet":
        cid = int(parts[1])
        qs = parts[2]
        qe = parts[3]
        cfg = get_cfg(cid)
        cfg.quiet_start = qs
        cfg.quiet_end = qe
        storage.save(cfg)
        await q.edit_message_text("Quiet hours updated ✅", reply_markup=main_menu_kb(cid))
        return

    if act == "askquiet":
        cid = int(parts[1])
        context.user_data["await_quiet_for"] = cid
        await q.edit_message_text("Send quiet hours as HH:MM-HH:MM (e.g. 23:00-07:00).")
        return

    if act == "onlychg":
        cid = int(parts[1])
        await q.edit_message_text("Only on change:", reply_markup=onlychg_kb(cid))
        return

    if act == "togonly":
        cid = int(parts[1])
        cfg = get_cfg(cid)
        cfg.only_on_change = not cfg.only_on_change
        # default triggers from selection if empty
        if not cfg.trigger_items:
            cfg.trigger_items = list(dict.fromkeys(cfg.selected_fx + cfg.selected_coins + cfg.selected_markets))
        storage.save(cfg)
        await q.edit_message_text("Updated ✅", reply_markup=main_menu_kb(cid))
        return

    if act == "postmode":
        cid = int(parts[1])
        await q.edit_message_text("Post mode:", reply_markup=postmode_kb(cid))
        return

    if act == "setpm":
        cid = int(parts[1])
        mode = parts[2]
        cfg = get_cfg(cid)
        cfg.post_mode = mode
        storage.save(cfg)
        await q.edit_message_text("Updated ✅", reply_markup=main_menu_kb(cid))
        return

    if act == "side":
        cid = int(parts[1])
        await q.edit_message_text("Sell/Buy:", reply_markup=side_kb(cid))
        return

    if act == "setside":
        cid = int(parts[1])
        side = parts[2]
        cfg = get_cfg(cid)
        cfg.price_side = side
        storage.save(cfg)
        await q.edit_message_text("Updated ✅", reply_markup=main_menu_kb(cid))
        return

    if act == "triggers":
        cid = int(parts[1])
        await q.edit_message_text("Triggers:", reply_markup=triggers_kb(cid))
        return

    if act == "trsel":
        section = parts[1]
        cid = int(parts[2])
        page = int(parts[3])
        await q.edit_message_text("Select trigger items:", reply_markup=trigger_section_kb(section, cid, page))
        return

    if act == "trtog":
        section = parts[1]
        cid = int(parts[2])
        item_id = parts[3]
        page = int(parts[4])
        cfg = get_cfg(cid)
        if item_id in cfg.trigger_items:
            cfg.trigger_items.remove(item_id)
        else:
            cfg.trigger_items.append(item_id)
        storage.save(cfg)
        await q.edit_message_reply_markup(reply_markup=trigger_section_kb(section, cid, page))
        return

    if act == "trallsel":
        cid = int(parts[1])
        cfg = get_cfg(cid)
        cfg.trigger_items = list(dict.fromkeys(cfg.selected_fx + cfg.selected_coins + cfg.selected_markets))
        storage.save(cfg)
        await q.edit_message_text("Triggers set to ALL selected items ✅", reply_markup=main_menu_kb(cid))
        return

    if act == "traddsec":
        section = parts[1]
        cid = int(parts[2])
        page = int(parts[3])
        cfg = get_cfg(cid)
        for it in ITEMS_BY_SECTION[section]:  # type: ignore
            if it.item_id not in cfg.trigger_items:
                cfg.trigger_items.append(it.item_id)
        storage.save(cfg)
        await q.edit_message_reply_markup(reply_markup=trigger_section_kb(section, cid, page))
        return

    if act == "trclrsec":
        section = parts[1]
        cid = int(parts[2])
        page = int(parts[3])
        cfg = get_cfg(cid)
        sec_ids = {it.item_id for it in ITEMS_BY_SECTION[section]}  # type: ignore
        cfg.trigger_items = [x for x in cfg.trigger_items if x not in sec_ids]
        storage.save(cfg)
        await q.edit_message_reply_markup(reply_markup=trigger_section_kb(section, cid, page))
        return

    if act == "th":
        cid = int(parts[1])
        await q.edit_message_text("Thresholds:", reply_markup=threshold_kb(cid))
        return

    if act == "setabs":
        cid = int(parts[1]); v = int(parts[2])
        cfg = get_cfg(cid)
        cfg.min_abs_change = v
        storage.save(cfg)
        await q.edit_message_text("Updated ✅", reply_markup=main_menu_kb(cid))
        return

    if act == "setpct":
        cid = int(parts[1]); v = float(parts[2])
        cfg = get_cfg(cid)
        cfg.min_pct_change = v
        storage.save(cfg)
        await q.edit_message_text("Updated ✅", reply_markup=main_menu_kb(cid))
        return

    if act == "sendnow":
        cid = int(parts[1])
        cfg = get_cfg(cid)
        if not (cfg.approved and cfg.enabled):
            # allow manual send even if enabled off, but require approved
            if not cfg.approved:
                await q.edit_message_text("Chat not approved yet.")
                return
        data = await client.fetch()
        text = build_message(
            data=data,
            selected_fx=cfg.selected_fx,
            selected_coins=cfg.selected_coins,
            selected_markets=cfg.selected_markets,
            price_side=cfg.price_side,
            last_seen_by_key=last_seen_by_key,
        )
        mid = await safe_send_or_edit(context, cfg, text)
        if mid:
            cfg.last_message_id = mid
        snap={}
        for it_id in (cfg.selected_fx + cfg.selected_coins + cfg.selected_markets):
            k = key_for_item(it_id, cfg.price_side)
            if k in data:
                snap[k]=data.get(k)
        cfg.last_posted = snap
        storage.save(cfg)
        await q.edit_message_text("Sent ✅", reply_markup=main_menu_kb(cid))
        return

    if act == "export":
        cid = int(parts[1])
        cfg = get_cfg(cid)
        payload = {
            "chat_id": cfg.chat_id,
            "title": cfg.title,
            "approved": cfg.approved,
            "enabled": cfg.enabled,
            "interval_min": cfg.interval_min,
            "quiet_start": cfg.quiet_start,
            "quiet_end": cfg.quiet_end,
            "only_on_change": cfg.only_on_change,
            "post_mode": cfg.post_mode,
            "price_side": cfg.price_side,
            "selected_fx": cfg.selected_fx,
            "selected_coins": cfg.selected_coins,
            "selected_markets": cfg.selected_markets,
            "trigger_items": cfg.trigger_items,
            "min_abs_change": cfg.min_abs_change,
            "min_pct_change": cfg.min_pct_change,
        }
        await q.edit_message_text("```json\n" + json.dumps(payload, ensure_ascii=False, indent=2) + "\n```", reply_markup=main_menu_kb(cid))
        return

    if act == "help":
        cid = int(parts[1])
        await q.edit_message_text(
            "Help:\n"
            "- Use the buttons to select items.\n"
            "- Interval is aligned (12:00, 12:05...).\n"
            "- Quiet hours use Tehran time.\n"
            "- Only-on-change checks your trigger items.\n"
            "- Post mode 'Edit last' keeps one message updated.\n"
            "Examples:\n"
            "Quiet: 23:00-07:00\n"
            "Trigger: USD + Emami\n"
            "Threshold: Abs=200t\n",
            reply_markup=main_menu_kb(cid),
        )
        return


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_owner(update):
        return
    txt = (update.message.text or "").strip()

    # awaiting custom interval?
    if "await_int_for" in context.user_data:
        cid = int(context.user_data.pop("await_int_for"))
        cfg = storage.get_chat(cid)
        if not cfg:
            return
        try:
            v = int(txt)
            cfg.interval_min = max(1, v)
            storage.save(cfg)
            schedule_job(context.application, cfg)
            await update.message.reply_text("Interval updated ✅", reply_markup=main_menu_kb(cid))
        except Exception:
            await update.message.reply_text("Invalid number. Example: 5")
        return

    if "await_quiet_for" in context.user_data:
        cid = int(context.user_data.pop("await_quiet_for"))
        cfg = storage.get_chat(cid)
        if not cfg:
            return
        # format HH:MM-HH:MM
        if "-" not in txt:
            await update.message.reply_text("Invalid format. Example: 23:00-07:00")
            return
        a, b = txt.split("-", 1)
        if not parse_hhmm(a.strip()) or not parse_hhmm(b.strip()):
            await update.message.reply_text("Invalid time. Example: 23:00-07:00")
            return
        cfg.quiet_start = a.strip()
        cfg.quiet_end = b.strip()
        storage.save(cfg)
        await update.message.reply_text("Quiet hours updated ✅", reply_markup=main_menu_kb(cid))
        return


async def cmd_shutdown(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_owner(update):
        return
    await update.message.reply_text("Shutting down…")
    await client.aclose()
    await context.application.stop()


# ---------- MAIN ----------
async def warmup() -> None:
    # Pre-fetch once so arrows can show direction soon after startup (like site behavior).
    try:
        data = await client.fetch()
        # store a small baseline
        for k, v in data.items():
            if isinstance(v, (int, float)):
                last_seen_by_key[k] = v
    except Exception:
        pass


def build_app() -> Application:
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("panel", cmd_panel))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("shutdown", cmd_shutdown))

    app.add_handler(ChatMemberHandler(on_my_chat_member, ChatMemberHandler.MY_CHAT_MEMBER))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    return app


async def main() -> None:
    app = build_app()

    # schedule existing approved/enabled chats
    for cfg in storage.list_chats(only_approved=True):
        schedule_job(app, cfg)

    await warmup()
    await app.initialize()
    await app.start()
    await app.updater.start_polling(allowed_updates=Update.ALL_TYPES)  # type: ignore

    print("Bot running.")
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
