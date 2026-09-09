from pyrogram import filters
from pyrogram.types import Message, InlineKeyboardMarkup, CallbackQuery

import asyncio
import logging

from bot_instance import app
from config import OWNER_ID, AUTO_CARD_POINTS, AUTO_CARD_INTERVAL_SECONDS
from database.models import (
    is_admin, is_owner, add_admin, remove_admin, list_admins,
    set_log_channel, get_log_channel, next_card_id, add_character,
    set_leaderboard_image, list_characters_page, delete_character,
    count_active_groups, count_all_groups, count_total_users,
    count_guesses_since, count_guesses_total, total_characters,
    ban_user, unban_user,
)
from utils.timeutils import ist_today_start_epoch
from utils.character_api import fetch_random_character
from utils.state import (
    start_card_flow, get_card_flow, update_card_flow, set_card_step,
    cancel_card_flow, STEP_WAIT_PHOTO, STEP_WAIT_NAME, STEP_WAIT_ANIME,
    STEP_WAIT_RARITY, STEP_WAIT_POINTS, STEP_CONFIRM,
    is_web_fetch_running, start_web_fetch_task, stop_web_fetch_task,
)
from utils.formatting import card_preview_caption, log_channel_caption
from utils.keyboards import inline_btn

log = logging.getLogger("guessbot.admin")


# ---------------- Admin management ----------------

@app.on_message(filters.command("addadmin") & filters.private)
async def addadmin_cmd(client, message: Message):
    if not await is_owner(message.from_user.id):
        await message.reply_text("⛔ Only the bot owner can add admins.")
        return

    target = None
    if message.reply_to_message:
        target = message.reply_to_message.from_user
    elif len(message.command) > 1 and message.command[1].isdigit():
        target_id = int(message.command[1])
        target = type("Obj", (), {"id": target_id})()

    if not target:
        await message.reply_text("↩️ Reply to a user's message with /addadmin, or use /addadmin <user_id>.")
        return

    await add_admin(target.id, message.from_user.id)
    await message.reply_text(f"✅ Added <code>{target.id}</code> as bot admin.")


@app.on_message(filters.command("removeadmin") & filters.private)
async def removeadmin_cmd(client, message: Message):
    if not await is_owner(message.from_user.id):
        await message.reply_text("⛔ Only the bot owner can remove admins.")
        return
    if len(message.command) < 2 or not message.command[1].isdigit():
        await message.reply_text("Usage: /removeadmin <user_id>")
        return
    await remove_admin(int(message.command[1]))
    await message.reply_text("✅ Removed.")


@app.on_message(filters.command("admins") & filters.private)
async def admins_cmd(client, message: Message):
    if not await is_admin(message.from_user.id):
        return
    admins = await list_admins()
    if not admins:
        await message.reply_text(f"Owner: <code>{OWNER_ID}</code>\nNo extra admins added yet.")
        return
    lines = [f"Owner: <code>{OWNER_ID}</code>", "Admins:"]
    lines += [f"• <code>{a['user_id']}</code>" for a in admins]
    await message.reply_text("\n".join(lines))


# ---------------- Log channel setup ----------------
# Two ways to set the logs channel (bot must already be admin there):
#   1. Owner posts /addlog directly inside the channel (works if not posting anonymously).
#   2. Owner DMs the bot: /addlog @channelusername  or  /addlog -100xxxxxxxxxx

@app.on_message(filters.command("addlog") & filters.channel)
async def addlog_cmd(client, message: Message):
    if not message.from_user or not await is_owner(message.from_user.id):
        return
    await set_log_channel(message.chat.id)
    await message.reply_text(f"✅ This channel (<code>{message.chat.id}</code>) is now set as the card logs channel.")


