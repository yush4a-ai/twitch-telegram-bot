# Готовность TwitchSignalBot к основному боту

Дата: 04.10.2026. Проверяемый source HEAD: `3dc9aad3d64284940fd58386b2ffd01f23b6e8dc`, ветка `autonomous/twitchsignal-roadmap`.

## Решение

**Текущий тестовый продукт работает. Перенос всего продукта в основной бот пока не готов.** Реализация R0–R9, P01–P21 и Telegram T0–T8 сохранена. Остались конкретные исправления и проверки выпуска.

Аудит не изменяет продукт, конфигурацию, данные или deployment. Production не затронут. Реальные платежи, OAuth владельца, новые сообщения в Telegram и native управление не выполнялись.

## Проверенная версия и доказательства

- Testbot: `@TwitchSignalTestbot`, ID `8859004067`.
- Активный deployment: `5211c9f8-6a2f-4834-9aa6-88706a4a34a7`, runtime SHA `39bc82171114a0a6b20ad4251bd68c4cc2d3f3f0`.
- [Mini App](https://worker-staging-2f74.up.railway.app/app). Прямой browser-вход не заменяет подписанную Telegram-сессию.
- Свежая проверка [STAGING-READ-ONLY.json](STAGING-READ-ONLY.json): 195 runtime-файлов и 17 HTTP-assets совпали с опубликованным commit; правильные бот, deployment и меню; 27 schema versions, integrity/FK без ошибок; девять неподписанных API-запросов получили 401.
- System MenuButton — `web_app`, «Приложение». Владелец наследует общий MenuButton через `default`; это допустимый контракт, не ошибка.
- Полный suite опубликованного snapshot: **1570 passed, 2 исходных Windows symlink skips, 3646 subtests**, 1004,99 с. [Лог](../telegram-menu-recovery-2026-10-04/FULL-39bc821-PASS.log). Он переиспользован после подтверждения отсутствия изменений runtime/tests между релизом и текущим HEAD; новый полный прогон в этом аудите не выполнялся.
- [RUNTIME-PREREQUISITES.json](RUNTIME-PREREQUISITES.json): оба FFmpeg/ffprobe найдены в runtime PATH, версия 9.0.1; 63 таблицы, очередь содержит 49 `done`, без pending/leased/failed на момент чтения. Это не доказательство будущего отсутствия ошибок доставки.
- `pip check`: совместимость установленных зависимостей подтверждена. Актуальные CVE/advisory и безопасность инфраструктурных ACL этим не проверены.
- Production metadata до/после чтения: deployment `466499d5-6979-4a81-8aee-67efcf628976`, SHA `dc9239eb0b82fb80d1788fc657205740cebc49e9`, без изменения.

## Что завершено, а что ещё открыто

| Область | Реализовано и проверено | Остаток перед переносом |
|---|---|---|
| Free / базовый бот | Команды, подписки, текущие бесплатные отчёты и HTML/export, старые группы сохранены | Совместимость на изолированном представителе старой схемы/данных и живой Telegram-сценарий после выпуска |
| Mini App / выбранный дизайн | Два режима, четыре нижних пункта, «Тариф», профиль, темы, SDK-навигация, браузерная матрица | Реальная приёмка Desktop/iOS/Android, safe areas/fullscreen/BackButton и возврата в чат |
| Viewer | Добавление/импорт, уведомления, тихие часы, Free 50 / Plus 200, фильтры, категории, напоминания, папки, история | Исправить ограничение дорогих follow-запросов; проверить OAuth/импорт/тихие часы с разрешённым аккаунтом |
| Видео | Пять выбранных вместе с offline, атомарная замена и запрет шестого, shared capture/render, per-bot reuse, фото при отказе | Реальный полный цикл animation → photo при expiry/refund/выключении и media-нагрузка с настоящей доставкой |
| Streamer | Бесплатное подключение, права канала, оформление и кнопки, подтверждённые публикации | Реальное подключение/отзыв прав/публикация/редактирование; исправить состояние первой сетевой ошибки |
| Telegram «Меню» / «Ещё» | Live возвращён через прежний `menu:live`; восстановление клавиатуры и cancellation/restart проверены регрессиями | Desktop AFTER на новом выпуске, возврат из приложения, selector/cancel, закрытие/открытие чата; исходная клиентская причина исчезновения не установлена |
| Тарифы и наследование | Viewer150 ₽/месяц; Streamer300 ₽/месяц; включает Viewer для зафиксированного получателя; серверные права | Реальная продажа остаётся отдельным незавершённым этапом |
| Платежи | Mock/sandbox ledger, идемпотентность, reversals/refunds/expiry, fake sender | Live transport, callback/reconciliation, Stars invoice/precheckout/refund, реальные документы/условия и разрешённая проверка |
| Legal / поддержка / bank approval | Канонические маршруты, версии/hash/catalog gate, безопасная недоступность | Реальный контакт/оператор/условия и принятие документов; сейчас пять документов отвечают 503, bank package не готов |
| SQLite / backup | 27 миграций; backup, внешний export и restore в отдельный временный файл последнего релиза; migration-copy evidence проверен по идентичности source | Репетиция production migration/rollback на разрешённой изолированной копии; эксклюзивная остановка writers/очереди, восстановление ключа и активной БД |
| Выпуск / эксплуатация | Guarded pinned staging, правильный artifact, один worker, диагностика | Явный production admission, production release/rollback guard и наблюдение первого контролируемого выпуска |
| Масштаб / public site | Synthetic нагрузки и ограничители; сайт/demo подготовлены, noindex | Реальная ёмкость Telegram/Twitch/медиапроцессов и согласованные задержки; public launch не выполнялся |

P20/P21 и T7/T8 в прежних ledger означают завершённый инженерный staging release. Они не закрывают owner visual/native acceptance, деньги или production readiness. Новые семь макетов админ-панели — концепция; действующая R2-панель существует. Новая концепция не является обязательным незавершённым редизайном для этого переноса.

## Блокеры переноса

### 1. Production admission ещё не реализован

`bot/config.py:359–391` включает Mini App/Viewer Plus/Streamer Plus/growth только локально либо при точном pinned staging target. В обычном Railway production они выключены; admin credentials очищаются вне staging. `main.py:135–145` в таком контуре возвращает MenuButtonCommands, а не «Приложение». Включение `NOTIFICATION_QUEUE_ENABLED=1` вне pinned staging останавливает startup с ConfigError.

[LOCAL-ADMISSION.json](LOCAL-ADMISSION.json) воспроизводит это с фиктивным environment, без секретов, сети и БД. Значит, копирование текущей версии в основной бот не перенесёт всё приложение. Нужен отдельный production admission с проверкой бота, environment/service/Volume и безопасного денежного режима. Массово снимать staging guards нельзя.

### 2. Найдены дефекты прав и доступности

| ID | Серьёзность | Подтверждённый дефект | Что исправить |
|---|---|---|---|
| SEC-01 | Низкая | Бывший администратор может завершить ранее открытое добавление стримера в группу/канал после отзыва прав: прямой текстовый ввод использует FSM target без повторной проверки | Проверять текущего автора и права перед сохранением, очищать просроченный/чужой draft; отрицательный тест на отзыв |
| SEC-02 | Средняя | Mini App follow обращается к общему Twitch API без frequency gate, до проверки заполненности списка. Повторные несуществующие логины тратят общий бюджет с poller | Ограничитель до внешнего lookup, ранняя проверка лимита, объединение/краткий cache одинаковых проверок; тест burst без вызовов Twitch сверх лимита |
| SEC-03 | Низкая | Публичные страницы старых web-кабинетов вытесняют чужие Login Widget states; повторный вход одного стримера вытесняет чужие сессии из общего пула | Ограничивать создание по клиенту/пользователю, отзывать собственные сессии; тест сохранения чужого входа/сессии при burst |

Это исходниковые находки: атаки на живой бот не выполнялись. SEC-01 ограничен ранее открытым действием бывшего администратора; SQL injection или доступ к другому private account этим не установлены. SEC-02 требует действительную Telegram-подпись. SEC-03 не позволяет украсть сессию или обойти подпись. Эти границы учтены в серьёзности.

### 3. Платежи и документы ещё не готовы для денег

Публичный `POST /app/api/billing/prepare` возвращает недоступность без создания order/checkout/grant. `first_release_payment_policy` фиксирует OFF. Реальные provider transports не подключены к main; текущие адаптеры требуют injected `network_free` transport и sandbox. Platega callback монтируется только для отдельного local contract runtime. Это безопасный первый staging release, но не готовая денежная интеграция.

Решение владельца сохраняется: **покупка внутри Mini App → выбор Stars / СБП / банковская карта; СБП и карта через Platega, Stars через Telegram**. Не заменяем его сайтом и не запрашиваем повторно уже выбранные методы или RUB-цены.

Открытые данные перечислены в [OWNER-INPUTS-FOR-LAUNCH.md](../../workflows/OWNER-INPUTS-FOR-LAUNCH.md): реальный support/operator, определение месяца, refund/chargeback/upgrade, две XTR-цены, merchant/test допуск, чек и сроки хранения/удаления. Пять legal manifest entries `owner_accepted=false`; реальные маршруты возвращают 503. Требования bank approval к полной покупке/конкретной цене/кликабельному CTA/Privacy/Agreement/контакту сохраняются, утверждение банка не получено.

### 4. Перенос и откат данных не отрепетированы

Backup/verify восстанавливает новый временный файл, а не активную БД. Успешный staging restore не доказывает безопасную замену production. [Runbook](../../runbooks/staging-backup-rollback.md) прямо оставляет manual maintenance path для остановки worker/writers, WAL/SHM и очереди. Старому R2-reader при откате нужны материализованные legacy samples; простого revert недостаточно.

Нужны проверенный production admission/runbook, миграция из разрешённого изолированного source snapshot и репетиция rollback. Тестовую БД нельзя подставлять вместо основной: test grants, jobs, recipient IDs и Telegram file IDs не переносить как реальные покупки/подписки. Backup на том же Volume не защищает от его потери; нужна внешняя копия и восстановимый ключ Twitch token encryption.

При подготовке запуска отдельно сверить незавершённые отчёты и их адресатов: persisted delivery сохраняет frozen positive recipient/payload по документированному контракту `database.py:4108–4109`, даже после смены routing. Deferred queue сверяет текущий маршрут иначе. Само это различие не доказывает уязвимость; не обещать, что смена настройки перенаправляет уже начатый отчёт.

### 5. Реальная приёмка и media-SLA открыты

Desktop BEFORE подтверждал оба входа. AFTER Computer Use был остановлен физическим Escape; последующих вызовов не было. Desktop AFTER, iOS/Android, настоящий подписанный сценарий Mini App, OAuth/выбор канала/публикация, реальные платежи и owner visual acceptance остаются **NOT TESTED**. Browser fixtures и fake sender этого не заменяют.

Фото для уже зарегистрированных staging постов подтверждено read-only DB после обычного poller в M1; новый ручной тест не отправлялся. Настоящая animation и последующий возврат к фото по каждому lifecycle событию не подтверждены.

Медиа-тесты с настоящим FFmpeg, но fake Telegram показывают ограниченный расход ресурсов и честное откладывание. При cap2 большая часть уникальных одновременных capture откладывается: 98 из100 и4998 из5000. Один stream×1000 остановлен ограничением25s после463 fake edits. Это полезная проверка защиты, не доказательство доставки пяти видео каждому при массовом запуске. Старые R9 synthetic20/30/40k тоже не измеряют реальные Telegram/Twitch quotas или задержки.

## Ещё два функциональных риска выпуска

- **Тихие часы и рейды расходятся со справкой.** `bot/handlers/telegram_help.py:29` обещает «Рейды не входят в этот режим», но `_notify_raid` в `bot/poller.py:862,868` вызывает `_private_alert_blocked`, который учитывает quiet-hours. Личные рейды во время тихих часов блокируются. Нужно привести поведение к согласованному исключению и закрепить оба пути регрессией; публикации сообщества не подменять личным контрактом.
- **Перезапуск удаляет накопленные Telegram updates.** `main.py:774` вызывает `delete_webhook(drop_pending_updates=True)` перед polling при каждом запуске. Durable notification queue защищает исходящие jobs, но не восстанавливает отброшенные входящие команды/ответы. При будущих Stars-платежах особенно важны successful_payment/refund updates и независимая reconciliation: текущие деньги OFF не дают проверить их recovery. Для выпуска нужно выбрать и проверить сохранение/обработку pending updates без повторной выдачи прав или действий.

## Доработки меньшего приоритета

- `bot/mini_app_ui/streamer.js:337–338`: первая загрузка при сетевой ошибке оставляет только статус, без явной кнопки повторения. Повтор возможен через visibility/refresh, но на экране действие не предложено. Исправить до финальной пользовательской приёмки; это UX-дефект, не обход прав.
- В подробностях тарифа видеодемонстрация ещё обозначена как не добавленная. Это пример в интерфейсе, не состояние рабочего live-preview pipeline.
- Статистика переходов на Twitch честно помечена «В разработке». Подтверждённые публикации реализованы; clicks/views не выдавать за готовые измерения.
- Дополнительные новые иллюстрации не генерировались: используются три одобренных изображения, подробные гайды — текстом по запросу. Дополнительная генерация не заменяет функциональные gates.

## Порядок завершения

1. Исправить подтверждённые дефекты отдельными RED → код → PASS → scoped review; проверить все затронутые права/старые callbacks и error recovery.
2. Пройти настоящие Desktop/iOS/Android сценарии на Testbot: Menu/live/app return, Viewer, разрешённый OAuth/channel, photo/video/fallback/revocation. Для отправок и OAuth нужен отдельный конкретный разрешённый получатель/аккаунт.
3. Подготовить production admission, migration/rollback rehearsal и эксплуатационные проверки; ничего не выкладывать автоматически.
4. Закрыть документы/support и отдельно реализовать/проверить реальные Stars/Platega после предоставления условий и допуска. Без этого возможно обсуждать лишь выпуск с явно отключённой оплатой.
5. На финальном изменённом snapshot — полный suite, scoped review, browser/native checklist, backup/migration evidence, artifact/bot identity. Затем отдельное разрешение владельца на контролируемый production выпуск.

## Граница аудита

Это оценка всего продукта по подсистемам, текущему коду, планам, тестам и read-only staging. Независимые baseline/architecture/runtime обзоры выполнялись последовательно, один reviewer за раз. Runtime reviewer подтвердил SEC-01 независимо; повторная находка объединена с той же ошибкой контроля, не посчитана четвёртой уязвимостью.

Полностью прочитаны **137 из137 текущих текстовых runtime-файлов `bot` + `main.py`**. Вместе с конфигурацией и существенными operator scripts — **155 уникальных tracked файлов**. Архитектурное чтение само по себе не добавляет файл к security coverage. Development/QA scripts, все tests, исторические документы/макеты/skills и бинарные assets не объявляются построчно проверенными. Поэтому общий security scan имеет **partial repository coverage**, при завершённом проходе текущего runtime.

Managed scan `c309a957-3a46-4fe4-99ea-48a6754e8da8` завершён: три находки, medium1/low2, partial source coverage всего repository. [Generated security report](C:/Users/yusha/.codex/state/plugins/codex-security/scans/TG-BOT.-TwtichSignal/3dc9aad3d64284940fd58386b2ffd01f23b6e8dc_20261004T083132Z_9dw01a9b/report.md), [manifest](C:/Users/yusha/.codex/state/plugins/codex-security/scans/TG-BOT.-TwtichSignal/3dc9aad3d64284940fd58386b2ffd01f23b6e8dc_20261004T083132Z_9dw01a9b/scan-manifest.json), [findings](C:/Users/yusha/.codex/state/plugins/codex-security/scans/TG-BOT.-TwtichSignal/3dc9aad3d64284940fd58386b2ffd01f23b6e8dc_20261004T083132Z_9dw01a9b/findings.json), [coverage](C:/Users/yusha/.codex/state/plugins/codex-security/scans/TG-BOT.-TwtichSignal/3dc9aad3d64284940fd58386b2ffd01f23b6e8dc_20261004T083132Z_9dw01a9b/coverage.json).

Инструмент предупредил об изменении working tree во время сканирования: добавлены собственные документы и read-only/fake-config audit helpers. Runtime/tests/config/legal не менялись; результаты привязаны к исходному source snapshot3dc9aad, а не будущему документальному commit.

Метрика workbench `codex_rollout`: 5 threads, input33084908, cached input31644032, output110321, total33195229 tokens. Это агрегат доступной истории участвующих чатов с cache, не отдельное измерение стоимости только текущего запроса или остатка лимитов аккаунта. `usage.coverage=complete` относится к учёту этого агрегата, не к source security coverage.

Никаких заявлений «весь репозиторий безопасен» или «все реальные сценарии пройдены» из этого отчёта не следует.
