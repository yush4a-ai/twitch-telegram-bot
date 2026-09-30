"""Signed Telegram identity is the normal owner-panel credential."""

import hashlib
import hmac
import json
import time
import unittest
from urllib.parse import urlencode

from bot.admin_auth import AdminAccess


BOT_TOKEN = "123456:test-telegram-token"
OWNER_ID = 425785231
KEY = "staging-test-key-with-at-least-32-chars-123"


def signed_webapp(user_id: int, *, auth_date: int | None = None) -> str:
    fields = {
        "auth_date": str(int(time.time()) if auth_date is None else auth_date),
        "user": json.dumps({"id": user_id, "first_name": "Owner"}, separators=(",", ":")),
    }
    data = "\n".join(f"{key}={value}" for key, value in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, data.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


def signed_login(user_id: int, *, auth_date: int | None = None) -> dict[str, str]:
    fields = {"auth_date": str(int(time.time()) if auth_date is None else auth_date), "id": str(user_id), "first_name": "Owner"}
    data = "\n".join(f"{key}={value}" for key, value in sorted(fields.items()))
    fields["hash"] = hmac.new(hashlib.sha256(BOT_TOKEN.encode()).digest(), data.encode(), hashlib.sha256).hexdigest()
    return fields


class TelegramAdminAccessTests(unittest.TestCase):
    def setUp(self):
        self.access = AdminAccess(KEY, enabled=True, owner_id=OWNER_ID, bot_token=BOT_TOKEN)

    def test_owner_webapp_signature_grants_session(self):
        token = self.access.login_webapp(signed_webapp(OWNER_ID))
        self.assertTrue(self.access.authenticated(token))

    def test_non_owner_webapp_signature_is_denied(self):
        self.assertIsNone(self.access.login_webapp(signed_webapp(OWNER_ID + 1)))

    def test_modified_expired_future_and_duplicate_webapp_data_are_denied(self):
        good = signed_webapp(OWNER_ID)
        self.assertIsNone(self.access.login_webapp(good.replace("Owner", "Other")))
        self.assertIsNone(self.access.login_webapp(signed_webapp(OWNER_ID, auth_date=int(time.time()) - 601)))
        self.assertIsNone(self.access.login_webapp(signed_webapp(OWNER_ID, auth_date=int(time.time()) + 61)))
        self.assertIsNone(self.access.login_webapp(good + "&user=evil"))
        self.assertIsNone(self.access.login_webapp(good.replace("hash=", "hash=неверный")))

    def test_owner_login_widget_signature_grants_session(self):
        token = self.access.login_telegram_widget(signed_login(OWNER_ID))
        self.assertTrue(self.access.authenticated(token))

    def test_non_owner_or_invalid_widget_signature_is_denied(self):
        self.assertIsNone(self.access.login_telegram_widget(signed_login(OWNER_ID + 1)))
        bad = signed_login(OWNER_ID)
        bad["id"] = str(OWNER_ID + 1)
        self.assertIsNone(self.access.login_telegram_widget(bad))
        self.assertIsNone(self.access.login_telegram_widget(signed_login(OWNER_ID, auth_date=int(time.time()) - 601)))
        bad_hash = signed_login(OWNER_ID)
        bad_hash["hash"] = "неверная подпись"
        self.assertIsNone(self.access.login_telegram_widget(bad_hash))

    def test_unicode_state_is_denied_without_exception(self):
        state = self.access.new_login_state()
        self.assertFalse(self.access.consume_login_state("неверно", state))


if __name__ == "__main__":
    unittest.main()
