# R7 — Telegram Mini App и Viewer Plus: design

Дата: 2026-10-01. Основание: `docs/ROADMAP.md`, R2 owner-only auth, R3 staging queue и R4/R5 entitlement/ledger. Только закреплённый Railway staging с `@TwitchSignalTestbot`; production и реальные платежи исключены.

## Цель и граница

Дать обычному пользователю личный Mini App для просмотра его подписок и управления базовым флагом оповещений. Viewer Plus добавляет правила по игре, словам заголовка и исключениям, а также переключатель уже существующей сводки после тихих часов. Plus выдаётся лишь закрытым тестовым owner grant; R5 mock billing для Streamer Plus не превращается автоматически в продажу Viewer Plus. Бот и Mini App читают/пишут одну staging SQLite DB; `tracked_channels.chat_id=telegram_user_id` остаётся источником подписок и `notify_enabled`.

## Аутентификация и границы данных

- Mini App получает только `Telegram.WebApp.initData`, передаёт сырую строку серверу в каждом запросе настроек и никогда не использует `initDataUnsafe` для права доступа. Сервер переиспользует `verify_webapp_user`: сортировка полей, HMAC-SHA256 с `WebAppData` и bot token, constant-time сравнение, свежесть `auth_date` до 600 секунд, отказ на дубли полей/неверный user ID. Это соответствует [официальной схеме Telegram](https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app). Секрет и ID другого пользователя не принимаются из URL или JSON payload.
- `/viewer` отдаёт только публичный shell без пользовательских данных и без любых меток/команд «Админ-панель». `/viewer/api/*` при отсутствии/подмене/устаревании initData отвечает 401/403 до чтения и записи. Подписки и настройки выбираются только по проверенному Telegram ID; прямой URL не даёт данных. Browser fallback для Viewer Plus в R7 не вводится: запуск предусмотрен в личном чате тестового бота.
- Кнопка «Мои оповещения» показывается обычным пользователям только в личном чате на pinned staging. В группах, каналах и общем меню бота её нет; owner-only `/admin` и аварийный `ADMIN_PANEL_ACCESS_KEY` остаются изолированы. R2 отрицательные тесты повторяются после изменений меню.

## Entitlement, хранение и правила

- Существующая `entitlement_grants` поддерживает `subject_kind='viewer'`, `subject_id=str(telegram_user_id)`, `plan='viewer_plus'`, `source='test'`; закрытый owner CLI выдаёт/отзывает grant на pinned staging с request key, сроками и audit events. Истечение/отзыв сразу выключает расширенный фильтр. Free-пользователь может читать свои подписки и переключать базовый `notify_enabled`, но не менять Plus-правила.
- Additive миграция `r7_001_viewer_filters` хранит правило отдельно для `(telegram_user_id,twitch_login)`: списки `games`, `title_keywords`, `exclude_keywords` в ограниченном JSON, `version` и `updated_at`. Разрешены только уже отслеживаемые в личном чате Twitch login; каждое изменение требует текущего Viewer Plus и ожидаемой версии, иначе отказ. Удаление подписки ботом убирает связанное правило в той же DB операции. Название игры сравнивается без регистра точно, keyword — без регистра как подстрока заголовка; внутри списка OR, между непустыми `games` и `title_keywords` AND, `exclude_keywords` всегда имеет приоритет. Пустой список не ограничивает. Списки ограничены пятью элементами, строки длиной 2–40 символов без control characters; значения трактуются как текст, без исполнения URL или regex.
- На pinned staging poller применяет правила только к личным destination (`chat_id > 0`) до создания нового go-live job или прямой отправки. После смены правила queued worker повторно сверяет актуальную конфигурацию перед отправкой, чтобы не выдать уже исключённый эфир. Группы/каналы и Free-подписки сохраняют прежний путь. Когда фильтр не совпал, stream state/sample сохраняются для отчёта, но новый пост не публикуется. Уже отправленный live-пост не удаляется из-за позднего изменения правила; изменение действует на следующий новый send.
- Сводка пропущенного в R7 использует существующие quiet-hours/digest методы и `notify_after_enabled` только для личного чата; Mini App читает и меняет тот же флаг, что кнопки бота. Она не пересылает намеренно исключённые фильтром эфиры и не обещает сводку при отсутствии настроенных тихих часов. Отдельный digest движок и массовая рассылка не создаются.

## Проверка и rollout

TDD: подпись owner/ordinary/non-owner/direct URL и отсутствие admin entry; Free vs Plus доступ, чужие подписки, версии и invalid filters; match semantics и fallback после expiry/refund/revoke; бот↔Mini App синхронизация `notify_enabled`/quiet-hours digest; queue/direct path фильтр и recheck; миграция старой DB. После полного suite и code review — внешний staging backup и migration drill на копии, deploy только через pinned guard. Smoke: `/viewer` без чужих данных/админ-метки, API отказывает без подписи, synthetic signed owner/non-owner получает только свои строки, `getMe=TwitchSignalTestbot`, DB integrity. Реальный Mini App UI E2E зависит от разрешения staging domain в BotFather и не объявляется завершённым по synthetic HMAC.

## Самопроверка

Viewer Plus здесь является тестовым entitlement без цены и провайдера. Настройки единственные в существующей DB; JSON не выбирает субъект доступа. Фильтр применяется к личным новым уведомлениям и повторно перед queued send; сообщества и текущие посты не меняются скрыто. Owner admin UI отсутствует у обычного пользователя. Production не затрагивается.
