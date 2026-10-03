# Приёмка T4–T8

## Проверено локально

| Сценарий | Результат | Доказательство |
|---|---|---|
| Новичок / возвращающийся пользователь | Welcome или compact Home; неизвестный эфир не выдаётся за offline | T4-complete.log |
| 42 отслеживаемых, 2 оповещения | Раздельные честные счётчики, 8/page и поиск | T4-complete.log / T3-final-focused.log |
| Menu после незавершённого шага | Отмена owned selector, сохранённый канал остаётся | T2-final-GREEN.log / T5-final.log |
| Добавить / импорт | Явное подтверждение, отмена, add ещё; чужой actor не пишет/не очищает draft | T5-final.log |
| Канал / права | Короткая approved photo + подробности; свежая проверка post-right и notify отдельно | T5-final.log |
| Тихие часы | Местное preview/UTC, nonce+actor+chat+expiry, отмена и UTC change блокируют save | T5-final.log |
| Отчёт Free | Начало/конец сохранённого эфира UTC, incomplete marker; full HTML сохраняется | T6-GREEN.log / T6-focused-final.log |
| Тариф / оплата | Canonical150/300 и пять offline+live slots; paymentOFF до checkout, нет денег/grants | T6-final.log / test_telegram_plus |
| Фильтры / категории | Точные games/keywords/exclusions и стабильная category смена | T6-final.log |
| Browser Mini App | T0 Chromium/WebKit232checks/58PNG каждый;18web files byte identity на текущем коде | BROWSER-IDENTITY.json |
| Backup/restore | Новая stage-only копия, integrity ok/63tables; local export/restore | STAGING-backup.json |
| Migration-copy | Переиспользован PASS с byte identity13schema/cipher modules, не свежий прогон | MIGRATION-IDENTITY.json |

Проверки выше используют настоящие handlers/router/FSM/SQLite и fake external sender. Они не доказывают реальную Telegram-доставку или прохождение OAuth.

## Native и внешняя приёмка: NOT TESTED

На попытке чтения Telegram открыта другая переписка, окно перекрыто работающими приложениями владельца. Ввод и отправки не выполнялись, посторонняя переписка в evidence не сохранялась. Нет screenshot новых Telegram экранов. iOS/Android, owner OAuth, signed-live Mini App, реальные адресные оповещения и отчёты остаются NOT TESTED до разрешённой проверки владельцем.

После release владелец может проверить: «Меню» → welcome/home; добавить стримера и отменить повтор; поиск/страницы; канал → «Как подключить?» → Cancel; readiness с выключенными постами; quiet preview → Cancel → Save; help guides; tariff → способ оплаты → честный отказ; ручной Free отчёт и HTML. OAuth проходить только самому владельцу разрешённого аккаунта. Права и subscriptions текущих аккаунтов ради QA не меняются.

Production/push/merge/real payment/provider POST не входят в эту приёмку. Полный suite и exact deployment identity — обязательный отдельный финальный gate.
