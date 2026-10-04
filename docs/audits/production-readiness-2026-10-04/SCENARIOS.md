# Проверки выпуска: что подтверждено и что осталось

Runtime: `39bc82171114a0a6b20ad4251bd68c4cc2d3f3f0`, Testbot deployment `5211c9f8-6a2f-4834-9aa6-88706a4a34a7`. Аудит не меняет бот и данные. «Код/тесты» не означает живой Telegram-сценарий.

| Сценарий | Доказательство сейчас | Перед выпуском |
|---|---|---|
| Правильный бот/artifact, unsigned API denied | Свежий read-only PASS, 195 runtime hashes / 17 assets / 9×401 | Повторить для каждого нового изменённого выпуска |
| Первый /start, постоянное «Меню», «Приложение» | Регрессии + Desktop BEFORE предыдущего runtime; свежий Bot API MenuButton PASS | Desktop AFTER, новый чат, restart, WebApp return, selector cancel: NOT TESTED |
| «Ещё» → «Сейчас в эфире» через прежний callback | Код/pure builder/scoped tests/макет PASS | Настоящий Desktop/iOS/Android: NOT TESTED |
| Viewer список/поиск/add/pause/delete/Undo | Код, временная БД, browser fixture и fake sender PASS | Подписанный live API/телефон: NOT TESTED |
| Импорт Twitch с подтверждением, отмена/late result | Код/регрессии PASS | OAuth владельца: NOT TESTED; конкретный аккаунт требует отдельного допуска |
| Свободные 50/Plus200, шестое видео, atomic replacement/version conflict/offline slots | Серверные регрессии PASS | Не выдавать за реальную media-доставку |
| Добавление после отзыва Telegram admin rights | Исходниковый дефект SEC-01 | Новый отрицательный тест и исправление |
| Follow burst до внешнего Twitch lookup | Исходниковый дефект SEC-02 | Ограничитель и проверка фактического числа вызовов |
| Чужие Login Widget states/sessions при burst | Исходниковый дефект SEC-03 | Сохранение чужих состояний/сессий в новых регрессиях |
| Фильтры/категория/напоминания/папки/история и Plus expiry | Серверные и browser fixture tests PASS | Разрешённый live путь и ожидания времени: NOT TESTED |
| Тихие часы, исключение, сводка, рейды | Код прочитан; справка и личный raid guard расходятся | Исправить согласованное исключение, оба pre-send guards проверить |
| Фото при недоступном видео | M1: ordinary poller → существующие staging posts с photo, read-only DB; fake pipeline PASS | Native AFTER и новый разрешённый media-сценарий: NOT TESTED |
| Animation H.264/no audio 6→12→18→24→6 | FFmpeg synthetic/fake delivery tests PASS | Реальная animation/file_id reuse: NOT TESTED |
| Expiry/refund/выключение/утрата прав → photo | Серверные negative/fake delivery tests PASS | Native animation→photo: NOT TESTED |
| Shared media/global overload/cleanup/recovery | Synthetic bounded profiles; ресурсный предел и deferrals сохранены | Реальное SLA/combined load: NOT TESTED |
| Streamer Free connection, свои/чужие channel permissions | Серверные tests и фиктивный Telegram PASS | Реальный OAuth/requestChat/send/edit/delete/rights removal: NOT TESTED |
| Стример: первая network error → повтор | Исходниковый UX gap, нет кнопки повторения | Исправить, browser error→retry→success |
| Старые groups/Free full report/HTML экспорт | Совместимость в tests/source; исторический экспорт сохранён | Репрезентативная разрешённая migration-copy и live Free-сценарий |
| Оформление/кнопки/подтверждённые публикации | Серверные/browsers tests PASS | Измеренные разрешённые публикации; clicks всё ещё «В разработке» |
| Viewer150/Streamer300 с включённым Viewer | Каталог/эффективные права/fixture active & expired PASS | Не путать test trial/grant с оплатой |
| Stars/СБП/карта внутри приложения | UI flow; публичный prepare unavailable, деньги OFF | Live implementation/условия/допуск провайдера; NOT TESTED |
| Refund/duplicate/replay/reversal/reconciliation | Local mock/sandbox/temporary DB PASS | Реальные provider/Telegram events: NOT TESTED |
| Privacy/Agreement/support/tariffs/payments | Свежий 5×503, manifest acceptance OFF | Реальные данные и принятое содержимое; bank NOT READY |
| Restart и входящие pending updates | `drop_pending_updates=True` подтверждён source | Проверить retention/replay, будущие Stars payment recovery |
| Backup/export/restore integrity | Последний staging release: snapshot+внешний export+temp restore PASS | Active production migration/rollback/key recovery: NOT TESTED |
| Production identity/admission/menu/queue | Isolated fake config показывает features OFF/commands/queue startup error | Подготовить отдельный безопасный production admission; production deploy НЕ выполнялся |

Разрешения на OAuth/получателей/реальные деньги не предполагаются из этого checklist. Отсутствие разрешения сохраняет NOT TESTED; локальные исправления и подготовка runbook могут продолжаться отдельно. Основной вывод и порядок: [REPORT.md](REPORT.md).
