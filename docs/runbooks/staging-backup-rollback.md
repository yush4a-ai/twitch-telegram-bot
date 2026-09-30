# Staging SQLite backup и rollback

## Online backup и проверка восстановления

Все команды ниже указывают Railway project `14282646-e318-4b80-b35d-4369270de255`, environment `7a873177-8ada-4b78-8732-a0bfdc1d519b` и service `45e46f2a-dba3-4b18-bc5f-b6fafa260055`. Скрипт дополнительно требует `RAILWAY_ENVIRONMENT_NAME=staging` для операций с `/data` и не перезаписывает существующий backup.

```powershell
$backupName = "staging-$(Get-Date -Format yyyyMMdd-HHmmss).db"
railway ssh --project 14282646-e318-4b80-b35d-4369270de255 --environment 7a873177-8ada-4b78-8732-a0bfdc1d519b --service 45e46f2a-dba3-4b18-bc5f-b6fafa260055 python -m scripts.sqlite_backup backup --db /data/bot.db --out "/data/backups/$backupName"
railway ssh --project 14282646-e318-4b80-b35d-4369270de255 --environment 7a873177-8ada-4b78-8732-a0bfdc1d519b --service 45e46f2a-dba3-4b18-bc5f-b6fafa260055 python -m scripts.sqlite_backup verify --backup "/data/backups/$backupName"
```

`backup` использует SQLite online backup API для согласованного WAL snapshot, проверяет `integrity_check` и публикует файл без перезаписи. `verify` проверяет backup и восстанавливает его **в новый временный файл**, сравнивая целостность и число таблиц. Активная `/data/bot.db` не заменяется. Подтверждение R1: `/data/backups/2026-09-30-r1-7b5862d.db`, `integrity=ok`, `restored_tables=21`, `bytes=360448`.

## Откат кода на staging

1. Проверить pinned target через `scripts.staging_deploy --check` и зафиксировать текущий deployment ID, commit, status, схему DB и причину отката.
2. Если схема совместима, подготовить на `autonomous/twitchsignal-roadmap` отдельный reviewable revert commit с прежним кодом. Пройти тесты и обычный guarded staging deploy из `staging-deploy.md`; проверить terminal `SUCCESS`, `/healthz` и `getMe`.
3. Если схема несовместима, сначала выполнить отдельное восстановление staging DB по процедуре ниже. Не запускать старый код против неизвестной новой схемы.

## Восстановление активной staging DB

Это отдельное **offline** действие. Online `verify` выше не является заменой рабочей DB. Перед восстановлением проверить ID среды и Volume, целостность выбранного backup, снять свежую копию текущей DB и обеспечить исключительный доступ к staging Volume при остановленном worker. Только затем заменить основной SQLite-файл, убедиться, что старые WAL/SHM не применятся к восстановленной DB, запустить совместимый код и пройти smoke. На текущем контуре автоматической безопасной команды для остановки worker с сохранением эксклюзивного доступа к Volume нет; операцию выполнять вручную после подготовки такого maintenance path. Production DB и Volume не участвуют.

Backup на том же staging Volume защищает от ошибочной записи, но не от потери Volume. Перед существенной migration нужен отдельный внешний staging export/snapshot и проверенный путь восстановления. Копия production DB в staging запрещена.
