# R0 — TwitchSignalBot Audit & Baseline Design

Дата: 2026-09-30
Статус: утверждено к исполнению 2026-09-30
Исходный commit: `6074744aefe2ee6a314760d86f95684733f8c05f`

## 1. Цель

R0 должен дать проверенную картину текущего TwitchSignalBot перед новой разработкой.

Мы не строим новые функции в R0. Мы отвечаем на пять вопросов:

1. Что реально работает сейчас?
2. Как устроены ключевые подсистемы и где проходят их границы?
3. Какие проблемы подтверждены кодом, тестами, логами или инфраструктурой?
4. Какие ограничения блокируют ближайшие продуктовые этапы?
5. Какой следующий самостоятельный этап можно безопасно специфицировать и реализовать?

После R0 Codex не должен угадывать архитектуру по чату. У него должен быть baseline-документ с проверяемыми ссылками на файлы, команды, тесты и окружения.

## 2. Зафиксированное намерение

Пользователь утвердил общий roadmap развития TwitchSignalBot.

Целевая последовательность:
`R0 Audit → R1 safe staging workflow → R2 owner admin panel → R3 commercial foundation → R4 Streamer Plus → R5 payments → R6 pilot → R7 Mini App/Viewer Plus → R8 growth → R9 scale validation`.

TwitchSignalBot остаётся отдельным проектом. CigilBot и Media не входят в scope.

Новые продуктовые изменения сначала попадают в staging / `@TwitchSignalTestbot`, проходят автоматическую и ручную проверку и только затем отдельно выпускаются в production.

## 3. Известная baseline-точка

На старте R0 требуется подтвердить, а не просто предположить:

- branch: `main`;
- исходный commit: `6074744`;
- Telegram framework: aiogram;
- один основной процесс совмещает Telegram polling, Twitch polling/listeners и HTTP OAuth callback;
- состояние хранится в SQLite на persistent Railway Volume;
- Twitch OAuth tokens шифруются;
- существуют live-post lifecycle, reports, EventSub/follow logic, chat listener и preview pipeline;
- live preview использует H.264 MP4 без аудио и Telegram Animation semantics;
- preview цикл ограничен примерно `6 → 12 → 18 → 24 → reset`;
- staging и production существуют как отдельные Railway environments;
- admin panel, billing subsystem и Mini App пока не считаются реализованными.

Если аудит обнаруживает расхождение, baseline-документ фиксирует фактическое состояние.

## 4. Scope аудита

### 4.1 Repository / code map

Составить карту модулей и зависимостей:

- entrypoint / dependency wiring;
- config/env;
- Telegram handlers;
- live-post lifecycle;
- Twitch API;
- OAuth/token storage;
- Twitch polling;
- EventSub/follow;
- IRC/chat activity;
- reporting/outbox;
- preview source/capture/analysis/render/runtime;
- persistence layer;
- health/diagnostics;
- tests;
- deployment/config docs.

Для каждого блока указать назначение, публичный вход, основные зависимости, долговременное состояние, внешние API, failure behavior и соответствующие тесты.

### 4.2 Runtime behavior

Проверить startup/shutdown, reconnect, stream start/update/end, duplicate prevention, Telegram rate-limit handling, Twitch transient/auth failures, preview fail-open behavior, stale-post recovery, report delivery/retry и restart recovery.

Критичный code path считается подтверждённым только при наличии теста, staging-лога или другого воспроизводимого evidence.

### 4.3 Persistence

Проверить SQLite schema и write model:

- таблицы и indexes;
- ownership данных;
- per-stream vs per-destination duplication;
- write serialization;
- long-running growth tables;
- migrations;
- transaction boundaries;
- backup/restore capability;
- WAL behavior;
- corrupted/missing DB behavior.

Отдельно отделить реальные блокеры ближайшего этапа от гипотетического масштабного долга.

### 4.4 Infrastructure

Для Railway staging и production проверить read-only:

- environment/service identity;
- public domain;
- Volume mount;
- DB path;
- healthcheck;
- replica count;
- deployment source/branch behavior;
- restart/draining settings;
- preview env;
- наличие необходимых secret names без вывода secret values.

Production secrets и production DB в staging не копировать.

### 4.5 Tests / quality gates

Зафиксировать полный test command, passed/skipped/warnings, preview-specific suite, integration tests и пробелы в coverage критичных потоков.

В R0 обнаруженные баги не исправляются. Они оформляются как findings с evidence и отдельным recommended action.

### 4.6 Documentation drift

Сверить README, `.env.example` и deploy instructions с реальным кодом.

Особое внимание:

- preview status;
- Railway behavior;
- команды;
- environment variables;
- backup instructions;
- staging vs production workflow.

R0 может менять документацию, если это необходимо для корректной фиксации baseline. Product behavior не меняется.

### 4.7 Security / privacy / abuse surface

Проверить архитектурно:

- owner access;
- token encryption;
- secret handling;
- permission checks;
- user-controlled callback/deep-link inputs;
- logs and PII;
- наличие/отсутствие Mini App auth;
- отсутствие или наличие payment surface;
- destructive admin actions.

Intrusive security testing против внешних сервисов не проводить.

### 4.8 Scaling evidence

Подтвердить кодом текущие потенциальные ограничения:

