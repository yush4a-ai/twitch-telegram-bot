# P02 — одна SQLite и frozen beneficiary

BASE `ac37c8ca36e228a8393276a8eefbca80d59960f9`. RED: отсутствующая migration/beneficiary/Money; отдельный RED store/new order freeze. Initial RED также выявил неправильное закрытие sqlite3 тестовой копии на Windows — исправлено через closing, assertions сохранены.

PASS28tests/5subtests: migrations, existing billing viewer/orders/lifecycle, backup. Проверены legacy TEST/order/payment/audit/grant IDs, backup copy hash, integrity/foreign keys, injected migration rollback, reopen/repeat, event/active attempt uniqueness, two-connection request race, frozen buyer, atomic order/attempt/audit rollback. Новый month rule не назначен; для проверки длины будущего периода используется явная QA duration, не 30 дней по умолчанию.

Scoped review: миграция вызывается последней внутри текущего BEGIN IMMEDIATE, не commits/executescript; readers совместимы с old mock fields. Streamer source mock backfill только по совпадающему paid TEST order/subject/plan/grant, unbound test не получает buyer из issued_by или нынешнего owner. Новые orders фиксируют server buyer; frozen beneficiary trigger запрещает перенос. BillingStore использует existing conn/_write_lock и не открывает вторую базу/ledger; transport/grant отсутствуют. Денежная duration не ограничена legacy TEST31сутки.

Ruling: storage принимает duration_seconds отдельно из будущей server-approved policy, default0 не выдаёт срок — точный месяц не утверждён — grant blocked до P06 validator/policy.

Активная staging/production DB не читалась/не менялась; оплаты/Telegram sends0. Далее P03 каталог.
