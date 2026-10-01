# R9 — synthetic 20k/30k/40k validation: design

Дата: 2026-10-01. Основание: `docs/ROADMAP.md` R9, результаты R3 `docs/audits/2026-09-30-r3-results.md`, фактический R8 staging checkpoint `docs/audits/2026-10-01-r8-growth-staging.md`. R9 измеряет поведение текущей архитектуры; он не утверждает целевое SLA и не переключает SQLite на PostgreSQL без данных.

## Цель и границы

Измерить на последовательных синтетических профилях 20 000, 30 000 и 40 000 назначений путь shared Twitch observation → durable live update jobs → fake Telegram sender с восстановлением lease после simulated restart. Для каждого размера записать latency очереди и DB, depth, CPU/RSS, размер DB/WAL и свободное место. Отдельно проверить preview concurrency, mock payment idempotency и consistent backup/restore. Все ID, логины, сообщения и медиа синтетические; никакого массового Telegram/Twitch API трафика, реальных платежей, production данных, изменения тарифов или платной инфраструктуры.

## Выбранный подход

Новый `scripts/r9_scale_validation.py` использует реальные `Database`, `NotificationQueue`, `NotificationWorker`, `PreviewManager`, mock billing и `scripts.sqlite_backup`, но создаёт БД только в новом OS temporary directory. Запуск в Railway дополнительно сверяет точные project/environment/service IDs с `scripts/staging_target.json` и требует staging. Каждое значение 20k/30k/40k запускается отдельным процессом последовательно, с ограниченными batch sizes и fake sender. Команда никогда не открывает `/data/bot.db`; payload не содержит Telegram token или персональных данных.

Режим `shared` из R3 показывает низкую стоимость одной записи sample на Twitch-логин, но не измеряет задержку full fan-out. Простое повторение старого harness оставило бы этот пробел. Внешний load service создал бы ненужную зависимость и риск сетевого трафика. Поэтому смешанный профиль собирается из существующих компонентов в изолированной БД.

## Сценарий и метрики

1. С фиксированным seed создать `N` разных фиктивных chat ID и `N/100` Twitch-логинов, по 100 назначений на логин. Подтвердить отсутствие коллизий и начальный нулевой backlog. Для новых temp DB выполнить полную текущую миграцию.
2. Выполнить минимум два poll-like раунда: shared observation для каждого логина и обновление live jobs для всех назначений. Не создавать по sample на destination. Замерить p50/p95/p99 одного observation write, snapshot tracked и `request_live_updates`, число уникальных jobs и max revision; `N` не умножается на число раундов при подсчёте jobs.
3. После первого enqueue взять короткий lease одного job, закрыть и снова открыть временную БД. После истечения lease вернуть тот же job, отвергнуть устаревший ack, затем fake worker отправляет все jobs. Замерить p50/p95/p99 от постановки job до fake send, throughput, максимум и финальную глубину. Отдельно показать расчёт нижней границы времени при 40 ms global start interval; это не измерение Telegram API.
4. Зафиксировать CPU process seconds и wall seconds по фазам, peak RSS, DB/WAL bytes, свободное место до и после. Профиль завершён только при `done=N`, `pending=leased=failed=0`, одном job на destination, актуальном revision и `PRAGMA integrity_check=ok`.
5. Preview: реальный coordinator c fake capture/send при concurrency 1/2/4 из R3; дополнительно ограниченный локальный/staging FFmpeg тест на синтетическом 854×480 H.264 MP4 без аудио, чтобы показать CPU/RSS/длительность и размер без Twitch source. Он не меняет 6→12→18→24 live pipeline и не выдаётся за сетевой preview E2E.
6. Billing: только `MockPaymentProvider` и временная DB; повтор одного verified event и конкурентные попытки не создают второй payment/grant, refund/cancel/expiry сохраняют существующие правила. Записать число повторов и инварианты, не денежную конверсию.
7. Backup/restore: `sqlite3.Connection.backup` временного профиля, новый restore target, integrity и schema/row counts; staging active DB перед deployment дополнительно получает отдельный внешний snapshot, как в R8.

## Ограничения ресурсов и безопасность

- Не запускать профили параллельно; при недостатке свободного temp диска, превышении 256 MiB RSS до drain или отдельного 15-минутного wall budget профиль останавливается и фиксируется как capacity limitation. После первого такого ограничения не форсировать следующий больший размер на Railway; локальные профили продолжаются отдельно. Фальшивый worker не создаёт внешних запросов.
- Не публиковать идентификаторы, бот-токены, содержимое staging DB или сырые платежные события в JSON-отчёте. Сохранять агрегаты, seed, параметры, версии Python/SQLite, commit и среду. Скрипт отказывает неизвестному environment и не принимает путь к активной DB.
- Синтетический успех не подтверждает задержку реального Telegram, реальные Twitch poll циклы, эффективность при нескольких Railway replicas, streaming capture под сетевой нагрузкой или готовность production. Если DB p95/lock/WAL растут непропорционально, зафиксировать evidence и отдельное решение о PostgreSQL/architecture, без автоматической миграции.

## Приёмка

- Unit/integration TDD на отказ вне staging, ограничения размера/пути, маленький mixed profile с `jobs=N` после двух раундов, lease recovery/stale ack, нулевую потерю и backup/restore. Отдельные focused проверки billing replay и preview contract.
- Полный suite и code review на чистом commit, pinned staging target, guarded deploy, `SUCCESS`, bot identity и active DB integrity. Затем последовательные stage 20k/30k/40k только при прохождении resource guards; локальные сравнимые прогоны. Машинные JSON и датированный audit показывают как успешные, так и прерванные профили.
- В `docs/STATUS.md` и `docs/DECISIONS.md` записать фактический предел, следующие архитектурные решения и непроверенные внешние части. Production и `main` не меняются.

## Самопроверка

Каждый отчёт маркируется synthetic, содержит фазу и базу latency. One-job-per-destination проверяется независимо от числа poll rounds. Две БД в сценарии означают временную DB и её восстановленную копию; активная staging DB в workload не участвует. При ограничении ресурсов результат называется неполным, а не 40k acceptance. Telegram rate limit остаётся аналитической границей, не успешным отправленным сообщением.
