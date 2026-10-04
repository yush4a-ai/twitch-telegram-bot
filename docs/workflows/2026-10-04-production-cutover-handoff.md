# Production cutover: checkpoint 04.10.2026

**PREPARATION INCOMPLETE — DEPLOY NOT STARTED. Cutover: NO.**

Владелец разрешил завершать подготовку при малом остатке allowance и остановиться на проверенном checkpoint. В этой сессии закрыты SEC-01, SEC-02, SEC-03 и маленький UX blocker. Этапы B, C, D остаются открытыми. Не маркировать проект `PRODUCTION READY` до их завершения и проверки владельцем.

## Ветка и snapshot

- Final branch: `autonomous/twitchsignal-roadmap`.
- Последний commit с изменением runtime: `9e899300fbc222058a2ca746fb90336d78a8d2a5`. Полный suite запущен один раз на этом чистом committed snapshot.
- Final HEAD SHA: закреплён локальным tag `production-prep-partial-2026-10-04`; точный hash получить `git rev-parse 'production-prep-partial-2026-10-04^{commit}'`. Тот же полный SHA указан в финальном сообщении владельцу. Tag фиксирует финальный документальный commit; runtime SHA выше остаётся источником полного test gate.
- База задачи: `81e186ac2dddb88b0d29e131a8fb38ad61d87d0d`.
- Source of truth: `docs/audits/production-readiness-2026-10-04/REPORT.md` и связанные PLAN/SCENARIOS/LOCAL-ADMISSION/RUNTIME-PREREQUISITES/STAGING-READ-ONLY. Старый REPORT остаётся историей исходных findings.

## Сделанные изменения и точечные доказательства

| Пункт | Изменение | Commit / проверка |
|---|---|---|
| SEC-01 | add draft получает actor и 10-минутный expiry; прямой add flow проверяет автора/target/expiry и Telegram-права до lookup и после него непосредственно перед domain write | `51ffec7`; RED: 4 failures; PASS: 14 tests /2 subtests |
| SEC-02 | cheap tracked/limit check до Twitch; 6 follow requests/user/10s, 30 globally/10s; cache positive/negative 10s до256 entries; сериализация одинаковых lookup с5s timeout. Ограничитель относится только к Mini App follow и не оборачивает poller client | `911f126`; RED:4; PASS:22 tests /3 subtests |
| SEC-03 | hashed client ownership,4 outstanding widget states/client, global32 admin/128 streamer, expiry300s, reuse cookie state; новый запрос при заполнении получает429 вместо чужого eviction. Сессии streamer4/user,128 total, отзыв только собственного oldest, deny нового пользователя при capacity | `9a2383c`; RED:7 failures; PASS:30 tests /4 subtests |
| SEC-03 callback | Подпись и linked identity проверяются до расходования nonce; consume и session creation идут без await между ними. Неудачный unlinked login не портит следующую корректную попытку | Исходные HTTP assertions403→403→303→403 проходят. Один scoped reviewer проверил diff; полный scan не повторялся |
| UX | Первая сетевая ошибка streamer предлагает «Повторить»; повтор использует существующий abort/generation flow, disabled при загрузке. Текст не обещает прошлые данные, если их нет | `9e89930`; Python13 PASS /3023 subtests; browser error→retry→loaded Chromium/WebKit,390/1440,light/dark; см. уточнение QA ниже |

Логи RED/PASS и screenshot находятся в `docs/audits/production-readiness-2026-10-04/`. Reusable browser script: `scripts/production_retry_qa.cjs`; для установленного Playwright использовать `NODE_PATH=C:/Users/yusha/.agents/skills/playwright-skill/node_modules`, `RETRY_ENGINE=chromium` или `webkit`. Он использует fake API и local data modules, без сервера/Telegram/credentials. Native iOS/Android/Desktop Telegram и signed live API этим не подтверждаются.

Полный suite: **PASS —1582 passed,2 skipped,3656 subtests,1085.49s (18:05),exit0**. Лог `docs/audits/production-readiness-2026-10-04/FULL-PREP.log`, manifest `PREP-EVIDENCE.json`. Два исходных Windows skips сохранены, новые skips/assertion weakening не добавлялись. Команда соответствует test gate `scripts/staging_deploy.py`: `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider`; deployment script не запускался. Runtime (`bot`/`main.py`/config) и `tests` byte-identical source9e89930 во время и после прогона; после него менялись только QA fixture, browser evidence и документы, полный suite повторно не запускался.

QA уточнение: первый вариант screenshot fixture задавал атрибут темы без semantic tokens. Это обнаружено визуальным просмотром; fixture исправлен на существующий theme controller с assertion фактического colorScheme, повторные8 checks и screenshots PASS, mobile dark/desktop light просмотрены. Это ошибка isolated fixture, не изменение дизайна продукта. Исходный UX RED — отсутствие кнопки. При первой проверке успеха fixture также использовал неверный heading «Telegram-канал»; actual heading «Мой канал» восстановлен, assertions не ослаблены.

