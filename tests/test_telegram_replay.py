import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from aiogram.types import Update
from bot.database import Database


def ordinary(uid, text='/start'):
    return Update.model_validate({'update_id': uid, 'message': {'message_id': uid,
        'date': 1700000000, 'chat': {'id': 101, 'type': 'private'},
        'from': {'id': 101, 'is_bot': False, 'first_name': 'A'}, 'text': text}})


class UpdateKindTests(unittest.TestCase):
    def test_kind_names_what_telegram_sent(self):
        from bot.telegram_replay import update_kind

        self.assertEqual(update_kind(ordinary(1)), 'message')
        callback = Update.model_validate({
            'update_id': 2,
            'callback_query': {
                'id': 'c', 'chat_instance': 'i',
                'from': {'id': 101, 'is_bot': False, 'first_name': 'A'},
                'data': 'menu',
            },
        })
        self.assertEqual(update_kind(callback), 'callback_query')

    def test_payment_stays_financial(self):
        from bot.telegram_replay import update_kind

        payment = Update.model_validate({
            'update_id': 3,
            'message': {
                'message_id': 3, 'date': 1700000000,
                'chat': {'id': 101, 'type': 'private'},
                'from': {'id': 101, 'is_bot': False, 'first_name': 'A'},
                'successful_payment': {
                    'currency': 'XTR', 'total_amount': 150,
                    'invoice_payload': 'p', 'telegram_payment_charge_id': 'c',
                    'provider_payment_charge_id': 'pr',
                },
            },
        })
        self.assertEqual(update_kind(payment), 'financial')


class ReplayTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = str(Path(self.tmp.name) / 'local.db')
        self.db = Database(self.path)
        await self.db.connect()
        self.addAsyncCleanup(self.db.close)

    def dispatcher(self, feed):
        from bot.telegram_replay import ReplayDispatcher
        dp = ReplayDispatcher()
        dp['db'] = self.db
        dp.feed_update = feed
        return dp

    async def test_completed_commands_survive_restart_without_second_mutation(self):
        calls = []
        async def feed(bot, update, **kwargs):
            calls.append(update.update_id)
            await self.db.add_channel(101, 'alpha')
        bot = SimpleNamespace(id=999)
        await self.dispatcher(feed)._process_update(bot, ordinary(1))
        await self.db.close()
        self.db = Database(self.path)
        await self.db.connect()
        self.addAsyncCleanup(self.db.close)
        await self.dispatcher(feed)._process_update(bot, ordinary(1))
        self.assertEqual(calls, [1])
        self.assertEqual(await self.db.list_channels(101), ['alpha'])

    async def test_received_pending_update_is_recovered_before_polling(self):
        u = ordinary(2, '/track alpha')
        await self.db.receive_telegram_update(999, 2, 'ordinary', u.model_dump_json())
        calls = []
        async def feed(bot, update, **kwargs):
            calls.append(update.update_id)
        await self.dispatcher(feed).recover_pending(SimpleNamespace(id=999))
        self.assertEqual(calls, [2])

    async def test_crash_after_action_is_unknown_and_never_automatically_repeats(self):
        calls = []
        async def feed(bot, update, **kwargs):
            calls.append(update.update_id)
            await self.db.add_channel(101, 'alpha')
            raise asyncio.CancelledError()
        dp = self.dispatcher(feed)
        bot = SimpleNamespace(id=999)
        with self.assertRaises(asyncio.CancelledError):
            await dp._process_update(bot, ordinary(3))
        await dp.recover_pending(bot)
        await dp._process_update(bot, ordinary(3))
        self.assertEqual(calls, [3])
        row = await (await self.db.conn.execute('SELECT status FROM telegram_update_inbox WHERE update_id=3')).fetchone()
        self.assertEqual(row[0], 'unknown')

    async def test_storage_failure_propagates_before_any_handler(self):
        calls = []
        async def feed(*args, **kwargs):
            calls.append(1)
        dp = self.dispatcher(feed)
        await self.db.close()
        with self.assertRaises(Exception):
            await dp._process_update(SimpleNamespace(id=999), ordinary(4))
        self.assertEqual(calls, [])

    async def test_callback_and_channel_selection_duplicates_are_guarded(self):
        updates = [Update.model_validate({'update_id': 5, 'callback_query': {
            'id': 'c', 'from': {'id': 101, 'is_bot': False, 'first_name': 'A'},
            'chat_instance': 'ci', 'data': 'notify:alpha'}}),
            Update.model_validate({'update_id': 6, 'message': {'message_id': 6,
            'date': 1700000000, 'chat': {'id': 101, 'type': 'private'},
            'chat_shared': {'request_id': 123, 'chat_id': -100123}}})]
        calls = []
        async def feed(bot, update, **kwargs):
            calls.append(update.update_id)
        for u in updates:
            for _ in range(2):
                await self.dispatcher(feed)._process_update(SimpleNamespace(id=999), u)
        self.assertEqual(calls, [5, 6])

    async def test_financial_crash_returns_to_durable_replay(self):
        # Build a real, validated financial Update without transport.
        payload = ordinary(7).model_dump(mode='json', exclude_none=True)
        payload['message'].pop('text')
        payload['message']['successful_payment'] = {'currency': 'XTR', 'total_amount': 1,
            'invoice_payload': 'old', 'telegram_payment_charge_id': 'charge', 'provider_payment_charge_id': ''}
        u = Update.model_validate(payload)
        calls = []
        async def feed(bot, update, **kwargs):
            calls.append(update.update_id)
            if len(calls) == 1:
                raise RuntimeError('local failure')
        dp = self.dispatcher(feed)
        bot = SimpleNamespace(id=999)
        with self.assertRaises(RuntimeError):
            await dp._process_update(bot, u)
        await dp.recover_pending(bot)
        await dp._process_update(bot, u)
        self.assertEqual(calls, [7, 7])

    async def test_next_update_is_journaled_while_previous_oauth_can_still_wait(self):
        from aiogram import Dispatcher
        async def incoming(*args, **kwargs):
            yield ordinary(8, '/auth_twitch')
            yield ordinary(9, 'Меню')
        async def feed(*args, **kwargs):
            await asyncio.Event().wait()
        dp = self.dispatcher(feed)
        with patch.object(Dispatcher, '_listen_updates', incoming):
            iterator = dp._listen_updates(SimpleNamespace(id=999))
            first = await anext(iterator)
            row = await (await self.db.conn.execute(
                'SELECT status FROM telegram_update_inbox WHERE update_id=8')).fetchone()
            self.assertEqual(row, ('received',))
            task = asyncio.create_task(dp._process_update(SimpleNamespace(id=999), first))
            try:
                second = await asyncio.wait_for(anext(iterator), 1)
                self.assertEqual(second.update_id, 9)
                row = await (await self.db.conn.execute(
                    'SELECT status FROM telegram_update_inbox WHERE update_id=9')).fetchone()
                self.assertEqual(row, ('received',))
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                await iterator.aclose()

    async def test_recovery_menu_can_cancel_an_inflight_oauth(self):
        await self.db.receive_telegram_update(999, 10, 'ordinary', ordinary(10, '/auth_twitch').model_dump_json())
        await self.db.receive_telegram_update(999, 11, 'ordinary', ordinary(11, 'Меню').model_dump_json())
        finished = asyncio.Event()
        calls = []
        async def feed(bot, update, **kwargs):
            calls.append(update.update_id)
            if update.update_id == 10:
                await finished.wait()
            else:
                finished.set()
        await asyncio.wait_for(self.dispatcher(feed).recover_pending(SimpleNamespace(id=999)), 2)
        self.assertEqual(calls, [10, 11])

    async def test_cancel_start_polling_stops_owned_polling_before_db_close(self):
        from aiogram import Dispatcher
        entered = asyncio.Event()
        stopped = asyncio.Event()
        async def polling(*args, **kwargs):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()
        async def feed(*args, **kwargs):
            pass
        dp = self.dispatcher(feed)
        with patch.object(Dispatcher, '_polling', polling):
            task = asyncio.create_task(dp.start_polling(SimpleNamespace(id=999),
                handle_signals=False, close_bot_session=False))
            await asyncio.wait_for(entered.wait(), 1)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            self.assertTrue(stopped.is_set())

    async def test_recovery_and_fresh_dispatch_share_one_handler_budget(self):
        release = asyncio.Event()
        active = 0
        peak = 0
        async def feed(*args, **kwargs):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            try:
                await release.wait()
            finally:
                active -= 1
        dp = self.dispatcher(feed)
        tasks = [asyncio.create_task(dp._process_update(SimpleNamespace(id=999), ordinary(i)))
                 for i in range(100, 164)]
        try:
            await asyncio.sleep(1)
            release.set()
            await asyncio.gather(*tasks)
            self.assertLessEqual(peak, 32)
            self.assertEqual(active, 0)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
