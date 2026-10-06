# Перепроверка прошлых находок (на HEAD `cb2e021`)

Правило перепроверки: прошлые отчёты — только исторический контекст. Каждый пункт ниже
проверен заново по текущему коду и/или тестами. Статусы: CONFIRMED FIXED / REGRESSED /
PARTIALLY FIXED / NOT REPRODUCIBLE / NOT TESTED.

## 1. Из аудита готовности к production (`docs/audits/production-readiness-2026-10-04`)

| ID | Прошлая находка | Что проверено сейчас | Доказательство | Статус |
|---|---|---|---|---|
| SEC-01 | Бывший администратор мог завершить ранее открытое добавление стримера в группу/канал | Подключение идёт через durable intent: при возобновлении сверяется владелец намерения и срок | `bot/handlers/telegram_streamer.py:145-146` (`row[1]==callback.from_user.id`, `row[6]=='pending'`, `row[4]>time.time()`), `:163-165` | CONFIRMED FIXED |
| SEC-02 | Mini App follow обращался к Twitch API без ограничителя до проверки лимита списка | Ограничитель стоит до внешнего обращения: сначала лимит каналов, затем `admit`, только потом lookup | `bot/mini_app_viewer.py:503-517` (`admit` на 509, `exists` на 514) | CONFIRMED FIXED |
| SEC-03 | Публичные кабинеты вытесняли чужие Login Widget states/сессии | Пул не вытесняет чужие записи: при заполнении возвращается отказ, добавлен лимит на источник и лимит активных состояний на клиента | `bot/login_states.py:30-38` (`_allow_rate`), `:52-59` (`return None`), `bot/admin_auth.py:42,81-82`, `bot/streamer_auth.py:27,73-74` | CONFIRMED FIXED |
| Writer exclusivity | Вторая реплика не должна писать в БД | Эксклюзивная блокировка файла тома, захват при старте, освобождение в `finally` | `bot/production_admission.py:150-171`, `main.py:653-657`, `:1048-1049` | CONFIRMED FIXED |
| Admission fail-closed | При несоответствии контракта прод не должен стартовать | Допуск требует точных значений: `replica_count=1`, `writer_policy=exclusive_lock`, `queue_policy=lease_fenced_v1`, `payment_policy=off`, сверка `RAILWAY_*`, путей и `PUBLIC_URL` | `bot/production_admission.py:87-115` | CONFIRMED FIXED |
| XFF/Forwarded trust | Нельзя доверять заголовкам при определении клиента | Заголовки не читаются вовсе: клиент берётся из соединения | поиск `X-Forwarded-For`/`X-Real-IP` по `bot/**` — совпадений нет; `bot/admin_web.py:148,213,228,243` используют `request.remote` | NOT REPRODUCIBLE (риск отсутствует по построению) |
| Mini App API budget abuse | Повторные запросы не должны тратить общий бюджет Twitch | См. SEC-02 плюс лимиты на остальных маршрутах мини-аппа | `bot/mini_app_viewer.py:365,476,510` | CONFIRMED FIXED |
| Деньги «не готовы» (раздел 3 отчёта) | Публичная покупка возвращала недоступность, транспорты провайдеров не подключены | На 06.10.2026 звёзды включены владельцем и проверены живой покупкой; СБП/карта по-прежнему закрыты (транспорт Platega в прод не монтируется) | `docs/STATUS.md` (оплата звёздами), `bot/payment_web.py:10-12`, переменные production | PARTIALLY FIXED (по решению владельца: звёзды да, Platega нет) |

## 2. Из предыдущего аудита оплаты (три проверки 06.10.2026)

