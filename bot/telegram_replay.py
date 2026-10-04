"""Durable ingress; ordinary ambiguous effects are quarantined, never guessed."""
import asyncio
import signal
from contextlib import suppress
from aiogram import Dispatcher
from aiogram.dispatcher.dispatcher import DEFAULT_BACKOFF_CONFIG
from aiogram.dispatcher.event.bases import UNHANDLED
from aiogram.methods import TelegramMethod
from aiogram.types import Update


def update_kind(update):
    message = update.message
    if message is not None and (message.successful_payment or message.refunded_payment):
        return 'financial'
    return 'ordinary'


class ReplayDispatcher(Dispatcher):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._ingress_budget = asyncio.Semaphore(32)

    async def _listen_updates(self, bot, *args, **kwargs):
        async for update in super()._listen_updates(bot, *args, **kwargs):
            # This executes BEFORE yielding to aiogram, hence BEFORE its next offset ACK.
            await self.workflow_data['db'].receive_telegram_update(
                bot.id, update.update_id, update_kind(update), update.model_dump_json())
            yield update

    async def _process_update(self, bot, update, call_answer=True, **kwargs):
        async with self._ingress_budget:
            return await self._dispatch_durable_update(bot, update, call_answer, **kwargs)

    async def _dispatch_durable_update(self, bot, update, call_answer=True, **kwargs):
        db = self.workflow_data['db']
        kind = update_kind(update)
        await db.receive_telegram_update(bot.id, update.update_id, kind, update.model_dump_json())
        if not await db.claim_telegram_update(bot.id, update.update_id):
            return True
        outcome = {'failed': False}
        try:
            response = await self.feed_update(bot, update, ingress_outcome=outcome, **kwargs)
            if call_answer and isinstance(response, TelegramMethod):
                await bot(response)
        except BaseException:
            # Cancellation/crash leaves processing durable; recovery uses the same rule.
            await db.finish_telegram_update(bot.id, update.update_id,
                'received' if kind == 'financial' else 'unknown')
            raise
        await db.finish_telegram_update(bot.id, update.update_id,
            'unknown' if outcome['failed'] else 'done')
        return response is not UNHANDLED

    async def recover_pending(self, bot, *, prepared=False):
        db = self.workflow_data['db']
        if not prepared:
            await db.recover_telegram_updates(bot.id)
        semaphore = asyncio.Semaphore(32)
        tasks = set()
        async def process(update):
            try:
                return await self._process_update(bot, update)
            finally:
                semaphore.release()
        after_id = -1
        try:
            while True:
                payloads = await db.pending_telegram_updates(bot.id, limit=32, after_id=after_id)
                if not payloads:
                    break
                for payload in payloads:
                    update = Update.model_validate_json(payload)
                    after_id = update.update_id
                    await semaphore.acquire()
                    # Keep completed tasks until checked: no lost recovery exceptions.
                    done = {task for task in tasks if task.done()}
                    for task in done:
                        task.result()
                    tasks.difference_update(done)
                    tasks.add(asyncio.create_task(process(update)))
            if tasks:
                await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

    async def start_polling(self, *bots, **kwargs):
        if len(bots) != 1:
            raise ValueError('Single-writer ingress requires one bot')
        bot = bots[0]
        handle_signals = kwargs.pop('handle_signals', True)
        close_session = kwargs.pop('close_bot_session', True)
        kwargs.setdefault('allowed_updates', self.resolve_used_update_types())
        kwargs.setdefault('handle_as_tasks', True)
        kwargs.setdefault('tasks_concurrency_limit', 32)
        kwargs.setdefault('polling_timeout', 10)
        kwargs.setdefault('backoff_config', DEFAULT_BACKOFF_CONFIG)
        async with self._running_lock:
            self._stop_signal = asyncio.Event()
            self._stopped_signal = asyncio.Event()
            if handle_signals:
                loop = asyncio.get_running_loop()
                for sig in (signal.SIGINT, signal.SIGTERM):
                    with suppress(NotImplementedError):
                        loop.add_signal_handler(sig, self._signal_stop_polling, sig)
            data = {'dispatcher': self, 'bots': bots, **self.workflow_data, **kwargs}
            data.pop('bot', None)
            tasks = []
            await self.emit_startup(bot=bot, **data)
            try:
                await self.workflow_data['db'].recover_telegram_updates(bot.id)
                recovery = asyncio.create_task(self.recover_pending(bot, prepared=True))
                tasks = [asyncio.create_task(self._polling(bot=bot, **data)),
                         asyncio.create_task(self._stop_signal.wait()), recovery]
                waiting = set(tasks)
                while waiting:
                    done, waiting = await asyncio.wait(waiting, return_when=asyncio.FIRST_COMPLETED)
                    for task in done:
                        task.result()
                    if any(task is not recovery for task in done):
                        break
            finally:
                owned = tasks + list(self._handle_update_tasks)
                for task in owned:
                    task.cancel()
                if owned:
                    await asyncio.gather(*owned, return_exceptions=True)
                try:
                    await self.emit_shutdown(bot=bot, **data)
                finally:
                    if close_session:
                        await bot.session.close()
                    self._stopped_signal.set()
