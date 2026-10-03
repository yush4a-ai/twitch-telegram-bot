# Доработка Telegram-путей — 03.10.2026

План: `docs/superpowers/plans/2026-10-03-telegram-journey-refinement.md`.
BASE b29482589be27adefaad4d30e7be635c0695cf73; clean, autonomous/twitchsignal-roadmap.

## Правка текста главной по screenshot владельца

BASE62572db. Изменён только caption Home: иерархия bold/italic, отдельные блоки, сокращённый Twitch footer. Plan/evidence: docs/audits/telegram-home-copy-2026-10-03/PLAN.md. RED3FAIL/1PASS → focused32PASS; Chromium390/1440 локальный макет, freshbackup/restorePASS. Full gate и Testbot release впереди; native/owner acceptance открыты.

## Продолжение T4–T8 (BASE e0580fc)

## T7/T8: технические gates и Testbot release complete — 00c6093

Четвёртый неизменный полный guard на00c6093d87d1e2617ddd19a736c42aab0d14e1d6:1541PASS/2existingWindowsSkips/3638subtests/1268.11s; failures0. Пакет671files/16,081,745bytes, excluded только docs/audits/design, exact committed bytes verified. SUCCESS603eeb44-0bca-4a28-a1e1-4d365b5db377. Actual smoke195runtimefiles/17HTTPassets+shell, bot8859004067/TwitchSignalTestbot, MenuButtonWebApp«Приложение»,9unsigned401,27versions/integrityok/FK0/paymentOFF/livecallback404/legal503 PASS. Production466499d5/dc9239e до/после неизменён. Bounded100deploymentlogs credential patterns0, raw logs не экспортированы. Первый smoke helper не нашёл имя STAGING-migration-copy.json; mapping reuse evidence добавлен после проверки13module hashes, повторный actual smoke PASS. Это исправление отчётного пути, не новый migration run.

All T0–T8 technical work published; nativeDesktop/iOS/Android/ownerOAuth/signedlive/ручная адресная доставка и визуальная приёмка остаются NOT TESTED. Outboundtestmessages0/realpayment0; текущие списки/entitlements владельца для QA не менялись. Новые изображения0, одобренные3byteidentical; подробные инструкции текстом. RELEASE.md/SCENARIOS.md содержат входы/ожидаемые сценарии/limitations/решения. Итоговые документационные изменения не меняют опубликованный runtime00c6093; не объявлять новый docs HEAD задеплоенным. Ветка/все HTML/export/evidence сохранены, push/merge не выполнялись.

Владелец подтвердил продолжение всего сохранённого плана после M1. T4 in progress: три одобренных PNG переносятся без перерисовки; главная разделяет tracking/notify, неизвестный live status не подменяет offline. T2 nearby Menu завершается здесь; T5 add/import/channel/quiet и T6 help/report/tariff затем последовательно. T7 один свежий reviewer и полный suite финального snapshot, T8 guarded Testbot. Ruling: отдельный M1 checkpoint не завершает задачу — уже выданное разрешение на весь план сохраняется. Старые evidence/HTML/экспорт не удаляются; прежняя инструкция skills о очистке scratch не применяется по прямому решению владельца.

T4 complete (BASE e0580fc): RED4 сценария + channel-step RED1; GREEN51PASS/4subtests/50.17s. Три одобренных PNG в bot/assets, welcome/home file_id раздельно per-bot, compact home для возвращающегося пользователя. Notify2/42 и all-muted не обещают все42; live помечен последней проверкой, ошибка query даёт unknown. Twitch identity без выбранного канала показывает незавершённый шаг; сохранённые подключения не выдаются за свежие publishing permissions. Nearby reuse≤2messages/45s; дальний Home отвечает внизу и снимает предыдущие inline buttons. Старые tests обновлены для одобренной photo-подачи/счётчиков, проверки fresh bottom/cap/escaping/concurrency/permissions сохранены. Scoped self-review: auth, bounded cache, caption fallback и legacy group text без изменений. T5 next; полный финальный guard только T7/T8.

