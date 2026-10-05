"""Здоровье EventSub: тревога живёт только пока канал действительно сломан."""

import time
import unittest
from unittest.mock import Mock

from bot.follow_listener import FollowEventListener
from bot.token_store import TokenStore


def listener() -> FollowEventListener:
    return FollowEventListener(Mock(), Mock(), "client", Mock())


class TokenBlockedLoginsTests(unittest.TestCase):
    def test_blocked_logins_are_listed_but_tokens_are_not(self):
        store = TokenStore(Mock(), "client", "secret", Mock())
        store._terminal_refresh_tokens = {"two": "refresh-secret-two", "one": "refresh-secret-one"}

        self.assertEqual(store.health_snapshot(), {"auth_blocked_logins": 2})
        names = store.blocked_logins()
        self.assertEqual(names, ["one", "two"])
        self.assertNotIn("refresh-secret", str(names))


class FollowHealthTests(unittest.TestCase):
    def test_error_is_quiet_when_no_channel_is_failing(self):
        subject = listener()
        subject._last_error = "TwitchAuthError"
        subject._last_error_at = time.time()

        snapshot = subject.health_snapshot()

        # Ошибка осталась в истории, но активной тревоги нет: каналы подключены.
        self.assertIsNone(snapshot["last_error"])
        self.assertEqual(snapshot["failed_logins"], 0)

    def test_error_is_visible_while_a_channel_fails(self):
        subject = listener()
        subject._last_error = "TwitchAuthError"
        subject._last_error_at = time.time()
        subject._failed.add("dobriy_yura")

        snapshot = subject.health_snapshot()

        self.assertEqual(snapshot["last_error"], "TwitchAuthError")
        self.assertEqual(snapshot["failed_logins"], 1)

    def test_recovery_clears_the_active_error(self):
        subject = listener()
        subject._last_error = "TwitchAuthError"
        subject._failed.add("dobriy_yura")
        subject._failed.discard("dobriy_yura")

        self.assertIsNone(subject.health_snapshot()["last_error"])


if __name__ == "__main__":
    unittest.main()
