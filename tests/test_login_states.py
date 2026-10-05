"""Состояния входа: чужой поток не должен закрывать вход владельцу."""
import unittest
from unittest.mock import patch

from bot.login_states import PER_CLIENT_ACTIVE, TRUSTED_HEADROOM, LoginStates


class LoginStatesTests(unittest.TestCase):
    def test_trusted_client_gets_a_state_when_the_pool_is_full(self):
        states = LoginStates(4, per_client_per_minute=100)
        for index in range(20):
            states.new(f"anonymous-{index}")
        # Чужому при заполненном пуле по-прежнему отказывают: чужие состояния
        # не вытесняются.
        self.assertIsNone(states.new("stranger"))
        # Подтверждённому владельцу место находится всегда.
        self.assertIsNotNone(states.new("owner", trusted=True))

    def test_trusted_headroom_is_finite(self):
        states = LoginStates(1, per_client_per_minute=100)
        for index in range(TRUSTED_HEADROOM + 4):
            states.new(f"owner-{index}", trusted=True)
        self.assertLessEqual(len(states.entries), 1 + TRUSTED_HEADROOM)

    def test_one_source_cannot_flood_the_pool(self):
        states = LoginStates(1000, per_client_per_minute=3)
        created = [states.new("scanner") for _ in range(6)]
        self.assertEqual(sum(1 for state in created if state), 3)
        self.assertEqual(sum(1 for state in created if state is None), 3)

    def test_existing_state_is_reused_without_spending_the_limit(self):
        states = LoginStates(1000, per_client_per_minute=1)
        first = states.new("owner")
        self.assertIsNotNone(first)
        again = states.new("owner", existing=first)
        self.assertEqual(again, first)
        # Лимит уже израсходован: второе новое состояние не выдаётся.
        self.assertIsNone(states.new("owner"))

    def test_one_source_cannot_keep_more_than_the_active_limit(self):
        states = LoginStates(1000, per_client_per_minute=100)
        created = [states.new("owner") for _ in range(PER_CLIENT_ACTIVE + 1)]
        self.assertEqual(sum(1 for state in created if state), PER_CLIENT_ACTIVE)
        self.assertIsNone(created[-1])

    def test_state_is_one_time_and_bound_to_its_cookie(self):
        states = LoginStates(10)
        state = states.new("owner")
        other = states.new("other")
        self.assertFalse(states.consume(state, other))
        self.assertTrue(states.consume(state, state))
        self.assertFalse(states.consume(state, state))

    def test_expired_states_are_dropped(self):
        states = LoginStates(10)
        with patch("bot.login_states.time.monotonic", return_value=1000.0):
            state = states.new("owner")
            self.assertIsNotNone(state)
        with patch("bot.login_states.time.monotonic", return_value=1000.0 + 400):
            self.assertFalse(states.consume(state, state))
            self.assertEqual(states.entries, {})


if __name__ == "__main__":
    unittest.main()
