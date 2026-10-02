# TwitchSignalBot: план переноса редизайна, Plus и оплаты на staging

> **Для исполнителя:** REQUIRED SUB-SKILL: `superpowers:executing-plans`. Один основной исполнитель в этом чате, задачи последовательно; максимум один reviewer без записи файлов. Использовать checkbox для фактически выполненных шагов. План утверждён последним промптом владельца (attachment `650afe6a-f60f-4201-bc2e-938a49c9d84a`): разрешены P01–P21 и guarded staging после всех gates; реальные деньги и production запрещены.

**Цель:** перенести одобренный А «Собранный» с группировкой стримеров из Б в настоящий Mini App, сохранить работающие функции, внедрить каталог и наследование Plus, подготовить прозрачную покупку и legal/support, выпустить только на `@TwitchSignalTestbot` для проверки владельцем.

**Архитектура:** сохраняем aiogram/aiohttp, HTML/CSS/JavaScript ES modules и одну существующую SQLite. Расширяем текущий PaymentProvider → BillingService → orders/events/grants; новый интерфейс использует существующие `/app/api/*` и общий каталог сервера. Платёжные адаптеры проверяем локально с подставным transport; первый staging release запрещает внешние платежи и показывает нормальный кликабельный unavailable flow.

**Стек:** Python 3.12.10; aiogram 3.30.0, aiohttp 3.14.3, aiosqlite 0.22.1; существующие pinned requirements и FFmpeg, pytest/unittest; уже установленный Playwright Chromium/WebKit. Expo/React Native и новый frontend framework не вводим.

**Spec:** [2026-10-02-plus-platega-payment-design.md](../specs/2026-10-02-plus-platega-payment-design.md). Владелец одобрил его и визуальную концепцию сообщением из attachment `ce26fd15-29eb-47ba-911d-45e9236d2c39` от 02.10.2026. Последний промпт владельца отдельно утвердил выполнение P01–P21 и staging; текущая цена Streamer300 ₽ имеет приоритет. Старые отметки «spec/дизайн не утверждены» относятся к предыдущим checkpoints.

**Последнее утверждённое дополнение:** Streamer Plus300 ₽, role-aware Plus без основного внутреннего переключателя тарифов, четыре главных benefit blocks с системными SVG, «Все возможности», вторичная ссылка Viewer у стримера и честная активная подписка. Эти поправки учтены в P03/P15/P18/P21. Четвёртый `Plus` и общий Subscription из меню/профиля сохраняются. Отдельное повторное утверждение не требуется.

## 1. Зафиксированная основа и приоритет

- Папка `C:\Users\yusha\Desktop\cloude\TG-BOT.(TwtichSignal)`, ветка `autonomous/twitchsignal-roadmap`, HEAD `e0d44c315a1c81dc5b310dd1f3c7f3169915c985`; staged пуст. Незакоммиченные документы/навыки/макеты сохраняются.
- Исторический одобренный spec SHA256: `94975d454af9db6404ce4bc208e528093d9ebaa164dae39b0824969383157668`; после обязательных поправок последнего промпта SHA256: `30a5c47d977248497b261919cc463d03907078c858f79ee6026778f8423e697e`. Этот план опирается на него и последнее сообщение владельца, а не на отменённую схему отдельного сайта.
- Перечитаны AGENTS, spec, Plus/Platega handoff, README, DESIGN-SELECTED, THEMES-PLANS-SCOPE, CHANNEL-PERMISSIONS-CONTRACT, PLATEGA-BANK-APPROVAL, API research, DECISIONS и STATUS; проверены затронутые продуктовые маршруты, UI, billing, SQL gates, OAuth, reports, staging guard. 47/47 файлов redesign skills-lock совпали.
- R0–R9 и T1–T17 сохраняем. Исторический staging: `273db13` / deployment `9039cde2-08a9-4c30-b36e-232ad70e7410`; текущую Railway identity на этапе планирования не запрашивали. Перед новым deploy её нужно получить заново.
- `bank-review.html`, selected/preview HTML, старые QA и archives сохраняются. Они подтверждают соответствующие прототипы, но не новый продукт. Bank review ещё показывает прежнюю схему; его копирование целиком запрещено.
- Приоритет: последнее решение владельца → одобренный spec → этот утверждённый план → актуальный redesign workflow. Старые предложения в design/legal/research о Platega только на сайте и независимых Viewer/Streamer правах больше не требования.

## 2. Общие ограничения

- Только локально и pinned Railway staging / `@TwitchSignalTestbot`. Production, main/master push/merge, production DB/variables/secrets и CigilBot/Media не трогаем. Новый чат/контроллер/вложенный git/копия бота/второй ledger не нужны.
- Сохраняем текущие модель и усилие. Не меняем глобальные skills, runtime, инфраструктуру, replica/Volume или платные услуги.
- Free: 50 личных стримеров, фото, базовый go-live, quiet hours и прежние подтверждённые бесплатные функции. Plus: 200 личных стримеров и 5 выбранных видео, включая offline; шестой выбор запрещает сервер, замена атомарная. Групповой лимит не расширяем.
- Viewer Plus: 150 ₽ / 1 месяц. Streamer Plus: 300 ₽ / 1 месяц, все Viewer-возможности тому же frozen Telegram beneficiary плюс оформление собственных подтверждённых размещений. Участники канала личный Plus не получают.
- Один месяц ещё не означает 30 суток. Каталог отображает `one_month`, `period_rule=unapproved`, `auto_renew=false`; XTR отдельно не назначен. Эти пробелы запрещают денежную активацию, но не UI, каталог, unavailable flow или staging.
- Внутри бота / Mini App: тариф → CTA → Stars / СБП / банковская карта. Stars — Telegram, СБП/карта — Platega. Все три способа видны, внешний провайдер обозначен. Платформенный риск принят владельцем; маскировка, обходы и скрытие способов от проверяющих запрещены.
- Первый deploy: zero-network payment policy; никаких денежных POST, Stars invoices, fake success/grant, автосписаний или выдуманного order ID. Credentials не включают кассу автоматически. Реальный test API — отдельный разрешённый шаг после доказательства безопасности.
- Пользовательский UI содержит реальные данные и понятные состояния. `demo/mock/prototype/staging/test state/technical fixture` и их русские аналоги не переносятся из макета в обычные экраны. Во внутренних именах, QA, guard и audit технические термины сохраняются. Реальное ознакомление называется «Ознакомление на 7 дней», не оплатой; оно остаётся allowlisted, добровольным и однократным.
- Дизайн А: light при первом входе; own dark `#171717` / `#242424` / `#f1f1f1`; отдельный Telegram mode с настоящими ThemeParams/themeChanged; контент до 600 CSS px, 16 px отступ, системная кириллица, действия ≥44 px, primary ≥48 px. Размеры текста относительные, text200/zoom и короткие окна обязательны.
- Два режима, по четыре пункта нижней навигации по новому решению владельца: Зритель — Главная / Стримеры / Профиль / Plus; Стример — Мой канал / Посты / Профиль / Plus. Короткая подпись ровно `Plus`; оба входа (меню и профиль) открывают один Subscription, а не разные экраны. Отдельные Profile, Subscription и Support; live/offline/stale, полноценные экраны работающих функций. Не копировать рамку телефона, системные панели, сценарные query flags или fake SDK из prototype.
- Новый streamer connection — Telegram channel only. Legacy groups сохраняются и работают; подключение Free. Выбор канала, OAuth callback или оплата не включают публикации автоматически.
- Видео: H.264 MP4 без звука как Telegram Animation, 6 → 12 → 18 → 24 → 6, прежнее качество/size guard, shared capture/render и per-bot file_id. Общий cap и честный photo fallback сохраняем; личные 5 слотов не доказывают серверный SLA.
- HTML reports/export и старые команды сохраняем. Новый первый вход и платность расширенных отчётов не вводим. Raid/name-change/spikes не рекламируем как готовые Plus; collabs/служебная недоступность Twitch и quiet hours не получают новый paywall.
- Реальные Telegram send/delete/edit/invoice и OAuth от имени владельца требуют отдельного разрешения конкретного получателя/аккаунта. Независимые задачи продолжаются с fake sender и временной DB. Native PASS записываем только после реального сценария.
- TDD: RED с причиной → минимальный код → focused PASS → scoped review → checkpoint/commit. Не ослаблять assertions/skip или молча заменять baseline. Операционные backup/deploy/owner acceptance проверяем по результатам, не создаём искусственный RED для документации.
- Навыки: `mobile-app-ui-design` главный; `design-system`, Impeccable Operate как review, Playwright, Stop-Slop. Apple/четыре Expo — адаптированные references. Не использовать ui-ux-pro-max. Перед применением читать выбранный SKILL.md; helpers запускать только существующие и проверенные.

## 3. Что проверяющий должен искать

| Риск за пределами обычного happy path | Ожидаемое поведение / задача |
|---|---|
| Старый Streamer grant без buyer; Twitch owner сменился, grant в будущем или между источниками есть разрыв | Личный доступ не угадывать; отображать только непрерывный действующий период, независимый Viewer сохраняется. P02/P04 |
| Callback пришёл до ответа POST; ответ потерян; chargeback появился раньше confirmed | Durable событие, отсутствие слепого второго POST и второго месяца; неизвестный факт не выдаёт права. P05/P06 |
| Пользователь меняет режим/аккаунт/экран во время запроса, нажимает Tab или Back | Поздний ответ не перехватывает focus, не показывает чужой черновик и не возвращает отменённый экран. P09/P10/P12/P13 |
| Telegram timeout при проверке канала; bot creator без optional permission fields; group из старого request | Unknown network не становится «нет прав»; creator обрабатывается явно, новая group отклоняется, прежняя остаётся. P13 |
| ThemeParams неполные/неверные, localStorage недоступен, клавиатура уменьшила viewport при text200 | Безопасный контрастный fallback, явная тема сохраняется, основной action/back доступны. P08/P18 |

## 4. Файлы и границы ответственности

Пути ниже относительно корня. **Create** означает будущий файл после утверждения плана, не уже существующую реализацию. Старые большие модули не переписываем целиком.

