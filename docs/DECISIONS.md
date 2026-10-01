# Решения TwitchSignalBot

Записываются только подтверждённые решения. Новые продуктовые/коммерческие параметры здесь не предполагаются.

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
- **Последствие:** прямой URL и подписанный чужой ID не открывают данные; на staging это подтверждено отрицательными тестами и синтетическими подписями. Реальный browser widget пока сообщает `Bot domain invalid`; домен staging нужно разрешить в BotFather только для тестового бота. Удалять fallback до реального E2E нельзя.
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
