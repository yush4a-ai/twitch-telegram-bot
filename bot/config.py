import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _require(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Не задана переменная окружения {name} (проверь .env)")
    return value


@dataclass(frozen=True)
class Config:
    telegram_bot_token: str
    twitch_client_id: str
    twitch_client_secret: str
    poll_interval_seconds: int
    db_path: str
    owner_chat_id: int | None
    oauth_host: str
    oauth_port: int
    oauth_public_base_url: str


def load_config() -> Config:
    owner_chat_id_raw = os.getenv("OWNER_CHAT_ID")
    oauth_port = int(os.getenv("PORT", "8765"))
    # PUBLIC_URL — публичный адрес, на который Twitch должен слать редирект после
    # авторизации (например, https://<project>.up.railway.app). Без него (локальная
    # разработка) используем localhost — тогда работает только на этом же компьютере.
    public_url = os.getenv("PUBLIC_URL", f"http://localhost:{oauth_port}").rstrip("/")
    return Config(
        telegram_bot_token=_require("TELEGRAM_BOT_TOKEN"),
        twitch_client_id=_require("TWITCH_CLIENT_ID"),
        twitch_client_secret=_require("TWITCH_CLIENT_SECRET"),
        poll_interval_seconds=int(os.getenv("POLL_INTERVAL_SECONDS", "60")),
        db_path=os.getenv("DB_PATH", "bot.db"),
        owner_chat_id=int(owner_chat_id_raw) if owner_chat_id_raw else None,
        oauth_host="0.0.0.0",
        oauth_port=oauth_port,
        oauth_public_base_url=public_url,
    )