| Область | Файлы и ответственность |
|---|---|
| Общий каталог | Modify `bot/plan_catalog.py`; цены/period/feature IDs/readiness. Existing helpers `viewer_channel_limit`, `viewer_video_slots` сохраняются |
| Типы и миграция | Modify `bot/billing_models.py`, `bot/database.py`; Create `bot/billing_migrations.py`, `bot/billing_store.py`; операции в той же conn/_write_lock, существующая миграционная transaction |
| Effective access | Create `bot/entitlements.py`; Modify `bot/capabilities.py`, SQL/readers в `bot/database.py`, `bot/category_alert_store.py`, `bot/viewer_history.py`, `bot/viewer_trial.py`; остальные потребители через общий helper |
| Payments | Modify `bot/billing_provider.py`, `bot/billing.py`; Create `bot/platega_provider.py`, `bot/stars_provider.py`, `bot/billing_reconciliation.py`, `bot/payment_web.py`, `bot/handlers/payments.py`; transport и runtime enable policy отделены от ledger |
| Web composition | Modify `bot/mini_app_web.py`, `bot/mini_app_billing.py`, `bot/mini_app_streamer.py`, `bot/mini_app_streamer_plus.py`, `bot/oauth.py`, `main.py`, `bot/handlers/__init__.py`, `bot/config.py`; новые assets добавляются в allowlist и packaging tests |
| Design system / навигация | Modify `bot/mini_app_ui/index.html`, `app.css`, `app.js`, `components.js`, `router.js`, `telegram.js`, `api.js`; Create `theme.js`, `profile.js`, `support.js`, `purchase.js`; existing feature factories/render/refresh сохраняют контракт |
| Viewer / Streamer | Modify `bot/mini_app_ui/viewer.js`, `streamer.js`, `subscription.js`; переносим отдельные render-функции и подключаем реальные services. Prototype selected.js не становится runtime |
| Channel / OAuth | Modify `bot/streamer_community.py`, intents в `bot/database.py`, `bot/handlers/streams.py`, `bot/oauth.py`; Create `bot/oauth_ui/result.html`, `result.css`, `result.js` |
| Legal/support | Create `bot/legal_web.py`, `bot/legal_documents.py`, `bot/legal_ui/`; canonical документы `docs/legal/PRIVACY-POLICY.md`, `USER-AGREEMENT.md`, `SUPPORT.md`, `TARIFFS.md`, `PAYMENTS.md`; checklist/owner inputs в `docs/workflows/` |
| QA / evidence | Extend `scripts/mini_app_browser_fixture.py`; Create `scripts/mini_app_redesign_browser_qa.cjs`, `scripts/mini_app_redesign_staging_smoke.py`; reports/screenshots под `docs/audits/mini-app-redesign-plus-2026-10-02/`; старые QA не удаляются |

### 4.1. Общие типы между задачами

Имена ниже — будущие immutable dataclasses/DTO, не отдельная реализация на этапе планирования. P02 определяет денежные типы в `bot/billing_models.py`, P03 — policy/readiness в `bot/plan_catalog.py`, P04 — effective state в `bot/entitlements.py`, P05–P06 — результаты provider/service. UTC timestamps — `float`; идентификаторы — `str`; optional поля явно допускают `None`. Existing readers/constructors сохраняются через default поля/адаптер, без изменения смысла TEST.

| Тип | Обязательные поля / семантика |
|---|---|
| `Money` | `amount_minor:int`, `currency:Literal['RUB','XTR','TEST']`; только целое положительное число, bool отвергается; XTR — целые Stars |
| `ProductSnapshot` | `product_id`, `catalog_version`, `rub:Money`, `xtr:Money\|None`, `period_code`, `period_rule`, `period_rule_version:str\|None`, `auto_renew:bool`, `includes:tuple[str,...]`, `feature_ids:tuple[str,...]` |
| `ServerOrderSnapshot` | `order_id`, `telegram_user_id:int`, `beneficiary_telegram_user_id:int`, `subject:BillingSubject`, `broadcaster_id:str\|None`, `product:ProductSnapshot`, `money:Money`, `provider`, `method`, `terms_version`, `created_at`, `checkout_expires_at`; значения server-owned и frozen до внешнего POST |
| `PaymentAttempt` | `attempt_id`, `order_id`, `provider`, `method`, `state`, `provider_reference:str\|None`, `created_at`, `next_reconcile_at:float\|None`, `reconcile_count:int`, `lease_until:float\|None`, `payload_digest`; `creation_unknown` принадлежит попытке, не оплаченному order |
| `BillingRuntimePolicy` | `mode:Literal['offline','sandbox']`, `target_verified:bool`, `allow_external_create:bool`, `allow_invoice:bool`, `period_approved:bool`, `refund_policy_approved:bool`; initial offline/false. Sandbox policy доступна лишь после отдельного допуска; локальный fixture внедряет fake transport, а не live-auth bypass |
| `CheckoutReadiness` | `enabled:bool`, `reason_code:str\|None`; неизвестный месяц/XTR/политика/допуск запрещают соответствующую денежную операцию, но не скрывают продукт/способ |
| `CheckoutSession` | сохранить существующие `order_id`, `reference`; новые `hosted_url:str\|None`, `status`, `checkout_expires_at:float\|None` с совместимыми defaults. URL проходит HTTPS/host проверку до передачи клиенту |
| `VerifiedPaymentEvidence` | `provider`, `transaction_id`, `order_id`, `attempt_id`, `money:Money`, `method`, нормализованный `status`, `raw_status`, `observed_at`; создаётся только adapter после строгой проверки критических полей; не из клиентского body |
| `ProviderNotice` | `provider`, `transaction_id`, `raw_status`, `event_key`, `order_hint:str\|None`; authenticated notice — повод для durable записи/сверки, не evidence и не beneficiary |
| `RefundOutcome` | `state:Literal['unsupported','accepted','manual_control_required','completed','declined','unknown']`, `provider_reference:str\|None`; только подтверждённый финальный факт плюс утверждённая policy меняют доступ |
| `CheckoutResult` | `state:Literal['unavailable','pending','creation_unknown','manual_review']`, `order_id:str\|None`, `hosted_url:str\|None`, `reason_code:str\|None`; первый release unavailable/None/None |
| `ApplyResult` | `state:Literal['applied','already_applied','recorded','pending','manual_review']`, `order_id`, `grant_id:str\|None`; `recorded` хранит финансовый факт, не обещает активный доступ |
| `NoticeReceipt` / `ReconcileSummary` | receipt: `durable:bool`, `duplicate:bool`, `event_key`; summary: `attempted:int`, `deferred:int`, `applied:int`, `manual_review:int`. ACK только durable; exhausted попытки остаются в ledger |
| `EffectiveViewerState` | `active:bool`, `sources:tuple[EffectiveAccessSource,...]`, `expires_at:float\|None`; `EffectiveAccessSource(grant_id, product_id, starts_at, expires_at)` содержит действующие источники и только непрерывное продление, без обещания будущего разрыва |

## 5. Mapping: старая функция → новый экран → API/service → тест

Все `/app/api` требуют `verified_payload`; user/chat ownership определяет сервер. Путь без префикса в таблице всё равно относится к `/app/api/`.

| Функция | Новый экран | Текущий API / service | Сохранить / добавить тест |
|---|---|---|---|
| Подписки, live/offline/stale, счётчики | Зритель: Главная/Стримеры, группы | `viewer/state`, `Database.list_personal_channel_status`, stale >300 s | `test_mini_app_viewer.py`, browser P10: 0/6/200/длинные имена |
| Поиск своих / добавление Twitch | Стримеры: поиск списка / отдельное «Добавить» | `viewer/search`, `viewer/follow`, TwitchClient; rate 6/10 s | `test_mini_app_viewer.py`, `test_viewer_plus_limits.py`, P10 |
| Unfollow / pause / активные 50 после downgrade | Детали стримера | `viewer/unfollow`, `viewer/notify`, `viewer/plan-activate`, DB priority/CAS | `test_viewer_plus_limits.py`, P04/P10 |
| Фильтр категорий/слов, исключения, reset | Детали: Уведомления | `viewer/filter`, `viewer/filter/reset`, viewer_filter, DB effective filter | `test_mini_app_viewer_plus.py`, `test_viewer_filter.py`, P12 |
| Нужная категория / смена категории | Детали: Категория | `viewer/category-search`, `viewer/category-alert`, CategoryAlertStore; стабильный detector/cooldown | `test_category_alerts.py`, `test_category_alert_delivery.py`, P04/P12 |
| Напоминание 15/30, отмена | Детали текущего live | `viewer/reminder`, `viewer/reminder/cancel`, ViewerReminderService | `test_viewer_reminders.py`, P12 sending/409/unknown |
| Папки и наследование правил | Стримеры → Папки → правило/перенос | `viewer/folder/create\|rename\|rule\|move\|delete`, ViewerFolderService; личный пустой фильтр старше общего | `test_viewer_folders.py`, P12 |
| История sent/suppressed/unknown | Профиль → Мои данные → История | `viewer/history`, ViewerHistoryService, pagination | `test_viewer_history.py`, P04/P12 |
| 5 видео/offline/замена/статус доставки | Верхнее «Видео N/5» → выбор | `viewer/video-selection`, version/logins; DB resolves broadcaster, PreviewManager/LivePostUpdater | `test_viewer_preview_slots.py`, `test_viewer_video_delivery.py`, P11/P18 |
| Quiet hours, digest, Viewer settings | Профиль → Уведомления | `viewer/quiet-hours`, `viewer/digest`; совместимый `/viewer/api/digest` | `test_mini_app_viewer_plus.py`, `test_viewer_web.py`, `test_mini_app_legacy_compat.py`, P12/P17 |
| Twitch streamer connection | Стример → Мой канал → Подключить Twitch | `streamer/connect-intent`, `/status`, `/cancel`; OAuthCallbackServer verified exchange | `test_mini_app_streamer_connect.py`, P13 |
| Telegram channel / fallback | Мой канал → Подключить Telegram-канал | `streamer/community-intent`, `/status`, `/cancel`; complete_community_intent, prepared requestChat, `tscommunity_` bot fallback | `test_mini_app_streamer_connect.py`, `test_streamer_communities.py`, P13 |
| Free включение/выключение публикаций | Мой канал → Публикации | `streamer/communities/toggle`, verified placement, tracked_channels | `test_mini_app_streamer_connect.py`, P13; 0 send при выборе |
| Legacy group connections | Мой канал: существующие размещения | `streamer/profile\|communities`, прежние group rows/проверки | `test_streamer_communities.py`, P13/P17; новые группы не предлагаются |
| Пример стандартного поста / своё оформление / кнопки / video | Стример → Посты → своё размещение | `streamer/post-example`, `streamer/template`, `streamer/preview`; StreamerTemplateService/poller/live_post | `test_mini_app_streamer_plus.py`, `test_streamer_template.py`, P14 |
| Saved variants | Посты → Варианты → Применить | `streamer/presets`, `/create`, `/apply`, `/delete`; StreamerPresetService/CAS | `test_streamer_presets.py`, P14 |
| Analytics | Посты → Статистика | `streamer/stats`, `streamer/stats/compare`, `streamer_post_events`; подтверждённые публикации 30 d / 7+7 | `test_streamer_stats.py`, P14; 0 fake views/clicks |
| HTML reports/export | Профиль → Мои данные → Итоги эфиров → Отчёты в боте | существующие `/report`, `menu:report`, `report:<login>`, build_report_html/validate_report_destination | `test_regressions.py`, P17; реальный доступ/HTML остаются Free по прежним правилам |
| Twitch follows import | Стримеры → Добавить → Импорт через бот | существующие `/import_follows`, `menu:import_follows`, OAuth/import confirmation; никаких автоматических OAuth/send | `test_regressions.py`, `test_viewer_plus_limits.py`, P17; сохранены выбор/отмена/лимиты |
| Привязка отчётов к личке, управление прежними сообществами | Профиль → Мои данные / Мой канал; ссылка на прежний путь в боте | `menu:link_stats`, `menu:manage_group`, существующие handlers/permissions | `test_regressions.py`, `test_streamer_communities.py`, P17; IDs/доступ/группы сохраняются |
| Старые команды/callbacks | Бот и совместимые `/viewer`, `/streamer` | `bot/handlers/streams.py`, `auth.py`, `viewer_web.py`, `streamer_web.py`; старый togglepreview закрыт | `test_regressions.py`, `test_viewer_preview_slots.py`, `test_mini_app_legacy_compat.py`, P17 |
| Help/invite/myid и owner admin/stats/health | Прежние команды бота, существующая отдельная админка | `/start\|help\|invite\|myid\|track\|untrack\|list\|live\|auth_twitch\|streamer_connect\|stats\|health\|admin`; прежние role/ownership scopes | `test_regressions.py`, `test_mini_app_legacy_compat.py`, P17; админ-права не выдаются по режиму Mini App |
| Текущий план / срок / свои операции / trial | Профиль → Возможности Plus / Моя подписка | `subscription/state`, общий resolver/catalog; `test-trial` только existing allowlist | `test_mini_app_subscription.py`, `test_viewer_trial.py`, P04/P15 |
| Покупка Stars/СБП/карта | Подписка → способ → результат | новые `subscription/catalog`, `purchase/prepare`, `purchase/state`; BillingService | `test_mini_app_purchase.py`, P15; 0 money HTTP/invoice/grant |
| Legal / Support | Профиль → Поддержка → Документы; ссылки у покупки | новые `support/state`, `/app/legal/privacy\|agreement`, config/support/docs version | `test_mini_app_legal.py`, P16 |

