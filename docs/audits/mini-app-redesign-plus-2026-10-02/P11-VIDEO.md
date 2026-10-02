# P11 — полный выбор пяти видеопревью

BASE `dfa13408f5d540d9523541d33d6167b82b09aa21`. Один исполнитель, backend/API/media engine сохранены; новая UI композиция и request/focus fences.

## RED → PASS

- `P11-browser-red.log`: верхнего picker нет. Теперь Стримеры → Видео N/5 → отдельный выбор всех own subscriptions, поиск/счётчик/доставка. Free «Видео · Plus» ведёт на общий Subscription, direct POST403 не выдаёт право.
- `P11-server-before.log`: existing slots/media22tests/3subtests PASS до UI правки. Итоговый `P11-server-pass.log`: **40 tests / 6 subtests**, 45.68 s; slots/CAS/own IDs/Free/sixth direct409, old callbacks/shared media rights, revoke/refund/unknown edit→photo, API delivery status/filter/metadata. Python assertions/skips не ослаблены.
- Итоговые `P11-free-empty`, `P11-free-six`, `P11-independent-viewer`, `P11-plus-two-hundred`: Chromium151/WebKit26.5 PASS. 0/6/200, 3→4→5→sixth dialog, offline и paused занимают слот, search не уменьшает общий счётчик, cancel без мутации, явная атомарная замена сохраняет остальные четыре. Direct sixth отвергается сервером.
- Open replacement dialog сохраняет исходный version: другой client меняет выбор, visibility refresh обновляет underlying state, выбор старой строки получает409 и перечитывает канонический server state. Reload сохраняет выбор; foreign selection/expired access покрыты server suite.
- Delayed POST оставляет checkbox/счётчик прежними до ACK. Пользователь переводит фокус в поиск; late ACK сохраняет его. `P11-focus-red.log` выявил BODY focus: два последовательных render отменяли первый RAF и теряли исходный snapshot. Router сохраняет pending restore при промежуточном BODY/MAIN; новый interactive focus имеет приоритет. `P11-shell`: два движка повторили nav/back/row position/late focus/double refresh/text200/auth.
- `locator.check()` на sixth было неверным QA действием: checkbox намеренно остаётся прежним до замены. Теперь обычный click и точные server/no-mutation/checked assertions; это не разрешение optimistic success. Первое наблюдение сохранено в `P11-first-browser.log`.
- UI delivery presentation limited/unavailable/preparing/unknown/photo/video/returning_photo отдельно проверена с явно simulated HTTP field; pipeline — отдельные fake-sender tests, не native Telegram. Limited честно показывает фото. Five widths/text200/whole label hit target проходят. **54 итоговых PNG**, source/backend/fixture/runner/image SHA в JSON. Picker/five/replacement/limited PNG просмотрены.
- Impeccable detector `P11-design-detector.json`: []; scoped mobile/Operate/Stop-Slop review и права/async/status review выполнены.

## Граница

Selection/effective/delivery разделены; выбранное видео не объявляется доставленным. Late state response не перезаписывает более новую video mutation. Shared capture/render/file_id, H.264/no audio/6–12–18–24 и guards не менялись. Личный предел5 не является доказательством серверной мощности; новая нагрузка — P18.

Actual animation→photo проверяется local fake pipeline, реальные Telegram получатели не использовались. Native/реальная доставка/OAuth/payments/staging новая версия **NOT TESTED**. Production и деньги не менялись, HTML/экспорт сохранены. Следующий P12 settings.
