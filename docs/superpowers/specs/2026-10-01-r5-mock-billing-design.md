# R5 — provider-agnostic billing с mock provider: design

Дата: 2026-10-01. Основание: утверждённый `docs/ROADMAP.md`, R4 staging engineering acceptance и существующие `streamer_identities`/`entitlement_grants`. Реализация ограничена локальным контуром и pinned Railway staging с `@TwitchSignalTestbot`.

## Цель и граница

Проверить всю цепочку заказа и доступа без денег: подтверждённый Telegram/Twitch стример создаёт тестовый checkout; только серверно проверенное событие mock provider переводит заказ в paid и создаёт Streamer Plus grant; cancel, refund и expiry дают ожидаемый доступ и audit. Никакой redirect/возврат с checkout сам по себе не подтверждает оплату. Реальные цены, валюты, провайдеры, банковские/крипто операции и production интеграция не входят в R5.

Mock использует ровно одну условную `TEST` unit на заказ, не имеющую денежной стоимости. Длительность grant задаётся тестовым сценарием в диапазоне 60–2 678 400 секунд и не фиксирует коммерческий срок подписки. Pending checkout истекает через 900 секунд. Никаких автопродлений и публичной продажи.

## Слои и интерфейсы

- `PaymentProvider` — протокол `create_checkout`, `verify_webhook`, `request_refund`, `cancel_checkout`. `VerifiedPaymentEvent` содержит provider, уникальный event ID, order ID, provider payment ID, событие `captured`/`refunded`, TEST units и currency. Billing service получает только этот объект после проверки сырого payload; ни query string, ни клиентский success URL не являются источником paid.
- `MockPaymentProvider` реализует протокол без внешней сети. Он выдаёт неприменимый к реальным платежам checkout reference и подписывает тестовые webhook HMAC-SHA256 отдельным тестовым секретом: `X-Mock-Timestamp` содержит Unix seconds, `X-Mock-Signature` — hex HMAC от ASCII timestamp, точки и raw body. `verify_webhook` ограничивает body 4096 байт, допускает разницу server time не более 300 секунд и сверяет подпись constant-time; затем валидирует точные JSON поля `event_id`, `order_id`, `payment_id`, `type`, `units`, `currency`. Отсутствующая/поддельная/устаревшая подпись отвергается до записи в DB. Mock test helper создаёт события capture/refund для тестов и закрытого staging drill, но не создаёт public payment callback/UI.
- `BillingService` управляет order state, а не доверяет provider callback напрямую. Он разрешает checkout только подтверждённому `streamer_identities.telegram_user_id`, фиксирует `broadcaster_id`, plan=`streamer_plus`, provider=`mock`, одну `TEST` unit и checkout expiry. `request_key` — ASCII строка длиной 1–128 символов; повтор с тем же owner/plan/duration возвращает тот же order/checkout, с иными данными отклоняется. Order сначала сохраняется pending, затем получает provider checkout reference; при ошибке provider reference остаётся пустым, тот же request key повторяет вызов с тем же order ID. `cancel` возможен только из pending; `refund` запрашивается только для paid, а доступ отзывается после проверенного refunded webhook. При ошибке provider cancel/refund состояние не меняется и операция повторяема. Checkout expiry закрывает только pending. Оплаченный grant истекает по серверному времени в существующем entitlement query.
- SQLite миграция добавляет `billing_orders`, `billing_payments`, `billing_webhook_events`, `billing_audit`. Уникальные ключи защищают request key, provider event ID и provider payment ID. Provider event ID вместе с SHA-256 raw body обеспечивает replay idempotency: точный повтор не меняет состояние, тот же ID с иным body отвергается. Запись события, перехода order/payment и grant/audit выполняется как одна write-транзакция с rollback при ошибке.
- Для mock capture создаётся существующий `entitlement_grants` c `source='mock'`, `request_key='mock-order:' + order_id`, `issued_by=0` как системный actor; `entitlement_events` получает `actor_telegram_id=0`. Refund ставит `revoked_at` этому grant и создаёт audit event. R4 test grants не перезаписываются; если они действуют одновременно, доступ сохраняется до истечения/отзыва каждого grant по текущему SQL.

## Переходы и отказ по умолчанию

`pending → paid → refunded`; `pending → cancelled`; `pending → expired`. Повтор capture/refund с тем же event ID и body в пределах 300-секундного окна подписи возвращает прежний результат; позже событие отвергается как устаревшее. Capture после cancel/expiry, refund до capture, несовпадение order/provider/payment/TEST units/currency и конфликт нового event ID с уже занятым payment ID отвергаются без grant. Разные event IDs с одинаковым допустимым capture после paid не создают второй grant. При provider/API ошибке order остаётся в однозначном состоянии и может быть повторён по request key; отрицательный результат не превращается в paid.

Серверная проверка текущего order owner и сохранённого broadcaster ID обязательна при cancel/refund. Никакой ввод пользователя не выбирает чужой order или получателя grant. Billing API/CLI для mock не монтируется в production: точный pinned project/environment/service/Volume gate R4 проверяется перед staging drill. Секрет mock webhook не хранится в Git, не логируется и не является реальным payment credential. Публичный webhook endpoint и checkout UI не требуются для R5 core: тестовые signed события подаются через закрытый harness; это исключает случайную продажу до выбора реального провайдера.

## Тесты и staging evidence

TDD покрывает: повтор checkout/конфликт ключа; unsigned/tampered/stale webhook; redirect без paid; capture + grant; replay и конфликт event ID; поздний capture; refund + revoke, повтор refund; cancel pending и запрет cancel paid; expiry pending/paid; failure rollback; чужой Telegram ID и Twitch identity; два действующих grant источников test/mock; восстановление после DB reopen. Все тесты используют локальный SQLite и mock, без внешних HTTP-платежей.

Перед staging migration: просмотр diff, полный suite, новый внешний online backup и restore/migration drill на копии. Staging deploy только через guard и commit snapshot. Staging smoke: версии/`integrity_check`, HTTP health/auth/testbot, mock lifecycle на отдельной временной staging DB с фиктивной identity и условными units, без активной production/staging пользовательской оплаты. Реальный provider остаётся нерешённым и не выбирается автоматически.

## Рассмотренные варианты и выбор

Отдельный billing ledger и `PaymentProvider` выбран, потому что сохраняет order/payment/audit независимо от R4 grants и даёт место для будущего адаптера после отдельного решения пользователя. Запись paid непосредственно в grant без order/payment отброшена: она не даёт проверить replay/refund. Общее платёжное веб-окно отложено до юридического, продуктового и provider выбора; mock drill закрывает требуемые переходы без внешней оплаты.

## Самопроверка

Тестовый checkout не является платёжной услугой. Доступ появляется только после проверенного события и атомарной записи; refund revokes именно связанный grant. Повтор события идемпотентен, конфликт отклоняется. Scope не включает production, настоящий webhook endpoint, реальные суммы/валюты и выбор провайдера. Неопределённых переходов order state нет.
