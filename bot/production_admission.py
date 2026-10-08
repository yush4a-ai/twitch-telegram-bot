"""First-release opt-in. Reads a pinned operator contract, never guesses targets."""
import hashlib
import json
import os
import posixpath
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit
from cryptography.fernet import Fernet
from .config import ConfigError, PAYMENT_POLICIES


@dataclass(frozen=True)
class ProductionAdmission:
    project_id: str
    environment_id: str
    environment_name: str
    service_id: str
    volume_id: str
    volume_instance_id: str
    mount_path: str
    db_path: str
    public_url: str
    bot_id: int
    bot_username: str
    owner_chat_id: int
    replica_count: int
    writer_policy: str
    queue_policy: str
    queue_enabled: bool
    payment_policy: str
    login_client_policy: str


def _value(name):
    raw = os.environ.get(name, '').strip()
    if (not raw or raw.casefold() in {'secret', 'test', 'fixture', 'changeme', 'staging'}
            or re.search(r'owner[ _-]*input|placeholder|<|>', raw, re.I)):
        raise ConfigError(f'Production admission: отсутствует подтверждённый {name}')
    return raw


def load_production_admission(*, railway, db_path, public_url):
    flag = os.environ.get('PRODUCTION_PRODUCT_ENABLED', '0').strip()
    if flag not in {'0', '1'}:
        raise ConfigError('PRODUCTION_PRODUCT_ENABLED должен быть 0 или 1')
    if flag == '0':
        return None
    if not railway:
        raise ConfigError('Production opt-in требует Railway target')
    path = Path(_value('PRODUCTION_ADMISSION_FILE')).resolve()
    if path.is_relative_to(Path(__file__).resolve().parents[1]):
        raise ConfigError('Operator contract должен храниться вне repository')
    try:
        raw = path.read_bytes()
        if len(raw) > 16384 or hashlib.sha256(raw).hexdigest() != _value('PRODUCTION_ADMISSION_SHA256'):
            raise ConfigError('Production operator contract hash mismatch')
        contract = ProductionAdmission(**json.loads(raw))
    except (OSError, ValueError, TypeError) as error:
        raise ConfigError('Некорректный production operator contract') from error
    for name, value in vars(contract).items():
        if name not in {'bot_id', 'owner_chat_id', 'replica_count', 'queue_enabled'}:
            if (not isinstance(value, str) or not value.strip()
                    or re.search(r'owner[ _-]*input|placeholder|<|>', value, re.I)):
                raise ConfigError(f'Production contract: invalid {name}')
    for field in ('project_id', 'environment_id', 'service_id', 'volume_id', 'volume_instance_id'):
        value = getattr(contract, field)
        try:
            if str(uuid.UUID(value)) != value or uuid.UUID(value).int == 0:
                raise ValueError()
        except (ValueError, TypeError, AttributeError) as error:
            raise ConfigError(f'Production contract: invalid {field}') from error
    target = json.loads((Path(__file__).resolve().parents[1] / 'scripts/staging_target.json').read_text())
    host = urlsplit(contract.public_url).hostname or ''
    if (host in {'localhost', '127.0.0.1', '::1', 'example.com', 'example.org', 'example.net'}
            or host.endswith(('.invalid', '.test', '.example', '.localhost'))):
        raise ConfigError('Production PUBLIC_URL содержит placeholder domain')
    if (contract.environment_id == target['staging_environment_id']
            or contract.volume_instance_id == target['staging_volume_instance_id']
            or contract.environment_name.casefold() in {'staging', 'test', 'development', ''}
            or contract.bot_id == 8859004067
            or contract.bot_username.casefold() in {'twitchsignaltestbot', 'signalstreamsbot'}
            or target['staging_domain'] in contract.public_url):
        raise ConfigError('Production contract содержит staging identity')
    if (type(contract.bot_id) is not int or contract.bot_id <= 0
            or type(contract.owner_chat_id) is not int or contract.owner_chat_id <= 0
            or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{4,31}', contract.bot_username)
            or not contract.bot_username.lower().endswith('bot')
            or type(contract.replica_count) is not int or contract.replica_count != 1
            or type(contract.queue_enabled) is not bool
            or contract.writer_policy != 'exclusive_lock'
            or contract.queue_policy != 'lease_fenced_v1'
            or contract.payment_policy not in PAYMENT_POLICIES
            or contract.login_client_policy != 'peer_shared_fail_safe'):
        raise ConfigError('Production contract: invalid identity/writer/queue/payment policy')
    for name in ('mount_path', 'db_path'):
        value = getattr(contract, name)
        if not isinstance(value, str) or not value.startswith('/') or posixpath.normpath(value) != value:
            raise ConfigError('Production paths должны быть normalized absolute POSIX')
    if (contract.mount_path == '/' or contract.db_path == contract.mount_path
            or posixpath.commonpath((contract.mount_path, contract.db_path)) != contract.mount_path
            or contract.db_path != db_path or contract.public_url != public_url
            or not public_url.startswith('https://')):
        raise ConfigError('Production DB/PUBLIC_URL mismatch')
    expected = {
        'RAILWAY_PROJECT_ID': contract.project_id, 'RAILWAY_ENVIRONMENT_ID': contract.environment_id,
        'RAILWAY_ENVIRONMENT_NAME': contract.environment_name, 'RAILWAY_SERVICE_ID': contract.service_id,
        'RAILWAY_VOLUME_ID': contract.volume_id, 'RAILWAY_VOLUME_INSTANCE_ID': contract.volume_instance_id,
        'RAILWAY_VOLUME_MOUNT_PATH': contract.mount_path, 'DB_PATH': contract.db_path,
        'PUBLIC_URL': contract.public_url, 'OWNER_CHAT_ID': str(contract.owner_chat_id),
        'ADMIN_TELEGRAM_BOT_USERNAME': contract.bot_username, 'PRODUCTION_REPLICA_COUNT': '1',
        'PRODUCTION_PAYMENT_POLICY': contract.payment_policy, 'PRODUCTION_WRITER_POLICY': 'exclusive_lock',
        'PRODUCTION_QUEUE_POLICY': 'lease_fenced_v1', 'PRODUCTION_LOGIN_CLIENT_POLICY': 'peer_shared_fail_safe',
        'PRODUCTION_QUEUE_ENABLED': '1' if contract.queue_enabled else '0',
        'NOTIFICATION_QUEUE_ENABLED': '1' if contract.queue_enabled else '0',
    }
    for name, value in expected.items():
        if _value(name) != value:
            raise ConfigError(f'Production admission mismatch: {name}')
    for name in ('TELEGRAM_BOT_TOKEN', 'TWITCH_CLIENT_ID', 'TWITCH_CLIENT_SECRET',
                 'TOKEN_ENCRYPTION_KEY', 'ADMIN_PANEL_ACCESS_KEY', 'PORT'):
        _value(name)
    if (len(_value('ADMIN_PANEL_ACCESS_KEY')) < 32
            or _value('TELEGRAM_BOT_TOKEN').partition(':')[0] != str(contract.bot_id)):
        raise ConfigError('Production token/admin key invalid')
    try:
        Fernet(_value('TOKEN_ENCRYPTION_KEY'))
    except (ValueError, TypeError) as error:
        raise ConfigError('Production encryption key invalid') from error
    return contract


def validate_storage(contract):
    mount = Path(contract.mount_path)
    db = Path(contract.db_path)
    if (not mount.is_dir() or not os.path.ismount(mount) or not db.is_file()
            or mount.resolve() != mount.absolute() or db.resolve() != db.absolute()
            or not os.access(db, os.R_OK | os.W_OK) or not os.access(db.parent, os.W_OK)):
        raise ConfigError('Production volume/source DB/permissions не подтверждены')


async def verify_identity(bot, contract):
    identity = await bot.get_me()
    if identity.id != contract.bot_id or identity.username != contract.bot_username:
        raise ConfigError('Production getMe identity mismatch')


class WriterLock:
    def __init__(self, contract):
        self.file = None
        path = Path(contract.mount_path) / '.twitchsignal-writer.lock'
        if path.is_symlink():
            raise ConfigError('Writer lock не должен быть symlink')
        try:
            self.file = path.open('a+b')
            if os.name == 'nt':
                import msvcrt
                self.file.seek(0)
                if not self.file.read(1):
                    self.file.write(b'0')
                    self.file.flush()
                self.file.seek(0)
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            self.close()
            raise ConfigError('Production writer ownership недоступен') from error

    def close(self):
        if self.file is not None:
            self.file.close()
            self.file = None
