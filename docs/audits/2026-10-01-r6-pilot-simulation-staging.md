# R6 — staging pilot simulation

Дата: 2026-10-01. Ветка `autonomous/twitchsignal-roadmap`. Только фиктивные участники во временной DB; production не менялся.

## Подготовка и gate

- Spec и plan: `docs/superpowers/specs/2026-10-01-r6-staging-pilot-simulation-design.md`, `docs/superpowers/plans/2026-10-01-r6-staging-pilot-simulation.md`; самопроверка без незаполненных решений. TDD: отсутствующий модуль дал RED, затем 2 tests, 17 subtests; соседний focused suite 22 passed, 39 subtests. `git diff --cached --check` без ошибок.
- Полный локальный suite: 1084 passed, 2 skipped, 334 subtests за 384,51 с. Deploy guard повторил suite: 1084 passed, 2 skipped, 334 subtests за 398,65 с. Снимок `ff04f3775911` из чистой отдельной ветки, точный pinned staging target проверен до и после тестов. Новых миграций активной DB в R6 нет; проверенный R5 staging backup сохранён.

## Deployment и сценарий

- Deployment `31086fa4-21ca-4190-bedb-6d79d95a267e` terminal `SUCCESS`; независимый Railway status: тот же active deployment, `active_target_ok=true`, `errors=[]`. Production active commit по-прежнему `6074744aefe2ee6a314760d86f95684733f8c05f`, `SUCCESS`.
- Staging `/healthz` и `/streamer` → 200; `getMe` → `TwitchSignalTestbot`. Активная staging DB до и после simulation: `integrity_check=ok`, сумма строк `streamer_identities`, `streamer_communities`, `streamer_post_templates`, `billing_orders` = 0.
- Закрытый `python -m scripts.staging_r6_pilot` внутри закреплённого staging runtime создал временную DB вне `/data`, выполнил 8 synthetic Telegram/Twitch identity links, 8 TEST captures/Plus grants, 8 communities, 8 templates, 8 recorded post events и owner analytics. 16 проверенных отказов — чужой Telegram владелец и неверный broadcaster ID для каждого. Затем 2 verified refunds, 6 истечений Plus и 1 pending cancel; активный template исчез после утраты Plus, историческая статистика осталась. `unexpected_errors=0`, `integrity=ok`.
- Отчёт временной DB: `elapsed_ms=66`, `db_bytes=401408`, Telegram/Twitch/payment network calls = 0, `external_cost_units=0`. Это время локальных операций внутри одного процесса без сети и не оценка latency/стоимости реального пилота.

## Граница приёмки

Здесь нет восьми реальных участников, фактического Twitch OAuth, прав Telegram сообществ, сообщений или настоящих платежей. Real owner/streamer Telegram Login Widget всё ещё зависит от разрешения staging domain в BotFather; положительный реальный UI E2E не был выдан за synthetic тест. R6 закрыт как инженерная staging simulation; продуктовый pilot с 5–10 стримерами и production rollout остаются отдельными действиями владельца.
