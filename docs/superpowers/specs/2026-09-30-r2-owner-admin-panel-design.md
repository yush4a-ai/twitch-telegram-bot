# R2 — Owner Admin Panel v1: design

Дата: 2026-09-30. Scope: Railway staging и локальный тестовый контур. Основание: `docs/ROADMAP.md`, `docs/workflows/2026-09-30-autonomy-design-testing.md`, R0 audit, существующие `bot/oauth.py`, `bot/database.py`, `bot/poller.py` и `/health`.

## Цель и граница

Владелец открывает отдельную адаптивную браузерную панель и видит, какие части бота работают, сколько людей и сообществ подключено, какие Twitch-каналы сейчас в эфире, что происходит с preview, очередями, ошибками и ресурсами. Первая версия только читает. `@TwitchSignalTestbot` и Railway staging — единственный удалённый контур испытания. Production, реальные пользователи и платёжные действия не меняются.

## Один проверяемый сценарий

Владелец открывает `/admin`, входит с отдельным staging access key, видит dashboard и обновляет его. Без правильного ключа HTML-страница панели и JSON недоступны. После выхода сессия недействительна. Пустая БД, отключённый preview и временная ошибка чтения имеют самостоятельные сообщения, а не фиктивные нули или `OK`.

## Доступ

- Панель включена только при явно заданном `ADMIN_PANEL_ACCESS_KEY` длиной не менее 32 символов и только в локальном окружении или Railway `staging`. В остальных Railway environment маршруты `/admin*` возвращают 404. Секрет никогда не выводится в логи, HTML, JSON и тестовые отчёты.
- `POST /admin/login` сравнивает ключ через `hmac.compare_digest` и ограничивает попытки по IP. Успех создаёт случайную серверную сессию на 12 часов; cookie `HttpOnly`, `Secure` на HTTPS, `SameSite=Strict`, путь `/admin`. Сессии теряются при перезапуске. Ключ передаётся в body, не в URL.
- Все маршруты данных и статических ресурсов панели требуют серверной сессии. Неавторизованный `/admin/api/snapshot` отвечает 401 JSON без данных. `POST /admin/logout` удаляет сессию. `GET /admin` до входа показывает только форму без операционных данных.
- Ответы панели имеют `Cache-Control: no-store`, CSP, `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `X-Frame-Options: DENY`. Cookie/сессия не дают доступ к Twitch OAuth callback.
- На staging отдельный случайно созданный ключ задаётся только как staging variable после локального gate. Если ключ не удаётся установить безопасно, deployed auth отмечается непроверенным, но локальные fixture и отрицательный staging smoke обязательны.

## Данные и смысл показателей

- Снимок API содержит `generated_at`, `environment=staging|local`, `telegram`, `twitch`, `preview`, `audience`, `live`, `queues`, `errors`, `resources`. Не включает chat ID, Twitch/Telegram токены, OAuth state, персональные имена пользователей или пути на диске.
- Telegram: активна ли polling-задача процесса. `delivery_verified` остаётся `unknown` без отдельного исходящего E2E. Состояние polling не объявляет доказанную доставку.
- Twitch: свежесть успешного poll cycle, длительность и ошибки; EventSub running и ready/configured, состояние авторизации. `ok`, `degraded`, `unknown` вычисляются по наблюдаемым полям. `healthz` не используется вместо проверки preview.
- Preview: enabled/manager_running, active sessions/jobs, возраст наблюдения/успеха, класс последней ошибки и причина отключения. Отключённый preview помечен `disabled`, отсутствие manager — `unknown`; не подменять ни одно из них `healthy`.
- Аудитория: существующие агрегаты `get_bot_stats()` с подписями «личные пользователи», «группы», «подписки чат+канал», «уникальные Twitch-каналы». Это записи staging, не production reach.
- Live: до 20 уникальных Twitch-каналов из текущих `tracked_channels.is_live`, с количеством назначений, последним viewer count и временем наблюдения. Пустая выборка — «Сейчас нет зафиксированных эфиров». Имена экранируются клиентом; SQL параметризован.
- Очереди: pending deliveries, deferred reports, возраст старейшего элемента из `Database.health_snapshot()`. R2 не называет их универсальной durable fan-out queue; её проектирует R3.
- Ресурсы: CPU **этого процесса** (дельта времени с предыдущим снимком), текущая RAM процесса на Linux или `unknown`, свободное/общее место тома DB и размер DB/WAL. Это не CPU/RAM всего хоста. Неизмеримое поле — `null`.
- Ошибки: только безопасные классы ошибок poller/EventSub/preview и отметка ошибки получения dashboard snapshot. Полный traceback и сырые API ответы не выдаются. Сбор DB статистики и health имеет ограничение времени; ошибка одного DB блока не скрывает in-memory health.

## UI и поведение

- Язык — русский; вид — рабочий dark dashboard в палитре существующего аватара и баннера: тёмный сине-фиолетовый, живой фиолетовый акцент, красный только для live/ошибки. Отдельный `DESIGN.md` фиксирует цвета, шрифты, типографику, состояния и визуальное решение перед UI кодом.
- Навигация по одному экрану: верхняя строка с окружением и временем обновления; три чётких блока Telegram/Twitch/Preview; ниже аудитория, live-таблица, очереди/ошибки/ресурсы. На узком экране таблица становится списком, а не горизонтальной прокруткой всего документа.
- Обновление при открытии и каждые 30 секунд, явная кнопка «Обновить». При ожидании — `Загрузка…`, при сетевой ошибке — сообщение с повтором, старый успешный снимок после 90 секунд явно `Данные устарели`. Отсутствующие метрики показывают «Нет данных», а не 0.
- Breakpoints 360, 390, 768, 1440 px; фокус с клавиатуры, видимые labels, контраст текста, `prefers-reduced-motion`, light/dark системная тема, длинные русские названия, без горизонтального переполнения. Никаких управляющих кнопок ботом в R2.

## Архитектура

- Использовать существующий aiohttp `OAuthCallbackServer` как единственный HTTP listener; вынести админ-маршруты, авторизацию и сбор dashboard snapshot в отдельные компактные модули. `/healthz` остаётся быстрым in-memory readiness и не вызывает SQLite.
- `main.py` связывает уже созданные runtime объекты с read-only snapshot provider и отмечает Telegram polling task; до готовности runtime API показывает `unknown`, не стартует дополнительный poller. Сбор snapshot не обращается к Telegram/Twitch API и не меняет DB.
- UI — локальные HTML/CSS/JS без CDN и новых runtime зависимостей. Статические файлы выдаются точными routes после auth. Общие визуальные tokens пригодны для R7, но Mini App не смешивается с owner auth.

## Проверка и критерии приёмки

1. TDD: auth отказ/успех/истечение/logout/rate limit, staging-only gating, secret leakage, security headers; снимки для healthy/degraded/disabled/unknown/ошибки DB; live query с дублирующимися назначениями и пустой БД.
2. Полный pytest suite на точном commit перед staging deploy, review diff и security/UX review.
3. Локальный browser journey: вход, обновление, выход, отказ, пустое/ошибочное/устаревшее состояние; скриншоты 360/390/768/1440, dark/light, отсутствие переполнения и keyboard focus.
4. Staging deploy только через проверенный guard R1, затем новый deployment ID/SHA, `getMe=TwitchSignalTestbot`, `/healthz`, 401 без cookie, успешный login с staging-only key без вывода key, API и браузерный smoke. Если live данные отсутствуют, это честное пустое состояние.
5. `docs/STATUS.md` и `docs/DECISIONS.md` фиксируют факты и непроверенное: Telegram outbound E2E, preview capture при отсутствии тестового live и native iOS/Android UI.

## Самопроверка спецификации

Нет новых production или денежных действий. Read-only UI не обещает Telegram delivery и preview success без наблюдений. Access key — временный staging механизм, дальнейшая owner identity может быть заменена после отдельного проектирования; тестовый ключ не является Twitch или Telegram credential. Все показатели имеют источник и формулировку, соответствующую источнику.
