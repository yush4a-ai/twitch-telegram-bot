# Редизайн панели владельца по одобренному прототипу — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Привести браузерную панель владельца (`bot/admin_ui`) к виду одобренного прототипа: тёмный графит, рельс 88 px, правая колонка подробностей, единая шкала скруглений, пять состояний на каждом экране.

**Architecture:** Меняется только слой представления (`index.html`, `panel.css`, `panel.js`) и его тесты. Серверные маршруты, авторизация, сессии, снапшот и все данные остаются прежними; новых сетевых вызовов не появляется. Разметка сохраняет существующие id, чтобы не ломать `panel.js` и тесты.

**Tech Stack:** HTML + CSS + обычный JavaScript (без сборки и CDN), Python 3.12 `.venv`, `pytest`, headless Chrome для снимков.

**Spec:** `docs/superpowers/specs/2026-10-05-owner-admin-redesign-and-broadcasts-design.md` (разделы 3–5) и одобренный прототип `docs/design/admin-redesign-2026-10-05/prototype.html`.

## Global Constraints

- Только тёмная тема, `color-scheme: dark`; светлой темы нет.
- Все размеры и отступы кратны 4 px; шкала скруглений одна: 8 px — теги, 12 px — кнопки/поля/мини-блоки, 16 px — карточки и секции. Радиусов 20/24 px не остаётся.
- Никаких внешних ресурсов: без CDN, внешних шрифтов и картинок.
- Цели нажатия не меньше 44 px; видимый фокус клавиатуры обязателен.
- Горизонтального переполнения нет на 360, 390, 768, 1440 px.
- Сохраняются: маршруты `#/overview… #/payments`, серверная авторизация, `Cache-Control: no-store`, скрытие данных до входа.
- Тексты интерфейса проходят `stop-slop`; цвет никогда не несёт смысл в одиночку.
- Тесты не ослабляются и не пропускаются: устаревшие проверки заменяются по смыслу, а не удаляются.
- Тесты запускаются так: `.venv\Scripts\python.exe -m pytest … -q` из корня проекта.

## Review Focus

1. **Длинные русские имена и логины** — строка таблицы не должна ломать колонки и вызывать горизонтальную прокрутку; текст переносится, а не обрезается многоточием там, где это скрывает смысл.
2. **Пустое и устаревшее не выглядит как ноль или «сейчас»** — «Нет данных» и отметка времени обязательны; цифры без времени наблюдения не показываются.
3. **Мобильная нижняя навигация** не перекрывает последний блок и кнопки; у контента есть нижний отступ под неё.
4. **Состояния не двигают раскладку** — скелетон, пусто, ошибка и устарело занимают те же области, что и данные.
5. **До входа и при истёкшей сессии** — никаких операционных данных; ответ 401 уводит на страницу входа.

---

### Task 1: Токены и оболочка

**Files:**
- Modify: `bot/admin_ui/panel.css:1-70` (токены, `.shell`, `.sidebar`, `.topbar`, `.button`)
- Modify: `bot/admin_ui/index.html:14-47` (оболочка, шапка, навигация)
- Test: `tests/test_admin_ui.py`

**Interfaces:**
- Consumes: ничего.
- Produces: классы `.rail` (бывший `.sidebar`, id `app-nav` сохраняется), `.rail-link`, `.rail-icon`, `.topbar`, `.topbar-actions`; токены `--radius-chip: 8px`, `--radius-control: 12px`, `--radius-card: 16px`, `--radius-group: 16px`.

- [x] **Step 1: Написать падающие проверки**

В `tests/test_admin_ui.py` заменить проверки старых радиусов и сайдбара на новые: CSS содержит `--radius-card: 16px`, `--radius-group: 16px`, `--radius-chip: 8px`; в CSS нет `--radius-card: 20px` и `--radius-group: 24px`; `index.html` содержит `class="rail"` и `id="app-nav"`; каждый пункт навигации имеет `class="rail-link"` и текстовую подпись рядом с иконкой.

- [x] **Step 2: Убедиться, что проверки падают**

Run: `.venv\Scripts\python.exe -m pytest tests/test_admin_ui.py -q`
Expected: FAIL — радиусы 20/24 px, класса `rail` нет.

