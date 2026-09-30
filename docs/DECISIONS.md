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
- **Последствие:** production config отвергает флаг даже при ошибочно заданном имени окружения; staging флаг пока 0 до полного gate и отдельного deploy/smoke. Обновления уже существующих live-постов и offline cleanup остаются прежними до следующего пакета.
- **Пересмотр:** после staging Telegram E2E и измерений backlog/latency.
