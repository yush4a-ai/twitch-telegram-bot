# R1 — план реализации безопасного staging

> Исполнение в этом чате, без отдельной CLI-сессии. Каждый программный пакет проходит red/green TDD и локальный commit.

**Цель:** Защитить staging deploy от неправильного Railway target и подтвердить backup/restore на тестовом контуре.

**Архитектура:** Чистые Python-функции проверяют Railway status и Git; CLI вызывает `railway up` только с pinned IDs после полного тестового прогона. Отдельная утилита SQLite делает online backup и неразрушающий restore drill. Настройки Railway и runbooks закрепляются после тестов.

**Стек:** Python 3.12 standard library, pytest/unittest, Railway CLI 5.63.1, SQLite WAL, PowerShell как операторский shell.

**Спецификация:** `docs/superpowers/specs/2026-09-30-r1-safe-staging-workflow-design.md`.

## Общие ограничения

- Никаких production deploy/variables/DB/data и push/merge `main`.
- Staging project/service/environment/Volume IDs берутся из R0; любые расхождения закрывают gate.
- Программный deploy только из `autonomous/twitchsignal-roadmap`, чистого Git tree и после `pytest -q -p no:cacheprovider`.
- Telegram token и другие secret values не выводятся и не попадают в файлы/логи.
- Для `/data/*` backup/verify допускается только `RAILWAY_ENVIRONMENT_NAME=staging`.

## Review Focus

1. Metadata без production environment или с одинаковым Volume ID должны останавливать deploy — `test_rejects_missing_or_shared_production_volume`.
2. Staging service с GitHub source или без healthcheck должен останавливать deploy — `test_rejects_source_or_health_drift`.
3. Dirty tree или неверная ветка не должны вызывать `railway up` — `test_git_gate_rejects_dirty_and_main`.
4. SQLite backup при WAL должен включать committed rows и не включать uncommitted — `test_online_backup_is_consistent_with_wal`.
5. Битый backup и существующий destination должны отклоняться без изменения активной DB — `test_rejects_corrupt_and_existing_backup`.

## Task 1 — pinned staging target и deploy guard

**Файлы:** создать `scripts/staging_target.json`, `scripts/staging_deploy.py`, `tests/test_staging_deploy.py`; обновить `.gitignore` (`graphify-out/`, `.pytest_cache/`).

**Интерфейсы:**

- `validate_target(status: dict, target: dict, require_health: bool = True) -> list[str]` возвращает список ошибок, без secret values.
- `validate_git(branch: str, porcelain: str) -> list[str]` проверяет ветку и чистоту.
- `build_deploy_command(target: dict, commit: str) -> list[str]` создаёт точную CLI команду с тремя ID.
- `main(argv: list[str] | None = None) -> int`: `--check` только читает, `--deploy` запускает полный suite, повторяет target check и выполняет attached upload.

- [x] Написать тесты положительного status, ошибок target, Git gate и команды deploy.
- [x] Выполнить `\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests/test_staging_deploy.py`; увидеть ожидаемый red.
- [x] Реализовать минимальные pure functions и CLI без fallback на default target.
- [x] Повторить targeted tests до green; выполнить полный suite.
- [x] Просмотреть diff, commit `feat: guard staging deploy target`.

## Task 2 — SQLite online backup и restore drill

**Файлы:** создать `scripts/sqlite_backup.py`, `tests/test_sqlite_backup.py`.

**Интерфейсы:**

- `ensure_staging_path(path: Path, environment_name: str | None) -> None` отклоняет `/data/*` вне staging.
- `backup_database(source: Path, destination: Path) -> dict[str, object]` создаёт проверенный backup без перезаписи.
- `verify_backup(path: Path) -> dict[str, object]` проверяет source и копию, восстановленную в новый временный файл.
- `main(argv: list[str] | None = None) -> int`: команды `backup --db --out` и `verify --backup`, вывод только безопасных метрик.

- [x] Написать тесты WAL snapshot, существующего destination, повреждённой DB, `/data` guard и restore drill.
- [x] Выполнить targeted tests и увидеть red по отсутствующим функциям.
- [x] Реализовать SQLite backup API, integrity gate, cleanup временных файлов и CLI.
- [x] Повторить targeted tests до green; выполнить полный suite.
- [x] Просмотреть diff, commit `feat: add staging sqlite backup drill`.

## Task 3 — staging settings, deploy и smoke

**Файлы:** создать `railway.json`, `docs/runbooks/staging-deploy.md`, `docs/runbooks/staging-backup-rollback.md`; обновить `scripts/staging_deploy.py`, его тесты, `docs/STATUS.md`, `docs/DECISIONS.md`.

- [x] Проверить `git diff`, полный suite и Railway project/environment/service/Volume metadata перед изменением конфигурации.
- [x] После подтверждённого `No changes to apply` от CLI включить только staging healthcheck `/healthz`, timeout 300 с, draining 30 с и overlap 0 через `railway.json`; пройти `--bootstrap-deploy` из чистого commit и прочитать обратно metadata.
- [x] Загрузить только `git archive` проверенного commit через `--path-as-root`: рабочая директория вызвала локальные ошибки индексации; подтвердить отсутствие ignored/untracked файлов в тесте и архиве.
- [x] Запустить обычный `scripts/staging_deploy.py --check` после bootstrap; записать SHA и Railway deployment ID.
- [x] Проверить staging `/healthz`, Railway terminal status, Telegram `getMe` из staging container без вывода токена; preview health signal недоступен без `OWNER_CHAT_ID` и отмечен как `unknown`.
- [x] Выполнить staging backup/verify через Railway SSH и записать только технические результаты без строк пользователей.
- [x] Заполнить runbooks/STATUS/DECISIONS фактическим результатом, отметить неисполненные E2E отдельно.
- [x] Провести финальный code review, `git diff --check`, targeted/full tests и документальный commit.

## Самопроверка плана

Каждое требование R1 spec имеет задачу: target/branch guard — Task 1; backup/restore — Task 2; Railway settings, smoke и runbooks — Task 3. Для пяти наиболее вероятных отказов назначены конкретные тесты. Реальный rollback активной staging DB остаётся ручной процедурой по spec; его не следует превращать в автоматическую опасную команду.
