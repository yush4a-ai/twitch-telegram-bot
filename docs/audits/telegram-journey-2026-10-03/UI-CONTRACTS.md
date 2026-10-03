# Актуальные Telegram-пути и факты

BASE b294825; существующая карта полного бота `../telegram-ui-2026-10-03/UI-MAP.md` сохраняется. Эта сверка адресная, whole-repo graphify не повторяется.

| Экран/вход | Действие и источник данных | Возврат / проверяемая проблема |
|---|---|---|
| /start, Меню, menu:home | own_private → cancel_ui → load_home_state → show_home | Команда уже отвечает снизу; callback редактирует текущую карточку. Не возвращать скрытый cached edit. |
| Главная: приложение | _viewer_url + WebAppInfo /app; обычные четыре входа | Системная Приложение остаётся. Никакой выдачи прав по выбранному режиму. |
| Добавить оповещения | существующий AddChannel, Twitch lookup, одно подтверждение, atomic add50/200 | Нужны явная отмена, источник списка, Добавить ещё после успеха. Late query generation уже защищён. |
| Импорт | legacy OAuth generation, preview нового списка, atomic limit | Сохраняется; не импортировать по одному открытию или после отмены. |
| Ещё | more_keyboard; admin URL только подтверждённому owner private | Вынести live в список, about в помощь; сохранить старые callback routes. |
| Список | list_channels_with_routing(target), source chat / admin check | Сейчас без страниц и поиска; карточка→legacy channellist теряет allow_add удалённого канала и создаёт промежуточный список. |
| Карточка | независимые notify, auto_report, format, raids, recipient/quiet exemption | Личные и публичные получатели различаются; follower permission принадлежит стримеру, не зрителю. |
| Удаление | _check_manage_permission + remove_channel | Inline untrack сейчас без подтверждения; команда /untrack — уже явное действие. |
| Кто в эфире | текущие DB live/samples; старый menu:live остаётся | Не объявлять неизвестный/старый статус подтверждённым offline. |
| Я стример | get_streamer_identity(user); verified OAuth intent | Free подключение сохраняется. Шаги Twitch→канал→посты→права, без ложного readiness. |
| Telegram-канал | owned expiring community intent; официальный request_chat(channel-only); свежая проверка прав | Selector→posts не отменяет intent/native keyboard: воспроизведено RED на router+DB. Новые подключения только каналов; старые группы сохраняются. |
| Публикации | WebAppInfo текущего /app | Сейчас router не читает mode/tab query в app.js; не выдавать неработающую deep-link ссылку. Допустима честная инструкция «Стример → Посты» без изменения свежего Mini App. |
| Тариф | общий catalog150/300, SubscriptionService, frozen beneficiary | Вход человеческий; product name берётся из текущего catalog. Source streamer/secondary/back нужно сохранить. |
| Покупка | BillingService.public_purchase и nonce/expiry; payment OFF | Stars/СБП/карта доступны как выбор, не создают invoices/orders/POST/grants. Source navigation не авторизует деньги или функции. |
| Отчёты | существующий отчёт/stream samples/HTML | Free и HTML/export сохраняются. Проверить дату эфира и timezone, не путать с датой доставки Telegram. |
| Тихие часы | Free recipient settings; UTC-minute conversion | Сейчас ввод целого UTC offset; нужны быстрые варианты и итоговое местное время. Группы — по admin check. |
| Помощь | фактические команды/ссылки; configured support, ready legal docs | Не придумывать контакт или юридическую готовность. About включить в помощь, старый вход оставить. |

## Контракт личных фильтров

`viewer_filter.py`: до5 terms каждой группы,2–40 символов; Unicode casefold. Игры — точное название, слова/фразы заголовка — literal substring, любое включение внутри своей группы; выбранная игра и включение заголовка должны совпасть одновременно; любое исключение блокирует. Пустая группа не ограничивает.

`poller._viewer_filter_allows`: только личный chat_id>0 и включённый feature flag; DB выдаёт effective filter с текущим Plus и наследованием папки. Это фильтр стартового оповещения, не отдельный новый сигнал изменения названия. Групповая публикация не наследует личное правило владельца.

CategoryAlertStore / CategoryObservation — отдельный стабильный detector перехода во время эфира; свои ledger/dedupe/preferences/Plus/quiet checks. Не переносить правила старта на категорийные сигналы без подтверждённого контракта.

Вопрос «смотрим/фильм → дополнительное уведомление при изменении заголовка» требует отдельного продуктового решения после проверки существующего поведения. Здесь не изобретаются AND/OR, частота повторов или новый платный продукт.

## Ограничения доказательств

Осмотр исходного чата: наблюдения интерфейса владельца, не полный native аудит. Сейчас проверки на real router/FSM/temp DB + fake transport. Новичок, успешный live/raid delivery, owner OAuth, реальные канальные сохранения, signed Mini App, native размеры/клавиатура после нового релиза и payments ещё не подтверждены. Не менять owner данные/подписки ради теста.
