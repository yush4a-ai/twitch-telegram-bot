"""Рассылки владельца и переписка: схема, аудитория, отписки, состояния, чат."""
import os
import tempfile
import unittest

from bot.database import Database

NOW = 1_700_000_000.0
OWNER = 13


class BroadcastDatabaseCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Database(os.path.join(self.tmp.name, "bot.db"))
        await self.db.connect()
        for user_id in (10, 11, 12, OWNER):
            await self.db.conn.execute(
                "INSERT OR IGNORE INTO known_private_users(user_id) VALUES (?)", (user_id,))
        await self.db.conn.commit()

    async def asyncTearDown(self):
        await self.db.close()

    async def draft(self, **overrides):
        params = {
            "title": "Обновление", "body": "Привет", "created_by": OWNER, "now": NOW,
        }
        params.update(overrides)
        return await self.db.save_broadcast_campaign(**params)


class SchemaTests(BroadcastDatabaseCase):
    async def test_migration_is_recorded_and_tables_exist(self):
        cursor = await self.db.conn.execute(
            "SELECT 1 FROM schema_migrations WHERE version = ?",
            ("admin_003_broadcasts_and_dialogues",),
        )
        self.assertIsNotNone(await cursor.fetchone())
        for table in ("broadcast_campaigns", "broadcast_recipients",
                      "broadcast_optouts", "dialogue_messages", "dialogue_state"):
            cursor = await self.db.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name = ?", (table,))
            self.assertIsNotNone(await cursor.fetchone(), table)


class AudienceTests(BroadcastDatabaseCase):
    async def test_audience_excludes_owner_optouts_and_unreachable(self):
        await self.db.opt_out_broadcast(12, now=NOW, reason="button")
        audience = await self.db.broadcast_audience(owner_id=OWNER)
        self.assertEqual(audience["recipients"], [10, 11])
        self.assertEqual(audience["total"], 2)
        self.assertEqual(audience["opted_out"], 1)
        self.assertEqual(audience["owner"], 1)

    async def test_unreachable_person_drops_out_of_later_campaigns(self):
        campaign = await self.draft()
        await self.db.prepare_broadcast_recipients(campaign, [10, 11], now=NOW)
        await self.db.mark_broadcast_recipient(
            campaign, 10, "unreachable", now=NOW, error_code="forbidden")
        audience = await self.db.broadcast_audience(owner_id=OWNER)
        self.assertNotIn(10, audience["recipients"])
        self.assertEqual(audience["unreachable"], 1)

    async def test_optout_is_idempotent_and_restorable(self):
        await self.db.opt_out_broadcast(11, now=NOW, reason="button")
        await self.db.opt_out_broadcast(11, now=NOW + 5)
        self.assertTrue(await self.db.is_broadcast_opted_out(11))
        listed = await self.db.list_broadcast_optouts()
        self.assertEqual(len(listed), 1)
        self.assertTrue(await self.db.restore_broadcast_optout(11))
        self.assertFalse(await self.db.is_broadcast_opted_out(11))

    async def test_queueing_skips_people_who_opted_out_meanwhile(self):
        campaign = await self.draft()
        await self.db.opt_out_broadcast(11, now=NOW)
        # Между расчётом аудитории и постановкой в очередь человек успел отписаться.
        await self.db.prepare_broadcast_recipients(campaign, [10, 11, 12], now=NOW)
        self.assertEqual(await self.db.next_broadcast_recipients(campaign), [10, 12])

    async def test_optout_removes_person_from_a_pending_campaign(self):
        campaign = await self.draft()
        await self.db.prepare_broadcast_recipients(campaign, [10, 11], now=NOW)
        await self.db.opt_out_broadcast(10, now=NOW)
        self.assertEqual(await self.db.next_broadcast_recipients(campaign), [11])
        counts = await self.db.refresh_broadcast_counters(campaign)
        self.assertEqual(counts["opted_out"], 1)


