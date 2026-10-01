# TwitchSignalBot Native Mini App + Plus Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: `superpowers:executing-plans` for the single visible Codex implementation owner. Use a scoped reviewer when available, with no concurrent writers. Steps use checkboxes; an unchecked item is not completed work.

**Goal:** Выпустить на testbot единый нативно ощущающийся Mini App с работающими Free/Viewer Plus/Streamer Plus, тестовым жизненным циклом подписки и отдельными category-change уведомлениями.
**Architecture:** Переиспользовать Python/aiohttp, SQLite, существующие auth/services/queue. Новые модули отделяют расчёт возможностей, событийную логику и `/app` UI; старые API/бесплатные сценарии остаются совместимыми.
**Tech Stack:** Текущие Python, aiohttp, aiogram, SQLite/aiosqlite, HTML/CSS/ES modules, pytest и существующий Playwright. Не менять версии и стек без конкретной необходимости и проверки.
**Spec:** `docs/superpowers/specs/2026-10-01-native-mini-app-plus-design.md`.
**State:** Пользователь 01.10.2026 разрешил запуск в новом видимом чате «Мини-апп». Выполненные задачи отмечать только по факту.
**Approved extension:** `mini-app/2026-10-01-scope-update.md` и `mini-app/IMPLEMENTATION-ADDENDUM.md` (T13–T17). Порядок: T1–T6 → T13 → T7–T11 → T14–T17 → T12. T12 всегда последний.

## Global Constraints

- Только `autonomous/twitchsignal-roadmap`, pinned staging и `@TwitchSignalTestbot`. Main/master, production, CigilBot/Media, реальные платежи и публичный запуск не входят в задачу.
- Работать в новом видимом чате «Мини-апп» того же проекта TwitchSignalBot. Проверить отсутствие второго активного исполнителя; не запускать скрытый Codex CLI.
- Пользователь согласовал общий интерфейс и Free/Plus; коммерческие вопросы из spec §5/7 не подменять инженерным решением. Исполнение разрешено текущим сообщением пользователя; дополнительные правила зафиксированы в mini-app scope update.
- `docs/workflows/2026-10-01-mini-app-skills.md` задаёт скиллы и разрешение конфликтов. Не выполнять helper-скрипты, не установленные в локальной справочной поставке.
- Существующие бесплатные функции сохраняются. Новый доступ применяется на сервере, не только в UI. Режим «Стример» и Telegram Premium не равны Streamer Plus.
- Превью: H.264 MP4 без звука, Telegram Animation, 6→12→18→24→6, существующий бюджет размера и качество. Не возвращать 30 секунд.
- Category: stable 60 s / минимум 2 наблюдения, отдельный сигнал не чаще 300 s одному зрителю об одном эфире. Новая категория при первом наблюдении — baseline, не событие.
- InitData: существующий проверяющий код, свежесть максимум 600 s; тестовые mocks только в изолированной fixture. Повторные поля, чужие объекты и поддельные данные отклоняются.
- UI: 360/390/768/1440 px, оба режима и темы, 44×44 CSS px минимум, поля от 16 px. Отдельный `docs/design/mini-app.md`; root DESIGN.md относится к админке.
- Outbound tests, реальные OAuth/чат-публикации только после отдельного разрешения. По умолчанию fake sender и отдельная временная DB. Не запускать scripts с внешними отправками просто потому, что в названии есть staging.

## Review Focus

1. Истечение/возврат Plus между построением job и отправкой: проверяется в T2/T8/T10, Free остаётся работоспособным.
2. Один пользователь с двумя режимами и несколькими субъектами: T2/T3/T9/T11 исключают выдачу не тому broadcaster/chat и доступ чужого пользователя.
3. Reconnect, пустая категория, переставленные/повторные наблюдения и неопределённый ответ Telegram: T7/T8 исключают известные дубли, не обещают exactly-once.
4. Возврат из выбора чата, клавиатура и закрытие экрана оплаты: T4/T9/T11 сохраняют контекст/ввод и не считают callback разрешением.
5. Исторические Free-настройки и старый billing ledger: T1/T3/T5/T10 проверяют миграцию и отсутствие скрытого отъёма функций.

