## D-055 — 04.10.2026: фиксы аудита выложены в production

Владелец прямо разрешил выкладку в главный контур. Источник — коммит `7a1abb5` ветки `fix/audit-quiet-chats-and-backups`, собранный из exact Git blobs компактным пакетом (165 файлов, 9 595 029 байт), без правок админ-панели второго исполнителя. Перед выкладкой снята согласованная копия базы (SQLite backup API, `integrity ok`, внешняя копия SHA256 `81840b82…`) — простое скачивание файла дало несогласованную копию и было отклонено проверкой. Deployment `6d2ab85d…` SUCCESS, прежний `44fe69d3…` сохранён как точка отката. Подтверждено: `getMe`=`@TwitchSignalBot`, health/app 200, unsigned 401, legal 503, БД `integrity ok`/FK0/28 миграций, платежи и очередь OFF, 0 отправок пользователям, 17 подписок недоступных чатов отключены одним действием, 0 traceback'ов Twitch-авторизации, первая авто-копия `auto-20261004T181345Z.db`. Production-код деплоится не из GitHub `main` (она отстаёт на `dc9239e`), а из локального снапшота — это зафиксировано как фактическая процедура релиза.

## D-054 — 04.10.2026: закрытие находок аудита production в коде

По прямому указанию владельца закрыты три находки read-only аудита production, при жёстком условии: ни одного сообщения пользователям и в каналы. Сбор фолловеров без пользовательского токена переведён на `debug` вместо traceback; при Forbidden с признаками недоступности чата подписки этого чата выключаются одним действием с единственной записью в журнал (ошибки прав этого не делают); добавлен `bot/db_backup.py` — ежедневная онлайн-копия базы с ротацией и защитой чужих файлов, подключённая таском в `main.py`. Тесты: 15 новых passed, связанный набор 381 passed/129 subtests. Выкладка в production не выполнялась: production собирается из GitHub `main`, и по действующим правилам нужен отдельный deploy-разрешение владельца. Момент и порядок выкладки дополнительно ограничены тем, что 4 личные подписки находятся в состоянии «эфир идёт, пост не опубликован»; логика continuing-сессий неизменна, поэтому перезапуск не создаёт новых сообщений.

## D-053 — 04.10.2026: переезд staging-бота на @SignalStreamsBot

Владелец создал нового бота `@SignalStreamsBot` и поручил переезд. Причина: базовый username бота в Telegram изменить нельзя (ни бесплатно, ни платно; Fragment даёт только дополнительный коллекционный username), а `Twitch` в имени нарушает товарный знак Twitch. Staging-идентичность переносится на нового бота: обновлены staging-guard'ы (`main.py` menu button/identity/R8/mock checkout), `bot/growth_site.py`, `telegram_help.py`, `/invite` в `bot/handlers/streams.py`, go-live E2E, R9 guard и browser helpers, runbook и README. `bot/production_admission.py` теперь отвергает оба staging-username, production-имя и `bot/deep_links.py` не менялись. Focused gate 443 passed/183 subtests. Railway variables, BotFather-настройки, права бота в каналах, бренд-тексты «TwitchSignalBot» и полный gate остаются отдельными шагами; production не изменялся.

## D-052 — 04.10.2026: compact exact runtime upload, staging PASS

Owner attachment0717b9ee прямо разрешает packaging implementation и один guarded pinned staging deploy. Full source сохраняется; canonical allowlist включает весь bot, docs/legal, build configs и фактически используемый scripts/staging_target.json. Every uploaded byte — exact Git blob; manifest вне upload, gate50MiB до materialization. Runtime9584073bytes вместо full source364424250bytes/rejected323153880bytes. One read-only reviewPASS и один полный gate на committed code snapshot, без bypass/новых skips. Compact upload first attempt accepted, fresh deployment SUCCESS и164actualhashes verified. Production untouched, paymentsOFF, D CLOSED; native postponed, cutoverNO. Статус PRODUCTION PREPARED — OWNER FINAL ACCEPTANCE REQUIRED; остаются5 gates из current handoff. Existing Config-as-Code CLI deprecation warning сохранён в log; автоматической migration конфигурации в этом scope нет.

## D-051 — 04.10.2026: native отложено, staging upload STOP

Owner отложил UI/native проверку, поскольку работает за компьютером; Computer Use inputs прекращены. Background pinned staging release разрешён, production read-only. Два upload failures/File too large323153880bytes — retries STOP, scoped read-only reviewer; exact artifact не урезать и старый stage не подставлять. Runtime/code/tests unchanged, full/D не повторять. Mount/TLS metadata PASS, actual request.remote/external writers PARTIAL. Draft admission owner approval не подделывать; ADMIN только будущий operatorsecret step, свежий cutoverbackup отдельный future gate. D CLOSED/private sealed copy сохранить; cutover NO.

## D-050 — 04.10.2026: реальная D закрыта, final preflight остаётся

Owner attachment5cea7fa8 прямо разрешает readonly consistent snapshot и isolated rehearsal, отменяя прежний source-copy blocker. OS uid65534 без source write permissions защищает active DB/WAL/SHM; копия только ephemeral /tmp, downloaded original sealed/private вне Git. Первая external/local copy допустима этим owner prompt и проверена download/hash/restore. D CLOSED не даёт production READY/cutover: ADMIN/admission/infra/native/отдельное разрешение открыты. Long-term backup policy и свежий cutover snapshot/reconciliation обязательны отдельно. Identity теперь два источника getMe+current deployment polling log. Runtime/tooling unchanged, focused12PASS; full не повторяется. Exact artifact Windows pathlength решён short private path, не code/global setting changes.

## D-049 — 04.10.2026: узкая preparation без production cutover

Read-only metadata/getMe разрешены текущим запросом владельца, production writes/deploy/send/OAuth запрещены. Подтверждение одного getMe не закрывает требование двух источников identity. Present key не доказывает decrypt; отсутствие storage variables не доказывает отсутствие внешнего storage. Representative copy не предоставлена, поэтому D BLOCKED; staging/synthetic не подставлять. Runtime не изменять и full не повторять; packaging exit0 сохранён. Старый tag сохраняется, literal final HEAD материализуется вне Git после docs/evidence commit.

# Решения TwitchSignalBot

## Аудит готовности — 04.10.2026

По запросу владельца выполнена оценка всего продукта по подсистемам и полный source-проход текущего bot/main runtime:137/137 текстовых файлов;155unique tracked вместе с operator/config. Независимые reviewers последовательно, три уникальных security findings medium1/low2; весь repository security coverage partial. Итог REPORT.md/SCENARIOS.md в docs/audits/production-readiness-2026-10-04/. Это оценка, не разрешение переноса или изменения продуктового scope.

Source3dc9aad/runtime39bc821/Testbot5211c9f8; fresh read-only identity/assets/DB/menu/paymentOFF/production unchanged PASS, suite1570PASS/2existingSkips/3646subtests переиспользован после runtime/tests identity. Перед всем продуктовым переносом остаются дефекты прав/доступности, raid/copy/startup updates, production admission, migration/active rollback, native/media и отдельная реальная платёжная/документальная готовность. Дизайн, Free/HTML/export/старые группы и Stars/СБП/card inside Mini App150/300 сохранены. Product/config/DB/deploy не изменялись; только audit docs/helpers. Production/OAuth/outbound/payment допуски не предполагаются из аудита.

## Оформление описаний — 04.10.2026

Замечание владельца «сплошной текст» применяется ко всем пользовательским описаниям. Telegram использует короткие заголовки, отдельные нативные блоки фактов/условий и действия в кнопках; это не global replace и не новый Mini App redesign. Тариф формируется из тех же четырёх групп каталога; продуктовые условия, цены150/300, платежиOFF, права, бесплатные отчёты и резервный HTML неизменны. Цвет нативных блоков выбирает клиент Telegram; browser preview не считается native acceptance. Публикация только на pinned Testbot после полного guard точного commit; два старых прерванных full runs не являются PASS.

Записываются только подтверждённые решения. Новые продуктовые/коммерческие параметры здесь не предполагаются.

## 03.10.2026 — завершение Telegram T4–T8 на Testbot

Владелец сохранил разрешение на весь план после M1. Release00c6093/deployment603eeb44 опубликован после полного1541PASS/2existingWindowsSkips/3638subtests и actualsmoke195runtimehashes/menu/бот/paymentOFF/productionunchanged. Новых картинок не генерировали: три согласованных PNG сохранили byteidentical, сложные инструкции вынесли в текст и on-demand guide/help. Пять новых regression scenarios закрыли deletion races и paginated import/add results. FreeHTML/export, цены150/300 и канальные права сохранены. Native/owner visual acceptance и адресные внешние проверки не объявляются пройденными. Ветка и отчёты сохраняются; push/merge/production не выполнялись. Evidence RELEASE.md и SCENARIOS.md в docs/audits/telegram-journey-final-2026-10-03.

## 03.10.2026 — M1: фото, когда видео недоступно

Промежуточный выпуск выполнен: Testbot160703a/deploymentac62b912, full1517PASS/2existingWindowsSkips/3638subtests, actual smoke PASS. Readonly staging подтвердил photo для существующих эфирных сообщений28/29; ручных отправок не было. Native AFTER остаётся NOT TESTED из-за активного ввода владельца/перекрытого окна. T4–T8 сохраняются отдельными незавершёнными задачами; срочная правка M1 их не подменяет. Полные доказательства: `docs/audits/telegram-journey-2026-10-03/M1-RELEASE.md`.

Владелец подтвердил: при отсутствии доступного видео эфирный пост должен показывать фото. Registered Telegram-каналы получают лёгкую Twitch-thumbnail через текущий sender/updater, с публичной подписью и действующим template; legacy группы сохраняют прежнее поведение. Animation→photo после потери effective entitlement/выключения или capacity fallback; raw preview_enabled не выдаёт права. Переносим verified production fixes4ce3887/dc9239e только в тестовую ветку. Диагностика production/Telegram history только чтением; чужие identity/community/grants не добавляются для получения видео. M1 — безопасный промежуточный выпуск текущего плана T0–T8 после штатного полного guard; затем продолжаются T4–T8. ОплатаOFF/production/HTML/export сохраняются. Native screenshot/внешние тестовые отправки без отдельного разрешения не объявляются проверенными.

## 03.10.2026 — главная и жесты: выпуск560f3cc

Общий home hero и последующие замечания к swipe/Undo опубликованы на pinned staging: SHA560f3ccac05fe8f6c4b1221a28b926b7358edfef/treea8d56fd8acee41c2523313f600b9c7cf3c5118bd/deployment631a94d5-f6a2-4d11-b10d-d620c8f6dc07. Full1491/2existing skips/3634subtests и default232browser checks каждый движок,192runtime/17HTTP assets actual smoke PASS. Все изменения UI, schema/backend и serverUndo60s не менялись. Toast6s не исчезает при фокусе/запросе; свайп28px раскрывает кнопку и сохраняет простые нажатия/вертикальную прокрутку. Отдельное удаление остаётся обязательным. Owner native acceptance ожидается; production/payment/legal readiness не объявлены изменёнными. Последний handoff: docs/audits/mini-app-owner-corrections-2026-10-03/HOME-STATES.md.

## 03.10.2026 — выпуск правок и замечание к главной без эфиров

Последующий пакет владельца включает общий home hero, лёгкий свайп и краткое уведомление удаления. UI Undo показывается6s; server token60s/ownership/one-use/limits не меняются. Жест раскрывает кнопку с28px и сохраняет scroll/tap; удаления одним свайпом нет. Новая default browser matrix включает home иinteraction сценарии:232PASS/58PNG каждый движок. Новый full gate/deployment остаются обязательны; это пока локальное изменение.

Release `720962b`/deployment `00481223-a132-4903-b3b2-5db606899f89` подтверждён actual smoke; full suite1491/2existing skips/3627subtests,192runtime hashes/17HTTP assets PASS. После HTTP413 разрешённая staging-выкладка повторена на том же неизменном SHA с исключением только docs/audits и docs/design из временного пакета; полный path/hash manifest сохранён, permanent skip-tests не добавлен. Проверочный oracle CRLF уточнён по настоящему handler с RED→GREEN и одним read-only reviewer; приложение для этого не менялось.

Владелец показал реальную главную без live: персонаж отсутствует. Сравнение live с no-live было неполным. Продолжаем исправление общего оформления во всех загруженных состояниях без вымышленных эфиров и статуса уведомлений; старая матрица не является доказательством нового кода. Подробности и SHA/tree: docs/audits/mini-app-owner-corrections-2026-10-03/RELEASE.md. Native visual acceptance остаётся открыта.

## D-001 — автономная работа ограничена staging

- **Дата:** 2026-09-30.
- **Решение:** R0–R9 разрешено разрабатывать и проверять локально и в Railway staging / `@TwitchSignalTestbot`; production deploy, production DB/variables/data и push/merge ветки, автоматически деплоющей production, запрещены.
- **Причина:** прямое разрешение и граница пользователя; production Railway сейчас связан с GitHub `main` (`railway status --json`, R0 baseline).
- **Последствие:** изменения ведутся в `autonomous/twitchsignal-roadmap`; каждый staging deploy требует diff, тестов и явной проверки target environment.
- **Пересмотр:** только после отдельного разрешения пользователя на production действие.

## D-002 — R5 строится с mock provider

- **Дата:** 2026-09-30.
- **Решение:** R5 включает provider-agnostic billing, order/payment/entitlement, idempotency, webhook verification interface, mock checkout/refund/cancel/expiry и audit log. Реального provider и реальных денег нет.
- **Причина:** прямое решение пользователя; юридические, возрастные и платформенные условия остаются открытыми в roadmap.
- **Последствие:** mock payment никогда не обозначается как реальная оплата; UI и данные тестового контура должны иметь явную маркировку.
- **Пересмотр:** отдельное решение пользователя после проверки правил и юридической возможности.

