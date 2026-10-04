import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from cryptography.fernet import Fernet
from bot.config import load_config, ConfigError


class AdmissionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.file = Path(self.tmp.name) / 'operator.json'
        self.contract = dict(project_id='11111111-1111-4111-8111-111111111111',
            environment_id='22222222-2222-4222-8222-222222222222', environment_name='production',
            service_id='33333333-3333-4333-8333-333333333333',
            volume_id='44444444-4444-4444-8444-444444444444',
            volume_instance_id='55555555-5555-4555-8555-555555555555',
            mount_path='/data', db_path='/data/bot.db', public_url='https://notifications.fixture-owned.ru',
            bot_id=123456789, bot_username='OwnerProductionBot', owner_chat_id=101,
            replica_count=1, writer_policy='exclusive_lock', queue_policy='lease_fenced_v1',
            queue_enabled=False, payment_policy='off', login_client_policy='peer_shared_fail_safe')
        self.env = dict(PRODUCTION_PRODUCT_ENABLED='1', PRODUCTION_ADMISSION_FILE=str(self.file),
            TELEGRAM_BOT_TOKEN='123456789:local-only-token-with-no-network',
            TWITCH_CLIENT_ID='client-id-local-only', TWITCH_CLIENT_SECRET='client-secret-local-only',
            TOKEN_ENCRYPTION_KEY=Fernet.generate_key().decode(), ADMIN_PANEL_ACCESS_KEY='k'*32,
            ADMIN_TELEGRAM_BOT_USERNAME='OwnerProductionBot', OWNER_CHAT_ID='101',
            RAILWAY_PROJECT_ID=self.contract['project_id'], RAILWAY_ENVIRONMENT_ID=self.contract['environment_id'],
            RAILWAY_ENVIRONMENT_NAME='production', RAILWAY_SERVICE_ID=self.contract['service_id'],
            RAILWAY_VOLUME_ID=self.contract['volume_id'], RAILWAY_VOLUME_INSTANCE_ID=self.contract['volume_instance_id'],
            RAILWAY_VOLUME_MOUNT_PATH='/data', DB_PATH='/data/bot.db', PUBLIC_URL=self.contract['public_url'],
            PORT='8765', PRODUCTION_REPLICA_COUNT='1', PRODUCTION_PAYMENT_POLICY='off',
            PRODUCTION_WRITER_POLICY='exclusive_lock', PRODUCTION_QUEUE_POLICY='lease_fenced_v1',
            PRODUCTION_QUEUE_ENABLED='0', NOTIFICATION_QUEUE_ENABLED='0',
            PRODUCTION_LOGIN_CLIENT_POLICY='peer_shared_fail_safe')

    def load(self, changes=None, contract_changes=None):
        c = {**self.contract, **(contract_changes or {})}
        self.file.write_text(json.dumps(c), encoding='utf-8')
        env = {**self.env, 'PRODUCTION_ADMISSION_SHA256': hashlib.sha256(self.file.read_bytes()).hexdigest(), **(changes or {})}
        with patch.dict(os.environ, env, clear=True):
            return load_config()

    def test_local_contract_enables_product_but_does_not_claim_bot_admission(self):
        c = self.load()
        self.assertTrue(c.mini_app_enabled)
        self.assertTrue(c.viewer_plus_enabled)
        self.assertTrue(c.streamer_plus_enabled)
        self.assertFalse(c.production_admitted)
        self.assertFalse(c.pinned_staging)
        self.assertFalse(c.growth_enabled)

    def test_each_required_operator_input_is_missing_empty_or_mismatched_fail_closed(self):
        for key in ('RAILWAY_PROJECT_ID', 'RAILWAY_ENVIRONMENT_ID', 'RAILWAY_ENVIRONMENT_NAME',
            'RAILWAY_SERVICE_ID', 'RAILWAY_VOLUME_ID', 'RAILWAY_VOLUME_INSTANCE_ID',
            'RAILWAY_VOLUME_MOUNT_PATH', 'DB_PATH', 'PUBLIC_URL', 'OWNER_CHAT_ID',
            'ADMIN_TELEGRAM_BOT_USERNAME', 'TOKEN_ENCRYPTION_KEY', 'ADMIN_PANEL_ACCESS_KEY',
            'TWITCH_CLIENT_ID', 'TWITCH_CLIENT_SECRET', 'TELEGRAM_BOT_TOKEN', 'PORT',
            'PRODUCTION_REPLICA_COUNT', 'PRODUCTION_PAYMENT_POLICY', 'PRODUCTION_WRITER_POLICY',
            'PRODUCTION_QUEUE_POLICY', 'PRODUCTION_QUEUE_ENABLED', 'PRODUCTION_LOGIN_CLIENT_POLICY',
            'PRODUCTION_ADMISSION_SHA256'):
            for value in ('', 'OWNER INPUT', 'staging'):
                with self.subTest(key=key, value=value), self.assertRaises(ConfigError):
                    self.load({key: value})

    def test_testbot_staging_target_and_unsafe_paths_are_rejected(self):
        for change in ({'bot_id': 8859004067}, {'bot_username': 'TwitchSignalTestbot'},
            {'environment_id': '7a873177-8ada-4b78-8732-a0bfdc1d519b'},
            {'volume_instance_id': 'f1e0d4c5-c190-4989-ae34-29a61f9010bb'},
            {'db_path': '/data/../tmp/bot.db'}, {'mount_path': '/'},
            {'replica_count': 2}, {'queue_policy': 'anything'}, {'payment_policy': 'on'}):
            with self.subTest(change=change), self.assertRaises(ConfigError):
                self.load(contract_changes=change)

    def test_queue_requires_separate_matching_opt_in(self):
        with self.assertRaises(ConfigError):
            self.load({'NOTIFICATION_QUEUE_ENABLED': '1'})
        c = self.load({'NOTIFICATION_QUEUE_ENABLED': '1', 'PRODUCTION_QUEUE_ENABLED': '1'},
                      {'queue_enabled': True})
        self.assertTrue(c.notification_queue_enabled)
        self.assertFalse(c.production_admitted)

    def test_placeholder_domains_cannot_be_production_contract_values(self):
        for url in ('https://example.com', 'https://bot.invalid', 'https://bot.test', 'https://localhost'):
            with self.subTest(url=url), self.assertRaises(ConfigError):
                self.load({'PUBLIC_URL': url}, {'public_url': url})

    def test_money_secrets_cannot_enable_first_release_policy(self):
        c = self.load({'PLATEGA_SECRET': 'local', 'STARS_ENABLED': '1', 'BILLING_MODE': 'sandbox'})
        from bot.config import first_release_payment_policy
        policy = first_release_payment_policy()
        self.assertFalse(policy.allow_external_create)
        self.assertFalse(policy.allow_invoice)

    def test_no_optin_keeps_legacy_production_features_closed(self):
        with patch.dict(os.environ, {k: v for k, v in self.env.items() if not k.startswith('PRODUCTION_')}, clear=True):
            c = load_config()
        self.assertFalse(c.mini_app_enabled)
        self.assertFalse(c.notification_queue_enabled)