Отчёты в этом release открывают существующий путь в боте с понятной инструкцией `/report <login>` и копированием команды; ссылка открывает проверенный bot chat и ничего не отправляет сама. Новый отчётный движок, новая структура/платность отчётов и автоматическая рассылка не входят в план. HTML export в боте остаётся рабочим fallback, пока полноценная замена отдельно не доказана и не согласована.

## 6. Owner inputs и разные границы готовности

Будущий файл: `docs/workflows/OWNER-INPUTS-FOR-LAUNCH.md` (P16). Сейчас значения не заполняем и не публикуем.

| Недостающее решение/данные | Что блокирует | Что продолжается |
|---|---|---|
| 30 дней или calendar month; начало доступа, покупка заранее/активного плана | Настоящая денежная активация и финальные условия покупки | Catalog display «1 месяц», unavailable UI, staging, fixture-тесты с явно заданным QA period |
| XTR-цены | Stars invoice/precheckout денежного пути | Видимый кликабельный Stars с объяснением недоступности |
| Refund/chargeback→доступ, поддержка возвратов | Денежное включение / принятая User Agreement | Локальные adapters/lifecycle fixtures, раздельные finance/access статусы |
| Viewer→Streamer формула / перенос | Только upgrade checkout | Информация о включённом Viewer, обычный Free-путь, unavailable upgrade |
| Реальный индивидуальный support username/email, сведения оператора/юрисдикция, принятые документы и retention | Публикация неполного legal/contact, банковская подача и деньги | Готовые routes/layout; обычное «Поддержка/Документы временно недоступны», без fake реквизитов и owner placeholders |
| Merchant onboarding/category/методы/metadata, бесплатный test mode, договор/НПД чеки | Внешний provider test и банковская подача; не первый UI deploy | Zero-network purchase, локальный fake transport, первое staging принятие |
| API schema: CHARGEBACKED, mechantId, recovery/idempotency; разрешённые hosted domains | Внешний Platega create/confirm/refund | Fail-closed adapters и отрицательные fixtures; вопросы менеджеру через владельца |
| Reviewed binding старых test Streamer grants без buyer | Только новые inherited личные права из этих legacy grants | Прежние placements, Free, новые заказы с доказанным beneficiary, controlled local fixtures |
| Разрешение конкретного OAuth аккаунта/Telegram получателя | Только соответствующий настоящий E2E/send | Fake sender/browser tests и owner self-test package |

**Отдельные отметки:** инженерный staging release может пройти с отключённой кассой; bank approval остаётся NOT READY до полного checklist; внешняя оплата/native сценарий остаётся NOT TESTED до факта. Дизайн/цены/три метода/включение Viewer/отсутствие auto-renew повторно не спрашиваем.

## 7. Общий цикл задач и команды

Запуск Python из корня: `.\.venv\Scripts\python.exe`. Для каждой кодовой задачи шаги ниже фиксируют RED/PASS. Имена тестов и assertions являются требованиями; предусловия создаются в существующем unittest `IsolatedAsyncioTestCase`, без нового async plugin. Все DB-тесты используют отдельную temp DB / :memory:, concurrency — две conn.

После focused PASS: один scoped review файлов задачи/тестов/контракта, исправление найденного дефекта отдельным RED, затем `git diff --check` и отдельный commit только точных файлов задачи. `git add -A` запрещён при смешанном дереве. STATUS/DECISIONS — фактический SHA/tree/evidence/следующий шаг. Если после двух попыток дефект не закрыт, остановить догадки и разобрать причину с одним reviewer.

Перед P01 ещё раз проверить cwd/HEAD/status/staged и отсутствие второго исполнителя. Утверждённые docs/skills закрепить отдельным документационным коммитом после просмотра содержимого/секретов/точного списка; сохранить все материалы. Кодовые коммиты не смешивать с прежними staged изменениями. Перед P19 дерево должно быть чистым: существующий `staging_deploy.validate_git` не ослаблять и незакоммиченные документы не stash/удалять.

Для браузера использовать существующий runtime/runner `C:/Users/yusha/.agents/skills/playwright-skill/run.js` после проверки наличия; NODE_PATH и browser paths брать из уже установленной среды. Не выполнять install/community helpers. Проектный QA runner принимает `MINI_APP_QA_URL`, `MINI_APP_QA_SCREENSHOTS`, scenario/engine и пишет компактный JSON. QA fixture bind только `127.0.0.1`; его служебные routes не попадают в live app.

## P01. Baseline, mapping и повторяемый локальный fixture

**Файлы:** Modify `scripts/mini_app_browser_fixture.py`; Create `tests/test_mini_app_redesign_fixture.py`, `scripts/mini_app_redesign_browser_qa.cjs`; evidence `docs/audits/mini-app-redesign-plus-2026-10-02/BASELINE.md`. Mapping §5 сверить перед переносом.

**Интерфейсы:** сохранить script CLI, добавить `build_fixture(scenario: str) -> tuple[web.Application, Database]` с закрытым перечнем сценариев; QA runner экспортирует `runJourney(page, scenario)` и `assertLayout(page)`. Сценарии: free-empty/free-six/plus-two-hundred/streamer-plus/independent-viewer/legacy-group/channel-permissions/payment-off/legal-unready. Подписи только локальные, server data настоящая временная SQLite.

- [x] RED `test_fixture_supports_required_scenarios_and_never_calls_external_sender`: assert 0/6/200 rows, channel+legacy group, long display names, sources/expiry, payment external calls=0; неизвестный scenario отвергается. Текущий fixture не имеет полного scenario builder.
- [x] Запустить `python -m pytest tests/test_mini_app_redesign_fixture.py -q`; зафиксировать ожидаемый FAIL, не сетевую ошибку.
- [x] Минимально расширить fixture, fake Telegram/Twitch, deterministic clock и сценарий данных. `/_qa/*` остаются только loopback helper, в `install_mini_app_routes` их не добавлять.
- [x] PASS этот файл и existing `tests/test_mini_app_shell.py tests/test_mini_app_auth.py`; исходный браузерный journey на реальном текущем `/app`, а не selected preview. Сохранить исходные screenshots/SHA. R0–R9/full suite сейчас не перезапускать.
- [x] Scoped review mapping/изоляции fixture → точный QA commit/checkpoint. Для documentation-only фиксации не выдумывать RED.

## P02. Схема orders/attempts и frozen beneficiary в одной SQLite

**Файлы:** Modify `bot/billing_models.py`, `bot/database.py` (_migrate, billing/order/grants definitions/readers); Create `bot/billing_migrations.py`, `bot/billing_store.py`, `tests/test_plus_payment_migrations.py`; existing `tests/test_billing_viewer_product.py` сохранить.

**Интерфейсы:** `migrate_plus_payments(conn, *, now: float) -> None` внутри существующей outer transaction; `BillingStore(db)` не открывает новую DB. Типы `Money(amount_minor:int,currency:str)`, `ProductSnapshot`, `PaymentAttempt`, `VerifiedPaymentEvidence`; order хранит frozen buyer/beneficiary, catalog/period/terms versions, method/provider, UTC access start/end, финансовое состояние отдельно от доступа. Начальные версии `r11_001_plus_payment_orders`, `r11_002_plus_payment_events`, `r11_003_entitlement_beneficiary` не заменяют старые migrations.

