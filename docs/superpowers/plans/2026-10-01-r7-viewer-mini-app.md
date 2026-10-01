# R7 Viewer Mini App Implementation Plan

**Goal:** staging-only Mini App с подписанным Telegram ID, Viewer Plus test entitlement, общими настройками бота и фильтром новых личных live-уведомлений.

**Architecture:** `telegram_identity.verify_webapp_user` защищает каждый Viewer API вызов; DB хранит фильтр для личной подписки и test grant. Pure matcher используется poller и worker. UI показывает только пользовательские настройки. Сводка использует текущий quiet-hours механизм.

**Spec:** `docs/superpowers/specs/2026-10-01-r7-viewer-mini-app-design.md`.

## Task 1: Viewer entitlement и фильтр

**Files:** `bot/database.py`, new `bot/viewer_filter.py`, tests `tests/test_viewer_filter.py`, `tests/test_viewer_access.py`.

- [x] RED/GREEN: grant/revoke/expiry/idempotency и owner actor, Free не имеет Plus; additive `r7_001_viewer_filters` на fresh/old DB.
- [x] RED/GREEN: versioned rules только для личной подписки владельца, validation, чужой ID и удаление подписки, точная семантика игры/заголовка/исключений. Effectiveness проверяется одним SQL snapshot с grant.
- [x] Focused R4/R5/R7 suite 24 passed, 13 subtests; после проверки удаления всех подписок целевой suite 8 passed, 10 subtests; self-review и commit пакета.

## Task 2: Viewer API, Mini App и меню

**Files:** new `bot/viewer_web.py`, `bot/viewer_ui/*`; `bot/oauth.py`, `main.py`, `bot/config.py`, `bot/handlers/streams.py`; tests `tests/test_viewer_web.py`, existing R2 menu/auth tests.

- [x] RED/GREEN: без initData/подделка/устаревание/direct URL/чужие подписки отклоняются, owner и обычный подписанный пользователь видят только свои данные; Free меняет базовый notify, Plus — versioned filter.
- [x] RED/GREEN: UI shell/активы без admin entry; кнопка только в private pinned staging, не в группах/каналах и без `/admin` в общих командах. Мобильная/desktop визуальная проверка будет на staging.
- [x] Синхронное чтение/изменение `notify_enabled` и quiet-hours digest через существующую DB, без отдельной копии настроек; focused HTTP/menu/auth suite 17 passed, 5 subtests; commit пакета.

## Task 3: Доставка и сводка

**Files:** `bot/poller.py`, queue send path, tests `tests/test_viewer_delivery.py` и регрессии.

- [x] RED/GREEN: личный новый live post отфильтровывается до queue/direct send, state и stream sample сохраняются; group/channel и Free не меняются.
- [x] RED/GREEN: queued worker повторно проверяет правило после изменения и завершает исключённый job как stale; revoke возвращает Free поведение. Замена уже отправленного поста обходится без потери при позднем фильтре. Существующий quiet-hours digest читает флаг, который меняет Mini App.
- [x] Focused queue/R7/R2 suite 50 passed, 7 subtests; review и commit пакета.

## Task 4: Staging acceptance

- [ ] Полный suite, `git diff --check`, внешний online backup/restore, миграция на копии, чистый commit и pinned target check; guard staging deploy.
- [ ] Terminal SUCCESS, active target, `/healthz`, `TwitchSignalTestbot`, schema/integrity, negative/positive signed API и private menu scopes; описать реальный UI blocker BotFather domain.
- [ ] Обновить `docs/STATUS.md`, `docs/DECISIONS.md`, audit evidence и начать R8.

## Самопроверка плана

В каждом пакете сохраняются staging-only граница, проверка Telegram подписи и owner-only admin. Существующая R5 mock оплата не расширяется на Viewer Plus. Реальный UI E2E и production не утверждаются без внешних условий.
