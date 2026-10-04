# Native acceptance — NOT TESTED

Текущая сессия: native-проверку владелец отложил, поскольку работает за компьютером. Computer Use прекращён; Desktop/iOS/Android NOT TESTED, screenshots отсутствуют. Exact prepared staging upload BLOCKED по size; old staging не закрывает новый runtime.

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

## Минимальный smoke order после разрешения owner — 04.10.2026

Для Desktop, затем iOS, затем Android отдельно: `/start` → «Меню» → live → system «Приложение» → возврат в чат. Записать client version, artifact/deployment/bot ID, UTC, PASS/FAIL/NOT TESTED и screenshot. Пока все три NOT TESTED.

После базового открытия на разрешённых аккаунтах: Viewer add с подтверждением → pause → delete/Undo → quiet live/raid inside/outside/exemption → Streamer network error/Retry → OAuth/cancel/reopen → channel selector → photo → animation/video/fallback → expiry/revoke/notifyOFF. Сохранить HTML/export и старые группы; проверить payment unavailable без invoice/checkout/grant. Не делать реальные действия без конкретного разрешённого recipient/account/channel и лимита. Все расширенные сценарии пока NOT TESTED.

## Manual iOS / Android — NOT TESTED

На каждом реальном устройстве отдельно: открыть @TwitchSignalTestbot → Меню → Приложение/Mini App → переключить Viewer/Streamer → Back/close → reopen → safe areas → клавиатура → тема → Тариф/payment unavailable. Результат каждого устройства: PASS / FAIL / NOT TESTED; указать client version/UTC/deployment artifact и один снимок. Пока актуальный prepared artifact не опубликован, старый staging не закрывает эту acceptance.

Owner visual: подтвердить (1) прежние пользователи/стримеры отображаются, (2) существующий report/HTML открывается, (3) Mini App правильный, (4) Home/Menu и возврат правильные, (5) нет явно потерянных данных. Новый дизайн-аудит не нужен.
