# R5 Mock Billing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans in this visible Work chat. Follow tasks sequentially; no separate implementation session.

**Goal:** Проверить тестовый checkout → verified payment → Streamer Plus, refund/cancel/expiry без денег и реального провайдера.

**Architecture:** Отдельный `PaymentProvider` protocol и `MockPaymentProvider` производят/проверяют тестовые события. `BillingService` управляет переходами через SQLite ledger и существующий R4 entitlement; raw redirect не меняет доступ. Ledger сохраняет идемпотентность после рестарта.

**Tech Stack:** Python 3.12, aiosqlite, стандартные `hmac`/`hashlib`/`json`, pytest/unittest, существующий Railway staging guard.

**Spec:** `docs/superpowers/specs/2026-10-01-r5-mock-billing-design.md`.

## Global Constraints

- Только локально и pinned Railway staging / `@TwitchSignalTestbot`; production `main`, variables, DB и deploy не меняются.
- Только provider=`mock`, currency=`TEST`, units=`1`; диапазон grant 60–2 678 400 секунд, pending checkout 900 секунд.
- `X-Mock-Timestamp` + `X-Mock-Signature` проверяются по `timestamp + "." + raw body`, окно ±300 секунд, constant-time HMAC-SHA256; body не более 4096 байт.
- Grant `source='mock'`, системный actor `0`; R4 test grants остаются независимыми.
- Отсутствуют реальные цены, деньги, платёжные provider adapters, публичный checkout/webhook endpoint и автообновление подписки.

## Review Focus

- Provider callback с верным HMAC, но чужим `order_id`: отказ без чужого grant (Task 3).
- Тот же provider event ID с иным валидно подписанным body: конфликт без повторного перехода (Task 3).
- Capture после cancel/expiry и refund до capture: отказ без payment/grant (Task 3).
- Два overlapping grants `test` и `mock`: refund отзывает только связанный mock grant, доступ тестового grant остаётся (Task 3).
- Ошибка после вставки payment, но до grant: transaction rollback сохраняет pending order и отсутствие payment/grant (Task 3).

---

### Task 1: Provider contract и подписанный mock

**Files:** Create `bot/billing_provider.py`; Test `tests/test_billing_provider.py`.

**Interfaces:** `CheckoutSession(order_id, reference)`, `VerifiedPaymentEvent(provider,event_id,order_id,payment_id,event_type,units,currency)`, `PaymentProvider` protocol; `MockPaymentProvider(secret: bytes)` реализует `async create_checkout(order_id,units,currency)`, `verify_webhook(body,headers,now)`, `async request_refund(payment_id,request_key)`, `async cancel_checkout(reference)`, `sign_test_event(event,now) -> (bytes,dict[str,str])`.

- [x] RED: checkout reference, independently signed capture/refund, bad/stale signature и schema/body size; отсутствие модуля подтверждено.
- [x] GREEN: typed contract, HMAC verifier и mock без сети; malformed event type дал отдельный RED→GREEN.
- [x] Focused suite `3 passed, 11 subtests`, без реальных URL/ключей; commit пакета.

### Task 2: Order/payment ledger и checkout

**Files:** Modify `bot/database.py`; Create `bot/billing.py`, `bot/billing_models.py`; Test `tests/test_billing_orders.py`; Modify `tests/test_growth_schema.py`.

**Interfaces:** `BillingOrder` и `PaymentRecord` typed dataclasses; `BillingService(db: Database, provider: PaymentProvider)` с `async create_checkout(telegram_user_id:int,request_key:str,duration_seconds:int,now:float|None=None)->CheckoutSession`; DB методы `create_billing_order`, `save_billing_checkout_reference`, `get_billing_order`, `cancel_billing_order`, `expire_pending_billing_orders` и миграция `r5_001_billing_ledger`.

- [x] RED: новая/старая DB migration, подтверждённая Telegram/Twitch связка, idempotent request key и конфликт owner/duration, provider failure/retry того же order ID, checkout без Plus, reopen сохраняет order.
- [x] GREEN: additive schema `billing_orders`, `billing_payments`, `billing_webhook_events`, `billing_audit`, уникальные ключи и индексы; checkout reference сохраняется после provider call.
- [x] RED/GREEN: pending cancel, 900-секундный expiry, чужой owner, provider cancel failure и audit failure rollback. Focused совместно с R4: 19 passed, 16 subtests; commit пакета.

### Task 3: Проверенное событие и entitlement

**Files:** Modify `bot/billing.py`, `bot/database.py`; Test `tests/test_billing_lifecycle.py`.

**Interfaces:** `BillingService.handle_webhook(body:bytes,headers:Mapping[str,str],now:float|None=None)->str`, `request_refund(telegram_user_id:int,order_id:str,request_key:str)->None`; DB `apply_verified_billing_event(event,body_sha256,now)->str` одной serialized транзакцией. Возвращается фактический order status.

- [ ] RED: capture выдаёт один `source='mock'` grant и audit; точный replay не удваивает; конфликт event ID/body, wrong amount/currency/order/payment, late capture и refund before capture отказывают.
- [ ] GREEN: event dedupe, payment transition и entitlement grant/revoke в одной write-транзакции; отдельные источники grant не трогать.
- [ ] RED/GREEN: refund запрос не отзывает grant до verified refund; повтор refund идемпотентен; expiry paid grant вычисляется по серверному времени; fault injection после payment доказывает rollback. Focused tests и commit.

### Task 4: Закрытый staging drill и acceptance

**Files:** Create `scripts/staging_mock_billing.py`, `tests/test_staging_mock_billing.py`; update `docs/STATUS.md`, `docs/DECISIONS.md`, `docs/audits/2026-10-01-r5-mock-billing-staging.md`.

**Interfaces:** CLI без сетевой продажи запускает end-to-end mock checkout/capture/refund/cancel/expiry на отдельной временной staging DB; проверяет pinned Railway project/environment/service/Volume IDs и `OWNER_CHAT_ID=425785231`, не пишет в активный `DB_PATH`.

- [ ] RED/GREEN: guard отклоняет production/wrong IDs/активный DB_PATH, drill не меняет staging user DB и проверяет события/доступ/rollback.
- [ ] Полный suite, code review, `git diff --check`, внешний online backup/restore, миграция на копии; commit snapshot и `python -m scripts.staging_deploy --deploy` только после проверки target=staging.
- [ ] Проверить terminal `SUCCESS`, active target, `/healthz`, закрытые API, testbot identity, `schema_versions`/`integrity_check`; запустить закрытый temp-DB drill. Записать реальные результаты и ограничения; затем двигаться к R6.

## Самопроверка плана

Все переходы из spec имеют тест в Task 2/3; Task 1 не зависит от DB, Task 3 использует точные контракты Task 1/2. Положительный grant начинается только после verified webhook. Staging drill не затрагивает активные пользовательские данные. Реальный provider и production исключены.