- [x] **Step 3: Правка CSS и разметки**

`panel.css`: `--radius-card` → `16px`, `--radius-group` → `16px`, добавить `--radius-chip: 8px`; `.shell` → `grid-template-columns: 88px minmax(0, 1fr)`; `.sidebar` переименовать в `.rail` (`padding: 16px 0`, симметрично, `align-items: center`); `.rail-link` — вертикальная колонка «иконка + подпись 10–11 px», `min-height: 44px`, `border-radius: 12px`, активное состояние — фон `--surface` и граница `--border`.
`index.html`: у `aside` оставить `id="app-nav"` и `aria-label`, добавить класс `rail`; каждый `a.nav-link` → `a.rail-link` с `<b aria-hidden="true">` для знака и `<span>` для подписи; в шапке оставить `#environment`, `#refresh`, `#logout`; добавить `color-scheme: dark` в `panel.css`, если его нет.

- [x] **Step 4: Проверить**

Run: `.venv\Scripts\python.exe -m pytest tests/test_admin_ui.py tests/test_admin_web.py -q`
Expected: PASS.

- [x] **Step 5: Commit**

```bash
git add bot/admin_ui/index.html bot/admin_ui/panel.css tests/test_admin_ui.py
git commit -m "feat(admin): graphite shell with 88px rail and 4px radius scale"
```

---

### Task 2: Экран «Обзор»

**Files:**
- Modify: `bot/admin_ui/index.html:51-101` (`#view-overview`)
- Modify: `bot/admin_ui/panel.css` (`.metric-grid`, `.subsystems`, `.aside`, `.event-list`)
- Modify: `bot/admin_ui/panel.js:123-200` (`renderMetrics`, добавление `renderEvents`)
- Test: `tests/test_admin_ui.py`, `tests/test_admin_metrics.py`

**Interfaces:**
- Consumes: Task 1 (`--radius-card`, `.rail`).
- Produces: `renderEvents(events)` — рисует до трёх последних событий в `#event-list`; контейнеры `#subsystem-list`, `#quick-actions`, `#event-list`.

- [x] **Step 1: Написать падающие проверки**

`tests/test_admin_ui.py`: `index.html` содержит `id="subsystem-list"`, `id="quick-actions"`, `id="event-list"`; `panel.js` содержит `function renderEvents(`; `.metric-grid` в CSS — `grid-template-columns: repeat(3, minmax(0, 1fr))`.
`tests/test_admin_metrics.py`: снапшот без `attention` рисует «Нет открытых проблем», а не пустоту.

- [x] **Step 2: Проверить падение**

Run: `.venv\Scripts\python.exe -m pytest tests/test_admin_ui.py tests/test_admin_metrics.py -q`
Expected: FAIL — контейнеров и функции нет.

- [x] **Step 3: Реализация**

`index.html`: сетка показателей 3 колонки, последняя карточка — на две колонки; под ней три мини-строки подсистем (`Telegram`, `Twitch`, `База`) в `#subsystem-list`; правая колонка `<aside class="aside">` с `#attention-list` и `#event-list`; блок `#quick-actions` с тремя кнопками (навигация по существующим `#/users`, `#/access`).
`panel.js`: `renderMetrics` заполняет карточки и мини-строки; `renderEvents` берёт `data.live` и `data.deliveries`, сортирует по времени и рисует до трёх строк «что · где · время», каждое состояние — словом и знаком.
`panel.css`: `.metric-grid` 3 колонки с промежутком 16 px; `.aside` — 340 px, граница слева, `padding: 24px 20px`; `.event` — сетка «знак · текст · время», время моноширинное.

- [x] **Step 4: Проверить**

Run: `.venv\Scripts\python.exe -m pytest tests/test_admin_ui.py tests/test_admin_metrics.py tests/test_admin_api.py -q`
Expected: PASS.

- [x] **Step 5: Commit**

```bash
git add bot/admin_ui tests/test_admin_ui.py tests/test_admin_metrics.py
git commit -m "feat(admin): overview screen with subsystems and event column"
```

---

### Task 3: Экран «Люди»

