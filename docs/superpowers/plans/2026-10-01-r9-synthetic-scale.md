# R9 Synthetic Scale Validation Implementation Plan

> Выполнять в этом видимом Work одним основным writer через `superpowers:executing-plans`; отдельную implementation session не создавать. Для нового поведения применять RED → GREEN → review и короткие commits.

**Goal:** получить воспроизводимые synthetic 20k/30k/40k результаты полного shared observation → durable queue → fake sender пути и доказать recovery, preview, mock payment и backup/restore без пользовательских данных.

**Architecture:** изолированный Python workload на новой temp SQLite DB с реальными DB/queue/worker объектами и fake network; отдельный CLI отказывает вне pinned staging и сохраняет агрегированные JSON. Preview и billing остаются отдельными короткими probe, чтобы ограничить время и память каждого профиля.

**Tech Stack:** Python 3.12, aiosqlite/SQLite, aiogram-independent queue components, pytest, локальный FFmpeg/ffprobe; без новых зависимостей и внешних load APIs.

**Spec:** `docs/superpowers/specs/2026-10-01-r9-synthetic-scale-design.md`.

## Global Constraints

- Только временные synthetic DB; никогда `/data/bot.db` или production. Railway execution требует pinned staging project/environment/service IDs и `TwitchSignalTestbot`.
- Одно значение N на процесс; строго 20k, 30k, 40k для CLI, small N разрешён только unit/integration API. Не отправлять Telegram/Twitch requests; fake sender.
- Два poll-like раунда минимум; уникальных durable jobs ровно N, final backlog/failed 0, stale ack rejected, `integrity_check=ok`.
- Остановить последующие большие staging профили после resource guard или 15 min limit; результат не объявлять pass. Никакой смены DB engine, реального payment provider, production deploy или push main.
- Числа latency всегда указывают базу отсчёта; synthetic metrics не равны реальной доставке и не задают SLA.

## Review Focus

- Активный `DB_PATH` или path вне temp никогда не откроется CLI/workload; тест на прямую подмену пути в Task 1.
- Повторный poll может повысить revision, но не число jobs; тест с двумя раундами и одной destination в Task 1.
- Lease после reopen может быть stolen, но старый ack не должен завершить новый lease; тест в Task 1.
- Переполнение RSS/temp disk и timeout останавливает профиль с явным partial result, без продолжения 30k/40k; тест guard в Task 1.
- Два одновременно подписанных mock события не создают два grant; тест replay в Task 2.

## Task 1: Изолированный mixed-load harness

**Files:** создать `scripts/r9_mixed_load.py`, `scripts/r9_scale_validation.py`, `tests/test_r9_mixed_load.py`, `tests/test_r9_guard.py`.

**Interfaces:** `run_mixed_profile(destinations:int, *, rounds:int=2, seed:int=20261001, temp_root:Path|None=None, max_rss_bytes:int=268435456, max_seconds:float=900) -> dict`; `validate_r9_runtime(environ:Mapping[str,str]) -> None`; CLI `python -m scripts.r9_scale_validation --destinations 20000 --output <new.json> [--staging]`.

- [x] RED: 2 ожидаемых import error до новых модулей; тесты на недопустимый N/rounds, temp-only DB, pinned stage runtime, один job после двух updates, shared observation count `N/100`, lease reopen/stale ack, final done=N/depth=0 и integrity.
- [x] GREEN: компактный dataset, фазовые таймеры, bounded queues/worker, CPU/RSS/disk snapshots, backup/restore временной DB, result schema с seed/no network. Focused 3 passed/5 subtests, связанные R3 queue regressions 20 passed/8 subtests; 1000 destinations дали 1000 done, 20 shared observations, revision=2, integrity=ok.
- [x] Review: DB и TemporaryDirectory закрываются в `finally`/context manager, output агрегирован без токенов/ID; `git diff --check` и 5k calibration (5000 done, revision=2, integrity=ok, 33.49 s, peak RSS 185 270 272 B) пройдены. Пакет готов к commit.

## Task 2: Preview и mock billing probes