@app.on_message(filters.command("addlog") & filters.private)
async def addlog_private_cmd(client, message: Message):
    if not await is_owner(message.from_user.id):
        await message.reply_text("⛔ Only the bot owner can set the logs channel.")
        return
    if len(message.command) < 2:
        await message.reply_text(
            "Usage: /addlog @channelusername  or  /addlog -100xxxxxxxxxx\n\n"
            "(Make sure the bot is already an admin in that channel.)"
        )
        return
    target = message.command[1]
    try:
        chat = await client.get_chat(target if target.startswith("@") else int(target))
    except Exception as e:
        await message.reply_text(f"⛔ Couldn't find that channel: {e}")
        return
    try:
        member = await client.get_chat_member(chat.id, "me")
        if member.status.value not in ("administrator", "creator"):
            await message.reply_text("⛔ I need to be an admin in that channel first.")
            return
    except Exception:
        await message.reply_text("⛔ I need to be an admin in that channel first.")
        return

    await set_log_channel(chat.id)
    await message.reply_text(f"✅ <b>{chat.title}</b> is now set as the card logs channel.")


# ---------------- Guided card creation ----------------

@app.on_message(filters.command("addcard") & filters.private)
async def addcard_cmd(client, message: Message):
    if not await is_admin(message.from_user.id):
        await message.reply_text("⛔ This command is for bot admins/owner only.")
        return

    log_channel = await get_log_channel()
    if not log_channel:
        await message.reply_text(
            "⛔ No logs channel is set yet. Post /addlog inside your logs channel first "
            "(the bot must already be admin there)."
        )
        return

    start_card_flow(message.from_user.id)
    await message.reply_text(
        "🎴 <b>New Card — Step 1/5</b>\n\n📸 Send the character image now."
    )


@app.on_message(filters.command("cancel") & filters.private)
async def cancel_cmd(client, message: Message):
    if get_card_flow(message.from_user.id):
        cancel_card_flow(message.from_user.id)
        await message.reply_text("❌ Card creation cancelled.")


@app.on_message(filters.private & (filters.photo | filters.text) & ~filters.command([
    "start", "help", "addcard", "addadmin", "removeadmin", "admins", "cancel",
    "startmedia", "imageleb", "top", "gtop", "startgame", "stopgame",
    "listcards", "removecard", "stats", "ban", "unban", "reset",
    "startweb", "stopweb",
]))
async def card_flow_router(client, message: Message):
    state = get_card_flow(message.from_user.id)
    if not state:
        return  # not in a card creation flow, ignore

    step = state["step"]

    if step == STEP_WAIT_PHOTO:
        if not message.photo:
            await message.reply_text("📸 Please send a photo of the character.")
            return
        update_card_flow(message.from_user.id, file_id=message.photo.file_id)
        set_card_step(message.from_user.id, STEP_WAIT_NAME)
        await message.reply_text("🎴 <b>Step 2/5</b>\n\n✏️ What is the character's name?")
        return

    if step == STEP_WAIT_NAME:
        if not message.text:
            await message.reply_text("✏️ Please send the character's name as text.")
            return
        update_card_flow(message.from_user.id, name=message.text.strip())
        set_card_step(message.from_user.id, STEP_WAIT_ANIME)
        await message.reply_text("🎴 <b>Step 3/5</b>\n\n🎭 Which anime/series is this character from?")
        return

    if step == STEP_WAIT_ANIME:
        if not message.text:
            await message.reply_text("🎭 Please send the anime/series name as text.")
            return
        update_card_flow(message.from_user.id, anime=message.text.strip())
        set_card_step(message.from_user.id, STEP_WAIT_RARITY)
        kb = InlineKeyboardMarkup([[
            inline_btn("🟤 Common", callback_data="rarity_Common"),
            inline_btn("🟢 Rare", callback_data="rarity_Rare"),
        ], [
            inline_btn("🔵 Epic", callback_data="rarity_Epic"),
            inline_btn("🟣 Legendary", callback_data="rarity_Legendary"),
        ]])
        await message.reply_text("🎴 <b>Step 4/5</b>\n\n💎 Choose a rarity:", reply_markup=kb)
        return

    if step == STEP_WAIT_POINTS:
        if not message.text or not message.text.strip().isdigit():
            await message.reply_text("⭐ Please send a whole number for points (e.g. 10).")
            return
        points = int(message.text.strip())
        update_card_flow(message.from_user.id, points=points)
        set_card_step(message.from_user.id, STEP_CONFIRM)
        await _show_preview(message.from_user.id, message)
        return


