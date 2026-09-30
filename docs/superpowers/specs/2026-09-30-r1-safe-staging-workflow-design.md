# R1 — безопасная разработка и staging: спецификация

Дата: 2026-09-30. Статус: утверждённый roadmap, локальная реализация разрешена пользователем. Основание: `docs/audits/2026-09-30-baseline.md`, `docs/audits/2026-09-30-risk-register.md`, коммит R0 `3071f00`.

## 1. Цель и границы

R1 даёт воспроизводимый путь **только** для Railway `staging` и `@TwitchSignalTestbot`: проверяемый источник кода, явный target, тестовый gate, deployment health, резервная копия SQLite, проверка восстановления и инструкция отката. Production окружение, его DB, variables и deployment не изменяются; push `main` не выполняется.

Критерий успеха: инженер может из этой ветки запустить один staging deploy, заранее увидев commit/diff/tests/target, затем доказать статус deployment и `/healthz`, получить отдельное подтверждение bot identity, и выполнить backup/restore drill без затрагивания активной DB.

## 2. Подходы и выбор

1. **Рекомендуемый: CLI deploy с guard и pinned target.** Staging сейчас уже принимает CLI deployments, а production связан с GitHub `main`. Python preflight проверяет Git, Railway metadata, изоляцию Volume и настройки, запускает тесты и вызывает `railway up` только с явными `--project`, `--environment`, `--service`. Плюсы: не требует менять production source, fail-closed при дрейфе ID. Минусы: локальный оператор/хост остаётся частью процесса.
2. Staging GitHub branch auto deploy. Удобнее после зрелого CI, но добавляет ещё один source/branch trigger и требует отдельного контроля secrets/approval. В R1 не нужен.
3. Только ручной runbook. Меньше кода, но нет машинной защиты от ошибочного target и грязного дерева; недостаточно при прямом риске `R0-OPS-001`.

## 3. Pinned target и deploy gate

Создать `scripts/staging_target.json` с Railway project ID `14282646-e318-4b80-b35d-4369270de255`, service ID `45e46f2a-dba3-4b18-bc5f-b6fafa260055`, staging environment ID `7a873177-8ada-4b78-8732-a0bfdc1d519b`, staging Volume ID `f1e0d4c5-c190-4989-ae34-29a61f9010bb` и production environment/Volume ID только для отрицательной проверки. Эти ID не являются credentials. Изменение target требует осознанного изменения reviewable файла; внезапное несовпадение останавливает deploy.

`scripts/staging_deploy.py` получает статус через `railway status --json`, не читает и не печатает secret values. `validate_target(status, target)` обязан проверить:

- точное совпадение project, staging environment, service, staging Volume и mount `/data`;
- наличие отдельного production environment и **отличного** Volume ID;
- staging service не подключён к GitHub repo; активный production source — `yush4a-ai/twitch-telegram-bot`/`main`;
- staging replicas = 1, `healthcheckPath=/healthz`, `healthcheckTimeout >= 180`, `drainingSeconds >= 30`, overlap отсутствует или равен 0;
- активный staging deployment не помечен failed.

При неполных/неожиданных metadata результат — отказ до upload. Режим `--check` печатает только commit, branch и безопасные target identifiers; `--deploy` дополнительно требует branch `autonomous/twitchsignal-roadmap`, чистое tracked/untracked Git tree, успешный полный `pytest -q`, повторную проверку target сразу перед `railway up`, затем attached deploy до terminal `SUCCESS`. Исключения: `.gitignore` скрывает только локальные артефакты (`graphify-out/`, pytest cache, `.env`, DB, логи и `.venv`); `--no-gitignore` не используется. Сообщение deployment содержит SHA commit. Никакого fallback к linked/default environment и никакой команды push.

Перед `--deploy` оператор видит `git diff` и тесты в этом чате. Скрипт не создаёт новый Railway project/service при отсутствии связи; при неуспехе не применяет авто-rollback к DB.

## 4. Настройки staging

