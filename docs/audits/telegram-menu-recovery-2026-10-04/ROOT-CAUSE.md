# Причины и исправления

## Подтверждённые серверные ошибки

- MenuStore переносил исторический keyboard_ready вместе с редактируемой карточкой. Каждое редактирование продлевало его срок; selector не менял этот флаг. После неудачной отправки восстановления следующий Home мог не отправить обычную клавиатуру.
- Selector после ожидания guide отправлялся без проверки живого FSM intent. Отмена могла завершиться раньше; поздний selector возвращался после неё. Отмена во время самой API-отправки требовала общего порядка отправок.
- После restart/redeploy recovery существовал только для Home/deep start. Другие callbacks и команды могли отвечать без установки ReplyKeyboard. Для долгого OAuth недостаточно recovery только после завершения handler.
- Connected ChatShared replay возвращался раньше восстановления; при задержанном DB-чтении старый ответ мог очистить новый FSM selector. Ошибка DB cleanup не должна сообщать об успешной отмене.

RED воспроизведения сохранены рядом с этим документом. Новый MenuStore хранит последнее успешное keyboard delivery отдельно от Home cache: тип, время, intent и срок selector. Inline edits не продлевают 24-часовой срок. Неизвестный результат отправки сбрасывает доверие к прошлому delivery. Per-bot/per-chat lock упорядочивает selector и restore. Selector проверяет живой intent перед отправкой; ChatShared повторно сверяет поколение перед clear. Ошибка cleanup восстанавливает обычную клавиатуру с честным сообщением и сохраняет исключение.

ErrorGuard восстанавливает путь возврата перед разрешённым собственным private interaction и после handler, в том числе после ошибки. Активный актуальный selector сохраняется. Тёплый Home не создаёт новое служебное сообщение. Cold/reset/expiry и замена клавиатуры восстанавливаются. Это сведения об отправке, а не подтверждение видимости на устройстве.

## Что не было ошибкой в исходном коде

Обычная markup уже была правильной: [[Меню]], is_persistent=True, resize_keyboard=True, one_time_keyboard=False. Runtime ReplyKeyboardRemove отсутствует. request_contact/request_user(s) отсутствуют. Inline edit/detach не удаляет ReplyKeyboard. Навигация ловит «Меню» раньше FSM; throttle пропускает эту кнопку. Системная «Приложение» остаётся отдельной MenuButtonWebApp → /app; main.py не изменён.

## Причина исчезновения live

В commit 4d4dcf1 при сокращении more_keyboard из pairs убрали menu:live. cb_menu_live продолжал работать. Возвращены прежний callback и обработчик с названием «🔴 Сейчас в эфире». Второй реализации нет. Проверка existing callback через реальный SQLite/aiogram и fake API delivery возвращает собственные live-стримы, исключает чужие и восстанавливает cold keyboard.

## Граница native-доказательств

Причина визуального отсутствия «Меню» у владельца в Telegram Desktop не установлена. Первое чтение окна Testbot было частично вне экрана; затем helper обнаружил ввод владельца и сворачивание. Ввод остановлен. Последнее чтение выбрало окно TwitchSignalTest, но его область перекрывал браузер. Чужие снимки не сохранялись. Native BEFORE/AFTER и проверка совместного отображения не выполнены. Нельзя объявлять это client limitation или native PASS.

В истории Testbot было служебное сообщение «Возвращайся сюда кнопкой „Меню“» в 9:15. Это источник установки из ensure_menu_keyboard; сервер не получает подтверждение клиентской видимости.

Официальный Bot API: https://core.telegram.org/bots/api#replykeyboardmarkup и https://core.telegram.org/bots/api#editmessagereplymarkup . Persistent просит клиента показывать клавиатуру; getReplyKeyboard API отсутствует. editMessageReplyMarkup принимает InlineKeyboardMarkup. Для повторной установки ReplyKeyboard нужен новый send. Поведение toggle/ручного скрытия Desktop требует фактической native проверки.