**Files:** создать `scripts/r9_preview_probe.py`, `scripts/r9_billing_probe.py`, `tests/test_r9_preview_probe.py`, `tests/test_r9_billing_probe.py`; использовать существующий `scripts.load_harness.run_preview_profile` без его перестройки.

**Interfaces:** `run_preview_probe() -> dict` собирает coordinator concurrency 1/2/4 и ограниченный синтетический FFmpeg H.264 854×480 без аудио; `run_billing_probe(replays:int=100) -> dict` использует только MockPaymentProvider/temp DB.

- [x] RED: новые модули дали ожидаемые import errors; отдельный тест CPU-метрики сначала выявил скрытый `ffmpeg -benchmark` вывод. Проверяются concurrency, ffprobe contract, concurrent billing replay, refund/cancel/expiry и подпись.
- [x] GREEN: probe без Twitch/Telegram/real payment network, временный FFmpeg MP4 и `:memory:` mock billing; агрегаты с `synthetic=true`. Focused 3 passed; связанные R5/preview regressions 121 passed, 11 subtests перед последней правкой только FFmpeg loglevel.
- [x] Review: после последней правки 499 passed, 2 skipped, 225 subtests; 24s encode: H.264 854×480/30 fps, 3 244 466 B, audio=0, wall 2,87 s, CPU 2,218 s; 100 concurrent billing replays дали один payment/grant. Локальный FFmpeg не сообщил maxrss, поле оставлено `null`; timeout/cleanup ограничены. Пакет готов к commit.

## Task 3: Локальные профили и интерпретация

**Files:** machine outputs в `docs/audits/2026-10-01-r9-local-20k.json`, `...-30k.json`, `...-40k.json`, первый audit draft `docs/audits/2026-10-01-r9-scale.md`.

- [x] По одному процессу и новому output на 20k, 30k, 40k; все три локально завершились без resource stop. JSON содержат p50/p95/p99 observation/queue/DB, wall/CPU/RSS/DB/WAL/free disk.
- [x] R3 shared-only baseline используется только как контекст; аналитический предел Telegram 40 ms равен 800/1200/1600 s, измеренный fake drain 54,825/128,770/130,905 s. Ни один из них не является реальной доставкой или SLA.
- [x] Аномалия 30k: drain throughput 233/s против 365/s при 20k и 306/s при 40k; monotonic regression не подтверждён 40k. Причина без trace не установлена, повторять профиль только ради сглаживания цифр не требуется; сохранить все исходные JSON.

## Task 4: Staging validation

**Files:** `docs/audits/2026-10-01-r9-scale.md`, `docs/STATUS.md`, `docs/DECISIONS.md`.

- [x] Полный suite на `ccb958d`: 1126 passed, 2 skipped, 383 subtests локально и повторно в guard; код review/clean Git, pinned target. Online staging backup 688 128 B, внешний SHA match/restore и migration drill 39 tables/10 versions.
- [x] Guarded staging deployment `729e0070-709e-4dc7-a04f-40cef6031447` terminal/active `SUCCESS`; `getMe=TwitchSignalTestbot`, active DB integrity OK; production deployment `2d440603-b74f-4c03-ba42-a5d53a491c02` без изменений.
- [x] Последовательно 20k/30k/40k в `/tmp` staging, все завершились без resource stop и без внешнего трафика. Три stage JSON сохранены; preview/FFmpeg и mock billing probes прошли, active DB после нагрузки `integrity=ok`, 39 tables, growth/billing rows 0.
- [x] Аудит фиксирует цифры, пределы метрик, решение об SQLite/очереди/preview и непроверенные реальные условия; STATUS/DECISIONS обновлены, шесть JSON сверены. Результаты входят в отдельный docs-only commit.

## Самопроверка плана

Task 1 доставляет тестируемый путь масштаба без остальных probes; Task 2 независимо проверяет preview и billing. Полный suite выполняется один раз перед каждым необходимым staging deploy на точном commit; docs-only результаты не требуют повторного полного suite. При capacity stop R9 фиксирует предел, продолжает только независимые безопасные проверки и не создаёт ложную 40k acceptance.
