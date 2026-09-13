import os
import posixpath
import logging
from dataclasses import dataclass
from urllib.parse import urlsplit

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

PREVIEW_INITIAL_DELAY_SECONDS = 75
PREVIEW_INTERVAL_SECONDS = 300
PREVIEW_MAX_CONCURRENT_JOBS = 1
PREVIEW_JOB_TIMEOUT_SECONDS = 120
PREVIEW_BUFFER_SECONDS = 120
PREVIEW_BUFFER_MAX_BYTES = 150 * 1024 * 1024


class ConfigError(RuntimeError):
    """Постоянная ошибка конфигурации: повтор запуска без изменения env не поможет."""


def _require(name: str) -> str:
    value = os.getenv(name)
    if not value or not value.strip():
        raise ConfigError(f"Не задана переменная окружения {name} (проверь .env)")
    return value.strip()


def _positive_int(name: str, default: str) -> int:
    raw = os.getenv(name, default)
    try:
        value = int(raw)
    except ValueError as e:
        raise ConfigError(f"Переменная {name} должна быть целым числом") from e
    if value <= 0:
        raise ConfigError(f"Переменная {name} должна быть больше нуля")
    return value


def _optional_int(name: str) -> int | None:
    raw = os.getenv(name)
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError as e:
        raise ConfigError(f"Переменная {name} должна быть целым числом") from e


def is_railway_environment() -> bool:
    """True только по служебным runtime-переменным Railway."""
    return any(
        os.getenv(name)
        for name in (
            "RAILWAY_PROJECT_ID",
            "RAILWAY_ENVIRONMENT_ID",
            "RAILWAY_SERVICE_ID",
            "RAILWAY_PUBLIC_DOMAIN",
            "RAILWAY_VOLUME_MOUNT_PATH",
        )
    )


def _public_base_url(oauth_port: int, *, railway: bool) -> str:
    configured = os.getenv("PUBLIC_URL")
    public_domain = os.getenv("RAILWAY_PUBLIC_DOMAIN")
    if configured is not None and configured.strip():
        raw_url = configured.strip()
    elif public_domain and public_domain.strip():
        raw_url = f"https://{public_domain.strip().strip('/')}"
    elif railway:
        raise ConfigError(
            "На Railway нужен PUBLIC_URL или сгенерированный "
            "RAILWAY_PUBLIC_DOMAIN для Twitch OAuth callback"
        )
    elif configured is not None:
        raise ConfigError("Переменная PUBLIC_URL не может быть пустой")
    else:
        raw_url = f"http://localhost:{oauth_port}"

    try:
        parsed = urlsplit(raw_url)
        # Доступ к port отдельно валидирует malformed netloc (например :abc).
        _ = parsed.port
    except ValueError as e:
        raise ConfigError("Переменная PUBLIC_URL содержит некорректный URL") from e
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ConfigError("PUBLIC_URL должен быть полным http(s)-URL")
    if parsed.username is not None or parsed.password is not None:
        raise ConfigError("PUBLIC_URL не должен содержать credentials")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ConfigError("PUBLIC_URL должен содержать только scheme и host")
    if railway and parsed.scheme != "https":
        raise ConfigError("PUBLIC_URL на Railway должен использовать https")
    return f"{parsed.scheme}://{parsed.netloc}"


def _database_path(*, railway: bool) -> str:
    configured = os.getenv("DB_PATH")
    if configured is not None and not configured.strip():
        raise ConfigError("Переменная DB_PATH не может быть пустой")
    if not railway:
        return configured.strip() if configured is not None else "bot.db"

    if configured is None:
        raise ConfigError("На Railway нужен DB_PATH внутри persistent Volume")
    mount = os.getenv("RAILWAY_VOLUME_MOUNT_PATH")
    if not mount or not mount.strip():
        raise ConfigError("На Railway нужен persistent Volume и RAILWAY_VOLUME_MOUNT_PATH")

    # Railway runtime — Linux, поэтому валидируем его POSIX-пути даже
    # в local-only Windows-тестах.
    db_path = posixpath.normpath(configured.strip())
    mount_path = posixpath.normpath(mount.strip())
    if not db_path.startswith("/") or not mount_path.startswith("/"):
        raise ConfigError("DB_PATH и Railway Volume mount должны быть абсолютными POSIX-путями")
    try:
        inside_mount = posixpath.commonpath((db_path, mount_path)) == mount_path
    except ValueError:
        inside_mount = False
    if not inside_mount or db_path == mount_path:
        raise ConfigError("DB_PATH на Railway должен указывать файл внутри Volume")
    return db_path


@dataclass(frozen=True)
class PreviewRuntimeConfig:
    enabled: bool = False
    initial_delay_seconds: int = PREVIEW_INITIAL_DELAY_SECONDS
    interval_seconds: int = PREVIEW_INTERVAL_SECONDS
    max_concurrent_jobs: int = PREVIEW_MAX_CONCURRENT_JOBS
    job_timeout_seconds: int = PREVIEW_JOB_TIMEOUT_SECONDS
    disabled_reason: str | None = None


@dataclass(frozen=True)
class PreviewCaptureConfig:
    enabled: bool = True
    buffer_seconds: int = PREVIEW_BUFFER_SECONDS
    buffer_max_bytes: int = PREVIEW_BUFFER_MAX_BYTES
    disabled_reason: str | None = None


