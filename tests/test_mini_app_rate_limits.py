"""Ограничители частоты на дорогих путях мини-аппа.

Аудит (D13, D14, D18): подготовка покупки, проверка статуса, отчёты и отписка
не имели ограничений, а один человек мог занимать общий ресурс. Лимит должен
быть на пользователя и не позволять одному вытеснить остальных.
"""
import time
import unittest

from bot.mini_app_limits import RequestBudget


class RequestBudgetTests(unittest.TestCase):
    def test_one_user_is_limited_and_the_window_recovers(self):
        budget = RequestBudget(per_user=5, window_seconds=60.0)

        allowed = [budget.admit(101, now=1000.0 + index) for index in range(8)]

        self.assertEqual(allowed.count(True), 5)
        self.assertEqual(allowed.count(False), 3)
        # Через окно лимит снова доступен.
        self.assertTrue(budget.admit(101, now=1000.0 + 61))

    def test_other_users_are_not_affected_by_a_noisy_one(self):
        budget = RequestBudget(per_user=3, window_seconds=60.0)
        for index in range(10):
            budget.admit(101, now=1000.0 + index)

        self.assertTrue(budget.admit(202, now=1005.0))
        self.assertTrue(budget.admit(303, now=1005.0))

    def test_memory_stays_bounded_when_many_users_come(self):
        budget = RequestBudget(per_user=2, window_seconds=60.0, max_users=50)

        for user_id in range(500):
            budget.admit(1000 + user_id, now=2000.0)

        self.assertLessEqual(budget.tracked_users(), 50)

    def test_a_global_ceiling_protects_the_whole_service(self):
        budget = RequestBudget(per_user=100, window_seconds=60.0, global_limit=10)

        allowed = [budget.admit(1000 + index, now=3000.0) for index in range(20)]

        self.assertEqual(allowed.count(True), 10)


if __name__ == "__main__":
    unittest.main()
