"""Отправитель рассылки: состояния получателей, ошибки Telegram, остановка."""
import os
import tempfile
import unittest

from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter

from bot.broadcast_worker import BroadcastWorker
from bot.database import Database

NOW = 1_700_000_000.0
OWNER = 777001
PEOPLE = (777010, 777011, 777012)


def forbidden() -> TelegramForbiddenError:
    return TelegramForbiddenError(method=None, message="bot was blocked by the user")


class BroadcastWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Database(os.path.join(self.tmp.name, "bot.db"))
        await self.db.connect()
        for user_id in (*PEOPLE, OWNER):
            await self.db.conn.execute(
                "INSERT OR IGNORE INTO known_private_users(user_id) VALUES (?)", (user_id,))
        await self.db.conn.commit()
        self.sent: list[int] = []

        async def sender(user_id, campaign):
            self.sent.append(user_id)

        self.sender = sender

    async def asyncTearDown(self):
        await self.db.close()

    async def start_campaign(self, *, fails=None):
        campaign_id = await self.db.save_broadcast_campaign(
            title="Новость", body="Текст", created_by=OWNER, now=NOW)
        await self.db.prepare_broadcast_recipients(
            campaign_id, list(PEOPLE), now=NOW)
        await self.db.set_broadcast_state(campaign_id, "sending", now=NOW)
        return campaign_id

    def worker(self, sender=None, **kwargs):
        return BroadcastWorker(
            self.db, sender or self.sender, clock=lambda: NOW, **kwargs)

    async def test_batch_marks_recipients_sent_and_closes_campaign(self):
        campaign_id = await self.start_campaign()
        processed = await self.worker().run_once()
        self.assertEqual(processed, 3)
        self.assertEqual(sorted(self.sent), sorted(PEOPLE))
        campaign = await self.db.get_broadcast_campaign(campaign_id)
        self.assertEqual(campaign["state"], "sent")
        progress = await self.db.broadcast_progress(campaign_id)
        self.assertEqual(progress["sent"], 3)
        self.assertEqual(progress["pending"], 0)

    async def test_second_run_does_not_send_again(self):
        await self.start_campaign()
        worker = self.worker()
        await worker.run_once()
        self.sent.clear()
        self.assertEqual(await worker.run_once(), 0)
        self.assertEqual(self.sent, [])

    async def test_blocked_person_is_unreachable_and_others_continue(self):
        async def sender(user_id, campaign):
            if user_id == 777011:
                raise forbidden()
            self.sent.append(user_id)

        campaign_id = await self.start_campaign()
        await self.worker(sender).run_once()
        self.assertEqual(sorted(self.sent), [777010, 777012])
        progress = await self.db.broadcast_progress(campaign_id)
        self.assertEqual(progress["sent"], 2)
        self.assertEqual(progress["unreachable"], 1)
        cursor = await self.db.conn.execute(
            "SELECT error_code FROM broadcast_recipients "
            "WHERE campaign_id = ? AND user_id = ?", (campaign_id, 777011))
        self.assertEqual((await cursor.fetchone())[0], "forbidden")

    async def test_ordinary_failure_is_recorded_and_does_not_stop_batch(self):
        async def sender(user_id, campaign):
            if user_id == 777010:
                raise RuntimeError("network")
            self.sent.append(user_id)

        campaign_id = await self.start_campaign()
        await self.worker(sender).run_once()
        progress = await self.db.broadcast_progress(campaign_id)
        self.assertEqual(progress["failed"], 1)
        self.assertEqual(progress["sent"], 2)

    async def test_stopped_campaign_is_not_sent(self):
        campaign_id = await self.start_campaign()
        await self.db.stop_broadcast(campaign_id, now=NOW)
        self.assertEqual(await self.worker().run_once(), 0)
        self.assertEqual(self.sent, [])
        progress = await self.db.broadcast_progress(campaign_id)
        self.assertEqual(progress["stopped"], 3)

    async def test_retry_after_keeps_the_person_in_the_queue(self):
        calls: list[int] = []

        async def sender(user_id, campaign):
            calls.append(user_id)
            if len(calls) == 1:
                raise TelegramRetryAfter(method=None, message="flood", retry_after=0)

        campaign_id = await self.start_campaign()
        worker = self.worker(sender)
        await worker.run_once()
        # Первый получатель остался в очереди, остальные не потерялись.
        progress = await self.db.broadcast_progress(campaign_id)
        self.assertEqual(progress["pending"], 1)
        self.assertEqual(progress["sent"], 2)

    async def test_send_budget_is_used_for_every_message(self):
        turns: list[bool] = []

        class Budget:
            async def wait_turn(self, *, normal: bool) -> None:
                turns.append(normal)

        await self.start_campaign()
        await self.worker(send_budget=Budget()).run_once()
        self.assertEqual(turns, [True, True, True])

    async def test_second_copy_does_not_take_a_person_already_being_sent_to(self):
        """Во время выкладки работают обе копии: человек не должен получить дважды."""
        import asyncio

        await self.start_campaign()
        release = asyncio.Event()
        started = asyncio.Event()

        async def slow_sender(user_id, campaign):
            self.sent.append(user_id)
            if user_id == 777010:
                started.set()
                await release.wait()

        first = self.worker(slow_sender)
        task = asyncio.create_task(first.run_once())
        await started.wait()      # первая копия уже захватила первого человека
        second = self.worker()    # вторая копия берёт ту же очередь
        await second.run_once()
        release.set()
        await task

        # Каждый получил ровно одно сообщение, несмотря на две копии сервиса.
        self.assertEqual(sorted(self.sent), sorted(PEOPLE))
        self.assertEqual(len(self.sent), len(set(self.sent)))

    async def test_failed_bookkeeping_does_not_resend_the_message(self):
        await self.start_campaign()
        original = self.db.mark_broadcast_recipient
        calls = {"count": 0}

        async def flaky(campaign_id, user_id, state, **kwargs):
            calls["count"] += 1
            if calls["count"] == 1:
                raise RuntimeError("db is busy")
            return await original(campaign_id, user_id, state, **kwargs)

        self.db.mark_broadcast_recipient = flaky
        try:
            worker = self.worker()
            await worker.run_once()
            await worker.run_once()
        finally:
            self.db.mark_broadcast_recipient = original

        # Запись сначала упала, но сообщение не ушло повторно.
        self.assertEqual(sorted(self.sent), sorted(PEOPLE))
        self.assertEqual(len(self.sent), len(set(self.sent)))

    async def test_optout_mid_batch_stops_the_rest(self):
        campaign_id = await self.start_campaign()
        # Получатели идут по возрастанию: 777010, 777011, 777012.
        async def sender(user_id, campaign):
            self.sent.append(user_id)
            if user_id == 777010:
                # Пока пачка в полёте, следующий человек нажал «Больше не присылать».
                await self.db.opt_out_broadcast(777011, now=NOW + 1)

        await self.worker(sender).run_once()
        self.assertEqual(self.sent, [777010, 777012])
        progress = await self.db.broadcast_progress(campaign_id)
        self.assertEqual(progress["opted_out"], 1)

    async def test_temporary_network_failure_keeps_the_person_in_the_queue(self):
        from aiogram.exceptions import TelegramNetworkError

        async def sender(user_id, campaign):
            raise TelegramNetworkError(method=None, message="timeout")

        campaign_id = await self.start_campaign()
        await self.worker(sender).run_once()
        progress = await self.db.broadcast_progress(campaign_id)
        # Это не «ошибка навсегда»: человек остался в очереди.
        self.assertEqual(progress["failed"], 0)
        self.assertEqual(progress["pending"], 3)

    async def test_invalid_settings_are_rejected(self):
        with self.assertRaises(ValueError):
            BroadcastWorker(self.db, self.sender, batch_size=0)
        with self.assertRaises(ValueError):
            BroadcastWorker(self.db, self.sender, idle_interval=0)


if __name__ == "__main__":
    unittest.main()