T5 complete (BASE fe58189): RED6 сценариев + quick UTC/readiness/cancel RED; GREEN54PASS/4subtests/49.13s. Add ещё и явная отмена найденного стримера, OAuth URL/cancel и preview duplicates/limit; foreign import confirm не очищает чужой draft и не сохраняет список. Approved channel guide появляется с short text, подробности по кнопке, owned pending resume сохраняет intent; readiness переход отменяет selector, отдельно показывает свежие права и notify switch. Free channel connection не требует покупки. Quiet preset/custom сначала местное preview+UTC; confirmation bound actor/chat/nonce/expiry, clear-before-write, отказ при изменившемся UTC, быстрыеUTC0/2/3/5. Старые callback routes/group connections сохранены. Scoped self-review: domain permissions сохраняются; help и выбранный режим не авторизуют гранты. Реальные OAuth/native/outbound ещё NOT TESTED. Дополнительные изображения не сгенерированы: в канальном сценарии используется одобренная иллюстрация, точные инструкции текстом. T6 next.

## M1: staging complete — release160703a

## T6: complete (BASE 69a8fa7)

## T7: final review / fix pass (BASE 1d1cca7)

Один fresh reviewer просмотрел taskdiff b294825→1d1cca7 без writers/subagents/внешних calls. Important cancel-during-permission deletion race подтверждён Root3RED (cancel/duplicate after re-add/expiry). Atomic claim после await +fresh expiry, GREEN79PASS/4subtests/76.89s, diffcheck code PASS. Результаты import/remote legacy add оставались42строк: Root re-grade reviewerMinor→Important как незавершённая8/page часть согласованного journey; RED2 (42!=8). Fix pass переключает результаты на canonical bounded list context без изменений auth/grants. Финальный focused/fullgate впереди. Reviewer declinednative/OAuth/delivery/deploy: реальные сценарии NOT TESTED, guard/actualsmoke проверяются Root отдельно.

Ruling: не оставлять42строки после импорта/remoteAdd, хотя reviewer назвал это Minor — пользователь согласовал bounded list journey и просил завершить план. Это functional plan alignment, не косметическая правка; стоимость ошибки — дополнительные regression tests текущих legacy returns. Подтверждать отказ Telegram202buttons без фактической проверки нельзя.

Свежий staging-only backup3,108,864bytes/integrityok/63tables, restore remote и local PASS. Переиспользование migration-copy подтверждено byte identity13schema/cipher modules; новый migration run не заявляется. Mini App18files identical к browser-evidence527b0c3; Chromium/WebKit232checks/58PNG на движок переиспользуются. Approved assets3byteidentical originals, generated0. SCENARIOS.md отражает ожидаемое/фактическое и nativeNOT ограничения.

Fix pass final focused52PASS/4subtests/45.31s, REVIEW-final-GREEN.log. Пять новых RED→PASS scenarios (3delete races, 2pagination) проверяют реальное SQLite/FSM и fake delayed permissions. Один проход исправлений завершён; новых findings не скрыто. Полный suite только штатным guard на clean committed final snapshot; до результата T7/T8 не отмечены завершёнными.

Первый fullguard на514fbed показал2FAIL в test_admin_entry: mocked DB не содержит canonical list_channels_with_routing, который добавлен Home вT4. На isolated rerun2FAIL/5PASS; исправлена только fixture, owner/admin isolation assertions неизменны, GREEN7PASS/3subtests. Неуспешный fullrun остановлен после13% по проверенному PID pytest, guard зарегистрировал STANDARD_GATE_FAILED/deploy отменён. Частичный run не засчитан как полный suite, log/manifest/RED сохранены. Следующий clean snapshot повторит неизменный полный guard; skips/assertions/testselection не меняются.

Второй fullguard30d050f выявил test_older_client_deep_link_and_chat_shared_use_same_intent: SimpleNamespace Message без answer_photo для нового approved guide. Independent file run1FAIL/8PASS подтвердил AttributeError fixture; добавлен только fake transport method, old request_id/community/replay assertions сохраняются. Второй run остановлен после30%/guardDEPLOY_CANCELLED, не засчитан полным. Scope scan всех callers cmd_start/start_link/Home/guide выполнен, affected compatibility47PASS/14subtests/50.79s. Логи обоих незавершённых runs и RED/GREEN сохранены. Product fixes от review остаются неизменными; финальный полный gate повторяется на новом clean snapshot.