Сначала попробовать `railway environment edit` с явными `--project`, `--environment staging`, `--service-config worker` для staging `deploy.healthcheckPath=/healthz`, `deploy.healthcheckTimeout >=180`, `deploy.drainingSeconds=30`, `deploy.overlapSeconds=0`. CLI 5.63.1 сообщил `No changes to apply`, а чтение конфигурации подтвердило отсутствие новых полей. Поэтому первый deploy использует `railway.json` только с `environments.staging.deploy` и отдельный `--bootstrap-deploy`: он допускает строго текущее отсутствие health/drain, проверяет точное содержимое файла, pinned target, чистую ветку и полный suite. После него обычный `--check`/`--deploy` требует увидеть настройки в metadata активного deployment. Production settings не редактировать. Config as Code Railway объявил устаревающим с 2026-12-01; до этой даты перейти на Infrastructure as Code или settings API после проверки изоляции окружения. Источник: [Railway Config as Code](https://docs.railway.com/config-as-code/reference).

Railway выполняет healthcheck при запуске нового deployment, а не постоянный мониторинг; сервис с Volume имеет небольшой простой при redeploy. Эти ограничения не заменяют внешнее наблюдение и не означают, что Telegram/preview исправны. Источники: [Railway healthchecks](https://docs.railway.com/deployments/healthchecks), [Railway volumes](https://docs.railway.com/volumes), [Railway CLI up](https://docs.railway.com/cli/up).

## 5. Backup и восстановление

Создать `scripts/sqlite_backup.py` с двумя безопасными командами:

- `backup --db SOURCE --out DEST`: использовать `sqlite3.Connection.backup()` для live SQLite/WAL snapshot во временный файл рядом с DEST, выполнить `PRAGMA integrity_check`, закрыть соединения и атомарно опубликовать DEST. Не перезаписывать существующий DEST; ошибку временного файла очистить.
- `verify --backup FILE`: открыть backup в read-only режиме, проверить `integrity_check`, затем восстановить через SQLite backup API в **новый** временный DB-файл и проверить его integrity и таблицы. Активную `DB_PATH` не перезаписывать.

На Railway запускать эти команды только через `railway ssh --project <ID> --environment staging --service worker` с явным staging guard в самом скрипте (`RAILWAY_ENVIRONMENT_NAME=staging` для `/data/*`). Бэкап в том же Volume нужен для drill и краткого rollback, но не защищает от потери самого Volume. Перед будущей существенной migration нужен внешний snapshot/export staging и отдельное подтверждение срока хранения; R1 не копирует production DB.

При восстановлении реального staging состояния остановить единственный staging process, убедиться в backup integrity, сохранить текущий DB отдельным backup, затем выполнить offline replace и перезапустить предыдущий код. Это ручной runbook с проверкой target; автоматический destructive restore не включать. Application rollback — повторный staging deploy проверенного commit (или Railway rollback UI) после сравнения схемы. Код старого release не запускать на несовместимой новой схеме без соответствующего backup.

## 6. Проверки и наблюдаемость

TDD для `validate_target` и backup/verify: неправильный project/env, одинаковый Volume, GitHub source у staging, отсутствие healthcheck, две replicas, dirty branch; WAL backup с незакоммиченными изменениями, повторный DEST, битый backup и восстановление в отдельный файл. Полный suite после изменений.

После staging deploy: `railway status --json` показывает target `staging`, success, 1 replica, ожидаемый commit в CLI message; `GET https://worker-staging-2f74.up.railway.app/healthz` отвечает 200 `ok`; Telegram `getMe` из staging container возвращает `TwitchSignalTestbot` без вывода token. Preview health проверять отдельно через доступный безопасный snapshot/log, не выводя PII/secret; если нет канала для read-only snapshot, отметить `unknown`, не выдавать `/healthz` за preview E2E.

Telegram E2E с исходящим сообщением допустим только на заранее подтверждённом тестовом chat ID. Если такого ID безопасно установить нельзя, R1 заканчивается честным smoke API/health и оставляет пользовательское E2E открытым для R6; отсутствие recipient не даёт права писать случайному staging пользователю.

## 7. Документы и acceptance

Добавить `docs/runbooks/staging-deploy.md` и `docs/runbooks/staging-backup-rollback.md`. Обновить `docs/STATUS.md` и `docs/DECISIONS.md` после живой проверки. До staging изменения: spec self-review без незаполненных мест/противоречий, implementation plan, TDD, code review, `git diff`, полный suite. Никаких production действий и реальных платежей.

R1 принят, когда guard проходит synthetic negative tests, staging settings реально отражены в Railway, backup и restore drill пройдены на staging или явно задокументирована техническая причина, `railway up` привязан к commit и target, а smoke evidence записан. Только после этого начинать R2.