## Оставшиеся блокеры

1. **B/quiet hours + raid:** runtime и help расходятся. D-048 сохраняет старую семантику quiet-hours go-live/raid, но оставляет несоответствие на T12. `tests/test_mini_app_legacy_compat.py` утверждает блокировку личного raid во время quiet hours и fresh check после retry. Help обещает исключение. В этой сессии ни runtime, ни copy не менялись. Следующий исполнитель должен зафиксировать canonical решение с источником; при отсутствии утверждённого исключения сохранить существующий runtime и исправить copy. Проверить ordinary personal/live, raids с/без quiet, exemption, community separately, delayed retry/toggle.
2. **B/polling:** `main.py` по-прежнему вызывает `delete_webhook(drop_pending_updates=True)`. Не заменять флаг без regression-first плана retention/replay. Нужны replay-safe commands/channel selections и recovery границы. Stars ledger имеет local idempotency, но полная входящая processing/recovery стратегия не завершена. Деньги OFF не дают права обещать exactly-once Telegram delivery.
3. **C/admission:** безопасный production contract ещё НЕ реализован. Текущие staging guards не сняты. Production features выключены и queue ON приводит к ConfigError. Требования к реализации ниже; документация не является runtime guard.
4. **D/migration:** нет разрешённой репрезентативной isolated production copy и key recovery evidence. Active migration/rollback rehearsal, external production backup и остановка writers не выполнены. Staging backup не подставлять вместо production source.
5. **Release acceptance:** native, owner visual acceptance, разрешённые OAuth/channel/outbound/media smoke открыты. Эти результаты не подменять browser/fake evidence.
6. **SEC-03 proxy pre-flight:** per-client quota использует `request.remote`, без доверия произвольному X-Forwarded-For. При общем proxy/NAT клиенты делят лимит4 states/300s. До release проверить реальные client boundaries и выбрать проверенную trusted proxy конфигурацию либо скорректировать ownership contract regression-first. Старые valid states сохраняются даже при capacity.

Прогресс текущего исправительного scope: **4 из8 пунктов (50%)**: три SEC findings и UX закрыты; два B-дефекта, C и D открыты. Это счётчик обязательных пунктов этой задачи, не измерение готовности всего продукта или вероятности успешного deploy.

## Production admission contract, ещё без реализации

Новый продукт допускается только отдельным явным production opt-in, после совпадения всех ожидаемых значений. Отсутствие, пустое значение, placeholder, staging/testbot identity, несовпадение или невозможность проверки: ConfigError и остановка до открытия/migration БД, Telegram commands/menu, worker, OAuth/public routes, poller и исходящих запросов продукта.

Двухфазная проверка: сначала локальный immutable target/config, затем getMe для точного bot ID+username. Создать Bot и подтвердить identity **до** `Database.connect()`: сейчас main делает DB mutations раньше identity check. Нельзя просто разрешить production через `not railway` либо переименовать staging. First release payment policy сохраняется OFF независимо от env/secrets. Negative tests проверяют отсутствие DB/worker/menu side effects при wrong target/bot и каждый missing prerequisite.

Ожидаемые operator inputs, все обязательны:

| Поле | Что подтвердить |
|---|---|
| production bot | Числовой bot ID и точный username основного бота из проверенного getMe. **Не**8859004067/`TwitchSignalTestbot` |
| Railway target | exact project ID, environment ID+name, service ID, volume ID/instance ID, mount path; single worker/replica и эксклюзивные writers |
| DB | absolute normalized path строго внутри ожидаемого volume; реальный mount, существующий source DB, ownership/permissions и свободное место |
| Required secrets | TELEGRAM_BOT_TOKEN matches getMe; TWITCH_CLIENT_ID/SECRET; существующий TOKEN_ENCRYPTION_KEY decrypts старые token fields; ADMIN_PANEL_ACCESS_KEY≥32, OWNER_CHAT_ID, production admin widget username, PUBLIC_URL HTTPS/PORT |
| Queue | отдельный явный production queue opt-in, согласованный queue/worker режим, lease/recovery policy; ledger pending/leased/unknown и frozen recipient review |
| Money | effective first_release_payment_policy=OFF; allow_invoice=false, allow_external_create=false; billing prepare не создаёт checkout/order/grant; live provider routes не монтируются |
| Runtime | FFmpeg/ffprobe доступны в новом production artifact, preview contract6→12→18→24→6/H.264/no audio/size guard; approved resource limits |
| Artifact | утверждённый exact commit/tree/artifact digest, полные tests именно этого snapshot, совместимый rollback artifact |

