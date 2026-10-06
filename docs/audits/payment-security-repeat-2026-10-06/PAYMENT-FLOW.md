# Карта платёжной системы (состояние на HEAD `cb2e021`, 06.10.2026)

Собрано независимым чтением кода: импорты, регистрации маршрутов, воркеры, схема БД.
Это фактическая карта, а не пересказ прошлых отчётов.

## 1. Цепочка целиком

```
Telegram Bot (aiogram, long polling)                Telegram Mini App (WebApp, aiohttp)
        │                                                     │
        │ callback plus:buy / plus:pay                        │ POST /app/api/purchase/prepare
        ▼                                                     ▼
bot/handlers/telegram_plus.py:cb_buy / cb_payment_method     bot/mini_app_billing.py:prepare
        │                                                     │
        └──────────────► bot/billing.py: BillingService.prepare_payment ◄──────────┘
                                   │
                                   │ 1. проверки политики и условий
                                   │ 2. _cancel_stale_unresolved (закрыть неоплаченные)
                                   │ 3. BillingStore.create_order (заказ + попытка в одной транзакции)
                                   ▼
                       bot/billing_store.py: order + attempt (durable)
                                   │
                                   │ 4. провайдер создаёт счёт
              ┌────────────────────┴────────────────────┐
              ▼                                         ▼
  bot/stars_provider.py                     bot/platega_provider.py
  create_payment() → sendInvoice (бот)      create_payment() → внешний POST
  create_link_payment() → createInvoiceLink (мини-апп)
              │                                         │
              ▼                                         ▼
  Telegram pre_checkout_query → ответ бота      Platega callback (HTTP)
  Telegram successful_payment → handler          bot/payment_web.py (условно)
              │                                         │
              └────────────────────┬────────────────────┘
                                   ▼
                 VerifiedPaymentEvidence (провайдерские проверки)
                                   │
                                   ▼
        bot/billing.py: apply_payment_evidence (одна транзакция)
                                   │
        ┌──────────────────────────┼───────────────────────────┐
        ▼                          ▼                           ▼
 billing_provider_facts     billing_orders → paid      entitlement_grants (source='paid')
        │                          │                           │
        └──────────────────────────┴───────────────────────────┘
                                   ▼
                 Доступ: has_viewer_plus / has_streamer_plus /
                 effective_viewer_predicate (bot/entitlements.py)
                                   │
        ┌──────────────────────────┼───────────────────────────┐
        ▼                          ▼                           ▼
   Mini App (capabilities)   Бот (тариф/статус)         Превью, лимиты, фильтры
                                   │
                                   ▼
        Истечение (expires_at) · Возврат (refunded_payment) · Отзыв владельцем
```

## 2. Точки входа

| Точка входа | Файл | Что делает |
|---|---|---|
| Кнопка «Оформить» в боте | `bot/handlers/telegram_plus.py:168` (`cb_buy`) | выбор способа оплаты |
| Кнопка способа в боте | `bot/handlers/telegram_plus.py:186` (`cb_payment_method`) | Stars → `prepare_payment(..., prefer_link=False)` |
| Подготовка в мини-аппе | `bot/mini_app_billing.py:56` (`prepare`) | `prepare_payment(..., prefer_link=True)` |
| Статус заказа в мини-аппе | `bot/mini_app_billing.py:95` (`purchase_state`) | сверяет владельца заказа |
| Каталог тарифов | `bot/mini_app_billing.py:116` (`catalog`) | готовность способов по политике |
| Состояние подписки | `bot/mini_app_billing.py:108` (`state`) | свои гранты |
| Telegram pre-checkout | `bot/handlers/payments.py:41` | подтверждение/отказ заказа |
| Telegram successful/refunded | `bot/handlers/payments.py:77,81` | применяет платёж/возврат |
| Platega callback | `bot/payment_web.py:10` | монтируется только при `local_contract_enabled` |
| Возврат по запросу | `bot/billing.py:402` (`request_payment_refund`) | требует `merchant_actor_ids` (в проде пуст) |
| Выдача владельцем | `bot/admin_web.py` (`/admin/api/access/grant|extend|revoke`) | ручные гранты |