**Files:**
- Modify: `bot/admin_ui/index.html:102-158` (`#view-users`)
- Modify: `bot/admin_ui/panel.css` (`.person`, `.person-card`, `.filters`)
- Modify: `bot/admin_ui/panel.js:375-503` (`renderPeople`, `renderPerson`)
- Test: `tests/test_admin_ui.py`, `tests/test_admin_people.py`

**Interfaces:**
- Consumes: Task 1.
- Produces: контейнеры `#people-list`, `#person-card`, `#people-filters`.

- [x] **Step 1: Написать падающие проверки**

`tests/test_admin_ui.py`: есть `id="people-filters"`, `id="person-card"`; в `panel.css` есть `.person-card` с `border-radius: var(--radius-card)`.
`tests/test_admin_people.py`: карточка человека без Twitch показывает «Не подключён», без каналов — «Нет данных», и не показывает «Никогда».

- [x] **Step 2: Проверить падение**

Run: `.venv\Scripts\python.exe -m pytest tests/test_admin_ui.py tests/test_admin_people.py -q`
Expected: FAIL.

- [x] **Step 3: Реализация**

`index.html`: строка фильтров (поиск + «Все / Plus / Активны сегодня / Фильтры») в `#people-filters`, таблица в `#people-list`, правая колонка `#person-card` с блоком прав и двумя кнопками («Изменить доступ», «Написать»).
`panel.js`: `renderPeople` рисует строки с инициалом, именем, `@username`, тарифом (тег), активностью и лимитами; `renderPerson` заполняет карточку и явно подписывает неизвестные значения фразами из Step 1.
`panel.css`: `.person` — строка с `min-height: 44px`; `.person-card` — поверхность 16 px радиуса; длинные имена переносятся (`overflow-wrap: anywhere`).

- [x] **Step 4: Проверить**

Run: `.venv\Scripts\python.exe -m pytest tests/test_admin_ui.py tests/test_admin_people.py tests/test_admin_api.py -q`
Expected: PASS.

- [x] **Step 5: Commit**

```bash
git add bot/admin_ui tests/test_admin_ui.py tests/test_admin_people.py
git commit -m "feat(admin): people list and person card in the new layout"
```

---

### Task 4: Экран «Доступы»

**Files:**
- Modify: `bot/admin_ui/index.html:159-186` (`#view-access`)
- Modify: `bot/admin_ui/panel.css` (`.tabs`, `.tab`, `.data-table`)
- Modify: `bot/admin_ui/panel.js:209-274` (`renderAccess`)
- Test: `tests/test_admin_ui.py`, `tests/test_admin_access_ops.py`

**Interfaces:**
- Consumes: Task 1.
- Produces: `#access-active`, `#access-history`, `#access-expiring` — контейнеры вкладок.

- [x] **Step 1: Написать падающие проверки**

`tests/test_admin_ui.py`: есть `id="access-expiring"`; `.tab` имеет `min-height: 44px`.
`tests/test_admin_access_ops.py`: журнал без причины показывает «Причина не указана» и не выдумывает текст.

- [x] **Step 2: Проверить падение**

Run: `.venv\Scripts\python.exe -m pytest tests/test_admin_ui.py tests/test_admin_access_ops.py -q`
Expected: FAIL.

- [x] **Step 3: Реализация**

`index.html`: три вкладки-кнопки с `aria-selected`, таблицы внутри контейнеров.
`panel.js`: `renderAccess` заполняет действующие, историю (когда · что · кто · было → стало · причина) и истекающие; пустые значения — по Step 1.
`panel.css`: `.tabs` — граница 16 px радиуса, `.tab` — 44 px, активная вкладка с фоном `--surface`.

- [x] **Step 4: Проверить**

Run: `.venv\Scripts\python.exe -m pytest tests/test_admin_ui.py tests/test_admin_access_ops.py -q`
Expected: PASS.

- [x] **Step 5: Commit**

```bash
git add bot/admin_ui tests/test_admin_ui.py tests/test_admin_access_ops.py
git commit -m "feat(admin): access tabs and journal in the new layout"
```

---

### Task 5: Экран «Система»

