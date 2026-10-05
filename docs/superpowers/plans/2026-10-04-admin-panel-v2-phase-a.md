# Фаза A: админ-панель v2 (UI, DESIGN.md, обзор, доступы) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Заменить read-only страницу «Состояние бота» на графитовую панель с навигацией, обзором, действующими доступами, историей и заглушками, не трогая схему данных и общие файлы, кроме одной строки подключения в `main.py`.

**Architecture:** Данные для новых блоков собирает отдельный read-only модуль `bot/admin_directory.py`, который читает существующие таблицы прав и каталог резервных копий. `AdminSnapshot` вызывает его и добавляет блоки `access`, `backup`, `attention` в существующий снапшот; UI остаётся статическим HTML/CSS/JS без CDN и получает всё через `GET /admin/api/snapshot`.

**Tech Stack:** Python 3.12, aiohttp, SQLite (aiosqlite), unittest, статический HTML/CSS/JS.

**Spec:** `docs/superpowers/specs/2026-10-04-admin-panel-v2-design.md`

## Global Constraints

- Авторизация не меняется: подпись Telegram, owner id, свежесть, серверные сессии, аварийный ключ ≥32 символов. Неавторизованный запрос — 401 без данных.
- CSP панели: `default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'` — никаких CDN и внешних шрифтов.
- Только тёмная тема; палитра: canvas `#171717`, surface `#242424`, raised `#2D2B31`, текст `#F1F1F1`, вторичный `#B9B6BF`, accent `#C7A5FF`, текст на accent `#21162F`.
- Ширины проверки: 360, 390, 768, 1440 px. Цели нажатия ≥44 px. Текст контрастен не ниже 4.5:1. `prefers-reduced-motion` отключает переходы.
- Отсутствие данных показывается как «Нет данных», а не как ноль; выдуманные метрики и тренды запрещены.
- Секреты, токены, пути и персональные имена не попадают в ответы API и в HTML; имена появятся только в фазе B и только для владельца.
- Существующие проверки не ослабляются: `tests/test_admin_web.py`, `tests/test_admin_metrics.py`, `tests/test_admin_ui.py`, `tests/test_admin_entry.py` остаются зелёными.
- Из общих файлов в фазе A меняется только `main.py` (одно подключение `AdminDirectory`). `bot/database.py`, `bot/handlers/*`, `bot/middlewares.py` не трогаются.
- Сбор каждого нового блока данных ограничен 2 секундами; ошибка блока не скрывает остальные и не раскрывает текст исключения.

## Review Focus

1. **Пустая и частично пустая база.** У staging базы может не быть ни одного права: таблицы «Действующие» и «История» обязаны показать «Нет данных»/«Ничего не найдено», а не пустой белый блок и не ноль.
2. **Истёкшее и отозванное право.** Строка с `revoked_at` или истёкшим `expires_at` не должна попадать в «Действующие» и не должна считаться в показателе «Активный Plus».
3. **Streamer Plus и наследуемый Viewer.** Один Streamer-грант покрывает Viewer: в показателе уникальных людей он считается один раз, а в таблице не превращается в две строки.
4. **Отсутствие каталога резервных копий.** Если каталога нет или он пуст, блок «Резервная копия» говорит «Проверка не проводилась» и «Нет данных», а не падает и не показывает вчерашнюю дату.
5. **Узкий экран и длинные имена каналов.** На 360 px таблицы превращаются в вертикальные карточки без горизонтальной прокрутки документа; длинные логины и русские названия не обрезаются.

---

### Task 1: DESIGN.md под графит

**Files:**
- Modify: `DESIGN.md` (полная замена разделов Direction, Tokens, Typography, Layout and components)

**Interfaces:**
- Produces: имена CSS-токенов, которые обязан использовать Task 5: `--canvas`, `--surface`, `--raised`, `--text`, `--muted`, `--border`, `--accent`, `--accent-ink`, `--good`, `--warn`, `--danger`, `--unknown`.

- [ ] **Step 1: Зафиксировать палитру и семантические роли**

Взять значения из `docs/design/admin-concept-2026-10-04/CONCEPT.md` (раздел «Общие компоненты и состояния»). Семантические цвета подобрать так, чтобы каждый читался на `#242424` с контрастом ≥4.5:1, и записать пары «роль → значение → на каком фоне проверено».

- [ ] **Step 2: Переписать `DESIGN.md`**

