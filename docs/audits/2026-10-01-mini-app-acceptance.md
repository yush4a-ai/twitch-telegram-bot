# Mini App + Plus: инженерная приёмка staging

Дата: 2026-10-01. Ветка `autonomous/twitchsignal-roadmap`. Кодовый snapshot: `273db134d5aca71b588bbfda3f8bf47a33264574`, tree `39149895dbffa9f7a4a280eb31d37f2a07071b38`. Статус: локальные сценарии и pinned staging проверены; вход и доставка в настоящем Telegram-клиенте ожидают отдельной проверки с разрешённым аккаунтом и получателем.

## Что доступно

- Для `@TwitchSignalTestbot` read-only `getChatMenuButton` вернул `MenuButtonWebApp «Приложение»` с URL `https://worker-staging-2f74.up.railway.app/app`. Два режима имеют по три вкладки; данные и права серверные, общие с командами бота. Прямой URL без Telegram `initData` показывает только оболочку; API отклоняет неподписанный запрос. Фактическое открытие кнопки в Telegram-клиенте не проверено.
- Free сохраняет фото, уведомления, подключение собственного сообщества, краткие и HTML-отчёты с экспортом и предел 50 активных отслеживаний. Viewer Plus даёт 200, фильтры, нужную категорию и смену категории, напоминания 15/30 минут, папки, историю исходов и выбор пяти video-preview стримеров. Пять учитывают offline; шестой выбор требует атомарной замены. После истечения или отзыва права выбор сохраняется, эффективное видео выключается, существующая animation возвращается к фото.
- Streamer Plus отдельно управляет оформлением, кнопками, живым preview и измеряемыми подтверждёнными публикациями в разрешённых сообществах. Тестовое семидневное ознакомление доступно только allowlist на staging по явному действию, без денег и автосписания. Реальные покупки, цены, реклама и платная инфраструктура не включались.
- Видео локально кодируется как H.264 без звука, Telegram Animation до 24 с. Один эфир разделяет capture/render, повторяет file_id внутри одного бота; одновременно допускаются не более двух capture. При перегрузке остаётся фото с честным статусом. Это ограничение не является обещанием серверной пропускной способности.

## Локальные проверки

| Проверка | Факт |
| --- | --- |
| Полный suite кодового snapshot | Два независимых прогона: `1290 passed, 2 skipped, 419 subtests passed`; второй выполнен deploy guard непосредственно перед upload. Первый безуспешный вызов guard тоже прошёл suite, но остановился до upload на `RuntimeError` при post-test фазе; staging остался старым. Повтор на том же commit завершился успешно. |
| Legacy совместимость | `tests/test_viewer_video_delivery.py`, `tests/test_mini_app_legacy_compat.py`: старые private/group callbacks не выдают video, effective право требует соответствующий Plus и серверный Twitch ID, шестой выбор не проходит, raid в личке соблюдает quiet-hours, startup выбирает кнопку приложения только для pinned staging/testbot. |
| Возврат фото и восстановление | `tests/test_viewer_video_delivery.py`, `tests/test_notification_live_update.py`, `tests/test_stream_thumbnail.py`, `tests/test_viewer_preview_slots.py`: revoke/expiry/отключение и downgrade переводят animation→photo без удаления сообщения; повторный grant восстанавливает сохранённый выбор. Неизвестный Telegram-исход остаётся pending. |
| Браузер | Chromium на новой временной SQLite и синтетическом Telegram SDK: 360/390/768/1440 px, светлая/тёмная темы, BackButton, Free/Plus, подключение сообщества, trial, фильтр, ограниченное видео, 4→5→6 и замена. Дополнительно два окна одного ID: stale write отклонён, пятый слот сохранён, замена и reload PASS. Всего 32 реальных PNG в `docs/design/mini-app-qa/t12-*`. |
| Медийная нагрузка | `docs/audits/2026-10-01-mini-app-media-load.md`: 1×1000 остановлен после 463/1000 fake edit за 25 с; 100×10 и 1000×5 удержали максимум 2 capture/encode, 98/4998 потоков отложены. Внешних отправок 0. Это synthetic admission, не Telegram SLA. |
| Обзор | Один scoped reviewer за раз. Последний read-only обзор `273db13` не выявил блокеров по auth, media, billing, миграциям и frontend-секретам. Web design и stop-slop проверены на изменённых поверхностях; контрольные снимки осмотрены вручную. |