**Files:**
- Modify: `bot/admin_ui/index.html:187-248` (`#view-system`)
- Modify: `bot/admin_ui/panel.css` (`.sysrow`)
- Modify: `bot/admin_ui/panel.js:275-333` (`renderSystem`)
- Test: `tests/test_admin_ui.py`, `tests/test_admin_metrics.py`

**Interfaces:**
- Consumes: Task 1.
- Produces: `#system-rows` — контейнер групповых строк.

- [x] **Step 1: Написать падающие проверки**

`tests/test_admin_ui.py`: есть `id="system-rows"`, класс `.sysrow`.
`tests/test_admin_metrics.py`: подсистема без наблюдения показывается как «Не удалось проверить», а не «работает».

- [x] **Step 2: Проверить падение**

Run: `.venv\Scripts\python.exe -m pytest tests/test_admin_ui.py tests/test_admin_metrics.py -q`
Expected: FAIL.

- [x] **Step 3: Реализация**

`index.html` + `panel.js`: шесть строк (Telegram, Twitch, очередь уведомлений, видеопревью, база, резервная копия), в каждой — название, состояние словом и знаком, пояснение и время наблюдения (`HH:MM` моноширинно); отсутствие сигнала — «Не удалось проверить».
`panel.css`: `.sysrow` — фиксированные колонки 200/180/1fr/auto, разделитель снизу, на узких ширинах — вертикальная раскладка.

- [x] **Step 4: Проверить**

Run: `.venv\Scripts\python.exe -m pytest tests/test_admin_ui.py tests/test_admin_metrics.py -q`
Expected: PASS.

- [x] **Step 5: Commit**

```bash
git add bot/admin_ui tests/test_admin_ui.py tests/test_admin_metrics.py
git commit -m "feat(admin): system rows with honest per-subsystem state"
```

---

### Task 6: Экраны «Рост» и «Оплаты»

**Files:**
- Modify: `bot/admin_ui/index.html:249-290` (`#view-growth`, `#view-payments`)
- Modify: `bot/admin_ui/panel.css` (`.empty`)
- Modify: `bot/admin_ui/panel.js:334-374` (`renderGrowth`)
- Test: `tests/test_admin_ui.py`

**Interfaces:**
- Consumes: Task 1.
- Produces: класс `.empty` — общая заглушка «причина + действие».

- [x] **Step 1: Написать падающие проверки**

`tests/test_admin_ui.py`: `panel.js` содержит литерал «Недостаточно данных» для роста; `index.html` содержит «Приём платежей не подключён»; `.empty` в CSS имеет `border-style: dashed`.

- [x] **Step 2: Проверить падение**

Run: `.venv\Scripts\python.exe -m pytest tests/test_admin_ui.py -q`
Expected: FAIL.

- [x] **Step 3: Реализация**

`renderGrowth` показывает то, что известно (сайт, приглашения), и «нет данных» для остального; «Оплаты» — статическая заглушка со ссылкой на `#/access`. Кнопки «создать платёж» нет.

- [x] **Step 4: Проверить**

Run: `.venv\Scripts\python.exe -m pytest tests/test_admin_ui.py -q`
Expected: PASS.

- [x] **Step 5: Commit**

```bash
git add bot/admin_ui tests/test_admin_ui.py
git commit -m "feat(admin): honest growth and payments placeholders"
```

---

### Task 7: Пять состояний на всех экранах

**Files:**
- Modify: `bot/admin_ui/panel.js` (`showError`, `emptyRow`, `setState`, `updateFreshness`)
- Modify: `bot/admin_ui/panel.css` (`.skeleton`, `.stale`, `.state-block`)
- Test: `tests/test_admin_ui.py`

**Interfaces:**
- Consumes: Tasks 1–6 (все контейнеры экранов).
- Produces: `renderSkeleton(containerId, rows)`, `showEmpty(containerId, text, actionLabel)`, `showStale(containerId, observedAt)`.

- [x] **Step 1: Написать падающие проверки**

`tests/test_admin_ui.py`: `panel.js` содержит `function renderSkeleton(`; скелетон не пишет цифры (`0` не появляется в разметке скелетона); при `lastUpdated` старше 90 секунд в шапке появляется слово «устарели»; ответ 401 уводит на `/admin`.