Разделы: Direction (операционная консоль владельца, графит, спокойные поверхности, лавандовый accent только для главного действия и выбранного состояния), Tokens (таблица ролей со значениями), Typography (заголовок 30–32, секции 18–20, текст 15–16, подписи 12–13, табличные цифры), Layout and components (сайдбар 200–216 px, карточки 20 px, группы 24 px, кнопки 12 px, отступы 4/8/12/16/24/32/48), Responsive and accessibility (360/390/768/1440, 44 px, focus, reduced motion), Evidence and acceptance (только реальные скриншоты, согласие владельца ожидается).

- [ ] **Step 3: Проверить, что токены совпадают с макетом**

Run: `Select-String -Path DESIGN.md -Pattern '#171717|#242424|#2D2B31|#F1F1F1|#B9B6BF|#C7A5FF|#21162F'`
Expected: найдены все семь значений.

- [ ] **Step 4: Commit**

```bash
git add DESIGN.md
git commit -m "docs(design): switch owner panel to graphite design system"
```

---

### Task 2: `bot/admin_directory.py` — read-only выборки панели

**Files:**
- Create: `bot/admin_directory.py`
- Test: `tests/test_admin_directory.py`

**Interfaces:**
- Consumes: объект `Database` (используется только `db.conn`), каталог копий и `retention` из `bot/db_backup.py`.
- Produces (все методы `async`):
  - `access_overview(now: float) -> dict` → `{"active_total": int, "viewer": int, "streamer": int, "by_source": {str: int}, "expiring_7d": int}`
  - `active_grants(now: float, limit: int, offset: int) -> list[dict]` → элементы `{"grant_id": str, "subject_id": str, "plan": str, "source": str, "starts_at": float, "expires_at": float, "issued_by": int}`
  - `history(limit: int, offset: int) -> list[dict]` → элементы `{"grant_id": str, "plan": str, "source": str, "action": str, "actor_telegram_id": int, "happened_at": float}`
  - `backup_status() -> dict` → `{"last_backup_at": float | None, "last_backup_name": str | None, "retention": int, "restore_verified": False}`
  - `deliveries_24h(now: float) -> dict` → `{"notifications": int, "reports": int, "total": int}`
  - `channel_usage(chat_id: int) -> dict` → `{"used": int, "limit": int}`; `video_usage(telegram_user_id: int) -> dict` → `{"used": int, "limit": int}`

- [ ] **Step 1: Написать падающие тесты `tests/test_admin_directory.py`**

Тесты на реальной `Database(":memory:")` (образец — `tests/test_admin_metrics.py:9-30`). Обязательные случаи:

```python
async def test_active_grants_exclude_revoked_and_expired(self): ...      # active_total == 2
async def test_streamer_grant_counts_viewer_once(self): ...              # viewer == 1 при одном streamer-гранте
async def test_by_source_splits_test_paid_mock(self): ...                # {"test": 1, "paid": 1}
async def test_expiring_7d_counts_only_future_within_window(self): ...   # 1
async def test_history_returns_events_newest_first(self): ...            # порядок по happened_at DESC
async def test_empty_database_returns_zeroes_and_empty_lists(self): ...  # без исключений
async def test_backup_status_without_directory_is_honest(self): ...      # last_backup_at is None, restore_verified False
async def test_deliveries_24h_counts_done_notifications_and_reports(self): ...  # успех только status='done' и text_sent=1
async def test_deliveries_24h_excludes_older_than_window(self): ...      # запись 25 ч назад не считается
async def test_channel_usage_respects_viewer_plus_limit(self): ...       # 200 при действующем viewer plus, иначе 50
```

- [ ] **Step 2: Запустить и убедиться, что падает**

Run: `python -m pytest tests/test_admin_directory.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'bot.admin_directory'`.

- [ ] **Step 3: Реализовать модуль**

Один класс `AdminDirectory` с `__init__(self, db, *, backup_dir: Path | str, retention: int = 5, now: Callable[[], float] = time.time)`. SQL — параметризованный, только `SELECT`. Активность права определяется теми же условиями, что в `bot/entitlements.py:29-63`: `revoked_at IS NULL AND starts_at <= now AND expires_at > now`; уникальность людей для Streamer — через `beneficiary_telegram_user_id`. Лимиты брать из `bot/plan_catalog.py`, а не дублировать числа.

- [ ] **Step 4: Запустить тесты**

Run: `python -m pytest tests/test_admin_directory.py -v`
Expected: PASS, все случаи.

- [ ] **Step 5: Commit**

