# Production admission первого выпуска — деньги OFF

Это локальная реализация допуска, не разрешение на deploy. Первоначальный template ниже сохранён как контрактная схема; актуальный known-values draft подготовлен отдельно: 2026-10-04-production-owner-admission-draft.md, OWNER APPROVAL PENDING. Runtime JSON вне Git, секретов не содержит. Старый Railway production без opt-in сохраняет выключенные новые поверхности; staging guards не сняты. Growth/site/referral остаётся отдельным staging-only контуром.

Оператор после отдельного разрешения получает подтверждённые metadata и сохраняет JSON **вне repository**, например в read-only operator/config mount. Задаёт PRODUCTION_PRODUCT_ENABLED=1, PRODUCTION_ADMISSION_FILE=absolute_path, PRODUCTION_ADMISSION_SHA256=hash exact bytes. Файл читается один раз в frozen ProductionAdmission. Template ниже намеренно невалиден до OWNER INPUT.

```json
{
  "project_id": "OWNER INPUT", "environment_id": "OWNER INPUT",
  "environment_name": "OWNER INPUT", "service_id": "OWNER INPUT",
  "volume_id": "OWNER INPUT", "volume_instance_id": "OWNER INPUT",
  "mount_path": "OWNER INPUT", "db_path": "OWNER INPUT",
  "public_url": "OWNER INPUT", "bot_id": "OWNER INPUT",
  "bot_username": "OWNER INPUT", "owner_chat_id": 425785231,
  "replica_count": 1, "writer_policy": "exclusive_lock",
  "queue_policy": "lease_fenced_v1", "queue_enabled": false,
  "payment_policy": "off", "login_client_policy": "peer_shared_fail_safe"
}
```

Phase1: exact project/envID+name/service/volumeID+instance/mount/path/PUBLIC_URL/owner/admin-widget из Railway env должны совпасть с file. Не использовать исторические candidate IDs без двух независимых подтверждений. Shared project/service с staging допустимы: окружение и Volume должны отличаться. Production testbot8859004067/username, staging env/instance/domain запрещены.

Пути normalized POSIX absolute, DB строго внутри Volume. Перед getMe: существующий source DB, настоящий mount, без symlink-path escape, read/write permissions. Ключ Fernet валиден; TELEGRAM_BOT_TOKEN prefix соответствует bot_id, required Twitch/owner/admin/PORT present, пустые/placeholder запрещены. Валидный key ещё не доказывает decryption старых tokens: D отдельный gate.

Параметры оператора: PRODUCTION_REPLICA_COUNT=1; PRODUCTION_WRITER_POLICY=exclusive_lock; PRODUCTION_PAYMENT_POLICY=off|external_admitted (ровно два значения: `off` закрывает банковский канал СБП/карта, `external_admitted` разрешает его — ключи, объявленный транспорт и `BILLING_ALLOW_EXTERNAL` при этом всё равно обязательны); PRODUCTION_QUEUE_POLICY=lease_fenced_v1; PRODUCTION_LOGIN_CLIENT_POLICY=peer_shared_fail_safe; PRODUCTION_QUEUE_ENABLED=0|1 должен совпасть с contract.queue_enabled и NOTIFICATION_QUEUE_ENABLED. Отдельный queue opt-in не снимает необходимость getMe. Реальная replica count подтверждается infra preflight: environment assertion не является чтением Railway control plane.

Phase2: только Bot client/getMe; exact numeric ID **и exact username**, после чего exclusive OS advisory lock на Volume, затем Database construction/connect/migrations. При несовпадении client закрывается, DB/menu/commands/worker/routes отсутствуют. Новый config получает production_admitted только после проверки. Второй экземпляр этого artifact на том же Volume не получает writer lock; старый artifact не умеет этот lock, поэтому STOP всех старых writers остаётся обязательным maintenance gate.

Notification worker использует прежние fenced attempt/lease/recovery/unknown policies. Нет автоматического включения денег: first_release_payment_policy offline, allow_external_create=false, allow_invoice=false; prepare unavailable без orders/grants; live callback/transport не подключаются при случайных credentials. Production не получает mock checkout/trial: pinned_staging=false и billing_test_enabled=false.

Proxy preflight: единственный разрешённый вариант первого contract — peer_shared_fail_safe. Admin/streamer Login Widget считают request.remote; X-Forwarded-For/Forwarded не меняют ownership. Ожидается Railway edge/TLS proxy, но production chain/peer IP пока OWNER INPUT. Общий NAT/proxy делит4 pending states/300s; при capacity429, чужие valid states не вытесняются. Оператор проверяет remote boundary двумя разрешёнными браузерами/сетями; если неудобно — STOP release login acceptance и отдельный TDD-план trusted proxy или signed client ownership. Не включать доверие заголовкам без pinned proxy hop/ACL доказательств. SEC-03 isolation сохранён.

Это admission safety, не автоматическая доказанная готовность production storage, реального ключа, backup, native или maintenance. Любой missing/mismatch → ConfigError startup STOP. Никаких Railway конфигурационных операций этот contract не выполняет.
