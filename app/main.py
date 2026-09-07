from contextlib import asynccontextmanager
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Header, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .bot import bot, start_bot
from .database import get_db, init_db
from .downloader import build_streaming_response
from .models import File


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Initialize database schema and Telethon MTProto connection
    await init_db()
    await start_bot()
    yield
    # Shutdown: Gracefully disconnect MTProto client
    await bot.disconnect()


app = FastAPI(
    title="Telegram Virtual File Server",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/health", status_code=status.HTTP_200_OK)
async def health_check():
    return {
        "status": "healthy",
        "bot_connected": bot.is_connected(),
    }


@app.get("/d/{token}")
async def download_file_stream(
    token: str,
    range: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_db),
):
    stmt = (
        select(File)
        .where(File.download_token == token)
        .options(selectinload(File.parts))
    )
    result = await db.execute(stmt)
    file_record = result.scalar_one_or_none()

    if not file_record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The requested file was not found or the link has expired.",
        )

    if file_record.status != "ready":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="File upload is still in progress or incomplete.",
        )

    return build_streaming_response(
        client=bot,
        file_record=file_record,
        range_header=range,
    )
