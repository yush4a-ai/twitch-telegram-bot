# Доработка Telegram-путей — 03.10.2026

План: `docs/superpowers/plans/2026-10-03-telegram-journey-refinement.md`.
BASE b29482589be27adefaad4d30e7be635c0695cf73; clean, autonomous/twitchsignal-roadmap.

## T0: complete (BASE b294825)

Большинство входов уже соответствует правилу владельца. Оставшийся `Подробнее о Plus` в подробностях Mini App заменён на «О тарифе», маршрут product:details сохранён. Старые документы/аудиты/имена продуктов не переписываются.

RED: обновлённый пользовательский переход отсутствовал в viewer/streamer, four failed checks + locator timeout. Это ожидаемое расхождение, а не ошибка окружения; предшествующий MODULE_NOT_FOUND решён выбором установленного bundled NODE_PATH без установки пакетов.
GREEN: быстрый141checks/28PNG, Chromium232checks/58PNG и WebKit232checks/58PNG. Реальные UI-модули, fixture-данные, сеть заблокирована. Current hero/swipe/Undo/regressions сохранены.
Focused pytest: tariff copy/Telegram Plus/Mini App user copy/subscription/purchase —24PASS/3047subtests/22.18s. Единственное предупреждение: нет записи в старый .pytest_cache; assertions/skips не менялись. Полный suite запланирован на финальный snapshot.
Evidence: `docs/audits/telegram-journey-2026-10-03/copy/`. Scoped diff review: одна runtime-подпись; QA selectors отражают правило владельца и продолжают проверять реальный переход, цену и состав features. Stop-Slop copy pass: «О тарифе» обозначает назначение ссылки.

## T1: выполняется

Сверка исходного аудита с текущим кодом: /start и «Меню» уже отвечают внизу чата (show_home previous=None), поэтому старое замечание не воспроизводится этим кодом и не требует возврата скрытого редактирования. Переход streamer:posts действительно не вызывает cancel_ui — незавершённый selector может остаться. Следующий RED покрывает отмену и поздний chat_shared без сохранения соединения.
Фильтр viewer_filter применяется к личному стартовому сигналу: точное название игры (casefold), любое из включённых слов/фраз как подстрока, исключения имеют приоритет; группы обходят личный фильтр. Отдельный category detector требует стабильной смены категории. Новый сигнал только от изменения заголовка не входит в этот контракт; не изобретается в рамках проверки.

Pre-flight interfaces: T2/T3 — текущая Telegram reply_markup/источник возврата; T3/T5 — Add target/return; T4/T5 — media-caption/keyboard restoration; T6/T7 — существующие фильтры и права; T7/T8 — неизменный snapshot и штатный guard. Источники page/query будут только серверной navigation-state, без изменений доменных прав.
Ruling: Windows bookkeeping ведётся видимым ledger без запуска отсутствующих Bash community helpers. Модель/усилие основного исполнителя сохраняются.

## Ограничения

Текущий staging560f3cc не покрывает новые изменения. Реальные native, OAuth и отправки по новому snapshot ещё не проверены. Production и платёжные права не менялись.
