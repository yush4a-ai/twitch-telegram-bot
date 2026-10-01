# Mini App + Plus: фактическая Free-основа перед изменениями

Дата: 2026-10-01. Ветка `autonomous/twitchsignal-roadmap`, HEAD при чтении `69a980b`. Это карта существующего кода, а не доказательство работы нового приложения или реального Telegram E2E. R0–R9 не перезапускались.

## Сохраняемые пользовательские действия

| Сценарий | Проверенный путь в коде | Контракт следующего этапа |
| --- | --- | --- |
| Отслеживание Twitch-канала | `/track`, меню добавления, deep link и импорт проходят через `bot/handlers/streams.py::_add_validated_tracking`; `Database.add_channel_with_limit` в `bot/database.py` сериализует проверку и запись. Текущий `MAX_CHANNELS_PER_CHAT=50` применяется и к личным, и к групповым чатам. | Личный Free — 50, Viewer Plus — 200; групповой лимит остаётся 50. Повторное добавление не расходует место. Все входы бота и приложения должны считать один effective лимит. |
| Список, live и удаление | `/list`, `/live`, `/untrack`, карточки меню используют `tracked_channels`, `list_channels_with_notify`, `list_live_channels`, `remove_channel` в `bot/database.py`. | Одна БД для команд и Mini App. Пауза не удаляет подписку; удаление очищает связанные правила. |
| Обычный сигнал начала эфира | `bot/poller.py` проверяет `notify_enabled` и текущий live; durable очередь и `notification_worker.py` различают `SENT` и `STALE`. | Free получает базовый сигнал и после истечения Plus; платные правила не применяются без права. Не считать неопределённый ответ Telegram успехом. |
| Настройки уведомления | `bot/handlers/streams.py::cb_toggle_notify` и `/viewer/api/notify` в `bot/viewer_web.py` меняют `tracked_channels.notify_enabled` через `Database.set_notify_enabled`. | Оставить ручной флаг самостоятельным: понижение тарифа не должно включать выключенные вручную уведомления. |
| Тихие часы и сводка | Меню и команды в `bot/handlers/streams.py` сохраняют `quiet_hours`, `notify_after_enabled`; `bot/poller.py::_check_quiet_hours_end` и `_send_quiet_hours_digest` обрабатывают отложенное. | Существующее поведение Free сохраняется. Обнаруженное расхождение: `/viewer/api/digest` сейчас требует `has_viewer_plus`, хотя бот даёт настройку без проверки Plus. Исправить при T6, покрыв Free-тестом. |
| Базовые настройки карточки | `bot/handlers/streams.py::_channel_card_keyboard` даёт `notify`, `preview`, автоотчёт, формат, рейды, исключение тихих часов, получателя отчёта и удаление с проверкой прав чата. В `tracked_channels` новые строки имеют `notify_enabled=1`, `preview_enabled=0`. | Не переносить существующие бесплатные настройки за Plus. Права чужого чата сохраняются. |
| Сообщество стримера | Существуют `streamer_auth.py`, `streamer_community.py::verify_community_permission` и `/streamer/api/communities` в `streamer_web.py`; DB ограничивает `add_streamer_community` десятью записями. | Не менять число сообществ. Обнаруженное расхождение: существующий `/streamer/api/communities` POST требует Streamer Plus; согласованный Free onboarding должен работать через новый путь и совместимый старый путь в T9 без ослабления проверки прав. |
| Публикация и оформление | `bot/live_post.py` ведёт media lifecycle; Streamer Plus шаблон и статистика уже есть в `streamer_web.py`, базовый post pipeline — в `bot/poller.py`. | Стандартный Free-пост сохраняется, пользовательское оформление и новые кнопки требуют Streamer Plus; статистика считает подтверждённые публикации. |

## Media и доступ: исходное состояние

- `bot/config.py`: обновление preview по умолчанию 300 с, один одновременный тяжёлый job; `bot/preview_capture/service.py` ограничивает активные capture двумя. `bot/live_preview_provider.py` применяет защиту 10 MiB; формат остаётся H.264 MP4 без звука как Telegram Animation, цикл 6→12→18→24→6.
- `bot/poller.py::_refresh_thumbnail` использует `LivePostUpdater.apply_photo` для обычного фото. Сейчас `apply_photo` принимает только `text`/`photo` и возвращает конфликт для `animation`: T14 обязан проверить безопасное animation→photo после потери права.
- Старый `cb_toggle_preview` в `bot/handlers/streams.py` не проверяет тариф и может менять `preview_enabled` у личного чата. Новый личный выбор пяти каналов — отдельный серверный контракт T13; нельзя считать этот старый флаг доказательством Viewer Plus и нельзя менять групповое поведение без теста.
- `/viewer/api/filter` в `viewer_web.py` опирается на `Database.save_viewer_filter` с серверной проверкой Viewer Plus и `expected_version`. `/viewer/api/state` показывает только собственные подписки по проверенному `initData`. `/streamer` использует свою Telegram-проверку и права сообщества; `/admin` остаётся отдельной owner-поверхностью.
- `/viewer` монтируется при `viewer_plus_enabled` в `main.py`/`bot/oauth.py`; в `bot/config.py` флаг включён локально или на pinned staging. Новый `/app` не должен открываться в production одним отсутствующим параметром.

## Карта новых и изменяемых маршрутов

| Поверхность | Существующее | Планируемое |
| --- | --- | --- |
| Зритель | `/viewer`, POST `/viewer/api/state`, `/notify`, `/filter`, `/digest` | `/app`, `/app/api/bootstrap`, `/app/api/viewer/*`; старые маршруты остаются совместимыми |
| Стример | `/streamer`, Telegram Login/WebApp, `/streamer/api/profile`, `/stats`, `/communities`, `/templates/{chat_id}` | `/app/api/streamer/*`, бесплатное подключение с проверенными intent и правами |
| Владелец | `/admin`, `/admin/api/snapshot` | Не входит в общие вкладки Mini App; scope не расширяется |
| Инфраструктура | `/healthz`, OAuth callback | Не доказывают доставку и не меняют права пользователя |

## Проверка справочников и внешняя граница

`docs/workflows/2026-10-01-mini-app-skills-lock.json`: 31 ожидаемый файл, 31 найден, SHA256 и byte size совпали; upstream helpers отсутствуют по утверждённому manifest и не запускались. Для T1 прочитаны `impeccable` (Operate/new-work) и `design-system`; Telegram и security справочники читаются в соответствующих кодовых этапах. Незавершённого `pytest` или staging deploy по процессам рабочей папки не найдено. Существующие browser/Telegram проверки R7–R9 — исторические, новый UI ими не проверен.
