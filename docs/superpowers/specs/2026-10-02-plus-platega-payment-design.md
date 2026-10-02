# TwitchSignalBot — Plus, Platega и темы

## Актуальное решение владельца — реализация разрешена

Attachment `650afe6a-f60f-4201-bc2e-938a49c9d84a` утвердил выполнение P01–P21 и guarded staging после gates. Viewer Plus150 ₽/месяц; Streamer Plus300 ₽/месяц (200 ₽ отменены), Viewer включён для frozen Telegram beneficiary. Plus сразу предлагает продукт выбранного режима, без основного переключателя тарифов; четыре главных блока с SVG, «Все возможности», вторичный Viewer у стримера. Активный экран — «Моя подписка». Первый staging: три кликабельных метода внутри Mini App, Platega названа открыто, payment OFF и честная заглушка. Исторические checkpoints/QA ниже относятся к прежним файлам; их HTML/экспорт сохраняются.

Дата: 02.10.2026. **Проект spec для утверждения владельцем. Продуктовый код не изменён.**

## 1. Основание и разрешённое выполнение

Основание: attachment `89db2ad6-9158-4375-bf37-27ee9f48f81b`; merchant/purchase UX уточнён более поздним `7ced1d3a-dba2-4984-8c6e-3699f5b606ba`. Последнее прямое уточнение владельца от 02.10.2026 имеет приоритет: покупка начинается внутри TwitchSignalBot / Mini App, затем пользователь выбирает Telegram Stars, СБП или банковскую карту. Stars обслуживает Telegram, СБП/карту — Platega. Владелец осознаёт и принимает платформенный риск; способы нельзя маскировать или скрывать от проверок. Предложение «Mini App только Stars, Platega только на отдельном сайте» отменено. Альтернативные российские кассы не рассматриваются. Утверждены рублёвые цены и включение Viewer в Streamer. Spec и план P01–P21 утверждены последним промптом; разрешены последовательная реализация и guarded staging после gates. Реальные денежные операции не разрешены.

База: `C:\Users\yusha\Desktop\cloude\TG-BOT.(TwtichSignal)`, ветка `autonomous/twitchsignal-roadmap`, HEAD `e0d44c315a1c81dc5b310dd1f3c7f3169915c985`. Один исполнитель. Показанный А «Собранный» сохраняется. Новый дизайн, Expo/React Native, повтор R0–R9, вторая БД и второй бот не нужны.

Только локальные проверки и закреплённый staging `@TwitchSignalTestbot`. Реальные списания, сообщения получателям и OAuth владельца требуют отдельного разрешения конкретного действия/аккаунта. Production, main/master, глобальные установки и секреты не меняются. Резервные HTML/экспорт/документы и старые группы сохраняются.

## 2. Что уже есть

| Основа на текущем HEAD | Что расширить |
|---|---|
| PaymentProvider с create_checkout, verify_webhook, request_refund, cancel_checkout; MockPaymentProvider без сетевых запросов | Нормализованный контракт реальных провайдеров, status verification и capability refund |
| BillingService принимает только mock; единицы 1, валюта TEST; заказ с покупателем, subject, планом, сроком и grant | Денежный snapshot каталога, метод/провайдер/attempt, раздельные состояния платежа и доступа |
| Одна SQLite: billing_orders/payments/webhook_events/audit/refund_requests, entitlement_grants | Миграция этой схемы, durable inbox/reconciliation; без параллельного ledger |
| Атомарная проверка суммы/валюты/провайдера, запись события и grant; дубли не дают второй grant | Сохранить свойства, добавить реальные callbacks и поздние оплаты; mock TTL не переносить буквально |
| Viewer и Streamer независимы; Streamer subject — verified Twitch broadcaster | Зафиксировать Telegram-beneficiary и производное личное Viewer-право |
| Семь суток test-only trial, allowlist/однократность; строгий initData; mock owner-only API | Сохранить trial отдельным, считать inherited Viewer при eligibility; убрать денежные методы из test-confirm пути |

Источники: `bot/billing_provider.py:36`, `bot/billing.py:16`, `bot/billing_models.py`, `bot/database.py:38`, `bot/database.py:1126`, `bot/database.py:1739`, `bot/capabilities.py:50`, `bot/viewer_trial.py`, `bot/mini_app_billing.py`, `bot/mini_app_auth.py`.

Выбран подход: расширить существующий service/ledger и сохранить mock-адаптер. Отдельный новый billing рядом со старым создаёт двойную истину; прямое переключение Plus из callback обходит ledger. Эти варианты не предлагаются.

## 3. Продукты и единый каталог

| Продукт | Разовая цена RUB | Период | Права |
|---|---|---|---|
| viewer_plus | 150 ₽, `amount_minor=15000` | один месяц | Все Viewer Plus |
| streamer_plus | 300 ₽, `amount_minor=30000` | один месяц | Все Viewer Plus тому же покупателю + Streamer Plus |

Автопродления нет. Определение месяца — календарный либо фиксированный — ещё не утверждено; не подставлять 30 суток. В каталоге `period_code=one_month`, `period_rule=unapproved`, `auto_renew=false`. Денежный checkout отключён до выбора period rule. Семь суток trial не являются сроком покупки. XTR-цены обоих продуктов TBD, независимо от RUB.

