# R8 Growth / SEO / referrals: staging acceptance

Дата: 2026-10-01. Ветка `autonomous/twitchsignal-roadmap`, commit `6af8253`; pinned Railway staging service `45e46f2a-dba3-4b18-bc5f-b6fafa260055`, environment `7a873177-8ada-4b78-8732-a0bfdc1d519b`, test bot `TwitchSignalTestbot`. Production active deployment `2d440603-b74f-4c03-ba42-a5d53a491c02` остался прежним; `main` не push/merge.

## Gate и база

- `git diff --cached --check` прошёл без ошибок; пакет видео `6af8253` содержит MP4/постер/исходники, но не `node_modules`, DB или secrets. Локальный полный suite: 1120 passed, 2 skipped, 378 subtests за 399.97 s. Staging guard повторил 1120 passed, 2 skipped, 378 subtests за 382.24 s, проверил чистый commit и pinned target до/после тестов.
- До deployment online staging backup `/data/backups/2026-10-01-r8-pre-growth.db`: 667 648 B, SHA256 `d28849425404a439310303683449c7af81a707d7d992e3a7cebd7ccc6e45ccc8`. `verify_backup` восстановил отдельную DB, `integrity=ok`, 37 таблиц. Внешняя копия `%LOCALAPPDATA%\TwitchSignalBot\staging-backups\2026-10-01-r8-pre-growth.db` совпала по SHA и также восстановилась. Миграция на отдельной копии дала 39 таблиц, 10 versions, `r8_001_growth_attribution=true`, `integrity=ok`; активную DB не заменяли.
- Deployment `1f86e28e-4b3b-4e93-ba82-62fc9dd0bf9d` достиг `SUCCESS` и стал active. Внутри контейнера: `RAILWAY_ENVIRONMENT_NAME=staging`, `getMe=TwitchSignalTestbot`, активная DB `integrity=ok`, 39 таблиц, `r8_001_growth_attribution=true`, 0 growth touches до и после simulation.

## HTTP и браузер

- `/site`, `/site/for-viewers`, `/site/for-streamers`, `/site/help`: 4/4 HTTP 200, уникальные title/description/H1, точный staging canonical, HTTP+HTML `noindex`, ссылка только на `TwitchSignalTestbot?start=src_site`, без admin URL. `robots.txt` разрешает `/site`, чтобы crawler мог прочитать noindex. `brand-logo.png`, постер и оба MP4 совпали с локальными SHA256; постер/MP4 отвечают Range 206. Анонимный `/admin/api/snapshot` → 401.
- Chromium на staging 390 и 1440 CSS px: логотип 225 px загружен, обе роли показаны, horizontal overflow и console/page errors отсутствуют. На 390 px переход «Я зритель» открыл правильный маршрут, MP4 до нажатия play не был готов к воспроизведению; после play видео 1280×720 пошло и остановилось по pause. Скриншоты: `docs/design/r8-site-qa/staging-home-390.png`, `staging-home-1440.png`.
- В Telegram Bot API общие default/private/group commands не содержат `/admin`; owner private chat scope содержит `/admin`, общий private scope содержит `/invite`. Подписанный staging `initData` постороннего ID → 403, дальнейший API → 401; подписанный ID владельца `425785231` → 303 и snapshot 200 с growth aggregate. Это проверка серверной подписи, не реального входа владельца в клиенте Telegram.
- Изолированная `:memory:` DB внутри staging контейнера прошла site и referral first touch → первую private subscription → test Viewer Plus. По одному synthetic человеку в каждой корзине: touched=1, activated=1, ever_test_plus=1; self-referral, неизвестный код и replay отвергнуты, `integrity=ok`. Активная staging DB не получила synthetic growth rows.

## SEO и границы

- Датированный срез альтернатив и 28 поисковых гипотез/7 задач: `2026-10-01-r8-competitors.md`, `2026-10-01-r8-search-intents.md`. [SEO checklist](2026-10-01-r8-seo-launch-checklist.md) содержит будущие условия индексации, измерения и недельный шаблон без automation. [Copy inventory](2026-10-01-r8-copy-review.md) охватывает human-facing roadmap-тексты; [локальный видео-audit](2026-10-01-r8-video-local.md) фиксирует два 8-секундных рендера.
- Владелец сообщил, что BotFather `/setdomain` выполнен и Login popup открылся. Фактический вход аккаунтом владельца, реальный Telegram Mini App/streamer E2E, органические показы/клики, полевые Core Web Vitals и рынок/домен/имя не подтверждены. Аварийный ключ admin остаётся скрытым fallback до полного owner E2E.
- Публичная индексация, sitemap submission, domain purchase, реальные платежи/provider, платный render cloud и production deploy не выполнялись. Условия будущего публичного использования Remotion и ограничения Twitch trademark требуют отдельного решения до релиза.
