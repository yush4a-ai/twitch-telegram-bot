# R7 — Viewer Mini App staging acceptance

Дата: 2026-10-01. Ветка `autonomous/twitchsignal-roadmap`; закреплённый Railway staging и `@TwitchSignalTestbot`. Production deploy/DB/variables, `main` и реальные платежи не менялись.

## Подготовка и gate

- Spec и plan: `docs/superpowers/specs/2026-10-01-r7-viewer-mini-app-design.md`, `docs/superpowers/plans/2026-10-01-r7-viewer-mini-app.md`. Реализация в пакетах `4e02b61`, `b40032b`, `eb32265`, `1c78801` с focused TDD для DB/фильтра, подписанного API/меню, доставки и закрытого test grant. `git diff --check` без ошибок, дерево чистое до deploy.
- Полный локальный suite: 1099 passed, 2 skipped, 347 subtests за 402,25 с. Staging guard повторил: 1099 passed, 2 skipped, 347 subtests за 405,64 с. Pinned target check прошёл до и после deployment.
- До миграции сделан online staging backup `/data/backups/2026-10-01-r7-pre-viewer.db` и внешний экземпляр `%LOCALAPPDATA%\TwitchSignalBot\staging-backups\2026-10-01-r7-pre-viewer.db`: 659456 байт, SHA256 `5d1338bbf18a159ff6fef8b48cb0b7a3b233d837b533e599c01092463f3e740e`. Restore drill дал `integrity=ok`, 36 таблиц. Миграция на отдельной копии дала 37 таблиц, 9 schema versions, `r7_001_viewer_filters=true`, `integrity=ok`; исходный backup не менялся.

## Deployment и проверки

- Deployment `8cf965e2-abea-4536-b2b1-e2f06189a501` из `1c7880111020` достиг `SUCCESS` и стал active. `/healthz`, `/viewer`, `/viewer/app.js` → 200, неподписанный `/viewer/api/state` → 401. В контейнере подтверждены `RAILWAY_ENVIRONMENT_NAME=staging`, `r7_001_viewer_filters=true`, `PRAGMA integrity_check=ok`, `getMe=TwitchSignalTestbot`; на момент smoke новых filter rows 0.
- Синтетический `initData` подписан staging bot token внутри контейнера без вывода токена. Владелец `425785231` получил 200 и только одну собственную подписку; посторонний тестовый ID — 200 и пустой список; дублирование/подмена `user` — 403. В обоих ответах нет admin entry. Закрытый staging CLI выдал владельцу краткий тестовый Viewer Plus, подписанный API показал `plus_active=true`; grant сразу отозван, повторный подписанный API показал `plus_active=false`. Осталась только audit-запись отозванного тестового grant, пользовательские фильтры не менялись.
- R2 regression на том же deployment: прямой `/admin/api/snapshot` → 401, подписанный посторонний ID на `/admin/telegram-webapp` → 403, подписанный owner ID → 303. `getMyCommands` для default, all private и all group scopes не содержит `/admin`; owner private chat scope содержит. Аварийный ключ остаётся скрытым fallback.
- Browser shell без `initData` показал только приглашение открыть экран из личного Telegram-чата, без пользовательских данных и админ-метки. Измерения DOM на ширинах 390 и 1440 px: `scrollWidth=clientWidth`; при 1440 основной контейнер 680 px. Это проверка публичной оболочки, а не авторизованного экрана Telegram.

## Граница приёмки

Тест не подтверждает открытие Mini App в настоящем Telegram-клиенте. Ранее Login Widget показывал `Bot domain invalid`; после R7 smoke владелец сообщил, что выполнил `/setdomain` для testbot и затем Login popup открылось. Это owner-reported изменение состояния, но полный вход его аккаунтом и Mini App E2E не зафиксированы. Успешный synthetic HMAC и browser shell их не заменяют. Реальный эфир, меняющийся фильтр в очереди и Telegram fan-out покрыты кодовыми тестами, но не фактическим live smoke на testbot. Production не затрагивался; R7 принят как инженерный staging checkpoint, далее R8.
