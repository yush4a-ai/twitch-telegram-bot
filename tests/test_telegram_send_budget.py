"""Preview edits share the Telegram request start budget with normal signals."""

import asyncio
import unittest

from bot.telegram_send_budget import TelegramSendBudget


class TelegramSendBudgetTests(unittest.IsolatedAsyncioTestCase):
    async def test_waiting_notification_precedes_waiting_preview(self):
        budget = TelegramSendBudget(global_interval=0.04)
        order = []
        await budget.wait_turn(normal=True)

        async def wait(name, *, normal):
            await budget.wait_turn(normal=normal)
            order.append(name)

        preview = asyncio.create_task(wait("preview", normal=False))
        await asyncio.sleep(0)
        notification = asyncio.create_task(wait("notification", normal=True))
        await asyncio.gather(preview, notification)
        self.assertEqual(order, ["notification", "preview"])


if __name__ == "__main__":
    unittest.main()