```bash
git add bot/admin_directory.py tests/test_admin_directory.py
git commit -m "feat(admin): add read-only directory queries for the owner panel"
```

---

### Task 3: Блоки `access`, `backup`, `attention` в снапшоте

**Files:**
- Modify: `bot/admin_metrics.py:47-184`
- Test: `tests/test_admin_metrics.py:50-157`

**Interfaces:**
- Consumes: `AdminDirectory` из Task 2.
- Produces: в ответе `collect()` новые ключи `access`, `backup`, `attention`; в `errors` новый ключ `directory`. `AdminSnapshot.__init__` получает необязательный `directory=None`.

- [ ] **Step 1: Написать падающие тесты**

```python
async def test_missing_directory_leaves_new_blocks_none(self): ...        # access/backup is None
async def test_directory_values_land_in_snapshot(self): ...              # active_total и last_backup_at проброшены
async def test_directory_failure_is_isolated(self): ...                  # errors["directory"] == "unavailable", остальное живо
async def test_attention_lists_queues_and_errors_by_impact(self): ...    # failed_jobs > 0 → первая строка про очередь
async def test_attention_empty_when_everything_healthy(self): ...        # []
```

- [ ] **Step 2: Запустить и убедиться, что падает**

Run: `python -m pytest tests/test_admin_metrics.py -v`
Expected: FAIL на новых assert (`KeyError: 'access'`).

- [ ] **Step 3: Реализовать сбор блоков**

Каждый вызов — внутри `asyncio.wait_for(..., 2.0)` и `try/except`, по образцу существующих блоков `bot/admin_metrics.py:121-138`. `attention` формируется из уже собранных данных: `failed_jobs > 0`, `due_jobs > 0` со старейшим заданием старше 5 минут, ошибки poller/EventSub/preview, `errors["database"]`. Строки `attention` — словари `{"kind", "title", "detail", "severity"}`, максимум три, отсортированные по влиянию на людей.

- [ ] **Step 4: Запустить тесты**

Run: `python -m pytest tests/test_admin_metrics.py tests/test_admin_directory.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bot/admin_metrics.py tests/test_admin_metrics.py
git commit -m "feat(admin): expose access, backup and attention blocks in the snapshot"
```

---

### Task 4: Подключение `AdminDirectory` в `main.py`

**Files:**
- Modify: `main.py:47` (импорт), `main.py:803-811` (создание `AdminSnapshot`)

**Interfaces:**
- Consumes: `AdminDirectory` из Task 2, `config.db_path`, путь каталога копий и параметры `run_backup_loop` (`main.py:774-783`).
- Produces: работающая панель с непустыми блоками `access` и `backup` в локальном запуске и на staging.

- [ ] **Step 1: Передать каталог копий**

Использовать тот же каталог и `retention`, что у `run_backup_loop` в `main.py:774-783`; не вычислять путь заново другой формулой.

- [ ] **Step 2: Создать `AdminDirectory` и передать в `AdminSnapshot`**

Одна строка создания рядом с `AdminSnapshot(...)`, один новый аргумент. Если `main.py` в этот момент занят параллельной работой другого чата — остановиться и сообщить, а не разрешать конфликт вручную.

- [ ] **Step 3: Проверить, что панель поднимается и отдаёт новые блоки**

Run: `python -m pytest tests/test_admin_web.py tests/test_admin_ui.py tests/test_admin_metrics.py -v`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add main.py
git commit -m "feat(admin): wire read-only directory into the owner panel snapshot"
```

---

### Task 5: Оболочка панели и хеш-роутер

**Files:**
- Modify: `bot/admin_ui/index.html`, `bot/admin_ui/panel.css`, `bot/admin_ui/panel.js`
- Test: `tests/test_admin_ui.py`

**Interfaces:**
- Consumes: снапшот из Task 3.
- Produces: разметку с `id="app-nav"`, `id="view-overview"`, `id="view-users"`, `id="view-access"`, `id="view-system"`, `id="view-growth"`, `id="view-payments"`, `id="attention-list"`, `id="stat-*"`, `id="access-active-body"`, `id="access-history-body"`, `id="backup-state"`; функции `render(data)` и `navigate(hash)`.

- [ ] **Step 1: Обновить контрактный тест**

В `tests/test_admin_ui.py` заменить проверки старых `id` (`health-grid`, `live-queue-*`) на новый набор: наличие `id="app-nav"`, шести `id="view-*"`, `id="backup-state"`; сохранённые требования (`href="#main-content"`, `src="/admin/panel.js"`, 401 на статику без сессии) остаются. Никаких `skip`.

- [ ] **Step 2: Запустить и убедиться, что падает**

Run: `python -m pytest tests/test_admin_ui.py -v`
Expected: FAIL — в HTML нет `id="app-nav"`.

- [ ] **Step 3: Переписать оболочку**

`index.html`: скип-ссылка, сайдбар, мобильная нижняя навигация, шапка (окружение, «Обновить», «Выйти»), контейнеры экранов, живые регионы для обновления. `panel.css`: токены из Task 1, сетка 200–216 px + рабочая область, адаптив на 768 px, состояния `[data-state]`, focus-visible, `prefers-reduced-motion`. `panel.js`: хеш-роутер (`#/overview`, `#/users`, `#/access`, `#/system`, `#/growth`, `#/payments`), общий `refresh()` с сохранением прежнего поведения (timeout 8 с, 401 → `/admin`, «Данные устарели» после 90 с).

