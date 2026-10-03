# OLD FUNCTION → NEW PATH → TEST

Исходный HEAD608acff. Все31 callbacks и16commands сохранены с теми же filters; новые входы не выдаёт права. Новых DB migrations нет. HTML/export и исторические отчёты не удалялись.

| Старая функция | Новый путь | Проверка |
|---|---|---|
| /start / callback home / старые Start links | единый home4; persistent Меню | test_telegram_navigation + test_growth_handlers + DeepLinkPersonalTrackingTests |
| Mini App /app и System MenuButton | home → Открыть приложение; System Приложение | NavigationTests + test_mini_app_legacy_compat + startup/actual staging smoke |
| Owner admin/stats/health | Ещё → Админ-панель; прежние owner commands | NavigationTests.test_more... + test_admin_entry + AsyncStartupHardeningTests |
| Legacy /viewer | расширенный Ещё при viewer-only config | test_admin_entry.test_viewer_entry... + MiniApp legacy compatibility |
| menu:add / поиск / addfound / track / track_* | home Add → Twitch lookup → подтверждение → atomic insert | test_telegram_add; DeepLinkPersonalTrackingTests для старых прямых входов |
| Duplicate/Free50/Plus200 | тот же atomic DB tracking service | TelegramAddTests.test_confirm_checks_atomic_current_limit_50_and_200 + inherited limits tests |
| import_follows/menu:import_follows/importfollows:add | Add → Импорт; Помощь → Команды; прежняя команда | TelegramStreamerTests.test_menu_during_legacy_import... + UserTokenFlowTests + DeepLinkPersonalTrackingTests |
| list/card/togglenotify/untrack | Ещё → Мои стримеры → карточка | manifest31 + existing group/card/regression suite |
| report/manual report/text/HTML/export | Ещё → Отчёты; /report; full format в карточке | TelegramCompatibilityTests.test_reports... + ManualReportTests + FinalReportGuardTests |
| auto_report/format/recipient/channel_report | карточка стримера; legacy managegroup | AutoReportOptInTests + TelegramChannelReportTests + report destination tests |
| link_stats/link_* | внутри Отчётов → Привязать отчёты к личке | compatibility Reports test + DeepLinkPersonalTracking/ReportGuard |
| Кто в эфире /live/menu:live | Ещё → Кто сейчас в эфире | LiveListTests; formatter/HTML escaping preserved |
| Quiet Free/presets/custom/disable/digest/exempt | Ещё → Тихие часы; карточка для исключения | compatibility Free/rights test + poller/report/legacy suite |
| Старые group/channel connections/managegroup | Ещё → Мои подключения → существующая группа/канал | TelegramStreamerTests.test_connections_keep_legacy_groups... + GroupPermissionTests + test_channel_permissions_redesign |
| Старые add-group links | прежние startgroup ссылки и group lifecycle не удалены; новый UI их не предлагает | baseline helper/deep link остаётся; full legacy suite; native links NOT TESTED |
| auth_twitch / follower counts | Помощь → Команды; прежняя команда | UserTokenFlowTests, generation cancellation tests; owner OAuth NOT TESTED |
| streamer_connect / verified identity | Я стример → Подключить Twitch; прежняя команда | TelegramStreamerTests + StreamerConnectHandlerTests; fake OAuth только |
| community_intent / tscommunity / chat_shared | Я стример → Telegram-канал; shared intent/permission/CAS | TelegramStreamerTests + test_mini_app_streamer_connect + test_channel_permissions_redesign |
| Публикации / шаблоны / presets / stats | Я стример → Настройки публикаций → /app → Стример → Посты | existing real Mini App APIs/UI; браузерный Plus smoke после shared backend extraction |
| Старый togglepreview / video rights / шестой выбор | старые callback сохраняет entitlement отказ; выбор5 в /app | test_mini_app_legacy_compat + test_entitlement_inheritance + media runtime suite |
| Viewer/Streamer subscriptions | Ещё → Тариф; профиль/Тариф в /app | test_telegram_plus + test_mini_app_purchase + frozen inheritance |
| Покупка Stars/СБП/карта | общий BillingService.public_purchase → unavailable |6choices test + signed HTTP exact fields503, zero orders/payments/grants/provider |
| Financial precheckout/success/refund | прежние billing event handlers | Stars/lifecycle/idempotency/full suite; реальная оплата OFF |
| /paysupport / support / legal | Ещё → Помощь; actual config/canonical ready only | TelegramCompatibilityTests.test_help... + legal/support API suite |
| /invite / src_* / ref_* /myid | Помощь → Команды; прежние команды/links | test_growth_handlers + original commands manifest |
| Динамические URL кнопки Twitch/posts/digest | прежний live/post/report runtime | existing poller/streamer template/quiet digest regressions |
| Старые недоступные сообщения | ответ «Нажми Меню», без side effects | compatibility InaccessibleMessage/malformed ID tests |

## Scoped review и stop-slop

Новые home/Add/Streamer/More/Plus/Help/About, тихие часы и отчётные входы прочитаны полностью. Три факта About, вопросы Help, реальные контакты, каталог150/300, срок из grants, явный Platega. Убраны длинная вводная/прежнее огромный home, технические термины и неоднозначное «канал» для Twitch-стримера на новых экранах. Admin diagnostics остаётся отдельным техническим owner surface.

Сохранили точный owner unavailable текст и правдивые trial/test labels. До проверки реальные оплаты OFF; invoice/provider POST не вызываются. Legal без canonical readiness не показываются. HTML не обещан при default brief: пользователь выбирает full в настройках, прежнее бесплатное право сохранено.

Дополнительные найденные старые проблемы: managegroup malformed int; quiet group mutation без manager guard; immediate Menu throttling. Для каждой есть воспроизведение RED→PASS. Аудит исходного quiet access уточнён: прежние callbacks не проверяли group admin, новый guard это закрывает; личные Free права сохранены.

NOT TESTED: native Desktop/Android/iOS layout, signed live owner scenarios, реальный OAuth аккаунта, реальные сообщения/публикации получателям и реальные платежи. Локальный fake sender не заменяет эти проверки.

## Уточнения владельца после baseline

Smart Home: отдельный read-only builder по собственным DB rows; новый пользователь — статичный banner, возвращающийся — до3 live и остаток/спокойное offline. Compact verified Twitch, без заявления publishing по сохранённому toggle. Official primary/success, Add «➕ Добавить оповещения». Per-bot file_id и bounded1024/16 cache, weak locks, stale fallback, caption→text без обрезания и снятие старой inline keyboard. Menu восстанавливает единственную reply-кнопку после selector. Новые HOME-* tests/logs.

Тариф copy: только живые entry labels изменены в app/profile/paywalls/Telegram/streamer/recovery messages; продуктовые names, catalog150/300, entitlement/payment flow сохранены. CTA inactive «Оформить», active «Продлить»; source role/ownership не зависят от подписи. Актуальные browser locators изменены по прямому owner решению, historical audit/screenshots сохраняются.
