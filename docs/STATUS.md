# Статус TwitchSignalBot

Обновлено: 2026-10-01. Рабочая ветка: `autonomous/twitchsignal-roadmap`. Production-код и активный production deployment: `6074744`; push/merge `main` не выполнялись.

| Этап | Состояние | Evidence |
| --- | --- | --- |
| R0 Audit & Baseline | завершён | `docs/audits/2026-09-30-baseline.md`, risk register; исходный suite 886 passed, 2 skipped |
| R1 Safe Development/Staging Workflow | завершён с отмеченными ограничениями preview E2E и внешнего backup | staging deployment `e6cb7283-087d-4e76-8fb7-002c059c11d9` из `7b5862d`, terminal `SUCCESS`; suite перед upload 904 passed, 2 skipped, 259 subtests; финальный локальный suite 909 passed, 2 skipped, 259 subtests; guard `--check` прошёл из `5672d0a`; `/healthz` 200, `getMe=TwitchSignalTestbot`, backup/restore `integrity=ok` |
| R2 Owner Admin Panel v1 | реализация и staging smoke выполнены; реальный Telegram Login E2E ожидает BotFather domain | deployment `26ab6e4a-6548-4b29-9f07-60f6e75f0815` из `2faa74f` terminal `SUCCESS`; gate 942 passed, 2 skipped, 259 subtests; signed owner/non-owner staging проверки и браузерный 390/1440 smoke |
| R3 Growth Foundation | staging engineering acceptance с ограничениями реальной нагрузки | deployment `822ef65f-2fa6-437e-90cb-6357c0e5b405` из `e36b6d4` `SUCCESS`; gate 1026 passed, 2 skipped, 268 subtests; поздний локальный gate 1030 passed, 2 skipped, 268 subtests; lease recovery и 1k/5k mixed-load на временной staging DB; offline rollback copy проверена; `docs/audits/2026-10-01-r3-recovery-rollback.md` |
| R4 Streamer Plus | engineering acceptance на staging; реальный Telegram/Twitch UI E2E ожидает внешние условия | deployment `a1577af8-919e-42b5-b5e0-d08a17a9b2b2` из `5b9f0b4` `SUCCESS`; gate 1064 passed, 2 skipped, 299 subtests; `r4_001–r4_004`, `integrity=ok`; `docs/audits/2026-10-01-r4-templates-staging.md` |
| R5 Payments foundation | engineering acceptance на staging; mock/test без денег | deployment `fa758200-bb09-47d0-9cca-fdeab33b204f` из `fd17d34` `SUCCESS`; gate 1082 passed, 2 skipped, 317 subtests; `r5_001`, `integrity=ok`, временный checkout/capture/refund/cancel/expiry и rollback; `docs/audits/2026-10-01-r5-mock-billing-staging.md` |
| R6 Pilot simulation | engineering acceptance на staging, без реальных участников | deployment `31086fa4-21ca-4190-bedb-6d79d95a267e` из `ff04f37` `SUCCESS`; gate 1084 passed, 2 skipped, 334 subtests; 8 synthetic journeys, 16 ожидаемых отказов, 0 unexpected errors, активная DB 0 pilot rows; `docs/audits/2026-10-01-r6-pilot-simulation-staging.md` |
| R7 Telegram Mini App + Viewer Plus | следующий этап | production и реальные деньги запрещены |
| R8–R9 | не начаты | staging-only |

## Проверенное в R1