@app.on_callback_query(filters.regex("^rarity_"))
async def rarity_cb(client, callback_query: CallbackQuery):
    state = get_card_flow(callback_query.from_user.id)
    if not state or state["step"] != STEP_WAIT_RARITY:
        await callback_query.answer("This card session expired.", show_alert=True)
        return
    rarity = callback_query.data.split("_", 1)[1]
    update_card_flow(callback_query.from_user.id, rarity=rarity)
    set_card_step(callback_query.from_user.id, STEP_WAIT_POINTS)
    await callback_query.answer()
    await callback_query.message.edit_text(f"💎 Rarity set: <b>{rarity}</b>")
    await callback_query.message.reply_text("🎴 <b>Step 5/5</b>\n\n⭐ How many points for a correct guess on this card?")


async def _show_preview(user_id, message: Message):
    state = get_card_flow(user_id)
    data = state["data"]
    caption = card_preview_caption(data["name"], data["anime"], data["rarity"], data["points"])
    kb = InlineKeyboardMarkup([[
        inline_btn("✅ Confirm", callback_data="card_confirm", style="success"),
        inline_btn("🔙 Back", callback_data="card_back"),
    ], [
        inline_btn("❌ Cancel", callback_data="card_cancel", style="danger"),
    ]])
    await message.reply_photo(data["file_id"], caption=caption, reply_markup=kb)


