# Статус TwitchSignalBot

Обновлено: 2026-09-30. Рабочая ветка: `autonomous/twitchsignal-roadmap`. Production-код и активный production deployment: `6074744`; push/merge `main` не выполнялись.

| Этап | Состояние | Evidence |
| --- | --- | --- |
| R0 Audit & Baseline | завершён | `docs/audits/2026-09-30-baseline.md`, risk register; исходный suite 886 passed, 2 skipped |
| R1 Safe Development/Staging Workflow | завершён с отмеченными ограничениями preview E2E и внешнего backup | staging deployment `e6cb7283-087d-4e76-8fb7-002c059c11d9` из `7b5862d`, terminal `SUCCESS`; suite перед upload 904 passed, 2 skipped, 259 subtests; финальный локальный suite 909 passed, 2 skipped, 259 subtests; guard `--check` прошёл из `5672d0a`; `/healthz` 200, `getMe=TwitchSignalTestbot`, backup/restore `integrity=ok` |
| R2 Owner Admin Panel v1 | реализация и staging smoke выполнены; реальный Telegram Login E2E ожидает BotFather domain | deployment `26ab6e4a-6548-4b29-9f07-60f6e75f0815` из `2faa74f` terminal `SUCCESS`; gate 942 passed, 2 skipped, 259 subtests; signed owner/non-owner staging проверки и браузерный 390/1440 smoke |
| R3 Growth Foundation | в работе: shared samples и go-live queue cutover на staging; локальные 20k/30k/40k, preview и queue профили записаны, точечный fan-out lookup ждёт staging deploy | deployment `edefef88-980f-47cc-a544-3dc80cfdc141` из `dda30cb` `SUCCESS`; gate 994 passed, 2 skipped, 261 subtests; `/healthz` 200, DB integrity, owner API/HTML содержит метрики queue, неавторизованный panel JS 401; предыдущий deployment `db6f6316-c3c3-4871-8e0a-112f7858ce65` подтвердил `getMe=TwitchSignalTestbot` и синтетический worker job; 42 профильных теста после нового локального пакета; `docs/audits/2026-09-30-r3-results.md` |
| R4–R9 | не начаты | scope в `docs/ROADMAP.md`; production и реальные деньги запрещены |

## Проверенное в R1

- Production привязан к GitHub `main` и после staging работ остался на `6074744` (`SUCCESS`, deployment `2d440603-b74f-4c03-ba42-a5d53a491c02`); staging остаётся CLI source с отдельным Volume и одной replica. Deployment выполнялся с явными project, environment и service ID из `scripts/staging_target.json`.
- Railway CLI завершился до terminal status нового deployment. При переключении Volume `/healthz` кратковременно отвечал 502, после `SUCCESS` ответил HTTP 200 `{"status":"ok"}`.
- `railway.json` содержит только staging override: healthcheck `/healthz`, timeout 300 с, draining 30 с, overlap 0. Railway записал четыре точных пути в `propertyFileMapping`, оставив базовые service-поля `null`; локальный guard проверяет этот формат. Фактический runtime healthcheck не является постоянным мониторингом.
- Staging bot identity подтверждена Telegram `getMe` из staging контейнера без вывода token: `TwitchSignalTestbot`.
- Онлайн backup `/data/backups/2026-09-30-r1-7b5862d.db` и неразрушающий restore drill: `integrity=ok`, 21 таблица, 360 448 байт. Активная DB не заменялась; production DB не читалась и не копировалась.
- Принят workflow addendum `docs/workflows/2026-09-30-autonomy-design-testing.md` и краткие project rules `AGENTS.md`: R2 является браузерной панелью, QA включает desktop/mobile и безопасный owner access; завершённые R0/R1 не перезапускаются.

## Открытые ограничения

- Нельзя push/merge `main`: он является источником production deployment.
- В staging теперь задан подтверждённый владельцем `OWNER_CHAT_ID=425785231`; `getMe` вернул `TwitchSignalTestbot`. `getMyCommands` подтвердил: общие private и group команды без `/admin`, в scope личного чата владельца команда есть. Фактический Telegram-клиент владельца и исходящее E2E-сообщение пока не проверены. Preview в панели сейчас `unknown` без зафиксированного успешного capture; `/healthz` его не подтверждает.
- R2 signed auth на staging проверен с синтетическими Telegram-подписями, вычисленными внутри staging контейнера: non-owner WebApp/Login 403, owner 303 и API 200. Это проверка серверной валидации, не прохождение реального Telegram UI. Browser widget показывает `Bot domain invalid`: домен `https://worker-staging-2f74.up.railway.app` ещё не разрешён для `@TwitchSignalTestbot` в BotFather. Аварийный `ADMIN_PANEL_ACCESS_KEY` скрыт от обычного UI и сохранён до реального E2E.
- Backup находится на том же staging Volume. Перед существенной migration требуется внешний staging snapshot/export и проверенный offline maintenance path для активного DB rollback.
- Railway Config as Code (`railway.json`) устаревает 2026-12-01; нужен переход на Infrastructure as Code после отдельной проверки staging/production границы.
- Реальные платежи/provider, production rollout и юридические решения отложены до отдельного решения пользователя.

## Следующий шаг

Продолжать R3: развернуть точечный fan-out lookup и локальный load harness, затем staged update/cleanup и проверить откат shared данных. Контролируемый реальный go-live через testbot ещё не подтверждён; локальные профили не заменяют эту проверку. После привязки testbot domain в BotFather завершить реальный Telegram Login/Mini App owner E2E и закрыть R2 acceptance; production не менять.

## Проверенное в R2

- Read-only dashboard показывает Telegram, Twitch и preview отдельно; аудиторию, live, очереди, ошибки и ресурсы процесса. На staging browser smoke в ширинах 390/1440 px без переполнения; старая ошибка clock domain preview исправлена и проверена локальным тестом, staging preview остаётся без успешного capture.
- Нормальная страница входа содержит только Telegram Login и попытку `initData` из Mini App. Прямой API без cookie — 401, неподписанный Mini App — 403, прежний `/admin/login` — 404. Отдельный скрытый emergency route прошёл вход/API/logout без вывода ключа.
- Production deployment, production variables/DB и `main` не менялись. Последний R2 deploy выполнен из commit snapshot через guard с точными staging project/environment/service ID.
