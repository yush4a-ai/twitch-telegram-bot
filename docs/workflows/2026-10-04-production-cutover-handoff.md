# Production preparation — checkpoint 04.10.2026

**PRODUCTION PREPARED — OWNER INPUT REQUIRED. DEPLOY NOT STARTED. Cutover: NO.**

B1/B2/C закрыты локально; D не закрыт. Production source copy не предоставлена. Подготовка исправительного scope: **7/8 = 87,5% (≈88%)** — SEC-01/02/03, Retry, B1, B2, C; D открыт. Это счётчик восьми пунктов, не оценка готовности всего продукта. Native/owner/infra prerequisites дополнительно открыты. Production READY не заявляется.

## Точная версия

- Ветка: `autonomous/twitchsignal-roadmap`, база этого продолжения `b175782621e7aa818b4ca63d40927500a61d20f2`.
- Runtime SHA: `3fa8b649e737693eef94c3912c22313834f57586`.
- Первый полный test snapshot: `5474e5833bb8fc3eae3e1169f69c2bfe0ca55ff6`; RED1634PASS/2FAIL/2existingSkips/3741subtests. Обнаружены lifetime-RSS от buffered Git tar и устаревшее copy expectation. Исправлены offline tooling и усилен copy audit; Bot runtime не менялся. Финальный code/tool snapshot:869009401fc638405694297b8449e04c1d96abe8.
- Final HEAD закрепляется локальным tag `production-prepared-2026-10-04`. Exact SHA: `git rev-parse 'production-prepared-2026-10-04^{commit}'`; полный literal SHA также в финальном сообщении и материализованном handoff/manifest вне Git: `C:/Users/yusha/.codex/visualizations/2026/10/04/01a10670-d2b9-7420-96b0-f052f5135c96/production-checkpoint.json`. Сам документ входит в final commit, поэтому его content-addressed SHA записывается после commit во внешний manifest, а не выдумывается внутри него.
- Итоговый gate CONSOLIDATED PASS на869009401fc638405694297b8449e04c1d96abe8: полный1636PASS/2existingWindowsSkips/3739subtests/1136.57s, exit1 из-за двух byte-invariant subtests. Managed checkout с core.autocrlf=true добавил CR в .python-version/Procfile; immutable Git blobs и primary checkout уже имели правильные LF/хеши. Только validation bytes восстановлены из HEAD; свежий whole packaging15PASS/12subtests/0.40s, exit0. Код/tests/assertions/HEAD/deps/global Git config не изменялись. Scoped reviewer подтвердил reuse1636PASS без третьего full. Distinct consolidated1636PASS/3741subtests/2existingSkips; overlap15tests/10subtests не суммируется. FULL-BC.log/FULL-SECOND-RED.json сохраняют полный RED как RED, не fullPASS. FULL-GATE.json, PACKAGING-RECHECK.log и CHECKOUT-BYTES.json связывают correction и PASS. Первый code-related RED также сохранён. Далее только docs/evidence.
- Исторические audit REPORT/PLAN/SCENARIOS сохраняются. Текущий журнал: `CONTINUATION-PLAN.md`; свежий обзор: `FINAL-REVIEW.md` в том же audit folder. Общий аудит не повторялся.

Полный прогон выполнен в отдельном clean validation checkout того же коммита только для тестов, без второго разработчика. Чужие untracked output/imagegen assets в основной папке сохранены без move/delete/stage; вся рабочая папка с ними не объявляется clean. Материализованный handoff с literal final HEAD находится рядом с внешним production-checkpoint.json.

## Исправления и доказательства

