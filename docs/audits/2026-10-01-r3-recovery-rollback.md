# R3 staging recovery, mixed-load и rollback copy

Дата: 2026-10-01. Ветка `autonomous/twitchsignal-roadmap`; production source `main`, production deploy, variables и DB не менялись. Все нагрузочные записи создавались в отдельной временной DB staging контейнера; fake sender не отправлял Telegram сообщения.

## Деплой и проверка цели

- Commit `e36b6d4`, deployment `822ef65f-2fa6-437e-90cb-6357c0e5b405`, Railway terminal `SUCCESS`.
- Guard gate перед upload: 1026 passed, 2 skipped, 268 subtests; проверены чистый commit snapshot и точные project `14282646-e318-4b80-b35d-4369270de255`, environment `7a873177-8ada-4b78-8732-a0bfdc1d519b`, service `45e46f2a-dba3-4b18-bc5f-b6fafa260055`.
- Финальный guard read `railway status --json` временно завершился GraphQL ошибкой, поэтому процесс guard вернул ошибку уже после terminal `SUCCESS`. Независимый вызов `_active_deployment_check` подтвердил `active_target_ok=true`, `errors=[]`; `/healthz` вернул 200. Тестируемое повторение временного status read добавлено в `7e4e8e4`, без отдельного deployment.
- После нагрузки `/healthz` вернул 200; `/admin/api/snapshot` без owner session — 401.

## Восстановление очереди и измерения

`scripts/staging_r3_load_recovery.py` проверяет три раунда shared samples, queue coalescing/revision, reclaim истёкшего lease и запрет устаревшего ack. Он использует fake Telegram sender, не меняет `/data/bot.db`.

| Временный профиль | Done / Failed / Pending | Пик queue depth | Sample write p95 | Drain | Completion p95 | CPU | Peak RSS | DB / WAL |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1000 destinations | 1000 / 0 / 0 | 1000 | 4,726 ms | 0,680 s | 0,727 s | 0,781 s | 173 342 720 B | 1 171 456 / 4 128 272 B |
| 5000 destinations | 5000 / 0 / 0 | 5000 | 161,069 ms | 3,883 s | 4,230 s | 4,746 s | 176 033 792 B | 4 902 912 / 4 424 912 B |

В обоих профилях: просроченная аренда повторно выдана, устаревший ack отвергнут, максимальная revision 3, `integrity_check=ok`. Рост sample write p95 в профиле 5000 требует R9 смешанной нагрузки; это не задержка реальной отправки Telegram. Локальные отдельные 20k/30k/40k, preview 1/2/4 и 10k queue профили описаны в `2026-09-30-r3-results.md`.

## Offline copy для старого R2 reader

`scripts/r3_rollback_materialize.py` создаёт новую `.offline.db` из SQLite snapshot, материализует exact shared membership в legacy `stream_samples`, проверяет конфликтующие и уже существующие строки, не перезаписывает source/output. TDD тесты: позднее подключение, повторный запуск, конфликт с отказом без output, запрет overwrite. На внешнем staging backup `2026-10-01-r3-pre-live-update-0159631.db` отдельный drill дал `integrity=ok`, `memberships=0`, `inserted=0`, `bytes=548864`. Синтетический nonzero случай покрыт тестом. Полный локальный suite после guard fix и инструмента: 1030 passed, 2 skipped, 268 subtests.

Откат активной staging DB остаётся offline процедурой: остановка writers, reconciliation незавершённых jobs, отдельная проверенная копия, исключительный доступ к Volume, запуск совместимого кода и smoke. Инструмент не автоматизирует замену активной DB. Реальная массовая Telegram рассылка, FFmpeg одновременно с fan-out и фактический owner Login Widget E2E в этих измерениях не участвовали.
