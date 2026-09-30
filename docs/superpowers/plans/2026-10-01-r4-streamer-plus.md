# R4 Streamer Plus implementation plan

**Цель:** тестовая выдача Plus, подтверждённая связка Telegram/Twitch, отдельный кабинет и безопасные настройки постов для подтверждённых сообществ на staging.

**Spec:** `docs/superpowers/specs/2026-10-01-r4-streamer-plus-design.md`. Все пакеты проходят TDD, review, полный gate и staging acceptance; production и реальные деньги не меняются.

## Пакет 1 — модель identity и entitlement

- [x] RED: свежая/старое DB migration, one-to-one связка, чужой ID, grant/revoke/expiry, повтор idempotency key с тем же/другим payload и rollback при ошибке.
- [x] GREEN: additive tables/indexes и DB APIs; старый `/auth_twitch` не создаёт связь. Web session `StreamerAccess` добавлен в пакете 3.
- [x] Проверить focused suite и миграцию на копии staging snapshot; review SQL и audit без секретов; commit `765bab4`. Деплой `3fd52fb1-ec2b-4276-a2ae-c136df7d2aed` подтвердил миграцию.

## Пакет 2 — подтверждённый Twitch connect

- [x] RED: `/streamer_connect` только в private, OAuth result связывается с Telegram ID, захваченным до state; конфликт отвергается, временная ошибка сохраняет прежнюю связь.
- [x] GREEN: выделить проверяемый connect flow и бот-команду, повторно использовать OAuth exchange. Токены сохранять через существующий encrypted path.
- [x] Focused + regression suite, review командного scope; commit `478257f`. Реальный пользовательский OAuth ещё не пройден.

## Пакет 3 — отдельный кабинет

- [x] RED: подписанный Telegram WebApp/Login, direct URL denial, чужие данные, отсутствие owner/admin navigation/session crossover, logout/expiry.
- [x] GREEN: отдельные server-side streamer sessions, read-only `/streamer` и API своего профиля; responsive UI и пустые/ошибочные состояния. Commit `181c928`.
- [ ] Browser 390/1440, keyboard/security review; commit.

## Пакет 4 — сообщества и конструктор

- [ ] RED: Telegram user/bot member checks, stale permission denial, два сообщества, чужой chat ID, опасный URL, HTML/UTF-16 limit, optimistic version conflict, expiry fallback.
- [ ] GREEN: verified community binding, versioned template, controlled buttons; integrate current template into R3 go-live/live-update worker with Free fallback.
- [ ] Regression on text/photo/animation/retry/stale jobs, full suite, review; commit.

## Пакет 5 — статистика и staging acceptance

- [ ] RED/GREEN: свои агрегаты по queue/history, период и пустые данные; не называть просмотры поста доставкой.
- [ ] Внешний staging snapshot + restore, reviewed diff, полный gate, pinned staging deploy, health/auth/testbot E2E и cleanup.
- [ ] Обновить `docs/STATUS.md`, `docs/DECISIONS.md`, staging audit и явно отметить не прошедшие реальные UI/OAuth пути.

## Самопроверка плана

Порядок даёт server-side access раньше UI и template. Каждый пакет можно проверить отдельно; staging deploy следует только за полным gate и backup. R5 использует entitlement, но не требуется для тестовой R4 выдачи.