## Карта файлов и контрактов

Существующие: `bot/database.py`, `billing.py`, `billing_models.py`, `billing_provider.py`, `telegram_identity.py`, `streamer_auth.py`, `streamer_community.py`, `viewer_filter.py`, `viewer_web.py`, `streamer_web.py`, `poller.py`, `notification_queue.py`, `notification_worker.py`, `live_post.py`, `live_preview_provider.py`, `oauth.py`, `handlers/streams.py`, `main.py`.
Новые небольшие модули: `bot/plan_catalog.py` (серверные определения), `bot/capabilities.py` (доступ), `bot/mini_app_web.py` (auth/bootstrap), `bot/mini_app_viewer.py` / `mini_app_streamer.py` / `mini_app_billing.py` (границы маршрутов), `bot/category_alerts.py` (чистые правила), `bot/category_alert_store.py` (устойчивое состояние).
Frontend: `bot/mini_app_ui/index.html`, `app.css`, `app.js`, `telegram.js`, `router.js`, `api.js`, `components.js`, `viewer.js`, `streamer.js`, `subscription.js`. Не собирать весь продукт в один огромный JS-файл. Имена новых файлов — контракт плана; старые файлы не переименовывать попутно.
DB-изменения добавлять отдельными миграциями с версиями и сохранением текущей последовательности. Не переписывать 4-тысячную строковую Database целиком ради новых модулей.

## T1. Зафиксировать фактическую основу и список Free

**Files:** spec/plan, `docs/STATUS.md`, `docs/DECISIONS.md`, `docs/audits/2026-10-01-mini-app-free-baseline.md`, `docs/design/mini-app.md`.
**Produces:** таблица существующих Free-прав по реальным handlers/DB и карта изменяемых routes; проверенные пути скиллов.
- [x] Прочитать AGENTS, текущий STATUS, spec, план и skills workflow; проверить git status/HEAD и незавершённые процессы. Исторический HEAD `69a980b` не считать текущим без проверки.
- [x] Найти все существующие бесплатные switches, quiet-hours/digest, ограничения подписок/сообществ и media paths; записать, какие нужно сохранить. Использовать согласованные личные лимиты 50/200 и 5 выбранных видео; других тарифных чисел не вводить.
- [x] Сверить SHA256 файлов навыков с lock; прочитать нужные инструкции и записать отсутствующие навыки как блокер только соответствующей задачи.
- [x] Зафиксировать отдельный светлый surface brief по spec, не менять сайт/admin. Локальный макет/генерация для визуального выбора допустимы, но не являются работающим UI.
- [x] Выполнить `git diff --check`; сохранить один подготовительный commit только своих docs/skills после проверки отсутствия чужих staged изменений. Ничего не push.

T1 verification note: `git diff --check` для собственных изменённых документов и `git diff --cached --check -- AGENTS.md docs mini-app` прошли. Полный staged check нашёл шесть пробелов в неизменённых upstream reference-файлах двух закреплённых security skills; сохраняем их байты и SHA256 manifest, не переписываем third-party поставку ради whitespace.

## T2. Единый расчёт прав

**Files:** new `bot/plan_catalog.py`, `bot/capabilities.py`, `tests/test_mini_app_capabilities.py`; affected existing access helpers.
**Interfaces:** `CapabilityService(db)`; `async for_user(telegram_user_id: int, *, now: float) -> UserCapabilities`; `async for_placement(broadcaster_id: str, chat_id: int, *, now: float) -> PlacementCapabilities`. Result types frozen, имена возможностей: `viewer_filters`, `viewer_category_alerts`, `viewer_video_slots` (5 для Plus/0 Free), `viewer_channel_limit` (200/50), `streamer_preview`, `streamer_custom_post`, `streamer_post_stats`.
- [x] Написать `test_free_keeps_basic_notifications`, `test_viewer_and_streamer_products_are_independent`, `test_placement_cannot_borrow_other_broadcaster_plus`, `test_expiry_is_effective_without_ui_refresh`, `test_backend_access_failure_does_not_enable_plus` с соответствующими true/false asserts.
- [x] Запустить `.venv\Scripts\python.exe -m pytest tests/test_mini_app_capabilities.py -q`; получить ожидаемый RED отсутствующего контракта.
- [x] Реализовать слой над существующими grants. Тестовый каталог не содержит выдуманных реальных цен; viewer и streamer subjects вычисляются сервером. Базовые функции не зависят от успешной загрузки платного экрана.
- [x] Повторить этот тест и `tests/test_viewer_access.py tests/test_streamer_access.py`; требуется PASS без ослабления тестов.
- [x] Проверить diff на клиентское/межканальное повышение прав, сохранить commit `feat: centralize mini app capabilities` и checkpoint.

