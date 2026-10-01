# R4 Streamer Plus — шаблоны и статистика на staging

Дата: 2026-10-01. Ветка `autonomous/twitchsignal-roadmap`. Production `main`, deployment, variables и DB не менялись.

## Подготовка и миграция

- Target validation до действий: точные pinned staging project/environment/service IDs, один staging Volume `/data`, CLI source, production source `main`; `errors=[]`. Diff `--cached --check` без ошибок, дерево commit `5b9f0b4` чистое.
- Online backup `/data/backups/2026-10-01-r4-pre-templates.db`: `integrity=ok`, 581 632 байта; временный restore 28 таблиц. Внешняя копия `%LOCALAPPDATA%\TwitchSignalBot\staging-backups\2026-10-01-r4-pre-templates.db` вне Git тоже `integrity=ok`, restore 28 таблиц. SHA-256 обеих копий `fee0430f94d2ca68d3a3b323f0609dac21e851c043ad263e1fb424401278a6c3`.
- На отдельной копии внешнего backup `Database.connect()` добавил `r4_002_streamer_communities`, `r4_003_streamer_templates`, `r4_004_streamer_stats`; старые R3/R4 версии сохранились, `integrity_check=ok`, templates/events/communities = 0, hash исходного backup не изменился.
- Полный локальный suite до upload: 1063 passed, 2 skipped, 299 subtests. Защитный deploy повторил после финальных целевых тестов: 1064 passed, 2 skipped, 299 subtests за 382,11 с. `node --check` кабинета прошёл. Небольшое увеличение на один тест связано с отдельным сценарием двух сообществ.

## Deployment и smoke

- Commit `5b9f0b4fa9fd`, deployment `a1577af8-919e-42b5-b5e0-d08a17a9b2b2` terminal `SUCCESS`. Независимая проверка после deploy: `active_target_ok=true`, активен этот же deployment, ошибок нет.
- `/healthz` → 200; `/streamer` → 200 и без метки «Админ-панель»; `/streamer/api/profile`, `/streamer/api/stats`, `/streamer/api/templates/-1001` и `/admin/api/snapshot` без session → 401.
- Активная staging DB: `PRAGMA integrity_check=ok`, версии R3 `001/002/003` и R4 `001/002/003/004`; streamer identities, grants, templates, post events = 0. Staging `getMe` → `TwitchSignalTestbot`.
- `getMyCommands`: `/admin` отсутствует в default, all-private, all-group scopes и присутствует только в личном owner scope для `425785231`. Production меню не проверялось и не менялось.

## Граница приёмки

Положительный signed кабинет, Twitch OAuth, запись двух настоящих сообществ и реальная Telegram публикация с шаблоном проверены локальными изолированными HTTP/DB/poller тестами, но не реальным staging пользовательским сценарием. Активная staging DB остаётся без тестовых grant/identity. Telegram Login Widget ждёт настройки BotFather domain для testbot; не выдавать synthetic подпись за реальный вход. Приступать к R5 независимо, вернуться к реальному R2/R4 E2E после доступности Telegram UI и подтверждённого Twitch аккаунта.