- [x] RED `test_migration_preserves_legacy_and_does_not_guess_streamer_buyer`: старые TEST units/status/order/payment/audit/grant IDs сохранены; Viewer beneficiary из subject, Streamer из связанного непротиворечивого billing order; unbound test grant остаётся NULL, не issued_by/current owner. Assert исходная backup копия не меняется.
- [x] RED `test_migration_is_atomic_repeatable_and_rolls_back`: injected failure откатывает schema/rows, reopen/repeat не дублируют; unique provider transaction/event/order grant и foreign keys работают. Run `python -m pytest tests/test_plus_payment_migrations.py tests/test_billing_viewer_product.py -q` → ожидаемый FAIL нового контракта.
- [x] Добавить поля/attempt/inbox/reconciliation в ту же DB; legacy `pending/paid/cancelled` mapping сохранить через совместимые readers. Mock duration/TEST contract не применять к RUB/XTR; валютные единицы и raw provider status разделить. Не ограничивать будущий calendar period legacy CHECK 31 суток.
- [x] PASS новые/старые billing migrations, integrity/foreign_key_check/row counts, две независимые conn и restore drill temp copies. Никакой активной staging migration сейчас.
- [x] Scoped review migration/binding/rollback → отдельный commit. Не разбирать/переписывать все остальные Database methods.

## P03. Серверный каталог, цены и readiness без денежной активации

**Файлы:** Modify `bot/plan_catalog.py`, `bot/mini_app_web.py`, `bot/mini_app_billing.py`; Create `tests/test_product_catalog.py`; цены из prototype не импортировать.

**Интерфейсы:** `get_product(product_id: str) -> ProductSnapshot`, `list_products() -> tuple[ProductSnapshot,...]`, `checkout_readiness(product_id: str, method: str, policy: BillingRuntimePolicy) -> CheckoutReadiness`; методы `stars/sbp/bank_card`. `POST /app/api/subscription/catalog` возвращает один каталог с version/features/offers/readiness; bootstrap содержит только безопасные пользовательские сведения/capabilities.

- [x] RED `test_catalog_has_two_products_exact_prices_inheritance_and_unapproved_period`:
  ```python
  assert viewer.rub.amount_minor == 15000
  assert streamer.rub.amount_minor == 30000
  assert streamer.includes == ('viewer_plus',)
  assert viewer.period_code == streamer.period_code == 'one_month'
  assert viewer.period_rule == 'unapproved' and not viewer.auto_renew
  assert viewer.xtr is None and set(methods) == {'stars', 'sbp', 'bank_card'}
  ```
- [x] Run `python -m pytest tests/test_product_catalog.py -q` → FAIL отсутствующих server offers. Добавить отрицательные client-price/product/method и неизвестные feature ID.
- [x] Минимальная immutable catalog version, цены/feature IDs один раз. Existing limits helpers сохраняют 50/200/5. Непроверенные raids/name/spikes/clicks не попадают в активные преимущества.
- [x] PASS каталог/API/auth; RUB 150/300 отображается и при `unapproved`, все методы видны с reason, ни один не запускает платежей. Срок month rule не выбирать.
- [x] Scoped review → catalog commit/checkpoint.

## P04. Effective Viewer из Streamer и все SQL/media gates

**Файлы:** Create `bot/entitlements.py`, `tests/test_entitlement_inheritance.py`; Modify `bot/capabilities.py`, `bot/database.py` (has_viewer_plus, _EFFECTIVE_PREVIEW_SQL, лимиты/filters/delivery), `bot/category_alert_store.py`, `bot/viewer_history.py`, `bot/viewer_trial.py`, `bot/mini_app_billing.py`. Проверить consumers `viewer_folders.py`, `viewer_reminders.py`, `viewer_web.py`, `poller.py`, `live_post.py`, `notification_worker.py`, `handlers/streams.py`.

**Интерфейсы:** `effective_viewer_predicate(user_sql: str, now_sql: str) -> str` принимает только внутренние SQL expressions, не клиентские строки; `resolve_effective_viewer(db, user_id:int, *, now:float) -> EffectiveViewerState(active, sources, expires_at)`. `Database.has_viewer_plus` делегирует общей effective семантике; `get_current_plus_grant` остаётся reader конкретного продукта. New test Streamer grant принимает явный verified `beneficiary_telegram_user_id`, legacy без него не угадывается.

- [x] RED `test_streamer_grants_viewer_only_to_frozen_buyer_and_independent_viewer_survives`: Streamer у buyer101 → лимит200/слоты5/filters/category/folders/history/reminder; участник202 и actor999 → Free. После expiry/refund Streamer отдельный Viewer продолжает работать; transfer Twitch не переносит личные права. Future grant/разрыв не удлиняет display срок.
- [x] RED `test_inheritance_applies_inside_sql_transactions_and_dispatch`: две conn, category INSERT SELECT, history write/read, trial eligibility, 51/201 лимиты, 5→6 CAS; revoke между capture и edit даёт фото. Run `python -m pytest tests/test_entitlement_inheritance.py tests/test_mini_app_capabilities.py -q` → FAIL нынешнего viewer-only SQL.
- [x] Общий predicate внедрить в прямые SQL и callbacks/runtime; не ограничиться CapabilityService. Старые тесты независимых legacy unbound grants сохраняют свою compatibility цель; новые paid/bound cases проверяют наследование, не ослабляя чужие placement assertions.
- [x] PASS inheritance + existing viewer limits/slots/delivery/filter/folders/history/reminders/trial/category/streamer access tests. Snapshot всех SQL потребителей после scoped `rg`, без whole-repo graphify.
- [x] Scoped security/data/media review → commit/checkpoint, оба источника/сроки возвращаются сервером, client mode не участвует.

## P05. Provider contract и Platega adapter с локальным transport

**Файлы:** Modify `bot/billing_provider.py`, `bot/billing.py`, `bot/billing_models.py`; Create `bot/platega_provider.py`, `tests/test_platega_provider.py`; существующие mock tests не удалять.

**Интерфейсы:** сохранить legacy mock методы через adapter; нормализованные async `create_payment(snapshot:ServerOrderSnapshot, attempt_id:str) -> CheckoutSession`, `get_payment_status(reference:str) -> VerifiedPaymentEvidence`, `handle_callback(body:bytes, headers:Mapping[str,str]) -> ProviderNotice`, `refund_payment(reference:str, request_id:str) -> RefundOutcome`. `PlategaProvider(transport, merchant_id, secret, hosted_hosts, runtime_policy)`; real transport запрещён policy первого release. Transport `request(method:str, path:str, *, json:dict|None, headers:Mapping[str,str], timeout:float) -> ProviderHttpResponse(status:int, headers:Mapping[str,str], body:bytes)` внедряется явно; ограничение ответа 65536 bytes и отсутствие raw body/headers в логах проверяются fake transport.

- [x] RED `test_sbp_card_server_money_and_hosted_link_contract`: methods2/11, exact RUB/Decimal minor units, server orderId/payload/metadata; HTTPS/allowlist без secret, карточные данные не попадают в bot. Test v2 `url` vs v1 `redirect` без изменения основного UX.
- [x] RED `test_schema_auth_and_unknown_creation_fail_closed`: duplicate headers/JSON, bool/float/NaN, body bound, отсутствующий `mechantId`, чужой merchant/method/amount/currency, unknown statuses; timeout/crash → creation_unknown и external POST count=1. Run `python -m pytest tests/test_platega_provider.py tests/test_billing_provider.py -q` → FAIL нового adapter.
- [x] Сверить актуальные официальные docs с research/sources SHA перед написанием adapter, изменения записать. Не исполнять SDK/helper примеры. Canonical GET требует все критические поля. Нет доказанного recovery/idempotency — создание остаётся выключено для сети.
- [x] PASS только fake transport; test-normalized evidence не оказывается в live DB. Mock HMAC/TTL/TEST правила сохраняются и не навязываются Platega. Payout HMAC/recurring6/H2H/cards не добавлять.
- [x] Scoped provider/security review → commit. Merchant/test creds/допуск не объявляются проверенными.

## P06. Durable callback/reconciliation, атомарные права и refund lifecycle

**Файлы:** Modify `bot/billing.py`, `bot/billing_store.py`, `bot/database.py`; Create `bot/billing_reconciliation.py`, `bot/payment_web.py`, `tests/test_payment_lifecycle_v3.py`, `tests/test_platega_callback.py`; wire через `bot/oauth.py` / `main.py` с выключенным live flag.

**Интерфейсы:** async `BillingService.prepare_payment(user_id:int, product_id:str, method:str, request_key:str, *, now:float) -> CheckoutResult`; `apply_payment_evidence(evidence:VerifiedPaymentEvidence, *, now:float) -> ApplyResult`; `accept_provider_notice(body:bytes, headers:Mapping[str,str], *, now:float) -> NoticeReceipt`; `reconcile_due(*, now:float, limit:int) -> ReconcileSummary`. Persist attempt до POST, inbox до ACK, state/grant/audit одной transaction; source=`paid` только у строгого service validator. Для real-provider refund `request_payment_refund(actor_id:int, order_id:str, request_key:str, *, now:float) -> RefundOutcome` сохраняет request до внешней операции, проверяет merchant-side роль, не разрешает её обычным нажатием поддержки. Legacy mock refund сохраняется через отдельный совместимый adapter.

- [x] RED `test_callback_before_create_response_and_duplicate_apply_have_one_grant`: callback before POST response/return, GET+callback race/две conn/crash после commit до ACK; один payment fact/один grant/start/end, replay не двигает срок. Foreign order/buyer/payload не выбирают beneficiary.
- [x] RED `test_chargeback_unknown_and_refund_do_not_restore_or_revoke_wrong_access`: chargeback до confirmed, старое confirmed, late after invoice expiry, unknown GET, refund accepted/manualControlRequired не равны completed. Независимый grant сохраняется. Run `python -m pytest tests/test_payment_lifecycle_v3.py tests/test_platega_callback.py tests/test_billing_lifecycle.py -q` → FAIL новых жизненных циклов.
- [x] Inbox ограничить 4096 nonterminal событий на provider, body≤8192 bytes, reject overflow без ACK; уже записанный duplicate ACK не требует нового места. Записанные финансовые события не удалять ради лимита. Reconcile — concurrency1, max10 due за tick, request timeout5 s; schedule 5/15/30/60/120/300/300/300 s, max8 canonical GET attempts на attempt, затем ручная сверка. HTTP429 учитывает валидный Retry-After без раннего повтора, timeout/исчерпание не дают success. Это локальные защитные defaults, внешний допуск требует проверки merchant limits. POST create/refund при неизвестном outcome автоматически не повторять.
- [x] Callback обычный auth X-MerchantId/X-Secret с constant-time compare, без raw logs; GET подтверждает критические поля. `/payments/platega/callback` в первом live release не активировать (404); локальный test app монтирует его с fake transport. Cancel UI не вызывает cancel/refund API. Refund policy unspecified → денежный grant/refund transition не разрешать; mock revoke политика остаётся mock.
- [x] PASS новый набор и старые billing replay/rollback/reopen/refund; cancellation runtime закрывает worker/session. Audit bounded IDs/status/UTC без token/header/card/body.
- [x] Scoped ledger/security review → commit/checkpoint. Current callback URL всё ещё reserved, не обещать действующим по наличию файла.

