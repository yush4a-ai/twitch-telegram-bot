"""Маршруты рассылок и чата: доступ, защита записи, отправка и переписка."""
import os
import tempfile
import unittest

import aiohttp

from bot.admin_auth import AdminAccess
from bot.database import Database
from bot.oauth import OAuthCallbackServer

KEY = "staging-test-key-with-at-least-32-chars-123"
# Выдуманный владелец: настоящие идентификаторы в репозиторий не попадают.
OWNER = 777001
PEOPLE = (777010, 777011, 777012)


class AdminBroadcastsApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Database(os.path.join(self.tmp.name, "bot.db"))
        await self.db.connect()
        for user_id in (*PEOPLE, OWNER):
            await self.db.conn.execute(
                "INSERT OR IGNORE INTO known_private_users(user_id) VALUES (?)", (user_id,))
        await self.db.conn.commit()
        self.sent: list[tuple[int, str, str | None]] = []

        async def chat_sender(user_id: int, text: str, image_path: str | None = None) -> bool:
            self.sent.append((user_id, text, image_path))
            return True

        self.chat_sender = chat_sender

        self.server = OAuthCallbackServer(
            "https://example.test/twitch/callback", "127.0.0.1", 0,
            admin_access=AdminAccess(
                KEY, enabled=True, secure_cookie=False, owner_id=OWNER),
        )

        async def snapshot() -> dict:
            return {"environment": "local"}

        self.server.set_admin_snapshot_provider(snapshot)
        self.server.set_admin_services(database=self.db, chat_sender=chat_sender)
        await self.server.start()
        self.session = aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True))
        port = self.server._runner.addresses[0][1]
        self.base = f"http://127.0.0.1:{port}"

    async def asyncTearDown(self):
        await self.session.close()
        await self.server.stop()
        await self.db.close()

    async def login(self):
        async with self.session.post(
            self.base + "/admin/emergency/login", data={"access_key": KEY},
            allow_redirects=False,
        ):
            pass

    async def csrf(self):
        async with self.session.get(self.base + "/admin/api/snapshot") as response:
            return (await response.json())["csrf"]

    def headers(self, token):
        return {"X-Admin-CSRF": token, "Origin": self.base}

    async def draft(self, token, **overrides):
        body = {"request_key": "campaign-1", "title": "Обновление",
                "body": "Привет, это рассылка"}
        body.update(overrides)
        return await self.session.post(
            self.base + "/admin/api/broadcasts", json=body, headers=self.headers(token))

    async def test_write_without_configured_owner_explains_the_reason(self):
        await self.login()
        self.server._admin_access.owner_id = None
        try:
            async with self.session.post(
                self.base + "/admin/api/broadcasts",
                json={"request_key": "k", "title": "T", "body": "B"},
                headers=self.headers(await self.csrf()),
            ) as response:
                self.assertEqual(response.status, 403)
                # Владелец должен понимать, что дело в настройке, а не в правах.
                self.assertEqual(
                    (await response.json())["error"], "owner_not_configured")
        finally:
            self.server._admin_access.owner_id = OWNER

    async def test_broadcast_and_chat_routes_require_a_session(self):
        for path in ("/admin/api/broadcasts", "/admin/api/dialogues",
                     "/admin/api/dialogues/777010"):
            async with self.session.get(self.base + path) as response:
                self.assertEqual(response.status, 401, path)

    async def test_writes_require_csrf_mark(self):
        await self.login()
        async with self.session.post(
            self.base + "/admin/api/broadcasts",
            json={"request_key": "k", "title": "T", "body": "B"},
            headers={"Origin": self.base},
        ) as response:
            self.assertEqual(response.status, 403)

    async def test_audience_excludes_owner_and_opted_out(self):
        await self.login()
        await self.db.opt_out_broadcast(777012, now=1_700_000_000.0)
        async with self.session.get(self.base + "/admin/api/broadcasts") as response:
            payload = await response.json()
        self.assertEqual(payload["audience"]["total"], 2)
        self.assertEqual(payload["audience"]["opted_out"], 1)
        self.assertEqual(payload["audience"]["owner"], 1)
        self.assertNotIn("recipients", payload["audience"])

    async def test_draft_then_send_queues_recipients(self):
        await self.login()
        token = await self.csrf()
        async with await self.draft(token) as response:
            self.assertEqual(response.status, 200)
            campaign_id = (await response.json())["campaign_id"]
        async with self.session.post(
            self.base + f"/admin/api/broadcasts/{campaign_id}/send",
            json={"request_key": "send-1"}, headers=self.headers(await self.csrf()),
        ) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual((await response.json())["queued"], 3)
        async with self.session.get(self.base + "/admin/api/broadcasts") as response:
            campaigns = (await response.json())["campaigns"]
        self.assertEqual(campaigns[0]["state"], "sending")
        self.assertEqual(campaigns[0]["audience_total"], 3)

    async def test_sending_twice_is_a_conflict(self):
        await self.login()
        async with await self.draft(await self.csrf()) as response:
            campaign_id = (await response.json())["campaign_id"]
        async with self.session.post(
            self.base + f"/admin/api/broadcasts/{campaign_id}/send",
            json={"request_key": "send-1"}, headers=self.headers(await self.csrf()),
        ) as response:
            self.assertEqual(response.status, 200)
        async with self.session.post(
            self.base + f"/admin/api/broadcasts/{campaign_id}/send",
            json={"request_key": "send-2"}, headers=self.headers(await self.csrf()),
        ) as response:
            self.assertEqual(response.status, 409)

    async def test_stop_marks_the_rest_as_stopped(self):
        await self.login()
        async with await self.draft(await self.csrf()) as response:
            campaign_id = (await response.json())["campaign_id"]
        async with self.session.post(
            self.base + f"/admin/api/broadcasts/{campaign_id}/send",
            json={"request_key": "send-1"}, headers=self.headers(await self.csrf()),
        ):
            pass
        async with self.session.post(
            self.base + f"/admin/api/broadcasts/{campaign_id}/stop",
            json={}, headers=self.headers(await self.csrf()),
        ) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual((await response.json())["stopped"], 3)

    async def test_repeat_request_key_does_not_create_a_second_campaign(self):
        await self.login()
        token = await self.csrf()
        async with await self.draft(token) as response:
            first = (await response.json())["campaign_id"]
        async with await self.draft(await self.csrf()) as response:
            payload = await response.json()
        self.assertEqual(payload["campaign_id"], first)
        self.assertTrue(payload.get("repeat"))
        async with self.session.get(self.base + "/admin/api/broadcasts") as response:
            campaigns = (await response.json())["campaigns"]
        self.assertEqual(len(campaigns), 1)

    async def test_button_requires_https_link(self):
        await self.login()
        async with await self.draft(
            await self.csrf(), button_text="Открыть", button_url="http://insecure.test"
        ) as response:
            self.assertEqual(response.status, 400)

    async def test_chat_lists_history_replies_and_deletes(self):
        await self.db.remember_profile(
            777010, username="alex", display_name="Алекс", language_code="ru",
            now=1_700_000_000.0)
        await self.db.record_dialogue_message(
            777010, "in", "Здравствуйте", now=1_700_000_000.0)
        await self.login()
        async with self.session.get(self.base + "/admin/api/dialogues") as response:
            payload = await response.json()
        self.assertEqual(payload["unread"], 1)
        self.assertEqual(payload["dialogues"][0]["display_name"], "Алекс")

        async with self.session.get(self.base + "/admin/api/dialogues/777010") as response:
            history = (await response.json())["messages"]
        self.assertEqual([row["direction"] for row in history], ["in"])

        async with self.session.post(
            self.base + "/admin/api/dialogues/777010/read",
            json={}, headers=self.headers(await self.csrf()),
        ) as response:
            self.assertEqual(response.status, 200)
        self.assertEqual(await self.db.dialogue_unread_total(), 0)

        async with self.session.post(
            self.base + "/admin/api/dialogues/777010/reply",
            json={"request_key": "reply-1", "body": "Ответ владельца"},
            headers=self.headers(await self.csrf()),
        ) as response:
            self.assertEqual(response.status, 200)
            self.assertTrue((await response.json())["delivered"])
        self.assertEqual(self.sent, [(777010, "Ответ владельца", None)])
        self.assertEqual(await self.db.dialogue_unread_total(), 0)

        # Повтор с тем же ключом не отправляет второе сообщение.
        async with self.session.post(
            self.base + "/admin/api/dialogues/777010/reply",
            json={"request_key": "reply-1", "body": "Ответ владельца"},
            headers=self.headers(await self.csrf()),
        ) as response:
            self.assertTrue((await response.json())["repeat"])
        self.assertEqual(len(self.sent), 1)

        async with self.session.post(
            self.base + "/admin/api/dialogues/777010/delete",
            json={}, headers=self.headers(await self.csrf()),
        ) as response:
            self.assertEqual(response.status, 200)
        self.assertEqual(await self.db.dialogue_history(777010), [])

    async def test_reply_without_sender_is_unavailable(self):
        await self.login()
        self.server.set_admin_services(database=self.db, chat_sender=None)
        async with self.session.post(
            self.base + "/admin/api/dialogues/777010/reply",
            json={"request_key": "reply-2", "body": "Ответ"},
            headers=self.headers(await self.csrf()),
        ) as response:
            self.assertEqual(response.status, 503)

    async def test_campaign_image_is_checked_by_content(self):
        await self.login()
        async with await self.draft(await self.csrf()) as response:
            campaign_id = (await response.json())["campaign_id"]
        media = os.path.join(self.tmp.name, "media")
        self.server.set_admin_services(database=self.db, chat_sender=None, media_dir=media)

        png = b"\x89PNG\r\n\x1a\n" + b"0" * 64
        form = aiohttp.FormData()
        form.add_field("image", png, filename="shot.png", content_type="image/png")
        async with self.session.post(
            self.base + f"/admin/api/broadcasts/{campaign_id}/image",
            data=form, headers=self.headers(await self.csrf()),
        ) as response:
            self.assertEqual(response.status, 200)
        stored = await self.db.get_broadcast_campaign(campaign_id)
        self.assertTrue(stored["image_path"])
        self.assertTrue(os.path.isfile(stored["image_path"]))

        # Мусор не принимается, даже если назван картинкой.
        form = aiohttp.FormData()
        form.add_field("image", b"not an image", filename="x.png", content_type="image/png")
        async with self.session.post(
            self.base + f"/admin/api/broadcasts/{campaign_id}/image",
            data=form, headers=self.headers(await self.csrf()),
        ) as response:
            self.assertEqual(response.status, 400)

    async def test_reply_with_image_is_stored_and_sent(self):
        await self.login()
        media = os.path.join(self.tmp.name, "media")
        self.server.set_admin_services(
            database=self.db, chat_sender=self.chat_sender, media_dir=media)

        png = b"\x89PNG\r\n\x1a\n" + b"0" * 32
        form = aiohttp.FormData()
        form.add_field("request_key", "reply-img-1")
        form.add_field("body", "Смотри")
        form.add_field("image", png, filename="s.png", content_type="image/png")
        async with self.session.post(
            self.base + "/admin/api/dialogues/777010/reply",
            data=form, headers=self.headers(await self.csrf()),
        ) as response:
            self.assertEqual(response.status, 200)
            self.assertTrue((await response.json())["delivered"])
        self.assertEqual(len(self.sent), 1)
        sent_path = self.sent[0][2]
        self.assertTrue(sent_path and os.path.isfile(sent_path))

        history = await self.db.dialogue_history(777010)
        self.assertEqual(history[-1]["image_path"], sent_path)
        self.assertEqual(history[-1]["body"], "Смотри")

        # Повтор с тем же ключом не отправляет второе сообщение.
        form = aiohttp.FormData()
        form.add_field("request_key", "reply-img-1")
        form.add_field("body", "Смотри")
        form.add_field("image", png, filename="s.png", content_type="image/png")
        async with self.session.post(
            self.base + "/admin/api/dialogues/777010/reply",
            data=form, headers=self.headers(await self.csrf()),
        ) as response:
            self.assertTrue((await response.json())["repeat"])
        self.assertEqual(len(self.sent), 1)

    async def test_reply_with_junk_instead_of_image_is_rejected(self):
        await self.login()
        media = os.path.join(self.tmp.name, "media")
        self.server.set_admin_services(
            database=self.db, chat_sender=self.chat_sender, media_dir=media)
        form = aiohttp.FormData()
        form.add_field("request_key", "reply-bad-1")
        form.add_field("body", "Текст")
        form.add_field("image", b"not an image", filename="x.png", content_type="image/png")
        async with self.session.post(
            self.base + "/admin/api/dialogues/777010/reply",
            data=form, headers=self.headers(await self.csrf()),
        ) as response:
            self.assertEqual(response.status, 400)
        self.assertEqual(self.sent, [])

    async def test_button_url_is_validated_even_without_a_text(self):
        await self.login()
        async with await self.draft(
            await self.csrf(), button_url="ftp://insecure.test"
        ) as response:
            self.assertEqual(response.status, 400)

    async def test_strange_campaign_id_is_rejected_not_crashing(self):
        await self.login()
        # «²» проходит isdigit(), но не является числом.
        async with self.session.post(
            self.base + "/admin/api/broadcasts/%C2%B2/send",
            json={"request_key": "k"}, headers=self.headers(await self.csrf()),
        ) as response:
            self.assertEqual(response.status, 400)

    async def test_dialogue_history_hides_disk_paths(self):
        await self.db.record_dialogue_message(
            777010, "out", "С картинкой", now=1_700_000_000.0,
            image_path="/data/media/chat-abc.png")
        await self.login()
        async with self.session.get(self.base + "/admin/api/dialogues/777010") as response:
            messages = (await response.json())["messages"]
        self.assertEqual(messages[-1]["image_name"], "chat-abc.png")
        self.assertNotIn("image_path", messages[-1])

    async def test_media_route_serves_only_own_files(self):
        media = os.path.join(self.tmp.name, "media")
        os.makedirs(media, exist_ok=True)
        with open(os.path.join(media, "chat-abc.png"), "wb") as handle:
            handle.write(b"\x89PNG\r\n\x1a\n" + b"0" * 32)
        self.server.set_admin_services(
            database=self.db, chat_sender=self.chat_sender, media_dir=media)

        async with aiohttp.ClientSession() as anonymous:
            async with anonymous.get(self.base + "/admin/api/media/chat-abc.png") as response:
                self.assertEqual(response.status, 401)

        await self.login()
        async with self.session.get(self.base + "/admin/api/media/chat-abc.png") as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(response.headers.get("Content-Type"), "image/png")

        async with self.session.get(self.base + "/admin/api/media/panel.js") as response:
            self.assertEqual(response.status, 404)

    async def test_deleting_a_dialogue_removes_its_files(self):
        media = os.path.join(self.tmp.name, "media")
        os.makedirs(media, exist_ok=True)
        picture = os.path.join(media, "chat-abc.png")
        with open(picture, "wb") as handle:
            handle.write(b"\x89PNG\r\n\x1a\n" + b"0" * 32)
        await self.db.record_dialogue_message(
            777010, "out", "С картинкой", now=1_700_000_000.0, image_path=picture)
        await self.login()
        self.server.set_admin_services(
            database=self.db, chat_sender=self.chat_sender, media_dir=media)

        async with self.session.post(
            self.base + "/admin/api/dialogues/777010/delete",
            json={}, headers=self.headers(await self.csrf()),
        ) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual((await response.json())["images"], 1)
        self.assertFalse(os.path.exists(picture))

    async def test_optout_can_be_restored_by_owner(self):
        await self.login()
        await self.db.opt_out_broadcast(777011, now=1_700_000_000.0)
        async with self.session.post(
            self.base + "/admin/api/optouts/777011/restore",
            json={}, headers=self.headers(await self.csrf()),
        ) as response:
            self.assertEqual(response.status, 200)
            self.assertTrue((await response.json())["restored"])
        self.assertFalse(await self.db.is_broadcast_opted_out(777011))


if __name__ == "__main__":
    unittest.main()
