# Фаза B: админ-панель v2 (люди, активность, управление доступами) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Дать владельцу поиск и карточку человека с именами и активностью, чтение журнала прав с причиной и комментарием и управление доступами из веба — выдача, продление и отзыв ручного права.

**Architecture:** Схема расширяется идемпотентными миграциями в `bot/database.py` (профиль, активность, причина/комментарий, `extend`, `previous_grant_id`, `source='manual'`), запись профиля и активности делает middleware на горячем пути с троттлингом. Чтение людей и журнала переезжает из `bot/admin_directory.py` в методы `Database`, а `AdminDirectory` остаётся тонким адаптером. Веб-часть добавляет read- и write-маршруты `/admin/api/*` с проверкой `Origin`, одноразовой CSRF-меткой и идемпотентностью по `request_key`; UI получает экран «Пользователи», карточку человека и диалоги операций.

**Tech Stack:** Python 3.12, aiogram 3, aiohttp, aiosqlite, unittest/pytest, статический HTML/CSS/JS.

**Spec:** `docs/superpowers/specs/2026-10-04-admin-panel-v2-design.md` (разделы 3, 5.2–5.5, 6–9)

**Предусловие:** общие файлы `bot/database.py`, `bot/middlewares.py`, `bot/handlers/*`, `main.py`, `bot/oauth.py` должны быть свободны от параллельной работы над Mini App и release-инфраструктурой. Перед Task 1 проверить `git status` и последние коммиты по этим файлам; если они заняты — не начинать.

## Global Constraints

- Строгая авторизация владельца не меняется: подпись Telegram, owner id, свежесть, аварийный ключ, серверные сессии, 401 без данных.
- Имена и `@username` видны только в панели владельца; в Mini App, публичных страницах, логах и ответах чужих маршрутов они не появляются.
- Ручная выдача из панели пишется с `source='manual'`; оплаченное право (`source='paid'`) через эти маршруты не отзывается и не сокращается.
- Право неизменяемо: продление создаёт новую запись права, прежняя не редактируется; в журнале появляется действие `extend` со старым и новым сроком.
- `reason` обязателен для выдачи, продления и отзыва; «Другое» требует пояснения. Комментарий не отправляется пользователю.
- Идемпотентность: повтор с тем же `request_key` возвращает прежний результат и не создаёт второе право. Конкурентное изменение — 409 с актуальным состоянием, операция не выполняется.
- Каждый успешный write-запрос пишет журнал: кто, что, кому, основание, причина, комментарий, прежний и новый срок. Неуспешная попытка не выглядит состоявшейся выдачей.
- Миграции идемпотентны, совместимы с существующей базой staging, не удаляют и не переписывают данные прав.
- Секреты, токены и пути не попадают в ответы, журнал, UI и тесты.
- Тесты не ослабляются; полный прогон выполняется на финальном снимке; существующий контракт UI-тестов обновляется вместе с кодом.

## Review Focus

1. **Двойная отправка и две вкладки.** Владелец дважды нажал «Выдать»: второе нажатие не создаёт второе право и не меняет срок; при этом ответ на повтор содержит тот же `grant_id`.
2. **Отзыв оплаченного права.** Попытка отозвать оплаченное основание через панель обязана вернуть отказ, а не тихо отозвать право или показать успех.
3. **Сокращение срока.** Продление с меньшим сроком, чем текущий, и выдача Viewer человеку со Streamer не должны молча ухудшать права: либо отказ, либо явное подтверждение с расчётом итога.
4. **Приватность имён.** Имя и `@username` не появляются ни в одном ответе, доступном без сессии владельца, и не попадают в логи.
5. **Горячий путь.** Запись профиля и активности с троттлингом не ломает обработку апдейтов при сбое БД и не добавляет запись на каждый апдейт.

---

### Task 1: Миграции схемы фазы B

**Files:**
- Modify: `bot/database.py` (список миграций `:621-695`, новые методы `_migrate_admin_*`)
- Test: `tests/test_admin_schema.py`

**Interfaces:**
- Produces: таблица `telegram_user_profiles(user_id PRIMARY KEY, username, display_name, language_code, first_seen_at, profile_seen_at, last_active_at)`; колонки `reason`, `reason_note`, `comment`, `previous_grant_id` в `entitlement_events`; расширенный `CHECK(action IN ('grant','revoke','extend'))`; запись версии `admin_001_profiles` в `schema_migrations`.

- [ ] **Step 1: Написать падающие тесты `tests/test_admin_schema.py`**

