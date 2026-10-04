# TwitchSignalBot — Roadmap развития

Статус: утверждён пользователем 2026-09-30.

Исходная точка: `6074744` — live preview отправляется как Telegram Animation.

## Важное актуальное решение по live preview

- Старый вариант до **30 секунд отменён**.
- Актуальный максимум live preview: **24 секунды**.
- Цикл: **6 → 12 → 18 → 24 → reset → 6**.
- 30-секундный cumulative preview больше не должен проектироваться, тестироваться или возвращаться в продукт.
- Качество не снижаем специально ради лимита.
- Для Telegram Animation действует защитный бюджет размера; слишком тяжёлый preview не должен превращаться обратно в обычное video.

## Принципы

- TwitchSignalBot — отдельный проект. Не смешивать с CigilBot и Media.
- Все новые функции сначала проверяются в staging через `@SignalStreamsBot`.
- Production не изменяется в автономном режиме.
- Для крупных этапов: spec → implementation plan → реализация → тесты → staging → проверка.
- Не считать локальный коммит или локальный тест выпуском.
- Не принимать догадки за факты: спорное подтверждать кодом, тестом, логами или документацией.
- Платёжного провайдера пока не выбирать и реальные платежи не подключать.

## R0 — Audit & Baseline

Зафиксировать реальное состояние кода, инфраструктуры, данных, тестов, документации и рисков.

Результат:
- baseline;
- architecture map;
- risk register;
- decisions;
- STATUS;
- подтверждённые gaps перед следующими этапами.

## R1 — Безопасная схема разработки и staging

- staging отделён от production;
- отдельные переменные/БД/секреты;
- воспроизводимый deploy;
- rollback/runbook;
- все новые функции проверяются через `@SignalStreamsBot`.

## R2 — Owner Admin Panel v1

Read-only панель владельца:
- здоровье Telegram/Twitch/preview отдельно;
- аудитория;
- сообщества;
- текущие эфиры;
- состояние preview;
- ошибки;
- очередь;
- CPU/RAM/disk и базовые operational metrics.

Первая версия — без опасных управляющих действий.

## R3 — Коммерческая техническая основа

Подготовить систему к росту без преждевременных микросервисов:
- data model для коммерческой версии;
- общие данные стрима отдельно от per-destination состояния;
- durable queue;
- ограниченная тяжёлая preview-обработка;
- нагрузочные сценарии;
- backup/restore;
- решение о PostgreSQL только по evidence.

## R4 — Streamer Plus без реальных платежей

- entitlement/access model;
- кабинет стримера;
- подключение Twitch и Telegram-сообществ;
- конструктор live-поста;
- дополнительные контролируемые кнопки;
- несколько сообществ;
- статистика пользы бота;
- тестовая выдача Plus без реальных денег.

## R5 — Payments foundation

Пока без выбора реального провайдера:
- payment interfaces;
- order/payment models;
- idempotency;
- интерфейс server-side проверки webhook; redirect не подтверждает оплату;
- mock/test provider;
- entitlement activation;
- test checkout, refund, cancel и expiry lifecycle;
- audit log.

Реальные FreeKassa / Robokassa / lava.top / Heleket или другой провайдер выбираются отдельно.

## R6 — Закрытый Streamer Plus pilot

В текущей автономной работе только staging simulation и acceptance:
- 5–10 тестовых стримеров;
- проверка подключения;
- live-post customization;
- analytics;
- Plus lifecycle;
- расходы и ошибки.

Production rollout не выполнять автономно; он требует отдельного разрешения.

## R7 — Telegram Mini App + Viewer Plus

- единый кабинет;
- серверная валидация Telegram init data;
- smart alerts по игре/категории;
- title/keyword filters;
- исключения;
- digest пропущенного;
- синхронные настройки между ботом и Mini App.

## R8 — Growth / SEO / referrals

- attribution;
- deep links;
- referral model;
- public site;
- SEO pages;
- измерение source → activation → Plus;
- anti-abuse.

## R9 — Подтверждение масштаба 20–40k

Не обещать масштаб без измерений:
- synthetic load profiles;
- notification latency;
- queue depth;
- DB latency;
- CPU/RAM/disk;
- preview concurrency;
- recovery;
- payment idempotency;
- backup/restore drill.

## Free

- личное отслеживание стримеров;
- базовые go-live уведомления;
- базовые публикации в сообществах;
- существующие полезные бесплатные функции не отнимать внезапно.

## Streamer Plus — кандидаты

- live preview;
- расширенное оформление;
- дополнительные кнопки;
- несколько сообществ;
- измеримая статистика.

## Viewer Plus — кандидаты

- правила по игре/категории;
- title/keyword filters;
- исключения;
- digest;
- дополнительные preview-возможности после проверки стоимости и правил.

## Что нельзя решать автономно

- реальный платёжный провайдер;
- реальные деньги;
- production deploy;
- production DB migration;
- удаление production данных;
- юридические/возрастные решения;
- коммерческие решения, которые требуют отдельного согласования.

## Автономный режим

Пока пользователь отсутствует:
- двигаться максимально далеко по roadmap;
- работать только в staging;
- использовать `@SignalStreamsBot`;
- каждый этап документировать;
- после завершения этапа переходить к следующему, если нет блокера;
- не останавливать работу из-за мелких технических решений, которые можно безопасно принять по roadmap;
- останавливаться только на действительно внешнем/коммерческом/production-блокере.