def _preview_capture_config() -> PreviewCaptureConfig:
    invalid_names: list[str] = []

    def bounded(name: str, default: int, minimum: int, maximum: int) -> int:
        raw = os.getenv(name)
        if raw is None:
            return default
        try:
            value = int(raw)
        except ValueError:
            invalid_names.append(name)
            return default
        if not minimum <= value <= maximum:
            invalid_names.append(name)
            return default
        return value

    config = PreviewCaptureConfig(
        buffer_seconds=bounded(
            "PREVIEW_BUFFER_SECONDS", PREVIEW_BUFFER_SECONDS, 30, 240
        ),
        buffer_max_bytes=bounded(
            "PREVIEW_BUFFER_MAX_BYTES",
            PREVIEW_BUFFER_MAX_BYTES,
            32 * 1024 * 1024,
            150 * 1024 * 1024,
        ),
        disabled_reason="config_error" if invalid_names else None,
    )
    if not invalid_names:
        return config
    logger.warning(
        "Preview capture отключён: некорректные переменные %s",
        ", ".join(sorted(set(invalid_names))),
    )
    return PreviewCaptureConfig(
        enabled=False,
        buffer_seconds=config.buffer_seconds,
        buffer_max_bytes=config.buffer_max_bytes,
        disabled_reason="config_error",
    )


def _preview_config() -> PreviewRuntimeConfig:
    invalid_names: list[str] = []
    raw_enabled = os.getenv("PREVIEW_RUNTIME_ENABLED", "false").strip().lower()
    if raw_enabled in {"1", "true", "yes", "on"}:
        enabled = True
    elif raw_enabled in {"0", "false", "no", "off"}:
        enabled = False
    else:
        enabled = False
        invalid_names.append("PREVIEW_RUNTIME_ENABLED")

    def positive(name: str, default: int) -> int:
        raw = os.getenv(name)
        if raw is None:
            return default
        try:
            value = int(raw)
        except ValueError:
            invalid_names.append(name)
            return default
        if value <= 0:
            invalid_names.append(name)
            return default
        return value

    config = PreviewRuntimeConfig(
        enabled=enabled,
        initial_delay_seconds=positive(
            "PREVIEW_INITIAL_DELAY_SECONDS", PREVIEW_INITIAL_DELAY_SECONDS
        ),
        interval_seconds=positive("PREVIEW_INTERVAL_SECONDS", PREVIEW_INTERVAL_SECONDS),
        max_concurrent_jobs=positive(
            "PREVIEW_MAX_CONCURRENT_JOBS", PREVIEW_MAX_CONCURRENT_JOBS
        ),
        job_timeout_seconds=positive(
            "PREVIEW_JOB_TIMEOUT_SECONDS", PREVIEW_JOB_TIMEOUT_SECONDS
        ),
        disabled_reason="config_error" if invalid_names else None,
    )
    if not invalid_names:
        return config
    logger.warning(
        "Preview runtime отключён: некорректные переменные %s",
        ", ".join(sorted(set(invalid_names))),
    )
    return PreviewRuntimeConfig(
        enabled=False,
        initial_delay_seconds=config.initial_delay_seconds,
        interval_seconds=config.interval_seconds,
        max_concurrent_jobs=config.max_concurrent_jobs,
        job_timeout_seconds=config.job_timeout_seconds,
        disabled_reason="config_error",
    )


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
    token_encryption_key: str | None
    # подписки, которые нужно завести при старте, — запасной путь на случай, когда
    # до меню бота не добраться (нет Telegram под рукой). Список (chat_id, логин)
    auto_track: tuple[tuple[int, str], ...]
    preview: PreviewRuntimeConfig
    preview_capture: PreviewCaptureConfig


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
    railway = is_railway_environment()
    oauth_port = _positive_int("PORT", "8765")
    # PUBLIC_URL — публичный адрес, на который Twitch должен слать редирект после
    # авторизации (например, https://<project>.up.railway.app). Без него (локальная
    # разработка) используем localhost — тогда работает только на этом же компьютере.
    public_url = _public_base_url(oauth_port, railway=railway)
    token_encryption_key = os.getenv("TOKEN_ENCRYPTION_KEY")
    if token_encryption_key is not None:
        token_encryption_key = token_encryption_key.strip() or None
    if railway and token_encryption_key is None:
        raise ConfigError("На Railway обязателен TOKEN_ENCRYPTION_KEY для Twitch-токенов")
    return Config(
        telegram_bot_token=_require("TELEGRAM_BOT_TOKEN"),
        twitch_client_id=_require("TWITCH_CLIENT_ID"),
        twitch_client_secret=_require("TWITCH_CLIENT_SECRET"),
        poll_interval_seconds=_positive_int("POLL_INTERVAL_SECONDS", "60"),
        db_path=_database_path(railway=railway),
        owner_chat_id=_optional_int("OWNER_CHAT_ID"),
        oauth_host="0.0.0.0",
        oauth_port=oauth_port,
        oauth_public_base_url=public_url,
        token_encryption_key=token_encryption_key,
        auto_track=_parse_auto_track(os.getenv("AUTO_TRACK")),
        preview=_preview_config(),
        preview_capture=_preview_capture_config(),
    )