```python
async def test_profiles_table_and_journal_columns_exist(self): ...      # PRAGMA table_info
async def test_migration_is_idempotent(self): ...                       # двойной connect() не падает
async def test_legacy_rows_survive_migration(self): ...                 # старые grants/events на месте
async def test_extend_action_is_allowed_after_migration(self): ...      # INSERT action='extend' проходит
async def test_reason_columns_default_to_null(self): ...                # старые события читаются
```

- [ ] **Step 2: Запустить и убедиться, что падает**

Run: `python -m pytest tests/test_admin_schema.py -v`
Expected: FAIL — нет таблицы `telegram_user_profiles`.

- [ ] **Step 3: Реализовать миграцию**

Метод `_migrate_admin_schema()` по образцу соседних: колонки добавлять через существующий хелпер `Database._add_missing_columns` (`bot/database.py:2576-2581`), таблицу создавать через `CREATE TABLE IF NOT EXISTS`, версию писать `INSERT OR IGNORE` в `schema_migrations`. Изменение `CHECK` у `entitlement_events` — единственная неаддитивная операция: пересоздать таблицу с новым `CHECK(action IN ('grant','revoke','extend'))`, скопировать строки **с сохранением `id`**, сверить `COUNT(*)` до и после, и только затем удалить старую и переименовать; тест обязан проверять, что число и содержимое событий не изменились. Вызвать метод в списке миграций `connect()` последним, после `_migrate_growth_attribution_schema()` и перед `migrate_plus_payments` (`:695-696`).

- [ ] **Step 4: Запустить тесты**

Run: `python -m pytest tests/test_admin_schema.py tests/test_regressions.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bot/database.py tests/test_admin_schema.py
git commit -m "feat(admin): migrate profiles, activity and journal reason columns"
```

---

### Task 2: Профиль и активность на горячем пути

**Files:**
- Modify: `bot/database.py` (методы записи), `bot/middlewares.py` (новый middleware), `main.py` (передача объекта базы в `setup_middlewares`, если требуется)
- Test: `tests/test_admin_profiles.py`

**Interfaces:**
- Consumes: таблицу из Task 1, `data["event_from_user"]` aiogram.
- Produces:
  - `Database.remember_profile(user_id, *, username, display_name, language_code, now) -> None` — upsert, обновляет `profile_seen_at`, `first_seen_at` только при вставке;
  - `Database.touch_activity(user_id, *, now) -> None` — upsert `last_active_at`;
  - `ProfileMiddleware` с троттлингом в памяти: профиль не чаще раза в 24 часа на человека, активность не чаще раза в 5 минут, сбой записи логируется и не прерывает обработчик.

- [ ] **Step 1: Написать падающие тесты**

```python
async def test_new_user_profile_and_first_seen_are_written(self): ...
async def test_repeat_update_keeps_first_seen_and_refreshes_username(self): ...
async def test_activity_is_throttled_within_five_minutes(self): ...     # одна запись на два апдейта
async def test_profile_write_failure_does_not_break_handler(self): ...  # БД бросает → handler выполнен
async def test_middleware_ignores_updates_without_user(self): ...
```

- [ ] **Step 2: Запустить и убедиться, что падает**

Run: `python -m pytest tests/test_admin_profiles.py -v`
Expected: FAIL — нет `remember_profile`.

- [ ] **Step 3: Реализовать**

`remember_profile` — `INSERT ... ON CONFLICT(user_id) DO UPDATE SET username=excluded.username, display_name=excluded.display_name, language_code=excluded.language_code, profile_seen_at=excluded.profile_seen_at`. `touch_activity` — `INSERT ... ON CONFLICT(user_id) DO UPDATE SET last_active_at=excluded.last_active_at`. База передаётся в `setup_middlewares(dp, db=db)` из `main.py:567` — сейчас туда ничего не передаётся, а сервисы кладут в `Dispatcher` (`main.py:564-566`); выбрать один способ и не смешивать. Middleware регистрируется **после** троттлинга, чтобы писать только по апдейтам, дошедшим до обработчика; троттлинг-состояние — словарь `user_id → (profile_written_at, activity_written_at)` с ограничением размера (например, 4096 записей, вытеснение старейших), по образцу чистки в `bot/middlewares.py:46-53`.

- [ ] **Step 4: Запустить тесты**

Run: `python -m pytest tests/test_admin_profiles.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bot/database.py bot/middlewares.py main.py tests/test_admin_profiles.py
git commit -m "feat(admin): remember telegram profiles and last activity on the hot path"
```

---

