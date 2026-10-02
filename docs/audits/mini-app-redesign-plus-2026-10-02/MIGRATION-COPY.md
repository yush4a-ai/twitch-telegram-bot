# P19 — backup, restore и миграция копии

Сначала штатный target validator проверил точные project/environment/service, отдельные Volume, domain, replicas1 и source. Дополнительный SSH guard сверил runtime IDs, `staging`, `/data`, `DB_PATH=/data/bot.db` и owner. Production data/vars не читались. Fresh метаданные production: deployment466499d5-6979-4a81-8aee-67efcf628976, SHA `dc9239eb0b82fb80d1788fc657205740cebc49e9`; исторический6074744 не используется как текущая версия.

## Копия

Reviewed `scripts/sqlite_backup.py` использован без изменения. Источник открыт read-only, SQLite online backup публикуется через atomic no-clobber hard link. Последовательного копирования db/WAL нет.

- Backup: `/data/backups/redesign-20261002-7e81ecf975834d82994ecca22ee66959.db`,3006464байт.
- Внешний экспорт: `C:\Users\yusha\AppData\Local\TwitchSignalBot\staging-backups\redesign-20261002-7e81ecf975834d82994ecca22ee66959.db` — вне Git. Содержит staging данные, доступ как к backup; не публиковать.
- SHA256 обоих: `c674d07159110a46f1881b1480aa14da0dc0474886e07c2beb73c5fc0698c8b7`.
- Remote/local integrity и отдельный restore drill: PASS,54таблицы. Backup сохранён, активная БД не заменялась. `P19-backup.json` содержит только безопасные агрегаты.

## Миграция

В одноразовый `/tmp` переданы только11 зависимостей `Database` из Git commit8a8a70f с manifest/SHA и allowlisted zip paths. Это isolated QA код миграции; второй бот/runtime/controller не создавался. Новый импорт доказан точным путём. Действующий Fernet key остаётся внутри staging-контейнера; две зашифрованные token fields успешно расшифрованы там без вывода значений.

На новой копии backup:

-54→60таблиц,20→25версий. Добавлены r11_001–005.
- Сохранены все прежние columns/rows/explicitIDs/counts/digests каждой таблицы, включая TEST orders/grants/audit, legacy groups, tracking/history и report data. Для schema_migrations прежние строки сохранены, новые версии разрешены отдельно.
- integrity=ok, foreign_key_check пуст; repeat/reopen не меняет канонические данные.
- Искусственный failure после `migrate_plus_payments` откатывает R11 columns/tables/versions и сохраняет прежние данные.
- Backup SHA после проверки прежний. TEMP копии/пакет удалены, network sends0, active DB replacementFalse.

`P19-migration-copy.json` содержит агрегаты и committed source hashes, без строк данных/секретов. Перед release эти11 hashes сравниваются с окончательным Git snapshot; LF bytes Git archive отличаются от CRLF отдельных файлов Windows без изменения кода.

## Восстановление после rollout

Старый binary не объявляется автоматическим rollback после новой схемы. При аварии: остановить writer в maintenance, проверить совместимость binary с новой схемой; при несовместимости восстановить проверенную backup-копию только после отдельного разрешения активной замены и оценки данных после backup. Нынешний drill не заменял active DB и не доказывает допустимость потери новых данных.

Локальная Windows `.CMD` сначала отклонила длинную команду до SSH. Причина воспроизведена, reviewed code сжат/chunked ниже8191; глобальные установки не менялись, секреты/backup payload в tool output не выводились. Фактические backup/migration PASS выше относятся к успешным вызовам.