- [ ] **Step 4: Запустить тесты**

Run: `python -m pytest tests/test_admin_ui.py tests/test_admin_web.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bot/admin_ui tests/test_admin_ui.py
git commit -m "feat(admin): rebuild owner panel shell with graphite tokens and hash routing"
```

---

### Task 6: Экран «Обзор»

**Files:**
- Modify: `bot/admin_ui/index.html`, `bot/admin_ui/panel.js`, `bot/admin_ui/panel.css`

**Interfaces:**
- Consumes: `data.attention`, `data.access`, `data.audience`, `data.queues`, `data.resources`, `data.backup`.
- Produces: показатели «Всего пользователей», «Активный Plus», «Доставки за 24 ч», «Новые за 7 дней» (честное «Нет данных»), список «Требует внимания», строка здоровья.
- [ ] **Step 1: Написать падающий тест на «Нет данных»**

В `tests/test_admin_ui.py` новый тест: `panel.js` содержит литерал `'Нет данных'` для показателей `stat-new-7d` и `stat-active-today`, а `renderAttention` при пустом массиве не создаёт строк и показывает «Нет открытых проблем».

- [ ] **Step 2: Запустить и убедиться, что падает**

Run: `python -m pytest tests/test_admin_ui.py -v`
Expected: FAIL.

- [ ] **Step 3: Реализовать экран**

Порядок: заголовок и свежесть → строка здоровья («Система работает» / «Есть задержки» / «Не удалось проверить» с кнопкой «Проверить») → показатели → «Требует внимания» (до трёх) → быстрые действия («Найти пользователя», «Выдать доступ» — второе в фазе A неактивно с объяснением «появится вместе с управлением доступом»).

Показатели: «Всего пользователей» — `data.audience.private_users`; «Активный Plus» — `data.access.active_total` с разбивкой по источникам в подписи; «Доставки за 24 ч» — `data.deliveries.total` с подписью «доставки уведомлений и отчётов»; «Активны сегодня» и «Новые за 7 дней» — «Нет данных» с причиной при раскрытии (появятся в фазе B).

- [ ] **Step 4: Запустить тесты**

Run: `python -m pytest tests/test_admin_ui.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bot/admin_ui tests/test_admin_ui.py
git commit -m "feat(admin): add overview screen with honest unknown metrics"
```

---

### Task 7: Экран «Доступы» (действующие и история)

**Files:**
- Modify: `bot/admin_ui/index.html`, `bot/admin_ui/panel.js`, `bot/admin_ui/panel.css`

**Interfaces:**
- Consumes: `data.access.active_rows`, `data.access.history`.
- Produces: вкладки «Действующие»/«История», таблица с подписями дат в МСК, состояния «Нет данных»/«Ничего не найдено», разворачиваемая строка истории «было → стало» (в фазе A доступна только часть «стало»).

- [ ] **Step 1: Написать падающий тест**

```python
async def test_access_screen_has_tabs_and_empty_states(self): ...  # id="tab-active"/"tab-history", текст «Ничего не найдено»
async def test_access_rows_show_source_and_expiry(self): ...        # поля plan/source/expires_at рендерятся
```

- [ ] **Step 2: Запустить и убедиться, что падает**

Run: `python -m pytest tests/test_admin_ui.py -v`
Expected: FAIL.

- [ ] **Step 3: Реализовать экран**