class IdentityTests(unittest.IsolatedAsyncioTestCase):
    async def test_wrong_bot_stops_before_database_and_any_user_actions(self):
        import main
        local = AdmissionTests()
        local.setUp()
        self.addCleanup(local.tmp.cleanup)
        config = local.load()
        for identity in (SimpleNamespace(id=123456789, username='WrongBot'),
                         SimpleNamespace(id=999, username='OwnerProductionBot')):
            bot = SimpleNamespace(get_me=AsyncMock(return_value=identity), session=SimpleNamespace(close=AsyncMock()))
            db = SimpleNamespace(connect=AsyncMock(), close=AsyncMock())
            with (patch.object(main, 'load_config', return_value=config),
                  patch.object(main, 'Bot', return_value=bot), patch.object(main, 'Database', return_value=db),
                  patch('bot.production_admission.validate_storage'), self.assertRaises(ConfigError)):
                await main.main()
            db.connect.assert_not_awaited()
            bot.session.close.assert_awaited_once()

    async def test_correct_identity_precedes_database_constructor(self):
        import main
        from dataclasses import replace
        local = AdmissionTests()
        local.setUp()
        self.addCleanup(local.tmp.cleanup)
        config = local.load()
        events = []
        async def get_me():
            events.append('getMe')
            return SimpleNamespace(id=123456789, username='OwnerProductionBot')
        bot = SimpleNamespace(get_me=get_me, session=SimpleNamespace(close=AsyncMock()))
        def construct(*args, **kwargs):
            events.append('database')
            raise ConfigError('fixture stop before migration')
        with (patch.object(main, 'load_config', return_value=config),
              patch.object(main, 'Bot', return_value=bot), patch.object(main, 'Database', side_effect=construct),
              patch('bot.production_admission.validate_storage'), patch('bot.production_admission.WriterLock'),
              self.assertRaises(ConfigError)):
            await main.main()
        self.assertEqual(events, ['getMe', 'database'])
        self.assertEqual(main._menu_button_for_config(replace(config, production_admitted=True)).web_app.url,
                         'https://notifications.fixture-owned.ru/app')
        with self.assertRaises(ConfigError):
            main._make_notification_worker(config, None, None)


class StorageAndWriterTests(unittest.TestCase):
    def test_second_writer_is_stopped_by_real_filesystem_lock(self):
        from bot.production_admission import WriterLock
        with tempfile.TemporaryDirectory() as directory:
            contract = SimpleNamespace(mount_path=directory)
            first = WriterLock(contract)
            try:
                with self.assertRaises(ConfigError):
                    WriterLock(contract)
            finally:
                first.close()
            next_writer = WriterLock(contract)
            next_writer.close()

    def test_missing_mount_and_source_stop_before_database(self):
        from bot.production_admission import validate_storage
        with tempfile.TemporaryDirectory() as directory:
            contract = SimpleNamespace(mount_path=directory, db_path=str(Path(directory)/'missing.db'))
            with self.assertRaises(ConfigError):
                validate_storage(contract)