Третий fullguard e522398 завершён полностью:1FAIL/1540PASS/2existingWindowsSkips/3638subtests/1205.84s, deploy отменён. Единственный failure UserTokenFlowTests.test_import_temporary_error_does_not_publish_partial_selection — mocked private Message без from_user; после T5 import checks owned actor, это реальная обязательная часть Message contract. Independent RED подтверждён; добавлен from_user.id123, старые проверки отсутствия partial list и временной ошибки неизменны. Весь UserTokenFlow28PASS/24.06s. Никаких runtime изменений в этом исправлении, assertion relaxation/skips нет. FULL-third-complete-failed.log сохраняет настоящий полностью завершённый красный suite. Следующий финальный gate повторяется неизменным штатным guard.

RED5 реальных отсутствующих сценариев; GREEN70PASS/10subtests/53.52s (telegram help/reports/plus/navigation/guided/compat/tariff, viewer_filter/category/notification_cutover), manual/automatic report guards отдельно51PASS/35.79s. Первый focused запуск с неверным именем test_tariff_copy не выполнил tests, сохранён как ошибка команды; повтор исправлен. Помощь содержит on-demand about/import/quiet/reports и реальные configured support/legal links. Личные пять preview включают offline; описание каждого feature из canonical catalog, paymentOFF заранее на offer/method screen. Автоматический outbox и ручной отчёт используют сохранённые начало/завершение UTC; неизвестный start не подменяется датой доставки. Free full report и HTML файл проверены через real SQLite/fake transport. Existing follower warning уже требует подключение самим стримером, help поясняет, что viewer OAuth не даёт чужие followers. Copy Stop-Slop reviewed: точные действия, реальная навигация, raid exception и retention24h без новых обещаний. No migrations/grants/payments/provider requests. New images: 0; три approved original assets сохранены, подробные настройки текстом. Scoped diff review PASS, full final suite и fresh reviewer остаются T7/T8.

Ruling: browser evidence T0 может переиспользоваться только после проверки неизменности всех Mini App source/assets — текущие T4–T6 меняют Telegram, не web surface; иначе потребуется повторная browser matrix. Native Telegram/owner visual acceptance не заменяется pytest или макетом. Стоимость неверной идентичности — незамеченная web regression; identity proof сохраняется отдельно.

Штатный full guard1517PASS/2existingWindowsSkips/3638subtests/1117.61s и guarded SUCCESS deploymentac62b912-b6bb-4633-899b-6afa1ffecfd4. Exact runtime/HTTP bytes, bot8859004067, постоянный menu«Приложение», auth401, integrity/FK/paymentOFF/production unchanged подтверждены actual smoke. Обычный poller перевёл существующие сообщения28/29 (`samoylov___`/`gofns`) в photo; проверка readonly DB, без отдельной тестовой отправки. Native BEFORE сохранён; AFTER NOT TESTED из-за перекрытого окна и активного ввода владельца. Backup/restore свежий PASS; migration-copy evidence переиспользован после проверки неизменности schema/cipher. Отчёт M1-RELEASE.md и все RED/intermediate/PASS logs сохранены. T2 частично, T4–T8 остаются открыты.

## T0: complete (BASE b294825)

Большинство входов уже соответствует правилу владельца. Оставшийся `Подробнее о Plus` в подробностях Mini App заменён на «О тарифе», маршрут product:details сохранён. Старые документы/аудиты/имена продуктов не переписываются.

RED: обновлённый пользовательский переход отсутствовал в viewer/streamer, four failed checks + locator timeout. Это ожидаемое расхождение, а не ошибка окружения; предшествующий MODULE_NOT_FOUND решён выбором установленного bundled NODE_PATH без установки пакетов.
GREEN: быстрый141checks/28PNG, Chromium232checks/58PNG и WebKit232checks/58PNG. Реальные UI-модули, fixture-данные, сеть заблокирована. Current hero/swipe/Undo/regressions сохранены.
Focused pytest: tariff copy/Telegram Plus/Mini App user copy/subscription/purchase —24PASS/3047subtests/22.18s. Единственное предупреждение: нет записи в старый .pytest_cache; assertions/skips не менялись. Полный suite запланирован на финальный snapshot.
Evidence: `docs/audits/telegram-journey-2026-10-03/copy/`. Scoped diff review: одна runtime-подпись; QA selectors отражают правило владельца и продолжают проверять реальный переход, цену и состав features. Stop-Slop copy pass: «О тарифе» обозначает назначение ссылки.

