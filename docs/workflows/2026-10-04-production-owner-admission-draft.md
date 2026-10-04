# Production admission — конкретный draft, OWNER APPROVAL: PENDING

Файл runtime contract подготовлен вне Git в private `TwitchSignalBotBackups/tsb-d-20261004T124953Z-3083bf6c93/production-admission-DRAFT.json`. Он НЕ установлен в production и не является owner approval. Owner account ID берётся из уже проверенного значения и не повторяется в отчёте.

| Поле | Предложенное значение |
|---|---|
| Bot | @TwitchSignalBot /8707370390 |
| Project |14282646-e318-4b80-b35d-4369270de255|
| Environment |production /af6d873b-a2cf-45aa-be42-cd9efbd102a7|
| Service |worker /45e46f2a-dba3-4b18-bc5f-b6fafa260055|
| Volume |9afd2204-881d-41af-bfd8-ad395b9c9ca9|
| Volume instance |eded4a6e-c2c1-44ab-a238-b3c860632cee|
| Mount / DB |/data / /data/bot.db|
| PUBLIC_URL / PORT |https://worker-production-cee5.up.railway.app /8765|
| Replica |exactly1; current manifest1/RUNNING1|
| Prepared artifact |557244dd01c8e98d6d8bc8cdf5927f052b630a93|
| Runtime / tested tooling |3fa8b649e737693eef94c3912c22313834f57586 /869009401fc638405694297b8449e04c1d96abe8|
| Rollback artifact |dc9239eb0b82fb80d1788fc657205740cebc49e9|
| Payment |off; invoices/external create false|
| Queue |lease_fenced_v1; proposed queue_enabled=false pending owner decision; NOTIFICATION_QUEUE_ENABLED must match|
| Writer |exclusive_lock; old writers must actually STOP before new runtime admission|
| Client boundary |peer_shared_fail_safe; arbitrary Forwarded/XFF untrusted; actual shared peer acceptance unresolved|

Queue false — безопасный предложенный initial policy, не молчаливое owner approval/включение очереди. Возможность первого production выпуска с этой policy подтверждает владелец; новый queue opt-in требует отдельной явной конфигурации после approval. Artifact выше не опубликован даже на staging: upload blocked. Нельзя заменять его прежним tested staging runtime.

OWNER APPROVAL: PENDING

После owner approval и готовности cutover оператор сверяет immutable bytes JSON, фиксирует SHA256 и устанавливает admission file/hash/opt-ins согласно production-admission-contract. Сейчас variables/mount ничего не меняются.