## D-003 — live preview ограничен 24 секундами

- **Дата:** 2026-09-30.
- **Решение:** действующий цикл Telegram Animation: 6 → 12 → 18 → 24 → reset; H.264 MP4 без аудио и защитный бюджет размера около 10 MiB.
- **Причина:** `6074744`, тесты preview provider и явно утверждённый `docs/ROADMAP.md`.
- **Последствие:** новый код и тесты не возвращают cumulative 30 секунд.
- **Пересмотр:** отдельное продуктовое решение и staging evidence.

## D-004 — PostgreSQL только по измерениям

- **Дата:** 2026-09-30.
- **Решение:** SQLite остаётся исходной моделью; переход на PostgreSQL не считается обязательным до измерений R3/R9.
- **Причина:** `Database` использует одно соединение и WAL, но реальная latency/capacity ещё не измерена; roadmap требует evidence.
- **Последствие:** сначала synthetic workload, DB latency, growth и backup/restore; затем архитектурный выбор.
- **Пересмотр:** измеренные показатели или новое функциональное требование к многопроцессной записи.

## D-005 — HTTP health и preview health разделены

- **Дата:** 2026-09-30.
- **Решение:** `/healthz` отражает основной poll/EventSub runtime и не падает из-за optional preview; R2 показывает preview отдельно.
- **Причина:** фактическая семантика `bot/oauth.py:97-159`, `bot/poller.py:410-441` и утверждённое требование R2.
- **Последствие:** HTTP 200 нельзя выдавать за исправность preview или Telegram E2E доставки.
- **Пересмотр:** если появится отдельный проверенный SLO readiness для preview.

## D-006 — staging deploy загружает только commit snapshot

- **Дата:** 2026-09-30.
- **Решение:** `scripts/staging_deploy.py` после полного тестового gate собирает `git archive` проверенного SHA в Windows TEMP и передаёт его Railway с `--path-as-root` и явными project/environment/service ID.
- **Причина:** Railway CLI 5.63.1 дважды остановился до создания deployment при индексации рабочей директории (`prefix not found`, затем `os error 5`). Архив из commit исключает локальные `.env`, DB, логи и ignored/untracked файлы; тест подтвердил состав.
- **Последствие:** staging deploy воспроизводится из чистого commit. Нулевой код выхода `railway up` сам по себе не доказывает terminal `SUCCESS`; guard ждёт активного `SUCCESS`, а runbook требует независимый HTTP smoke.
- **Пересмотр:** при переходе на проверенный staging CI/IaC с эквивалентным target и secret gate.

## D-007 — staging healthcheck задаётся конфигурацией текущего deployment

- **Дата:** 2026-09-30.
- **Решение:** только `environments.staging.deploy` в `railway.json` задаёт `/healthz`, timeout 300 с, draining 30 с и overlap 0. Guard проверяет точное содержимое файла и Railway `propertyFileMapping` активного deployment.
- **Причина:** `railway environment edit` вернул `No changes to apply` без сохранения полей; успешный staging deployment `e6cb7283-087d-4e76-8fb7-002c059c11d9` отметил все четыре пути file override, оставив базовые поля service равными `null`.
- **Последствие:** конфигурация действует на конкретный deployment, production service settings не менялись. До 2026-12-01 требуется перейти с устаревающего Railway Config as Code на Infrastructure as Code после проверки изоляции окружений.
- **Пересмотр:** после миграции на новый Railway IaC или безопасный environment-specific settings API.

## D-008 — online backup и отдельный restore drill перед миграциями

- **Дата:** 2026-09-30.
- **Решение:** staging SQLite резервируется через `sqlite3.Connection.backup()` в новый файл, проверяется `integrity_check` и восстанавливается только во временную DB. Автоматического перезаписывания активной DB нет.
- **Причина:** на staging `2026-09-30-r1-7b5862d.db` создан с `integrity=ok`; отдельное восстановление подтвердило 21 таблицу и 360 448 байт.
- **Последствие:** перед существенной schema migration нужен также внешний staging snapshot/export, потому что backup лежит на том же Volume. Откат активной DB допускается только offline с исключительным доступом к Volume.
- **Пересмотр:** после появления проверенного maintenance path и внешнего хранения backup.

## D-009 — R2 строится как браузерная owner-панель

- **Дата:** 2026-09-30.
- **Решение:** R2 должен дать адаптивный браузерный read-only интерфейс владельца с безопасным доступом на staging. Telegram `/stats` и `/health` остаются дополнительными командами и возможными источниками метрик, но не заменяют панель.
- **Причина:** утверждённый владельцем workflow addendum `docs/workflows/2026-09-30-autonomy-design-testing.md` и project `AGENTS.md`.
- **Последствие:** отдельная spec/plan определит auth, сбор метрик, UI states и browser QA. Если реальный owner credential не доступен, использовать явно тестовый fixture и пометить deployed login как непроверенный, продолжая независимую работу.
- **Пересмотр:** только после нового продуктового решения владельца.

## D-010 — нормальный вход в R2 требует подписанный Telegram ID владельца