### Task 3: Люди, карточка, журнал и операции в `Database`

**Files:**
- Modify: `bot/database.py`
- Test: `tests/test_admin_people.py`

**Interfaces:**
- Produces (все `async`):
  - `search_people(query, *, filter_kind, limit, offset, now) -> list[dict]` — поиск по `@username`, username, display_name, числовому ID и Twitch login; точное совпадение ID первым; элементы `{user_id, username, display_name, plan, expires_at, last_active_at}`;
  - `person_card(telegram_user_id, *, now) -> dict | None` — права (все активные основания), аккаунты (Twitch login из `streamer_identities`, каналы из `telegram_channels`), лимиты (50/200/5 через существующие методы), активность; эффективный тариф берётся из `bot/subscription_state.py` и `resolve_effective_viewer` (`bot/entitlements.py`), а не считается заново;
  - `person_history(telegram_user_id, *, limit, offset) -> list[dict]` — события с причиной, комментарием и «было → стало»;
  - `count_active_since(since) -> int`, `count_first_seen_since(since) -> int`, `activity_by_day(*, days, now) -> list[dict]`;
  - `grant_manual_access(...)`, `extend_manual_access(...)`, `revoke_manual_access(...)` — как в Task 5 плана по интерфейсам ниже.

- [ ] **Step 1: Написать падающие тесты**

```python
async def test_search_matches_username_id_and_twitch_login(self): ...
async def test_search_puts_exact_id_first(self): ...
async def test_person_card_includes_limits_and_sources(self): ...
async def test_person_history_returns_reason_and_change(self): ...
async def test_active_today_counts_from_moscow_midnight(self): ...      # граница суток МСК
async def test_new_users_window_is_seven_days(self): ...
async def test_activity_by_day_returns_seven_buckets(self): ...
```

- [ ] **Step 2: Запустить и убедиться, что падает**

Run: `python -m pytest tests/test_admin_people.py -v`
Expected: FAIL — нет `search_people`.

- [ ] **Step 3: Реализовать**

SQL параметризован; поиск — `LIKE` по нормализованным значениям с `ESCAPE`, лимит страницы 20. Границы суток МСК считать фиксированным смещением `timezone(timedelta(hours=3))`, как уже сделано в `bot/handlers/telegram_plus.py:86` — `zoneinfo` в этой среде недоступен без пакета `tzdata`, и новая зависимость не вводится. `AdminDirectory` переводится на эти методы (его `_ACTIVE_GRANTS_SQL` остаётся для фазы A до полного переноса).

- [ ] **Step 4: Запустить тесты**

Run: `python -m pytest tests/test_admin_people.py tests/test_admin_directory.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bot/database.py bot/admin_directory.py tests/test_admin_people.py
git commit -m "feat(admin): search people, read cards and history in the database layer"
```

---

### Task 4: Read-маршруты панели

**Files:**
- Modify: `bot/admin_web.py`, `bot/oauth.py`, `main.py`, `bot/admin_metrics.py`
- Test: `tests/test_admin_api.py`

**Interfaces:**
- Consumes: методы Task 3, существующую сессию `AdminAccess`.
- Produces: `GET /admin/api/users`, `GET /admin/api/users/{id}`, `GET /admin/api/users/{id}/history`, `GET /admin/api/access?state=active|history`; `install_admin_routes(app, access, snapshot_provider, *, directory=None, csrf=None)` — провайдеры передаются из `main.py` через `oauth.py`.

- [ ] **Step 1: Написать падающие тесты**

```python
async def test_read_routes_require_session(self): ...                   # 401 без cookie
async def test_users_search_returns_people_without_secrets(self): ...
async def test_person_card_hides_other_people(self): ...
async def test_access_history_filters_by_action(self): ...
```

- [ ] **Step 2: Запустить и убедиться, что падает**

Run: `python -m pytest tests/test_admin_api.py -v`
Expected: FAIL — маршрут `/admin/api/users` не зарегистрирован.

- [ ] **Step 3: Реализовать маршруты и подключение**

JSON-ответы, `Cache-Control: no-store`, ограничение параметров (длина `q` ≤ 64, `limit` ≤ 100, `offset` ≤ 10000), ошибки — короткие коды без внутренних деталей. Передача зависимостей — через существующий `oauth.py` (`set_admin_snapshot_provider` рядом добавить `set_admin_services`), чтобы `main.py` остался точкой сборки.

- [ ] **Step 4: Запустить тесты**

