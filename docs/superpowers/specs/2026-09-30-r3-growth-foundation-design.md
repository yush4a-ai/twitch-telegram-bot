# R3 — Commercial Growth Foundation: design

Дата: 2026-09-30. Основание: `docs/ROADMAP.md`, R0 audit/risk register, R1 staging/backup runbooks и текущий код `bot/database.py`, `bot/poller.py`, `bot/preview_runtime.py`. Исполнение ограничено локальным контуром и Railway staging; production и реальные деньги не участвуют.

## Цель и проверяемый результат

Рост числа назначений одного Twitch-эфира не должен умножать одинаковые stream observations в хранилище или делать Telegram fan-out частью времени Twitch poll. Отдельная очередь должна переживать restart, выдавать ограниченное число Telegram отправок, показывать backlog/возраст и сохранять существующие уведомления, отчёты и preview lifecycle. Измерения, а не предположение, определяют необходимость PostgreSQL. Текущий один процесс, SQLite WAL и одна Railway replica сохраняются до измеренного основания для изменения.

## Текущие факты и границы

- `Database` держит одну `aiosqlite.Connection` и сериализует writes через `_write_lock`; чтения на той же connection тоже проходят последовательно в её worker thread. Из этого нельзя вывести предел пользователей без замера latency/queue wait.
- `stream_samples` записывается в цикле назначений (`poller.py`), а `stream_chatters` уже разделяется по физическому эфиру. `stream_history` и report state остаются per destination. Текущие samples старых сессий нельзя переписать без проверки отчётов и retention.
- `_check_once()` выполняет стадии последовательно, а `_check_streams()` ждёт Telegram и DB внутри назначения. Превью уже имеет лимит capture/artifact, но его отправка по получателям последовательна. `/healthz` не подтверждает Telegram delivery/preview.
- `tracked_channels` уже хранит CAS/pending media state, `report_deliveries` является отдельным durable outbox. R3 не заменяет эти механизмы без эквивалентного теста crash/retry.

## Выбранный путь

Сравнивались: (A) сразу перейти на PostgreSQL и внешний broker, (B) оставить прямую отправку и оптимизировать SQL, (C) добавить измеряемую SQLite-backed очередь и общий stream snapshot при сохранении одного процесса. Выбран C: он разрывает наблюдаемую связь poll/Telegram, не вводит второй источник истины, даёт replay после restart и позволяет измерить предел текущей платформы. Вариант A не обоснован latency evidence; B не решает durable backlog.

## Измерительная опора

Новый локальный harness использует только синтетические chat/login/stream ID и временную SQLite DB. Профили назначений 1k/5k/10k, затем R9 20k/30k/40k; фиксированное seed, распределение нескольких назначений на один Twitch-канал, режимы warm/cold и отдельные операции read state, enqueue, claim, acknowledge, sample insert, report read. Он пишет JSON с количеством операций, размером DB/WAL, p50/p95/p99 latency, throughput и верхним RSS процесса. Harness не обращается к Telegram/Twitch/Railway и не импортирует staging/production DB. Порог миграции PostgreSQL задаётся после результата, а не подставляется задним числом.

## Общие данные эфира

- Для новых logical stream IDs вводятся `stream_observations(twitch_login, stream_id, sampled_at, viewer_count, title, game_name)` и компактная `stream_observation_memberships(chat_id, twitch_login, stream_id, sampled_at)`. Обе таблицы `WITHOUT ROWID`. Уникальный ключ observation — `(twitch_login, stream_id, sampled_at)`; строка observation записывается однажды за poll/stream, membership точно указывает, какое назначение участвовало в конкретном sample. Интервал first/last не используется: при пропуске одного poll он добавил бы чужую точку в отчёт. При ошибке позднего destination уже обработанные samples сбрасываются перед выходом из poll.
- Existing active logical streams, имеющие строки в `stream_samples`, остаются в legacy режиме до окончания; новые streams используют общую таблицу. Решение режима принимается на уровне `(twitch_login, stream_id)`, чтобы новый destination старого stream не получил смешанные источники. Отчётный `get_stream_samples(chat_id, login, stream_id)` возвращает только observations с точной membership или legacy samples и сохраняет прежнюю сигнатуру/сортировку. Остальные readers (`list_live_channels`, `get_live_post_details`, owner live snapshot и report builder) используют тот же источник по режиму stream. Ретенция удаляет общие observations только когда нет active/pending destination и нет свежей history, требующей их; membership чистится согласованно.
- Migration только additive и идемпотентная. До staging schema deploy выполняется внешний staging snapshot/export плюс online backup/restore drill по R1; активная БД не копируется из production. Старая таблица не удаляется в R3.

## Durable Telegram fan-out

