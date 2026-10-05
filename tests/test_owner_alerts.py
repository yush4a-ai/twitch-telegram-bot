"""Оповещения владельцу: переходы состояний, тишина без изменений, антиспам."""
import unittest
from unittest.mock import AsyncMock

from bot.owner_alerts import OwnerAlerter, subsystem_states


def snapshot(*, twitch="ok", failed_jobs=0, database_error=False, backup_at=1000.0):
    return {
        "twitch": {"state": twitch},
        "queues": {"failed_jobs": failed_jobs},
        "errors": {"database": "ошибка" if database_error else None},
        "backup": {"last_backup_at": backup_at},
    }


class StateTests(unittest.TestCase):
    def test_states_follow_the_snapshot(self):
        states = subsystem_states(snapshot())
        self.assertEqual(states["twitch"], "ok")
        self.assertEqual(states["queue"], "ok")
        self.assertEqual(states["database"], "ok")
        self.assertEqual(states["backup"], "ok")

    def test_unknown_data_is_not_treated_as_failure(self):
        states = subsystem_states({})
        # Нет данных — нет и тревоги: снимок ещё не собран.
        self.assertEqual(states, {})

    def test_failures_are_detected(self):
        states = subsystem_states(snapshot(
            twitch="degraded", failed_jobs=3, database_error=True, backup_at=None))
        self.assertEqual(states["twitch"], "bad")
        self.assertEqual(states["queue"], "bad")
        self.assertEqual(states["database"], "bad")
        self.assertEqual(states["backup"], "bad")


class OwnerAlerterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.sent: list[str] = []

        async def send(text):
            self.sent.append(text)

        self.clock = [1000.0]
        self.alerter = OwnerAlerter(send, cooldown=3600.0, clock=lambda: self.clock[0])

    async def test_transition_to_failure_sends_one_message(self):
        delivered = await self.alerter.inspect(snapshot(twitch="degraded"))
        self.assertEqual(len(delivered), 1)
        self.assertIn("Twitch", delivered[0])
        # В сообщении нет служебных подробностей и секретов.
        self.assertNotIn("token", delivered[0].lower())
        self.assertNotIn("/data", delivered[0])

    async def test_no_message_while_nothing_changes(self):
        await self.alerter.inspect(snapshot())
        self.assertEqual(await self.alerter.inspect(snapshot()), [])
        self.assertEqual(self.sent, [])

    async def test_same_failure_is_not_repeated_within_the_hour(self):
        await self.alerter.inspect(snapshot(database_error=True))
        self.assertEqual(len(self.sent), 1)
        self.clock[0] += 60
        self.assertEqual(await self.alerter.inspect(snapshot(database_error=True)), [])
        self.assertEqual(len(self.sent), 1)

    async def test_reminder_after_an_hour_of_silence(self):
        await self.alerter.inspect(snapshot(backup_at=None))
        self.assertEqual(len(self.sent), 1)
        self.clock[0] += 3600
        reminded = await self.alerter.repeat_if_still_bad(snapshot(backup_at=None))
        self.assertEqual(len(reminded), 1)
        self.assertIn("всё ещё", reminded[0])

    async def test_recovery_is_reported_once(self):
        await self.alerter.inspect(snapshot(failed_jobs=5))
        self.assertEqual(len(self.sent), 1)
        recovered = await self.alerter.inspect(snapshot(failed_jobs=0))
        self.assertEqual(len(recovered), 1)
        self.assertIn("снова", recovered[0])
        self.assertEqual(await self.alerter.inspect(snapshot(failed_jobs=0)), [])

    async def test_new_failure_within_the_quiet_hour_waits(self):
        await self.alerter.inspect(snapshot(twitch="degraded"))
        await self.alerter.inspect(snapshot(twitch="ok"))
        self.clock[0] += 10
        # Флаппинг не должен превращаться в поток сообщений: час тишины на подсистему.
        self.assertEqual(await self.alerter.inspect(snapshot(twitch="degraded")), [])
        self.clock[0] += 3600
        reminded = await self.alerter.repeat_if_still_bad(snapshot(twitch="degraded"))
        self.assertEqual(len(reminded), 1)

    async def test_without_a_sender_nothing_happens(self):
        alerter = OwnerAlerter(None)
        self.assertEqual(await alerter.inspect(snapshot(twitch="degraded")), [])

    async def test_send_failure_does_not_break_inspection(self):
        async def broken(_text):
            raise RuntimeError("telegram")

        alerter = OwnerAlerter(broken, clock=lambda: self.clock[0])
        self.assertEqual(await alerter.inspect(snapshot(twitch="degraded")), [])
        self.assertEqual(await alerter.inspect(snapshot()), [])


if __name__ == "__main__":
    unittest.main()
