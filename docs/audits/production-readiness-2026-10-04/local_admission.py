"""Reproduce production admission with fake env; no network or database."""
import dataclasses
import json
import os
import pathlib
import sys
from unittest.mock import patch

os.environ['PYTHON_DOTENV_DISABLED'] = '1'
ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from bot.config import ConfigError, first_release_payment_policy, load_config
from main import _menu_button_for_config

fake = {
    'TELEGRAM_BOT_TOKEN': '123456:offline-not-a-real-token',
    'TWITCH_CLIENT_ID': 'offline-client', 'TWITCH_CLIENT_SECRET': 'offline-secret',
    'TOKEN_ENCRYPTION_KEY': 'offline-reference-never-used',
    'RAILWAY_PROJECT_ID': 'offline-project', 'RAILWAY_ENVIRONMENT_ID': 'offline-env',
    'RAILWAY_SERVICE_ID': 'offline-service', 'RAILWAY_ENVIRONMENT_NAME': 'production',
    'RAILWAY_VOLUME_MOUNT_PATH': '/data', 'DB_PATH': '/data/bot.db',
    'PUBLIC_URL': 'https://offline.example',
    'ADMIN_PANEL_ACCESS_KEY': 'x' * 32, 'ADMIN_TELEGRAM_BOT_USERNAME': 'MainBot',
}
with patch.dict(os.environ, fake, clear=True):
    config = load_config()
    result = {name: getattr(config, name) for name in [
        'pinned_staging', 'mini_app_enabled', 'streamer_plus_enabled',
        'viewer_plus_enabled', 'growth_enabled', 'notification_queue_enabled']}
    result['admin_panel_enabled'] = config.admin_panel_access_key is not None
    result['menu_button_type'] = _menu_button_for_config(config).type
    result['money_policy'] = dataclasses.asdict(first_release_payment_policy())
    assert all(result[name] is False for name in [
        'pinned_staging', 'mini_app_enabled', 'streamer_plus_enabled',
        'viewer_plus_enabled', 'growth_enabled', 'admin_panel_enabled'])
    assert result['menu_button_type'] == 'commands'
    os.environ['NOTIFICATION_QUEUE_ENABLED'] = '1'
    try:
        load_config()
    except ConfigError as error:
        result['queue_on_startup_error'] = str(error)
    else:
        raise AssertionError('Expected production queue admission rejection')
result['status'] = 'PASS'
result['scope'] = 'isolated fake environment; no real credentials, no network, no DB'
pathlib.Path(__file__).with_name('LOCAL-ADMISSION.json').write_text(
    json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps(result, ensure_ascii=False))
