# Статус TwitchSignalBot

Обновлено: 2026-09-30. Рабочая ветка: `autonomous/twitchsignal-roadmap`; baseline HEAD до R0-документов `b4898a9`, production-код и активный production deployment `6074744`.

| Этап | Состояние | Evidence |
| --- | --- | --- |
| R0 Audit & Baseline | завершён локально; production и staging deployment в R0 не выполнялись | `docs/audits/2026-09-30-baseline.md`, risk register; локально 886 passed, 2 skipped; `git diff --cached --check` без ошибок |
| R1 Safe Development/Staging Workflow | следующий этап | нужен staging-only deploy runbook, healthcheck, backup/restore и проверка bot identity |
| R2–R9 | не начаты | scope в `docs/ROADMAP.md`; production и реальные деньги запрещены |

## Проверенное

- Railway production активен из GitHub `main` на `6074744`; staging — отдельный environment и отдельный Volume, текущий deploy через CLI.
- Оба `/healthz` ответили HTTP 200; staging Railway healthcheck setting в active deployment отсутствует.
- Код использует одну SQLite connection, последовательный poll cycle, preview artifact semaphore по умолчанию 1 и capture limit 2.
- README/`.env.example` приведены к реальному preview pipeline; это документальное изменение.

## Staging

R0 deployment и Telegram E2E не выполнялись. Последний видимый staging deployment создан 2026-09-30 10:33 UTC; Git SHA в его metadata отсутствует. Проверка target identity `@TwitchSignalTestbot` и безопасный deploy gate входят в R1.

## Открытые ограничения

- Нельзя push/merge `main`: он является источником production deployment.
- Staging healthcheck/draining и backup/restore runbook пока не закреплены.
- Secret values и production DB не копировались; различие credentials между окружениями ещё не доказано.
- Реальные платежи/provider, production rollout и юридические решения отложены до отдельного решения пользователя.

## Следующий шаг

Написать спецификацию и implementation plan R1, затем настроить и проверить только staging. Начать с branch/deploy guard и изоляции данных, затем backup/rollback и smoke через тестовый бот.
