# Статус TwitchSignalBot

Обновлено: 2026-09-30. Рабочая ветка: `autonomous/twitchsignal-roadmap`. Production-код и активный production deployment: `6074744`; push/merge `main` не выполнялись.

| Этап | Состояние | Evidence |
| --- | --- | --- |
| R0 Audit & Baseline | завершён | `docs/audits/2026-09-30-baseline.md`, risk register; исходный suite 886 passed, 2 skipped |
| R1 Safe Development/Staging Workflow | staging deploy и smoke выполнены; локальный guard приведён к реальной metadata | staging deployment `e6cb7283-087d-4e76-8fb7-002c059c11d9` из `7b5862d`, terminal `SUCCESS`; suite перед upload 904 passed, 2 skipped, 259 subtests; финальный локальный suite 909 passed, 2 skipped, 259 subtests; `/healthz` 200, `getMe=TwitchSignalTestbot`, backup/restore `integrity=ok` |
| R2 Owner Admin Panel v1 | следующий этап | по утверждённому workflow нужна адаптивная браузерная owner-панель; отдельная spec/plan, безопасный вход и staging проверка ещё нужны |
| R3–R9 | не начаты | scope в `docs/ROADMAP.md`; production и реальные деньги запрещены |

## Проверенное в R1

- Production привязан к GitHub `main` и после staging работ остался на `6074744` (`SUCCESS`, deployment `2d440603-b74f-4c03-ba42-a5d53a491c02`); staging остаётся CLI source с отдельным Volume и одной replica. Deployment выполнялся с явными project, environment и service ID из `scripts/staging_target.json`.
- Railway CLI завершился до terminal status нового deployment. При переключении Volume `/healthz` кратковременно отвечал 502, после `SUCCESS` ответил HTTP 200 `{"status":"ok"}`.
- `railway.json` содержит только staging override: healthcheck `/healthz`, timeout 300 с, draining 30 с, overlap 0. Railway записал четыре точных пути в `propertyFileMapping`, оставив базовые service-поля `null`; локальный guard проверяет этот формат. Фактический runtime healthcheck не является постоянным мониторингом.
- Staging bot identity подтверждена Telegram `getMe` из staging контейнера без вывода token: `TwitchSignalTestbot`.
- Онлайн backup `/data/backups/2026-09-30-r1-7b5862d.db` и неразрушающий restore drill: `integrity=ok`, 21 таблица, 360 448 байт. Активная DB не заменялась; production DB не читалась и не копировалась.
- Принят workflow addendum `docs/workflows/2026-09-30-autonomy-design-testing.md` и краткие project rules `AGENTS.md`: R2 является браузерной панелью, QA включает desktop/mobile и безопасный owner access; завершённые R0/R1 не перезапускаются.

## Открытые ограничения

- Нельзя push/merge `main`: он является источником production deployment.
- `OWNER_CHAT_ID` отсутствует в staging, поэтому Telegram `/health` с отдельным preview snapshot и исходящее E2E-сообщение тестовому chat ID не проверены. Preview состояние в живом staging — `unknown`; HTTP `/healthz` его не подтверждает.
- Backup находится на том же staging Volume. Перед существенной migration требуется внешний staging snapshot/export и проверенный offline maintenance path для активного DB rollback.
- Railway Config as Code (`railway.json`) устаревает 2026-12-01; нужен переход на Infrastructure as Code после отдельной проверки staging/production границы.
- Реальные платежи/provider, production rollout и юридические решения отложены до отдельного решения пользователя.

## Следующий шаг

Завершить проверку локального R1 guard и документации, затем перейти к R2: spec и plan для адаптивной браузерной owner-панели с отдельным Telegram/Twitch/preview health, аудиторией, очередью и ресурсами. Все новые функции проверять только в staging.