`scripts/staging_target.json` содержит исторические production candidate IDs: environment `af6d873b-a2cf-45aa-be42-cd9efbd102a7`, volume instance `eded4a6e-c2c1-44ab-a238-b3c860632cee`, project `14282646-e318-4b80-b35d-4369270de255`, service `45e46f2a-dba3-4b18-bc5f-b6fafa260055`. Это **не подтверждённые inputs для нового admission**. Не брать testbot IDs или staging DB. Записанный `expected_production_branch=main` не разрешает push/merge main.

## OWNER INPUTS ещё отсутствуют

- Подтверждённые exact production identity/target/volume/DB/PUBLIC_URL и способ одноразового закрепления contract без секретов в Git.
- Разрешённый isolated source snapshot, его manifest/hash, внешний backup storage/access/restore, существующий encryption key через secret storage.
- Проверенный maintenance path: остановка bot/poller/worker/OAuth/прочих writers с сохранением доступа к volume; оператор, окно и rollback artifact.
- Решение по quiet/raid, если ранее утверждалось исключение, которого нет в прочитанном D-048.
- Конкретный разрешённый smoke recipient/account/channel, лимит отправок и native acceptance.
- Отдельное разрешение на production cutover. Эта задача его не даёт.

Сведения для реальных платежей/legal остаются в `OWNER-INPUTS-FOR-LAUNCH.md`; payment OFF release не закрывает bank approval, legal acceptance или реальную продажу. Настроенный support/legal без owner acceptance не выдумывать.

## PRE-FLIGHT

1. Проверить branch/clean tree, exact runtime/tests SHA; закончить B/C/D и scoped review. Полный gate после последнего runtime изменения; старый gate не покрывает новый код.
2. Получить отдельное разрешение владельца и заполнить inputs. Сверить Railway/бот/volume двумя независимыми read-only источниками, не выводя secrets.
3. Проверить negative admission matrix: wrong/missing bot,project,environment,service,volume,DB,key,queue,payment. Во всех случаях ноль пользовательских и DB side effects.
4. Подтвердить fresh consistent backup, hash, integrity, восстановление из внешнего хранилища, token decryption и rollback artifact. Закрыть isolated rehearsal ниже.
5. Установить maintenance window и наблюдение; проверить отсутствие second writer/replica. Проверить новые/старые pending report recipients и unknown jobs. Зафиксировать offset/update replay recovery решение. Все send проверки требуют конкретного разрешённого адресата.

## Migration на isolated copy и будущая production процедура

Эта процедура пока **не отрепетирована и не разрешает выполнять её на active production**.

1. Оператор получает consistent production snapshot через SQLite backup API; исходный файл не заменяется. Не копировать live DB/WAL/SHM последовательно как три независимых файла. `scripts/sqlite_backup.py` имеет staging `/data` guard: не отключать guard и не запускать его как production maintenance tool.
2. Сохранить sealed backup и manifest (source identity,UTC timestamp,hash,size,schema versions, таблицы/counts/ключевые ID, encrypted-field count) вне Railway Volume. Проверить скачанную внешнюю копию по hash и восстановить её в новый isolated путь; source остаётся read-only.
3. Для offline migration обеспечить остановку всех writers/worker, закрытие соединений и эксклюзивный filesystem lock. Подтверждённый checkpoint WAL выполняет только оператор в утверждённом maintenance path. Если checkpoint занят или ownership не доказан, STOP. Сохранить оригинальный recovery bundle; старые WAL/SHM не должны попасть к восстановленному файлу. Не удалять sidecars активной DB.
4. На isolated restored copy выполнить integrity_check=`ok`, foreign_key_check пуст; снять logical fingerprint legacy user/subscription/report rows и token fields. Существующий Fernet key должен расшифровать все legacy encrypted access/refresh fields; содержимое token не логировать. Ошибка ключа: STOP.
5. Запустить **только** `Database.connect()`/migration и закрыть БД; не запускать main/poller/handlers/network. Сверить expected schema versions и additive changes, старые IDs/row contents/subscriptions/report payloads и token plaintext equality в памяти. Закрыть и повторно открыть; integrity/FK и права Free/HTML/старых групп снова проверить.
6. Не импортировать staging grants/jobs/test recipients/Telegram file IDs/payment fixtures. Сохранять реальные source rows; случайную staging provenance обнаружить и остановиться, не чистить production молча.
7. Репетиция rollback: закрыть новый код; восстановить sealed pre-migration backup в **новый** isolated DB path без stale sidecars; открыть старым exact artifact/schema, проверить integrity, legacy rows/HTML и key decryption. R2 reader compatibility/materialized legacy samples проверить отдельно; Git revert не является data rollback.
8. Сохранить evidence stop/reopen/hash/decryption counts/migration/rollback/external restore. Только затем отдельное owner-approved production окно повторяет эти операции на точном target. Перед активной заменой DB ещё раз backup последнего состояния и эксклюзивная остановка writers.

