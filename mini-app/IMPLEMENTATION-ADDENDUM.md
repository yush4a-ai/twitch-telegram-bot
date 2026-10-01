# Mini App Plus: implementation addendum T13–T17

> Agentic worker: use superpowers:executing-plans and test-driven-development. Один видимый исполнитель в чате «Мини-апп».
**Goal:** Дополнить основной план утверждёнными 50/200, пятью выбранными автоматическими видеопревью, напоминаниями и следующими удобствами Plus.
**Architecture:** Существующие aiogram/aiohttp/SQLite/PreviewManager, единые capabilities, отдельные небольшие модули и additive миграции. Не переносить бот на python-telegram-bot: в requirements стоит aiogram.
**Spec:** `mini-app/2026-10-01-scope-update.md` + основной Native Mini App spec.
**Order:** T1–T6 → T13 → T7–T11 → T14–T17 → T12. Все checkboxes изначально незавершены.
**Global constraints:** Все границы основного spec сохраняются; тестовые отправки только с согласием, никаких денег/production. Новое scope update отменяет прежнее «video позже / лимиты ещё не согласованы» только для описанного объёма.
**Review focus:** гонки 4→6 слотов; lost/refunded entitlement перед send; ограничения всех входов; shared stream и per-bot media cache; нагрузка на массовые edit; безопасный animation→photo; ручные паузы при downgrade.

## T13. Тарифные лимиты и атомарный выбор пяти видеоканалов

Files: `bot/plan_catalog.py`, `bot/capabilities.py`, new `bot/viewer_preferences.py`, `bot/database.py`, `bot/mini_app_viewer.py`, `bot/handlers/streams.py`, UI viewer/subscription; new `tests/test_viewer_plus_limits.py`, `tests/test_viewer_preview_slots.py`.
Interfaces: `async replace_video_selection(user_id: int, broadcaster_ids: list[str], *, expected_version: int, now: float) -> VideoSelection`; frozen result `version`, `selected_ids`, `effective_ids`, `limit=5`. Сервер сам проверяет ID пользователя/каналов. GET-like state по текущему POST-контракту, save `/app/api/viewer/video-selection`.
- [ ] RED: Free 50/51 и Plus 200/201 через bot и app; группы сохраняют прежний лимит; снижение тарифа сохраняет строки/ручные паузы, effective активны максимум 50. Одновременный add не превышает лимит.
- [ ] RED: Plus выбирает 5 даже offline; 6 отклоняется; при 4 выбранных два конкурентных запроса дают <=5; stale version 409; чужой/unfollowed канал 403/400; замена атомарна; notify off не освобождает выбор, unfollow освобождает.
- [ ] Запустить `.venv\Scripts\python.exe -m pytest tests/test_viewer_plus_limits.py tests/test_viewer_preview_slots.py -q`, зафиксировать ожидаемый RED.
- [ ] Реализовать единый серверный каталог, versioned selection и plan-paused независимо от ручного notify_enabled. Video effective только при активном Viewer Plus; сохранённый выбор после expiry не удаляется.
- [ ] PASS тех же тестов + старые viewer/menu/access tests; обзор SQL races и всех entry points, commit/checkpoint. UI «Видеопревью: 3 из 5», фото default, шестому предложить замену.

## T14. Viewer video fan-out с ограничением ресурсов

Files: `bot/preview_runtime.py`, `bot/live_post.py`, consumers/capabilities, new `bot/preview_admission.py` при необходимости; tests `tests/test_viewer_video_delivery.py`, `tests/test_preview_admission.py`; existing preview/lifecycle suites.
Interfaces: Viewer eligibility = verified private destination + active Viewer Plus + membership of effective selected IDs + current live/notify. Streamer eligibility остаётся отдельной. Shared generation key включает physical stream и профиль render; file-id cache включает bot identity.
- [ ] RED: 100 подписчиков одного эфира используют один artifact/upload и повторное file_id; 5 разных потоков не превышают configured capture/render limits; смена выбора во время подготовки исключает старого получателя; неизвестный результат не считается успешным.
- [ ] RED: expiry/refund/offline/unfollow между render и send запрещают новый video; безопасный animation→photo применим к текущему сообщению; caption length/type ошибки не удаляют пост и не вызывают бесконечный repost.
- [ ] Запустить `.venv\Scripts\python.exe -m pytest tests/test_viewer_video_delivery.py tests/test_preview_admission.py tests/test_live_post_media_lifecycle.py -q`; подтвердить RED новых условий.
- [ ] Реализовать переиспользование, bounded/fair admission и общий sender budget. Никаких отдельных capture на человека; сборка 24 s прежнего качества. При перегрузке фото + явный временный статус. Фоновые media не блокируют новые сигналы; старые версии не накапливаются.
- [ ] PASS новых и существующих `test_preview_runtime.py`, `test_live_preview_provider.py`, capture/lifecycle tests. Обзор stale callbacks, fairness, cancellation/cleanup и per-bot cache; commit.

## T15. Напоминание через 15/30 минут

