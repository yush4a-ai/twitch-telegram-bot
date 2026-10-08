"""Реальный HTTPS-транспорт Platega: один хост, без редиректов и без журналов.

Это единственное место в проекте, которое обращается к внешнему платёжному API.
Транспорт не знает про деньги: он доставляет запрос и возвращает ограниченный
ответ как есть. Заголовки с секретом приходят от адаптера, не попадают ни в URL,
ни в журнал, ни в текст исключения.
"""

from __future__ import annotations

from typing import Mapping

import aiohttp

from .billing_provider import ProviderHttpResponse

# Официальный базовый адрес API: https://docs.platega.io/ («Авторизация»).
PLATEGA_API_BASE = "https://app.platega.io"
_MAX_BODY = 65536
_MAX_TIMEOUT = 30.0
_PATH_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._~%/-"
)
_METHODS = frozenset({"GET", "POST"})


class PlategaHttpTransport:
    """Сетевой транспорт одного разрешённого хоста.

    ``network_free = False`` — честный признак того, что запрос уходит во внешнюю
    сеть. ``trusted_contract = True`` — объявление контракта, которое проверяет
    адаптер: без него денежные операции запрещены.
    """

    network_free = False
    trusted_contract = True

    def __init__(self, *, base_url: str = PLATEGA_API_BASE, session=None,
                 timeout: float = 5.0, max_body: int = _MAX_BODY) -> None:
        if base_url != PLATEGA_API_BASE:
            raise ValueError("invalid Platega API base URL")
        if type(max_body) is not int or not 1024 <= max_body <= 1024 * 1024:
            raise ValueError("invalid response body bound")
        if type(timeout) not in (int, float) or not 0 < timeout <= _MAX_TIMEOUT:
            raise ValueError("invalid request timeout")
        self._timeout = float(timeout)
        self._max_body = max_body
        self._owned = session is None
        self._session = session if session is not None else aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=self._timeout, connect=self._timeout),
        )

    @staticmethod
    def _target(path: str) -> str:
        if (not isinstance(path, str) or len(path) > 201
                or any(char not in _PATH_CHARS for char in path)
                or not path.startswith("/") or path.startswith("//") or ".." in path):
            raise ValueError("invalid provider path")
        return PLATEGA_API_BASE + path

    @staticmethod
    def _headers(headers: Mapping[str, str]) -> dict[str, str]:
        if not isinstance(headers, Mapping) or not headers:
            raise ValueError("invalid provider headers")
        clean: dict[str, str] = {}
        for key, value in headers.items():
            if (not isinstance(key, str) or not isinstance(value, str) or not key
                    or any(char in "\r\n\x00" for char in key + value)):
                raise ValueError("invalid provider headers")
            clean[key] = value
        return clean

    async def request(self, method: str, path: str, *, json: dict | None,
                      headers: Mapping[str, str], timeout: float) -> ProviderHttpResponse:
        if method not in _METHODS:
            raise ValueError("invalid provider method")
        if json is not None and not isinstance(json, dict):
            raise ValueError("invalid provider payload")
        if type(timeout) not in (int, float) or not 0 < timeout <= _MAX_TIMEOUT:
            raise ValueError("invalid provider timeout")
        url = self._target(path)
        clean = self._headers(headers)
        budget = min(float(timeout), self._timeout)
        # Редиректы запрещены: ответ 3xx не должен уводить запрос с секретом на
        # чужой хост, а ограничение тела не даёт вычитывать бесконечный ответ.
        async with self._session.request(
            method, url, json=json, headers=clean,
            timeout=aiohttp.ClientTimeout(total=budget, connect=budget),
            allow_redirects=False,
        ) as response:
            body = bytearray()
            async for chunk in response.content.iter_chunked(4096):
                body.extend(chunk)
                if len(body) > self._max_body:
                    break
            return ProviderHttpResponse(
                int(response.status), dict(response.headers), bytes(body),
            )

    async def close(self) -> None:
        """Закрывает только собственную сессию: чужую не трогаем."""
        if self._owned:
            await self._session.close()