- **Дата:** 2026-09-30.
- **Решение:** вход из личного чата `@TwitchSignalTestbot` использует проверенный сервером Mini App `initData`; обычный браузер — Telegram Login Widget с проверенной сервером подписью и одноразовым CSRF state. Обе ветки требуют `user.id == OWNER_CHAT_ID=425785231`. Кнопка и команда `/admin` показываются только в личном чате этого ID; общие команды и меню не содержат админ-вход. `ADMIN_PANEL_ACCESS_KEY` остаётся скрытым аварийным fallback на отдельном route до реального Telegram E2E на staging.
- **Причина:** уточнение владельца перед R2 acceptance и официальные правила проверки [Mini App initData](https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app) и [Telegram Login Widget](https://core.telegram.org/widgets/login/#checking-authorization).
- **Последствие:** прямой URL и подписанный чужой ID не открывают данные; на staging это подтверждено отрицательными тестами и синтетическими подписями. Ранее browser widget сообщал `Bot domain invalid`; 2026-10-01 владелец сообщил о выполненном `/setdomain` для testbot и открывшемся Login popup. Полный вход его аккаунтом ещё не подтверждён; удалять fallback до реального E2E нельзя.
- **Пересмотр:** после фактического входа владельца через Telegram на staging и отдельного решения об удалении аварийного механизма.

## D-011 — общие stream observations с точной привязкой к poll

- **Дата:** 2026-09-30.
- **Решение:** новый logical stream хранит один набор Twitch-полей за poll в `stream_observations` и отдельную компактную membership для каждого реально обработанного destination. Обе таблицы `WITHOUT ROWID`; уже активный stream с `stream_samples` остаётся целиком в legacy режиме. Старый формат и таблица сохраняются.
- **Причина:** интервал first/last включал бы sample пропущенного poll в чужой отчёт. Тесты проверили позднее подключение, пропуск poll, legacy stream, rollback при ошибке записи и retention. Парный локальный synthetic профиль на 10 000 назначений дал 39.348 → 19.846 с и 4 595 712 → 3 395 584 байта DB, при WAL 4 144 752 → 4 486 712 байт; см. `docs/audits/2026-09-30-r3-shared-observations.md`.
- **Последствие:** staging migration остаётся additive и выполняется только после полного gate и проверки target; реальная Telegram нагрузка, preview и решение SQLite/PostgreSQL ещё требуют R3/R9 измерений.
- **Пересмотр:** после staging fan-out/retention smoke и синтетических 20k/30k/40k профилей.

## D-012 — очередь уведомлений использует аренду с номером попытки

- **Дата:** 2026-09-30.
- **Решение:** `notification_jobs` выдаёт due jobs ограниченными пакетами под короткий lease; уникальный ключ делает повторное enqueue идемпотентным. `ack`, `defer` и `fail` принимают `attempt_count` текущей выдачи и не меняют job после повторного claim. Повторно доступный просроченный lease виден в backlog метриках.
- **Причина:** локальные тесты подтвердили restart recovery, конкурентные claim на одном и двух SQLite соединениях, старое acknowledgement, retry и terminal failure. Запросы счётчиков используют индексы status/due/lease и не раскрывают chat ID, login или текст ошибки в owner health.
- **Последствие:** модель очереди локально готова, но Telegram worker и staging cutover ещё не включены; текущая доставка остаётся прежней до отдельных TDD и полного staging gate. Retention удаляет только старые terminal jobs; незавершённые сохраняются для recovery.
- **Пересмотр:** после измерений worker latency, Telegram rate limits и восстановления на staging.

## D-013 — rollback R3 должен учитывать формат samples

- **Дата:** 2026-09-30.
- **Решение:** staging R3 схема и writer допущены после полного gate и внешнего backup. Возврат только старого R2 кода после появления `stream_observations` не считать безопасным откатом: его reader видит лишь `stream_samples`. Для rollback требуется остановить staging запись и материализовать shared rows в legacy формат с проверкой количества и целостности до запуска старого кода.
- **Причина:** deployment `8d44e632-ceca-42ba-8cf1-1711b830cb46` достиг `SUCCESS`, DB версии/`integrity_check` проверены, но новый формат меняет семантику чтения отчётов. На момент smoke shared observations = 0; позже они могут появиться при live.
- **Последствие:** до проверенного offline maintenance path автоматический code-only revert R3 не применять. Backup/export и ограничение записаны в `docs/audits/2026-09-30-r3-staging-migration.md`.
- **Пересмотр:** после теста обратной материализации и offline rollback drill на staging.

## D-014 — go-live cutover включается только на pinned staging

- **Дата:** 2026-09-30.
- **Решение:** `NOTIFICATION_QUEUE_ENABLED=1` допустим локально и только при совпадении Railway project, environment и service ID с `scripts/staging_target.json`. При нём новый go-live transition и job записываются одной SQLite транзакцией; отдельный worker отправляет пост. Ожидающий job не запускает legacy sender в повторном poll. После завершённого stale job повторное включение уведомлений может воспользоваться прежним resume путём.
- **Причина:** тесты проверили атомарность при DB ошибке, retry/terminal/stale, гонки worker CAS с live/offline poll, позднюю отмену подписки и отсутствие per-destination query к очереди. При завершении job между двумя снимками poller перечитывает текущий message ID перед legacy resume, чтобы не создать дубль. Worker ждёт первого sample, чтобы не публиковать ошибочные 0 зрителей. По [Telegram Bot FAQ](https://core.telegram.org/bots/faq) старт отправок worker ограничен до 25/с глобально, 1/с в личном чате и интервалом 3,1 с в группе; остальные пути отправки пока не разделяют этот бюджет.
- **Последствие:** production config отвергает флаг даже при ошибочно заданном имени окружения; на pinned staging флаг 1 после gate и deployment `e757b5e5-ba83-4be7-a4cb-cd1d5737b0db` (`SUCCESS`). Synthetic worker claim/ack и один настоящий testbot go-live send на временной DB прошли: job `done` за одну попытку, пост удалён. Обновления уже существующих live-постов и offline cleanup остаются прежними до следующего пакета.
- **Пересмотр:** после staging mixed-load, lease recovery и измерений backlog/latency.

## D-015 — SQLite остаётся на staging до полного R9 load/recovery evidence

- **Дата:** 2026-09-30.
- **Решение:** не мигрировать на PostgreSQL сейчас. Сохранить одну сериализованную SQLite запись и одну staging replica с Volume; продолжить измерения и проверку восстановления.
- **Основание:** локальные синтетические профили в `docs/audits/2026-09-30-r3-results.md`: shared sample path на 20k/30k/40k, fake preview 1/2/4 и durable queue до 10k jobs без сети. 10k jobs завершились без потерь; DB-only drain 28,96 с, но установленный Telegram budget 25 стартов/с задаёт минимум 400 с для 10k одновременных sends. PostgreSQL сам по себе не снимет этот внешний предел.
- **Ограничение:** это один прогон каждого локального профиля без FFmpeg, Telegram, настоящего poller и одновременных mixed workloads. Вывод не подтверждает SLA 40k пользователей, безопасность горизонтального scaling или отсутствие будущего DB bottleneck.
- **Пересмотр:** R9 full mixed load, WAL/lock/CPU/RAM/disk, recovery и staging testbot delivery; перейти к PostgreSQL по измеренному DB bottleneck, а не по числу пользователей в одиночку.

## D-016 — offline cleanup переносится из poll cycle в staging queue

- **Дата:** 2026-10-01.
- **Решение:** при staging queue flag после grace периодически ставить due offline cleanup jobs одним индексированным `INSERT SELECT`. Уникальный ключ включает message ID как `payload_version`: новый пост после reconnect получает новый job, старый worker проверяет stream и message перед действием. Личная карточка редактируется в ended через media-aware updater, публичный пост удаляется под post lock; transient error откладывает job.
- **Основание:** TDD тесты grace, идемпотентности, stale target, private/public путей, retry и батчевой постановки; gate 1011 passed, 2 skipped, 265 subtests. Deployment `d05d8d5d-56c9-4c51-abff-0fb804cd0431` terminal `SUCCESS`; staging testbot E2E: go-live и cleanup jobs `done`, карточка `ended`, тестовый пост удалён.
- **Ограничение:** это одиночный тестовый lifecycle; mixed-load fan-out, общий Telegram budget для старых путей и code/data rollback ещё не проверены.
- **Пересмотр:** после R3 live update coalescing, staging mixed-load и recovery.

## D-017 — live post refresh объединяется по текущему message ID

- **Дата:** 2026-10-01.
- **Решение:** только при staging queue flag poller сохраняет stream sample и увеличивает revision одного `live_update` job для текущего `(chat, login, logical stream, message ID)`. Worker берёт свежий sample, редактирует content через существующий post lock и CAS, затем при необходимости применяет thumbnail. Revision, изменившаяся во время lease, переводит тот же job обратно в pending; Telegram `RetryAfter` задаёт due без сна poller. Для flag 0 прежний direct path сохранён.
- **Основание:** TDD на coalescing, fenced ack/terminal, stale reconnect/offline, photo/animation, latest sample и RetryAfter; полный gate 1024 passed, 2 skipped, 265 subtests. Внешний staging snapshot и локальный migration/restore drill прошли; deployment `8a701528-dd3a-4252-b4cd-8b61b6c110fc` terminal `SUCCESS`; temp-DB testbot E2E подтвердил go-live, live edit и ended edit.
- **Ограничение:** один live update job может сделать content edit и thumbnail edit в пределах одного worker слота; Telegram `RetryAfter` защищает фактический лимит, но общий rate budget с legacy/report/preview путями ещё не объединён. При crash после Telegram edit до ack возможен повторный edit. Mixed-load latency и lease recovery на staging остаются открытыми.
- **Пересмотр:** после R3 mixed-load/rollback и R9 synthetic 20k/30k/40k validation.

## D-018 — R3 принимается на staging с измеренными границами

- **Дата:** 2026-10-01.
- **Решение:** считать R3 инженерно принятым на staging: shared observations, durable queue, ограниченный worker, preview isolation и go-live/live-update/offline lifecycle работают; SQLite и одна replica остаются до R9. Не утверждать SLA 20–40k пользователей по синтетическим тестам.
- **Основание:** deployment `822ef65f-2fa6-437e-90cb-6357c0e5b405` terminal `SUCCESS`, независимая проверка active target и `/healthz` 200; staging lease recovery и 1k/5k job mixed-load на временной DB с fake sender; локальные 20k/30k/40k профили, тестовый Telegram lifecycle и полный локальный gate 1030 passed, 2 skipped, 268 subtests. Подробности в `docs/audits/2026-10-01-r3-recovery-rollback.md`.
- **Граница:** массовая реальная Telegram fan-out задержка, общий rate budget остальных путей, смешанная нагрузка с FFmpeg и активная DB замена не измерены. Инструмент offline materialization делает только отдельную проверенную копию, не заменяет активную DB.
- **Пересмотр:** R9 после расширенного mixed-load, recovery и backup/restore drill; сменить DB только при доказанном bottleneck.

## D-019 — transient Railway status после SUCCESS повторяется

- **Дата:** 2026-10-01.
- **Решение:** после terminal `SUCCESS` staging guard повторяет временно неудачный read `railway status --json` до подтверждения active target либо timeout. Ошибка target mismatch не превращается в успех.
- **Основание:** при deployment `822ef65f-2fa6-437e-90cb-6357c0e5b405` CLI status read завершился GraphQL ошибкой после terminal `SUCCESS`, но независимая проверка вернула `active_target_ok=true`, `errors=[]`; RED/GREEN тест и полный gate подтвердили исправление в `7e4e8e4`.
- **Последствие:** guard fix включится в следующий staging commit snapshot; сам по себе он не требует отдельного deploy.

## D-020 — Streamer Plus привязан к подтверждённой паре Telegram/Twitch

- **Дата:** 2026-10-01.
- **Решение:** старый `twitch_user_tokens` не даёт право на Streamer Plus. Новая one-to-one связь появляется только после `/streamer_connect` в личном Telegram-чате и проверки Twitch OAuth `helix/users`; конфликт не перезаписывает другую связь или token. Тестовый grant привязан к broadcaster ID, имеет idempotency key, сроки, отзыв и audit events. R4 работает только локально и на pinned staging.
- **Основание:** TDD `tests/test_streamer_access.py`, миграция snapshot, deployment `3fd52fb1-ec2b-4276-a2ae-c136df7d2aed`; полный gate 1045 passed, 2 skipped, 271 subtests.
- **Граница:** реальный `/streamer_connect` с пользовательским Twitch OAuth на staging ещё не проходили; подписанный кабинет проверен локально синтетическими Telegram-подписями, staging API проверен без сессии. Test grant не является оплатой.

## D-021 — кабинет стримера изолирован от owner-панели

- **Дата:** 2026-10-01.
- **Решение:** `/streamer` использует отдельные подписанные Telegram WebApp/Login проверки, session cookie и profile API; R2 owner cookie/key не даёт streamer доступ. Сервер выводит Twitch account только из DB-связи после проверки Telegram ID. В Railway маршруты монтируются только при точном совпадении pinned staging ID.
- **Основание:** отрицательные/положительные локальные HTTP тесты и staging smoke: `/streamer/api/profile` 401 без session, `/admin/api/snapshot` 401, `/streamer` только login без админ-метки.
- **Граница:** реальный Telegram Login Widget на staging по-прежнему зависит от BotFather domain; тест с подписью не заменяет реальный UI E2E. Кабинет пока без сообществ и конструктора — это следующие пакеты R4.

## D-022 — оформление Streamer Plus привязано к текущему Helix broadcaster ID

- **Дата:** 2026-10-01.
- **Решение:** версия шаблона хранится для пары `(broadcaster_id, chat_id)` и пишется только после подписанного Telegram-входа, действующего test Plus и свежей проверки прав пользователя/бота в сообществе. При публикации и обновлении очередь применяет шаблон только если Helix `user_id` текущего эфира совпадает с подтверждённой связкой broadcaster ID/login; при отсутствии ID, завершении эфира, отзыве/истечении Plus или превышении 1024 UTF-16 единиц применяется базовый пост. Ссылка на Twitch остаётся. Каждый poll обновляет или очищает ID, включая reconnect.
- **Основание:** TDD на два сообщества, опасные ссылки/HTML, прямой URL и чужую сессию, потерю прав, конфликт версии, подмену/missing ID, revoke, preview composition и migration; gate 1064 passed, 2 skipped, 299 subtests. Перед deployment `a1577af8-919e-42b5-b5e0-d08a17a9b2b2` внешний backup/restore и миграция на копии прошли. Staging `r4_002–r4_004`, `integrity_check=ok`, HTTP smoke и testbot identity подтверждены.
- **Статистика:** `streamer_post_events` фиксирует успешную установку текущего Telegram message ID в той же транзакции и только при совпадении Twitch ID/login. За 30 дней считаются подтверждённые публикации, не `done` queue jobs и не просмотры Telegram.
- **Граница:** реальные `/streamer_connect`, Telegram Login Widget и положительный UI путь в staging не пройдены; активная DB содержит 0 identity/grants/templates. R4 принят как инженерный staging checkpoint, не как пользовательский pilot.

## D-023 — R5 billing проверяется закрытым mock provider без продажи

- **Дата:** 2026-10-01.
- **Решение:** `PaymentProvider` остаётся контрактом для будущего адаптера, но R5 исполняет только `MockPaymentProvider` с условной `TEST` unit, локальной HMAC-проверкой и без публичных checkout/webhook маршрутов. Order/payment/event/refund-request ledger хранится отдельно от Streamer Plus grants; только verified capture атомарно создаёт `source='mock'` grant, verified refund отзывает именно его. Точный replay возвращает сохранённый результат события и не меняет доступ.
- **Основание:** TDD на подпись, idempotency, чужой order/payment, поздний capture, cancel/expiry/refund, grant overlap, rollback и DB reopen; полный staging guard 1082 passed, 2 skipped, 317 subtests; deployment `fa758200-bb09-47d0-9cca-fdeab33b204f` terminal `SUCCESS`. Временная staging DB прошла capture/refund/cancel/expiry/rollback; активная staging DB не получила billing rows. Backup/restore и миграция на копии описаны в `docs/audits/2026-10-01-r5-mock-billing-staging.md`.
- **Граница:** тест не является денежной операцией или выбором FreeKassa/Robokassa/lava.top/Heleket/Stars. Реальный provider, цены/сроки, возрастные/юридические решения и production rollout требуют отдельного решения владельца. R6 начинается как staging simulation, не пользовательский pilot.

## D-024 — R6 закрывается инженерной staging simulation восьми стримеров

- **Дата:** 2026-10-01.
- **Решение:** утверждённый R6 в автономной работе выполняется как восемь синтетических Streamer Plus journeys в отдельной временной DB staging. Это проверяет связность R4/R5, изоляцию владельца, шаблон текущего broadcaster, сохранение статистики и потерю доступа при refund/expiry. Активную staging DB и настоящих пользователей simulation не меняет.
- **Основание:** TDD и полный guard 1084 passed, 2 skipped, 334 subtests; deployment `31086fa4-21ca-4190-bedb-6d79d95a267e` terminal `SUCCESS`, независимый active target и testbot identity. Staging run: 8 identities/communities/templates/post events, 16 ожидаемых отказов, 2 refunds, 6 expiries, 1 cancel, 0 unexpected errors; активная DB сохранила 0 pilot rows. Подробности в `docs/audits/2026-10-01-r6-pilot-simulation-staging.md`.
- **Граница:** 66 мс и нулевые внешние затраты относятся только к временной DB без сетевых вызовов; это не SLA, смета или пилот восьми реальных стримеров. Реальные OAuth/UI/права Telegram сообществ и production rollout остаются непроверенными. После этого этапа переходить к R7 на staging.

## D-025 — Viewer Plus использует подписанный Mini App и отдельный тестовый grant

- **Дата:** 2026-10-01.
- **Решение:** `/viewer` доступен как пустая оболочка, но настройки читает и меняет только `/viewer/api/*` после проверки сырого Telegram `initData` на каждом запросе. `tracked_channels` остаётся общей базой подписок и базового `notify_enabled`; Viewer Plus даёт versioned фильтр для новых личных live-уведомлений и переключатель существующей сводки после тихих часов. Расширенное право выдаётся/отзывается только закрытым owner test grant на pinned staging. R5 mock billing для Streamer Plus не продаёт Viewer Plus.
- **Основание:** полный локальный и staging gate 1099 passed, 2 skipped, 347 subtests; deployment `8cf965e2-abea-4536-b2b1-e2f06189a501` terminal `SUCCESS`, `r7_001`, `integrity_check=ok`, `getMe=TwitchSignalTestbot`. Подписанный owner видит одну свою подписку, тестовый чужой ID — ноль; подмена подписанного ID даёт 403, API без подписи 401. Краткий owner grant показал `plus_active=true`, отзыв вернул `false`. R2 `/admin` остался 401 по прямому API, 403 для чужого подписанного ID и 303 для владельца; общие command scopes без `/admin`, owner chat scope с ним. Подробности в `docs/audits/2026-10-01-r7-viewer-mini-app-staging.md`.
- **Граница:** реальный Telegram Mini App UI и live с действующим фильтром ещё не проверены. После предыдущей ошибки domain владелец сообщил о выполненном `/setdomain` и открывшемся Login popup; Mini App и полный вход аккаунтом остаются отдельными непроверенными E2E. Browser shell в ширинах 390/1440 не переполняется, но синтетическая подпись не заменяет реальный клиент. Production и реальные платежи не изменялись.
- **Пересмотр:** после реального Telegram UI E2E на testbot и R9 mixed-load; удаление R2 аварийного ключа требует отдельного подтверждённого staging входа.

## D-026 — R8 расширен публичным site prototype и отдельным demo video

- **Дата:** 2026-10-01.
- **Решение:** owner-approved дополнение `docs/workflows/2026-10-01-r8-seo-design-remotion.md` дополняет, а не заменяет attribution/referrals. Для staging сайта сначала сохранить два дешёвых визуальных направления, выбрать одно обратимым инженерным решением, использовать существующий Taste Skill, затем Impeccable review. Четыре закреплённых SEO-навыка поддерживают карту страниц, содержание, аудит и правдивую schema; отдельный локальный Remotion-проект создаёт демо 16:9 и 9:16. Admin/Mini App UI и live preview 24 с не меняются этим решением.
- **Основание:** прямое уточнение владельца в видимом Work и SHA256 manifest `docs/workflows/2026-10-01-r8-skills-lock.json`: 5 project-local bundles, 155 manifest files проверены без missing/mismatch; ещё 9 файлов являются upstream README/license. На момент решения Remotion инструкции установлены, runtime и видео ещё не созданы.
- **Граница:** сайт остаётся `noindex` на staging; финальный домен, индексация, реклама, реальные платежи, платный cloud и production требуют отдельных решений. Владелец отдельно сообщил о выполненном testbot `/setdomain` и открывшемся Login popup; полный owner account E2E не зафиксирован.

## D-027 — R8 SEO проверяет спрос и альтернативы, а staging noindex остаётся читаемым

- **Дата:** 2026-10-01.
- **Решение:** включить owner-approved `docs/workflows/2026-10-01-r8-competitive-seo.md` в текущий R8 spec/plan: датированный срез прямых hosted Telegram альтернатив и self-hosted кода, 20–30 гипотез запросов в 6–8 задачах, полезная карта страниц и измеримый checklist. Два дополнительных документационных навыка `competitor-profiling` и `cro` закреплены локально: 6/6 файлов совпали с SHA256 в `docs/workflows/2026-10-01-r8-seo-competition-skills-lock.json`; скрипты, API и зависимости не запускались.
- **Основание:** [Google Search Central](https://developers.google.com/search/docs/crawling-indexing/block-indexing) указывает, что robots.txt `Disallow` препятствует чтению `noindex`; для `/site/*` оставить HTTP+HTML noindex и доступность для crawler, не объявляя robots средством защиты. [Twitch Trademark Guidelines](https://www.twitch.tv/p/nl-nl/legal/trademark/) ограничивают использование знака Twitch в доменных и продуктовых именах. Это вопрос перед публичным запуском, а не основание молча менять текущий бренд или останавливать staging работу.
- **Граница:** ранги, спрос, field Core Web Vitals, реальные оплаты и органическая конверсия отсутствуют до публичного сайта и подтверждённых аккаунтов. Домен, название, покупка, запуск индексации, платные API и production остаются решением владельца. Шаблон недельного отчёта не создаёт скрытую automation.

## D-028 — светлая схема B, маршрут ролей A и логотип владельца

- **Дата:** 2026-10-01.
- **Решение:** владелец выбрал сгенерированный светлый концепт «Из эфира в Telegram», отдельно попросил сохранить из тёмного концепта заметный выбор «Я зритель»/«Я стример» и передал понравившийся логотип TwitchSignalBot. В R8 site использовать белую/графитовую основу с одним синим акцентом, горизонтальную схему «эфир → пост → зритель», два пути по ролям и цельный логотип на его тёмной подложке. Исходный logo asset `bot/growth_ui/brand-logo.png` имеет 225×72 px и SHA256 `d002aec1b24860d0eb5cbe3527896c466101ee7580782c3ea29ee43cf0c9d7f2`.
- **Основание:** прямые сообщения и вложенные изображения владельца в текущем видимом Work; оба исходных generated concept сохранены в `docs/design/r8-concepts/`, подробности в `docs/design/public-site.md`.
- **Граница:** растровые концепты не подменяют рабочий HTML, их сгенерированные слова «мгновенно»/«в реальном времени» не становятся заявлением продукта. Визуальное предпочтение не снимает вопрос публичного домена/названия и не меняет 24-секундный live preview.

## D-029 — staging-сайт остаётся отдельной неиндексируемой поверхностью

- **Дата:** 2026-10-01.
- **Решение:** четыре серверные страницы `/site` монтируются только для точного `TwitchSignalTestbot` при R8-флаге; другие имена отклоняются, без флага страниц нет. HTML, CSS, переданный логотип и локальный Golos Text образуют отдельный светлый стиль B с путями «Я зритель» и «Я стример». Публичный контент и синтетический пример не содержат личных данных. `src_site` ведёт только в тестовый бот.
- **Основание:** RED/GREEN `tests/test_growth_site.py` 4 passed и 12 subtests, связанные R2/R8 26 passed и 29 subtests; Chromium 360/390/768/1440 без горизонтального переполнения и browser errors, keyboard/reduced-motion/внутренние 200 и точный логотип проверены. Скриншоты, локальный mobile lab и границы измерения — `docs/audits/2026-10-01-r8-site-local.md`.
- **Граница:** HTTP+HTML `noindex` не снимается, robots разрешает `/site` для прочтения noindex. SEO launch checklist не запускает индексацию и не выбирает имя/домен. Railway R8 deploy, реальный Telegram owner E2E, полевая скорость и поисковые метрики ещё не подтверждены. Admin/Mini App и live preview не изменялись этим пакетом.

## D-030 — Remotion demo отделено от live preview и запускается пользователем

- **Дата:** 2026-10-01.
- **Решение:** отдельный `marketing/video` рендерит синтетическую восьмисекундную историю в 16:9 и 9:16; сайт показывает статичный постер и native controls без autoplay или предзагрузки MP4. Существующий 24-секундный Telegram Animation pipeline не меняется. Для воспроизводимости Remotion закреплён на `4.0.530` после обнаружения пустого `dist/render-queue/queue.js` в официальном архиве `4.0.531` на дату проверки.
- **Основание:** Studio и выборка 8 кадров двух форматов, локальный render с concurrency 2, lint и `scripts.verify_r8_video` прошли; оба MP4 8.000 s/H.264/30 fps, без audio и меньше 400 KiB каждый. Browser проверил отсутствие MP4 до play, воспроизведение/паузу, Range 206 и четыре размера. Подробности в `docs/audits/2026-10-01-r8-video-local.md`.
- **Граница:** при локальном решении R8 Railway ещё не был проверен; последующий staging smoke записан в D-031. Локальное использование не подтверждает право на будущий публичный коммерческий релиз по лицензии Remotion. Домен/индексация, production, cloud и реальные платежи не меняются.

## D-031 — R8 закрыт как инженерная staging-проверка, публичный SEO-релиз не начат

- **Дата:** 2026-10-01.
- **Решение:** принять R8 engineering checkpoint после guarded staging deployment `1f86e28e` из `6af8253`, полного suite, внешнего backup/restore, миграции на копии и фактического HTTP/browser/auth/testbot smoke. Светлый сайт с выбранным логотипом и двумя ролевыми путями остаётся `noindex`; `src_site`/referral измеряют первый touch и активацию только после действия в testbot. Условное Remotion demo остаётся отдельным от live preview. Следующий этап — R9 synthetic validation.
- **Основание:** 1120 passed, 2 skipped, 378 subtests локально и повторно в guard; staging 4/4 страницы, 4/4 asset hashes, Range 3/3, Chromium 390/1440, подписанный посторонний 403 и владелец 303/200, bot scopes, isolated stage DB journey и активные 0 growth rows. `docs/audits/2026-10-01-r8-growth-staging.md`, `docs/audits/2026-10-01-r8-copy-review.md` и SEO checklist фиксируют доказательства и границы.
- **Граница:** реальный вход владельца аккаунтом Telegram, реальные Mini App/streamer/live journeys, органический спрос, Search Console/Яндекс, field CWV, утверждение названия/домена, индексация и публичное использование Remotion остаются непроверенными. Текущий production deployment `2d440603-b74f-4c03-ba42-a5d53a491c02` не менялся; `main` не push/merge.

## D-032 — R9 измеряет смешанный synthetic путь на временной базе

- **Дата:** 2026-10-01.
- **Решение:** дополнить R3 shared-only baseline отдельным mixed-load harness 20k/30k/40k с реальными SQLite/queue/worker объектами, fake sender, lease recovery и ресурсными ограничителями. Профили запускать последовательно на новой temp DB, затем сравнить локальную и Railway staging среды; preview/FFmpeg и mock billing проверять ограниченными отдельными probes. Не менять DB engine или Telegram rate policy по одному синтетическому прогону.
- **Основание:** R3 результаты измеряли shared sample path и очереди до 10k отдельно; они не доказывают full fan-out latency при 20–40k. Спецификация и план: `docs/superpowers/specs/2026-10-01-r9-synthetic-scale-design.md`, `docs/superpowers/plans/2026-10-01-r9-synthetic-scale.md`.
- **Граница:** нет реальных Telegram/Twitch API запросов, production data, денег или публичного SLA. При первом ресурсном ограничении на staging следующий больший профиль не форсируется; фактический предел фиксируется.

## D-033 — R9 сохраняет SQLite с одной staging replica после synthetic 40k

- **Дата:** 2026-10-01.
- **Решение:** принять R9 как инженерную synthetic-проверку на pinned staging. Сохраняем текущие SQLite, durable queue и single-replica Volume; PostgreSQL не вводим без замера живой конкуренции, lock/WAL или восстановления. Telegram start interval и общие send пути ещё не измерены под живой нагрузкой. Preview concurrency оставляем ограниченной; 24-секундный H.264 Animation pipeline не меняем.
- **Основание:** commit `ccb958d`, полный suite 1126 passed, 2 skipped, 383 subtests дважды; guarded deployment `729e0070-709e-4dc7-a04f-40cef6031447` terminal `SUCCESS`, `getMe=TwitchSignalTestbot`. Локально и на staging последовательные 20k/30k/40k завершили все jobs без потерь, с revision 2, lease recovery, целостным backup/restore. На staging 40k заняли 71,59 s, fake drain 50,40 s, process peak RSS 219,3 MB, DB read p95 3,48 ms; остальные цифры и backup SHA в `docs/audits/2026-10-01-r9-scale.md`. Mock billing replay/refund и синтетический FFmpeg preview прошли. Production deployment `2d440603-b74f-4c03-ba42-a5d53a491c02` остался без изменений.
- **Граница:** fake sender не ждал Telegram и не отправлял 40k сообщений; 40 ms интервал даёт арифметический минимум 1600 s на 40k стартов. Один прогон на размер не даёт SLA и не измеряет Twitch сеть, реальный Telegram API, горизонтальный Volume, owner UI E2E или полевую нагрузку. Реальный provider, публичный домен/индексация и production требуют решения владельца.

## D-034 — новый Mini App строится поверх общей Free-основы

- **Дата:** 2026-10-01.
- **Решение:** исполнять одобренные T1–T6 → T13 → T7–T11 → T14–T17 → T12 в текущей ветке и одном видимом чате. Для личного зрителя серверные лимиты 50 Free / 200 Viewer Plus и пять сохранённых видеовыборов считаются по утверждённому scope update; число сообществ и групповой лимит не менять. Новый `/app` использует общую БД и отдельный светлый Operate brief, не копирует owner admin panel.
- **Основание:** прямое разрешение владельца, `mini-app/2026-10-01-scope-update.md`, `mini-app/IMPLEMENTATION-ADDENDUM.md`, фактическая карта `docs/audits/2026-10-01-mini-app-free-baseline.md`, проверка 31/31 SHA256 навыков.
- **Граница:** найденные Plus-gates старых `/viewer/api/digest` и `/streamer/api/communities` противоречат согласованному Free-сценарию; исправить с регрессионными тестами в T6/T9. Личный старый `preview_enabled` не служит новым entitlement. Эта запись не означает готовность UI, отправки, native E2E или staging deployment.

## D-035 — права Viewer и Streamer рассчитываются независимо на сервере

- **Дата:** 2026-10-01.
- **Решение:** единый `CapabilityService` вычисляет возможности из текущих DB grants и точной пары broadcaster/chat для placement. При отказе проверки платное право закрыто, Free базовая возможность остаётся доступной как политика. Каталог фиксирует только согласованные 50/200/5; цена и реальные покупки не определяются кодом.
- **Основание:** RED→PASS пяти сценариев `tests/test_mini_app_capabilities.py`, связанные viewer/streamer access тесты — суммарно 17 passed и 3 subtests; кодовый commit `d7d78d2`, tree `b7996dcefa01b253a98f5c72821aa6f1f8d07db3`.
- **Граница:** T2 создаёт контракт, но не подключает новые `/app` маршруты и не заменяет все старые проверки; потребители и expiry перед отправкой реализуются на последующих этапах. Full suite и staging относятся к финальному snapshot.

## D-036 — mock ledger различает viewer и streamer subject

- **Дата:** 2026-10-01.
- **Решение:** `BillingService` оставляет прежний streamer-вызов и принимает `plan='viewer_plus'` только в разрешённом mock-контракте. Viewer subject — проверенный Telegram ID; streamer subject — подтверждённый Twitch broadcaster. Старые R5 заказы получают тип `streamer` через транзакционную migration, а refund отзывает связанный grant своего заказа.
- **Основание:** RED→PASS `tests/test_billing_viewer_product.py`, связанные billing и synthetic probe тесты: 24 passed, 18 subtests; временная legacy DB `integrity_check=ok`; commit `34b3966`, tree `12b3a67b88b90ee3a1397739a45e531727149a76`.
- **Граница:** этот внутренний mock-контракт не является публичным checkout в Mini App; T11 ещё должен ограничить тестовую активацию allowlist и вывести только реальные состояния. Цены, периоды реальной покупки и деньги не определены.

## D-037 — Mini App использует отдельный проверяемый вход на общем веб-сервере

- **Дата:** 2026-10-01.
- **Решение:** новый `/app` монтировать только при `mini_app_enabled` локально и на pinned staging; private menu ведёт на него, старые `/viewer` и `/streamer` остаются совместимыми. `bootstrap` выдаёт лишь собственный Telegram ID и рассчитанные сервером capabilities. Режим и сохранённый выбор вкладки являются только интерфейсным состоянием. Два вида Telegram safe-area отступов объединяются максимальным значением для каждой стороны, чтобы не удваивать область.
- **Основание:** commit `a6ab8f8`, tree `b2018a4fea0d1b2b64663c405fcbf3f4d73e12b8`; auth/menu RED→PASS, 29 passed, 15 subtests; browser PASS 20 переходов и 8 снимков.
- **Граница:** синтетический SDK/browser не подтверждает реальный клиент Telegram. Сами Free/Plus данные и действия ещё не подключены; восстановление черновиков форм, настоящий вход Main Mini App и staging требуют следующих этапов.

## D-038 — Free viewer использует существующие подписки бота

- **Дата:** 2026-10-01.
- **Решение:** `/app/api/viewer/*` читает и изменяет личные tracked channels по проверенному Telegram ID. Добавление проходит Twitch lookup и `add_channel_with_limit`; поиск допускает только ник/ссылку Twitch и лимит 6 запросов за 10 секунд на пользователя. Отложенный live-маркер старше пяти минут показывается как stale. Клиент запрашивает native write access по действию «Добавить», а не при первом входе.
- **Основание:** commit `1ae0f71`, tree `967d4512fcf58052c3ff38be6ebc300f898cdf9c`; 25 passed, 19 subtests, browser PASS с четырьмя снимками и проверкой rollback/отмены поиска/длинного имени.
- **Граница:** fake Twitch и синтетический Telegram SDK не подтверждают возможность отправки пользователю на устройстве. Общие Free-настройки и Plus-фильтры приходят в T6, пять личных видео — T13/T14; staging и полный suite остаются впереди.

## D-039 — фильтр Plus и бесплатная сводка разделены по серверному праву

- **Дата:** 2026-10-01.
- **Решение:** текущий grant Viewer Plus проверяется при записи и применении фильтра; сохранённое правило после истечения не удаляется. Тихие часы и сводка являются Free-функцией и в новом приложении, и в старом `/viewer`. Каждая настройка сводки атомарно обновляет только существующий quiet-hours ряд.
- **Основание:** commit `d8a4359`, tree `2aab98800752f8217d41a71a46441ac103a491be`; RED 3 server/1 browser сценария, 24 passed, 24 subtests, 4 browser screenshots и проверка BackButton контекста.
- **Граница:** реальные Telegram-клиенты, live delivery и guarded staging ещё не проверены; фильтр не является вариантом оформления публикации стримера и не открывает другие Plus-права.

## D-040 — личный лимит и видео выбираются по подтверждённым данным сервера

- **Дата:** 2026-10-01.
- **Решение:** личный предел Free 50 / Viewer Plus 200 перепроверяется в SQLite-транзакции независимо от bot/app precheck; группы остаются на 50. При downgrade активность первых 50 определяется стабильным порядком добавления и отдельным приоритетом зрителя, а не ручным `notify_enabled`. Mini App передаёт выбранные logins и версию; сервер сам получает Twitch broadcaster IDs, проверяет владение подписками и атомарно хранит до пяти ID, включая offline. Клиентский ID или старый `preview_enabled` не дают доступ.
- **Основание:** commit `74adbaa`, tree `9510ef1cf90a5c950def681cace222abfb42eefe`; RED → 55 финальных targeted passed/5 subtests, до последней узкой правки 440 related passed/55 subtests; browser PASS 8 снимков, временная БД, два независимых SQLite-соединения и старая схема миграции.
- **Граница:** выбор и серверные права готовы локально, но автоматическая media-доставка, безопасный animation→photo, real Telegram/Twitch и staging не проверены. T14 реализует/проверяет доставку; T12 выполнит итоговый gate.

## D-041 — смена категории фиксируется только после устойчивого наблюдения

- **Дата:** 2026-10-01.
- **Решение:** категорию брать из текущего успешного общего poller-наблюдения Twitch и фиксировать переход только после двух наблюдений новой категории за не менее чем 60 секунд. Состояние и уникальный переход сохранять одной SQLite-транзакцией. Offline, смена logical stream, переставленные наблюдения и большой пробел переустанавливают baseline без догоняющего уведомления; sequence сохраняется после offline для повторного подключения к тому же logical stream ID.
- **Основание:** RED отсутствующего детектора, Twitch category ID, offline и sequence collision; commit `a71da4f`, tree `6ab064b72991374a783626c431463700a85a0881`; финальные related 40 passed + 2 subtests, широкий набор до последней узкой правки 402 passed + 47 subtests. Проверены rollback, два соединения, backup/restore и integrity.
- **Граница:** T7 не отправляет категорийные сигналы и не выдаёт Viewer Plus право. Fenced enqueue/dispatch, UI toggle, cooldown и fake sender относятся к T8; real Twitch/Telegram и staging остаются NOT TESTED.

## D-042 — отдельный category-сигнал имеет opt-in и проверку перед отправкой

- **Дата:** 2026-10-01.
- **Решение:** Viewer Plus сохраняет отдельный выключенный по умолчанию сигнал на собственную подписку: любая новая категория или до пяти выбранных стабильных Twitch ID. Сервер заново получает имена по ID. Подтверждённый переход и задания создаются одной SQLite-транзакцией, уникальный job привязан к проверенному зрителю и transition. Старые ожидающие смены заменяются последней; перед отправкой повторно проверяются Plus, notify, подписка, текущая категория, фильтр, тихие часы и 300-секундный интервал. Известный успех сохраняется до ack. При неопределённом результате внешней отправки job отмечается `UnknownOutcome` и автоматически не повторяется.
- **Основание:** RED пяти отсутствующих путей, API route RED 404, rollback/injected failure/fake-clock tests; commit `74052df`, tree `6c133451830d98d27a38e0102d1b3ad46713cdbf`. Связанный набор 63 passed/2 subtests до последней оптимизации, финальные 32/2. Browser QA 4 снимка 390/360 light/dark и сохранение после reload. Для поиска категорий использован официальный Twitch Helix [Search Categories](https://dev.twitch.tv/docs/api/reference/#search-categories).
- **Граница:** это локальные тесты с fake sender и синтетическим SDK. Они не подтверждают реальную Telegram-доставку, Twitch live или staging. При неизвестном результате статус честно требует наблюдения; математическое exactly-once не обещается.

## D-043 — подключение сообщества не выдаёт права публикации по callback

- **Дата:** 2026-10-01.
- **Решение:** Free стример связывает Twitch через существующий OAuth с одноразовым серверным intent. Выбор группы/канала через новый prepared `requestChat` или старый reply keyboard принадлежит проверенному Telegram ID и живёт 600 секунд. После service message сервер заново проверяет админские права пользователя и бота. Подключение сообщества и включение обычных публикаций — два отдельных действия; ни выбор, ни оплата, ни флаг клиента не запускают отправку. Отмена инвалидирует intent и поздний callback.
- **Основание:** RED Free-пути, отсутствующих routes, отмены и зависшего verifying; commit `82dd7ab`, tree `f30e4145c0152e20eaab1cf51642ef1e467f6371`; 42 related passed/20 subtests. Browser PASS 6 снимков 360/390/768/1440 light/dark на временной DB с fake bot.
- **Граница:** реальный OAuth аккаунта владельца, нативный Telegram выбор чата, исходящие сообщения и staging не проверены. T10 добавит оформление и статистику; T12 выполняет полный gate.

## D-044 — платное оформление проверяется у отправки, сохранённый шаблон остаётся

- **Дата:** 2026-10-01.
- **Решение:** локальный пример поста не делает Telegram send. Free получает стандартный пост. Сохранённое оформление Streamer Plus принадлежит verified broadcaster/community, защищено версией и действующим grant; при окончании доступа данные остаются, но новый текст и анимация сообщества не применяются. Очередь пересобирает пост у фактического send, `LivePostUpdater` повторно проверяет эффективное media-право до edit. Статистика считает подтверждённые публикации, не просмотры.
- **Основание:** RED отсутствующих Mini App routes и платного текста после revoke между сборкой и send, RED animation revoke после render; commit `2650937`, tree `f9f2636c595a0c8da97a9083df6a83a42d7fe857`. Финальные related 156 passed/38 subtests; browser PASS 5 снимков Free/Plus и конфликта двух окон.
- **Граница:** старые личные preview callbacks и пять video-слотов требуют T14; общий media load T17. Реальные sends/native/staging не проверены. По production-journey audit A1–A4 добавлены в T14/T12; HTML-отчёты и экспорт сохраняются, новая структура и платность отчётов не утверждены.

## D-045 — тестовый Plus включается только серверным событием на закреплённом staging

- **Дата:** 2026-10-01.
- **Решение:** Mini App показывает Viewer Plus и Streamer Plus раздельно по текущим серверным grants, включая источник и срок, и собственную историю заказов. Только точный pinned Railway staging с `TwitchSignalTestbot` и owner ID допускает технический mock checkout. Клиент выбирает лишь продукт: субъект, единица `TEST`, длительность технического QA и подтверждённое mock-событие задаются сервером. Клиентский `paid`, неподписанный вход и чужой order ID не выдают право. Бесплатное подключение и прежние Free-права не меняются.
- **Основание:** RED 3 server cases, commit `db47b43`, tree `8a04a63347748151b3089e853a5885d413f134f5`; 40 related passed/15 subtests; QA-дополнение `8e40b48`, 6 browser screenshots на временной БД. В браузере проверены pending/cancel/confirm/refund, ошибка сети, переход назад, новый grant без restart и черновик.
- **Граница:** это тест без денег, цены, реального провайдера и автоматического списания. Семидневное добровольное ознакомление относится к T16 отдельно. Native Telegram, реальный OAuth, внешняя отправка и staging остаются NOT TESTED; полный gate — T12.

## D-046 — видео получает текущее право и уступает бюджет обычным сигналам

- **Дата:** 2026-10-01.
- **Решение:** старый callback не включает видео. Личная анимация допускается только для текущих Viewer Plus, выбранного broadcaster ID, активной подписки и текущего live-поста; сообщество требует собственный Streamer Plus и identity. Capture ограничен двумя активными сессиями с удержанием до первой законченной попытки, artifact создаётся один раз на эфир и file_id повторяется внутри одного бота. Общий Telegram бюджет ставит обычные сигналы перед фоновым видео; 429 останавливает веер и уважает retry_after. После потери права, отключения уведомлений или снижения плана текущая анимация возвращается к фото в том же сообщении без повторной публикации, включая 51-ю подписку. Неопределённый результат не считается успешной сменой вида.
- **Основание:** RED старого callback, 100 получателей/пять потоков, прогрева 75/60, 429, unknown edit и 51-й подписки; `05bcbb3`, tree `f218da9b545bfb403a3fd02bbfd137865e2de1b9`; 310 related passed/18 subtests, два browser screenshots на временной БД. Scoped review нашёл и помог закрыть пять дефектов.
- **Граница:** это локальная проверка с fake sender и synthetic SDK. Она не подтверждает native Telegram, реальный Twitch capture, внешнюю отправку или staging. Отдельные A3 raid/quiet-hours и A4 MenuButton из production-аудита остаются T12. HTML-отчёты/экспорт и Free-права сохраняются.

## D-047 — напоминание привязано к текущему эфиру и версии отправки

- **Дата:** 2026-10-01.
- **Решение:** 15/30 минут выбирает сам зритель с действующим Viewer Plus для собственного текущего эфира. Повтор меняет версию, отмена убирает ожидающий job. Общий worker непосредственно перед внешним send проверяет доступ, live, подписку, notify и тихие часы. После перехода в `sending` отмена/перенос возвращают 409, поскольку запрос уже мог уйти в Telegram. Потерянный ответ и истёкший lease дают `unknown` без автоматического дубля; подтверждённый 429 допускает повтор после задержки. UI не выдаёт просроченное ожидание за будущее.
- **Основание:** RED отсутствующих сервиса/API и старого pending job; scoped review выявил гонку отмены и зависание после 429. Кодовый commit `3e3b3de`, tree `9d9d1a56cc3e1a388a25d220ddfbe38d99ed8de3`; финальные 44 related passed/9 subtests, 2 browser screenshots 390/360 light/dark.
- **Граница:** fake sender, временная БД и synthetic Telegram SDK. Реальная доставка, native UI, Twitch и staging NOT TESTED. T16 и финальный T12 остаются открыты.

## D-048 — папочный фильтр наследуется только при отсутствии личного

- **Дата:** 2026-10-01.
- **Решение:** собственный фильтр стримера, включая пустой, целиком старше общего правила папки. Явный versioned reset личного фильтра возвращает наследование. Одна подписка принадлежит максимум одной папке; её перенос защищён текущим folder ID, правка папки — версией. Expiry выключает платное применение, но сохраняет папки/связи. Удаление папки оставляет подписки. Технический потолок 200 папок ограничивает рост пустых записей.
- **Основание:** `mini-app/T16A-CONTRACT.md`, RED отсутствующего сервиса/API, foreign access, CAS и expiry; кодовый commit `362a6b7`, tree `75d5b464521a216f35b9409c1d9525fb100a7b0c`; 29 related passed/17 subtests, browser PASS 2 снимка 390/360 light/dark. Scoped review указал на reset/cap/stale draft/name conflict, исправлено.
- **Граница:** папочный фильтр не меняет старую семантику quiet-hours для go-live/raid. Это несоответствие продуктового текста и dispatch проверяется в T12 вместе с A3 production-аудита. Native, реальные Telegram/Twitch и staging NOT TESTED.

## D-049 — история показывает только результаты собственных отправок

- **Дата:** 2026-10-01.
- **Решение:** личная лента Viewer Plus читает только события проверенного Telegram ID с текущим grant. `sent` возникает после подтверждённого send, `suppressed` — после известного отказа или несостоявшейся отправки, `unknown` — когда исход внешнего запроса не доказан. Queue-факт и запись истории совпадают транзакционно; после падения между подтверждённой отправкой напоминания и ack истёкший lease закрывается как `sent`, без повторного send. Данные ограничены метаданными и очищаются через 30 суток server UTC; это техническое хранение, не срок тарифа.
- **Основание:** `mini-app/T16B-CONTRACT.md`, RED fake delivery, foreign/expiry, pagination, terminal rejection, lease crash; кодовый commit `60d9f53`, tree `8c3648cbb3d8697345584ebc93c1feade3eeaca1`; финальные 47 related passed и browser PASS 2 снимка 390/360 light/dark. Scoped review повторно проверил crash-fix.
- **Граница:** это не перенос существующих HTML-отчётов и не подтверждение чтения сообщения человеком. Free-оповещения, HTML-экспорт, production `6074744` не менялись; реальные Telegram/Twitch/native/staging NOT TESTED. T16c следующий, T12 остаётся последним.

## D-050 — вариант оформления действует только после явного применения

- **Дата:** 2026-10-01.
- **Решение:** сохранённый вариант привязан к verified broadcaster и сам по себе не меняет текущий шаблон или опубликованный пост. Применение к подключённому сообществу повторно требует Streamer Plus, права и ожидаемую версию активного шаблона. Имена уникальны без учёта регистра, технический предел — 12, поля проходят действующий валидатор текста и HTTPS-кнопок. После expiry варианты остаются доступными для чтения; удаление собственного варианта разрешено. Сравнение 7+7 дней считает только `streamer_post_events` собственного broadcaster.
- **Основание:** `mini-app/T16C-CONTRACT.md`, RED отсутствующего сервиса/API, ownership, cap, expiry, stale version и confirmed-post comparison; кодовый commit `d48d064`, tree `6e8baf0b6724a1d6ac002ce76249e100d0ca0f55`; 28 related passed, browser PASS 2 снимка 390/360 light/dark. Scoped review закрыл гонки поздних ответов и потери нового черновика.
- **Граница:** сравнение не измеряет просмотры и эффект конкретного варианта. Это локальный fake/browser тест, реальный Telegram send/native/staging NOT TESTED. HTML-отчёты/экспорт, Free-подключение и production `6074744` не менялись. Следующий T16d.

## D-051 — ознакомление выдаётся однократно и только по явному действию

- **Дата:** 2026-10-01.
- **Решение:** семь суток Viewer Plus доступны лишь проверенному Telegram ID в server allowlist на закреплённом staging. Открытие приложения не активирует trial. `BEGIN IMMEDIATE`, уникальный ключ запроса и запись `viewer_test_trials` в одной транзакции защищают от повторного grant; повторный запрос возвращает исходный срок. Отзыв/expiry не открывают новый trial, а отдельный действующий Plus не расходует ещё не начатое ознакомление. UI различает активность самого trial и иного Plus.
- **Основание:** `mini-app/T16D-CONTRACT.md`, RED отсутствующего сервиса/маршрута/`test_trial_active`; commit `dcfca4a`, tree `1bfbfa14a4a4e79043849124c98f786899596a0c`; 29 связанных тестов PASS и browser PASS 2 снимка на временной БД. Scoped review проверил задержанный ответ состояния и статус после другого grant.
- **Граница:** это test-only без денег, цены, автопродления и public checkout. Production `6074744`, HTML-отчёты/экспорт и Free-права не менялись. Реальный Telegram/native/staging NOT TESTED; T17 и T12 остаются.

## D-052 — медийный допуск ограничен измеренным локальным пределом

- **Дата:** 2026-10-01.
- **Решение:** общий `PreviewManager` оставляет максимум две активные capture/encode и фото для отложенных эфиров. Нагрузочный harness ограничен временем, задачами, очередью, RSS/CPU Python и дочерних кодировщиков и временным диском; стоп прекращает локальный прогон с ненулевым кодом и очисткой. При 1×1000 общий бюджет не завершил веер за 25 секунд, поэтому масштабируемость на такую доставку не заявляется и инфраструктура не увеличивается автоматически.
- **Основание:** `docs/audits/2026-10-01-mini-app-media-load.md`, RED отсутствующего harness, commit `ecc4f46`, tree `dcc2a77515e7bda4e75217e50bb5e0629419d43b`; 33 связанных теста PASS. H.264 профили: 463/1000 fake edit до стопа; при 100×10 и 1000×5 пиковые capture/encode 2, отложены 98/4998 потоков. Scoped review подтвердил cleanup child, учёт CPU после завершения и честные границы fake sender.
- **Граница:** это synthetic load, не реальная пропускная способность Twitch/Telegram, не native E2E и не SLA. RSS — периодически наблюдаемый пик. UI сценарии 4→5→6, expiry→photo и restart повторяются в T12. Production, HTML-отчёты/экспорт и Free-права не затронуты.

## D-053 — Mini App принят инженерно на staging с явной границей native E2E

- **Дата:** 2026-10-01.
- **Решение:** кодовый snapshot `273db13` развёрнут только в закреплённый staging deployment `9039cde2-08a9-4c30-b36e-232ad70e7410`. Готовность означает подтверждённые локальные функциональные/браузерные сценарии, полный suite, backup/migration-copy, серверные права и проверенные staging identity/SHA/menu/routes. Она не означает, что настоящий Telegram-клиент, Twitch OAuth, внешний send или live capture уже пройдены. Для владельца остаётся отдельное визуальное и account E2E принятие.
- **Основание:** `docs/audits/2026-10-01-mini-app-acceptance.md`; локальный и deploy guard suite `1290 passed, 2 skipped, 419 subtests passed`; 32 Chromium screenshots; staging `SUCCESS`, `getMe=TwitchSignalTestbot`, `MenuButtonWebApp «Приложение»`, SQLite `integrity=ok` и 20 миграций. Production остался `6074744`.
- **Граница:** старые HTML-отчёты/экспорт и Free-права сохранены. Новый первый вход и платность расширенных отчётов не утверждены и не внедрены. Одноразовый ключ migration-copy проверил схему без доступа к staging-секрету; запрос на локальное чтение переменных Railway и удаление дополнительной копии вне Git отклонила автоматическая политика. Реальные деньги, public launch и production изменения запрещены этим этапом.

## D-054 — выбран А с группировкой эфиров; перенос ждёт приёмки

- **Дата:** 2026-10-02.
- **Решение:** использовать «А · Собранный» как единственное направление Mini App, с группами live/offline из Б и отдельным stale. Сначала доработанный изолированный предпросмотр и визуальная приёмка; затем согласование последовательного плана и перенос. Светлая тема по умолчанию; явные light/dark/Telegram сохраняются. Компактный профиль, «Управление подпиской» с собственными операциями, «Итоги эфиров» с прежним HTML-экспортом. Новый выбор — только Telegram-канал, старые группы сохраняются.
- **Основание:** последнее сообщение владельца «ВЫБОР СДЕЛАН»; `docs/design/mini-app-redesign-2026-10-02/DESIGN-SELECTED.md`, `selected-browser-qa.json`, `selected-webkit-qa.json` и итоговые PNG. Три RED дефектов single → три PASS; один read-only проверяющий подтвердил закрытие переходов и итоговые центральные колонки. Визуальная приёмка ещё не получена.
- **Граница:** отдельный локальный макет на HEAD `e0d44c3`, не изменение продукта/размещения. Не переносить учебные Expo/device frame, не использовать ui-ux-pro-max, не менять модель/усилие. Настоящие Telegram, клавиатура, SDK, OAuth, серверные права и отправки NOT TESTED. Free 50 / Plus 200, пять видео, существующие функции/HTML/группы и серверные права остаются в согласуемом переносе. Production, реальные деньги и пользовательские данные не менялись.
## D-055 — одобрен пакет исправлений предпросмотра А; приёмка ещё ожидается

- **Дата:** 2026-10-02.
- **Решение:** доработать единственный выбранный А в отдельном макете. Видео сверху, все выбранные вместе с offline; реальные действия имени бота с честным отказом, корректная ссылка только при проверенных метаданных; девять состояний канала; max-column600/короткая высота; относительная системная типографика/text200/zoom200; подписка отдельным экраном, помощь отдельно; доступные панели и диалоги. Минимальный будущий контракт прав своих сообщений сверить одновременно с checker при переносе, текущий checker сохранить.
- **Основание:** последний пакет владельца «Я одобряю все исправления из последнего независимого обзора»; `docs/design/mini-app-redesign-2026-10-02/README.md`, `DESIGN-SELECTED.md`, `CHANNEL-PERMISSIONS-CONTRACT.md`, `REVIEW-PACKAGE.md`. Новые `package-*-qa.json` на финальных хешах:80 обычных layout,32text200,32text-navigation,12actual zoom,14contrast, два движка и portable. RED→PASS для async focus и скрытого обрезания menu; ограниченный проверяющий подтвердил Chromium и переснятый PNG. Без ослабления ожиданий.
- **Граница:** только документы и предпросмотр на прежнем HEAD `e0d44c3`, не продукт/деплой/БД. Макетные clipboard/verified URL/канал/OAuth не доказывают серверный успех. Telegram/iPhone/клавиатура/реальные права/send/OAuth NOT TESTED. Архивы/HTML/экспорт/группы/Free и серверные gates сохранены. Модель/усилие, глобальные навыки и стек не менялись; ui-ux-pro-max не применялся. После приёмки дизайна — утверждение плана, затем перенос на тестовый бот; сейчас остановка.

## D-056 — Platega, новые тарифы и merchant approval зафиксированы

- **Дата:** 2026-10-02.
- **Решение:** внешний российский payment provider текущего этапа — Platega; владелец работает как самозанятый без ИП/ООО. Viewer Plus = 150 ₽/месяц, Streamer Plus = 200 ₽/месяц и включает все Viewer Plus-права для того же Telegram-пользователя. Автопродление выключено. В purchase flow пользователь видит тариф, цену и кликабельную покупку; СБП/карта идут через Platega, Stars остаются отдельным provider с неутверждённой XTR-ценой. Для банковского согласования обязательны Privacy Policy, User Agreement, реальный контакт поддержки и актуальные тарифы.
- **Основание:** решение владельца «Platega выбираем точно» и рекомендации менеджера Platega от 2026-10-02; актуальный handoff: `docs/workflows/2026-10-02-plus-payments-platega-handoff.md`.
- **Граница:** реальные списания, production, auto-renew, upgrade/refund формулы, XTR-цены, international provider и непроверенные raid/name-change/spike Plus-события не утверждены. Реальные merchant/KYC/налоговые данные не придумывать и секреты не передавать через чат/Git/client.


## D-057 — темы и тарифы А проверены в отдельном макете

- **Дата:** 2026-10-02.
- **Решение:** сохранить А, собственный neutral graphite и отдельные mock ThemeParams, Free/Viewer/Streamer, общий design catalog с включением Viewer в Streamer. Demo purchase не выдаёт grant/HTTP; upgrade не рассчитывается до решения владельца. Серверное наследование/реальные SDK не перенесены.
- **Основание:** attachment `62411d40-62b8-40b6-82d9-121df4370c58`; finalized package-*-qa.json/snapshot пяти файлов. Chromium/WebKit: 80 layout, 32 text200, 32 navigation, 48 plan-reflow, 72 новых text200, async focus; Chromium12real zoom/14contrast; portable HTTP0/JS0; detector[]. 17PNG source-bound. Один read-only обзор12PNG/узкого journey не выявил остаточных дефектов; native/account/payments NOT TESTED.
- **Граница:** docs/изолированный preview на HEAD e0d44c3, без commit/deploy/рабочей БД. Archives/HTML/export/группы/Free сохранены. Условные подписи старого макета не заменяют утверждённые RUB цены из D-056.

## D-058 — расширение billing описано до plan и кода

- **Дата:** 2026-10-02.
- **Решение:** сохранить D-056 и handoff, расширить существующий PaymentProvider/ledger, фиксировать Telegram beneficiary независимо от issued_by/Twitch ownership; использовать единый effective Viewer resolver всех SQL/API/runtime gates. Spec `docs/superpowers/specs/2026-10-02-plus-platega-payment-design.md` ждёт утверждения; migrations/adapters не внедрены.
- **Основание:** attachments89db2ad6/7ced1d3a, актуальный handoff, чтение текущих billing/trial/entitlements/config и всех31official index docs/126778bytes/metadata SHA. Подтверждены методы2/11, creation/status/callback/отмена-возврат. Документированные schema/GET/idempotency пробелы перечислены.
- **Открыто:** Telegram требует Stars для digital Plus внутри Mini App; предложены Stars внутри Telegram/Platega на самостоятельном согласуемом сайте. XTR, месяц/anchor, upgrade/активная покупка, refund→доступ, legacy binding и merchant/NPD/test/idempotency не придуманы. Непроверенные дополнительные Plus-события не продаются и текущие Free-права не отнимаются.
- **Граница:** остановка перед plan/кодом по прямому указанию владельца. Только staging/testbot; real account/credentials/callback/Stars/деньги NOT TESTED. Public callback URL зарезервирован, не активен. Production/model/effort не менялись.

## D-059 — merchant UX с работающей заглушкой и legal drafts

- **Дата:** 2026-10-02.
- **Решение:** дополнение менеджера сохраняет provider/order/entitlement архитектуру. Цены/состав видны до оплаты; обе CTA кликабельны, внешний способ→временная недоступность без invoice/payment/grant. Privacy/Agreement доступны до оплаты; поддержка требует отдельный username/email. Bank-ready false при незаполненных контактах/данных/условиях.
- **Основание:** attachment7ced1d3a; оба шаблона и текст short-URL Google Docs прочитаны. Адаптированные legal drafts/PLATEGA-BANK-APPROVAL.md/отдельный bank-review.html с общими tokens/catalog. Chromium151/WebKit26.5 PASS52layout/text200,оба метода/плана/reload/back/keyboard/focus,exact drafts,no fake support,0payment/outbound/JSerrors,7full-pagePNG со source/imageSHA256.
- **Граница:** документы/локальный показ, не рабочий продукт/deploy/legal publication/bank approval. Реальные сведения/SUPPORT_USERNAME/SUPPORT_EMAIL не предоставлены; месяц/возвраты/Stars/площадка/НПД не утверждены. Bank NOT READY, Platega sandbox/connectivity/native NOT TESTED. Возвратные запреты из шаблона не перенесены; отправки на согласование не было. Остановка для проверки spec/макета/документов, затем отдельное утверждение plan.

## D-060 — три способа покупки внутри бота / Mini App по прямому решению владельца

- **Дата:** 2026-10-02.
- **Решение:** покупка начинается внутри TwitchSignalBot / Mini App: тариф → «Купить» → Telegram Stars / СБП / банковская карта. Stars обслуживает Telegram, СБП/карту — Platega. Прежнее предложение D-058 о Mini App только Stars и Platega только на самостоятельном сайте отменено. Владелец осознаёт и принимает платформенный риск; скрытие методов от проверок, маскировка внешнего перехода, обходы Telegram и ослабление безопасности запрещены.
- **Сохранено:** Viewer150 ₽/месяц, Streamer200 ₽/месяц со всеми Viewer-возможностями тому же покупателю, отсутствие автопродления, общий provider/order/ledger, строгая авторизация, проверка callback и canonical status, атомарные права. Требования менеджера: полный путь/конкретная цена/кликабельный CTA, Privacy Policy/User Agreement до оплаты, реальный индивидуальный контакт поддержки.
- **Изменено:** только `docs/superpowers/specs/2026-10-02-plus-platega-payment-design.md` и указатели статуса/дизайна. Spec не утверждён. XTR, месяц/anchor, upgrade/активная покупка, refund→доступ, legacy binding, реальные сведения/контакты и merchant/NPD/test/schema/idempotency остаются открытыми; точка покупки/провайдеры больше не запрашиваются.
- **Проверка/граница:** текстовая сверка spec и сохранности существующих файлов; код/UI-макеты/legal drafts/QA/БД/secrets/deployment не изменялись, staged отсутствует. Прежний bank-review не подтверждает новый трёхспособный flow. Bank NOT READY; внешние/native/денежные сценарии NOT TESTED. HTML/экспорт/архивы сохранены. HEAD `e0d44c315a1c81dc5b310dd1f3c7f3169915c985`; остановка для утверждения spec, plan и код не начаты.

## D-061 — дизайн/spec приняты; финальный план переноса подготовлен до кода

- **Дата:** 2026-10-02.
- **Решение владельца:** А «Собранный» с группировкой из Б и исправленный payment/Plus spec одобрены для подготовки реализации. Разрешено составить финальный implementation plan, затем остановиться для отдельного утверждения. Прежние отметки об ожидании выбора дизайна/spec перестали описывать текущую точку.
- **Основание:** attachment `ce26fd15-29eb-47ba-911d-45e9236d2c39`; одобренный `docs/superpowers/specs/2026-10-02-plus-platega-payment-design.md`, SHA256 `94975d454af9db6404ce4bc208e528093d9ebaa164dae39b0824969383157668`. Новый план `docs/superpowers/plans/2026-10-02-mini-app-redesign-plus-staging.md`, 21 задача P01–P21, пока не утверждён.
- **Сохранено:** Free50/фото/quiet hours/старые функции, Viewer150 ₽/200/5 offline-inclusive видео, Streamer200 ₽ со всеми Viewer-правами frozen покупателю; канал-only для новых связей, legacy groups и HTML reports/export. Светлая/graphite/реальные Telegram ThemeParams, полноценные API/services, три способа внутри бота/Mini App, прозрачная Platega и строгие server gates. Непроверенные дополнительные события не продаются; public UI без технических demo/mock/staging labels, внутренние QA/guard остаются.
- **Будущая граница:** первый staging интерфейс полностью кликабелен, касса OFF: нет внешних money POST/invoice/fake success/grant. Точный месяц/XTR/refund/upgrade нужны перед соответствующими денежными операциями, не UI переносом; реальные support/operator/legal/merchant/NPD/schema/recovery данные — перед banking/внешним test API. Для настоящих payment tests, OAuth и Telegram send нужно отдельное конкретное разрешение. После утверждения плана последовательно один исполнитель и максимум один reviewer, TDD/scoped review/commit, полный финальный gate и pinned staging identity/SHA.
- **Проверка/граница сейчас:** writing-plans и самостоятельная сверка с spec/кодом, 47/47 locked skill hashes. Только новый plan и STATUS/DECISIONS; одобренный spec, продукт/tests/config/БД, макеты/HTML/архивы, secrets, staging/Railway/production не менялись. HEAD `e0d44c315a1c81dc5b310dd1f3c7f3169915c985`, staged пуст, commit/deploy/payments/outbound отсутствуют. Полный suite/browser/native не выдаются за новую проверку. Bank NOT READY, внешние/native/денежные сценарии NOT TESTED. Остановка до последнего утверждения плана.

## D-062 — четвёртый нижний пункт Plus; общий экран с входом из профиля

- **Дата:** 2026-10-02.
- **Решение владельца:** добавить отдельный пункт с точной подписью `Plus`, сохранить прежнюю композицию и вход из профиля; сначала обновить локальный интерактивный preview, затем остановиться. Порядок прежних пунктов сохранён, Plus добавлен последним в обоих режимах.
- **Реализация preview:** меню → существующий Subscription; Free «Возможности Plus», active «Моя подписка», сравнение Free/Viewer/Streamer, цены/кнопки/статус/срок/операции. Активный Plus в Subscription/purchase; Back к исходному экрану/фокусу/прокрутке, без дублей history. Четыре равные области в обычном размере; text200 переносит целые действия; CTA подключения при short viewport доступен в прокрутке и сохраняет focus.
- **Основание/проверка:** прямое сообщение владельца «Новая утверждённая правка»; новый `qa/plus-navigation.cjs`, initial missing-tab RED, сохранённые layout/focus RED → PASS124 Chromium/WebKit проверок, шесть source-bound PNG; `plus-navigation-qa.json`, syntax PASS, detector[], scoped просмотр/Stop-Slop новых подписей. Прежние источники/HTML/QA сохранены в archive и на исходных путях.
- **Граница:** только design preview/QA/документы, P09/P21 плана учли новую навигацию; весь implementation plan ещё не утверждён,21 задача. Product/staging/Railway/production/DB/config/secrets/payments/outbound не менялись; полный suite не перезапускался. Native SDK/OAuth/права/реальные оплаты NOT TESTED. HEAD `e0d44c315a1c81dc5b310dd1f3c7f3169915c985`, staged пуст, commit/deploy отсутствуют. После показа остановка.

## D-063 — полная реализация P01–P21 разрешена; Streamer300 и role-aware Plus

- **Дата:** 2026-10-02.
- **Основание:** финальный attachment `650afe6a-f60f-4201-bc2e-938a49c9d84a`, заменяющий прежний большой неотправленный промпт.
- **Решение:** последовательные P01–P21 и guarded pinned staging после gates разрешены. Viewer150 ₽; Streamer300 ₽/месяц включает Viewer frozen покупателю. 200 ₽ отменены в актуальных источниках, история сохраняется. По режиму Plus сразу предлагает нужный продукт, без главного внутреннего switch; четыре блока/SVG/«Все возможности»/вторичный Viewer/честная активная подписка.
- **Граница:** production, настоящие money POST/invoices/списания, fake success/grant и auto-renew запрещены. Три кликабельных метода внутри Mini App завершаются честной недоступностью. Реальные support/operator/month/refund/chargeback/upgrade/XTR/merchant/NPD/retention не придумываются, блокируют соответствующие операции, не UI/staging. OAuth владельца/Telegram send/native требуют конкретного разрешения/факта.
- **Проверка сейчас:** cwd/HEAD/staged, обязательные docs/plan, 47/47 locked hashes, адресные поправки текущих требований. Продуктовые checks/deploy ещё не выполнены. Один исполнитель; журнал `mini-app/REDESIGN-PROGRESS.md`; старые HTML/экспорт сохранены.

## D064 — Streamer включает личный Viewer фиксированному покупателю

- **Основание:** финальный промпт владельца §9 и P04.
- **Решение:** общий resolver/SQL используют immutable beneficiary, не actor/нынешнего владельца Twitch/участников сообщества. Старые test grants без buyer остаются unbound; конкретные продуктовые grants не копируются. Конец доступа — непрерывная сумма интервалов, без будущего разрыва.
- **Проверка:** P04 PASS130tests/43subtests, два conn/CAS, media expiry/revoke, сохранение отдельного Viewer. Два старых refund ожидания отражают новую effective семантику с сохранённой проверкой возврата конкретного продукта. Внешние проверки не выполнены.

## D065 — финансовый факт и денежный доступ разделены

- **Основание:** P06 утверждённого плана.
- **Решение:** durable attempt/inbox, canonical evidence и frozen buyer проходят общий атомарный apply. Unknown create/refund не повторяется автоматически; один unresolved order на buyer и refund на order. Shared provider lease ограничивает сверки между соединениями. Terminal fact не заменяется старым pending; конфликт сохраняется для review.
- **Граница:** callback404/runtimeOFF первого release; настоящий месяц/terms/refund/XTR не выдумываются. Fixture policy30days не утверждает продажу. Финансовые факты не вытесняются ради лимита.
- **Проверка:** P06 PASS72tests/57subtests, две DB/rollback/reopen/cancellation/refund/monotonic; `P06-LEDGER.md`. Внешние и native NOT TESTED.

## D066 — общий shell и verified профиль

- **Основание:** утверждённый P09. Четыре точных пункта и один Subscription с Profile/menu/Plus, max600/relative text/SVG/native dialogs. Имя только signed bootstrap; личные черновики по verified ID, прежний субъект очищается.
- **Проверка:** Python13tests/37subtests, Chromium/WebKit6/200/theme,26PNG, JS0/external0. WebKit оставляет MAIN focus; read-only reviewer подтвердил причину, восстановление допускает только прежний container и сохраняет новый user focus. Modal Back закрывает верхний диалог до изменения route.
- **Граница:** перенос feature страниц P10–P16 продолжается, не готовый продукт. `P09-SHELL.md`; native/staging NOT TESTED, внешних действий нет.

## D067 — перенесённые формы используют прежние сервисы

- **Основание:** утверждённый P12. Quiet/digest остаются Free и сохраняют DEFAULT1; paid формы следуют effective rights. Папки/фильтры/CAS/reminder queue/history retention используют existing services, временные browser fixtures не добавляются в live routes.
- **Проверка:** Python66tests/34subtests,18browser reports/78PNG и exact current SHA; `P12-SETTINGS.md`. Read-only reviewer отделил ошибочный исходный статус QA от actual ACK, default продукта сохранён.
- **Граница:** fake sender/SDK и временная БД; native/Telegram delivery/OAuth/staging НЕ проверены. P13 следующий, final full suite P19.

## D068 — канал проверяется до подключения, OAuth до успеха

- Основание: утверждённый P13 и CHANNEL-PERMISSIONS-CONTRACT. Новый flow принимает channel; legacy groups продолжают работать. Публикация требует can_post, чужие edit права не запрашиваются. Сетевая ошибка хранится как неизвестный результат.
- Проверка:48tests/33subtests,10reports/94PNG; атомарная отмена/срок после DB-lock, late SDK generation fence, verified OAuth binding перед success. Один scoped read-only reviewer; `P13-CONNECTION.md`.
- Граница: native send/edit/delete/requestChat/account OAuth НЕ проверены; fixture/SDK fake только локально. P14 следующий.

## D069 — предпросмотр не публикует пост

- Основание: утверждённый P14. Draft проходит server validator и прежний composer без записи/send; template/preset/apply/delete остаются отдельными действиями. Каждый placement имеет свой draft/version.
- Проверка:34tests/38subtests,4reports/34PNG; exact CAS/pending/403/expiry/reload/late ACK. Статистика — подтверждённые own IDs, без views/clicks.
- Граница: fake browser/DB/sender, native и staging NOT TESTED; `P14-POSTS.md`, следующий P15.

## D070 — покупка пока не создаёт платёж

- Основание: утверждённый P15. Режим выбирает основной Plus из каталога;150/300 и4блока, secondary Viewer. Все3метода внутри Mini App ведут к server503, деньги OFF независимо от credentials/restart.
- Проверка:35tests/57subtests,6reports/84PNG/hash, один reviewer. canonical confirmed/canceled и pending-expiry, frozen buyer/own orders, late re-entry проверены без ослабления assertions/index.
- Граница: локальный prepared ledger/SDK double, native/provider/staging NOT TESTED; `P15-PLUS-PURCHASE.md`. Далее P16.

## D071 — документ публикуется после принятия и необходимых данных

- Основание: утверждённый P16. Canonical source/version/SHA/catalog/owner acceptance плюс обязательные сведения проверяются сервером; manifest не снимает mandatory inputs. Контакт из SUPPORT_USERNAME/EMAIL один для Mini App и paysupport, без defaults.
- Проверка:55tests104subtests,4reports44PNG/current SHA; один reviewer подтвердил URI/mailto и legacy fixture corrections с прежними assertions.
- Граница: canonical5 unaccepted,реальный контакт отсутствует,bank NOT READY; TEMP ready-copy не принятие владельца. `P16-LEGAL-SUPPORT.md`; дальше P17.

## D072 — старые команды и экспорт сохраняются

- Основание: утверждённый P17. Gate охватывает old callbacks/common media/sixth/quiet/startup menu/HTML/legacy groups/50–200 import и новую копию/каталог. Новый onboarding/расширенные отчёты остаются отдельными предложениями.
- Проверка:387tests50subtests,40tests2667subtests,4reports44PNG/currentSHA; один reviewer и detector[]. Английские literals и все UI payment-controls защищены, legal принятиеfalse сохраняется.
- Граница: fake sender/TEMP DB/SDK double, native/real send/OAuth/payments NOT TESTED. `P17-COPY-COMPATIBILITY.md`; следующий P18.

## D073 — браузерный PASS и media-пределы учитываются отдельно

- Основание: P18/19. HTML приложения допускает iframe только exact Telegram Web origin; прочие CSP/XFO защиты сохранены. Пять личных слотов не доказывают производительность сервера.
- Проверка:92journeys/2520checks/984PNG/174sourceSHA; один reviewer/F1 closed; load cap2/deferred98/4998 и честный1×1000 RESOURCE_STOP25s. Backup/restore/migration-copy PASS, активная БД не заменена; фактический staging Fernet проверен без выгрузки ключа.
- Граница: full-suite/release/staging ещё впереди, native/OAuth/send/payment NOT TESTED; `P18-QA.md`, `MIGRATION-COPY.md`, `mini-app-threat-model.md`. Production fresh SHA dc9239 не подменяет исторические аудиты.

## D074 — P19 gate закрыт на финальном коде; guard повторяет suite

- Основание: утверждённые P19–P21 и фактические evidence.
- Проверка:1403passed/2skipped/3248subtests/903.06s exit0; существующие Windows symlink skips отдельно отмечены. Старое ожидание20versions обновлено пятью явными R11 migrations; reopen стал точнее, assertions/skip не ослаблены. Один scoped reviewer подтвердил исправление. Whole-change review/F1, threat model и actual staging backup/restore/migration-copy/ключ PASS.
- Решение: сохранить RED/PASS, backup вне Git и P21 draft, выпустить чистый commit штатным pinned guard. Guard самостоятельно повторяет полный suite и перепроверяет дерево/SHA/target. Runtime после P18 не менялся.
- Граница: actual deploy/SHA/bot/menu/assets ещё впереди; native/signed live API/send/OAuth/payment NOT TESTED. Payment OFF/bank NOT READY сохраняются. Production metadata fresh dc9239 записаны до выпуска без изменения production.

## D075 — первый инженерный staging release выпущен, приёмка ожидается

- Версия: код52e56e9633a77f7e5025a1be7dacbbf8c0769970/deploymentfba34520-f2a7-4d5d-aa86-1e8e3b0072b1/activeSUCCESS/getMeTwitchSignalTestbot8859004067/MenuWebAppПриложение exactdomain.
- Проверка: guard повторный full suite1403/2existing skips/3248subtests/850.45s; actual174file/14HTTP hashes/25versions/integrity/FK/unsigned401/paymentOFF/HTTPsecret0/last100logs patterns0/productionbeforeafterequal. Backup и прежняя БД/данные сохранены; active production466499d5/dc9239 не изменены.
- Operational review: uppercase-only header lookup и rawGitblob expectation исправлены в одноразовом probe после доказанных RED. Git archive с core.autocrlf создаёт CRLF; все174artifact hashes доказаны независимо. HTTP .read_text нормализует строки. Exact file/body hashes/полные наборы/DENY/auth/identity не ослаблены;7selftests PASS и один reviewer. Runtime/guard/config не менялись.
- Решение: P01–P21 завершены в разрешённом объёме, owner package готов. Документационный final commit не новая deployed версия. Owner-only voluntary7day flow сохраняет прошлые trial/grants, автоматически ничего не выдаёт. Previewstage1job/2sessions/20delay/60interval и честный overload/photo сохранены, mediaSLA не доказана.
- Граница: native/signed live API/ownerOAuth/real send/edit/delete/animation→photo/Stars/Platega NOT TESTED; деньги OFF,legal503/supportinput/bank NOT READY. Десять owner inputs остаются отдельными. Следующий круг — самостоятельная приёмка и замечания владельца; production launch/банковская подача не выполнены.

## 2026-10-03 — обычный Telegram UI, Smart Home и название входа в тариф

Владелец подтвердил новый Telegram Home из четырёх действий, постоянное «Меню», собственную live-сводку/баннер и официальные цвета кнопок. Редкие функции доступны в «Ещё», HTML/export и старые группы/команды/callbacks сохраняются. Последнее прямое copy решение отменяет подпись Plus у входов: Mini App/Ещё — «Тариф», профиль без доступа — «О тарифе», активный — «Моя подписка». Продукты Viewer Plus150₽/месяц и Streamer Plus300₽/месяц с Viewer включён остаются. Purchase CTA называет выбранный продукт; Stars/СБП/карта возвращают unavailable при first-release OFF.

Реализация и регрессии завершены локально, reviewer findings закрыты; full1462/2existingWindowsSkips и14browserreports PASS. Выкладка разрешена только штатным guarded pinned staging. Production, реальные деньги, OAuth владельца и отправка получателям не разрешены этой проверкой; native acceptance остаётся фактической отдельной проверкой.

Checkpoint 03.10.2026: разрешённый guarded staging выполнен на bd2534d/f48e90a5. Реальная проверка183artifact/15HTTP/identity/Menu/paymentOFF/production compare PASS; последующий evidence-only commit отдельно. Нативную приёмку, OAuth/получателей/деньги не выдаём за проверенные; остановка для владельца.

## Mini App polish — 03.10.2026

- Основание: владелец attachment79fa28bf-e920-488e-9220-9355b690e45d поручил полный визуальный аудит и самостоятельные точечные исправления. Новый стиль/стек не вводятся; прежние ограничения staging/production/payment действуют.
- Исполнитель один; работа началась только после idle предыдущего чата на098736f. Нет parallel writers.
- SafeAreaInset и contentSafeAreaInset складываются как независимые области; CSS env заменяет системный источник, не добавляется к нему. Основание: официальный Telegram-iOS45cc8468, WebAppController.swift915–1060; неверное прежнее max-ожидание исправлено явно, геометрические проверки усилены.
- Не повторяем bootstrap на401/403; повтор сетевой ошибки одноразовый в полёте и отменяется приpagehide. Сообщения подключения scoped к обзору канала и ревизии; публикации scoped к текущему экрану.
- Payment readiness отображается до выбора, три способа сохранены; постоянный503 не получает бессмысленный повтор, сетевой сбой получает. Права/цены/биллинг не менялись.
- Чистая ветка сохраняется без merge/push main/master. Штатный guard выполняет полный suite финального commit перед staging; нельзя считать старый full gate доказательством нового кода. Native visual acceptance отдельно.

- Итог polish: release3189fa8 прошёл guard full1462/3274 и фактическую pinned staging проверку185files/Testbot8859004067; deployment36e6e769 activeSUCCESS. Нативная приёмка и внешние коммерческие блокеры явно остаются NOT TESTED/NOT READY. Ветка сохраняется, только evidence/docs checkpoint после release.


## Правки владельца Mini App — финальный локальный checkpoint 03.10.2026

Принятый голосом объём интегрирован в codex/mini-app-owner-corrections (база9aac74e). Меню на staging пользователь подтвердил «Да, работает»; остальные изменения ещё не развёрнуты. Избранное/свайп/серверный Undo, права/аватары, компактные тарифы и Подробнее, Главная/Профиль/темы, настройки отчётов сохранены в рабочем дереве. Итоговая component matrix: Chromium115 и WebKit115 PASS, по37PNG, без входа/SDK/API. Reviewer подтвердил закрытие трёхP2, detector[]. Подробности/ограничения: docs/audits/mini-app-owner-corrections-2026-10-03/REVIEW.md. Впереди backup, migration-copy, полный gate точного commit и pinned staging.


Последняя правка владельца к персонажу учтена: прозрачный полный силуэт, крупная композиция Главной; рабочие снимки показаны в чате. RELEASE matrix: Chromium 137 / WebKit 137 PASS, по 41 PNG; cutover 19 PASS / 2 subtests, shell/copy 9 PASS / 3024 subtests. Backup 60 таблиц PASS. Точные итоговые сведения: docs/audits/mini-app-owner-corrections-2026-10-03/REVIEW.md. Ожидаются migration-copy и полный guard перед staging.


Миграция committed кода 38f5f21 на свежей копии staging завершилась PASS: 60→63 таблицы, 27 schema versions; все прежние строки/ID сохранены, reopen и rollback после искусственной ошибки подтверждены, два Fernet-поля читаются текущим ключом без его экспорта. Активная БД не заменялась, исходящих сообщений нет. Selftest release smoke: 7 PASS. Следующий шаг — штатный полный guard/deploy на чистой ветке autonomous/twitchsignal-roadmap.

## 04.10.2026 — постоянный возврат и приоритет live в «Ещё»

Прямое решение владельца: «📡 Мои стримеры» и «🔴 Сейчас в эфире» в первом ряду; настройки/Telegram-каналы во втором; тариф/помощь в третьем; reports вторичны, admin owner-only, Home внизу. Переиспользуем menu:live.

ReplyKeyboard [Меню] и system WebApp [Приложение] остаются независимыми. MenuStore хранит последний успешный delivery/type/time отдельно от editable Home; API не подтверждает клиентскую видимость. Bounded recovery при cold/reset/expiry/замене, без повтора при обычном warm Home. Общий lock selector/restore и generation guards; неблокирующий pre-recovery не задерживает отмену. При DB failure нельзя утверждать, что подключение отменено. Desktop BEFORE подтвердил совместное отображение и Menu→Home на старом runtime. AFTER остановлен пользователем Escape, NOT TESTED; нативная приёмка открыта.

Owner-approved admin mockups сохранены без изменения в отдельном2a418bb. Full guard39bc821 и deployment5211c9f8-6a2f-4834-9aa6-88706a4a34a7 PASS; actual production/payment/identity evidence в RELEASE.md. Scoped owner MenuButton default означает наследование exact global web_app по официальному контракту; helper принимает только exact default/null/null или ту же global кнопку, commands override остаётся запрещённым. Telegram config/runtime не менялись ради smoke. Без native AFTER не объявлять Desktop-приёмку завершённой.

## 04.10.2026 — production preparation при остатке allowance около5%

Владелец разрешил focused fixes существующих findings и clean committed handoff при малом лимите, запретил production deploy/config/DB, реальные sends/OAuth/деньги/main push. Runtime snapshot9e899300fbc222058a2ca746fb90336d78a8d2a5/tree3568b006d9431f76b6dfe903e077af94ac94ab9c: SEC-01 actor/expiry и fresh Telegram permission непосредственно до domain write; SEC-02 local limit до lookup,6/user/10s и30/global/10s,10s cache256/5s timeout, route-only gate без изменения poller; SEC-03 bounded hashed client states без чужого eviction и4 streamer sessions/user с отзывом только своих; UX first-error Retry без redesign. Existing signature/auth/Free/HTML/groups/payment OFF сохранены. Все четыре fixes прошли RED→focused PASS;14/22/30/13 tests, browser8 checks в actual light/dark tokens. Scoped reviewer выявил nonce consumption до linked identity, исправлен порядок без await между consume/session. request.remote/NAT/proxy quota остаётся обязательным release pre-flight.

Полный suite на чистом committed runtime9e89930 запущен один раз:1582 PASS/2 исходных skips/3656 subtests/1085.49s/exit0. Затем менялись только QA theme fixture, evidence и docs; runtime/tests unchanged. B quiet/raid copy и pending updates, C fail-closed admission, D разрешённая isolated production copy/key/external backup/rollback открыты. Существующий D-048 не является явным решением исключить raids из quiet-hours; его canonical уточнение остаётся B. Документированный cutover runbook не считать реализованным runtime guard или проведённой миграцией.4/8 пунктов текущего исправительного scope закрыты; production readiness не объявляется. Production не затронут, исправления в staging не опубликованы. Следующий исполнитель начинает с handoff/checklist, не повторяет общий audit и не перезапускает R/P/T этапы.

## 04.10.2026 — закрытие B/C и честная граница production D

Основание: прямой запрос continuation из attachment70c196b6, базаb175782, один исполнитель/один read-only reviewer. Production deploy/config/DB/main push/real sends/OAuth/money не разрешены. Community skills применены к TDD/debug/review/verification, сторонние workflow helpers не исполнялись; постоянный компактный CONTINUATION-PLAN заменяет их временный ledger.

Canonical B1: D-048 и current legacy tests не утверждают исключение raids. Исправить только help/confirmation, сохранить personal live+raid quiet/exemption/fresh retry и отдельную community publication; Free50 неизменен. B2: pending retention с durable received до offsetACK; ordinary processing после crash→unknown, без угадывания опасного дубля; financial replay через прежний durable billing ledger. Нет обещания exactly-once send, FSM MemoryStorage не стал durable. Общий32-budget recovery+live, owned tasks cancelled+await до DB close.

C: отдельный frozen local operator contract/hash вне Git, exact Railway/env/volume/mount/DB/required-secret/one-replica/queue/paymentOFF validation, затем точный getMe/OS lock **до** Database construction. Queue требует admitted; старые staging-only guards не сняты глобально. Runtime3fa8b649e737693eef94c3912c22313834f57586. Одна replica из env и новый lock не доказывают остановку старого artifact; maintenance preflight остаётся обязательным. request.remote не доверяет XFF/Forwarded, shared-peer quota4/300s fail-safe; actual production edge/TLS/peer boundary не доказана.

D: разрешённой representative production copy нет; не подставлять staging. Tooling5474e58 проверено на synthetic old artifact/temp DB: sealed backup API/immutable-source/no sidecars, hash/rows/schema definitions/approved additions/key/HTML, migration-only Database.connect, reopen и fresh sealed rollback после нового path. Предыдущие Windows IOCP/network-guard, read-only WAL-sidecars и rollback-order findings воспроизведены и исправлены. Synthetic result никогда не закрывает D; external storage/download+restore, existing key и exact rollback artifact подтверждает оператор. Active production не читалась и не изменялась.

Final focused110PASS/118subtests; fresh scoped review clean5474e58 без конкретных code blockers. Единственный final full gate PENDING; после него только docs/evidence, код/tests не изменяются. Итог preparation7/8≈88% и PRODUCTION PREPARED — OWNER INPUT REQUIRED, cutoverNO; READY не заявляется. Native Desktop/iOS/Android/owner acceptance и реальные разрешённые smoke/OAuth/media NOT TESTED. OWNER_CHAT_ID425785231, quiet/raid, цены150/300 не спрашиваются заново. Пять групп недостающих production inputs внесены отдельно; денежные/legal решения и согласие владельца не выдуманы.

Дополнение final gate: первый full5474e58 RED1634PASS/2FAIL/2existingSkips/3741subtests/1144.44s. Не скрыт и не назван PASS. Scoped reviewer подтвердил lifetime RSS от347MiB tar in-memory и устаревшее assertion raid exemption. Исправлен streaming exact Git archive в exclusive TEMP file, hash/extraction/filter сохранены; прежний RSS256MiB не повышен. Copy assertion усилен canonical quiet/raid/exemption/community и negative ложного обещания, остальная иерархия/catalog сохранены. RED подтверждён combined22-case run; после fix22PASS/6subtests. Bot runtime3fa8b64 unchanged; новое tooling изменение требует повторного полного gate на clean snapshot, далее только evidence/docs.

## Итоговая проверка8690094 — 04.10.2026

Итоговый gate CONSOLIDATED PASS на869009401fc638405694297b8449e04c1d96abe8: полный1636PASS/2existingWindowsSkips/3739subtests/1136.57s, exit1 из-за двух byte-invariant subtests. Managed checkout с core.autocrlf=true добавил CR в .python-version/Procfile; immutable Git blobs и primary checkout уже имели правильные LF/хеши. Только validation bytes восстановлены из HEAD; свежий whole packaging15PASS/12subtests/0.40s, exit0. Код/tests/assertions/HEAD/deps/global Git config не изменялись. Scoped reviewer подтвердил reuse1636PASS без третьего full. Distinct consolidated1636PASS/3741subtests/2existingSkips; overlap15tests/10subtests не суммируется. FULL-BC.log/FULL-SECOND-RED.json сохраняют полный RED как RED, не fullPASS. FULL-GATE.json, PACKAGING-RECHECK.log и CHECKOUT-BYTES.json связывают correction и PASS. Первый code-related RED также сохранён. Далее только docs/evidence.

Полный прогон выполнен в отдельном clean validation checkout того же коммита только для тестов, без второго разработчика. Чужие untracked output/imagegen assets в основной папке сохранены без move/delete/stage; вся рабочая папка с ними не объявляется clean. Материализованный handoff с literal final HEAD находится рядом с внешним production-checkpoint.json.

B/C закрыты локально; D/production identity/external restore/native/OAuth — OWNER INPUT REQUIRED. Production untouched YES, payments OFF YES, cutover NO.