class CampaignTests(BroadcastDatabaseCase):
    async def test_draft_can_be_edited_and_publishing_closes_editing(self):
        campaign = await self.draft()
        await self.db.save_broadcast_campaign(
            title="Другое", body="Текст", created_by=OWNER, now=NOW, campaign_id=campaign)
        stored = await self.db.get_broadcast_campaign(campaign)
        self.assertEqual(stored["title"], "Другое")
        await self.db.set_broadcast_state(campaign, "sending", now=NOW)
        with self.assertRaises(ValueError):
            await self.db.save_broadcast_campaign(
                title="Поздно", body="Текст", created_by=OWNER, now=NOW, campaign_id=campaign)

    async def test_request_key_prevents_a_second_campaign(self):
        campaign = await self.draft(request_key="key-1")
        self.assertEqual(
            await self.db.broadcast_campaign_by_request_key("key-1"), campaign)

    async def test_counters_and_stop_keep_what_was_already_sent(self):
        campaign = await self.draft()
        await self.db.prepare_broadcast_recipients(campaign, [10, 11, 12], now=NOW)
        await self.db.mark_broadcast_recipient(campaign, 10, "sent", now=NOW,
                                               telegram_message_id=555)
        await self.db.mark_broadcast_recipient(campaign, 11, "failed", now=NOW,
                                               error_code="retry_after")
        progress = await self.db.broadcast_progress(campaign)
        self.assertEqual(progress["sent"], 1)
        self.assertEqual(progress["failed"], 1)
        self.assertEqual(progress["pending"], 1)
        stopped = await self.db.stop_broadcast(campaign, now=NOW + 10)
        self.assertEqual(stopped, 1)
        after = await self.db.broadcast_progress(campaign)
        self.assertEqual(after["sent"], 1)
        self.assertEqual(after["stopped"], 1)
        self.assertEqual(after["pending"], 0)
        stored = await self.db.get_broadcast_campaign(campaign)
        self.assertEqual(stored["state"], "stopped")

    async def test_sending_campaigns_are_resumed_after_restart(self):
        campaign = await self.draft()
        await self.db.set_broadcast_state(campaign, "sending", now=NOW)
        self.assertEqual(await self.db.resume_broadcast_campaigns(), [campaign])

    async def test_repeated_prepare_does_not_reset_recipients(self):
        campaign = await self.draft()
        await self.db.prepare_broadcast_recipients(campaign, [10, 11], now=NOW)
        await self.db.mark_broadcast_recipient(campaign, 10, "sent", now=NOW)
        await self.db.prepare_broadcast_recipients(campaign, [10, 11], now=NOW + 1)
        progress = await self.db.broadcast_progress(campaign)
        self.assertEqual(progress["sent"], 1)
        self.assertEqual(progress["pending"], 1)


class DialogueTests(BroadcastDatabaseCase):
    async def test_incoming_message_creates_an_unread_dialogue(self):
        message_id = await self.db.record_dialogue_message(
            10, "in", "Здравствуйте", now=NOW, telegram_message_id=900)
        self.assertIsInstance(message_id, int)
        self.assertEqual(await self.db.dialogue_unread_total(), 1)
        dialogues = await self.db.list_dialogues()
        self.assertEqual(len(dialogues), 1)
        self.assertEqual(dialogues[0]["user_id"], 10)
        self.assertEqual(dialogues[0]["last_body"], "Здравствуйте")
        self.assertEqual(dialogues[0]["last_direction"], "in")

    async def test_owner_reply_does_not_add_unread(self):
        await self.db.record_dialogue_message(10, "in", "Вопрос", now=NOW)
        await self.db.mark_dialogue_read(10, now=NOW + 1)
        await self.db.record_dialogue_message(
            10, "out", "Ответ", now=NOW + 2, delivery="sent", request_key="reply-1")
        self.assertEqual(await self.db.dialogue_unread_total(), 0)
        history = await self.db.dialogue_history(10)
        self.assertEqual([row["direction"] for row in history], ["in", "out"])

    async def test_same_request_key_does_not_send_twice(self):
        await self.db.record_dialogue_message(
            10, "out", "Ответ", now=NOW, request_key="reply-1")
        again = await self.db.record_dialogue_message(
            10, "out", "Ответ", now=NOW + 1, request_key="reply-1")
        self.assertIsNone(again)
        self.assertEqual(len(await self.db.dialogue_history(10)), 1)

    async def test_delete_dialogue_removes_messages_and_reports_images(self):
        await self.db.record_dialogue_message(10, "in", "Текст", now=NOW)
        await self.db.record_dialogue_message(
            10, "in", "Картинка", now=NOW + 1, image_path="/data/media/one.png")
        images = await self.db.delete_dialogue(10)
        self.assertEqual(images, ["/data/media/one.png"])
        self.assertEqual(await self.db.dialogue_history(10), [])
        self.assertEqual(await self.db.dialogue_unread_total(), 0)

    async def test_storage_limit_purges_old_messages(self):
        await self.db.record_dialogue_message(10, "in", "Старое", now=NOW - 200 * 86400)
        await self.db.record_dialogue_message(10, "in", "Новое", now=NOW)
        removed = await self.db.purge_dialogue_messages(older_than=NOW - 180 * 86400)
        self.assertEqual(removed, 1)
        history = await self.db.dialogue_history(10)
        self.assertEqual([row["body"] for row in history], ["Новое"])

    async def test_search_finds_person_by_name_and_id(self):
        await self.db.remember_profile(
            10, username="alex", display_name="Алекс", language_code="ru", now=NOW)
        await self.db.record_dialogue_message(10, "in", "Привет", now=NOW)
        await self.db.record_dialogue_message(11, "in", "Другое", now=NOW)
        self.assertEqual(len(await self.db.list_dialogues(query="Алекс")), 1)
        self.assertEqual(len(await self.db.list_dialogues(query="10")), 1)
        self.assertEqual(len(await self.db.list_dialogues()), 2)


if __name__ == "__main__":
    unittest.main()