## P07. Telegram Stars adapter/handlers без invoice в первом release

**Файлы:** Create `bot/stars_provider.py`, `bot/handlers/payments.py`, `tests/test_stars_provider.py`; Modify `bot/handlers/__init__.py`, `bot/billing_provider.py`, `main.py`, `bot/config.py`.

**Интерфейсы:** `TelegramStarsProvider(sender, runtime_policy)`; `validate_precheckout(query, order) -> PrecheckoutDecision`; `successful_payment_to_evidence(message, order) -> VerifiedPaymentEvidence`. Verified Bot API user/charge ID, общая apply из P06. `/paysupport` использует P16 real support/readiness.

- [x] RED `test_xtr_tbd_disabled_runtime_and_client_paid_cannot_send_invoice_or_grant`: sender calls=0 при первом release/неутверждённом XTR; fake client invoiceClosed/paid/precheckout не дают Plus.
- [x] RED `test_verified_stars_payment_is_buyer_scoped_unique_and_refund_tracked`: payload/product/amount/currency/buyer/Streamer binding; duplicate charge не добавляет месяц, foreign charge/refund закрыты. Run `python -m pytest tests/test_stars_provider.py -q` → FAIL adapter/handlers.
- [x] Локальные invoices fixtures только с явно заданной QA XTR/period, без настоящего Bot API. Currency XTR/provider_token пустой, no recurring, precheckout ответ ≤10 s, успешный факт — successful_payment; refundStarPayment отдельно от закрытия UI. Не выдумывать Stars GET по charge ID.
- [x] PASS fake sender/service/auth; зарегистрировать handlers с той же runtime policy, без денежной активации от env наличия. Не добавлять Stars цену или Telegram test environment.
- [x] Scoped review → commit. Настоящий invoice/refund/native остаётся отдельным разрешением.

## P08. Три темы и настоящий Telegram SDK adapter

**Файлы:** Create `bot/mini_app_ui/theme.js`, `tests/test_mini_app_theme_assets.py`; Modify `telegram.js`, `app.js`, `app.css`, `index.html`, `bot/mini_app_web.py`; browser runner P01.

**Интерфейсы:** `createThemeController({storage, telegram, applyTokens}) -> {setChoice, getChoice, dispose}`; choices `light/dark/telegram`. `createTelegramAdapter` сохраняет existing методы, передаёт полный безопасный ThemeParams snapshot и update events; insets top/right/bottom/left + viewport, max каждого safe/content значения, не сумма.

- [x] Browser RED `theme_first_visit_is_light_even_in_dark_telegram`, `theme_explicit_choice_survives_reload_event_and_denied_storage`: assert exact own dark canvas #171717; Telegram params реально обновляют text/bg/button/link/header/bottom semantic tokens, не `colorScheme`→наша тема.
- [x] Run QA `--journey theme --engine chromium` / WebKit → ожидаемый FAIL нынешней двухтемной логики. Asset test проверяет новый модуль/CSP без inline/untrusted code.
- [x] Перенести tokens из selected.css выборочно, относительную типографику и один controller; значения ThemeParams валидировать как цвета, fallback контрастный. SDK ready/expand/fullscreen при наличии; old clients fallback, один BackButton handler/event cleanup.
- [x] PASS два движка, themeChanged/safeArea/contentSafeArea/viewport repeated init/dispose, explicit choice и storage errors. Никаких canned ThemeParams/live fixture query parameters в рабочем UI.
- [x] Scoped design-system/SDK review → commit. Native fullscreen/safe areas пока NOT TESTED.

## P09. Shell, компоненты и навигация выбранного А

**Файлы:** Modify `app.css`, `index.html`, `components.js`, `router.js`, `app.js`, `api.js`; Create `profile.js`, `support.js` (навигация/обычные состояния, контент P16); Modify `bot/mini_app_web.py` allowlist/bootstrap, `bot/telegram_identity.py`, `tests/test_mini_app_shell.py`, `tests/test_mini_app_auth.py`; tests в QA runner.

**Интерфейсы:** existing `createRouter(onChange)` + `openDetail/detail/back/refresh`, сохранить `state.mode/tab`; route detail допускает `{name,id}` для своей сущности. `createProfileFeature(api,getRouter,theme)` / `createSupportFeature(api,getRouter,telegram)` имеют `render/refresh/dispose`. Два существующих feature factories/render/refresh не ломать. `dialog/sheet` helpers имеют accessible name, close, focus origin, async aria-disabled guard.

Profile получает имя только из server bootstrap: `verify_webapp_identity(init_data:str, bot_token:str) -> VerifiedTelegramIdentity|None`, `VerifiedTelegramIdentity(id:int, display_name:str|None, username:str|None)` использует существующую HMAC/freshness проверку; `verify_webapp_user` сохраняется совместимым wrapper, повторный независимый validator не создаётся. Optional строки ограничены 256 символами и выводятся через textContent; при отсутствии — настоящий Telegram ID. Клиентское initDataUnsafe/переданный username не источник подтверждённой личности; avatar не выдумывается.

- [x] RED `navigation_restores_visible_row_scroll_focus_and_draft`: modes/четыре пункта; точные меню из §2, `Plus` активен в Subscription/purchase; Plus→Subscription→Back и Profile→тот же Subscription/Support→Back; повторный Plus не создаёт дубль history. Free «Возможности Plus», active «Моя подписка», цены/CTA/status/срок/includes доступны из обоих входов. Последний видимый row, short window, Tab во время pending/поздний ответ не крадёт focus. Dialog Tab/Shift+Tab/Escape/name/scroll/close. При text200 меню переносит целые действия без обрезания; при нехватке высоты CTA подключения уходит в общую прокрутку с сохранением focus, а не вытесняет инструкцию.
- [x] RED `test_profile_metadata_is_signed_and_id_wrapper_keeps_auth_contract`: altered name/user ID/signature/date/duplicate fields отвергаются; отсутствующее имя даёт ID, не вымышленную личность. Assert `verify_webapp_user(valid, token) == identity.id` и `verify_webapp_identity(tampered, token) is None`; freshness -60..600 s и initData≤4096 остаются прежними.
- [x] Run QA shell Chromium/WebKit и `python -m pytest tests/test_mini_app_shell.py -q` → ожидаемый FAIL новой композиции/asset контракта.
- [x] Переносить shell/components отдельно от Viewer/Streamer render. Убрать prototype boards, artificial phone chrome, fake role/plan query flags. Max600, системные fonts, минимум44/48, reduced motion и короткие transitions, без нового icon framework.
- [x] PASS auth/loading/error routes, CSP/allowlist всех modules (никакого произвольного file path), back/scroll/window resizing и input draft по субъекту. initData/token в localStorage не сохранять; личные draft keys привязать к verified user и очищать при смене пользователя.
- [x] Scoped Operate/accessibility review → commit. Это shell checkpoint, полнота данных придёт P10–P16, не объявлять продукт готовым.

## P10. Viewer Free: Главная, Стримеры, поиск/подписка/пауза

**Файлы:** Modify `bot/mini_app_ui/viewer.js`, `app.css`, `bot/mini_app_viewer.py`; existing `tests/test_mini_app_viewer.py`, `tests/test_viewer_plus_limits.py`; QA runner.

**Интерфейсы:** сохранять `/viewer/state|search|follow|unfollow|notify|plan-activate` payloads; add optional server `display_name` к state. Existing `TwitchClient.get_display_names` — batched по100; bounded cache300 s на максимум2000 public names, timeout5 s/fallback на реальный login, не обязательный сетевой запрос на каждую строку/рендер. Сервисы подписок/лимитов остаются в Database.

- [x] RED `viewer_free_journey_uses_same_bot_rows_and_preserves_input`: 0→search→follow→pause→reload→unfollow; local search среди собственных отделён; network error сохраняет query, late search не заменяет новый. Server 51-й Free/201-й Plus отвергается атомарно.
- [x] Run browser viewer-free + `python -m pytest tests/test_mini_app_viewer.py tests/test_viewer_plus_limits.py -q` → UI RED и scoped server RED только для нового metadata path.
- [x] Новая list-row композиция live/offline/stale с counts, category у live, pause отдельный; имена две строки/full detail+accessible name. Home использует реальные server counts/upcoming/reminders, никаких fake metrics; unavailable metadata оставляет login/initials, не invented аватар.
- [x] PASS 0/6/200, live marker >300 s stale, offline, reconnect, auth expiry, add write-access только по действию, server pause/priority после expiry не теряется. Bot и app изменяют одни rows.
- [x] Scoped journey review → commit/screenshots. Реальное write access/send не утверждать по fake SDK.

## P11. Полный выбор пяти видео и честный media status

**Файлы:** Modify `viewer.js`, `components.js`, `app.css`; existing `bot/mini_app_viewer.py` / `bot/viewer_preferences.py` / Database менять только при найденном API дефекте; tests `test_viewer_preview_slots.py`, `test_viewer_video_delivery.py`.

**Интерфейсы:** верхний выбор на любой длине списка; `video-selection` принимает `selected_logins` + `expected_version`, ID определяет сервер. UI показывает selected/effective/delivery отдельно; existing media states video/photo/preparing/limited/unavailable/unknown/offline/returning_photo используют честные тексты.

- [ ] RED `video_selector_counts_offline_search_and_atomically_replaces_sixth`: 4→5→6, offline и pause считаются, search count не уменьшает; шестой предлагает выбрать замену, не silently drop первый. Два окна/409 перечитывают server version, pending не показывает выполненный выбор.
- [ ] Run QA video и `python -m pytest tests/test_viewer_preview_slots.py tests/test_viewer_video_delivery.py -q`; UI RED прежней неполной композиции отдельно от existing server PASS.
- [ ] Перенести полноценный picker вместо prototype пояснения. Free видит «Видео · Plus» → возможности без доступа; Plus получает выбор своих logins, статусы/ошибки и cancel без серверной мутации.
- [ ] PASS 0/6/200, reload, offline count, all subscriptions owner-only; expiry/refund/disable→photo из P04/media, независимый Viewer сохраняется. Нагрузка limited не маскируется как video ready.
- [ ] Scoped UI/media review → commit/checkpoint.