- [x] **Step 2: Проверить падение**

Run: `.venv\Scripts\python.exe -m pytest tests/test_admin_ui.py -q`
Expected: FAIL.

- [x] **Step 3: Реализация**

Загрузка — скелетоны в местах содержимого; пусто — причина и кнопка действия; ошибка — что не удалось и «Повторить», введённое не очищается; устарело — время последнего успеха и «Обновить»; нет доступа — редирект на вход без данных. Все пять занимают те же области, что и данные (высота контейнера не меняется).

- [x] **Step 4: Проверить**

Run: `.venv\Scripts\python.exe -m pytest tests/test_admin_ui.py tests/test_admin_web.py -q`
Expected: PASS.

- [x] **Step 5: Commit**

```bash
git add bot/admin_ui tests/test_admin_ui.py
git commit -m "feat(admin): five interface states that do not shift layout"
```

---

### Task 8: Адаптив, доступность и снимки

**Files:**
- Modify: `bot/admin_ui/panel.css` (`@media` 768 px и ниже, `prefers-reduced-motion`)
- Modify: `scripts/admin_ui_fixture.py` (создан в Task 1 для визуальной проверки; дополнить видами и состояниями)
- Create: `docs/design/admin-redesign-2026-10-05/panel-frame.html` (рамка с iframe 360/390: headless Chrome не умеет окно уже 500 px)
- Test: `tests/test_admin_ui.py`

**Interfaces:**
- Consumes: Tasks 1–7.
- Produces: снимки `docs/design/admin-redesign-2026-10-05/screenshots/impl/responsive-<width>-<view>.png` для 768 и 1440 px и `responsive-narrow-frame.png` для 360/390 px. Отдельный скрипт-харнесс снимков в этой среде не запускается: `.ps1` блокирует политика выполнения, а headless Chrome, запущенный из Python, не создаёт файл. Рабочая процедура — фикстура фоном плюс прямые вызовы браузера.

- [x] **Step 1: Написать падающие проверки**

`tests/test_admin_ui.py`: в CSS есть `@media (max-width: 768px)`; в мобильной ветке `.rail` скрыт, есть `.mobile-nav` с пунктами; есть `env(safe-area-inset-bottom)`; есть `@media (prefers-reduced-motion: reduce)`.

- [x] **Step 2: Проверить падение**

Run: `.venv\Scripts\python.exe -m pytest tests/test_admin_ui.py -q`
Expected: FAIL.

- [x] **Step 3: Реализация**

`panel.css`: ниже 768 px — одна колонка, `.rail` скрыт, `.mobile-nav` фиксирован снизу, отступ под неё, правая колонка переезжает под содержимое.
`docs/design/admin-redesign-2026-10-05/panel-frame.html`: два iframe (360 и 390 px) на локальную фикстуру — так узкие ширины видны целиком, потому что headless Chrome не делает окно уже 500 px.
Процедура снимков: поднять `scripts/admin_ui_fixture.py` фоном, затем снять Chrome headless для 768 и 1440 px напрямую и один снимок рамки для 360/390 px.

- [x] **Step 4: Проверить**

Run: `.venv\Scripts\python.exe -m pytest tests/test_admin_ui.py tests/test_admin_web.py -q`, затем снимки по процедуре выше.
Expected: PASS; снимки созданы; на 360 и 390 px (через рамку) горизонтального переполнения нет.

- [x] **Step 5: Commit**

```bash
git add bot/admin_ui tests/test_admin_ui.py scripts/admin_ui_fixture.py scripts/admin_ui_shots.py
git commit -m "feat(admin): mobile layout, motion preferences and screenshot harness"
```

---

## Вне объёма этого плана

- Раздел «Рассылки» (кампании, отправка, отписки) — отдельный план после подтверждения спецификации `docs/superpowers/specs/2026-10-05-owner-admin-redesign-and-broadcasts-design.md`.
- Любые серверные маршруты, права, сессии и работа с базой не меняются.
- Платежи, production-данные и мини-приложение не затрагиваются.
