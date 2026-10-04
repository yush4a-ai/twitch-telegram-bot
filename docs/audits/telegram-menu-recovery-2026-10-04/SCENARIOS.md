# Проверки сценариев

Это разделяет проверку серверного поведения и клиентского отображения. Fake sender не подтверждает Telegram UI. Исходная таблица всех 38 потоков: FLOWS.md.

## Серверные проверки

| Сценарии из задания | Проверка и доказательство |
|---|---|
| 1–4: /start, deep link, Меню, Home | test_telegram_home, navigation, review_regressions: cold/reset установка, тёплый Home без спама, отмена FSM до восстановления, data/actor isolation |
| 5: приложение | navigation/compatibility: единый /app, Home/back, собственный private actor; actual MenuButton проверяется отдельно |
| 6–12: add, login/error, confirmation, search/list/delete | telegram_add, guided_flows, journey_refinement, navigation: реальный FSM/SQLite, отмена/подтверждение/пагинация. Menu перехватывается раньше ввода |
| 13–17: streamer/OAuth | streamer, guided_flows, review_regressions: отмена поколений, expiry/error, late result. Новый cold OAuth тест использует реальный _run_auth_flow, внешняя авторизация заменена локальным ожиданием/ошибкой |
| 18–22: selector/requestChat/cancel/success/permission | streamer/channel_permissions и menu_recovery: выбранный канал и Menu, успешное/ошибочное завершение, replay, stale request, delayed DB read, guide/send races, DB/send failure. Реальный requestChat диалог и права владельца через Telegram не проверены |
| 23–26: Posts/settings/quiet/input | journey_refinement/guided_flows/navigation: отмена текущего draft, собственные настройки и права; Menu возвращает Home |
| 27–29: report/HTML/import | help_reports/guided_flows/review_regressions: legacy HTML остаётся, OAuth/import generation отменяется. Реальный OAuth не выполнен |
| 30–32: tariff/payment selector/unavailable | telegram_plus/navigation: роли, 150/300, payment OFF, без invoice/external POST/новых grants; Menu возвращает Home |
| 33–34: help/support | help_reports/compatibility: существующие кнопки и ссылки, back; common private recovery |
| 35: legacy groups | compatibility/navigation/channel_permissions: сохранены функции и ограничения; recovery не устанавливает private клавиатуру группе или чужому actor |
| 36: ErrorGuard | menu_recovery: recovery перед долгим handler и после ошибки; холодный недоступный callback; при занятом keyboard-send pre-recovery пропускает отмену к handler |
| 37–38: restart/redeploy | menu_recovery/home: reset server cache, первый non-Home interaction, один recovery без последующего спама. Actual deployment identity — отдельный smoke; native reopening остаётся NOT TESTED |

Новые 16 регрессий прошли на финальном scoped snapshot: 16 PASS / 2 subtests. Расширенный предыдущий snapshot: 105 PASS / 13 subtests. Полный suite финального commit выполняет штатный guard перед deploy; старый full-run не подменяет эту проверку.

## «Ещё»

Точные строки и callbacks проверены для зрителя/владельца/группы. Старый cb_menu_live обрабатывает menu:live, показывает собственные эфиры и исключает чужие. Cold reset восстанавливает Menu, warm повтор не создаёт новый keyboard message. Admin остаётся owner-only. HTML/browser макет: Chromium и WebKit, 360/390/768/1440, две темы, два режима пользователя, 32 PASS и четыре PNG; это не Telegram screenshots.

## Native и внешние ограничения

Desktop BEFORE/AFTER, закрыть/открыть чат, совместное отображение ReplyKeyboard с «Приложение», WebApp return и selector dialog/cancel: NOT TESTED. Ввод приостановлен после обнаруженного ввода владельца; окно Testbot позднее оказалось перекрыто другим окном. Ответ о доступности pending. Чужие снимки не сохранены.

iOS/Android, owner OAuth, реальная отправка другим получателям/группам/каналам и реальные платежи: NOT TESTED. Никаких новых outbound сообщений во время local/fake/API read-only проверок. Не утверждать, что сервер знает клиентскую видимость клавиатуры.
