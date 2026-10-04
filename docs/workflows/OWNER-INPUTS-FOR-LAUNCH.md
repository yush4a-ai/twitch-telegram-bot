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

## Уточнение operator inputs после read-only preflight — 04.10.2026

Railway production IDs/mount/DB/URL/PORT и текущий artifact теперь обнаружены live, а не только исторически: D-INPUTS-PREFLIGHT.json и новый раздел cutover handoff. getMe подтвердил @TwitchSignalBot8707370390 одним источником; окончательный identity gate остаётся PARTIAL. Не запрашивать известные значения заново: оператор сверяет этот конкретный набор и предоставляет второй независимый источник bot identity/подписанный admission contract.

1. Предоставить sealed representative production snapshot + provenance/UTC/hash/size и authorization в private OS TEMP, утверждённые schema versions/additions и exact old/new artifact. Текущий old commit dc9239eb0b82fb80d1788fc657205740cebc49e9 подтверждён Railway и доступен Git; rollback suitability пока не проверена. D BLOCKED — REPRESENTATIVE SOURCE COPY REQUIRED.
2. Existing encryption key present, decrypt NOT VERIFIED. Разрешить его использование через secret storage на isolated copy; key/токены не присылать в чат. ADMIN_PANEL_ACCESS_KEY absent: оператору нужно подготовить его для будущего admission, без config writes в этой сессии.
3. Указать уже существующее external storage/access/retention/encryption и оператора download/hash/restore. Storage keys в service не обнаружены; наличие внешнего хранилища вне service cannot verify. Railway Volume не external backup. Подтвердить maintenance owner/окно/STOP writers, фактические mount permissions/free space и edge/TLS/client-boundary preflight.
4. Указать разрешённые account/recipient/channel и пределы OAuth/send/media для native Desktop/iOS/Android. Checklist и порядок готовы; сейчас NOT TESTED.
5. Только после D/identity/infra/backup/native gates отдельно «Разрешаю production cutover». Payment OFF сохраняется, реальные платежи этим checkpoint не разрешены.

## Актуальные оставшиеся входные данные после D CLOSED — 04.10.2026

Representative source, decrypt12/12, migration/reopen/fresh rollback exact old artifact, HTML reader1622 и первая external/local download/hash/restore копия теперь PASS; повторно snapshot/key/identity не запрашивать и D не перезапускать без нового изменения. Exact identity подтверждена getMe + polling log текущего deployment; ID/target — D-REAL-EVIDENCE.json и D-IDENTITY-SECOND-SOURCE.json. Эти факты заменяют прежние BLOCKED/PARTIAL записи выше, сохранённые как история.

1. ADMIN_PANEL_ACCESS_KEY: MISSING — REQUIRED BEFORE CUTOVER. Оператор готовит сильное случайное значение не менее32символов через secret storage; не передавать в чат/Git. В этой сессии значение не создавалось, variables не менялись.
2. Подтвердить конкретный production admission contract: найденные exact bot/target/volume/paths/URL/PORT, queue policy/opt-in, единственный writer и будущий maintenance owner/окно. Известные значения не спрашивать заново; owner approval contract ещё отсутствует.
3. Закрыть actual proxy/TLS/client-boundary/shared-peer quota, mount permissions/free space/STOP всех writers и backup retention/offsite/свежий snapshot перед cutover. Local sealed copy сохранена вне Git; её snapshot UTC не покрывает дальнейшие записи production.
4. Разрешить конкретные recipient/account/channel и пределы send/OAuth/media; пройти native Desktop/iOS/Android и owner HTML/visual acceptance. Сейчас NOT TESTED.
5. Только после этих gates отдельное «Разрешаю production cutover». Эта D сессия deploy/restart/production variables/migration/send/OAuth/платежи не разрешает; payment OFF сохраняется.

## Final preflight: что осталось — 04.10.2026

1. Решить scoped staging packaging blocker323153880bytes/File too large; новый prepared runtime пока не опубликован. После двух failures retries остановлены, без runtime урезания.
2. Рассмотреть конкретный known-values admission draft: 2026-10-04-production-owner-admission-draft.md, OWNER APPROVAL PENDING; отдельно решить queue enabled initial policy. ADMIN key operator step после approval перед cutover, не менее32 strong random chars, только secret storage, presence/length verification без значения. Сейчас не генерировался/не установлен.
3. Закрыть actual proxy/client boundary (TLS/HTTPS/redirect уже PASS), внешних writers/exclusive maintenance и fresh cutover backup/offsite/retention/reconciliation. Mount/free≈382MiB/permissions/one-instance evidence есть; storage резерв не обещан.
4. Native Desktop/iOS/Android/owner visual и безопасный signed smoke остаются NOT TESTED, native отложено владельцем. Manual checklist готов; OAuth/внешний канал требуют отдельных permissions.
5. После всех gates отдельное «Разрешаю production cutover». Production untouched, payments OFF, D CLOSED не перезапускать.
