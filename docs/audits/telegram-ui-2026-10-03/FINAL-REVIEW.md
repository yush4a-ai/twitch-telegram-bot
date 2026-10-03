# Финальный read-only review и исправления

Рецензент /root/telegram_ui_final_review проверил 608acff..84c3528: 49 PASS за 66.30s, без записи checkout/Telegram transport. Critical отсутствуют; staging был отклонён до трёх Important. Второй reviewer не запускался.

| Finding | Решение и проверка |
|---|---|
| P1: channel → legacy Add → Menu теряет intent, поздний chat_shared подключает канал | Shared cancel_ui перед сменой Add/quiet/import/addfound/legacy OAuth/channel selector. Уже сохранённые связи не удаляются; разрешён только server-owned pending row при повторном открытии. RED 5 старых путей и OAuth → PASS. |
| P2: track/link cold start и cold Home callback без persistent Menu | Общая private initialization; входящее сообщение пользователя не кешируется как редактируемое меню. Fresh track/link/restart regression PASS. |
| P2: trial7 + test20 неверно показывает trial20; любой mock order меняет чужую подпись | Классификация только по конкретным contributing grant_id/связанным order. trial/test и mock/paid overlap для обоих продуктов PASS. Внешний Mini App JSON shape сохранён. |
| Minor: Help называет старую Add кнопку | «➕ Добавить оповещения», regression PASS. |
| Неоднозначное неактивное includes description | «В Streamer Plus включены все возможности Viewer Plus.»; нет утверждения уже выданных прав, negative grant test PASS. |

Scoped self-review обнаружил ещё позднее восстановление selector при channel row read/deep-link read после Menu. Оба воспроизведены отдельными RED; generation проверяется после чтения, новый intent отменяется, FSM/selector не восстанавливаются.

Исходный REVIEW-red.log: 11 failures (включая 5 subtests), 12 PASS. Импорт unittest fixture создавал 11 лишних collected tests; импорт заменён module alias, assertions не менялись. REVIEW-copy-red.log: 5 failures/6 PASS/5 subtests. REVIEW-deep-red.log: 1 воспроизведённый failure. Финальный REVIEW-pass.log: 37 PASS/7 subtests за26.50s. Единственное предупреждение — отказ Windows в записи необязательного pytest cache, тесты исполнены.

Статический network isolation gate не ослаблялся: реальные Bot constructors в двух новых fixture заменены network-free MenuClient/FakeTelegram; ISOLATION-pass.log 19 PASS.

Неоценённые reviewer gates остаются обязательными: full suite, fresh browser source snapshots, actual staging identity/artifact/SHA/payment OFF/production comparison. Native Desktop/Android/iOS, реальный OAuth/публикации/платежи NOT TESTED. Прежний перенос нижней навигации Chromium360 на две строки связан с неизменённым CSS; новая композиция вне этой задачи, owner acceptance/native остаются открытыми.
