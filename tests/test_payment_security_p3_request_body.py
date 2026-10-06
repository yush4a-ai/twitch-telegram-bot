"""C3 и C4: предел тела проверяется во время чтения, Mini App API требует JSON.

При chunked-запросе ``Content-Length`` неизвестен: раньше тело вычитывалось
целиком и только потом сравнивалось с лимитом. Теперь чтение прекращается на
первом превышении, а не-JSON тип тела отклоняется с 415.
"""

import asyncio
import hashlib
import hmac
import json
import os
import tempfile
import time
import unittest
from urllib.parse import urlencode

import aiohttp

from bot.database import Database
from bot.oauth import OAuthCallbackServer
from tests.test_admin_telegram_auth import BOT_TOKEN


def signed_identity(user, *, at=None):
    fields = {
        "user": user if isinstance(user, str) else json.dumps(user, separators=(",", ":")),
        "auth_date": str(int(time.time()) if at is None else at),
    }
    data = "\n".join(f"{key}={value}" for key, value in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, data.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


class MiniAppBodyLimitTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.db = Database(os.path.join(self.directory.name, "body.db"))
        await self.db.connect()
        self.addAsyncCleanup(self.db.close)
        self.server = OAuthCallbackServer(
            "https://example.test/twitch/callback", "127.0.0.1", 0,
            viewer_db=self.db, viewer_bot_token=BOT_TOKEN,
            mini_app_db=self.db, mini_app_bot_token=BOT_TOKEN,
        )
        await self.server.start()
        self.addAsyncCleanup(self.server.stop)
        self.session = aiohttp.ClientSession()
        self.addAsyncCleanup(self.session.close)
        self.base = f"http://127.0.0.1:{self.server._runner.addresses[0][1]}"

    async def test_mini_app_api_requires_json_content_type(self):
        init_data = signed_identity({"id": 101})
        body = json.dumps({"init_data": init_data})
        for content_type in ("text/plain", "application/x-www-form-urlencoded",
                             "application/octet-stream"):
            with self.subTest(content_type=content_type):
                async with self.session.post(
                    self.base + "/app/api/bootstrap", data=body,
                    headers={"Content-Type": content_type},
                ) as response:
                    self.assertEqual(response.status, 415)
        async with self.session.post(
            self.base + "/app/api/bootstrap", data=body,
            headers={"Content-Type": "application/json; charset=utf-8"},
        ) as response:
            self.assertEqual(response.status, 200)

    async def test_viewer_api_requires_json_content_type(self):
        body = json.dumps({"init_data": signed_identity({"id": 101})})
        async with self.session.post(
            self.base + "/viewer/api/state", data=body,
            headers={"Content-Type": "text/plain"},
        ) as response:
            self.assertEqual(response.status, 415)

    async def test_chunked_body_is_refused_before_the_stream_is_read_to_the_end(self):
        stream_finished = asyncio.Event()

        async def oversized_stream():
            yield b'{"init_data":"' + b"A" * 9000
            # Если сервер проверяет лимит только после полного чтения, он будет
            # ждать этот хвост и ответит лишь после него.
            await asyncio.sleep(2.5)
            stream_finished.set()
            yield b'"}'

        timeout = aiohttp.ClientTimeout(total=20, sock_read=15)
        async with self.session.post(
            self.base + "/app/api/bootstrap", data=oversized_stream(), timeout=timeout,
            headers={"Content-Type": "application/json"},
        ) as response:
            self.assertEqual(response.status, 413)
        self.assertFalse(
            stream_finished.is_set(),
            "тело дочитано до конца: предел проверяется после чтения, а не во время него",
        )

    async def test_viewer_api_refuses_an_oversized_chunked_body(self):
        async def oversized_stream():
            yield b'{"init_data":"' + b"B" * 9000
            await asyncio.sleep(0.01)
            yield b'"}'

        timeout = aiohttp.ClientTimeout(total=20, sock_read=15)
        async with self.session.post(
            self.base + "/viewer/api/state", data=oversized_stream(), timeout=timeout,
            headers={"Content-Type": "application/json"},
        ) as response:
            self.assertEqual(response.status, 413)

    async def test_small_chunked_body_still_reaches_authentication(self):
        async def small_stream():
            yield json.dumps({"init_data": signed_identity({"id": 101})}).encode()

        timeout = aiohttp.ClientTimeout(total=20, sock_read=15)
        async with self.session.post(
            self.base + "/app/api/bootstrap", data=small_stream(), timeout=timeout,
            headers={"Content-Type": "application/json"},
        ) as response:
            self.assertEqual(response.status, 200)


if __name__ == "__main__":
    unittest.main()