| ID | Находка | Что проверено сейчас | Доказательство | Статус |
|---|---|---|---|---|
| K1 | Неоплаченный счёт навсегда блокировал следующие покупки | Заказ закрывается по истечении счёта; заказы с сохранённым идентификатором платежа не трогаются | `bot/billing.py:98-147` (`checkout_expires_at <= now`, `provider_reference IS NULL`), тест `tests/test_stars_exploits.py::test_unpaid_expired_invoice_stops_blocking_the_next_purchase` | CONFIRMED FIXED |
| Д2 | Оплата по закрытому заказу не восстанавливалась — деньги без доступа | Сверка принимает и закрытые заказы, если совпали payload, сумма и гранта ещё нет | `bot/billing.py:243-281`, тест `::test_payment_for_an_expired_order_is_still_granted` | CONFIRMED FIXED |
| V1 | Возврат не отзывал доступ при изменившейся политике/версии периода | Отзыв больше не зависит от текущей политики: отзывается грант заказа | `bot/billing.py:354-361` | CONFIRMED FIXED |
| V4/D4 | Сверка читала только первые 30 транзакций (свежие могли не попасть) | Постраничное чтение истории до конца, размер страницы до 100 | `bot/stars_provider.py:193-243`, тест `::test_reconcile_reads_all_transaction_pages` | CONFIRMED FIXED |
| D4 | Реестр попыток провайдера рос без очистки (после 4096 продажи вставали) | Записи старше часа удаляются, при переполнении освобождается половина самых старых | `bot/stars_provider.py:32-52`, тест `::test_invoice_attempt_registry_does_not_block_sales_when_full` | CONFIRMED FIXED |
| V5 | Покупка из мини-аппа не работала: приходило сообщение вместо ссылки | Мини-апп запрашивает ссылку (`prefer_link=True`), бот — сообщение | `bot/mini_app_billing.py:73-81`, `bot/billing.py:151,207-212`, `bot/stars_provider.py:120-126`, тесты `tests/test_mini_app_stars_purchase.py::test_mini_app_route_takes_the_link_and_never_sends_a_chat_invoice`, `tests/test_stars_provider.py::test_bot_gets_an_invoice_message_and_the_mini_app_gets_a_link` | CONFIRMED FIXED |
| D3 | Витрина обещала звёзды без разрешения владельца | Готовность способа учитывает `allow_public_stars` | `bot/plan_catalog.py:123-127`, тест `tests/test_mini_app_stars_purchase.py::test_catalog_hides_stars_when_the_owner_has_not_opened_them` | CONFIRMED FIXED |
| C1/C2 (мини-апп) | Ложные тексты («подключаем платёжную систему») и «нет связи» после 10 минут | Причины отказа разведены по `reason_code`; 403 с `error=unauthorized` трактуется как истёкшая сессия и перезагружает приложение | `bot/mini_app_ui/purchase.js:17-25,55-62`, `bot/mini_app_ui/api.js:37-47` | CONFIRMED FIXED (проверка на устройстве остаётся) |

## 3. Прошлые темы, перепроверенные отдельно

| Тема | Что проверено | Доказательство | Статус |
|---|---|---|---|
| Дубликаты Telegram-апдейтов | Durable inbox с ключом `(bot_id, update_id)` и статусами; платёжные апдейты обрабатываются тем же путём | `bot/telegram_replay.py`, таблица `telegram_update_inbox` (PK `(bot_id,update_id)`) | CONFIRMED (см. отчёт A/B по идемпотентности платежей) |
| Pending Telegram updates | Обработка через `ReplayDispatcher`, payload обнуляется после `done` | `bot/telegram_replay.py:96-105` | NOT REGRESSED (по коду) |
| Идемпотентность платежей | Один факт на `(provider, transaction_id)`, один грант на заказ, повтор → `already_applied` | `bot/billing.py:360-361`, PK `billing_provider_facts`, `entitlement_grants.request_key UNIQUE` | CONFIRMED (см. отчёт A) |
| Consistency контракта и денег | Контракт требует `PRODUCTION_PAYMENT_POLICY=off`, при этом `BILLING_PUBLIC_STARS=1` | `bot/production_admission.py:95,114` против переменных production | OWNER DECISION REQUIRED (поле контракта не отражает реальность) |

## 4. Что осталось непроверенным здесь

- Реальная оплата на iOS/Android и в Telegram Web (нужно физическое устройство).
- Реальный Platega sandbox/callback (провайдер не подключён и не тестировался живьём).
- Нативная приёмка мини-аппа после последних правок (нужен клиент Telegram).
