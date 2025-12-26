import asyncio
import logging
import os
import re
from typing import List, Optional

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ChatMemberHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from storage import Storage

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("bonbast-bot")


def parse_owner_ids(raw: str) -> List[int]:
    out = []
    for x in (raw or "").split(","):
        x = x.strip()
        if x.isdigit():
            out.append(int(x))
    return out


def is_owner(user_id: Optional[int], owners: List[int]) -> bool:
    return user_id is not None and user_id in owners


async def safe_dm(app: Application, user_id: int, text: str, reply_markup=None) -> None:
    try:
        await app.bot.send_message(chat_id=user_id, text=text, reply_markup=reply_markup)
    except Exception as e:
        # Bots can't message users unless user has started the bot first (common Telegram restriction) :contentReference[oaicite:2]{index=2}
        logger.warning("DM failed to %s: %r", user_id, e)


def kb_panel(chats) -> InlineKeyboardMarkup:
    rows = []
    for c in chats:
        status = "✅" if c.approved else "⏳"
        rows.append(
            [InlineKeyboardButton(f"{status} {c.title}", callback_data=f"panel:chat:{c.chat_id}")]
        )
    return InlineKeyboardMarkup(rows or [[InlineKeyboardButton("—", callback_data="noop")]])


def kb_manage(chat_id: int, approved: bool, enabled: bool, is_owner_user: bool) -> InlineKeyboardMarkup:
    rows = []

    # APPROVE BUTTONS (this is what you were missing)
    if is_owner_user and not approved:
        rows.append(
            [
                InlineKeyboardButton("✅ تایید", callback_data=f"chat:approve:{chat_id}"),
                InlineKeyboardButton("❌ رد", callback_data=f"chat:reject:{chat_id}"),
            ]
        )
    elif is_owner_user and approved:
        rows.append(
            [
                InlineKeyboardButton("✅ تایید شده", callback_data="noop"),
                InlineKeyboardButton("↩️ لغو تایید", callback_data=f"chat:unapprove:{chat_id}"),
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(
                f"ارسال خودکار: {'فعال ✅' if enabled else 'غیرفعال ❌'}",
                callback_data=f"chat:toggle_enabled:{chat_id}",
            )
        ]
    )

    # keep your UI layout: put your other buttons here if you already have them
    rows.append(
        [
            InlineKeyboardButton("🔁 ارسال فوری (Send now)", callback_data=f"chat:sendnow:{chat_id}"),
            InlineKeyboardButton("⬅️ بازگشت", callback_data="panel:list"),
        ]
    )

    return InlineKeyboardMarkup(rows)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("سلام!\nبرای مدیریت: /panel\nبرای ثبت گروه/کانال: /register")


async def cmd_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    owners: List[int] = context.application.bot_data["owners"]
    if not is_owner(update.effective_user.id if update.effective_user else None, owners):
        await update.message.reply_text("⛔ دسترسی ندارید.")
        return

    st: Storage = context.application.bot_data["storage"]
    chats = st.list_chats()
    if not chats:
        await update.message.reply_text(
            "No chats yet — add bot to a group/channel first\n"
            "اگر اضافه کردید ولی نیامد، داخل گروه/کانال یک‌بار بزنید:\n"
            "/register"
        )
        return

    await update.message.reply_text("Select a chat to manage:", reply_markup=kb_panel(chats))


