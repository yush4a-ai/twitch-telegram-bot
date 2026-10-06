"""C9 и D15: пул сессий панели отказывает при заполнении, а не вытесняет старые.

Раньше 33-й вход владельца удалял самую старую рабочую сессию — владелец сам
выталкивал себя из уже открытой панели. Теперь, как в кабинете стримера,
освобождаются только истёкшие сессии.
"""

import unittest
from unittest.mock import patch

from bot.admin_auth import AdminAccess
from tests.test_admin_telegram_auth import BOT_TOKEN, KEY, OWNER_ID, signed_webapp


class AdminSessionPoolTests(unittest.TestCase):
    def test_full_pool_refuses_a_new_owner_webapp_session_without_eviction(self):
        access = AdminAccess(KEY, enabled=True, secure_cookie=False,
                             owner_id=OWNER_ID, bot_token=BOT_TOKEN)
        tokens = [access.login_webapp(signed_webapp(OWNER_ID)) for _ in range(32)]
        self.assertTrue(all(tokens))
        self.assertIsNone(access.login_webapp(signed_webapp(OWNER_ID)))
        # Старейшая сессия остаётся рабочей: вытеснения нет.
        self.assertTrue(access.authenticated(tokens[0]))
        self.assertEqual(len(access._sessions), 32)

    def test_emergency_key_reports_a_full_pool_instead_of_a_wrong_key(self):
        access = AdminAccess(KEY, enabled=True, secure_cookie=False)
        tokens = [access.login(KEY, f"10.0.0.{index}")[0] for index in range(32)]
        self.assertTrue(all(tokens))
        self.assertEqual(access.login(KEY, "10.0.0.200"), (None, True))
        self.assertTrue(access.authenticated(tokens[0]))

    def test_expired_sessions_free_the_pool_again(self):
        access = AdminAccess(KEY, enabled=True, secure_cookie=False,
                             owner_id=OWNER_ID, bot_token=BOT_TOKEN)
        # Время задаётся явно: с реальным TTL в миллисекунды тест нестабилен,
        # потому что 32 подписи могут создаваться дольше самого TTL.
        with patch("bot.admin_auth.time.monotonic", return_value=1000.0):
            for _ in range(32):
                self.assertIsNotNone(access.login_webapp(signed_webapp(OWNER_ID)))
            self.assertIsNone(access.login_webapp(signed_webapp(OWNER_ID)))
        with patch("bot.admin_auth.time.monotonic",
                   return_value=1000.0 + access.session_ttl + 1):
            self.assertIsNotNone(access.login_webapp(signed_webapp(OWNER_ID)))


if __name__ == "__main__":
    unittest.main()
