# Недостающие данные для денежного запуска

Утверждённые решения сохраняются: Viewer150 ₽/месяц, Streamer300 ₽/месяц, Viewer включён для frozen beneficiary, Stars/СБП/карта внутри Mini App, Platega открыто названа, первое staging money OFF. Эти решения повторно не запрашиваются.

## Что должен предоставить или подтвердить владелец

1. Реальный личный SUPPORT_USERNAME и/или SUPPORT_EMAIL; группа не единственный контакт. Сейчас значения не предоставлены, defaults отсутствуют.
2. Данные оператора, необходимые для документов и выбранной формы деятельности: имя/наименование, применимые идентификационные и адресные сведения; подтверждение размещения и обработки данных. Не выдумывать юридическое лицо или ИНН.
3. Точное определение «1 месяц»:30 дней либо календарный месяц, версия правила и момент начала денежного доступа.
4. Правила возврата и порядок обращения; не подставлять blanket no-refunds/24h.
5. Правила chargeback и подтверждённого влияния на доступ; не подставлять запрет.
6. Viewer→Streamer upgrade: доплата/зачёт/срок и повторная покупка активного плана.
7. Две цены XTR для Stars. RUB15000/30000 не конвертировать самостоятельно.
8. Подтверждение merchant/test-среды и отдельный допуск будущих Platega/Stars денежных проверок. Для текущей заглушки X-MerchantId/X-Secret не нужны.
9. Применимая схема НПД/чеков, кто формирует и передаёт документ покупателю; журнал заказа не заменяет чек. [ФНС: вопросы НПД](https://npd.nalog.ru/faq/).
10. Реальные сроки хранения по категориям и окружению: подписки/настройки/OAuth, история/отчёты, платежи/аудит, обращения, технические logs и backups; порядок удаления и обязательного сохранения.

## Границы

Данные оператора/контакт/сроки и фактическое принятие владельцем конкретной редакции нужны для публикации Privacy/Agreement. Все manifest owner_accepted=false. Исходники подготовлены локально, публичные routes показывают недоступность до выполнения условий. Подготовку политики оператор сверяет с применимыми требованиями; [152-ФЗ, публикация Минтруда](https://mintrud.gov.ru/docs/laws/130).

Недостающие month/XTR/merchant/refund/upgrade блокируют денежную активацию. Они не блокируют интерфейс, тарифное предложение, bank purchase stub, staging и независимые проверки. Полнота bank package отдельно зависит от реальных документов/контакта; это не bank approval. Ничего не отправлять банку или провайдеру от имени владельца.

## Production cutover с платежами OFF — реальные недостающие входные данные, 04.10.2026

1. Подтверждённые exact production bot numeric ID/username; Railway project/environment ID+name/service/volume ID+instance/mount; нормализованный DB path внутри mount; HTTPS PUBLIC_URL, PORT и реальная одна replica. Исторические candidate IDs из staging_target.json требуют независимой сверки, не являются одобренным contract. OWNER_CHAT_ID425785231 уже известен; повторно не запрашивается. Testbot/staging данные не подходят. Contract без секретов хранить вне Git и закрепить SHA256: 2026-10-04-production-admission-contract.md.
2. Разрешённый isolated representative production snapshot, metadata/hash/size/provenance, пригодный exact old artifact; доступность **существующего** production encryption key через secret storage и расшифровка токенов на копии. Ключ/токены не присылать в чат/Git. Synthetic тесты и staging backup этот пункт не закрывают.
3. Внешнее backup хранилище/access/retention, проверенный download+restore и maintenance оператор/окно/STOP всех writers/exclusivity/rollback. Подтвердить production proxy/TLS/реальный request.remote и shared-peer quota: произвольный X-Forwarded-For не доверен; общий NAT/proxy делит лимит4/300s без eviction старых states. Требуется инфраструктурный preflight, не ослабление SEC-03.
4. Конкретно разрешённые smoke recipient/account/channel, предел отправок и OAuth; native acceptance нового artifact в Telegram Desktop/iOS/Android. Сейчас NOT TESTED. До отдельного разрешения реальные OAuth/send/media не выполняются.
5. После закрытия D/preflight/acceptance — отдельное прямое сообщение «Разрешаю production cutover». Текущая подготовка не разрешает deploy/config/production DB mutations.

Quiet/raid уже подтверждено D-048 и текущими тестами: личные live и raids подчиняются quiet, есть исключение стримера; channel publication отдельно. Новое решение не требуется. Viewer150/Streamer300 и Free50 сохраняются. Денежные и legal решения выше не закрываются этим payment-OFF checkpoint; от имени владельца ничего не принимается.