@app.on_callback_query(filters.regex("^card_confirm$"))
async def card_confirm_cb(client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    state = get_card_flow(user_id)
    if not state or state["step"] != STEP_CONFIRM:
        await callback_query.answer("This card session expired.", show_alert=True)
        return

    await callback_query.answer("Saving...")
    data = state["data"]
    log_channel = await get_log_channel()

    card_id = await next_card_id()
    added_by_mention = f'<a href="tg://user?id={user_id}">{callback_query.from_user.first_name}</a>'
    log_caption = log_channel_caption(card_id, data["name"], data["anime"], data["rarity"], data["points"], added_by_mention)

    log_msg = await client.send_photo(log_channel, data["file_id"], caption=log_caption)

    await add_character(
        card_id=card_id,
        file_id=data["file_id"],
        name=data["name"],
        anime=data["anime"],
        rarity=data["rarity"],
        points=data["points"],
        added_by=user_id,
        log_message_id=log_msg.id,
    )
    cancel_card_flow(user_id)
    await callback_query.message.edit_caption(
        callback_query.message.caption + f"\n\n✅ Saved to logs channel — Card ID #{card_id}"
    )


@app.on_callback_query(filters.regex("^card_back$"))
async def card_back_cb(client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    set_card_step(user_id, STEP_WAIT_RARITY)
    await callback_query.answer()
    kb = InlineKeyboardMarkup([[
        inline_btn("🟤 Common", callback_data="rarity_Common"),
        inline_btn("🟢 Rare", callback_data="rarity_Rare"),
    ], [
        inline_btn("🔵 Epic", callback_data="rarity_Epic"),
        inline_btn("🟣 Legendary", callback_data="rarity_Legendary"),
    ]])
    await callback_query.message.reply_text("💎 Choose a rarity again:", reply_markup=kb)


@app.on_callback_query(filters.regex("^card_cancel$"))
async def card_cancel_cb(client, callback_query: CallbackQuery):
    cancel_card_flow(callback_query.from_user.id)
    await callback_query.answer("Cancelled")
    await callback_query.message.edit_caption(callback_query.message.caption + "\n\n❌ Cancelled.")


# ---------------- Leaderboard image ----------------

@app.on_message(filters.command("imageleb") & filters.private)
async def imageleb_cmd(client, message: Message):
    if not await is_admin(message.from_user.id):
        await message.reply_text("⛔ This command is for bot admins/owner only.")
        return
    replied = message.reply_to_message
    if not replied or not replied.photo:
        await message.reply_text("↩️ Reply to a photo with /imageleb to set it as the leaderboard banner.")
        return
    await set_leaderboard_image(replied.photo.file_id)
    await message.reply_text("✅ Leaderboard banner image updated.")


# ---------------- Card management: list & remove ----------------

RARITY_EMOJI = {"Common": "🟤", "Rare": "🟢", "Epic": "🔵", "Legendary": "🟣"}
CARDS_PAGE_SIZE = 10


def _format_cards_page(items, page, total):
    total_pages = max((total + CARDS_PAGE_SIZE - 1) // CARDS_PAGE_SIZE, 1)
    lines = [f"🎴 <b>All Cards</b> ({total} total) — page {page}/{total_pages}", ""]
    if not items:
        lines.append("No cards found.")
    for c in items:
        emoji = RARITY_EMOJI.get(c["rarity"], "⚪")
        lines.append(f"#{c['card_id']} — <b>{c['name']}</b> ({c['anime']}) {emoji} {c['rarity']} • {c['points']} pts")
    return "\n".join(lines), total_pages


def _cards_page_keyboard(page, total_pages):
    buttons = []
    if page > 1:
        buttons.append(inline_btn("⬅️ Prev", callback_data=f"cards_page_{page - 1}"))
    if page < total_pages:
        buttons.append(inline_btn("Next ➡️", callback_data=f"cards_page_{page + 1}"))
    return InlineKeyboardMarkup([buttons]) if buttons else None


@app.on_message(filters.command("listcards") & filters.private)
async def listcards_cmd(client, message: Message):
    if not await is_admin(message.from_user.id):
        await message.reply_text("⛔ This command is for bot admins/owner only.")
        return
    items, total = await list_characters_page(1, CARDS_PAGE_SIZE)
    text, total_pages = _format_cards_page(items, 1, total)
    kb = _cards_page_keyboard(1, total_pages)
    await message.reply_text(text, reply_markup=kb)


@app.on_callback_query(filters.regex(r"^cards_page_(\d+)$"))
async def cards_page_cb(client, callback_query: CallbackQuery):
    if not await is_admin(callback_query.from_user.id):
        await callback_query.answer("⛔ Admins only.", show_alert=True)
        return
    page = int(callback_query.data.split("_")[-1])
    items, total = await list_characters_page(page, CARDS_PAGE_SIZE)
    text, total_pages = _format_cards_page(items, page, total)
    kb = _cards_page_keyboard(page, total_pages)
    await callback_query.answer()
    try:
        await callback_query.message.edit_text(text, reply_markup=kb)
    except Exception:
        pass


@app.on_message(filters.command("removecard") & filters.private)
async def removecard_cmd(client, message: Message):
    if not await is_admin(message.from_user.id):
        await message.reply_text("⛔ This command is for bot admins/owner only.")
        return
    if len(message.command) < 2 or not message.command[1].lstrip("#").isdigit():
        await message.reply_text("Usage: /removecard <card_id>\nExample: /removecard 12")
        return
    card_id = int(message.command[1].lstrip("#"))
    deleted = await delete_character(card_id)
    if deleted:
        await message.reply_text(f"✅ Card #{card_id} removed. It won't drop again.")
    else:
        await message.reply_text(f"⛔ No card found with ID #{card_id}.")


# ---------------- Owner stats ----------------

@app.on_message(filters.command("stats") & filters.private)
async def stats_cmd(client, message: Message):
    if not await is_owner(message.from_user.id):
        await message.reply_text("⛔ Owner only command.")
        return

    active_groups = await count_active_groups()
    all_groups = await count_all_groups()
    total_cards = await total_characters()
    total_users = await count_total_users()
    guesses_today = await count_guesses_since(ist_today_start_epoch())
    guesses_all_time = await count_guesses_total()

    text = (
        "📊 <b>Bot Stats</b>\n"
        "⊹═══════════════⊹\n\n"
        f"👥 Total Users: {total_users}\n"
        f"💬 Active Groups: {active_groups} (of {all_groups} ever added)\n"
        f"🎴 Total Cards: {total_cards}\n"
        f"🎯 Guesses Today: {guesses_today}\n"
        f"🏆 Guesses All-Time: {guesses_all_time}"
    )
    await message.reply_text(text)


# ---------------- Ban / Unban (bot admin/owner only, works in groups or DM) ----------------

async def _resolve_target(client, message: Message):
    if message.reply_to_message and message.reply_to_message.from_user:
        return message.reply_to_message.from_user
    if len(message.command) > 1:
        arg = message.command[1].lstrip("@")
        if arg.isdigit():
            uid = int(arg)
            try:
                return await client.get_users(uid)
            except Exception:
                return type("Obj", (), {"id": uid, "first_name": str(uid)})()
        try:
            return await client.get_users(message.command[1])
        except Exception:
            return None
    return None


@app.on_message(filters.command("ban"))
async def ban_cmd(client, message: Message):
    if not await is_admin(message.from_user.id):
        await message.reply_text("⛔ This command is for bot admins/owner only.")
        return
    target = await _resolve_target(client, message)
    if not target:
        await message.reply_text(
            "Usage: reply to a user's message with /ban, or /ban <user_id / @username>"
        )
        return
    await ban_user(target.id, message.from_user.id)
    name = getattr(target, "first_name", None) or str(target.id)
    await message.reply_text(f"🚫 <b>{name}</b> has been banned from playing. They won't appear on any leaderboard.")


@app.on_message(filters.command("unban"))
async def unban_cmd(client, message: Message):
    if not await is_admin(message.from_user.id):
        await message.reply_text("⛔ This command is for bot admins/owner only.")
        return
    target = await _resolve_target(client, message)
    if not target:
        await message.reply_text(
            "Usage: reply to a user's message with /unban, or /unban <user_id / @username>"
        )
        return
    removed = await unban_user(target.id)
    name = getattr(target, "first_name", None) or str(target.id)
    if removed:
        await message.reply_text(f"✅ <b>{name}</b> has been unbanned and can play again.")
    else:
        await message.reply_text(f"{name} wasn't banned.")


# ---------------- Auto card fetching from the web (/startweb, /stopweb) ----------------

async def web_fetch_loop(client, log_channel):
    while True:
        try:
            char = await fetch_random_character()
            if char:
                card_id = await next_card_id()
                added_by_mention = f'<a href="{char["source_url"]}">{char["source_name"]}</a>'
                caption = log_channel_caption(
                    card_id, char["name"], char["anime"], "Rare", AUTO_CARD_POINTS, added_by_mention
                )
                msg = await client.send_photo(log_channel, char["image_url"], caption=caption)
                await add_character(
                    card_id=card_id,
                    file_id=msg.photo.file_id,
                    name=char["name"],
                    anime=char["anime"],
                    rarity="Rare",
                    points=AUTO_CARD_POINTS,
                    added_by=f'web:{char["source_name"]}',
                    log_message_id=msg.id,
                )
            else:
                log.warning("web_fetch_loop: all sources failed this cycle, skipping.")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.exception(f"web_fetch_loop error: {e}")

        await asyncio.sleep(AUTO_CARD_INTERVAL_SECONDS)


@app.on_message(filters.command("startweb") & filters.private)
async def startweb_cmd(client, message: Message):
    if not await is_admin(message.from_user.id):
        await message.reply_text("⛔ This command is for bot admins/owner only.")
        return

    log_channel = await get_log_channel()
    if not log_channel:
        await message.reply_text("⛔ No logs channel set yet. Set one with /addlog first.")
        return

    if is_web_fetch_running():
        await message.reply_text("🌐 Auto card fetching is already running.")
        return

    task = asyncio.create_task(web_fetch_loop(client, log_channel))
    start_web_fetch_task(task)
    await message.reply_text(
        f"🌐 Auto card fetching started!\n"
        f"A new <b>Rare</b> card ({AUTO_CARD_POINTS} pts) will drop into the logs channel every "
        f"{AUTO_CARD_INTERVAL_SECONDS} seconds, pulled from AniList/MyAnimeList.\n\n"
        f"Use /stopweb to stop."
    )


@app.on_message(filters.command("stopweb") & filters.private)
async def stopweb_cmd(client, message: Message):
    if not await is_admin(message.from_user.id):
        await message.reply_text("⛔ This command is for bot admins/owner only.")
        return
    stop_web_fetch_task()
    await message.reply_text("🛑 Auto card fetching stopped.")