## T1: complete (BASE 527b0c3)

Сверка исходного аудита с текущим кодом: /start и «Меню» уже отвечают внизу чата (show_home previous=None), поэтому старое замечание не воспроизводится этим кодом и не требует возврата скрытого редактирования. Переход streamer:posts действительно не вызывает cancel_ui — незавершённый selector может остаться. Следующий RED покрывает отмену и поздний chat_shared без сохранения соединения.
Фильтр viewer_filter применяется к личному стартовому сигналу: точное название игры (casefold), любое из включённых слов/фраз как подстрока, исключения имеют приоритет; группы обходят личный фильтр. Отдельный category detector требует стабильной смены категории. Новый сигнал только от изменения заголовка не входит в этот контракт; не изобретается в рамках проверки.

Pre-flight interfaces: T2/T3 — текущая Telegram reply_markup/источник возврата; T3/T5 — Add target/return; T4/T5 — media-caption/keyboard restoration; T6/T7 — существующие фильтры и права; T7/T8 — неизменный snapshot и штатный guard. Источники page/query будут только серверной navigation-state, без изменений доменных прав.
Ruling: Windows bookkeeping ведётся видимым ledger без запуска отсутствующих Bash community helpers. Модель/усилие основного исполнителя сохраняются.

Карта `docs/audits/telegram-journey-2026-10-03/UI-CONTRACTS.md` связывает экраны, callbacks, данные, права, возвраты и контракт фильтров. Изменение названия эфира не обещается как уже реализованное дополнительное уведомление.

## T2: выполняется (BASE 527b0c3)

Selector→posts: RED pending != cancelled; минимальное подключение существующего cancel_ui к cb_streamer_posts. GREEN27PASS/7subtests/16.92s: реальный router/FSM/DB, fake Telegram transport, восстановленный Меню, late share отклонён, сохранённый канал не удалён, чужой actor не отменяет owner intent. Один промежуточный запуск выявил неполную late-message fixture (не было bot); fixture исправлена без изменения assertions. Scoped review: доменные проверки прежние; state/oauth необязательны для legacy прямых вызовов, действующий router передаёт их.
Ruling: сохранение page/query/card source реализуется вместе с T3, где появляется сам paginated list contract; повторно переделывать старую временную навигацию перед T3 не нужно.

## T3: complete (BASE ba5a615)

RED:42 вместо8 строк, inline delete сразу удалял; отсутствовали page/search/filter/owned-confirm APIs. Дополнительные RED: cancel оставлял действующим старое удаление; старая card могла пройти синтетическую чужую private actor/member проверку; More ещё содержал8 верхних действий.
GREEN:54PASS/7subtests/52.37s (`telegram_journey_refinement`, navigation, compatibility, add, streamer, review_regressions, tariff_copy); scoped log `T3-final-focused.log`.
Реализованы8/page, поиск по нику, All/Live, сохранение страницы/фильтра после карточки и переключателя; remote list/card показывают название канала, возвращаются напрямую с Add; inline delete требует owned10-minute one-use confirmation, отмена его блокирует. Legacy untrack callback теперь открывает подтверждение; /untrack остаётся явной командой. Сохранены старые callback регистрации и функции. More6разделов, live доступен фильтром списка, about сохраняется старым маршрутом и будет включён в помощь T6.
Навигационный кэш максимум1024 contexts и1024 delete intents,30/10минут, bound bot/chat/actor; доменные права всегда сверяются отдельно. Это не новая БД и не исполнитель. SQL только существующих таблиц, без migrations. Private cross-actor/other-user cards fail closed; remote page/delete проверяет права, старые группы не удаляются.
Scoped review: callback payloads bounded (nonce вместо query); live обозначен по последней проверке, а не обещанием текущей готовности. Старые HTML/экспорт и Mini App вне T0 не менялись. Native screenshot новых Telegram-экранов ещё NOT TESTED: пока fake transport, не выдавать за клиент.

