# Native acceptance — NOT TESTED

Browser/fake/локальные tests не являются Desktop/iOS/Android acceptance. Новый B/C код не опубликован; staging сейчас прежний @TwitchSignalTestbot. Перед будущей проверкой закрепить опубликованный SHA/deployment/bot ID и версии клиентов. OAuth/send/media только на отдельно разрешённом аккаунте/чате с лимитом действий.

Для **каждого** Telegram Desktop / iOS / Android отметить PASS/FAIL/NOT TESTED и приложить реальный снимок/время/artifact:

- /start в новом/прежнем чате; постоянное «Меню», system «Приложение», открытие Mini App и возврат.
- Viewer add с подтверждением, pause/delete/Undo; live list и stale/offline состояния.
- Free quiet hours: go-live и raid внутри/вне интервала, исключение стримера, channel publication отдельно, delayed/retry fresh check.
- Тариф150/300: unavailable у всех трёх способов, без invoice/checkout/grant.
- Streamer первая network error → «Повторить» → loaded; cancel/reopen, late callback без чужих/повторных mutations.
- Разрешённый OAuth, Telegram channel selection, отмена выбора и повторное открытие; старые group connections/HTML export сохраняются.
- Фото fallback; разрешённая animation MP4/H.264/no audio6→12→18→24→6; expiry/revoke/notifyOFF возвращают к photo в том же посте.
- Обычный restart сохраняет pending commands/selection, completed update не выполняется повторно; ambiguous ordinary inbox не replay вручную без review.

Сейчас всё перечисленное — **NOT TESTED на новом runtime**. Исторические screenshots/docs остаются доступными и не объявляются свежей native приёмкой. Конкретные account/channel/smoke-recipient ещё OWNER INPUT; сторонним пользователям сообщения не отправлять.