Серверный каталог хранит product ID, версию цены/прав, валютные предложения, period rule, feature IDs и готовность каждого платёжного метода с причиной недоступности. Все три способа отображаются внутри бота / Mini App; неготовый способ открывает честное объяснение, а не исчезает. Клиент отображает каталог и выбирает ID; сервер повторно определяет сумму, срок и право. Не создавать stars_plus/sbp_plus/card_plus. Прототипный `plus-catalog.js` — демонстрация, не источник серверной цены.

Free Viewer: 50 стримеров, фото, стандартные уведомления, тихие часы и все прежние бесплатные функции. Viewer Plus: 200, пять выбранных видео вместе с offline, фильтры категорий/слов, нужная категория и уведомление о её смене, напоминания 15/30 минут, папки, история, остальные действующие возможности.

Free Streamer: подключение Twitch/Telegram-канала, стандартный пост и фото. Streamer Plus: видео в автоматическом посте, свой текст/оформление, дополнительные кнопки, варианты постов и расширенная статистика подтверждённых публикаций. Она использует существующие streamer_post_events/сравнение периодов, не обещает Twitch-просмотры или клики. Старые group connections остаются; новый выбор — channel-only.

Дополнительные raid/name-change/spike Plus-события из актуального handoff требуют отдельной проверки качества/ложных срабатываний и согласования прав перед продажей. Сейчас не добавлять их в активный каталог и не отнимать действующие бесплатные возможности. Collab/служебный статус недоступного Twitch-канала не переводить в платные здесь.

## 4. Утверждённая схема покупки и принятый платформенный риск

Точка начала покупки — **TwitchSignalBot / Mini App**. Пользователь выбирает тариф, видит функции, конкретную цену и период, нажимает «Купить» / priced CTA и получает выбор «Как оплатить?»:

| Способ внутри бота / Mini App | Провайдер и следующий шаг |
|---|---|
| Telegram Stars | TelegramStarsProvider → официальный Telegram invoice в XTR |
| СБП | PlategaProvider → открыто обозначенная внешняя платёжная страница Platega, метод 2 |
| Банковская карта | PlategaProvider → открыто обозначенная внешняя платёжная страница Platega, метод 11 |

У всех способов общие продукты, server order, ledger и правила доступа; отдельный сайт TwitchSignalBot для начала покупки не требуется. СБП и карта оплачивают Viewer Plus за 150 ₽/месяц либо Streamer Plus за 300 ₽/месяц. Streamer Plus включает все Viewer Plus-возможности тому же покупателю. Автопродления нет.

