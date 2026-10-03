# Доработка Telegram-путей — 03.10.2026

План: `docs/superpowers/plans/2026-10-03-telegram-journey-refinement.md`.
BASE b29482589be27adefaad4d30e7be635c0695cf73; clean, autonomous/twitchsignal-roadmap.

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
