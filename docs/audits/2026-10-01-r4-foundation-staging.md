# R4 foundation staging checkpoint

Дата: 2026-10-01. Ветка `autonomous/twitchsignal-roadmap`; production `main`, deployment, variables и DB не менялись.

## Подготовка

- Перед R4 schema migration: backup staging Volume `/data/backups/2026-10-01-r4-pre-181c928.db`, `integrity=ok`, 548 864 байт, restore 25 таблиц. Внешняя копия вне Git: `%LOCALAPPDATA%\TwitchSignalBot\staging-backups\2026-10-01-r4-pre-181c928.db`; отдельный restore также `integrity=ok`, 25 таблиц. SHA-256 обеих копий `ef4e85bb1b24c68b0df369ed67f2bca61745703ff8de8e01667c4032c74ccc82`.
- На временной копии внешнего snapshot `Database.connect()` добавил `r4_001_streamer_access`, сохранил R3 versions и `integrity_check=ok`; `streamer_identities=0`, `entitlement_grants=0`, исходный backup не изменился.
- Diff `e36b6d4..86a2fe3` без whitespace errors, дерево чистое; target guard подтвердил точные staging project/environment/service IDs и production source `main`. Полный локальный suite 1045 passed, 2 skipped, 271 subtests; guarded pre-upload gate повторил тот же результат.

## Deployment и smoke

- Commit `86a2fe3105d4951ab1fb54f10a5b983d8b60d5af`, deployment `3fd52fb1-ec2b-4276-a2ae-c136df7d2aed` terminal `SUCCESS`, подтверждён активным staging target. One replica, staging Volume `/data`.
- `/healthz` → 200 `{"status":"ok"}`; `/admin/api/snapshot` без session → 401; `/streamer/api/profile` без session → 401; `/streamer` → 200 login page, без «Админ-панель».
- Staging DB `PRAGMA integrity_check=ok`; versions R3 `001/002/003` + `r4_001_streamer_access`; identities 0, grants 0. `getMe` из staging контейнера → `TwitchSignalTestbot`.

## Что пока не доказано

Настоящий владелец ещё не прошёл Telegram Login Widget через staging BotFather domain; signed Telegram WebApp/Login positive и negative проверены локально, а не реальным клиентом. `/streamer_connect` с реальным Twitch OAuth не вызывался. В активной staging DB нет тестовой связки, поэтому положительный кабинет там пока не проверен. Этот checkpoint не является приёмкой всего R4: сообщества, template, кнопки, статистика, operator grant CLI и конечный lifecycle впереди.