Зафиксированный риск: цифровой Plus относится к digital goods/services, для которых Telegram требует Stars внутри бота и Mini App независимо от наличия другого сайта. Выбранный вход с СБП/картой расходится с этим требованием. Владелец прямо сообщил, что осознаёт и принимает платформенный риск; это не означает одобрение Telegram. Продуктовое решение остаётся указанным выше. [Официальное правило и FAQ](https://core.telegram.org/bots/payments-stars), [условия §6.2](https://telegram.org/tos/bot-developers).

Внешний переход подписывается как оплата через Platega; пользователь видит провайдера до перехода. Не маскировать его под Stars, не использовать обходы Telegram, не прятать способы от проверок и не менять их состав для проверяющего аккаунта. Применять обычные документированные средства перехода, сохраняя строгую авторизацию и серверную проверку платежа. Принятие риска не разрешает ослаблять безопасность.

XTR-цены ещё не назначены. Stars остаётся видимым и кликабельным: до решения показывать «Цена в Stars будет определена» и объяснение временной недоступности, без invoice и фиктивного success. RUB не конвертировать в XTR автоматически и не обещать рублёвый эквивалент. В согласуемом демонстрационном пути все способы должны вести только к честной заглушке без транзакций и выдачи прав; текущий макет ещё не обновлён (§20). Подключение требует утверждённых spec и plan и безопасных проверок.

## 5. Контракт PaymentProvider

Расширяем существующую абстракцию, сохраняя legacy mock-контракт через адаптацию:

- create_payment(server_order_snapshot, attempt_id) → provider reference, безопасная hosted/invoice URL, текущий статус и срок счёта.
- get_payment_status(reference) → нормализованное доказательство платежа либо явное «неизвестно/проверка недоступна».
- handle_callback(raw_body, headers) → проверенное уведомление, которое проходит общую сверку заказа перед применением прав.
- refund_payment(reference, request_id) → принят/ручной контроль/завершён/отказ; только при документированной capability.

PlategaProvider, TelegramStarsProvider и MockPaymentProvider различают transport/auth; BillingService управляет заказом, повтором, атомарным grant и audit. Для Stars status capability ограничена официальными доступными механизмами, не выдуманным GET по charge ID; основной факт — successful_payment. Место для Stripe/Paddle — контракт/реестр capability, без пакетов, ключей, подключений и цен сейчас.

Existing mock owner-only test-confirm никогда не подтверждает заказ platega/telegram_stars. Присутствие credentials само по себе не включает кассу. Refund и закрытие UI не являются общей операцией cancel_checkout.

## 6. Внутренний заказ и попытки

Минимальные поля:

| Поле | Смысл |
|---|---|
| order_id, request_key | Серверный ID и идемпотентный запрос покупателя |
| telegram_user_id, beneficiary_telegram_user_id | Проверенный покупатель и неизменный получатель личных прав |
| product_id, catalog_version, period_code, period_rule_version | Snapshot утверждённого продукта/периода |
| subject_kind/id, broadcaster_id | Verified Streamer subject; Viewer subject — Telegram ID |
| payment_provider, payment_method | mock / platega / telegram_stars; sbp / bank_card / stars, TEST отдельно |
| amount_minor, currency | Целое число копеек RUB или целые XTR; не float |
| provider_transaction_id, status | Корреляция и финансовое состояние |
| created_at, expires_at, confirmed_at, refunded_at, entitlement_applied_at | Server UTC; expires_at здесь срок счёта, не подписки |
| grant_id, access_starts_at, access_expires_at | Результат единственного применения подтверждённого заказа |

Статусы платежа: created, pending, succeeded, failed, canceled, expired, refunded. Raw provider status и причина возврата хранятся отдельно. Состояние доступа рассчитывается из grants; `succeeded` не означает «подписка ещё активна». Неизвестный outcome — состояние технической попытки/reconciliation, не успешный платеж.

В одном заказе фиксируем attempts и выбранный метод. Одновременно не создавать две активные provider transactions. Смена метода разрешается только после доказанного отсутствия риска оплаты прежней попытки. При неопределённом POST — creation_unknown, не повторная покупка. Если две внешние оплаты всё-таки подтверждены из-за гонки, ledger фиксирует обе, один order даёт один grant; излишек уходит на разбор/возврат по утверждённой политике, не теряется.

Повторная покупка того же активного плана и покупка Viewer при включённом Streamer блокируются до определения правил продления/сочетания. После окончания пользователь покупает следующий месяц вручную. Viewer→Streamer — отдельная точка upgrade, денежной формулы нет.

## 7. PlategaProvider и hosted checkout

Подтверждённые API перечислены в [исследовании](../../audits/2026-10-02-platega-api-research.md). Base `https://app.platega.io/`, авторизация X-MerchantId/X-Secret только серверная.

Основной flow: выбор СБП или карты внутри TwitchSignalBot / Mini App → проверенный серверный заказ/attempt → POST /transaction/process с официальными method 2 (СБП QR) либо 11 (карточный эквайринг), серверными paymentDetails/description/return/failedUrl/orderId/payload → открыто обозначенный переход на hosted checkout Platega. Transaction ID возвращает провайдер; ответ `redirect`/expiresIn нормализуется адаптером. Alternative POST /v2/transaction/process возвращает `url` и выбор у Platega; менять UX на него только после проверки разрешённых методов merchant account и согласования владельцем. [Создание](https://docs.platega.io/создание-платежной-ссылки-с-заданным-методом-29203843e0), [v2](https://docs.platega.io/создание-платежной-ссылки-без-заданного-метода-33845703e0), [enum](https://docs.platega.io/paymentmethodint-13226216d0).

JSON числа разбираем Decimal, сумма сравнивается точно с integer minor units; currency только серверная RUB. Return/failed URL берутся из закреплённой конфигурации, не из клиентского ввода. Hosted URL проходит HTTPS/allowlist, без передачи секретов. Данные карты проходят только у провайдера. Redirect клиента означает «Проверяем оплату…», не подтверждение.

Metadata.userId формируется из verified Telegram buyer либо согласованного внутреннего ID; userName только display. Категория магазина/обязательность/формат имени без username требуют ответа менеджера. Не ослаблять antifraud фиктивными значениями.

## 8. Создание без гарантии provider-idempotency

У Platega не найдена опубликованная гарантия идемпотентности POST создания и GET по orderId. Внутренний request_key обеспечивает один локальный заказ, но не исключает два внешних платежа при повторном POST.

Предлагаем durable attempt/outbox и lease одного отправителя. Сначала фиксируем attempt и server payload, затем делаем POST. При тайм-ауте или crash после отправки — creation_unknown; новая отправка запрещена до восстановления transaction ID или доказанного отсутствия транзакции. orderId/payload помогают корреляции, но не заменяют подтверждение. Подтверждённый официальный механизм восстановления — вопрос менеджеру и gate включения адаптера.

Callback раньше ответа POST сохраняется в inbox. Неизвестный transaction не даёт прав; он сверяется с каноническим GET и уже созданной попыткой. Payload из callback/return не выбирает Telegram beneficiary. Если доказуемая связь отсутствует, ручной разбор без выдачи и без потери финансового события.

## 9. Callback, проверка и атомарность

Предлагаемый маршрут: POST `/payments/platega/callback`, вне Telegram initData auth, со своей provider authentication. До миграции и утверждённого внедрения его нет. Проверять единственные нормализованные X-MerchantId/X-Secret, постоянное время сравнения, размер тела, content type, уникальные JSON keys, типы ID/amount/currency/status. Не писать body/headers/credentials в logs. Официальный механизм обычного callback — эти заголовки; HMAC Payout здесь неприменим.

После durable приёма уведомления сервер GET /transaction/{id} подтверждает merchant, transaction, сумму gross order, валюту, метод и связь с ранее созданным order. В документации merchant field записано `mechantId`; отсутствие доказуемого значения, несовпадение или unknown status не выдают Plus. До уточнения schema adapter работает с явными fixtures, fail closed. [Статус](https://docs.platega.io/проверка-статуса-оплаты-платежа-29203844e0).

Callback description содержит CHARGEBACKED, а request enum — только CONFIRMED/CANCELED. Эту несогласованность подтвердить у менеджера. GET временно недоступен → pending reconciliation, не success. Inbox ограничен и дедуплицирован; успешный ACK только после durable записи, ошибки хранения возвращают ошибку для retry. Provider ожидает ответ до 60 секунд и повторяет до трёх раз через пять минут; handler должен отвечать быстро. [Callback](https://docs.platega.io/callback-об-изменении-статуса-транзакции-29209725e0).

Нормализованный ключ включает provider/merchant/transaction/status; тело проверяется на конфликт. Ключ financial transaction уникален в provider, grant/order — UNIQUE. В одной SQLite transaction: событие/платёж → состояние заказа → grant с фиксированными start/end → entitlement_applied_at → audit. Повтор CONFIRMED, иной body с тем же событием и гонка GET/callback не добавляют месяц. HTTP/UI успех появляется после commit.

## 10. Статусы и reconciliation

| Provider факт | Наш результат |
|---|---|
| PENDING | pending, без прав |
| CONFIRMED и совпали все критические поля | succeeded, единственное применение grant |
| CANCELED | canceled без нового grant; позднее противоречие после success — сверка, не молчаливый rollback |
| CHARGEBACKED | refunded с raw reason/audit; изменение доступа по утверждённой политике |
| Неизвестный / неверные деньги / чужой merchant | карантин/ошибка сверки, без grant |

Возврат пользователя, reload и закрытие приложения читают только собственный server order. Периодический worker использует существующий runtime, durable next_check_at/attempt_count, backoff/jitter, общий лимит запросов и конечный retry budget. Числа/таймауты закрепит план по условиям account, без агрессивного polling. По исчерпании — «Оплата ещё проверяется», технический alert/ручная сверка, не ложный отказ и не автоматический новый POST.

Поздний подтверждённый денежный платёж не игнорируется только из-за локального expires_at: финансовый факт сохраняется, сервер проверяет order/attempt, один grant или разбор по утверждённому правилу. Порядок CONFIRMED/CHARGEBACKED защищает от восстановления отозванного доступа старым callback; окончательность проверяется GET. Mock сохраняет свои прежние TTL/tests. Предложение: начало разового доступа — первая атомарная verified apply по server UTC; повтор не двигает дату. Владелец утверждает это вместе с определением месяца.

## 11. TelegramStarsProvider

XTR invoice после утверждения отдельной XTR-цены; provider_token пустой, без recurring. Pre-checkout проверяет invoice payload, покупателя, product snapshot, currency/total_amount, состояние заказа и Streamer identity; ответ за 10 секунд. Pre-checkout, invoiceClosed/paid и клиентский callback сами по себе не дают доступ. Verified successful_payment с уникальным telegram_payment_charge_id применяется тем же атомарным ledger. Refund — официальный refundStarPayment с учётом результата/повторных updates, политики доступа и audit. `/paysupport` и доступные условия/поддержка входят в план. [Telegram flow](https://core.telegram.org/bots/payments-stars).

Обычный `@TwitchSignalTestbot` на production Bot API не делает Stars бесплатными. Отдельная Telegram test environment — отдельное согласование инфраструктуры/аккаунта; сейчас fixtures и fake sender. Не отправлять invoice и не списывать Stars без отдельного разрешения.

## 12. Права и наследование

Grant хранит product, subject, beneficiary Telegram ID, source test/mock/paid, origin order и start/end/revoked. Source не вычисляется по валюте. Streamer post rights остаются связаны с verified broadcaster и разрешённым placement; личные Viewer rights относятся к frozen beneficiary заказа. Issued_by — автор выдачи, не покупатель. Перепривязка Twitch/смена владельца канала не переносит оплаченный личный Viewer третьему лицу.

Effective Viewer = собственный активный viewer_plus **ИЛИ** активный streamer_plus с тем же beneficiary. Результат показывает оба источника и сроки; display дату рассчитываем по непрерывно действующим источникам, не обещаем доступ через будущий разрыв. Не копировать inherited Viewer в самостоятельный grant: это затруднит expiry/refund и может продлить права лишний раз. Streamer доступ проверяет одновременно продукт, связь и placement, без mode flag.

После expiry Streamer личные Viewer-функции сохраняются только при другом действующем источнике. Шаблоны, папки, настройки, история и подключения не удаляются; платный эффект отключается. Потеря Twitch-связи не лишает frozen buyer личного Viewer в оплаченном периоде, но блокирует чужие размещения. Финансовые последствия переноса Streamer subject отдельно не утверждены.

Общий resolver и общий SQL predicate должны использоваться не только CapabilityService, но и `database.py` limits/video/effective-preview, `category_alert_store.py`, `viewer_history.py`, trial eligibility, фильтры/папки/напоминания, poller/очередь и media runtime. В атомарных запросах нельзя оставить старый прямой viewer-only grant lookup. Gate проверяется до capture и перед edit; старый togglepreview остаётся закрытым для обхода.

Видео: личный cap 5 включая offline, CAS/атомарная замена и серверный отказ шестому. Общий capture/render и per-bot file_id reuse; H.264/no audio/Telegram Animation до 24 s. При общей нагрузке — фото и честный pending/degraded. После выключения/expiry/утверждённого revoke — animation→photo; если независимый Viewer ещё действует, личное видео сохраняет право. Существующие measured load ограничения не считать SLA.

## 13. Upgrade, продление и возврат

Viewer→Streamer: UI «Viewer Plus уже активен. Streamer Plus включает его возможности». Checkout upgrade заблокирован до формулы: не выбирать 300 ₽, 50 ₽, prorating/перенос, округление или новую дату автоматически. Архитектурная точка quote_upgrade возвращает unavailable_reason до решения. Ручная покупка следующего месяца после expiry разрешится утверждённым общим period rule; покупка заранее требует отдельного правила.

Platega имеет cancel-supported и cancel, но accepted не равен подтверждённому возврату; manualControlRequired означает отдельный контроль. Partial refund не подтверждён. Refund request сохраняется до внешнего POST, неизвестный outcome сверяется без слепого повтора. Только разрешённый серверный субъект инициирует возврат собственного подтверждённого order; пользовательский запрос поддержки не означает авторизацию списания у merchant. [API возврата](https://docs.platega.io/отмена-транзакции-38225949e0).

Политику grant после полного refund/CHARGEBACKED утверждает владелец: немедленный отзыв или остаток периода. До этого денежная касса не включается; mock-политика немедленного revoke остаётся только mock. Ledger умеет финансовый статус отдельно от доступа и не теряет chargeback, пришедший раньше CONFIRMED. Refund одного product/order никогда не отзывает независимый grant другого источника.

## 14. Миграция одной SQLite

1. Backup/export закреплённого staging, integrity/restore drill; далее migration-copy на временной копии. Активную БД сейчас не трогать.
2. Версионированно расширить/пересобрать billing_orders CHECK и snapshot поля, сохранив pending/paid/cancelled legacy mapping, units/TEST и существующие grant IDs. Реальные и mock заказы нельзя преобразовать в оплаченные друг друга.
3. Расширить billing_payments/events/refund_requests/audit и добавить attempt/inbox/reconciliation записи внутри той же БД. Уникальные ключи provider transaction, event, order grant; foreign keys и транзакционный rollback проверить.
4. Добавить beneficiary/order provenance в entitlement_grants. Сейчас таблица не имеет CHECK source, но методы создают только test/mock; paid вводится через строгий service validator. Не связывать paid source с конкретной кассой.
5. Legacy Viewer beneficiary восстанавливается из subject. Streamer mock — из связанного billing order при непротиворечивых данных. Старые test Streamer grants без достоверного buyer остаются с прежними размещениями и требуют явной reviewed binding для новых личных прав; не подставлять issued_by/current owner. Это compatibility-предложение для утверждения.
6. Проверить сохранность HTML/подписок/тихих часов/групп/templates/trial; количество/ID legacy записей, integrity, повторную миграцию и restore. Downgrade приложения после schema change — только проверенный совместимый snapshot либо restore; не обещать безусловный rollback к старому binary.

Версии и порядок миграций задаёт утверждённый plan. Ни одна миграция сейчас не выполнена, отдельной рабочей базы не создано.

## 15. UI и темы

Сохраняем А и единые мобильные строки. Plus сразу открывает Viewer в режиме зрителя и Streamer в режиме стримера; внутренний основной переключатель тарифов удаляется. Четыре главных блока с небольшими SVG, без emoji; «Все возможности» раскрывает каталог. Streamer показывает «Viewer Plus включён», компактное раскрытие Viewer и вторичную ссылку «Нужны только функции зрителя? Viewer Plus — 150 ₽». Активный экран показывает текущий продукт/статус/срок/includes/свои операции; формулу upgrade не угадывать. Free вход «Возможности Plus», активный план «Моя подписка». Viewer: краткое Free→Plus сравнение, все преимущества и 150 ₽/месяц. Streamer: Free пост/фото → Plus возможности, 300 ₽/месяц и заметное «Все возможности Viewer Plus уже включены». Внутри бота / Mini App CTA по уточнению менеджера: «Подключить Viewer Plus — 150 ₽» / «Подключить Streamer Plus — 300 ₽» → «Как оплатить?» с тремя способами из §4. СБП/карта сохраняют эти RUB-цены; у Stars отдельная явно показанная XTR-цена после её назначения, до этого — объяснение недоступности. Не обещать оплату Stars за эквивалент RUB.

Новый серверный каталог позволяет добавлять feature rows без дублирования прав/текстов. Расширенную статистику публикаций включить; «Переходы на Twitch · В разработке» — отдельное неактивное место, без подставных счётчиков/преимущества. Отправленные посты, нажатия, уникальные переходы и Twitch viewers имеют разные определения. Большой tracking subsystem в этот plan не включать.

Состояния: тариф → способ (Stars / СБП / банковская карта видны всем покупателям, неготовый открывает причину) → создаём → переходим → ждём → проверяем → успех/ошибка/отмена/истёк/возврат/уже активна. Даты только серверные. Viewer успех «Viewer Plus активен до <date>»; Streamer добавляет включение Viewer. До verified commit нет success-анимации. Сеть/повтор/reload не создают новый order, чужая операция недоступна; scroll/back/focus и сохранение выбора проверяются.

Первая тема own light. Own dark нейтральный графит, без синего canvas. Telegram mode читает реальные ThemeParams при запуске и themeChanged; обновляет семантические tokens текста/поверхности/link/button/header/bottom bar с безопасным fallback. Explicit choice сохраняется; OS/colorScheme не заменяет его. Один idempotent handler и cleanup. Реальные SDK BackButton/fullscreen/safe areas, Telegram variants и клавиатура проверяются при переносе. Сейчас preview использует подписанные имитации ThemeParams; это не native PASS. [Telegram ThemeParams](https://core.telegram.org/bots/webapps#themeparams).

Skills: mobile-app-ui-design главный; apple-liquid-glass справочник; design-system, четыре Expo reference без смены web stack; Impeccable review, Playwright, Stop-Slop. ui-ux-pro-max не применять.

## 16. Fiscal / NPD responsibilities

| Сторона | Подтверждённое / проектное обязательство | Что требует проверки |
|---|---|---|
| Platega | Публичный API приёма/статуса/отмены; позиционирование работы с самозанятыми без ИП | Допуск нашего merchant/category, договор, settlement/комиссии, чек/роль посредника, test mode |
| Наша система | Заказ, gross amount/currency, платежный audit, статус/доступ, support reference; без карт и секретов в UI | Согласованный экспорт/receipt reference; никакой автоматически объявленной fiscal integration |
| Владелец | Реальный onboarding/KYC у Platega, договор и организация выдачи корректных чеков/поддержки по подтверждённой схеме | Кто формирует/передаёт чек, учёт возврата и поступлений для конкретной схемы и Stars |
| Неподтверждено | Автоматическая фискализация/НПД через Platega и условия account | До получения договора/письменного ответа не утверждать, что вопрос решён |

ФНС описывает «Мой налог» и отдельные условия посреднической схемы; нельзя вывести налоговые обязанности только из API платежа. Наша квитанция заказа не налоговый чек. Не требовать выдуманное ИП/ООО, не подставлять фиктивные ИНН/СНО/НДС и не обходить KYC. [ФНС НПД](https://npd.nalog.ru/faq/?from=index), [Platega о самозанятых](https://platega.io/ru/blog/cheklist_samozanat). Оперативный договор конкретного account недоступен; это открытый документ, не выполненный legal audit.

## 17. Безопасность и staging

Trust boundaries: неподписанный browser/return → strict Telegram auth/server order; публичный callback → provider auth/canonical verification; transport → durable ledger; ledger → effective entitlement/placement/media. Заказ из Mini App создаётся только после строгой проверки initData, из бота — по проверенному Telegram субъекту входящего Bot API update. Покупатель/beneficiary фиксируется сервером до перехода к провайдеру. Только verified auth subject читает свои orders; произвольный URL user_id, return и клиентский paid не подтверждают личность или доступ. Отдельная авторизация на самостоятельном сайте не является условием выбранного flow. Cookie/CSRF и существующие auth scopes сохраняются.

Audit события: order_created, payment_pending, payment_confirmed, payment_canceled, payment_failed, payment_expired, payment_chargeback, entitlement_granted, entitlement_expired, entitlement_revoked. Отдельно creation_unknown/refund_requested/reconciliation_failed. Audit содержит внутренние ID/переход/время, не raw payload/headers/card/credential. Observed expiry логируется один раз; effective expiry проверяется по UTC независимо от задержки audit worker.

Понадобятся только server environment names `PLATEGA_MERCHANT_ID`, `PLATEGA_SECRET`; значения владелец вводит в Railway staging сам. Предлагаемый enable flag по умолчанию false, allowlist тестового доступа и жёсткие project/service/environment pins из scripts/staging_target.json. Bot identity getMe и deployment SHA проверяются перед активацией; ключи не печатаются и не отправляются в чат. Сейчас env не меняется.

Резервируем URL **https://worker-staging-2f74.up.railway.app/payments/platega/callback**. Маршрут сейчас отсутствует; передавать как действующий только после утверждённой реализации, guarded deploy, сертификата, identity и provider-safe проверки. Ни `/healthz=200`, ни DB label этого не доказывают.

После регистрации: несекретные merchant ответы, владелец добавляет env; затем разрешённый read-only connectivity GET /balance/all без логирования сумм/ключей и без транзакции. Callback fixtures сначала локально; внешний callback только подтверждённым бесплатным provider-safe способом. Если sandbox не доказан, денежные POST не выполнять. Native test, invoice/send/OAuth — с отдельным разрешением получателя/аккаунта.

## 18. Требования к будущим тестам

TDD RED → scoped implementation → PASS → scoped review → отдельный commit. Не ослаблять assertions/skip и не выдавать прежний suite за новый. План после утверждения spec: fixtures → migrations → provider contract/adapters → callback/reconciliation → resolver/gates → Plus UI/themes → browser/full suite/backup → guarded staging → разрешённый native E2E.

| Область | Обязательные случаи |
|---|---|
| Выбор покупки | Внутри бота / Mini App оба priced CTA открывают Stars/СБП/карту; методы не скрыты для проверяющих; Platega явно названа до внешнего перехода; общие product/order/ledger, безопасная смена метода; RUB 150/300 и отдельный XTR, неготовый способ объясняет причину; заглушка без payment request/grant |
| Platega создание | Методы 2/11, сумма/RUB серверные, hosted redirect/url, metadata, timeout/creation_unknown, запрет слепого retry и второго активного attempt |
| Подтверждение | PENDING/CONFIRMED/CANCELED/CHARGEBACKED/unknown; неверный secret/merchant/ID/amount/currency/method; отсутствующие поля и duplicate JSON |
| Гонки | Callback дважды/иной body, callback до ответа создания/до return, return до callback, закрытие UI, delayed/late after expiry, GET+callback race, crash после commit/до ACK |
| Reconciliation | Задержка/потеря callback, timeout/429, bounded retries, исчерпание без false success, refund раньше confirmed и старое событие после chargeback |
| Возврат | cancel-supported/отказ/accepted/manualControlRequired, unknown refund outcome, окончательный факт и утверждённое правило revoke |
| Stars | XTR TBD запрещает invoice; чужой buyer/payload/price/precheckout, успешный server update/duplicate charge/refund, client paid без grant |
| Права | Viewer, Streamer→Viewer, оба источника и отдельные expiry; foreign binding/owner transfer; test trial, режим/JS/старый callback без права |
| Лимиты/media | Free 51/Plus 201, видео 6, CAS/два соединения, offline count; общий cap, capture→edit recheck, photo fallback expiry/refund/disable и сохранённый независимый Viewer |
| Совместимость | HTML/export/группы/Free quiet, raid quiet-hours, MenuButtonWebApp после startup, старый togglepreview без bypass |
| UI | 360/390 и desktop, длинные имена/полные feature rows, 200% text/zoom, light/own dark/real Telegram params+event, Back/reload/keyboard/focus, все финансовые состояния и server date |
| Final snapshot | Полный suite, security/code/copy review, backup/restore/migration-copy, новая media нагрузка, staging pins/bot/SHA/route, разрешённые native screenshots |

## 19. Решения для утверждения

1. Утвердить исправленный spec с уже выбранной схемой §4 и общей архитектурой; после этого отдельно согласовать подробный implementation plan. Выбор точки входа и трёх способов не запрашивать повторно.
2. Определить месяц и anchor первой verified apply, повторную покупку активного плана/продление заранее.
3. Назначить XTR-цены, не выводить их из RUB.
4. Утвердить refund/chargeback→доступ и поддержку; partial refund пока не обещать.
5. Выбрать Viewer→Streamer formula позже; до неё upgrade недоступен.
6. Подтвердить compatibility binding старых test Streamer grants, не угадывая покупателя.
7. Получить у менеджера допуск самозанятого/категории/методов/metadata, договор/NPD responsibilities, безопасные тесты, schema/idempotency/восстановление unknown creation.
8. Предоставить реальные сведения владельца и индивидуальный SUPPORT_USERNAME и/или SUPPORT_EMAIL; утвердить Privacy Policy/User Agreement, сроки хранения и поддержку до банковского согласования.

Цены RUB, два product IDs, наследование Viewer, отсутствие автопродления, выбранный А, покупка внутри TwitchSignalBot / Mini App с выбором Stars/СБП/карты и Platega для СБП/карты уже решены. Платформенный риск прямо принят владельцем. Не запрашивать эти решения повторно и не возвращать отдельный сайт как обязательную точку покупки. Merchant/API-вопросы из пункта 7 требуют подтверждения Platega, а не угадывания владельцем.

## 20. Merchant approval / purchase UX

Дополнение менеджера сохраняет provider/order/entitlement архитектуру. Полный путь внутри TwitchSignalBot / Mini App: «Возможности Plus» → выбор плана → функции/точная цена/месяц → кликабельный CTA → «Как оплатить?» → Telegram Stars / СБП / банковская карта → понятный результат. До подтверждённых условий безопасного тестирования и остальных необходимых решений кнопка открывает «Оплата временно недоступна. Мы заканчиваем подключение платёжной системы». Нет внешнего POST, fictitious success или grant. Для неопределённого upgrade также кликабельное объяснение, без денежной формулы.

У трёх способов один вход внутри бота / Mini App. СБП/карта открыто ведут в hosted checkout Platega, Stars — в официальный Telegram invoice после назначения XTR-цены. Самостоятельный сайт для начала покупки не требуется. Принятый платформенный риск записан в §4; банковская проверка получает тот же набор способов и раскрытие внешнего провайдера, что пользователь. Требование к кликабельной покупке на этом этапе выполняется заглушкой и не включает реальный checkout.

Сохранённый [интерактивный макет А](../../design/mini-app-redesign-2026-10-02/bank-review.html) относится к предыдущему предложению: он показывает СБП/карту и пока не отражает утверждённый выбор всех трёх способов из §4. Его HTML, экспорт, screenshots и прежние QA сохраняются как резерв; они не подтверждают новую схему. Viewer150/Streamer300 в нём берутся из общего design fixture, не рабочего серверного каталога. Streamer статистика добавлена как согласуемая строка по реальному существующему источнику. На этой контрольной точке макет, рабочие UI/сервер/БД не изменяются; обновление экранов и новые проверки включить в plan после утверждения spec.

Шаблоны [Privacy](https://telegra.ph/Politika-konfidencialnosti-08-01-83) и [Agreement](https://telegra.ph/Polzovatelskoe-soglashenie-08-01-39) прочитаны только как структура. [Шпаргалка](https://clck.ru/3VRDWc) открылась в публичный Google Docs; текст экспортирован и прочитан, изображения-примеры не проверены. Она рекомендует временную кликабельную заглушку без credentials/транзакции, не доказывает наличие sandbox. Технический API берём из официальных Platega docs.

## 21. Документы и поддержка

В профиле/поддержке компактный вход «Документы»: Политика конфиденциальности и Пользовательское соглашение, доступные обычному пользователю и до оплаты. У покупки короткие ссылки; не вклеивать договор в CTA. Перед реальным checkout сохранять принятую версию условий в order, отдельное требуемое согласие не заменять фактом /start. Legal/tariff display получают product snapshot из того же серверного каталога, что order creation. Статические черновики сверяются с ним при публикации; mismatch блокирует выпуск.

Черновики для проверки: [Политика](../../design/mini-app-redesign-2026-10-02/legal/PRIVACY-POLICY-DRAFT.md), [Соглашение](../../design/mini-app-redesign-2026-10-02/legal/USER-AGREEMENT-DRAFT.md). Они описывают действующие Telegram/Twitch/настройки/отчёты/hosting и отдельно планируемую передачу Platega. Ссылки сохранены как материалы предыдущей версии; перед утверждением/публикацией привести описание покупки к §4 и заполнить реальные сведения владельца. Сейчас черновики не редактируются и не опубликованы. Карты, fake legal requisites, несуществующие Stripe/Paddle передачи, полный запрет возвратов, 24-часовой cutoff и запрет chargeback не добавлены.

Предлагаемые config names SUPPORT_USERNAME, SUPPORT_EMAIL: один реальный индивидуальный username и/или email владельца, предпочтительно оба если предоставлены. Группа не считается достаточной. Валидировать формат/безопасный URI, скрывать незаполненный способ, не подставлять testbot или примерный адрес. Пока контакт отсутствует — честное состояние для владельца; банковская готовность false. Сервер/документы/профиль должны использовать согласованный контакт, без секретов. Config сейчас не меняется.

## 22. PLATEGA BANK APPROVAL READY

[Отдельный checklist и данные владельца](../../design/mini-app-redesign-2026-10-02/PLATEGA-BANK-APPROVAL.md) — материалы предыдущей версии; актуальные требования задаёт этот раздел. Готовность требует фактических экранов и полного кликабельного пути из §4 на согласованном staging: конкретные цены Viewer150 ₽/месяц и Streamer300 ₽/месяц, все три способа с явным указанием провайдеров, доступные Privacy Policy и User Agreement до оплаты, хотя бы один реальный индивидуальный контакт поддержки. Также обязательны проверенные тарифы/серверный каталог, принятые и опубликованные документы, подтверждённые merchant условия и отсутствие утечек. Выбранную схему с принятым платформенным риском показывать банку открыто; не обозначать её как одобренную Telegram. Наличие локального макета/spec не закрывает пункты рабочего продукта. Сейчас NOT READY: контакты/реальные сведения/период/возвраты не заполнены, макет ещё не отражает три способа; отправка менеджеру не разрешена и не выполнялась.

Дополнить будущие тесты §18: оба priced CTA → все три способа → заглушка без payment request/grant; чтение обоих документов до оплаты; отсутствие fake support и скрытых способов; одна цена/period/catalog version в UI/order/legal с отдельным XTR-предложением, незаполненный contact или draft legal блокирует bank-ready; long legal text/text200/360/390/back/focus; Stars TBD не создаёт invoice.

## 23. Результат текущего этапа

Исследованы существующая основа и полный публичный API index; этот spec исправлен по прямому решению владельца о трёх способах внутри TwitchSignalBot / Mini App. Обновлены указатели статуса, предыдущий макет/документы/QA сохранены и не объявляются проверкой новой схемы. Код bot/tests/scripts/config, UI-макеты, БД, secrets и deployment не изменены. Preview остаётся локальным демонстрационным А. Финансовые/серверные права/настоящие Telegram сценарии нового этапа — **NOT TESTED**. Последующий промпт утвердил spec/plan как основу и разрешил P01–P21; эта прежняя остановка отменена. Текущие gates: payment OFF, bank NOT READY, native NOT TESTED до фактической проверки.