## T3. Полный тестовый цикл подписки для обоих продуктов

**Files:** `bot/billing.py`, `billing_models.py`, `database.py`, `plan_catalog.py`; new `tests/test_billing_viewer_product.py`; existing `test_billing_orders.py`, `test_billing_lifecycle.py`, `test_billing_provider.py`.
**Interfaces:** расширить `BillingService.create_checkout(telegram_user_id, request_key, duration_seconds, *, plan='streamer_plus', now=None) -> CheckoutSession`, сохранив старый вызов. `plan` только из allowlist. DB-слой получает проверенный `BillingSubject(kind: Literal['viewer','streamer'], subject_id: str)`.
- [x] Написать RED: viewer без Twitch получает только viewer grant; streamer требует подтверждённую связь; callback/неверная подпись ничего не активирует; replay создаёт один grant; refund не отзывает соседнюю действующую покупку.
- [x] Написать RED миграции копии старой R5 DB: orders/payment/audit/сроки сохранены, старые Streamer-заказы читаются и возвращаются; viewer не получает фиктивный broadcaster ID.
- [x] Запустить `.venv\Scripts\python.exe -m pytest tests/test_billing_viewer_product.py tests/test_billing_orders.py tests/test_billing_lifecycle.py -q` и подтвердить ожидаемые причины RED.
- [x] Внести типизированного субъекта в ledger и модели; nullable legacy broadcaster допускается только для viewer. При SQLite rebuild сохранить индексы/ограничения/данные в транзакции. Обновить явные SELECT и mapping, не полагаться на прежний `SELECT *`/позиционный tuple.
- [x] Прогнать все `tests/test_billing_*.py` через явно перечисленные пути/pytest discovery, restore/integrity на temp DB и scope review. Сохранить commit. Никаких реальных invoice/capture и открытого webhook.

## T4. Оболочка приложения и Telegram-поведение

**Files:** new `mini_app_web.py`, `mini_app_ui/{index.html,app.css,app.js,telegram.js,router.js,api.js,components.js}`; `oauth.py`, `config.py`, `handlers/streams.py`, `main.py`; new `tests/test_mini_app_auth.py`, `tests/test_mini_app_shell.py`, browser fixture.
**Interfaces:** `install_mini_app_routes(app, db, bot_token, *, bot, capability_service)`; `/app` public shell, POST `/app/api/bootstrap` with existing verified init-data convention returns own profile/capabilities only. `telegram.js` owns one set of SDK handlers; router owns mode/tab/back stack/scroll.
- [x] Написать RED auth: missing/tampered/expired/duplicate fields 401/403, чужой ID не выбирает данные, /admin остаётся изолированным, production flags не монтируют /app.
- [x] Добавить browser scenario: оба режима/три вкладки, BackButton с одним handler после 20 переходов, восстановление прокрутки, отсутствие mock JS на рабочем маршруте. Browser-сценарий создан после auth RED и до финального shell review; отдельный browser RED не зафиксирован.
- [x] Запустить `.venv\Scripts\python.exe -m pytest tests/test_mini_app_auth.py tests/test_mini_app_shell.py tests/test_admin_entry.py tests/test_admin_telegram_auth.py -q` и связанные viewer/streamer тесты.
- [ ] Реализовать shell и адаптер SDK по spec §9. Старые /viewer и /streamer не удалять. На истекшем входе восстановить безопасный черновик после переоткрытия, не увеличивать max-age. Shell/SDK и сохранение навигации готовы; восстановление черновиков форм зависит от T5/T9 и остаётся открытым.
- [x] Прогнать focused/browser checks; сохранить фактические скриншоты в `docs/design/mini-app-qa/`. Commit. Кнопки Main Mini App/BotFather на настоящем клиенте ещё требуют проверки.