## 3. Фоновые воркеры, влияющие на деньги

| Воркер | Где | Что делает |
|---|---|---|
| Сверка оплат звёздами | `main.py:973` → `_run_billing_reconcile` → `bot/billing.py:243` | раз в 5 минут читает `getStarTransactions` и применяет потерянные оплаты |
| Очередь уведомлений | `main.py:893` → `bot/notification_worker.py` | доставка, не деньги |
| Поллер | `main.py:916` | наблюдения, превью, уведомления |
| Бэкапы | `main.py:922` | копии БД |

## 4. Таблицы (billing/entitlement)

| Таблица | Назначение | Ключевые ограничения |
|---|---|---|
| `billing_orders` | заказ: продукт, провайдер, метод, сумма, валюта, условия, ссылка, grant_id | `request_key UNIQUE`, `grant_id UNIQUE`, частичный уникальный индекс «один активный заказ покупателя» |
| `billing_payment_attempts` | попытка платежа и её состояние | `provider_reference` хранит идентификатор списания |
| `billing_provider_facts` | подтверждённые факты оплаты | PK `(provider, transaction_id)` |
| `billing_provider_inbox` | входящие события провайдера | PK `(provider, event_key)` |
| `billing_payment_refunds` | возвраты | частичный уникальный индекс на активный возврат |
| `billing_provider_quarantine` | подозрительные события | — |
| `billing_audit` | журнал действий по заказу | — |
| `entitlement_grants` | выданные права | `request_key UNIQUE` (`paid-order:<order_id>`) |
| `entitlement_events` | журнал прав | — |
| `viewer_test_trials` | пробный доступ | PK по пользователю |

Схема: `bot/billing_migrations.py`, миграции интерфейса — `bot/database.py`.

## 5. Действующий режим (проверено на production 06.10.2026)

| Параметр | Значение | Смысл |
|---|---|---|
| `BILLING_MODE` | `sandbox` | денежные операции разрешены, внешний провайдер Platega не создаётся |
| `BILLING_TARGET_VERIFIED` | `1` | получатель платежей подтверждён |
| `BILLING_ALLOW_INVOICE` | `1` | счета звёздами разрешены |
| `BILLING_PERIOD_APPROVED` / `BILLING_REFUND_APPROVED` | `1` | условия периода и возврата утверждены |
| `BILLING_PUBLIC_STARS` | `1` | публичная оплата звёздами открыта владельцем |
| `BILLING_ALLOW_EXTERNAL` | не задан (0) | СБП и карта закрыты |
| `PRODUCTION_PAYMENT_POLICY` (контракт допуска) | `off` | поле контракта не ограничивает `BILLING_*` |

Фактически: **звёзды включены, СБП и карта выключены**.
Наличие ключей провайдера само по себе ничего не включает: `checkout_readiness` и `_require_ready` требуют полного набора флагов (`bot/plan_catalog.py:111`, `bot/stars_provider.py:55`).

## 6. Что произойдёт, если появятся «лишние» секреты

- Появление `PLATEGA_API_KEY`/секрета само по себе не открывает СБП/карту: нужен `BILLING_ALLOW_EXTERNAL=1` и создание провайдера Platega (`bot/config.py:28`, `bot/billing.py:105-110`).
- Появление Stars-конфига без `BILLING_PUBLIC_STARS` не открывает публичную оплату: `network_free` требует флаг (`bot/stars_provider.py:38-53`), а витрина показывает недоступность (`bot/plan_catalog.py:123-127`).
- Единственный путь «включить деньги» — явные переменные владельца плюс (для staging-ручек) привязка к pinned staging (`bot/config.py:435-453`).

## 7. Границы карты

- Реальный Platega не подключён: адаптер есть, транспорт не монтируется в проде (`bot/payment_web.py:10-12`).
- Реальный возврат звёзд инициируется вручную: `request_payment_refund` недоступен (пустой `merchant_actor_ids`, `main.py:693-697`).