## Deployment, только после отдельного разрешения

1. Начинать лишь после закрытия pre-flight и решения `PRODUCTION READY — DEPLOY NOT STARTED` с evidence.
2. Использовать утверждённый immutable artifact и явно pinned production target; staging deployment helper для production не использовать. Не push/merge main/master в рамках этой задачи.
3. Остановить старый writer, выполнить утверждённую migration и запустить ровно одну новую replica. Admission должен закончиться до DB/user side effects; payment OFF остаётся enforced.
4. Проверить фактические deployment ID, artifact/commit SHA, environment/project/service/volume/DB и getMe. HTTP200 не заменяет эти проверки.

## Post-deploy smoke

- health и worker/poller state; exact getMe и системный MenuButtonWebApp «Приложение».
- Schema/integrity/FK, preserved old subscriptions/reports/groups, existing Twitch token decryption без вывода значения.
- Неподписанные/expired/tampered запросы401/403, cross-user/admin denial, свежие Telegram-права и follow burst.
- По отдельному конкретному разрешению: Telegram /start→Меню→live→App→return, Viewer add/pause/delete, streamer error→retry, подключение/permissions; personal quiet/raid и community отдельно; photo/video fallback только в согласованном test scope.
- effective payment OFF; billing prepare unavailable и ноль invoice/external checkout/grant. Provider POST и реальные деньги не тестировать.
- Recovered pending updates не теряются и не дублируют grants/side effects; queue pending/leased/unknown и frozen report recipient сверены.
- Наблюдение latency/errors/retries/resources в согласованное окно. Native/device/media результаты, которые не выполнены, оставить NOT TESTED.

## Rollback и stop conditions

Остановить выпуск при mismatch identity/artifact/volume, неизвестном key/backup, integrity/FK failure, потерянных legacy rows, migration failure, неожиданных оплатах/отправках, auth bypass, повторных grants/side effects или двух writers. При unknown external delivery сначала reconciliation, не повторная отправка.

Rollback: остановить новую replica и все writers, сохранить свежий consistent post-failure snapshot для расследования, восстановить проверенный pre-migration backup без чужого WAL/SHM, запустить совместимый старый artifact, повторить identity/DB/key/health/разрешённый smoke. Не совмещать старый код с неизвестной новой schema. Учесть новые записи после cutover: восстановление старого backup может их потерять; оператор должен согласовать reconciliation до восстановления. Secrets сохраняются в secret storage; не копировать staging key в production.

## Production и текущий staging

В этой сессии production **не затронут**: не выполнялись deploy/config/DB writes, Railway commands, getMe/network к внешним API, owner OAuth, Telegram sends, invoice или Platega POST. Платежи **OFF**, staging guards сохранены. Создавались только локальные временные test DB/fake API.

Последнее внешнее состояние взято из предыдущего read-only audit, а не повторно проверено в этой сессии: Testbot `@TwitchSignalTestbot`, deployment `5211c9f8-6a2f-4834-9aa6-88706a4a34a7`, runtime `39bc82171114a0a6b20ad4251bd68c4cc2d3f3f0`; app `https://worker-staging-2f74.up.railway.app/app`. Исправления этой сессии не опубликованы. Старый production metadata: `466499d5-6979-4a81-8aee-67efcf628976` / `dc9239eb0b82fb80d1788fc657205740cebc49e9`, без новой live проверки.

## Точный следующий prompt

> Продолжи TwitchSignalBot на autonomous/twitchsignal-roadmap с чистого checkpoint. Прочитай docs/workflows/2026-10-04-production-cutover-handoff.md и checklist, актуальные STATUS/DECISIONS и исходный production-readiness REPORT. Сначала сверяй HEAD и final full-suite evidence, не повторяй общий audit. SEC-01/02/03 и UX уже исправлены, сохрани их и исторические HTML/groups. Закрой B: canonical quiet/raid из D-048/current tests/help и pending Telegram retention/replay regression-first. Затем C: отдельный fail-closed production admission с owner/operator inputs и identity до DB mutations, не снимай staging guards. Затем D: разрешённая isolated representative source copy, existing encryption key, внешний backup, exclusive stop/migration/reopen/legacy rows/rollback rehearsal. Никаких production deploy/config/DB writes, real sends, OAuth владельца, Stars invoice, Platega POST, денег, main/master push/merge. Payments OFF. По одному focused fix RED→PASS→review→commit; полный suite один раз после последнего runtime snapshot. При малом allowance остановись на clean committed checkpoint с updated handoff. Cutover только после отдельного явного разрешения владельца и закрытых gates. Не называй preparation PRODUCTION READY, пока открыты B/C/D/native/owner prerequisites.