## T5. Зрительский Free-сценарий от начала до конца

**Files:** new `mini_app_viewer.py`, UI `viewer.js`; `database.py`, существующий Twitch lookup; new `tests/test_mini_app_viewer.py`, browser journeys.
**Interfaces:** POST `/app/api/viewer/state`, `/search`, `/follow`, `/unfollow`, `/notify`; server derives Telegram ID. Переиспользовать `add_channel_with_limit`, `remove_channel`, `list_channels_with_notify`, `set_notify_enabled` и текущие проверки Twitch login.
- [x] Написать RED: ник и ссылка приводят к одному login; duplicate follow не плодит записи; чужие подписки недоступны; notify off не удаляет; unfollow очищает привязанные rules; Free работает без Plus.
- [x] Проверить bot→app/app→bot через одну DB; браузерный RED показал отсутствовавшую форму поиска, затем отказ сети оставил ввод, а ошибка сохранения откатила toggle.
- [x] Запустить `.venv\Scripts\python.exe -m pytest tests/test_mini_app_viewer.py tests/test_viewer_web.py -q` и связанные auth/access тесты; выполнить browser journey.
- [x] Реализовать поиск с ограничением частоты и отменой устаревшего ответа, страницу стримера, главную live/empty/stale, понятную паузу/удаление. Сервер принимает только Twitch login/проверенную ссылку, не выполняет запрос произвольного URL.
- [x] Проверить Free baseline и долгие русские имена; PASS, commit и скриншоты. Это первая законченная пользовательская контрольная точка; профиль будет дополнен общими настройками в T6.

## T6. Viewer Plus-фильтры и настройки без регрессии Free

**Files:** UI `viewer.js`, `mini_app_viewer.py`, `viewer_filter.py`; new `tests/test_mini_app_viewer_plus.py`; existing filter/delivery tests.
**Interfaces:** POST `/app/api/viewer/filter` retains expected_version; game/keyword/exclude contracts из R7 сохраняются, новый удобный UI не меняет семантику без теста.
- [x] Написать RED: Free не сохраняет Plus-правило прямым HTTP; Plus сохраняет; version conflict 409; expiry делает правило неактивным, но не удаляет; исходные quiet-hours/digest Free-права сохранены.
- [x] Запустить `.venv\Scripts\python.exe -m pytest tests/test_mini_app_viewer_plus.py tests/test_viewer_filter.py tests/test_viewer_delivery.py -q` и связанные web/auth тесты.
- [x] Реализовать выбор значений и объяснение правила обычным предложением; max 5 элементов и длины текущей валидации сохранены. Каждое значение добавляется отдельно, без списка через запятую.
- [x] Прогнать права в UI/API и queued recheck; закрытие Plus-экрана сохраняет контекст. PASS и commit. Native Telegram остаётся NOT TESTED.

## T7. Устойчивый детектор смены категории

**Files:** new `category_alerts.py`, `category_alert_store.py`, `tests/test_category_alerts.py`; `database.py`, `poller.py` в узком месте наблюдения.
**Interfaces:** `CategoryObservation(broadcaster_id: str, logical_stream_id: str, category_id: str|None, category_name: str|None, observed_at: float, is_live: bool)`; `async observe(observation: CategoryObservation) -> CategoryTransition|None` возвращает только зафиксированный уникальный переход.
- [ ] Написать RED: первая A → None; A→B при 59 s → None; B подтверждается на >=60 s и >=2 наблюдениях → один transition; A→B→A до стабильности → None; пустой ID/только title/offline → None.
- [ ] Написать RED: duplicate observation, out-of-order timestamp, rollback DB, restart и открытие новой сессии эфира не создают дублей. Пробел больше `max(120 s, 3*configured_poll_interval)` переустанавливает baseline без catch-up-события.
- [ ] Запустить `.venv\Scripts\python.exe -m pytest tests/test_category_alerts.py -q` и увидеть RED ожидаемых отсутствующих функций.
- [ ] Реализовать чистую логику + persistent baseline/candidate/sequence в отдельной versioned миграции. Использовать текущие успешные stream observations; не добавлять новые EventSub регистрации ради этого этапа.
- [ ] Проверить fake-clock/restore/integrity и существующие shared observation tests; PASS и commit. На этом этапе реальных отправок нет.