Следующий шаг: оставшиеся T2 primary cancel/source-aware tariff back, затем T4 assets/Home и T5/T6. Отдельный полный gate и свежий reviewer остаются T7.

## T2: source/cancel complete (BASE 4d4dcf1)

Primary list/help/open_app/manage_group отменяют незавершённый owned selector и восстанавливают native Меню. Тариф из Streamer сохраняет source через secondary Viewer и payment-unavailable; назад ведёт к выбранному продукту, затем к Streamer. Старые callbacks без source по-прежнему работают, неизвестный source безопасно возвращает More; source не выдаёт права.
RED: source возвращал menu:more вместо menu:streamer. Первые четыре primary subtests имели ошибку fixture (изменение frozen CallbackQuery); это не засчитано как behavioral RED. Исправлена fixture через model_copy, временно удалены только новые primary cancel calls, получен настоящий RED pending!=cancelled на4 маршрутах, calls повторно добавлены. GREEN45PASS/4subtests/33.57s; logs T2-primary-corrected-RED/T2-final-GREEN. Assertions не ослаблены; production решения не подгонялись под fixture.
Scoped review: общий cancel_ui сохраняет saved rows; shared purchase/state/catalog/paymentOFF не меняются. Navigation source ограничен more/streamer. Старый Menu отвечает снизу, callbacks редактируют текущую карточку; ограниченный nearby repeat reuse будет проверен вместе с фото-презентацией T4.

## Ограничения

## M1: расследование (BASE e7510b1)

Владелец прислал Telegram-канал test с текстовым live-постом без media. Прочитан вчерашний ChatGPT «Проверка видеопревью стрима»: ошибка phase=analysis. Фактический production commit 4ce3887 восстанавливает default analysis temp root; dc9239e добавляет out_range=tv в обе сборки renderer. Эти две правки отсутствуют в текущей ветке. Read-only comparison не изменяет production. Дополнительно thumbnail refresh ограничен chat_id>0, поэтому зарегистрированный Telegram-канал остаётся без фото при отсутствии видео; старые группы сохраняют прежний контракт. Screenshot не подтверждает конкретную причину отсутствия видео/права владельца. Диагностика pinned staging только чтением, без grants и отправок. После M1 продолжается текущий план T4–T8.

M1 локальный PASS: exact ports двух production-файлов; channel thumbnail через текущий queue/updater и публичный caption/template, при unavailable видео — photo, действующий animation сохраняется, expiry/revoke/mute возвращают photo. Новая text-card в shared stream не ждёт общего5-minute bucket. Новых DB migrations/capture jobs/прав нет. Подпись и button сохранены; старые группы остаются text.

RED: production3FAIL; channel2FAIL/1PASS; shared/muted2FAIL. Первый muted GREEN выявил неполную async fixture: cleanup работал после db.close; fixture теперь ожидает фактическую cleanup task и shutdown до close, без изменения assertions. Focused40PASS/1existingWindowsSkip/12subtests; media211PASS/1existingWindowsSkip/43subtests/210.55s с настоящим FFmpeg и нагрузкой. Reviewer нашёл queued mute race, новый RED awaited1; apply_photo проверяет notify после обеих fresh reads. GREEN90PASS/15subtests/74.57s; reviewer самостоятельно2PASS/4.47s, открытых findings нет. Полный suite финального committed snapshot и actual staging evidence ещё впереди.

Read-only staging: samoylov___ raw video=1, effective video=0; observed broadcaster1193685437 не совпадает с linked identity/community и не имеет active streamer grant. Это существующий серверный отказ видео, его не обходить. Фото отсутствует по подтверждённому channel-only bug. Production остаётся dc9239e/deployment466499d5. Старые reports/evidence сохранены; новые QA helpers переиспользуют предыдущий guard с отдельной output-папкой, не перезаписывают прежние отчёты.

Текущий staging560f3cc не покрывает новые изменения. Реальные native, OAuth и отправки по новому snapshot ещё не проверены. Production и платёжные права не менялись.