Run: `python -m pytest tests/test_admin_api.py tests/test_admin_web.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bot/admin_web.py bot/oauth.py main.py tests/test_admin_api.py
git commit -m "feat(admin): expose people and access read endpoints"
```

---

### Task 5: Защита write-запросов и операции с доступами

**Files:**
- Modify: `bot/admin_auth.py` (CSRF-метки), `bot/admin_web.py` (write-маршруты), `bot/database.py` (операции), `bot/oauth.py`, `main.py`
- Test: `tests/test_admin_access_ops.py`, `tests/test_admin_api.py`

**Interfaces:**
- Produces:
  - `AdminAccess.issue_csrf(session_token) -> str`, `AdminAccess.consume_csrf(session_token, value) -> bool` — до 4 активных меток на сессию, метка живёт 15 минут, потребляется один раз;
  - `POST /admin/api/access/grant` → тело `{request_key, target_user_id, plan, expires_at, reason, reason_note?, comment?, csrf}`;
  - `POST /admin/api/access/extend` → `{request_key, grant_id, expected_expires_at, expires_at, reason, reason_note?, comment?, csrf}`;
  - `POST /admin/api/access/revoke` → `{request_key, grant_id, expected_expires_at, reason, reason_note?, comment?, csrf}`;
  - `Database.grant_manual_access`, `extend_manual_access`, `revoke_manual_access` — возвращают `{grant_id, plan, starts_at, expires_at, source, actor, action}`; повтор с тем же `request_key` возвращает прежний результат; расхождение `expected_expires_at` → `ConflictError` (409).

- [ ] **Step 1: Написать падающие тесты**

```python
async def test_write_without_csrf_is_rejected(self): ...                # 403, право не создано
async def test_csrf_token_is_single_use(self): ...                      # второй раз тот же → 403
async def test_foreign_origin_is_rejected(self): ...                    # 403
async def test_grant_writes_manual_source_and_journal_reason(self): ...
async def test_repeated_request_key_returns_same_grant(self): ...
async def test_extend_creates_new_grant_and_logs_previous(self): ...
async def test_revoke_paid_grant_is_refused(self): ...                  # 409/403, право живо
async def test_shorter_expiry_is_refused(self): ...
async def test_conflicting_expected_expiry_returns_409(self): ...
async def test_unknown_reason_is_refused(self): ...
```

- [ ] **Step 2: Запустить и убедиться, что падает**

Run: `python -m pytest tests/test_admin_access_ops.py -v`
Expected: FAIL — нет маршрута `/admin/api/access/grant`.

- [ ] **Step 3: Реализовать**

Проверки на сервере: сессия; `Origin` — только если заголовок присутствует (как в `bot/streamer_web.py:204-206`, иначе существующие POST-тесты и неброузерные клиенты не должны ломаться); CSRF обязателен всегда; `content_type: application/json`; тело ≤ 4096 байт; строгий набор ключей; `request_key` по формату проекта (`[A-Za-z0-9][A-Za-z0-9._:-]{0,127}`); `plan ∈ {viewer_plus, streamer_plus}`; `expires_at` в будущем и не дальше 366 дней; `reason ∈ {compensation, testing, partnership, other}`, для `other` обязателен `reason_note`; отзыв и продление — только `source='manual'`; продление не сокращает срок; для Streamer-владельца выдача отдельного Viewer разрешена с пометкой. Ошибки — коротким кодом в стиле проекта (`web.json_response({"error": "..."}, status=409)`), как в `bot/mini_app_streamer.py:245`. Каждая операция — в одной транзакции с записью журнала.

- [ ] **Step 4: Запустить тесты**

Run: `python -m pytest tests/test_admin_access_ops.py tests/test_admin_api.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bot/admin_auth.py bot/admin_web.py bot/database.py bot/oauth.py main.py tests/test_admin_access_ops.py
git commit -m "feat(admin): guarded manual grant, extend and revoke from the web panel"
```

---

### Task 6: UI «Пользователи», карточка и диалоги операций

**Files:**
- Modify: `bot/admin_ui/index.html`, `bot/admin_ui/panel.js`, `bot/admin_ui/panel.css`
- Test: `tests/test_admin_ui.py`

**Interfaces:**
- Consumes: read- и write-маршруты задач 4–5, `csrf` из снапшота.
- Produces: поиск с фильтрами «Все / Plus / Активны сегодня», список людей, карточка с правами, аккаунтами, лимитами, активностью и лентой, диалог выдачи/продления (520 px, мобильный экран), подтверждение отзыва с составом прав после отзыва, состояния загрузки/пусто/ошибка/409.