## P12. Viewer settings: фильтры, категории, reminders, папки, история

**Файлы:** Modify конкретные render-функции `viewer.js` и shared styles/components; APIs/services из mapping сохраняются. Tests: `test_mini_app_viewer_plus.py`, `test_viewer_filter.py`, `test_category_alert_delivery.py`, `test_viewer_reminders.py`, `test_viewer_folders.py`, `test_viewer_history.py`.

**Интерфейсы:** existing expected_version/CAS, folder_id, category IDs, reminder15/30, history before_id. Quiet/digest — Free; настройки Plus после expiry хранятся без платного эффекта. UI authorisation следует effective state P04, не режиму.

- [ ] RED отдельными journeys `filter_folder_reset_and_conflict_keep_draft`, `category_search_and_signal_persist_verified_ids`, `reminder_15_30_cancel_inflight409`, `history_pagination_has_own_terminal_outcomes`, `quiet_digest_free_reload`. Для каждого зафиксировать DOM/assertion текущего UI, затем переносить его, не весь viewer.js разом.
- [ ] Запускать `--journey viewer-settings --scenario <один-сценарий>` в двух движках; focused Python файл соответствующего service перед его правкой → expected RED нового поведения/визуальной доступности, остальные сервисы могут уже проходить.
- [ ] Перенести формы в короткие detail-экраны/строки А; validate full text, pending/409/network/auth errors, reset личного пустого фильтра, принадлежность одной папке. Не писать новые detector/queue engines.
- [ ] PASS перечисленные service tests и браузерные mutations→reload→back. History `sent` только подтверждённый sender; отправки fake. Не менять технические retention/лимиты/Free quiet из-за копирайта.
- [ ] Scoped review всех перенесённых settings одним пакетом → commit/screenshots. Несвязанные cosmetic изменения откладывать, функциональные дефекты закрыть.

## P13. Streamer Free: Twitch OAuth и Telegram channel-only connection

**Файлы:** Modify `bot/streamer_community.py`, `bot/mini_app_streamer.py`, intent readers/writers в `bot/database.py`, `bot/handlers/streams.py`, `bot/oauth.py`, `bot/mini_app_ui/streamer.js`; Create OAuth result assets, `tests/test_channel_permissions_redesign.py`, `tests/test_oauth_result_pages.py`; existing connect/community/regressions tests.

**Интерфейсы:** `check_community_permission(bot, chat_id:int, user_id:int) -> CommunityPermissionResult(status, community, public_url)`; existing `verify_community_permission` совместимо возвращает VerifiedCommunity/None, использует тот же checker. Structured statuses: ready/bot_absent/bot_member/missing_post_right/user_denied/wrong_chat_type/network_error. Intent status pending/verifying/connected/denied/failed/cancelled/expired + permission_reason; backend отмена/TTL600 s сохраняются. Public channel URL только из проверенных метаданных того же chat.

- [ ] RED `test_new_intent_rejects_group_and_legacy_group_still_works`: direct API + prepared request + `tscommunity_` fallback + поздний chat_shared не подключают новую group; existing stored group доступна/публикации/настройки сохранены. Старые group-intent fixtures перевести в проверку explicit reject и отдельную seeded legacy group, не удалять ownership/replay coverage.
- [ ] RED `test_channel_post_only_creator_network_and_user_rights`: admin с can_post=True/can_edit=False ready; creator без optional flags ready; user чужой/не admin denied; API timeout → network_error, не missing rights; bot left/member/missing_post distinct. Run `python -m pytest tests/test_channel_permissions_redesign.py tests/test_streamer_communities.py tests/test_mini_app_streamer_connect.py -q` → FAIL нынешнего checker/type.
- [ ] RED OAuth `code_received_is_not_verified_success`: `_handle_callback` ещё только передал code, UI показывает «Проверяем подключение…»; success после verified token/Helix binding, cancel/error/reused state без успеха. Error query HTML escaped, OAuth state/token/code не попадают в screenshots/logs. Test `test_oauth_result_pages.py` и existing connect tests.
- [ ] Минимально согласовать checker с инструкцией «Администратор / Публикация сообщений», убрать скрытое требование чужих edits для channel flow. Groups не переводить на новую channel политику. Persist transient reason без ложного success; choose/check не включают публикации. OAuth страницы ожидания/успеха/ошибки/отмены и понятный возврат в Mini App; результат только verified server intent, URL не выдаёт право. Legacy OAuth без Mini App intent продолжает бот-сценарий: принятие code показывает «Вернитесь в бот, чтобы проверить подключение», не преждевременный успех; verified completion остаётся у существующего service.
- [ ] PASS backend + два browser engines: not chosen/absent/member/missing/checking/network/ready/cancel/stale; retry/focus/copy denial; exact fake bot send count=0 до явного publishing toggle. API lost rights между проверкой и toggle закрыт. Обновить existing tests can_edit requirement по принятому контракту с добавленными отрицательными кейсами.
- [ ] Scoped permission/OAuth review → commit/screenshots. Настоящие минимальные send/edit/delete/SDK requestChat — NOT TESTED до разрешённого канала P21.

## P14. Streamer Posts, variants и реальная статистика публикаций

**Файлы:** Modify `bot/mini_app_ui/streamer.js`, styles/components и `bot/mini_app_streamer_plus.py` только для подтверждённого разрыва контрактов; existing template/presets/stats services, poller/live_post gates сохраняются.

**Интерфейсы:** existing post-example/template/preview/presets/stats API. `renderPosts` показывает Free стандартный пост с фото; verified Streamer editor для конкретного placement; Viewer-only не получает оформление. Variants save ≠ apply; stats 30d +7/7 публикаций, не views/clicks.

- [ ] RED `streamer_post_draft_variant_apply_and_stats_are_real`: свой template version, safe text/HTTPS/buttons, ошибка/late result/два окна, сохранённый draft; preset delete не меняет active post, expired preset читается без paid apply. Неподтверждённая публикация не даёт счётчик.
- [ ] Run QA streamer-posts + `python -m pytest tests/test_mini_app_streamer_plus.py tests/test_streamer_template.py tests/test_streamer_presets.py tests/test_streamer_stats.py -q`; UI RED/новый contract RED до правки.
- [ ] Перенести действующий редактор/пример/варианты/аналитику в короткие А screens с двумя placements; не заменять их пустыми табами или local mock данных. Live preview UI не вызывает send; платный текст/media перепроверяются у runtime действия.
- [ ] PASS Free/Viewer/Streamer/both, wrong broadcaster/community, lost rights, CAS, expiry/regrant preserves settings; stats у verified broadcaster, «Переходы на Twitch · В разработке» отдельно от предоставленных функций без фиктивных чисел.
- [ ] Scoped journey/template review → commit/screenshots.

## P15. Subscription и три способа покупки с safe unavailable backend

**Файлы:** Modify `bot/mini_app_billing.py`, `bot/mini_app_web.py`, `bot/mini_app_ui/subscription.js`, `profile.js`, `app.js`, `bot/handlers/streams.py` (существующее меню); Create `purchase.js`, `tests/test_mini_app_purchase.py`; catalog P03/resolver P04/service P06.

**Интерфейсы:** `POST /app/api/purchase/prepare` принимает только product/method/request_key + verified initData; `POST /app/api/purchase/state` принимает только order_id/init_data и читает только свой существующий server order. Response: product, method, финансовый status, checkout_expires_at, access_starts_at/access_expires_at, effective access; не provider secrets/raw payload/чужой buyer. Первый release prepare возвращает HTTP503 `{state:'unavailable', message:<согласованный текст>, payment_request_created:false}` без order/grant/invoice/POST к провайдеру. Неправильные поля/чужой order не переходят в общий unavailable без auth проверки. `createPurchaseFeature(api,getRouter,telegram)` render/refresh/dispose. В личном бот-меню добавить «Возможности Plus» через проверенный WebApp URL на ту же Subscription; deep link только выбирает экран, не план/права. В группе не выдавать личный order/grant по chat ID.

- [ ] RED `test_three_methods_have_same_products_server_prices_and_no_payment_side_effects`: оба priced CTA, catalog15000/30000, выбор предложения по режиму/secondary Viewer link/раскрытие полного каталога/active status без внутреннего равноправного switch; все методы; 0 calls Platega/Stars sender, 0 новых orders/payments/grants, body amount/user_id/paid/mode не авторитет. Existing mock test-confirm не принимает real-provider order.
- [ ] RED browser `purchase_choose_back_retry_reload_unavailable_never_success`: «Как оплатить?» → каждый способ → честный текст; нет demo/mock/staging, внешняя Platega названа до предполагаемого перехода, никакого success-анимации. Run `python -m pytest tests/test_mini_app_purchase.py tests/test_mini_app_subscription.py -q` + QA purchase → FAIL нового flow.
- [ ] Подписка использует серверный каталог/features/effective sources/dates: Free «Возможности Plus», active «Моя подписка», Streamer «Все возможности Viewer Plus включены». По режиму Зритель сразу Viewer150 ₽; по режиму Стример сразу Streamer300 ₽. Основного внутреннего Viewer/Streamer switch нет. Viewer: четыре блока (200/Free50, 5 видео/Free фото, точные уведомления, папки/история). Streamer: четыре блока (видео поста, свой текст/оформление, кнопки/варианты, статистика); заметное «Viewer Plus включён», отдельное компактное раскрытие Viewer. Малые системные SVG без emoji. «Все возможности» раскрывает полный каталог. Вторичная ссылка «Нужны только функции зрителя? Viewer Plus — 150 ₽» открывает Viewer без расчёта доплаты. Active: «Моя подписка», текущий продукт/статус/срок/includes/свои операции/доступные действия. XTR отдельно не назначен. Active upgrade/повторная покупка открывают unavailable reason без выдуманной формулы.
- [ ] Обычный UI не содержит технических кнопок confirm/refund/test-checkout. Существующие owner-only API остаются строго gated для QA/совместимости, не общей покупкой. Mock history не называется денежной оплатой. Trial7 explicit/allowlist/one-time без auto start; inherited Plus не тратит trial и не позволяет двойной grant.
- [ ] PASS service/API/auth + browser normal/offline/errors/own history. Pending/succeeded/refunded/expired состояния проверять через локально подготовленный серверный ledger, не query/JS флаг успеха. Return URL только навигация; payment OFF сохраняется при наличии fake credentials и после restart.
- [ ] Scoped billing/UI/copy review → commit/screenshots. Это рабочий интерфейс без кассы, не успешная оплата.