## Backup, migration-copy и staging

До upload создан online backup staging SQLite `/data/backups/2026-10-01-t12-predeploy-20261001-173316.db` на отдельном staging Volume `3ead0ce7-ed8c-482d-946c-ee768bf909ff`. Remote и внешняя копия `%LOCALAPPDATA%\TwitchSignalBot\staging-backups\2026-10-01-t12-predeploy-20261001-173316.db` имеют один SHA-256 `0750651a7650c5b7a052d5ae68effc38a12a13fa883f917a79140f7828e12895`, 1 249 280 байт. Обе прошли `integrity=ok` и restore drill на 39 таблиц. Активная DB не заменялась.

На отдельной копии этого backup текущий код добавил схему 39→54 таблицы и версии 10→20 при `integrity=ok`. Количества существующих строк сохранились: 54 tracked channels, 9 stream history, 25 notification jobs, 1 Twitch token. Исходный backup остался с тем же SHA. Для копии использован одноразовый ключ: уже зашифрованные значения миграция не меняет. Запрос на локальный запуск с переменными Railway автоматическая политика отклонила (`blocked by policy`), поэтому эта копия не доказывает расшифровку настоящим staging-ключом. Новый staging затем запустился и показал `integrity=ok`, 20 миграций. Попытка удаления одной дополнительной мигрированной копии вне рабочего дерева тоже отклонена политикой; сохранён файл `%LOCALAPPDATA%\TwitchSignalBot\staging-backups\twitchsignal-t12-migration-wx2vrb07.db`, в Git он не попал.

Guard загрузил только зафиксированный Git snapshot. Новый deployment `9039cde2-08a9-4c30-b36e-232ad70e7410` имеет `SUCCESS`, `cliMessage=staging 273db134d5aca71b588bbfda3f8bf47a33264574`, image digest `sha256:67368e0cad3f1fa2c9b271ea9dd07b6660a00a927f982439e0735dfed531b43e`. Независимая проверка: `/healthz` 200 с `status=ok`, `/app` и `app.js` 200, неподписанные bootstrap/viewer state/streamer profile — 401, `/admin/api/snapshot` без cookie — 401, `getMe=TwitchSignalTestbot`, `getChatMenuButton=MenuButtonWebApp «Приложение»` с точным staging URL, staging SQLite `integrity=ok` и 20 миграций. Отдельные HTTP/TLS и Railway API запросы один раз истекали по времени; последовательные повторы прошли. Health 200 отдельно не считался доказательством бота или прав.

Production остался deployment `2d440603-b74f-4c03-ba42-a5d53a491c02`, `SUCCESS`, GitHub `main` на `6074744aefe2ee6a314760d86f95684733f8c05f`. Production DB, конфигурация и секреты не менялись; push/merge не выполнялись.

## Границы приёмки

Положительный signed Free/Plus API на активной staging DB **NOT TESTED**; положительные права проверены локальными API-тестами с временной БД. Реальные Telegram send/animation→photo, нативный BackButton/safe areas на iOS/Android, OAuth владельца, Twitch live capture и сетевой 429 также **NOT TESTED**. Для них нет разрешённого получателя/аккаунта или контролируемого эфира. Браузерный SDK и fake sender этого не заменяют. Визуальное принятие владельцем ожидается. Новый минимальный первый вход и перенос/платность расширенных HTML-отчётов из `mini-app/2026-10-01-production-journey-audit.md` остаются предложениями; текущие Free-права и HTML-резерв сохранены.