Files: new `bot/viewer_reminders.py`, узкое расширение queue/dispatch, mini_app_viewer и UI; new `tests/test_viewer_reminders.py`.
Interface: `async set_reminder(user_id: int, broadcaster_id: str, logical_stream_id: str, delay_minutes: Literal[15,30], *, now: float) -> ReminderState`; `/app/api/viewer/reminder`, cancel route. ID/stream сверяются сервером.
- [ ] RED: Free отказ, Plus создаёт один job; повтор клика обновляет срок, отмена прекращает; на offline/другой stream/expiry/unfollow/quiet-hours send не идёт; потеря сети в UI не показывает ложное сохранение.
- [ ] Run `.venv\Scripts\python.exe -m pytest tests/test_viewer_reminders.py -q` → RED; реализовать persistent state и fake-clock dispatch без отдельного бесконтрольного timer на человека.
- [ ] PASS + notification queue/worker tests, back/scroll browser scenario, обзор и commit. Не отправлять реальные сообщения в тестах.

## T16. Удобства Plus и добровольное тестовое ознакомление

Выполнять T16a–d последовательно отдельными bounded slices, не одним diff. Перед каждой частью записать короткий локальный контракт на основе решений ниже; не спрашивать повторно о цвете/названии.
- [ ] T16a folders: `bot/viewer_folders.py`, UI, `tests/test_viewer_folders.py`. Одна папка на подписку для однозначности; правило конкретного стримера приоритетнее общего правила папки; общие mute/quiet-hours остаются старшими. RED/PASS: чужой доступ, перенос, удаление папки не удаляет подписки, expiry не стирает настройки и не применяет платные правила.
- [ ] T16b event history: `bot/viewer_history.py`, `tests/test_viewer_history.py`. Личная read-only пагинированная лента собственных go-live/category/reminder outcomes с явными sent/suppressed/unknown; хранить метаданные, не видео. Техническая очистка через 30 дней по server UTC, не рекламное обещание тарифа. RED/PASS: чужие события закрыты, fake live/delivery не показываются настоящими, pagination/retention/expiry совместимы.
- [ ] T16c template presets: `bot/streamer_presets.py`, `tests/test_streamer_presets.py`. Именованные сохранённые варианты для собственного broadcaster, применение к разрешённому placement с version check. RED/PASS ownership/права/unsafe HTML/expiry, сохранённый вариант не меняет опубликованный пост без явного применения. Сравнение периодов считает только подтверждённые публикации, не просмотры.
- [ ] T16d trial: `bot/viewer_trial.py`, `tests/test_viewer_trial.py`, subscription UI. Одно добровольное 7-day grant на проверенный Telegram ID, server UTC, атомарная повторная защита, никогда не автоактивируется при первом входе. Только owner/tester allowlist на pinned staging. RED/PASS: повтор/replay/race не продлевает, expiry возвращает Free, денег/публичного checkout нет.
- [ ] Для каждой части сначала ожидаемый RED, минимальная реализация, PASS, обзор и отдельный commit; удерживать ограничения существующих текстовых полей и ресурсный потолок. Реальные продуктовые лимиты сверх утверждённых не изобретать.

## T17. Смешанная нагрузка фото/видео/обычных сигналов

Files: отдельный reusable `scripts/mini_app_media_load.py`, `tests/test_mini_app_media_load.py`, `docs/audits/2026-10-01-mini-app-media-load.md`.
- [ ] RED/PASS самого harness: только новая temp DB/fake Twitch/fake Telegram; обязательные resource stop и cleanup. Запрет активной/production DB и публичной массовой отправки проверяется тестом.
- [ ] Последовательные профили: 1 stream×1000 recipients; 100 streams×10 recipients; 1000 viewers×5 selected с максимально разными потоками. Числа обозначают synthetic input, не фактически создаваемые 5000 capture: admission обязан ограничить работу/очередь.
- [ ] Измерить max capture/encode/tasks/queue, RSS/CPU/temp disk, p95 задержки новых go-live при фоновых edits, cache hit/reuse, expired-slot cancellations. Сначала local, staging только в разрешённых ресурсах; не увеличивать оплату/replica самостоятельно.
- [ ] При превышении ресурса зафиксировать предел и безопасный fallback, не форсировать больший профиль. По synthetic цифрам не заявлять SLA и не скрывать native/capture/send E2E ограничения.
- [ ] Пройти очередное отключение/возобновление приложения, 4→5→6 UI, смену слота, expiry→photo, возобновление Plus, две сессии пользователя. Проверить честные статусы и отсутствие сохранённых secrets/initData в артефактах.
- [ ] После T13–T17 выполнить основной T12 целиком: review, функциональные/browser/security тесты, полный suite на финальном коде, backup/restore, точный staging deploy, реальная проверка только разрешённых внешних сценариев. Нет «готово» только по макету или health 200.

## Чек покрытия

T13: 50/200 и 5 слотов, ботовые команды↔приложение, downgrade. T14: автоматическое viewer video и shared engine/fallback. T15: напоминание. T16: папки, история, варианты оформления, test-only trial. T17: медийная нагрузка и ограничители. Основной T12 выполняется последним; T1–T11 сохраняют всю прежнюю навигацию/стиль/подписки/стримерскую часть.