## P16. Полный legal/support пакет и список owner inputs

**Файлы:** Create `docs/legal/PRIVACY-POLICY.md`, `USER-AGREEMENT.md`, `SUPPORT.md`, `TARIFFS.md`, `PAYMENTS.md`, `docs/legal/manifest.json`, `docs/workflows/PLATEGA-BANK-APPROVAL.md`, `docs/workflows/OWNER-INPUTS-FOR-LAUNCH.md`; Create `bot/legal_documents.py`, `bot/legal_web.py`, `bot/legal_ui/index.html`, `bot/legal_ui/legal.css` и `tests/test_mini_app_legal.py`; Modify `bot/config.py`, `bot/mini_app_web.py`, `support.js`, `subscription.js`, `tests/test_deployment_packaging.py`. Старые design legal drafts/checklist сохранить как архивную основу.

**Интерфейсы:** `LegalDocument(id,version,title,ready,body_html)`; `get_legal_document(id, catalog, owner_config) -> LegalDocument`; `get_support_state(config) -> SupportState`; signed `POST /app/api/support/state`, public static `/app/legal/privacy|agreement` не содержат частных данных. `SUPPORT_USERNAME`/`SUPPORT_EMAIL` без придуманных defaults; version согласия привязывается к order до будущего реального checkout.

`SupportState` содержит `available:bool`, `telegram_url:str|None`, `email:str|None`, `documents:tuple[LegalDocumentSummary,...]`; summary — id/title/version/ready/url. Manifest фиксирует id/filename/version/source_sha256/catalog_version/required_owner_inputs/owner_accepted; initial owner_accepted=false. Runtime читает только allowlisted filenames из canonical `docs/legal/`, кеширует по manifest version, не имеет произвольного path. Небольшой renderer на stdlib поддерживает заголовки/абзацы/списки и проверенные HTTPS/mailto ссылки, HTML экранируется; скрипты/remote include/непроверенные реквизиты запрещены. SHA/catalog mismatch либо missing inputs → unavailable, не публикация черновика. Packaging test включает manifest/документы/два legal assets в release archive; вторую независимо редактируемую копию текста не создаём.

- [ ] RED `test_unfilled_owner_inputs_never_appear_as_user_contact_or_published_legal`: group-only не контакт, unsafe URI rejected, missing contact hidden; нормальный unavailable текст без brackets/ИННexample/owner input. Bank-ready false, пустые docs не обозначены принятыми.
- [ ] RED `test_legal_tariffs_support_share_catalog_and_are_readable_before_purchase`: version/150/300/month/noauto/inclusion согласованы; содержимое реально читается, не modal title без текста. Run `python -m pytest tests/test_mini_app_legal.py -q` → FAIL routes/config; browser docs/support/text200/Back.
- [ ] Написать фактические полные документы по обработке Telegram identity, Twitch, tracked channels/settings/placements/Plus, orders/provider status/support и реально хранимой технике. Telegram/Twitch/hosting перечислить по факту; Platega раздел активен только при реальной передаче, будущая подготовка обозначена честно. Stripe/Paddle не действующая передача.
- [ ] Terms: Free/два Plus/150–300 ₽/ручной месяц/noauto/платёжный статус/срок/зависимости/поддержка/ограничения; не копировать ban refunds/24h/chargeback prohibition. Утверждённые владельцем реквизиты/retention/политики вставлять только после получения. Missing данные в OWNER-INPUTS, не обычный UI; неподготовленный документ имеет честный unavailable route, не fake опубликованный договор.
- [ ] PASS contact/route/CSP/document escaping/version/catalog tests, long content/360/390/keyboard; documents видны до покупки, условия не требуют Plus. Bank checklist включает весь путь/цены/все методы/контакт/docs/secrets/merchant; владельцу показать заполненный пакет до реальной банковской отправки, ничего не отправлять от его имени.
- [ ] Scoped factual/legal-copy review + Stop-Slop без изменения смысла → commit. User Agreement/Privacy публикация только после реальных обязательных данных и принятия владельцем; неполнота не блокирует остальные UI/staging задачи.

## P17. Отдельный user-copy gate и обратная совместимость

**Файлы:** Review всех изменённых `bot/mini_app_ui/*`, OAuth/legal pages и новых сообщений; Modify по найденным строкам; Extend `tests/test_mini_app_legacy_compat.py`, `tests/test_regressions.py`, `tests/test_viewer_preview_slots.py`; Create `tests/test_mini_app_user_copy.py`; старые `/viewer`, `/streamer`, report modules не удалять.

**Интерфейсы:** собирать текст только из видимых нормальных routes/states и штатных ошибок; scan внутренних identifiers/docs/logs не делать массовым replace. Существующие handlers/menu/exports остаются API совместимости.

- [ ] RED `test_normal_user_routes_have_no_prototype_copy_or_owner_payment_controls`: Mini App + OAuth + purchase + legal/support + empty/error/auth states, exact forbidden labels из §2. Test allowlist для реальных обязательных терминов узкий/обоснованный, не regex исключение всего экрана.
- [ ] RED для любого обнаруженного compatibility дефекта: old togglepreview owner/group-admin без Plus не меняет флаг; server sixth отказ; raid/live/rename quiet-hours Free; startup всегда сохраняет MenuButtonWebApp только для pinned testbot; HTML report bytes/build/send_document/24h fallback и legacy groups работают. Twitch follows import сохраняет существующие выбор/отмену/50–200 лимиты; private stats link и commands из mapping сохраняют прежние role/ownership scopes. Run `python -m pytest tests/test_mini_app_user_copy.py tests/test_mini_app_legacy_compat.py tests/test_viewer_preview_slots.py tests/test_regressions.py -q`.
- [ ] Убрать техническую копию/owner buttons из обычного UI, сохранив source flags/auth/audit и fake тесты внутри. «Оплата временно недоступна. Мы заканчиваем подключение платёжной системы.» дословно. Источник test/mock не называется покупкой/оплатой.
- [ ] PASS все перечисленные commands/old routes/report formats/privacy отрицательные cases. В новом профиле нормальное действие «Отчёты в боте» копирует `/report`/открывает проверенный chat, не отправляет автоматически. Free-функции из mapping сверить строка за строкой.
- [ ] Один batched Stop-Slop/Impeccable scoped review changed surfaces → исправления/подтверждение → commit. Не начинать новый дизайн и не обновлять screenshot baseline без объяснения принятой композиции.

## P18. Полная browser/accessibility проверка и новая media нагрузка

**Файлы:** Finalize `scripts/mini_app_redesign_browser_qa.cjs`, fixture; existing `scripts/mini_app_media_load.py` / `tests/test_mini_app_media_load.py`; evidence screenshots/JSON/scenario checklist в audit directory. Зафиксировать source/image hashes.

**Интерфейсы:** QA runner `--journey all --engine chromium|webkit`, common env §7; temporary DB, fake sender, explicit fixture SDK. Реальный browser zoom200 отдельным существующим проверенным helper, не CSS transform; text200 отдельно.

- [ ] Перед новым UI записать RED требований соответствующих P08–P17, затем полный QA на финальном UI snapshot. Не искать искусственные failures после уже пройденного focused gate.
- [ ] Matrix обоих engines: 360/390/430/768/1440 × light/own dark/Telegram light/dark × Free/Viewer/Streamer; 0/6/200 rows, длинные имена/плотный текст, stale/loading/empty/error/auth-expired, short390×440, text200 на360/390, настоящий zoom200, reduced motion/contrast/44px, dialogs/keyboard/focus/scroll restore. Проверить API→mutation→reload, а не только static DOM.
- [ ] Theme events/SDK handler cleanup/fullscreen fallback/safe inset horizontal+vertical и viewport change; channel/OAuth statuses, role-aware Viewer150/Streamer300, secondary Viewer link, четыре главных блока/раскрытие всех возможностей/active subscription, отсутствие 200 ₽ в runtime; Plus inheritance/expiry, 5→6 CAS в двух окнах, no accidental HTTP payment/client grant, legal/support готовый/недоступный. Console errors и внешние payment requests должны быть 0.
- [ ] Scoped accessibility/web-design-guidelines review (прочитать актуальные primary guidelines как требует навык), accessible names/roles, labels/errors/live regions без лишних объявлений. Impeccable + фактический просмотр выбранных PNG, один пакет конкретных исправлений и confirmation; не endless cosmetic loop.
- [ ] Run по одному, после функциональных PASS: `python -m scripts.mini_app_media_load --profile 1x1000 --encoder h264`, затем `100x10`, `1000x5` только при безопасных ресурсах. Сохранить реальные результаты, cap2/25s/96tasks/256MB growth/20CPU s/64MB temp/80pending. RESOURCE_STOP фиксируется как предел, не переименовывается PASS; следующий профиль не форсировать при unsafe stop. Fake edits/p95 не Telegram SLA. Обязательные fallback/cleanup/recheck тесты должны PASS.
- [ ] Review всех findings, hash screenshots/report/version/context → отдельный QA commit/checkpoint. Устройства Telegram/iPhone/Android не выдавать за WebKit/Chromium PASS.

## P19. Полный suite финального snapshot и backup/migration-copy/release

**Файлы:** existing `scripts/staging_deploy.py`, `scripts/sqlite_backup.py`, `tests/test_staging_deploy.py`, `tests/test_sqlite_backup.py`, `tests/test_deployment_packaging.py`; audit `RELEASE-GATE.md`, `MIGRATION-COPY.md`, `STAGING-ACCEPTANCE.md`. Новые tests/scripts/guard fixes создавать только при конкретном обнаруженном дефекте.

**Интерфейсы:** точный commit/tree/requirements/environment → evidence; `git archive` только committed snapshot; existing `backup_database(source:Path,destination:Path) -> dict[str,object]` / `verify_backup(path:Path) -> dict[str,object]` из `scripts/sqlite_backup.py`, verify/restore только отдельная копия. Согласовать clean tree без удаления/restash сохранённых docs.