- [ ] **Step 1: Написать падающие тесты** (контракт разметки и JS)

```python
async def test_users_screen_has_search_filters_and_list(self): ...
async def test_person_card_shows_limits_and_history(self): ...
async def test_grant_dialog_requires_reason_and_shows_summary(self): ...
async def test_revoke_confirmation_lists_remaining_rights(self): ...
async def test_conflict_state_is_shown_to_the_owner(self): ...
async def test_panel_sends_csrf_header_on_write(self): ...
```

- [ ] **Step 2: Запустить и убедиться, что падает**

Run: `python -m pytest tests/test_admin_ui.py -v`
Expected: FAIL — нет `id="person-card"`.

- [ ] **Step 3: Реализовать UI**

Никакого `innerHTML`: только `textContent` и создание узлов. Диалог удерживает фокус, Escape закрывает безопасно, фокус возвращается в исходную кнопку. Кнопка операции блокируется на время запроса; успех показывается только после ответа сервера; «Результат пока не подтверждён» при потере ответа; 409 → «Права изменились» с новым составом и повторным подтверждением. Время — МСК.

- [ ] **Step 4: Запустить тесты**

Run: `python -m pytest tests/test_admin_ui.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bot/admin_ui tests/test_admin_ui.py
git commit -m "feat(admin): people list, person card and access dialogs"
```

---

### Task 7: Показатели обзора

**Files:**
- Modify: `bot/admin_metrics.py`, `bot/admin_ui/panel.js`, `bot/admin_ui/index.html`
- Test: `tests/test_admin_metrics.py`

**Interfaces:**
- Consumes: `count_active_since`, `count_first_seen_since`, `activity_by_day` из Task 3.
- Produces: блок `activity` в снапшоте `{active_today, new_7d, by_day: [{date, users}]}`; экран «Обзор» показывает «Активны сегодня», «Новые за 7 дней» и график за 7 дней; при отсутствии данных — «Недостаточно данных».

- [ ] **Step 1: Написать падающие тесты**

```python
async def test_activity_block_reports_today_and_week(self): ...
async def test_activity_block_is_none_without_database(self): ...
async def test_activity_failure_does_not_hide_other_blocks(self): ...
```

- [ ] **Step 2: Запустить и убедиться, что падает**

Run: `python -m pytest tests/test_admin_metrics.py -v`
Expected: FAIL — нет ключа `activity`.

- [ ] **Step 3: Реализовать**

Сбор в общем бюджете каталога; при ошибке — `activity = None` и `errors.activity = "unavailable"`, что попадает в «Требует внимания». На графике подписи периода и осей, значения по наведению; вместо нарисованного тренда при неполных данных — «Недостаточно данных».

- [ ] **Step 4: Запустить тесты**

Run: `python -m pytest tests/test_admin_metrics.py tests/test_admin_ui.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bot/admin_metrics.py bot/admin_ui tests/test_admin_metrics.py
git commit -m "feat(admin): show activity today, new users and a seven day chart"
```

---

### Task 8: Приёмка фазы B

**Files:**
- Modify: `docs/STATUS.md`, `docs/DECISIONS.md`, `docs/audits/admin-panel-phase-a-2026-10-04/EVIDENCE.md` (или новый evidence-файл фазы B)

- [ ] **Step 1: Полный прогон**

Run: `python -m pytest -q`
Expected: PASS по админке; падения, не относящиеся к фазе B, перечисляются по именам с причиной и не выдаются за PASS.

- [ ] **Step 2: Браузерный сценарий владельца**

Локальный фикстур: поиск человека, карточка, выдача, продление, отзыв, повторная отправка, конфликт 409, отказ в отзыве оплаченного права, пустые состояния, 360/390/768/1440, клавиатура, `prefers-reduced-motion`. Скриншоты рядом с фазой A; детектор Impeccable — чистый.

- [ ] **Step 3: Проверка отсутствия утечек**

В ответах без сессии и в логах нет имён, `@username`, токенов и путей; имена видны только в снапшоте и карточке под сессией.

- [ ] **Step 4: Записать факты**

`STATUS.md` и `DECISIONS.md`: коммиты, что проверено, что нет (реальный staging-вход, native), следующий шаг. Плюс короткий scenario-чеклист для владельца.

- [ ] **Step 5: Commit**

```bash
git add docs
git commit -m "docs: record admin panel phase B"
```

## Что вне этого плана

Платежи и возвраты, версии staging/production, роли и наблюдатели, массовые операции, блокировка пользователей, сквозная аналитика роста, светлая тема, любые внешние CDN.
