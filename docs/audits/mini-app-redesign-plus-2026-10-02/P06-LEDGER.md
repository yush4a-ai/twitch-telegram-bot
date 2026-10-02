# P06 — durable lifecycle, callback и сверка

BASE `93461aa`; ветка `autonomous/twitchsignal-roadmap`. Scoped ledger/security review выполнен одним исполнителем.

## Изменения

Attempt сохраняется до create, inbox до ACK. Общий apply проверяет frozen buyer/product/money/method/provider, атомарно сохраняет финансовый факт, grant и audit. Дубликаты не продлевают срок. Старый pending не заменяет confirmed; противоречивые terminal observations сохраняются для ручной проверки. Extra transaction сохраняется, но не выдаёт второй grant и не отзывает чужой.

Четвёртая миграция той же БД добавляет checkout URL, inbox scheduling, quarantine и shared worker lease. Два соединения не создают второй денежный order по другому ключу/методу и не выполняют второй refund POST при unresolved outcome. Cancelled create/refund сохраняет unknown и пробрасывает cancellation.

Reconcile: общий lease concurrency1, максимум10 за tick, timeout5s, до8 GET с bounded backoff и Retry-After. Cancellation снимает attempt/global leases. Inbox4096 nonterminal, body8192: overflow не получает ACK, уже сохранённый duplicate получает ACK без нового места. Факты не вытесняются. Callback монтируется только явным local-contract flag и loopback; обычный OAuth server оставляет reserved route404.

Refund accepted/manual/unknown не равны completed. Только canonical refunded отзывает собственный paid grant при готовой policy; независимый Viewer сохраняется. Месяц/terms/XTR настоящего каталога не утверждены: runtime OFF, grant из заглушки отсутствует. 30_days/terms в тестах — явно заданная fixture policy, не решение за владельца.

## Доказательства

- RED: отсутствующий lifecycle, две DB/different keys, shared worker/cancellation и monotonic financial facts; журналы `P06-*-red.log` и `P06-red.log`.
- Итоговый scoped набор: **72 passed, 57 subtests passed, 54.58s**, exit0; `P06-pass.log`. Lifecycle/callback + legacy billing/catalog/migrations/Platega/subscription/backup/connection compatibility.
- Fake sender/transport, новая временная БД; trigger failure/rollback, reopen, callback-before-create-response, duplicate ACK/apply, chargeback-before-confirmed, late confirmation, refund/foreign/mismatch, default OFF, lease/max10/429/max8/cancellation проверены.
- Первый прогон выявил неправильное имя refund actor column; исправлен код, assertions не ослаблены. Ошибочный путь отдельного тестового файла дал no-tests, не принят за gate.

Реальные Platega/Stars/Telegram/OAuth/native NOT TESTED. Railway/staging/production не изменены. Полный suite финального snapshot — P19. Следующий P07.