- [ ] Операционный gate: никакой новой реализации до закрытия P18 findings. Проверить imports/static allowlist/CSP/packaging новых assets и legal/OAuth modules. Полный suite: `.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider` на финальном коде; записать exit/count/failures/skips/subtests/SHA, не обещать прежние 1290 для нового snapshot.
- [ ] Независимый whole-change code/security review одним reviewer, актуализировать repository-grounded threat checks: auth/IDOR, CSRF/cookie scopes, callback replay/secret logs, SQL атомарность, provider timeout, beneficiary/placement/media. Использовать security-best-practices/security-threat-model по релевантному Python/JS контексту, не общий чеклист вместо просмотра кода.
- [ ] Любая правка после suite инвалидирует evidence: RED/focused/review, затем новый полный gate на последнем snapshot. Не skip/disable/fudge assertions ради зелёного deploy. Существующие skips отдельно объяснить, новые не добавлять для обхода.
- [ ] По разрешённому после утверждения staging пути создать согласованный online SQLite backup + внешний export вне Git, SHA/integrity/restore drill. Последовательное копирование db+WAL не backup. Перед active migration прогнать новый код на новой temp copy: row IDs/counts/TEST/orders/grants/legacy groups/HTML data, повтор/reopen/foreign_key_check/rollback failure. Не читать production data.
- [ ] Закрыть доказательства миграции/секретов/чистого commit/платежей OFF. Старый binary после новой схемы не автоматический rollback: maintenance writer stop + проверенный совместимый binary или восстановленная копия с разрешённой активной заменой. Backup не удалять. Незадействованный staging-ключ не объявлять проверенным по копии с иным ключом.
- [ ] Gate PASS + scoped release review → release commit/checkpoint. Если runtime/backend/config после этого менялись, P19 повторяется на новом snapshot. В первом release отсутствие merchant/month/XTR/legal approval не отменяет UI deploy, но сохраняет payment/bank gates.

## P20. Guarded staging deploy и smoke точной версии

**Файлы:** existing `scripts/staging_deploy.py`, `scripts/staging_target.json`, `railway.json`; Create `scripts/mini_app_redesign_staging_smoke.py` / `tests/test_mini_app_redesign_staging_smoke.py` для read-only checks. Никакого редактирования Railway на этапе написания плана.

**Интерфейсы:** smoke `verify_release(target, expected_sha, expected_asset_hashes) -> SmokeResult`; fake responses unit tests, real mode только pinned staging, без invoice/send/OAuth/credentials output. Guard CLI `--check` проверяет target (сейчас не запускает suite!), `--deploy` сам повторяет полный pytest и загружает archive выбранного commit.

- [ ] Если нужен новый smoke helper: RED `test_smoke_rejects_foreign_bot_wrong_sha_asset_mismatch_and_fake_health_success` на fake HTTP/Bot API, затем minimal helper/PASS. Не запускать настоящий helper до approval/backup/P19.
- [ ] Проверить `staging_target.json`: project `14282646-e318-4b80-b35d-4369270de255`, service `45e46f2a-dba3-4b18-bc5f-b6fafa260055`, staging environment `7a873177-8ada-4b78-8732-a0bfdc1d519b`, staging Volume/domain из файла; одна replica, production environment/Volume отличны, no GitHub source staging. Любое расхождение остановить, pins не переписать ради deploy.
- [ ] Guard `python -m scripts.staging_deploy --check`, затем `--deploy` после утверждения плана и всех gates. Guard требует clean tree/фиксированный SHA; не обходить его raw railway up. Новые секреты/реальные payment env не вводить; adapter enable false сохраняется.
- [ ] Дождаться terminal SUCCESS и active identity; независимо проверить deployment ID/meta/SHA, getMe username/id, `/app` actual assets hash и `/app/api` unauthorized401/403, migrations/integrity, MenuButtonWebApp «Приложение» после restart. Health200 лишь один пункт. Внешний Platega callback first release404, client configs не раскрывают secret.
- [ ] Signed read-only API smoke выполняется только разрешённым путём без выгрузки bot token/Railway variables в чат/локальный лог; если такой auth путь недоступен, пометить его NOT TESTED и оставить проверку владельцу P21. Никакие синтетические подписи на live routes не получают отдельный bypass. Реальные mutations/публикации в smoke не отправлять.
- [ ] Smoke/pins/SHA/payment OFF фактически подтверждены → checkpoint/deployment запись. Не менять production. Если identity не доказана, owner пакет не объявлять выпущенным успешно.

## P21. Настоящий вход для владельца и пакет самостоятельной приёмки

**Файлы:** Create `docs/audits/mini-app-redesign-plus-2026-10-02/OWNER-ACCEPTANCE.md`, `SCENARIOS.md`, screenshots manifest; update STATUS/DECISIONS по факту. Реализация чужих сообщений/OAuth в эту документальную задачу не входит.

**Интерфейсы:** вход `@TwitchSignalTestbot` → постоянная кнопка «Приложение» → actual deployed `/app`; браузерный URL `https://worker-staging-2f74.up.railway.app/app` только оболочка, прямой URL не заменяет Telegram auth. Каждый сценарий имеет PASS/FAIL/NOT TESTED, snapshot/client и evidence.

- [ ] Дать владельцу рабочую кнопку и краткий путь через два режима/четыре пункта нижней навигации, быстрый Plus и сохранённый вход из профиля, Support/документы, темы, поиск/subscribe/pause/видео/settings, Streamer connection/posts/variants/stats, все три способа оплаты/unavailable, Back/scroll/focus/старые команды/HTML. Обычный UI без технических demo labels.
- [ ] Сохранить реальные mobile/desktop browser screenshots нового приложения, отдельно native screenshots если владелец фактически проверил. Не выдавать prototype/синтетический SDK/WebKit за iOS/Android/Desktop Telegram acceptance.
- [ ] Нативные fullscreen/safe areas/BackButton/themeChanged/requestChat/write access/клавиатура/OAuth return проверять на доступных клиентах и аккаунтах с отдельным разрешением. Нет разрешения/устройства → NOT TESTED, owner self-test возможен; не проходить OAuth от имени владельца автоматически.
- [ ] Для отдельно разрешённого тестового канала только после согласованного получателя проверить send→text/caption/media edit→delete собственных постов с `can_post_messages` и без `can_edit_messages`, legacy group, expiry→photo/animation response type. Иные получатели запрещены; в их отсутствие fake pipeline evidence остаётся явным ограничением.
- [ ] Проверку настоящих Plus-состояний обеспечить существующим allowlisted добровольным ознакомлением/отдельно подтверждённым техническим grant, не платёжной заглушкой. Не угадывать legacy beneficiary и не раздавать Plus открытием приложения. Даты и способ доступа UI сообщает честно, без утверждения «оплачено» для QA grants.
- [ ] Финал первого release: ссылочный пакет/точные commit+deployment+bot identity/tests/media пределы/owner checklist/открытые inputs и NOT TESTED. Ожидаются пользовательские правки после самостоятельного теста; production launch и bank submission не входят. Если владелец ещё не проверил native, итог «инженерный staging release, owner acceptance ожидается», а не полный PASS.

## 8. Отдельный допуск внешнего Platega/Stars test flow после первого staging

Это **не часть первого денежного запуска** и не разрешение текущего сообщения. Наличие готовых адаптеров P05–P07 не включает внешние операции.

1. Владелец подтверждает merchant/category/методы2/11, metadata, sandbox/free test, договор/чеки, schema/recovery/host allowlist и необходимые owner inputs; вводит PLATEGA_MERCHANT_ID/PLATEGA_SECRET только в Railway staging сам. Исполнитель не запрашивает значения в чат.
2. Отдельное разрешение конкретного безопасного provider test. По approved read-only connectivity GET /balance/all проверить связь без транзакции/логов сумм/секретов. Если бесплатность не доказана, остаться с unavailable flow.
3. После gates и guarded callback deployment проверить сертификат/точный route/identity, только официальный безопасный callback test. Reserved URL `https://worker-staging-2f74.up.railway.app/payments/platega/callback` не называть активным заранее.
4. Разрешённый sandbox E2E: create → hosted → callback/canonical GET → один grant → duplicate/late/status/cancel/refund по утверждённой policy; temporary/reviewed staging данные, никаких реальных денег. Не переносить metadata/credentials из examples.
5. Stars отдельно: утверждённые XTR/period/policy и конкретное разрешение invoice/test environment. Обычный testbot сам по себе не делает Stars бесплатными. До этого sender blocked и mock-only local tests.
6. Снова full final snapshot/security/backup/guard/smoke; включение runtime flag только при доказанных gates и разрешении владельца. Production/payment-public rollout всегда отдельно.

## 9. Контрольные точки и самопроверка плана

| Checkpoint | Содержание |
|---|---|
| P01 | Сохранённая основа, mapping, один fixture и QA builder |
| P02–P07 | Catalog/ledger/binding/effective права и локальные adapter/lifecycle tests; денег нет |
| P08–P12 | А shell/themes и полноценные Viewer journeys, реальные server данные |
| P13–P17 | Streamer/OAuth/channel/post/Plus/purchase/legal/support/copy и compatibility |
| P18–P19 | Новый browser/media/full-suite/review/backup snapshot, честные пределы |
| P20–P21 | Точный staging release и работающий вход владельцу, native ограничения отдельно |

**Покрытие:** spec §§1–4 → constraints/P03/P15; §§5–11 → P02/P05–P07; §§12–14 → P02/P04/P19; §15 → P08–P15/P18/P21; §§16–17 → owner gates/P05–P07/P16/P19–P20; §§18–23 → mapping/P16–P21. Новое сообщение владельца §§0–27 покрыто контекстом, mapping, отдельным P17 copy gate, первого zero-network release и owner input границами. Все пять review risks имеют названный RED у владельца задачи.

**Последовательность:** P01 → P02 → P03 → P04 → P05 → P06 → P07 → P08 → P09 → P10 → P11 → P12 → P13 → P14 → P15 → P16 → P17 → P18 → P19 → P20 → P21. Внешние gates блокируют только указанную денежную/публикационную/native операцию; independent local/UI задачи продолжаются. Все новые API/type names определены в Interfaces своей задачи; downstream повторно их не переименовывает.

**Выполнение:** P01–P21 утверждены attachment `650afe6a-f60f-4201-bc2e-938a49c9d84a`. Статусы и evidence ведутся в `mini-app/REDESIGN-PROGRESS.md`; checkbox отмечается только по факту. Первый staging сохраняет payment OFF. Owner inputs ограничивают деньги/bank/native операции, независимая реализация продолжается.
