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

- [ ] RED: fake capture concurrency не превышает лимит; FFmpeg output duration/resolution/codec/no audio проверяются ffprobe; billing verified event replay и concurrent duplicate оставляют ровно один payment/grant, refund/cancel/expiry сохраняют инварианты.
- [ ] GREEN: ограниченные probe без реальных Twitch media/Telegram send/payment checkout; возвращать агрегаты и явные `synthetic=true`, без secret fields. Focused regression R5/preview проходят.
- [ ] Review: внешний FFmpeg optional только при явной команде, bounded process timeout/cleanup, точный 24s preview contract не редактируется; commit.

## Task 3: Локальные профили и интерпретация

**Files:** machine outputs в `docs/audits/2026-10-01-r9-local-20k.json`, `...-30k.json`, `...-40k.json`, первый audit draft `docs/audits/2026-10-01-r9-scale.md`.

- [ ] По одному процессу и новому output на 20k, 30k, 40k; не запускать параллельно. Записать p50/p95/p99 observation/queue/DB и wall/CPU/RSS/DB/WAL/free disk; явно отметить остановленные профили.
- [ ] Сравнить R3 shared-only baseline без утверждения acceleration/SLA; посчитать аналитический нижний предел Telegram 40ms starts и отдельно фактический fake throughput.
- [ ] Review аномалий: повторить только конкретно неустойчивый профиль при диагностированном шуме, не переписывать все цифры ради красивого результата.

## Task 4: Staging validation

**Files:** `docs/audits/2026-10-01-r9-scale.md`, `docs/STATUS.md`, `docs/DECISIONS.md`.

- [ ] Полный suite на финальном коде, code review и чистый commit; pinned target check, новый consistent staging online backup + внешний restore, migration drill на копии.
- [ ] Guarded staging deploy с terminal SUCCESS; подтвердить `getMe=TwitchSignalTestbot`, active DB integrity и production deployment identity без изменений.
- [ ] Последовательно выполнить 20k/30k/40k на staging в `/tmp`; после resource stop не форсировать больший профиль. Preview/billing probes на временных данных. Зафиксировать каждый JSON и после эксперимента active DB integrity/row counts.
- [ ] Закрыть audit с реальными цифрами, метриками/ограничениями, R9 decision об SQLite/очереди/preview и честным списком внешних непроверенных условий. Обновить STATUS/DECISIONS и commit docs.

## Самопроверка плана

Task 1 доставляет тестируемый путь масштаба без остальных probes; Task 2 независимо проверяет preview и billing. Полный suite выполняется один раз перед каждым необходимым staging deploy на точном commit; docs-only результаты не требуют повторного полного suite. При capacity stop R9 фиксирует предел, продолжает только независимые безопасные проверки и не создаёт ложную 40k acceptance.