async def cmd_register(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    IMPORTANT:
    - CommandHandler does NOT handle channel posts. So we also register a MessageHandler for CHANNEL_POSTS. :contentReference[oaicite:3]{index=3}
    - We register chat but keep approved=0 (owner must approve from panel).
    """
    st: Storage = context.application.bot_data["storage"]
    chat = update.effective_chat
    if not chat:
        return

    title = chat.title or getattr(chat, "username", None) or str(chat.id)
    st.upsert_chat(chat.id, title, chat.type)

    await update.effective_message.reply_text("✅ ثبت شد. وضعیت: ⏳ نیاز به تایید (از /panel تایید کنید).")


async def on_my_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Detect bot being added/removed using my_chat_member updates. :contentReference[oaicite:4]{index=4}
    """
    st: Storage = context.application.bot_data["storage"]
    owners: List[int] = context.application.bot_data["owners"]

    cmu = update.my_chat_member
    if not cmu:
        return

    chat = cmu.chat
    new_status = cmu.new_chat_member.status  # "member", "administrator", "left", "kicked", etc

    if new_status in ("member", "administrator"):
        title = chat.title or getattr(chat, "username", None) or str(chat.id)
        st.upsert_chat(chat.id, title, chat.type)

        # notify owners with approve buttons
        for oid in owners:
            await safe_dm(
                context.application,
                oid,
                f"➕ Bot added to:\n{title}\n({chat.id})\nStatus: ⏳ نیاز به تایید",
                reply_markup=InlineKeyboardMarkup(
                    [[
                        InlineKeyboardButton("✅ تایید", callback_data=f"chat:approve:{chat.id}"),
                        InlineKeyboardButton("❌ رد", callback_data=f"chat:reject:{chat.id}"),
                    ]]
                ),
            )

    elif new_status in ("left", "kicked"):
        logger.info("Bot removed from chat %s (%s)", chat.title, chat.id)


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q:
        return
    await q.answer()
    data = q.data or ""

    st: Storage = context.application.bot_data["storage"]
    owners: List[int] = context.application.bot_data["owners"]
    is_owner_user = is_owner(q.from_user.id if q.from_user else None, owners)

    if data == "noop":
        return

    if data == "panel:list":
        chats = st.list_chats()
        await q.edit_message_text("Select a chat to manage:", reply_markup=kb_panel(chats))
        return

    m = re.match(r"panel:chat:(-?\d+)$", data)
    if m:
        chat_id = int(m.group(1))
        row = st.get_chat(chat_id)
        if not row:
            await q.edit_message_text("Chat not found. Try /register in that chat.")
            return
        await q.edit_message_text(
            f"Control Panel: {row.title} ({row.chat_id})\n"
            f"وضعیت: {'✅ تایید شده' if row.approved else '⏳ نیاز به تایید'}",
            reply_markup=kb_manage(row.chat_id, row.approved, row.enabled, is_owner_user),
        )
        return

    m = re.match(r"chat:approve:(-?\d+)$", data)
    if m:
        if not is_owner_user:
            await q.answer("⛔ فقط مالک می‌تواند تایید کند.", show_alert=True)
            return
        chat_id = int(m.group(1))
        st.set_approved(chat_id, True)
        row = st.get_chat(chat_id)
        await q.edit_message_text(
            f"✅ تایید شد: {row.title} ({row.chat_id})",
            reply_markup=kb_manage(row.chat_id, row.approved, row.enabled, True),
        )
        return

    m = re.match(r"chat:reject:(-?\d+)$", data)
    if m:
        if not is_owner_user:
            await q.answer("⛔ فقط مالک می‌تواند رد کند.", show_alert=True)
            return
        chat_id = int(m.group(1))
        st.set_approved(chat_id, False)
        st.set_enabled(chat_id, False)
        row = st.get_chat(chat_id)
        await q.edit_message_text(
            f"❌ رد شد: {row.title} ({row.chat_id})",
            reply_markup=kb_manage(row.chat_id, row.approved, row.enabled, True),
        )
        return

    m = re.match(r"chat:unapprove:(-?\d+)$", data)
    if m:
        if not is_owner_user:
            await q.answer("⛔ فقط مالک.", show_alert=True)
            return
        chat_id = int(m.group(1))
        st.set_approved(chat_id, False)
        st.set_enabled(chat_id, False)
        row = st.get_chat(chat_id)
        await q.edit_message_text(
            f"↩️ لغو تایید شد: {row.title} ({row.chat_id})",
            reply_markup=kb_manage(row.chat_id, row.approved, row.enabled, True),
        )
        return

    m = re.match(r"chat:toggle_enabled:(-?\d+)$", data)
    if m:
        if not is_owner_user:
            await q.answer("⛔ فقط مالک.", show_alert=True)
            return
        chat_id = int(m.group(1))
        row = st.get_chat(chat_id)
        if not row:
            return
        if not row.approved:
            await q.answer("اول تایید کن ✅", show_alert=True)
            return
        st.set_enabled(chat_id, not row.enabled)
        row = st.get_chat(chat_id)
        await q.edit_message_text(
            f"Control Panel: {row.title} ({row.chat_id})\n"
            f"وضعیت: {'✅ تایید شده' if row.approved else '⏳ نیاز به تایید'}",
            reply_markup=kb_manage(row.chat_id, row.approved, row.enabled, True),
        )
        return

    m = re.match(r"chat:sendnow:(-?\d+)$", data)
    if m:
        chat_id = int(m.group(1))
        row = st.get_chat(chat_id)
        if not row:
            return
        if not is_owner_user or not row.approved:
            await q.answer("⛔ اجازه ندارید / یا تایید نشده.", show_alert=True)
            return
        await context.application.bot.send_message(chat_id=chat_id, text="(Send now) تست ارسال ✅")
        await q.answer("ارسال شد ✅", show_alert=False)
        return


def build_app() -> Application:
    load_dotenv()
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("BOT_TOKEN missing")

    owners = parse_owner_ids(os.getenv("OWNER_IDS", ""))
    db_path = os.getenv("DB_PATH", "bot.db")

    st = Storage(db_path)

    app = Application.builder().token(token).build()
    app.bot_data["owners"] = owners
    app.bot_data["storage"] = st

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("panel", cmd_panel))
    app.add_handler(CommandHandler("register", cmd_register))

    # CommandHandler does NOT catch channel posts -> add MessageHandler for CHANNEL_POSTS. :contentReference[oaicite:5]{index=5}
    app.add_handler(
        MessageHandler(
            filters.UpdateType.CHANNEL_POSTS & filters.Regex(r"^/register(@\w+)?(\s|$)"),
            cmd_register,
        )
    )

    # Detect bot added/removed via my_chat_member updates. :contentReference[oaicite:6]{index=6}
    app.add_handler(ChatMemberHandler(on_my_chat_member, chat_member_types=ChatMemberHandler.MY_CHAT_MEMBER))

    app.add_handler(CallbackQueryHandler(on_callback))
    return app


if __name__ == "__main__":
    app = build_app()
    # Ensure we receive member updates; using ALL_TYPES is a common fix when updates are missing. :contentReference[oaicite:7]{index=7}
    app.run_polling(allowed_updates=Update.ALL_TYPES)
