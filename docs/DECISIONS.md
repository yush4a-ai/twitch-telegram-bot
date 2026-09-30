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
