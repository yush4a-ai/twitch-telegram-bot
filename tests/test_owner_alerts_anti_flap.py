"""Оповещения владельцу не должны дребезжать.

Жалоба владельца: сообщения «Twitch: каналы требуют авторизации», «снова
работает» и «База данных: отвечает с ошибками» приходили часто. Причины две:
одна медленная проверка базы считалась ошибкой, а смена состояния отправлялась
сразу, даже если она держалась всего один цикл.

Теперь состояние подтверждается двумя проверками подряд (гистерезис), а база
считается сломанной только если запрос не уложился и во второй, более щедрый
таймаут.
"""
import asyncio
import unittest

from bot.owner_alerts import OwnerAlerter, subsystem_states


def snapshot(*, twitch="ok", database_error=False, awaiting_access=None):
    data = {
        "twitch": {"state": twitch},
        "queues": {"failed_jobs": 0},
        "errors": {"database": "ошибка" if database_error else None},
        "backup": {"last_backup_at": 1000.0},
    }
    if awaiting_access is not None:
        data["billing"] = {"awaiting_access": awaiting_access}
    return data


class HysteresisTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.sent = []

        async def send(text):
            self.sent.append(text)

        self.clock = [1000.0]
        self.alerter = OwnerAlerter(send, cooldown=3600.0, clock=lambda: self.clock[0])

    async def test_a_single_bad_check_does_not_raise_alarm(self):
        """Один сбойный цикл ещё не повод писать владельцу."""
        await self.alerter.inspect(snapshot(twitch="ok"))
        self.clock[0] += 300
        await self.alerter.inspect(snapshot(twitch="degraded"))

        self.assertEqual(self.sent, [])

    async def test_two_bad_checks_in_a_row_raise_one_alarm(self):
        await self.alerter.inspect(snapshot(twitch="ok"))
        for _ in range(2):
            self.clock[0] += 300
            await self.alerter.inspect(snapshot(twitch="degraded"))

        self.assertEqual(len(self.sent), 1)
        self.assertIn("Twitch", self.sent[0])

    async def test_flapping_between_bad_and_ok_stays_silent(self):
        """Если состояние скачет каждый цикл, владельцу не пишем вовсе."""
        await self.alerter.inspect(snapshot(twitch="ok"))
        for index in range(10):
            self.clock[0] += 300
            await self.alerter.inspect(snapshot(twitch="degraded" if index % 2 == 0 else "ok"))

        self.assertEqual(self.sent, [])

    async def test_recovery_needs_two_good_checks(self):
        await self.alerter.inspect(snapshot(twitch="ok"))
        for _ in range(2):
            self.clock[0] += 300
            await self.alerter.inspect(snapshot(twitch="degraded"))
        self.assertEqual(len(self.sent), 1)

        self.clock[0] += 300
        await self.alerter.inspect(snapshot(twitch="ok"))
        self.assertEqual(len(self.sent), 1)
        self.clock[0] += 300
        await self.alerter.inspect(snapshot(twitch="ok"))
        self.assertEqual(len(self.sent), 2)
        self.assertIn("снова", self.sent[1])

    async def test_a_real_persistent_database_error_is_reported(self):
        await self.alerter.inspect(snapshot())
        for _ in range(2):
            self.clock[0] += 300
            await self.alerter.inspect(snapshot(database_error=True))

        self.assertEqual(len(self.sent), 1)
        self.assertIn("База данных", self.sent[0])

    async def test_unknown_snapshot_produces_no_alarm(self):
        await self.alerter.inspect({})
        self.assertEqual(self.sent, [])
        self.assertEqual(subsystem_states({}), {})


class SlowDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_slow_but_working_database_is_not_an_error(self):
        """Запрос дольше первого таймаута, но успешный, не должен тревожить."""
        import bot.admin_metrics as metrics

        calls = []

        async def slow():
            calls.append(1)
            await asyncio.sleep(0.01)
            return {"ok": True}

        value, failed = await metrics._resilient(slow, first_timeout=0.001, second_timeout=1.0)

        self.assertEqual(value, {"ok": True})
        self.assertFalse(failed)
        self.assertEqual(len(calls), 2)

    async def test_a_really_broken_database_is_an_error(self):
        import bot.admin_metrics as metrics

        async def broken():
            raise RuntimeError("database is locked")

        value, failed = await metrics._resilient(broken, first_timeout=0.01, second_timeout=0.05)

        self.assertIsNone(value)
        self.assertTrue(failed)


if __name__ == "__main__":
    unittest.main()
