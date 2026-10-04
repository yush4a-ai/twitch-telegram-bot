# Потоки клавиатуры — исходный аудит

RK = ReplyKeyboard, IK = InlineKeyboard. M = обычная[Меню]; S = selector[Выбрать Telegram-канал;Меню]; U = сервер не знает клиентское состояние. Системная WebAppПриложение всегда отдельна. Ни одного runtimeReplyKeyboardRemove; request_contact/request_user(s) отсутствуют.

| FLOW | RK BEFORE | WHAT IT CHANGES TO | MENU RESTORED исходно? | RISK |
|---|---|---|---|---|
|1 /start |U/M/S|M приcold илиcancel|Толькоcache/FSM|historicalready,failedrestore|
|2 deep /start payload|U/M/S|M,community→S|coldensure|более позднийselector|
|3 Меню|U/M/S|HomeIK;Mприcold/cancel|Даусловно|unknownclient/failedrestore|
|4 На главную|U/M/S|HomeIK|Даусловно|тотжеcache|
|5 Открыть приложение|U/M/S|IK/WebApp|cancelonly|coldotherentry|
|6 Добавить оповещения|U/M/S|IK/FSMinput|cancelonly|coldotherentry|
|7 вводTwitch login|U/M|IKconfirmation|RKнеизменна|coldinput|
|8 ошибкаlogin|U/M|plain/IK|RKнеизменна|coldinput/error|
|9 добавлениеуспех|U/M|IKresult|RKнеизменна|coldconfirmation|
|10 поиск|U/M/S|IK/FSMinput|cancelonly|coldsearch|
|11 список|U/M|IKpaging|RKнеизменна|coldcallback|
|12 удаление/отмена|U/M|IKconfirm/paging|RKнеизменна|coldcallback|
|13 Ястример|U/M/S|IK;cancelS→M|cancelonly|failedrestore/cold|
|14 OAuthstart|U/M/S|IKurl orplainlegacy|cancelonly|cold/error|
|15 OAuthsuccess|U/M|IK/plain|RKнеизменна|processreset|
|16 OAuthcancel|U/M/S|IK/cleardraft|cancelSonly|readyнеподтверждаетUI|
|17 OAuthexpiry/error|U/M|IK/plain|RKнеизменна|cold/error|
|18 channel selector|M/U|S|SсодержитМеню|readyнеобновлён|
|19 requestChat|S|клиентскийдиалог|Sещёактивна|отменадиалоганепосылаетupdate|
|20 cancelselector|S|M+IK|явныйsendпослеFSMclear|send/dbfailure|
|21 channel success|S|M|явныйsend|дублированныйChatSharedвозвращаетсярано|
|22 permissionerror|S|M|явныйsend|send/error/retry|
|23 Posts|U/M/S|IK/app|cancelonly|coldcallback|
|24 streamer settings|U/M/S|IK/app|cancelonly|coldcallback|
|25 quiet hours|U/M/S|IK/FSMinput|cancelonly|coldcallback|
|26 customquietinput|U/M/S|IK/FSMinput|cancelonly|coldinput/Menu|
|27 reports|U/M|IK/text|RKнеизменна|coldcallback|
|28 HTMLreport|U/M|document+IK|RKнеизменна|coldentry|
|29 import follows|U/M/S|IK/OAuth/input|cancelonly|cold/error/race|
|30 tariff|U/M/S|IK|cancelonly|coldcallback|
|31 paymentselector|U/M/S|IKnonce|cancelonly|coldcallback|
|32 paymentunavailable|U/M|IK|RKнеизменна|coldcallback|
|33 help|U/M/S|IK|cancelonly|coldcallback|
|34 support|U/M|plain/IK/url|RKнеизменна|coldcommand|
|35 legacy groups|нетprivateRK|толькоIK|НеустанавливатьRKвгруппах|crossactor/permissions|
|36 ErrorGuardfallback|U/M/S|alert/None|Нетrecovery|FSMclear+failedrestore|
|37 process restart|клиентM/S,serverU|приHomeM|otherentriesнет|первыйcallbackбезHome|
|38 stagingredeploy|клиентM/S,serverU|приHomeM|otherentriesнет|тотжеreset|

PreparedrequestChat в mini_app_streamer сохраняет одну кнопку для SDK savePreparedKeyboardButton; само по себе не отправляет RK в чат. RuntimeRKсоздают только menu_keyboard и show_channel_selector; обычнуюMотправляют ensure_menu_keyboard, cancel_ui и ChatSharedhandler. Все answer/send_message с IK или без markup не заменяют существующуюRK. НоваясессияDesktop/открытиечата не даютBotAPIсигнал видимости — нужнынативныепроверки.

ИтоговыеPASS/NOTTESTED по этим38сценариям будут записаны отдельно после regression/native этапов.
