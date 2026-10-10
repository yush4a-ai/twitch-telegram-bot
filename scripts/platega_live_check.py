"""Живая проверка платёжного канала Platega: все способы и обе суммы.

Скрипт создаёт счета (не оплачивает их) и проверяет, что наш адаптер принимает
ответы провайдера: ссылку на оплату, сумму с комиссией, способ в статусе. Деньги
не двигаются, секреты не печатаются.

Запуск в контейнере сервиса:
    railway ssh --environment production --service worker \
        "python -m scripts.platega_live_check"
Локально: нужны PLATEGA_MERCHANT_ID и PLATEGA_SECRET в окружении.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass

import aiohttp

from bot.platega_provider import PlategaProvider, ProviderRequestRejected
from bot.platega_transport import PlategaHttpTransport

METHOD_IDS = {"sbp": 2, "bank_card": 11}
AMOUNTS = {"viewer_plus": 150, "streamer_plus": 300}
RETURN_URL = "https://worker-production-cee5.up.railway.app/app"


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""


def _order_id() -> str:
    return ("%032x" % (int(time.time() * 1000) & 0xFFFFFFFF))[-32:]


async def _create(session, merchant, secret, method, amount):
    order_id = _order_id()
    payload = {
        "paymentMethod": METHOD_IDS[method],
        "paymentDetails": {"amount": amount, "currency": "RUB"},
        "orderId": order_id,
        "return": RETURN_URL,
        "failedUrl": RETURN_URL,
        "description": "TwitchSignalBot live check",
        "payload": json.dumps({"order_id": order_id, "attempt_id": "a" * 32}, separators=(",", ":")),
        "metadata": {"userId": "425785231", "userName": "LiveCheck"},
    }
    async with session.post(
        "https://app.platega.io/transaction/process", json=payload,
        headers={"X-MerchantId": merchant, "X-Secret": secret,
                 "Content-Type": "application/json", "Accept": "application/json"},
    ) as response:
        body = await response.text()
        return response.status, body


async def _status(session, merchant, secret, reference):
    async with session.get(
        "https://app.platega.io/transaction/" + reference,
        headers={"X-MerchantId": merchant, "X-Secret": secret, "Accept": "application/json"},
    ) as response:
        return response.status, await response.json()


async def main() -> int:
    try:
        # Консоль контейнера бывает в CP1251: падать на выводе нельзя.
        sys.stdout.reconfigure(errors="replace")
    except Exception:  # noqa: BLE001
        pass
    merchant = os.environ.get("PLATEGA_MERCHANT_ID", "").strip()
    secret = os.environ.get("PLATEGA_SECRET", "").strip()
    if not merchant or not secret:
        print("SKIP: нет PLATEGA_MERCHANT_ID/PLATEGA_SECRET в окружении")
        return 2

    adapter = PlategaProvider.__new__(PlategaProvider)
    adapter._hosts = frozenset({"pay.platega.io"})
    adapter._secret = secret
    adapter._merchant = merchant
    adapter._status_methods = {"SBPQR": "sbp"}

    results: list[Check] = []
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
        for product, amount in AMOUNTS.items():
            for method in METHOD_IDS:
                label = f"{method} {amount} RUB"
                status, body = await _create(session, merchant, secret, method, amount)
                if status != 200:
                    try:
                        parsed = json.loads(body)
                    except ValueError:
                        parsed = {}
                    reason = parsed.get("message") or "отказ провайдера"
                    method_hint = ""
                    if isinstance(parsed.get("data"), list):
                        method_hint = ",".join(
                            str(item.get("key")) for item in parsed["data"] if isinstance(item, dict))
                    results.append(Check(
                        f"{label}: создание счёта", False,
                        f"http {status} {reason}" + (f" [{method_hint}]" if method_hint else "")))
                    continue
                created = json.loads(body)
                reference = created.get("transactionId")
                redirect = created.get("redirect") or created.get("url") or ""
                try:
                    adapter._hosted_url(redirect)
                    url_ok, url_detail = True, f"host {redirect.split('/')[2] if '//' in redirect else '?'}"
                except Exception as error:  # noqa: BLE001
                    url_ok, url_detail = False, type(error).__name__
                results.append(Check(f"{label}: создание счёта", bool(reference) and url_ok,
                                     f"tx {'да' if reference else 'нет'}, ссылка {'ок' if url_ok else url_detail}"))

                status_code, data = await _status(session, merchant, secret, reference)
                if status_code != 200 or not isinstance(data, dict):
                    results.append(Check(f"{label}: статус", False, f"http {status_code}"))
                    continue
                raw_method = data.get("paymentMethod")
                mapped = adapter._status_methods.get(raw_method)
                details = data.get("paymentDetails") or {}
                gross = details.get("amount")
                commission = data.get("comission") or 0
                net = None
                try:
                    net = round(float(gross) - float(commission), 2)
                except (TypeError, ValueError):
                    net = None
                results.append(Check(
                    f"{label}: способ в статусе", mapped == method,
                    f"провайдер {raw_method!r} -> наш {mapped!r}"))
                results.append(Check(
                    f"{label}: сумма без комиссии", net == float(amount),
                    f"списание {gross}, комиссия {commission}, заказу {net}"))

    failed = [check for check in results if not check.ok]
    for check in results:
        print(f"{'PASS' if check.ok else 'FAIL'} {check.name} :: {check.detail}")
    print(f"\nИтог: {len(results) - len(failed)}/{len(results)} проверок пройдено")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
