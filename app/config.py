import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    bot_token: str
    api_id: int
    api_hash: str
    database_url: str
    download_base_url: str
    telegram_storage_chat_id: int
    chunk_size: int


settings = Settings(
    bot_token=os.environ["BOT_TOKEN"],
    api_id=int(os.environ["API_ID"]),
    api_hash=os.environ["API_HASH"],
    database_url=os.environ["DATABASE_URL"],
    download_base_url=os.environ.get("DOWNLOAD_BASE_URL", "http://localhost:8000").rstrip("/"),
    telegram_storage_chat_id=int(os.environ.get("TELEGRAM_STORAGE_CHAT_ID", "0")),
    chunk_size=int(os.getenv("CHUNK_SIZE", str(512 * 1024))),
)
