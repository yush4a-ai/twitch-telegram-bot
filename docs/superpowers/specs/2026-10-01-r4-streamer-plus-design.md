# R4 — Streamer Plus без денег: design

Дата: 2026-10-01. Основание: `docs/ROADMAP.md`, R3 staging acceptance, текущие Telegram handlers, Twitch OAuth, `tracked_channels` и R2 signed Telegram auth. Реализация ограничена локальным контуром и pinned Railway staging / `@TwitchSignalTestbot`.

## Пользовательский сценарий

Стример открывает личный чат с тестовым ботом, подключает Twitch-аккаунт через OAuth и видит свой кабинет. После тестовой выдачи Plus он может подготовить оформление live-поста для своего канала и подключить несколько Telegram-сообществ, где Telegram API подтверждает его права администратора и права бота публиковать. До подтверждения обоих прав настройки конкретного сообщества не пишутся. При истечении или отзыве Plus новые платные настройки не применяются; базовое отслеживание и старые бесплатные функции сохраняются.

## Идентичность и entitlement

- `Telegram user ID` берётся только из проверенного личного Telegram update или из подписанных `initData`/Telegram Login с серверной проверкой подписи, свежести и CSRF по отдельному streamer session. Параметр URL, chat ID из клиента и cookie R2 owner-панели не являются доказательством личности.
- Twitch broadcaster ID и login берутся только из ответа `helix/users` на только что полученный Twitch OAuth token. Отдельный `/streamer_connect` в личном чате связывает этот результат с Telegram user ID, захваченным до выдачи state. Существующий `/auth_twitch` для статистики не создаёт связь и не теряет Free-функцию.
- Связь one-to-one по Telegram ID и Twitch broadcaster ID. Попытка привязать уже связанный Twitch к иному Telegram ID либо иного Twitch к тому же Telegram ID отклоняется без перезаписи. Смена/восстановление связи требует отдельной безопасной процедуры после проверки владельца; вход только по старому `twitch_user_tokens` невозможен.
- `entitlement_grants` — отдельные записи с UUID, `subject_kind=streamer`, `subject_id=broadcaster_id`, `plan=streamer_plus`, `source=test`, временем начала/конца, отзывом и audit timestamp. Активность проверяется сервером на каждый запрос/изменение: `starts_at <= now < expires_at` и `revoked_at IS NULL`. Тестовый grant/revoke доступен только в pinned staging через guarded operator CLI, без биллинга. R5 добавит source/order/payment, не меняя смысл доступа.
- Время берётся с сервера; expiration не зависит от фонового cleanup. Повторный grant с тем же idempotency key возвращает прежнюю запись, несовпадающий payload отклоняется. Audit фиксирует выдачу/отзыв без токенов.

## Кабинет, сообщества и live-пост

- Streamer кабинет отделён от `/admin`: свои маршруты, подписанная Telegram identity и своя cookie. Никакой R2 owner session, key или admin navigation не даёт streamer доступа. Сервер всегда выводит broadcaster ID из связки. До входа нет данных. В кабинете показываются собственный Twitch login, статус Plus/срок, связанные сообщества и агрегаты полезности по собственному Twitch login; чужие chat ID, токены и аудитория не раскрываются.
- Связь Telegram-сообщества создаётся после `getChatMember(chat_id, user_id)` со статусом creator/administrator и `getChatMember(chat_id, bot_id)` с достаточными правами отправлять/редактировать. При чтении/записи настроек права пользователя и бота перепроверяются; сетевой отказ означает deny. Для публичного канала нельзя полагаться на анонимное сообщение админа как на identity.
- В рамках R4 Streamer Plus разрешает несколько связанных сообществ и шаблон поста с заголовком, кратким текстом и не более двух дополнительных HTTPS-кнопок. URL отклоняет `javascript:`, userinfo, локальные/частные IP literal, пустой host и слишком длинные адреса; ссылка Twitch генерируется ботом отдельно. Шаблон сохраняется как ограниченный plain text, HTML экранируется при сборке поста. Фактическая UTF-16 длина итогового текста или caption проверяется при отправке; если оформление не помещается в 1024 единицы, отправляется базовый пост. Никакого произвольного HTML.
- Настройки versioned по `(broadcaster_id, chat_id)`; optimistic compare-and-set не затирает более свежую правку. При отправке и live update worker берёт актуальный разрешённый template после серверной проверки entitlement, связи и `user_id` текущего Helix stream. Если Twitch не возвращает ID, ID меняется, заканчивается эфир или истекает Plus, используется базовый пост. На каждом poll ID записывается заново либо очищается; старые jobs не обходят проверку. Изменение шаблона применяется на следующем coalesced live update R3.
- Preview 6 → 12 → 18 → 24, H.264 MP4 без аудио и safety budget около 10 MiB не меняются. Free-пути не забираются.

## Метрики и границы

- Польза стримеру: число подключённых сообществ и подтверждённых публикаций live-поста за последние 30 дней. Событие публикации пишется атомарно после успешной установки текущего Telegram message ID и только если пара Helix broadcaster ID/login совпадает с подтверждённой связкой. `done` у queue job может означать устаревшую задачу, поэтому не считается публикацией. Нет заявлений о реальных просмотрах поста или конверсии без источника. Данные других Twitch-каналов не смешиваются.
- Миграция additive, one-replica SQLite. Перед staging schema deploy: diff, полный тестовый gate, внешний staging snapshot, online backup/restore, сверка pinned target. Staging smoke использует testbot и временные/контролируемые данные; cleanup и отсутствие следов в production проверяются.
- R2 owner-панель остаётся read-only. Реальные платежи, реальный provider, production deployment/DB/variables и push/merge `main` исключены.

## Приёмка

1. Отрицательные тесты: чужой Telegram ID, поддельные signed data, прямой URL, не связанный Twitch, чужое сообщество, недостаточные права бота, истёкший/отозванный grant, конфликт idempotency, опасные URL/HTML, replay OAuth state.
2. Положительный тест: личный Telegram ID + подтверждённый Twitch OAuth → связь; тестовый grant → кабинет и разрешённый template в двух сообществах → публикация; revoke/expiry → безопасный Free fallback.
3. Полный suite, review diff, staging deploy из commit snapshot; staging owner auth и обычное меню не регрессируют. Настоящее Twitch/Telegram UI E2E отмечается только после фактического прохождения, synthetic подпись не выдаётся за реальный вход.

## Самопроверка

Каждое изменение Plus требует серверной identity, активного grant и проверенных прав сообщества. Источник Twitch broadcaster ID не берётся из клиентского ввода. Ранее записанный token не доказывает владельца Telegram. Тестовая выдача не является оплатой. Production действия и выбор платёжного провайдера исключены.