## T8. Доставка category-сигналов только Viewer Plus

**Files:** `category_alert_store.py`, `notification_queue.py`, `poller.py` и фактический handler отправки; UI `viewer.js`; new `tests/test_category_alert_delivery.py`.
**Interfaces:** job kind `viewer_category_change`, unique key `(verified viewer ID, transition ID)`. `async enqueue_for_transition(transition_id: str, *, now: float) -> int`; конечный handler возвращает существующий `NotificationOutcome` и использует текущий rate budget.
- [ ] Написать RED: Free получает 0 category jobs, Plus opt-in получает 1; viewer toggle off/notify off/exclusion/quiet-hours запрещают; новый go-live не дублируется category job.
- [ ] Написать RED fake-clock: в 299 s следующий category send запрещён, после 300 s возможна только актуальная последняя смена; старое событие после offline/revoke/unfollow не отправляется; repeat enqueue/retry/restart не дублирует известный успех.
- [ ] Запустить `.venv\Scripts\python.exe -m pytest tests/test_category_alert_delivery.py tests/test_notification_queue.py tests/test_notification_worker.py tests/test_viewer_delivery.py -q`.
- [ ] Реализовать атомарные transition/enqueue и fenced dispatch с повторной проверкой доступа/текущей категории. UI показывает отдельный выключенный по умолчанию toggle и «любая / выбранные категории».
- [ ] Проверить отказы между enqueue/send, таймаут после потенциального send и честный статус неизвестного результата; нет новых бесконтрольных внешних retries. PASS, commit, временная DB с fake sender.

## T9. Нативное подключение стримера и бесплатного сообщества

**Files:** new `mini_app_streamer.py`, UI `streamer.js`; existing `streamer_auth.py`, `streamer_community.py`, `streamer_web.py`, handlers; new `tests/test_mini_app_streamer_connect.py`.
**Interfaces:** `/app/api/streamer/profile`, `/connect-intent`, `/community-intent`, `/community-intent/status`, `/communities`; single-use intent belongs to verified user, expires after 600 s. Return verified identity/placement only after server completion.
- [ ] Написать RED: Free может подключить стандартное размещение; выбор чужого chat ID/чужой intent/просроченный intent отклоняется; callback success без свежей проверки прав не подключает.
- [ ] Написать RED SDK paths: 9.6+ `requestChat` получает серверный prepared request; старый клиент предлагает безопасный переход к reply-keyboard выбора чата; пользовательская отмена не оставляет ложное connected.
- [ ] Запустить `.venv\Scripts\python.exe -m pytest tests/test_mini_app_streamer_connect.py tests/test_streamer_communities.py tests/test_streamer_access.py -q`.
- [ ] Реализовать связку через текущий Twitch OAuth и membership-checks. Basic onboarding не требует Plus. Подключение не отправляет тестовый пост само по себе.
- [ ] Прогнать возврат в Mini App, потерю прав и истечение входа; PASS/commit. Реальный owner OAuth/Telegram-путь оставить NOT TESTED до разрешения.

## T10. Оформление, preview и статистика Streamer Plus

**Files:** `mini_app_streamer.py`, UI `streamer.js`; `streamer_template.py`, `streamer_post.py`, `live_post.py` и точка выдачи preview; new `tests/test_mini_app_streamer_plus.py`; existing preview/template tests.
**Interfaces:** `/app/api/streamer/template`, `/post-example`, `/stats`; template uses existing version and verified broadcaster/chat. Preview provider сохраняет свой API, доступ контролируется у потребителя и до dispatch.
- [ ] Написать RED: Free имеет стандартный пост без нового custom/animation; Plus видит/применяет свой шаблон; чужой broadcaster/placement не использует чужой Plus; plain default refresh работает у Free.
- [ ] Написать RED: expiry между render и send запрещает новую анимацию/кастом, сохраняет данные шаблона и Free delivery. Статистика — подтверждённые публикации, не Telegram просмотры; локальный пример не отправляет сообщения.
- [ ] Запустить `.venv\Scripts\python.exe -m pytest tests/test_mini_app_streamer_plus.py tests/test_streamer_template.py tests/test_streamer_stats.py tests/test_live_post_media_lifecycle.py tests/test_live_preview_provider.py -q`.
- [ ] Реализовать inline example+редактор, текущие ограничения safe HTML/HTTPS/buttons/UTF-16, права перед применением и отправкой. Не менять pipeline 24 s и не создавать массовые delete/repost при downgrade.
- [ ] Проверить generic Free → Plus → expired → Plus на fake sender. PASS/commit и screenshots двух визуальных состояний.