| Этап | Результат | Проверка |
|---|---|---|
| B1 | D-048 не утверждает исключение raid. Сохранён runtime: личные live и raids подчиняются quiet; exemption стримера есть; channel publication отдельно. Исправлена только help/confirmation copy | commit9888455; RED2→24PASS, Free50/live/raid inside/outside/exemption/delayed fresh check/community |
| B2 | Pending updates не удаляются. Durable received до yield/ACK, atomic processing/done по bot+update ID. Done не выполняется повторно. Ordinary ambiguous→unknown без автоматического второго опасного действия; financial→retry через прежний ledger | commit5fdd5b2 + shared-budget correction в3fa8b64; restart/replay/callback/chat_shared/OAuth cancel/owned-task shutdown/shared32 и crash после реального ledger grant/refund PASS |
| C | Отдельный frozen/hash-pinned operator admission вне Git: local target/storage/config phase1, exact getMe phase2, OS exclusive writer lock, затем DB/migrations/menu/worker/routes. Отдельный queue opt-in после admitted | runtime3fa8b64; missing/placeholder/staging/wrong URL/path/bot/queue/replica, отсутствие DB side effects, второй writer STOP, credentials не включают деньги PASS |
| D tooling | Offline-only sealed SQLite backup API, hash/size/UTC/schema/rows, immutable source/no sidecars, migrations через Database.connect exact artifact, key/HTML readers, reopen и fresh restore в новый путь старым artifact после migration |12 synthetic tool tests в final focused PASS; stageD **NOT CLOSED**, source/key/external restore отсутствуют |
| Proxy | request.remote ownership сохранён, XFF/Forwarded не доверены; spoofed headers не обходят quota и не вытесняют valid state | local HTTP regression PASS; actual Railway edge/TLS/client boundary — OWNER INPUT/preflight |

Focused B/C: **110 passed /118 subtests**,80.94s; после полного RED и streaming-tool/copy correction: **22 passed /6 subtests**,19.27s, включая неизменный R9 RSS256MiB guard. Новых skips нет. RED/PASS логи сохранены; trailing whitespace удалён только для diffcheck. В scoped review исправлены подтверждённые recovery/cancellation/shared-budget и D Windows IOCP/WAL/order ошибки. Дополнительный review подтвердил buffered tar rootcause: Git snapshot347MiB при RSS лимите256MiB; exact archive теперь stream→exclusive TEMP file→digest/extract→удаление только своего файла. Ничего не исключалось из artifact и memory guard не повышен. Assertions не ослаблялись, skips не добавлялись, runtime/schema/payment guard не отключались.

B2 — at-least-once transport в пределах Telegram retention, не exactly-once SQLite+send. MemoryStorage FSM не durable: unknown требует operator review/нового действия пользователя. Не replay unknown callbacks массово; payload не логировать. Полный контракт: `2026-10-04-telegram-replay-contract.md`.

C template и точные обязательные env: `2026-10-04-production-admission-contract.md`. Required secrets только presence/format до getMe; настоящий key decryption доказывается отдельно в D. Одна replica в env не подтверждает Railway control plane; новый lock не останавливает старый artifact. Growth/site остаются отдельным staging-only контуром; новых public-site работ нет.

Money: effective `first_release_payment_policy` OFF, allow_invoice=false, allow_external_create=false. Публичный prepare unavailable, без order/checkout/grant; production не получает staging mock grants/trial. Credentials не монтируют live provider transport/routes. Реальные Stars invoices/Platega POST не выполнялись; финансовые tests используют только существующие sandbox doubles.

## Что требуется от владельца — пять групп

1. Подтвердить production bot ID/username, Railway project/envID+name/service/volumeID+instance/mount, DB path/PUBLIC_URL/PORT, одну replica и admission contract. Исторические candidate IDs не являются подтверждёнными значениями. OWNER_CHAT_ID425785231 известен, quiet/raid и цены150/300 не спрашиваются заново.
2. Дать разрешённый representative isolated production snapshot с manifest/provenance и доступность существующего encryption key через secret storage; подтвердить exact старый rollback artifact. Выполнить реальную D репетицию на этой копии. Staging/synthetic не заменяют её.
3. Выбрать внешнее backup storage и доказать download/hash/restore; подтвердить maintenance оператор/окно/STOP всех writers/exclusivity/rollback и production proxy/TLS/client-boundary preflight. NAT/shared proxy пока делит4states/300s, capacity429 без eviction.
4. Разрешить конкретные recipient/account/channel и лимит OAuth/send/media; пройти свежую native/owner acceptance Desktop/iOS/Android. Сейчас **NOT TESTED**.
5. Только после всех gates отдельное прямое сообщение **«Разрешаю production cutover»**. Подготовка этого разрешения не даёт.

