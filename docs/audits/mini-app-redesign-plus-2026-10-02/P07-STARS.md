# P07 — Stars adapter и OFF handlers

BASE `7f8b920`; один исполнитель/scoped review. Реальный Telegram sender не подключён.

## Изменения

Network-free TelegramStarsProvider проверяет готовность, серверный XTR snapshot, frozen buyer/product/period, payload order+attempt и точные поля Bot API. Invoice: XTR/пустой provider_token/одна LabeledPrice, без recurring/tips/provider data; forwarded invoice не даёт другому пользователю покупку. Invoice message не является charge ID: reference остаётся NULL до successful_payment.

Precheckout проверяет actor/order/amount/currency/expiry/attempt и актуальную Streamer identity, отвечает с лимитами3+5s, не выдаёт grant. Типизированный successful_payment проходит общий P06 apply; повтор charge не добавляет срок и не оплачивает чужой order. Refunded_payment требует прежний charge и свой buyer/order; refundStarPayment только merchant service с frozen user. Accepted refund не называется completed и не отзывает grant до проверенного refund update. GET по Stars charge не выдуман, reconciler для Stars выключен.

Fresh payment router включён в прежний aiogram dispatcher, default service OFF без sender. Fixed first_release_payment_policy игнорирует наличие secrets/env flags. Shutdown закрывает service. Старый throttle мог отбросить финансовые updates после обычного действия: RED воспроизвёл, исправление пропускает только типизированные payment service messages к durable ledger. Обычные действия сохраняют ограничения. `/paysupport` получит настоящий SupportService в P16.

## Доказательства

- `P07-red.log`: отсутствующий adapter/config/handlers; после исправления синтаксиса теста зафиксирован ImportError ожидаемого отсутствующего API.
- `P07-throttle-red.log`: 1 вызов вместо3, потеря successful/refunded; после исправления PASS.
- `P07-pass.log`: **53 passed /74 subtests**, 38.17s, exit0 (Stars, P06 lifecycle, legacy billing и Platega).
- `P07-compat-pass.log`: **52 passed /11 subtests**, 17.94s, exit0 (Config, startup wiring, quiet/legacy compatibility, notification cutover).
- Fake Bot session/sender + TEMP SQLite, real aiogram DTO/dispatcher. XTR17/30days/terms — explicit fixture only; настоящий каталог XTRNone/periodunapproved. Не утверждены цена Stars и реальный месяц.

Official sources reread: [Stars flow](https://core.telegram.org/bots/payments-stars), [sendInvoice](https://core.telegram.org/bots/api#sendinvoice), [precheckout](https://core.telegram.org/bots/api#answerprecheckoutquery), [successful/refunded DTO](https://core.telegram.org/bots/api#successfulpayment), [refundStarPayment](https://core.telegram.org/bots/api#refundstarpayment). Установленный aiogram3.30 поддерживает указанные поля.

Реальные invoice/refund/test environment/native/owner OAuth NOT TESTED, не разрешены текущим денежным запуском. Production/staging не менялись. Следующий P08 темы/SDK.