- `notification_jobs` хранит `(kind, chat_id, twitch_login, logical_stream_id, payload_version, due_at, status, attempt_count, lease_until, created_at, updated_at, last_error_class)` с уникальным ключом `(kind, chat_id, twitch_login, logical_stream_id, payload_version)`. В payload нет Telegram/Twitch токенов; данные для поста собираются из текущего DB state и безопасного stream snapshot. Новый job создаётся в той же SQLite transaction, что переход destination state; тест crash window проверяет атомарность этой границы. Retention удаляет только старые terminal `done`/`failed` jobs; `pending` и `leased` по возрасту не удаляются.
- Отдельный bounded worker claims due jobs через lease, достаточный для всего serial batch одного чата (число одновременно выданных jobs × send timeout + интервалы). Он ограничивает старты отправки до 25/с глобально, 1/с в private chat и менее 20/мин в group chat (интервал 3,1 с), ориентируясь на [Telegram Bot FAQ](https://core.telegram.org/bots/faq). Другие пути отправки бота пока не входят в этот общий бюджет; `RetryAfter` остаётся обязательной защитой. Worker трактует Telegram `RetryAfter` как `due_at` без sleep poller и сохраняет transient failure для retry. Terminal Telegram errors помечают job failed с безопасным классом ошибки. По restart просроченный lease вновь доступен для claim; `ack`, `defer` и `fail` требуют текущий `attempt_count`, поэтому поздний worker не меняет состояние уже повторно выданного job. Idle per-chat timing state очищается. Отмена подписки/смена stream ID делает старый job stale и безопасно пропускается.
- Exactly-once внешний Telegram send недоказуем при crash после успешного send до записи acknowledgement. R3 обещает durable at-least-once с idempotency в DB и CAS на tracked message state; в этом узком окне возможен дубль, который измеряется/логируется как риск, а не скрывается.
- Перевод live post delivery идёт через staging feature flag и отдельные TDD пакеты: сначала enqueue/claim/recovery, затем new go-live, затем update/cleanup и rollback на прежний путь. Флаг запрещён в Railway вне staging. Enqueue go-live и переход tracked state выполняются одной транзакцией; повторный poll с ожидающим job не запускает legacy send. Если worker завершил job между снимками tracked state и очереди, poll перечитывает message ID перед старым resume send. При гонке с worker обновление poll сохраняет уже записанный message ID, включая переход в offline для последующей очистки. Worker проверяет stream ID/notify state до отправки и фиксирует message ID через CAS; если состояние сменилось во время send, он пытается удалить устаревший пост. Report outbox и preview media CAS остаются отдельными до собственных доказательств. Не включать два независимых sender для одного destination одновременно.

## Preview и эксплуатация

- Существующие лимиты capture (2) и artifact jobs (1 по умолчанию) остаются. Harness измеряет очередь/latency preview при 1/2/4 одновременных эфирах с fake renderer/sender; реальные render и Telegram upload проверяются отдельно только на staging. Preview failure не блокирует основную очередь и poller; `healthz` остаётся in-memory readiness.
- Owner panel добавляет количество pending/leased/failed notification jobs, возраст старейшего due job и watermark обработки; метрики различают backlog и успешную доставку. Ресурсы CPU/RAM/disk берутся из наблюдаемого процесса/тома; показатели без источника остаются `unknown`.
- Rollback: выключить staging feature flag, остановить worker, оставить очередь и additive schema, применить предыдущий commit snapshot через R1 guard. Неразосланные jobs не удалять автоматически. Если DB schema несовместима со старым кодом, rollback только после отдельного restore drill, а не слепым redeploy.

## Приёмка

1. Harness воспроизводим с фиксированным seed; выдаёт метрики и raw config без личных данных. Сравнение baseline/после разносит DB latency, queue wait и fan-out latency.
2. TDD доказывает одинаковый report sample sequence для legacy/new streams, отсутствие чужих samples при присоединении destination позже или пропуске одного poll, сохранение active data в retention и одну общую observation при N destinations.
3. TDD доказывает unique enqueue, lease/recovery, RetryAfter без задержки poll, terminal failure, stale job, rate/concurrency bounds и идемпотентные изменения tracked state. Есть тест crash-window с честным at-least-once выводом.
4. Полный локальный suite, независимый diff/security review, staging backup и guard deploy, `getMe=TwitchSignalTestbot`, staging E2E на синтетических destinations, queue depth/latency и recovery после controlled restart. Нет production deploy, production variable/data touch и push `main`.
5. `docs/STATUS.md` и `docs/DECISIONS.md` фиксируют измерения, feature flag state и решение оставить SQLite или перейти на PostgreSQL; выбор не делается до evidence.

## Самопроверка

У каждого требуемого блока R3 есть источник данных, миграция, тест и staging gate. Новые таблицы и очередь не меняют платёжный выбор и не требуют второго процесса. Режимы legacy/new исключают смешанный отчёт. Preview concurrency и Telegram rate пределы обозначены измерительными профилями, а не выдуманными SLA. Production действия отсутствуют.