- SQLite single-connection/write lock;
- Telegram fan-out strategy;
- poll-cycle coupling;
- preview capture concurrency;
- preview job concurrency;
- per-recipient duplicated stream samples;
- one-replica assumptions;
- persistent Volume limitations.

R0 не утверждает, выдержит или не выдержит система 20–40k пользователей без load testing. Он только формирует измеримые hypotheses.

### 4.9 Product readiness gaps

Проверить наличие/отсутствие:

- owner admin API/UI;
- user/account model;
- entitlement model;
- billing/payment records;
- Mini App auth;
- attribution/referral model;
- public site/SEO;
- audit log для коммерческих действий.

Результат — gap map, не реализация.

## 5. Deliverables

R0 создаёт и коммитит только документацию.

### `docs/audits/2026-09-30-baseline.md`

Основной доказательный отчёт:

- current commit;
- architecture map;
- runtime map;
- data map;
- infrastructure map;
- test baseline;
- confirmed findings;
- documentation drift;
- verified constraints;
- unknowns.

### `docs/audits/2026-09-30-risk-register.md`

Для каждого риска:

- ID;
- severity: blocker / high / medium / low;
- evidence;
- affected roadmap stage;
- recommendation;
- fix now или defer.

Severity отражает влияние на roadmap, а не эмоциональную оценку.

### `docs/DECISIONS.md`

Только решения, уже подтверждённые пользователем или доказанные технически. Для каждой записи: дата, решение, причина, последствия и условие пересмотра.

### `docs/STATUS.md`

Кратко: baseline commit, текущий этап, завершённые проверки, открытые blockers и следующий шаг.

### README update

Только подтверждённые исправления документации. Никаких обещаний ещё не реализованных функций.

## 6. Формат finding

Каждый finding обязан иметь:

- **ID**: например `R0-DB-001`;
- **Statement**: одна конкретная проблема или ограничение;
- **Evidence**: file path + symbol/line или точная команда/лог;
- **Impact**: пользовательский или операционный риск;
- **Roadmap relevance**: какой этап затронут;
- **Action**: fix before next stage / defer / measure;
- **Confidence**: confirmed / likely / unknown.

Нельзя писать общие выводы вроде «архитектура плохая» или «нужно всё переписать» без разложения на доказуемые findings.

## 7. Non-goals

R0 НЕ включает:

- новую админ-панель;
- PostgreSQL migration;
- queues/workers implementation;
- payment provider integration;
- entitlement code;
- Mini App;
- SEO site;
- user-visible redesign;
- production schema migration;
- изменение тарифов;
- удаление production данных;
- изменение коммерческой политики Twitch/Telegram.

Если найден баг, создающий непосредственный production incident, аудит останавливается и баг оформляется как отдельная bounded bugfix task с собственным TDD-процессом.

## 8. Read-only и safety rules

Во время R0 разрешено:

- читать repository;
- запускать локальные tests;
- читать Git history;
- читать Railway status/config names/logs;
- проверять public health endpoints;
- читать DB schema и агрегированные технические данные без вывода секретов или личных данных;
- писать и коммитить audit documentation.

Запрещено без отдельного разрешения:

- deploy;
- product-code changes;
- менять Railway variables;
- менять базы;
- отправлять массовые Telegram сообщения;
- запускать реальные платежи;
- копировать production DB;
- удалять live posts;
- менять production behavior.

## 9. Audit execution order

1. Freeze baseline: git/status/commit/test command.
2. Repository architecture map.
3. Runtime/lifecycle flows.
4. Database/schema/write model.
5. Preview subsystem.
6. Telegram delivery/fan-out.
7. Twitch OAuth/EventSub/chat dependencies.
8. Reports/outbox/recovery.
9. Railway staging/production read-only verification.
10. Tests and documentation drift.
11. Security/privacy surface.
12. Scaling hypotheses.
13. Product readiness gap map.
14. Risk register and decisions.
15. Final consistency review.

Сначала фиксируются факты, затем выводы.

## 10. Acceptance criteria

R0 считается принятым, когда:

- baseline commit однозначно зафиксирован;
- все основные подсистемы описаны;
- критичные lifecycle flows имеют evidence;
- staging и production явно различены;
- полный test baseline записан;
- DB/backup/recovery состояние проверено;
- все findings имеют evidence;
- unknowns отделены от confirmed facts;
- README drift перечислен и безопасные расхождения исправлены;
- нет product-code изменений;
- нет deploy;
- risk register приоритизирует проблемы относительно roadmap;
- выбран следующий самостоятельный этап для новой спецификации.

## 11. Что идёт после R0

После принятия R0 мы не начинаем всё сразу.

Следующий этап выбирается по findings. По текущему roadmap ожидаемый кандидат — **R1 Safe Development/Staging Workflow**, затем **R2 Owner Admin Panel v1**.

Для следующего этапа создаётся самостоятельная architectural spec и implementation plan.

## 12. Ожидаемый формат отчёта Codex

Верхний краткий итог:

- baseline;
- что проверено;
- сколько confirmed findings;
- blockers;
- что можно отложить;
- рекомендуемый следующий spec.

Ниже — полный документ с evidence.

Codex не должен автоматически исправлять найденные проблемы в том же проходе.
