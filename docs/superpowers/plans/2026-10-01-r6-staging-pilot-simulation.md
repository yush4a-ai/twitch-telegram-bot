# R6 Staging Pilot Simulation Plan

**Goal:** проверить восемь фиктивных Streamer Plus lifecycle на staging и записать границу инженерной приёмки без реальных участников/денег.

**Architecture:** закрытый CLI использует R5 pinned staging guard и отдельную временную DB, существующие R4 identity/community/template/stats и R5 mock billing. Ни Telegram, ни Twitch client не создаются.

**Spec:** `docs/superpowers/specs/2026-10-01-r6-staging-pilot-simulation-design.md`.

## Task 1: TDD закрытой simulation

**Files:** Create `scripts/staging_r6_pilot.py`, `tests/test_staging_r6_pilot.py`.

- [x] RED: отсутствующий модуль остановил сбор тестов; production/wrong target и временная директория внутри Volume отвергаются до DB open; активный DB_PATH не меняется.
- [x] RED/GREEN: 8 уникальных identity, signed mock capture/Plus, сообщество, template, live lookup/compose, post event и owner analytics; подмена broadcaster/чужой владелец отрицательны.
- [x] RED/GREEN: два verified refund, шесть expiry, один cancel pending; template недоступен после Plus, историческая статистика остаётся; метрики без ID/секретов. Focused 22 passed, 39 subtests.

## Task 2: Проверка и staging acceptance

- [x] Self-review spec/plan без незаполненных решений и противоречий, `git diff --check`, focused и полный suite 1084 passed, 2 skipped, 334 subtests; code review и commit snapshot.
- [x] Deployment только после target validation=staging и полного guard (1084 passed, 2 skipped, 334 subtests). `31086fa4-21ca-4190-bedb-6d79d95a267e` terminal SUCCESS, независимый active target, `TwitchSignalTestbot`/HTTP/DB smoke; staging simulation run и 0 pilot rows в активной DB после.
- [x] `docs/STATUS.md`, `docs/DECISIONS.md`, `docs/audits/2026-10-01-r6-pilot-simulation-staging.md` с фактическими числами и границей реального pilot; далее R7.

## Самопроверка плана

Каждый roadmap пункт R6 отражён: 8 субъектов, подключение в модели, customization, analytics, Plus lifecycle, измерение времени/DB и учёт нулевых сетевых расходов. Настоящие OAuth, BotFather domain и права сообществ остаются отдельным пользовательским E2E, без production действий.
