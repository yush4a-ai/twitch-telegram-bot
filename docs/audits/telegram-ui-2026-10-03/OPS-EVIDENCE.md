# Операционные проверки

Сохранены точные исполнявшиеся версии трёх QA helpers из TEMP. Источник — ранее проверенные P19/P20 scripts; изменены только audit paths, backup tag и upload prefix, explicit UTF-8. При повторении копировать три файла в TEMP и запускать явно из корня текущего проекта; это QA, не второй разработчик. Production доступна только read-only metadata comparison.

Backup STAGING-backup.json: consistent online SQLite backup, remote + local export/restore, SHA325ea85c970b5b567eaf09651f927e3039c99cecc2c9ed98765aed6d6942fd5a, 60 таблиц, integrity ok. Экспорт вне Git в LOCALAPPDATA; прежние резервные копии сохранены.

Migration-copy STAGING-migration-copy.json: временная изолированная копия backup, 60→60 таблиц/25 schema versions, exact all-table rows preserved, repeat reopen, injected failure rollback и 2 Fernet поля проверены действительным ключом без его экспорта; active DB не заменяется, temp copy удалена, network sends0. Source84c3528; Database/11 dependency modules не менялись последующими handler fixes. Вместо исторических проверок отсутствия таблиц/колонки на старом backup используется более строгий exact snapshot equality до/после injected failure на нынешних 60 таблицах.

Smoke guards: прежние 7 local guard tests PASS55.328s. Artifact hashes берутся из фактического committed Git archive (CRLF), отдельно normalized HTTP text; exact DENY/401/pins/money/identity assertions сохранены. Actual smoke выполняется после штатного guarded deploy. Никаких сырых логов, backup payloads, токенов и OAuth URL в evidence не сохраняется.
