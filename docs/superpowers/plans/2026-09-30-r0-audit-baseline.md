# R0 — план аудита и базовой карты

**Цель:** Зафиксировать проверяемое состояние TwitchSignalBot на `b4898a9` относительно production-кода `6074744` и определить безопасный объём R1.

**Подход:** Использовать исходники, тесты и только чтение Railway. Каждый вывод получает ссылку на код, тест или наблюдение. В R0 изменяется только документация.

**Стек:** Python 3.12, aiogram, aiosqlite/SQLite, Railway CLI, pytest.

**Спецификация:** `docs/superpowers/specs/2026-09-30-twitchsignal-r0-audit-baseline-design.md`.

## Общие ограничения

- Production Railway, данные и secrets не изменять; `main` не отправлять в remote.
- Не считать локальные тесты или HTTP `/healthz` доказательством пользовательского E2E.
- Разделять подтверждённые факты и неизвестное; не заявлять масштаб 20–40 тыс. без R9.
- Исправлять только документальные расхождения; продуктовый код оставить без изменений.

## Проверки, которые легко упустить

- Отдельные Volume ID при одинаковом `DB_PATH` доказывают физическую изоляцию файлов, но не различие Telegram/Twitch credentials.
- HTTP `200` на `/healthz` не доказывает исправность preview и исходящей доставки Telegram.
- `pytest` может пропустить интеграции из-за отсутствующих FFmpeg/streamlink.
- Railway `source.repo` не раскрывает полностью политику auto deploy; ветка текущего production deployment и эффект push `main` проверяются отдельно.
- SQL `CREATE TABLE IF NOT EXISTS` не версионирует схему; миграционный и backup-процесс оценивается отдельно.

## Пакеты

### 1. Исходная точка и окружения

- [x] Сверить `git status`, `git log`, remote и ветки.
- [x] В режиме чтения проверить Railway environment/service, deployment metadata, domains, Volume IDs, конфигурационные имена и `/healthz`.
- [x] Занести доказательства и неизвестное в `docs/audits/2026-09-30-baseline.md`.

### 2. Код, данные и тесты

- [x] Составить карту модулей и критичных потоков по исходникам и тестам.
- [x] Проверить модель SQLite, poll cycle, Telegram fan-out, preview concurrency, health и recovery.
- [x] Выполнить полный `\.venv\Scripts\python.exe -m pytest -q` и записать итог с warning/skips.
- [x] Оформить findings и приоритеты в `docs/audits/2026-09-30-risk-register.md`.

### 3. Документация и закрытие R0

- [x] Исправить подтверждённый дрейф README и `.env.example`; уточнить R5/R6 в roadmap согласно текущему разрешению пользователя.
- [x] Создать `docs/DECISIONS.md` и `docs/STATUS.md`.
- [x] Проверить отсутствие незаполненных мест, противоречий и изменений продуктового кода; просмотреть diff.
- [ ] Коммитить только документы в ветку `autonomous/twitchsignal-roadmap`.

## Критерий перехода

R1 можно начинать после завершения документального R0, когда production branch подтверждена, staging описан отдельно, тестовый baseline записан, а backup/rollback и окружение staging указаны как проверяемые задачи R1.
