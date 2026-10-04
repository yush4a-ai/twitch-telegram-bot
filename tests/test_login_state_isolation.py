import unittest
from unittest.mock import patch

from bot.admin_auth import AdminAccess
from bot.streamer_auth import StreamerAccess
from tests.test_admin_telegram_auth import BOT_TOKEN, KEY, signed_webapp


class LoginIsolationTests(unittest.TestCase):
    def accesses(self):
        return (AdminAccess(KEY, enabled=True, bot_token=BOT_TOKEN),
                StreamerAccess(BOT_TOKEN))

    def test_public_state_burst_never_evicts_other_clients(self):
        for access in self.accesses():
            with self.subTest(access=type(access).__name__):
                victim = access.new_login_state('victim')
                issued = [access.new_login_state('attacker') for _ in range(200)]
                self.assertLessEqual(sum(state is not None for state in issued), 4)
                self.assertTrue(access.consume_login_state(victim, victim))
                self.assertFalse(access.consume_login_state(victim, victim))

    def test_global_capacity_rejects_new_state_preserves_existing(self):
        for access, cap in zip(self.accesses(), (32, 128)):
            with self.subTest(access=type(access).__name__):
                states = [access.new_login_state(f'client{i}') for i in range(cap)]
                self.assertTrue(all(states))
                self.assertIsNone(access.new_login_state('overflow'))
                self.assertTrue(access.consume_login_state(states[0], states[0]))
                self.assertIsNotNone(access.new_login_state('overflow'))

    def test_expired_capacity_is_reclaimed(self):
        for access in self.accesses():
            with patch('time.monotonic', return_value=100):
                state = access.new_login_state('same')
            with patch('time.monotonic', return_value=401):
                self.assertFalse(access.consume_login_state(state, state))
                self.assertIsNotNone(access.new_login_state('same'))

    def test_repeated_streamer_login_only_revokes_own_sessions(self):
        access = StreamerAccess(BOT_TOKEN)
        victim = access.login_webapp(signed_webapp(101))
        for _ in range(200):
            attacker = access.login_webapp(signed_webapp(202))
        self.assertEqual(access.user_for_session(victim), 101)
        self.assertEqual(access.user_for_session(attacker), 202)
        self.assertLessEqual(len(access._sessions), 5)

    def test_full_streamer_sessions_fail_closed_without_eviction(self):
        access = StreamerAccess(BOT_TOKEN)
        tokens = [access.login_webapp(signed_webapp(1000+i)) for i in range(128)]
        self.assertIsNone(access.login_webapp(signed_webapp(2000)))
        self.assertEqual(access.user_for_session(tokens[0]), 1000)
        self.assertIsNotNone(access.login_webapp(signed_webapp(1000)))