- Production привязан к GitHub `main` и после staging работ остался на `6074744` (`SUCCESS`, deployment `2d440603-b74f-4c03-ba42-a5d53a491c02`); staging остаётся CLI source с отдельным Volume и одной replica. Deployment выполнялся с явными project, environment и service ID из `scripts/staging_target.json`.
- Railway CLI завершился до terminal status нового deployment. При переключении Volume `/healthz` кратковременно отвечал 502, после `SUCCESS` ответил HTTP 200 `{"status":"ok"}`.
- `railway.json` содержит только staging override: healthcheck `/healthz`, timeout 300 с, draining 30 с, overlap 0. Railway записал четыре точных пути в `propertyFileMapping`, оставив базовые service-поля `null`; локальный guard проверяет этот формат. Фактический runtime healthcheck не является постоянным мониторингом.
- Staging bot identity подтверждена Telegram `getMe` из staging контейнера без вывода token: `TwitchSignalTestbot`.
- Онлайн backup `/data/backups/2026-09-30-r1-7b5862d.db` и неразрушающий restore drill: `integrity=ok`, 21 таблица, 360 448 байт. Активная DB не заменялась; production DB не читалась и не копировалась.
- Принят workflow addendum `docs/workflows/2026-09-30-autonomy-design-testing.md` и краткие project rules `AGENTS.md`: R2 является браузерной панелью, QA включает desktop/mobile и безопасный owner access; завершённые R0/R1 не перезапускаются.

## Открытые ограничения

- Нельзя push/merge `main`: он является источником production deployment.
- В staging задан подтверждённый владельцем `OWNER_CHAT_ID=425785231`; `getMe` вернул `TwitchSignalTestbot`. `getMyCommands` ранее подтвердил: общие private и group команды без `/admin`, в scope личного чата владельца команда есть. Фактический Telegram-клиент владельца и исходящее E2E-сообщение пока не проверены. Preview в панели сейчас `unknown` без зафиксированного успешного capture; `/healthz` его не подтверждает.
- R2 signed auth на staging проверен с синтетическими Telegram-подписями, вычисленными внутри staging контейнера: non-owner WebApp/Login 403, owner 303 и API 200. Это проверка серверной валидации, не прохождение реального Telegram UI. Browser widget показывает `Bot domain invalid`: домен `https://worker-staging-2f74.up.railway.app` ещё не разрешён для `@TwitchSignalTestbot` в BotFather. Аварийный `ADMIN_PANEL_ACCESS_KEY` скрыт от обычного UI и сохранён до реального E2E.
- R4 `/streamer_connect` с реальным Twitch OAuth и положительный вход в кабинет на staging не проверены: активная staging DB имеет 0 связанных identity, grants, templates и событий публикации. Серверные signed и entitlement positive/negative пути покрыты локальными тестами. Реальный Login Widget использует тот же неразрешённый BotFather staging domain.
- Перед существенной migration требуется внешний staging snapshot/export. Инструмент R3 создаёт отдельную проверенную копию с legacy samples; безопасная замена активной staging DB при остановленных writers ещё не автоматизирована.
- Railway Config as Code (`railway.json`) устаревает 2026-12-01; нужен переход на Infrastructure as Code после отдельной проверки staging/production границы.
- Реальные платежи/provider, production rollout и юридические решения отложены до отдельного решения пользователя.

## Следующий шаг

Начать R7: staging Telegram Mini App с серверной проверкой `initData`, Viewer Plus entitlement, smart alerts/filter/exclusions, digest и синхронными настройками бота. После привязки testbot domain в BotFather завершить реальный Telegram Login/Mini App owner и streamer E2E; production не менять. В R9 измерить полную смешанную нагрузку и реальную задержку Telegram; текущие synthetic прогоны не подтверждают 20–40k SLA.

## Проверенное в R2

- Read-only dashboard показывает Telegram, Twitch и preview отдельно; аудиторию, live, очереди, ошибки и ресурсы процесса. На staging browser smoke в ширинах 390/1440 px без переполнения; старая ошибка clock domain preview исправлена и проверена локальным тестом, staging preview остаётся без успешного capture.
- Нормальная страница входа содержит только Telegram Login и попытку `initData` из Mini App. Прямой API без cookie — 401, неподписанный Mini App — 403, прежний `/admin/login` — 404. Отдельный скрытый emergency route прошёл вход/API/logout без вывода ключа.
- Production deployment, production variables/DB и `main` не менялись. Последний R2 deploy выполнен из commit snapshot через guard с точными staging project/environment/service ID.
