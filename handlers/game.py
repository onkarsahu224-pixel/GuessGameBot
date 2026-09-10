import asyncio
import logging
import re
import unicodedata

from pyrogram import filters
from pyrogram.types import Message, ChatMemberUpdated
from pyrogram.enums import ChatMemberStatus, ChatType

from bot_instance import app
from config import GUESS_WINDOW_SECONDS, GAP_BETWEEN_DROPS_SECONDS
from database.models import (
    ensure_group, get_group, set_group_active, push_recent_card,
    random_character, total_characters, add_points, cache_user,
    is_admin,
)
from utils.formatting import drop_caption, correct_caption, timeout_caption
from utils.state import (
    is_game_running, register_game_task, stop_game_task,
    set_pending_drop, get_pending_drop, clear_pending_drop,
)

log = logging.getLogger("guessbot.game")


async def _is_group_admin_or_bot_admin(client, chat_id, user_id):
    if await is_admin(user_id):
        return True
    try:
        member = await client.get_chat_member(chat_id, user_id)
        return member.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER)
    except Exception:
        return False


async def game_loop(client, chat_id):
    try:
        while True:
            group = await get_group(chat_id)
            if not group or not group.get("active"):
                return

            count = await total_characters()
            if count == 0:
                await asyncio.sleep(10)
                continue

            recent = group.get("recent_card_ids", [])
            card = await random_character(recent)
            if not card:
                await asyncio.sleep(10)
                continue

            await push_recent_card(chat_id, card["card_id"])

            try:
                drop_msg = await client.send_photo(chat_id, card["file_id"], caption=drop_caption())
            except Exception as e:
                log.warning(f"Failed to send drop in {chat_id}: {e}")
                await asyncio.sleep(5)
                continue

            future = asyncio.get_running_loop().create_future()
            set_pending_drop(chat_id, future, card)

            winner = None
            try:
                winner = await asyncio.wait_for(future, timeout=GUESS_WINDOW_SECONDS)
            except asyncio.TimeoutError:
                winner = None
            finally:
                clear_pending_drop(chat_id)

            if winner:
                await add_points(chat_id, winner.id, card["points"])
                mention = f'<a href="tg://user?id={winner.id}">{winner.first_name}</a>'
                await client.send_message(
                    chat_id,
                    correct_caption(card["name"], card["anime"], card["rarity"], card["points"], mention),
                    reply_to_message_id=drop_msg.id,
                )
            else:
                await client.send_message(
                    chat_id,
                    timeout_caption(card["name"], card["anime"], card["rarity"]),
                    reply_to_message_id=drop_msg.id,
                )

            await asyncio.sleep(GAP_BETWEEN_DROPS_SECONDS)
    except asyncio.CancelledError:
        clear_pending_drop(chat_id)
        raise
    except Exception as e:
        log.exception(f"Game loop crashed for {chat_id}: {e}")


async def start_game_for_group(client, chat_id, title=None):
    await ensure_group(chat_id, title)
    await set_group_active(chat_id, True)
    if not is_game_running(chat_id):
        task = asyncio.create_task(game_loop(client, chat_id))
        register_game_task(chat_id, task)


@app.on_chat_member_updated()
async def on_bot_added(client, update: ChatMemberUpdated):
    # Only the game loop should run in actual groups/supergroups.
    # Channels (like the logs channel) must NEVER get the auto-drop game,
    # even though the bot becomes admin there too.
    if update.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return

    me_id = client.me.id if client.me else (await client.get_me()).id
    if update.new_chat_member and update.new_chat_member.user.id == me_id:
        status = update.new_chat_member.status
        if status in (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR):
            await start_game_for_group(client, update.chat.id, update.chat.title)
        elif status in (ChatMemberStatus.LEFT, ChatMemberStatus.BANNED):
            await set_group_active(update.chat.id, False)
            stop_game_task(update.chat.id)


@app.on_message(filters.command("startgame") & filters.group)
async def startgame_cmd(client, message: Message):
    if not await _is_group_admin_or_bot_admin(client, message.chat.id, message.from_user.id):
        await message.reply_text("⛔ Only group admins can control the game.")
        return
    await start_game_for_group(client, message.chat.id, message.chat.title)
    await message.reply_text("▶️ Game started! Get ready to guess.")


@app.on_message(filters.command("stopgame") & filters.group)
async def stopgame_cmd(client, message: Message):
    if not await _is_group_admin_or_bot_admin(client, message.chat.id, message.from_user.id):
        await message.reply_text("⛔ Only group admins can control the game.")
        return
    await set_group_active(message.chat.id, False)
    stop_game_task(message.chat.id)
    await message.reply_text("⏸ Game paused. Use /startgame to resume.")


import re
import unicodedata


def _normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "", text)
    return text


def _is_guess_match(guess: str, name: str) -> bool:
    guess = _normalize_text(guess)
    name = _normalize_text(name)

    if len(guess) < 2:
        return False

    return guess in name


@app.on_message(filters.text & filters.group & ~filters.via_bot & ~filters.regex(r"^/"))
async def guess_handler(client, message: Message):
    if not message.text or message.text.startswith("/"):
        return
    if not message.from_user:
        return
    if message.edit_date:
        return

    pending = get_pending_drop(message.chat.id)
    if not pending:
        return

    future = pending["future"]
    card = pending["card"]
    if future.done():
        return

    if _is_guess_match(message.text, card["name"]):
        future.set_result(message.from_user)
        await cache_user(message.from_user.id, message.from_user.first_name or str(message.from_user.id))
