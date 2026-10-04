# Production admission — конкретный draft, OWNER APPROVAL: APPROVED — production cutover разрешён владельцем в текущем чате 04.10.2026.

Исходный runtime contract был подготовлен вне Git в private `TwitchSignalBotBackups/tsb-d-20261004T124953Z-3083bf6c93/production-admission-DRAFT.json`. На cutover его exact bytes были материализованы как `/data/production-admission-v1.json`, SHA256 `fad64900cf8496197ea837f4b7697a0b13695e44ed8637c18068a960e11cd5dc`, и этот contract активирован после явного разрешения владельца. Owner account ID берётся из уже проверенного значения и не повторяется в отчёте.

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
| Prepared artifact |1c49330e773ded22d47a185866dd3921b1e00806|
| Runtime / tested tooling |3fa8b649e737693eef94c3912c22313834f57586 /869009401fc638405694297b8449e04c1d96abe8|
| Rollback artifact |dc9239eb0b82fb80d1788fc657205740cebc49e9|
| Payment |off; invoices/external create false|
| Queue |lease_fenced_v1; queue_enabled=false approved for initial production; NOTIFICATION_QUEUE_ENABLED=0|
| Writer |exclusive_lock; old writers must actually STOP before new runtime admission|
| Client boundary |peer_shared_fail_safe; arbitrary Forwarded/XFF untrusted; actual shared peer acceptance unresolved|

Queue false утверждён как безопасная initial production policy; будущий queue opt-in требует отдельной явной конфигурации. Artifact прошёл pinned staging/Testbot (`b3d8be15-0b6a-4b03-a131-b339e7e78917`, full gate 1648 PASS / 2 existing Windows skips / exit0) и затем production cutover (`44fe69d3-2d89-466c-9202-fcbf56e6c05a`, SUCCESS). Payments остаются OFF.

OWNER APPROVAL: APPROVED — production cutover разрешён владельцем в текущем чате 04.10.2026.

На cutover immutable bytes JSON были проверены, admission file/hash/opt-ins установлены, exact bot identity подтверждена startup-логом. Единственное отсутствующее Railway runtime metadata (`RAILWAY_VOLUME_INSTANCE_ID`) было pinned exact verified value после fail-closed первой попытки; затем exact runtime успешно запущен.
