"""C2: идентификатор Telegram выше поддерживаемой границы не доходит до SQLite.

Подпись бота проходила для id ≥2**63, и маршруты падали 500-й ошибкой вместо
честного отказа: целочисленные поля SQLite такой id не принимают.
"""

import hashlib
import hmac
import json
import os
import tempfile
import time
import unittest
from urllib.parse import urlencode

import aiohttp

from bot.database import Database
from bot.oauth import OAuthCallbackServer
from bot.telegram_identity import (
    verify_login_widget_user,
    verify_webapp_identity,
    verify_webapp_user,
)
from tests.test_admin_telegram_auth import BOT_TOKEN


def signed_identity(user, *, at=None):
    """Подпись Mini App по правилам Telegram; user — словарь или готовая строка."""
    fields = {
        "user": user if isinstance(user, str) else json.dumps(user, separators=(",", ":")),
        "auth_date": str(int(time.time()) if at is None else at),
    }
    data = "\n".join(f"{key}={value}" for key, value in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, data.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


def signed_widget(user_id, *, at=None):
    """Подпись Telegram Login Widget для того же тестового бота."""
    fields = {
        "auth_date": str(int(time.time()) if at is None else at),
        "first_name": "Owner",
        "id": str(user_id),
    }
    data = "\n".join(f"{key}={value}" for key, value in sorted(fields.items()))
    fields["hash"] = hmac.new(
        hashlib.sha256(BOT_TOKEN.encode()).digest(), data.encode(), hashlib.sha256
    ).hexdigest()
    return fields


class TelegramIdentityBoundsTests(unittest.TestCase):
    def test_ids_above_the_supported_bound_are_rejected_before_storage(self):
        for user_id in (2 ** 52, 2 ** 53, 2 ** 63 - 1, 2 ** 63, 2 ** 80):
            with self.subTest(user_id=user_id):
                self.assertIsNone(
                    verify_webapp_identity(signed_identity({"id": user_id}), BOT_TOKEN))
                self.assertIsNone(verify_webapp_user(signed_identity({"id": user_id}), BOT_TOKEN))
                self.assertIsNone(
                    verify_login_widget_user(signed_widget(user_id), BOT_TOKEN))

    def test_real_telegram_ids_still_pass(self):
        for user_id in (1, 202, 425785231, 2 ** 40, 2 ** 52 - 1):
            with self.subTest(user_id=user_id):
                identity = verify_webapp_identity(signed_identity({"id": user_id}), BOT_TOKEN)
                self.assertEqual(identity.id, user_id)
                self.assertEqual(verify_login_widget_user(signed_widget(user_id), BOT_TOKEN), user_id)


class HugeIdentityRouteTests(unittest.IsolatedAsyncioTestCase):
    """Отказ приходит клиенту как 403, а не как 500 от базы."""

    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.db = Database(os.path.join(self.directory.name, "identity.db"))
        await self.db.connect()
        self.addAsyncCleanup(self.db.close)
        self.server = OAuthCallbackServer(
            "https://example.test/twitch/callback", "127.0.0.1", 0,
            viewer_db=self.db, viewer_bot_token=BOT_TOKEN,
            mini_app_db=self.db, mini_app_bot_token=BOT_TOKEN,
        )
        await self.server.start()
        self.addAsyncCleanup(self.server.stop)
        self.session = aiohttp.ClientSession()
        self.addAsyncCleanup(self.session.close)
        self.base = f"http://127.0.0.1:{self.server._runner.addresses[0][1]}"

    async def test_huge_signed_user_id_gets_a_client_error_not_a_server_error(self):
        init_data = signed_identity({"id": 2 ** 63})
        for path in ("/app/api/bootstrap", "/app/api/subscription/state",
                     "/viewer/api/state"):
            with self.subTest(path=path):
                async with self.session.post(
                    self.base + path, json={"init_data": init_data},
                ) as response:
                    self.assertEqual(response.status, 403)
                    self.assertNotIn("Traceback", await response.text())


if __name__ == "__main__":
    unittest.main()
