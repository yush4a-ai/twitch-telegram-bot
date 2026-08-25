import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _require(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Не задана переменная окружения {name} (проверь .env)")
    return value


def _positive_int(name: str, default: str) -> int:
    raw = os.getenv(name, default)
    try:
        value = int(raw)
    except ValueError as e:
        raise RuntimeError(f"Переменная {name} должна быть целым числом") from e
    if value <= 0:
        raise RuntimeError(f"Переменная {name} должна быть больше нуля")
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
    # подписки, которые нужно завести при старте, — запасной путь на случай, когда
    # до меню бота не добраться (нет Telegram под рукой). Список (chat_id, логин)
    auto_track: tuple[tuple[int, str], ...]


def _parse_auto_track(raw: str | None) -> tuple[tuple[int, str], ...]:
    """Разбирает AUTO_TRACK вида '-1001234567890:login, -1009876543210:other'.

    Одна кривая пара не должна мешать остальным и уж тем более ронять бота на старте,
    поэтому непонятные куски просто пропускаем — о них скажет лог при запуске."""
    if not raw:
        return ()
    result: list[tuple[int, str]] = []
    for chunk in raw.replace(";", ",").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        chat_part, sep, login_part = chunk.rpartition(":")
        if not sep:
            continue
        try:
            chat_id = int(chat_part.strip())
        except ValueError:
            continue
        login = login_part.strip().lower()
        if login:
            result.append((chat_id, login))
    return tuple(result)


def load_config() -> Config:
    owner_chat_id_raw = os.getenv("OWNER_CHAT_ID")
    oauth_port = _positive_int("PORT", "8765")
    # PUBLIC_URL — публичный адрес, на который Twitch должен слать редирект после
    # авторизации (например, https://<project>.up.railway.app). Без него (локальная
    # разработка) используем localhost — тогда работает только на этом же компьютере.
    public_url = os.getenv("PUBLIC_URL", f"http://localhost:{oauth_port}").rstrip("/")
    return Config(
        telegram_bot_token=_require("TELEGRAM_BOT_TOKEN"),
        twitch_client_id=_require("TWITCH_CLIENT_ID"),
        twitch_client_secret=_require("TWITCH_CLIENT_SECRET"),
        poll_interval_seconds=_positive_int("POLL_INTERVAL_SECONDS", "60"),
        db_path=os.getenv("DB_PATH", "bot.db"),
        owner_chat_id=int(owner_chat_id_raw) if owner_chat_id_raw else None,
        oauth_host="0.0.0.0",
        oauth_port=oauth_port,
        oauth_public_base_url=public_url,
        auto_track=_parse_auto_track(os.getenv("AUTO_TRACK")),
    )