## T11. Подписка и управление тестовым доступом внутри Mini App

**Files:** new `mini_app_billing.py`, UI `subscription.js`; T2/T3 contracts; new `tests/test_mini_app_subscription.py`.
**Interfaces:** own `/app/api/subscription/state` and own order history; simulated checkout only for pinned staging and authorized tester. Ни один UI endpoint не принимает доверенный grant/price/subject от клиента.
- [ ] Написать RED: Free/Viewer/Streamer/оба продукта отображаются раздельно; viewer без Twitch допустим; wrong-user order 403; тестовая активация по клиентскому callback запрещена.
- [ ] Написать RED: pending/cancel/error/verified-active/expired/refunded; закрытие экрана возвращает к действию, не теряет ввод; серверный grant открывает возможность без перезапуска приложения; неизвестный ответ не выдаётся за успех.
- [ ] Запустить `.venv\Scripts\python.exe -m pytest tests/test_mini_app_subscription.py tests/test_billing_viewer_product.py tests/test_billing_lifecycle.py -q`.
- [ ] Реализовать компактный экран одной возможности и «Профиль → Подписка». Показать тестовый источник/срок и «Деньги не списываются». Не рисовать реальную цену, скидку, карточный checkout и работающий switch автопродления.
- [ ] Прогнать оба продукта вместе и независимый refund, stop-slop для подписей; PASS/commit. Основные экраны не превращать в рекламу Plus.

## T12. Итоговая приёмка и guarded staging

**Files:** reusable browser journeys/fixtures, `docs/audits/2026-10-01-mini-app-acceptance.md`, `docs/design/mini-app-qa/*`, STATUS/DECISIONS; existing deployment guard/runbook.
- [ ] Запустить независимый обзор доступного diff по spec: auth/права/доставка/платежи/миграции. Для frontend — web-design-guidelines, для русского текста — stop-slop. Один reviewer за раз; его замечания проверять, а не применять слепо.
- [ ] Выполнить браузерную матрицу spec; сохранить screenshots и результаты с точным tree/окружением. Один общий визуальный обзор → пакет исправлений → подтверждение; функциональные ошибки не закрывать лимитом косметических правок.
- [ ] Запустить `.venv\Scripts\python.exe -m pytest -q` на финальном коде и `git diff --check`. Нужен exit 0; старые 1126 passed не переносятся на новую версию. Не удалять тесты/не добавлять skip ради зелёного результата.
- [ ] Прочитать `scripts/staging_deploy.py --help`, runbook и pinned target; сделать внешний staging backup, restore/migration на копии. Убедиться, что hooks/scripts не отправляют внешние сообщения. Deploy только проверенного commit через существующий guard и с явным staging target.
- [ ] Независимо проверить active deployment/SHA, testbot identity, routes, Free/Plus API и отсутствие admin leak. Реальный Telegram client/контролируемое live-событие провести только с согласованным аккаунтом и получателем.
- [ ] Записать выполненные T1–T12, commit/tree, локальные и staging доказательства, entry point, screenshots, тестовые ограничения и недоступные native checks. Без подтверждённого account E2E статус «инженерно проверено; native E2E ожидает», не «готово без ограничений».

## Итог передачи

Результат — работающий testbot Mini App, не только макет. Codex не добавляет новые продукты/лимиты/провайдера вместо незакрытого решения владельца. Когда блокер внешний, завершает независимые задачи и указывает, какая проверка не выполнена. Production не выпускает.