Детали: `OWNER-INPUTS-FOR-LAUNCH.md`. Денежные/legal/bank условия остаются отдельными; документы от имени владельца не приняты.

## Migration, rollback, maintenance и STOP

Воспроизводимая процедура/CLI/authorization/schema-additions: `2026-10-04-production-copy-runbook.md`. Только отдельный каталог OS TEMP с разрешённым sealed source, без source WAL/SHM. Exact old/new artifacts экспортируются из commits. До миграции backup API→hash/size/integrity/FK/schema/counts/fingerprints; all legacy users/tracked/settings/routing/report/identity/community rows сохраняются. Existing key расшифровывает access/refresh без логов. Ожидаемые additions рассматриваются заранее, не принимаются автоматически по факту.

Migration: Database.connect only→close→integrity/FK/legacy columns+values/schema/additions→reopen. Потом закрытый новый path, sealed pre-migration backup→**новый** rollback path, exact SHA без stale sidecars, old artifact open→integrity/FK/rows/key/reader/HTML. Synthetic fixture подтверждает механизм, не полноту production категорий. Native HTML дополнительно проверяет оператор. Git revert не data rollback.

Будущий maintenance требует STOP polling, notification/preview/billing workers, OAuth/public writers, scripts и любых других DB процессов. Независимо доказать ноль старых writers/один maintenance owner и реальные replicas. Только после этого WAL checkpoint; busy/неизвестное ownership→STOP RELEASE. Active WAL/SHM вслепую не удалять. Backup на том же Volume недостаточен.

STOP при target/bot/artifact/mount mismatch, плохом key/hash/integrity/FK, потере/изменении legacy данных, неразрешённых schema additions, двух writers, unexpected money/send/auth bypass или duplicate mutation. При unknown external delivery сначала reconciliation. При data rollback сохранить post-failure snapshot; новые записи после snapshot нельзя молча потерять. Сверить совместимый old artifact и key перед любым будущим запуском.

Native checklist: `2026-10-04-production-native-acceptance.md` — все новые Desktop/iOS/Android сценарии NOT TESTED; browser/fake не подменяют их. Preview6→12→18→24→6/H.264/no audio/guard и старые HTML/groups сохранены.

## Production / staging

**Production untouched: YES. Payments OFF: YES. Cutover allowed: NO.** Не выполнялись deploy/config/production DB mutations, Railway commands, реальные Bot API/Twitch/OAuth/send/invoice/Platega операции; main/master не push/merge. Созданы только локальные TEMP DB и fake/local HTTP tests. Новые изменения не опубликованы даже на staging.

Исторически рабочий staging: [Testbot](https://t.me/TwitchSignalTestbot), [Mini App](https://worker-staging-2f74.up.railway.app/app); deployment5211c9f8-6a2f-4834-9aa6-88706a4a34a7/runtime39bc82171114a0a6b20ad4251bd68c4cc2d3f3f0. Новая live проверка здесь не выполнялась. Исторические снимки в audit folders относятся к прежним версиям, новых native screenshots нет. Старый production metadata466499d5/dc9239eb также не переверялся и не выдаётся за актуальный admission.

## Следующий шаг

Продолжать только D на отдельно разрешённой isolated representative source copy после owner inputs. Сначала сверить tag/exact HEAD/runtime/full evidence/clean tree, сохранить закрытые SEC/Retry/B1/B2/C и Free/HTML/groups. Не повторять общий audit и R/P/T этапы. Без source/key продолжать лишь разрешённые preflight/backup/native preparation. Production cutover не начинать без прямого сообщения владельца и реально закрытых D/infra/native gates.
