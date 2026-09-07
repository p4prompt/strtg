import re
import secrets
from typing import Any, Dict
from telethon import Button, TelegramClient, events

from .config import settings
from .database import SessionLocal
from .models import File, FilePart

bot = TelegramClient(
    "bot_session",
    settings.api_id,
    settings.api_hash,
)

sessions: Dict[int, Dict[str, Any]] = {}


def format_size(size_bytes: int) -> str:
    if size_bytes == 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    size = float(size_bytes)
    while size >= 1024 and i < len(units) - 1:
        size /= 1024
        i += 1
    return f"{size:.2f} {units[i]}"


def sanitize_multipart_filename(raw_name: str) -> str:
    cleaned = re.sub(r"\.(part\d+|[0-9]{3}|[a-z]{2})$", "", raw_name, flags=re.IGNORECASE)
    return cleaned if cleaned else raw_name


async def start_bot() -> None:
    await bot.start(bot_token=settings.bot_token)


@bot.on(events.NewMessage(pattern=r"^/start$"))
async def start_command_handler(event: events.NewMessage.Event) -> None:
    user_id = event.sender_id
    sessions.pop(user_id, None)

    await event.respond(
        "👋 **Welcome to the Large File Storage Bot**\n\n"
        "Choose an upload method:",
        buttons=[
            [
                Button.inline("📄 Single File Mode", b"mode_single"),
                Button.inline("🧩 Multipart Archive Mode", b"mode_split"),
            ]
        ],
    )


@bot.on(events.NewMessage(pattern=r"^/cancel$"))
async def cancel_command_handler(event: events.NewMessage.Event) -> None:
    user_id = event.sender_id
    if user_id in sessions:
        sessions.pop(user_id, None)
        await event.respond("❌ Upload session cancelled.")
    else:
        await event.respond("ℹ️ No active upload session.")


@bot.on(events.CallbackQuery)
async def callback_query_handler(event: events.CallbackQuery.Event) -> None:
    user_id = event.sender_id

    if event.data == b"mode_single":
        sessions[user_id] = {"mode": "single"}
        await event.edit(
            "📄 **Single File Mode**\n\n"
            "Send any file up to your Telegram account's upload limit.\n"
            "Send `/cancel` to abort."
        )

    elif event.data == b"mode_split":
        sessions[user_id] = {
            "mode": "split",
            "next_part": 1,
            "parts": [],
            "custom_filename": None,
        }
        await event.edit(
            "🧩 **Multipart Archive Mode**\n\n"
            "1. Upload **Part 1** (`.part01.rar`, `.001`, etc.)\n"
            "2. Upload all subsequent parts in strict sequential order.\n"
            "3. Send `/done` when all parts have been received.\n"
            "Send `/cancel` to abort at any time."
        )


@bot.on(events.NewMessage(pattern=r"^/done$"))
async def done_command_handler(event: events.NewMessage.Event) -> None:
    user_id = event.sender_id
    session = sessions.get(user_id)

    if not session or session.get("mode") != "split":
        await event.respond("❌ No active multipart upload. Send `/start` to begin a session.")
        return

    parts_data = session.get("parts", [])
    if not parts_data:
        await event.respond("❌ No parts were received. Please send part files before using `/done`.")
        return

    token = secrets.token_urlsafe(32)
    total_size = sum(p["size"] for p in parts_data)
    unified_filename = session.get("custom_filename") or "archive.bin"

    async with SessionLocal() as db:
        new_file = File(
            user_id=user_id,
            filename=unified_filename,
            total_size=total_size,
            total_parts=len(parts_data),
            mode="split",
            status="ready",
            download_token=token,
        )
        db.add(new_file)
        await db.flush()

        for p in parts_data:
            part_record = FilePart(
                file_id=new_file.id,
                part_number=p["part_number"],
                telegram_chat_id=p["chat_id"],
                telegram_message_id=p["message_id"],
                filename=p["filename"],
                size=p["size"],
            )
            db.add(part_record)

        await db.commit()

    sessions.pop(user_id, None)
    download_url = f"{settings.download_base_url}/d/{token}"

    await event.respond(
        f"✅ **Virtual Multipart File Assembled**\n\n"
        f"📁 **Name:** `{unified_filename}`\n"
        f"📦 **Total Parts:** `{len(parts_data)}`\n"
        f"💾 **Virtual Size:** `{format_size(total_size)}`\n\n"
        f"🔗 **Resumable HTTP Stream Link:**\n`{download_url}`"
    )


@bot.on(events.NewMessage)
async def incoming_file_handler(event: events.NewMessage.Event) -> None:
    if event.raw_text and event.raw_text.startswith("/"):
        return

    if not event.file:
        return

    user_id = event.sender_id
    session = sessions.get(user_id)

    if not session:
        await event.respond("ℹ️ Send `/start` to initiate an upload session.")
        return

    filename = event.file.name or f"file_{event.id}.bin"
    file_size = event.file.size or 0
    chat_id = event.chat_id
    message_id = event.id

    if session["mode"] == "single":
        token = secrets.token_urlsafe(32)

        async with SessionLocal() as db:
            new_file = File(
                user_id=user_id,
                filename=filename,
                total_size=file_size,
                total_parts=1,
                mode="single",
                status="ready",
                download_token=token,
            )
            db.add(new_file)
            await db.flush()

            part_record = FilePart(
                file_id=new_file.id,
                part_number=1,
                telegram_chat_id=chat_id,
                telegram_message_id=message_id,
                filename=filename,
                size=file_size,
            )
            db.add(part_record)
            await db.commit()

        sessions.pop(user_id, None)
        download_url = f"{settings.download_base_url}/d/{token}"

        await event.respond(
            f"✅ **File Upload Complete**\n\n"
            f"📁 **Name:** `{filename}`\n"
            f"💾 **Size:** `{format_size(file_size)}`\n\n"
            f"🔗 **Resumable HTTP Stream Link:**\n`{download_url}`"
        )

    elif session["mode"] == "split":
        part_num = session["next_part"]

        if part_num == 1:
            session["custom_filename"] = sanitize_multipart_filename(filename)

        session["parts"].append(
            {
                "part_number": part_num,
                "chat_id": chat_id,
                "message_id": message_id,
                "filename": filename,
                "size": file_size,
            }
        )
        session["next_part"] += 1

        await event.respond(
            f"📥 **Part {part_num} Saved**\n"
            f"• Original Name: `{filename}`\n"
            f"• Part Size: `{format_size(file_size)}`\n\n"
            f"Send **Part {part_num + 1}** or send `/done` if all parts have been uploaded."
        )
