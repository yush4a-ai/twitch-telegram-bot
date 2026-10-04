# Перезапуск и повторы входящих Telegram updates

Официальный [Bot API](https://core.telegram.org/bots/api#getupdates): update подтверждается следующим getUpdates offset выше его ID; Telegram хранит pending максимум24ч. Это at-least-once входящий транспорт в пределах retention, не exactly-once действия.

В установленном aiogram `_listen_updates` меняет offset после yield, default `_process_update` ловит ошибку и считает update обработанным. `ReplayDispatcher` сохраняет received в SQLite **до yield/offset**, затем атомарно резервирует processing перед handler. Асинхронные handlers сохраняются (32 concurrent максимум): долгий OAuth не блокирует «Меню»/отмену. Ошибка сохранения останавливает polling до ACK. Бот ID входит в ключ.

| Тип | Повтор после завершения | Падение/ошибка во время действия |
|---|---|---|
| /start, команды, text FSM, callbacks, chat_shared/requestChat, my_chat_member | done tombstone: без второго handler | processing→unknown: автоматический повтор запрещён; оператор смотрит сохранённый update и текущие domain rows, пользователь отправляет новое действие |
| OAuth command/continuation | та же ingress граница; текущие generation/intent/expiry guards сохранены | unknown не продолжает старый MemoryStorage FSM; новый /start/Меню отменяет черновик и открывает новый путь. HTTP OAuth callback использует прежний one-time state, не Telegram offset |
| successful_payment / refunded_payment | done tombstone; durable billing ledger сохраняет собственную idempotency по charge/event | received/processing повторяется через прежний ledger; новые order/grant не выдумываются |
| pre_checkout_query | guarded ordinary; отдельное ограниченное время ответа | после crash ответ может быть пропущен/просрочен, права не выдаются; first release всегда deny, invoice OFF |
| Остальные registered updates | обычная ingress граница | unknown; другие финансовые routers не зарегистрированы |

На startup незавершённые обычные processing переводятся в unknown, финансовые в received до запуска новых handlers. Received восстанавливаются параллельно вместе с polling с общим бюджетом32, чтобы старый OAuth не блокировал сохранённое «Меню». Сбой финансового recovery останавливает запуск, pending не выкидываются. Dispatcher отменяет и дожидается owned polling/recovery/stop/handler tasks до закрытия БД. Это bounded guard старых команд: нет обещания атомарности SQLite+Telegram send и нет автоматического угадывания опасного второго действия.

Done payload очищается сразу; tombstone IDs сохраняются (без удаления по MAX(update_id), который Telegram может сбросить после недели простоя). Received/unknown payload зашифрован существующим Fernet key на Railway; локально без ключа остаётся в локальной тестовой БД. Не выводить payload в логи/отчёт/Git. Unknown требует operator review; перед cutover проверить count/status без личных данных, не повторять сырые callbacks массово. При изменении политики retention нужен отдельный contract: удаление tombstones снимает защиту от старого ручного replay.

Money OFF сохраняется: финансовые факты не включают provider transport/invoices; payment ledger не заменён этим inbox.
