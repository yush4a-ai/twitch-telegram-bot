"""Canonical documents and configured contact; no invented published terms."""

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiohttp.test_utils import TestClient, TestServer
from aiohttp import web

from bot.database import Database
from bot.mini_app_web import install_mini_app_routes
from bot.plan_catalog import catalog_payload
from tests.test_admin_telegram_auth import BOT_TOKEN, signed_webapp


def complete_owner():
    return SimpleNamespace(support_username="verified_support_person", support_email="support@example.com",
                           legal_operator="Подтверждённый оператор для локальной проверки",
                           legal_operator_address="Адрес из локальной проверки",
                           legal_retention="Сроки хранения согласованы отдельно в локальной проверке.",
                           legal_refund_policy="Правило возвратов из локальной проверки.",
                           legal_chargeback_policy="Правило сверки из локальной проверки.")


class LegalDocumentsTests(unittest.TestCase):
    def store(self, *, accepted=True, required=(), document_id='tariffs'):
        from bot.legal_documents import LegalDocumentStore
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        body = "# Возможности TwitchSignalBot\n\nViewer Plus — 150 ₽ / месяц. Streamer Plus — 300 ₽ / месяц.\n\nБез автопродления.\n\n- Telegram ID и настройки\n- Twitch identity\n"
        filename = 'PRIVACY-POLICY.md' if document_id == 'privacy' else 'TARIFFS.md'
        (root / filename).write_text(body, encoding="utf-8", newline='\n')
        manifest = {"version": "local-v1", "documents": [{"id": document_id, "filename": filename,
                     "title": "Тарифы", "version": "local-v1", "source_sha256": hashlib.sha256(body.encode()).hexdigest(),
                     "catalog_version": catalog_payload()["version"], "required_owner_inputs": list(required),
                     "owner_accepted": accepted}]}
        (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        return LegalDocumentStore(root), root, manifest

    def test_privacy_mandatory_inputs_cannot_be_dropped_from_manifest(self):
        store, _, _ = self.store(document_id='privacy', required=())
        self.assertFalse(store.get('privacy', catalog_payload(), None).ready)

    def test_unfilled_inputs_and_unaccepted_documents_never_publish(self):
        store, _, _ = self.store(required=("operator", "retention", "support_contact"))
        doc = store.get("tariffs", catalog_payload(), SimpleNamespace())
        self.assertFalse(doc.ready)
        self.assertNotIn("Telegram ID", doc.body_html)
        self.assertNotIn("[", doc.body_html)
        self.assertIn("Документ пока недоступен", doc.body_html)
        store, _, _ = self.store(accepted=False)
        self.assertFalse(store.get("tariffs", catalog_payload(), complete_owner()).ready)

    def test_source_and_catalog_mismatch_fail_closed_and_paths_are_allowlisted(self):
        store, root, manifest = self.store()
        self.assertTrue(store.get("tariffs", catalog_payload(), complete_owner()).ready)
        (root / "TARIFFS.md").write_text("# Changed without approval", encoding="utf-8")
        self.assertFalse(store.get("tariffs", catalog_payload(), complete_owner()).ready)
        manifest["documents"][0]["filename"] = "../secrets.env"
        (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        self.assertFalse(store.get("tariffs", catalog_payload(), complete_owner()).ready)
        store, _, _ = self.store()
        catalog = {**catalog_payload(), "version": "unapproved-future"}
        self.assertFalse(store.get("tariffs", catalog, complete_owner()).ready)
        with self.assertRaises(KeyError):
            store.get("../../config.py", catalog_payload(), complete_owner())

    def test_full_readable_body_uses_prices_and_crlf_does_not_change_document_identity(self):
        store, root, _ = self.store(required=("operator", "retention", "support_contact"))
        original = (root / "TARIFFS.md").read_bytes()
        (root / "TARIFFS.md").write_bytes(original.replace(b'\n', b'\r\n'))
        doc = store.get("tariffs", catalog_payload(), complete_owner())
        self.assertTrue(doc.ready)
        for text in ("150 ₽", "300 ₽", "Без автопродления", "Telegram ID", "<ul>"):
            self.assertIn(text, doc.body_html)
        self.assertEqual(doc.version, "local-v1")

    def test_renderer_escapes_scripts_and_only_accepts_checked_https_mailto(self):
        from bot.legal_documents import render_markdown
        html = render_markdown('# Заголовок\n\n<script>alert(1)</script>\n\n[Официальный сайт](https://example.com/legal)\n\n[Плохой](javascript:alert)\n\n[Внутренний](https://127.0.0.1/a)\n\n[Почта](mailto:support@example.com)')
        self.assertIn('&lt;script&gt;', html)
        self.assertNotIn('<script>', html)
        self.assertIn('href="https://example.com/legal"', html)
        self.assertIn('href="mailto:support@example.com"', html)
        self.assertNotIn('href="javascript:', html)
        self.assertNotIn('href="https://127.', html)

    def test_renderer_rejects_noncanonical_numeric_hosts(self):
        from bot.legal_documents import render_markdown
        for url in ('https://0177.0.0.1/a', 'https://0x7f.0.0.1/a', 'https://127.1/a',
                    'https://127.0.0.1\\example.com/a', 'https://%31%32%37.0.0.1/a',
                    'https://public.example.%74%65%73%74/a'):
            with self.subTest(url=url):
                self.assertNotIn('href=', render_markdown(f'[Ссылка]({url})'))

    def test_contact_has_no_fake_defaults_and_group_only_is_not_contact(self):
        from bot.legal_documents import get_support_state
        for config in (None, SimpleNamespace(support_group_url='https://t.me/example_group'),
                       SimpleNamespace(support_username='https://t.me/group', support_email='mailto:x@example.com'),
                       SimpleNamespace(support_username='bad/name', support_email='x@example.com\r\nBcc:other@example.com')):
            with self.subTest(config=config):
                state = get_support_state(config)
                self.assertFalse(state.available)
                self.assertIsNone(state.telegram_url)
                self.assertIsNone(state.email)
                self.assertFalse(state.bank_ready)
        state = get_support_state(complete_owner())
        self.assertTrue(state.available)
        self.assertEqual(state.telegram_url, 'https://t.me/verified_support_person')
        self.assertEqual(state.email, 'support@example.com')

    def test_support_configuration_is_optional_validated_and_never_defaulted(self):
        from bot.config import load_config, ConfigError
        required = {'TELEGRAM_BOT_TOKEN': BOT_TOKEN, 'TWITCH_CLIENT_ID': 'local', 'TWITCH_CLIENT_SECRET': 'local'}
        with patch.dict(os.environ, required, clear=True):
            config = load_config()
        self.assertIsNone(config.support_username)
        self.assertIsNone(config.support_email)
        with patch.dict(os.environ, {**required, 'SUPPORT_USERNAME': '@verified_support_person', 'SUPPORT_EMAIL': 'support@example.com'}, clear=True):
            config = load_config()
        self.assertEqual(config.support_username, 'verified_support_person')
        self.assertEqual(config.support_email, 'support@example.com')
        for field, value in [('SUPPORT_USERNAME', 'https://t.me/group'), ('SUPPORT_EMAIL', 'x@example.com\r\nBcc:x@example.com')]:
            with self.subTest(field=field), patch.dict(os.environ, {**required, field: value}, clear=True):
                with self.assertRaises(ConfigError) as caught:
                    load_config()
                self.assertNotIn(value, str(caught.exception))


class PaymentSupportTests(unittest.IsolatedAsyncioTestCase):
    async def test_paysupport_uses_same_contact_without_external_send(self):
        from bot.handlers.payments import on_payment_support, build_payment_router
        message = SimpleNamespace(answer=AsyncMock())
        await on_payment_support(message, None)
        text = message.answer.call_args.args[0]
        self.assertIn('Контакт поддержки пока не указан', text)
        self.assertNotIn('t.me/', text)
        message.answer.reset_mock()
        await on_payment_support(message, complete_owner())
        text = message.answer.call_args.args[0]
        self.assertIn('https://t.me/verified_support_person', text)
        self.assertIn('support@example.com', text)
        self.assertIn(on_payment_support, [handler.callback for handler in build_payment_router().message.handlers])

    async def test_oauth_server_keeps_supplied_owner_config(self):
        from bot.oauth import OAuthCallbackServer
        owner = complete_owner()
        server = OAuthCallbackServer('http://localhost/oauth/callback', '127.0.0.1', 0, mini_app_owner_config=owner)
        self.assertIs(server._mini_app_owner_config, owner)


class MiniAppLegalRoutesTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.db = Database(str(Path(self.directory.name) / 'legal.db'))
        await self.db.connect()
        self.addAsyncCleanup(self.db.close)
        app = web.Application()
        install_mini_app_routes(app, self.db, BOT_TOKEN)
        self.client = TestClient(TestServer(app))
        await self.client.start_server()
        self.addAsyncCleanup(self.client.close)

    async def test_unprepared_public_documents_and_signed_support_are_honest(self):
        response = await self.client.post('/app/api/support/state', json={'init_data': signed_webapp(101)})
        self.assertEqual(response.status, 200)
        state = await response.json()
        self.assertFalse(state['available'])
        self.assertFalse(state['bank_ready'])
        self.assertIsNone(state['telegram_url'])
        self.assertIsNone(state['email'])
        self.assertEqual({row['id'] for row in state['documents']}, {'privacy', 'agreement', 'support', 'tariffs', 'payments'})
        # Без реквизитов оператора доступны только те документы, которым они не нужны.
        ready = {row['id'] for row in state['documents'] if row['ready']}
        self.assertEqual(ready, {'tariffs'})
        for path in ('privacy', 'agreement', 'support', 'payments'):
            response = await self.client.get('/app/legal/' + path)
            self.assertEqual(response.status, 503)
            html = await response.text()
            self.assertIn('Документ пока недоступен', html)
            self.assertNotIn('[', html)
            self.assertIn("script-src 'self'", response.headers['Content-Security-Policy'])
            self.assertIn('no-store', response.headers['Cache-Control'])
            self.assertIn('noindex', response.headers['X-Robots-Tag'])
        self.assertEqual((await self.client.get('/app/legal/tariffs')).status, 200)
        self.assertEqual((await self.client.get('/app/legal/config.py')).status, 404)
        self.assertEqual((await self.client.post('/app/api/support/state', json={})).status, 401)
        self.assertEqual((await self.client.post('/app/api/support/state', json={'init_data': signed_webapp(101), 'ready': True})).status, 400)

    async def test_canonical_manifest_documents_and_assets_are_in_release_inputs(self):
        root = Path(__file__).resolve().parents[1]
        manifest = json.loads((root / 'docs/legal/manifest.json').read_text(encoding='utf-8'))
        # Владелец принял редакции 05.10.2026: без этого флага документы не публикуются.
        self.assertTrue(all(row['owner_accepted'] is True for row in manifest['documents']))
        for row in manifest['documents']:
            path = root / 'docs/legal' / row['filename']
            digest = hashlib.sha256(path.read_text(encoding='utf-8').replace('\r\n','\n').encode()).hexdigest()
            self.assertEqual(row['source_sha256'], digest)
            self.assertEqual(row['catalog_version'], catalog_payload()['version'])
        for path in ('bot/legal_ui/index.html', 'bot/legal_ui/legal.css', 'docs/workflows/PLATEGA-BANK-APPROVAL.md', 'docs/workflows/OWNER-INPUTS-FOR-LAUNCH.md'):
            self.assertTrue((root/path).is_file(), path)

    async def test_confirmed_temporary_documents_publish_full_escaped_text(self):
        import shutil
        from bot.legal_documents import CANONICAL_DIR, LegalDocumentStore
        root = Path(self.directory.name)/'approved-documents'
        shutil.copytree(CANONICAL_DIR, root)
        path = root/'manifest.json'
        manifest = json.loads(path.read_text(encoding='utf-8'))
        for row in manifest['documents']:
            row['owner_accepted'] = True
        path.write_text(json.dumps(manifest), encoding='utf-8')
        app = web.Application()
        install_mini_app_routes(app, self.db, BOT_TOKEN, owner_config=complete_owner(), legal_store=LegalDocumentStore(root))
        client = TestClient(TestServer(app))
        await client.start_server()
        self.addAsyncCleanup(client.close)
        response = await client.post('/app/api/support/state', json={'init_data': signed_webapp(101)})
        self.assertTrue((await response.json())['bank_ready'])
        for row in manifest['documents']:
            with self.subTest(document=row['id']):
                response = await client.get('/app/legal/'+row['id']+'?format=json')
                self.assertEqual(response.status, 200)
                document = await response.json()
                self.assertEqual(document['id'], row['id'])
                self.assertTrue(document['ready'])
                self.assertGreater(len(document['body_html']), 600)
                self.assertNotIn('<script',document['body_html'])
                if row['id'] in {'agreement','tariffs','payments'}:
                    self.assertIn('150 ₽',document['body_html'])
                    self.assertIn('300 ₽',document['body_html'])
                    self.assertNotIn('200 ₽',document['body_html'])
                response = await client.get('/app/legal/'+row['id'])
                self.assertEqual(response.status,200)
                self.assertIn(document['body_html'],await response.text())
