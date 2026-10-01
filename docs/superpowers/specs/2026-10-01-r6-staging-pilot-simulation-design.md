# R6 — закрытая staging pilot simulation: design

Дата: 2026-10-01. Основание: утверждённый `docs/ROADMAP.md`, R4 staging engineering checkpoint и R5 mock billing acceptance. Только закреплённый Railway staging с `@TwitchSignalTestbot`; production не меняется.

## Цель и граница результата

Смоделировать восемь независимых стримеров в отдельной временной SQLite DB внутри staging контейнера. Для каждого пройти подтверждённую *синтетическую* пару Telegram/Twitch, тестовый Plus capture, одно сообщество, безопасный live-post template, фактическую запись события публикации и статистику. Проверить отказы для чужого владельца и неверного broadcaster ID, затем refund/expiry Plus и возврат к базовому посту. Измерить время и размер временной DB, зафиксировать ожидаемые отказы и отсутствие сетевых вызовов/денежных затрат. Это инженерная simulation, не реальный pilot 5–10 людей.

## Сценарий

- Guard проверяет точные staging project/environment/service IDs, имя окружения, `/data` mount, `DB_PATH=/data/bot.db` и подтверждённый `OWNER_CHAT_ID=425785231`. Точка вызова запускает временную DB вне `/data` и никогда не открывает активный DB_PATH. Deployment guard отдельно сверяет Volume instance ID и одну replica.
- Восемь фиктивных Telegram ID и Twitch broadcaster ID создаются только в этой DB через существующую identity модель. Это не Twitch OAuth и не присвоение реального аккаунта. Для каждого `BillingService` с `MockPaymentProvider` выдаёт TEST checkout; подписанный тестовый capture даёт один mock grant. Секрет создаётся в памяти и не выводится.
- Для каждого добавляется одно фиктивное сообщество через существующий DB путь, версия безопасного template сохраняется, а live state с текущим broadcaster ID даёт этот template. `compose_streamer_post` сохраняет базовую Twitch-ссылку и экранирует текст. Подмена broadcaster ID и запрос другого Telegram пользователя не дают доступа к template.
- `set_live_message_if_current` записывает одну подтверждённую simulated post event, повтор не добавляет вторую. Analytics каждого владельца видит ровно одно сообщество и одну публикацию, соседний владелец не получает его данные. Внешних сообщений в Telegram нет.
- Два mock grants проходят verified refund и теряют Plus; остальные шесть истекают по серверному времени. Active template после потери Plus не выбирается, историческая статистика остаётся. Отдельный pending checkout отменяется без выдачи Plus.

## Метрики и приёмка

Вывести компактный JSON без ID, token, webhook body и секретов: `streamers=8`, `communities=8`, `templates=8`, `published_events=8`, `refunds=2`, `expired_grants=6`, `cancelled_orders=1`, `expected_denials`, `unexpected_errors=0`, `telegram_calls=0`, `twitch_calls=0`, `payment_network_calls=0`, `external_cost_units=0`, wall time, DB bytes и `integrity=ok`. Нулевые внешние расходы означают только закрытый сценарий без сетевых API, не прогноз реальной стоимости pilot. Не объявлять SLA на основе восьми субъектов.

Сначала TDD на guard, положительный сценарий, изоляцию владельцев, неверный broadcaster ID, refund/expiry и отсутствие записи в активную DB. Затем полный локальный suite, review, commit snapshot, staging deploy через guard, terminal SUCCESS и отдельный запуск в staging. После запуска проверить активную DB и обновить `docs/STATUS.md`, `docs/DECISIONS.md`, audit evidence. Если staging run не состоялся, отметить R6 неполным.

## Ограничения и дальнейший путь

Реальный `/streamer_connect` требует настоящего Twitch OAuth; Telegram Login Widget на тестовом домене ранее сообщал `Bot domain invalid`. Проверки UI и разрешений сообществ с реальными участниками не заменяются этой симуляцией. Настоящий pilot с 5–10 стримерами, production rollout, платёжный provider и деньги не выполняются автономно. R7 может начаться после инженерной staging simulation, не ожидая этих внешних условий.

## Самопроверка

Фиктивные ID и TEST units существуют только во временной DB; публичных маршрутов и изменений активных пользовательских данных нет. Результат называет именно simulation и ограничивает вывод о затратах. Все восемь шагов имеют проверяемый счётчик или отрицательный assert; потеря Plus блокирует template и не стирает историческую статистику.