Таблица действующих: получатель (числовой ID в фазе A), план, источник («тестовый доступ» / «оплата» / «проверка оплаты»), срок, кто выдал. История: время, действие, план, исполнитель (`0` → «Автоматически»), результат. Строки с истёкшим сроком в «Действующих» не показываются.

- [ ] **Step 4: Запустить тесты**

Run: `python -m pytest tests/test_admin_ui.py tests/test_admin_directory.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bot/admin_ui tests/test_admin_ui.py
git commit -m "feat(admin): add access and history screens"
```

---

### Task 8: Экран «Система» и заглушки «Рост» и «Оплаты»

**Files:**
- Modify: `bot/admin_ui/index.html`, `bot/admin_ui/panel.js`, `bot/admin_ui/panel.css`

**Interfaces:**
- Consumes: `data.telegram`, `data.twitch`, `data.preview`, `data.queues`, `data.resources`, `data.backup`.
- Produces: строки состояний Telegram, Twitch, очереди уведомлений, видеопревью, базы, резервной копии; заглушки с объяснением.

- [ ] **Step 1: Написать падающий тест**

```python
async def test_system_screen_shows_backup_and_queue(self): ...        # id="backup-state", "restore-verified"
async def test_growth_and_payments_are_explained_placeholders(self): ...  # «Нет сквозных данных», «Приём платежей не подключён»
async def test_users_screen_explains_profiles_come_later(self): ...    # в фазе A: профили и поиск по именам появятся после обновления
async def test_unknown_state_is_not_shown_as_ok(self): ...            # state unknown → «Не удалось проверить»
```

- [ ] **Step 2: Запустить и убедиться, что падает**

Run: `python -m pytest tests/test_admin_ui.py -v`
Expected: FAIL.

- [ ] **Step 3: Реализовать экран**

Каждая строка: состояние словом и знаком, наблюдаемое время, короткое объяснение. «Данные устарели» для просроченных, «Не удалось проверить» при отсутствии сигнала. Резервная копия: дата создания и отдельная строка «Проверка восстановления: не проводилась». Диагностические детали — при раскрытии, без путей и токенов.

Экран «Пользователи» в фазе A: заголовок, объяснение, что профили и поиск по именам появятся после обновления, и рабочее поле поиска по числовому Telegram ID, ведущее на экран карточки. Карточка в фазе A показывает только права и сроки по ID, без имён, аккаунтов и ленты; адрес `#/users/<id>` открывается и не ломается при отсутствии прав.

- [ ] **Step 4: Запустить тесты**

Run: `python -m pytest tests/test_admin_ui.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bot/admin_ui tests/test_admin_ui.py
git commit -m "feat(admin): add system screen and explained placeholders"
```

---

### Task 9: Доступность, адаптив и приёмка фазы A

**Files:**
- Modify: `bot/admin_ui/panel.css`, `bot/admin_ui/index.html` (при необходимости)
- Test: `tests/test_admin_ui.py`

- [ ] **Step 1: Проверить ширины и клавиатуру**

Запустить локальную панель на тестовом снапшоте (`tests/admin_ui_fixture.py`) и пройти сценарий владельца: вход по аварийному ключу, обзор, доступы, система, рост, оплаты. Проверить 360/390/768/1440 px, обход клавиатурой, фокус, увеличение 200 %, отсутствие горизонтальной прокрутки, `prefers-reduced-motion`.

- [ ] **Step 2: Снять скриншоты**

Сохранить в `docs/design/admin-concept-2026-10-04/phase-a/` (тёмная тема, четыре ширины, состояния «Нет данных» и «Ничего не найдено»).

- [ ] **Step 3: Прогнать полный набор**

Run: `python -m pytest -q`
Expected: PASS, без новых пропусков и ослабленных проверок.

- [ ] **Step 4: Обновить документы**

`docs/STATUS.md` и `docs/DECISIONS.md`: что сделано, коммит, результат тестов, что осталось в фазе B, что непроверено (нативные устройства, реальный staging-вход, если он недоступен).

- [ ] **Step 5: Commit**

```bash
git add docs/STATUS.md docs/DECISIONS.md docs/design/admin-concept-2026-10-04/phase-a
git commit -m "docs: record phase A of the owner admin panel"
```

## Что вне этого плана

Фаза B (профили с именами, отметка активности, причина и комментарий в журнале, поиск и карточка человека, выдача/продление/отзыв из веба) описывается отдельным планом: она меняет `bot/database.py`, `bot/handlers/*` и `bot/middlewares.py`, которые сейчас заняты параллельной работой над Mini App.
