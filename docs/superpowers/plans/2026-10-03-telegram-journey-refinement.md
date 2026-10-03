# TwitchSignalBot: доработка Telegram-путей

Основа: утверждённый владельцем `C:/Users/yusha/Documents/Codex/2026-10-03/realtime-voice-chat-4/outputs/TwitchSignalBot-plan.md`; три одобренных PNG из той же папки. Последнее уточнение copy: вход в описание — «Тариф» / «О тарифе» / «Посмотреть тариф», активный доступ — «Моя подписка». Названия продуктов и покупка сохраняются.

BASE: `b29482589be27adefaad4d30e7be635c0695cf73`, ветка `autonomous/twitchsignal-roadmap`, чистая. Mini App release560f3cc и свежие исправления героя/swipe/Undo сохраняются.

## Порядок

- [x] T0. Адресная сверка copy Mini App/Telegram/покупки/помощи/документов, исправление оставшихся входов, browser RED→PASS.
- [x] T1. Карта актуальных Telegram-путей и контрактов: права, источники Back, личный/публичный контекст, фильтры начала эфира и категории. Классификация фактов аудита и новых предложений.
- [x] T2. Безопасная отмена/источник возврата реализованы ранее; T4 завершил nearby repeat reuse (≤2 сообщений/45s), свежую карточку внизу и снятие старых кнопок. Fake transport PASS; native acceptance отдельно.
- [x] T3. Поиск/8 стримеров на странице/Все и В эфире, сохранение контекста карточки; удаление с подтверждением; компактное «Ещё».
- [x] M1. Восстановление анализа видео из 4ce3887, нормализация диапазона из dc9239e и фото Telegram-канала при недоступном видео. RED→PASS, scoped review, full1517/2existingWindowsSkips/3638subtests; release160703a/ac62b912, actual smoke PASS. Обычный poller обновил сообщения28/29 до photo; ручные тестовые отправки и Native AFTER NOT TESTED. Evidence: M1-RELEASE.md.
- [x] T4. Одобренные три PNG скопированы без изменения; compact photo Home, tracking/notify, последнее наблюдение/unknown и незавершённый channel step. RED→GREEN51PASS/4subtests; scoped self-review. Публикация и native evidence — T7/T8.
- [x] T5. Добавить ещё/отмена, импорт с явным OAuth cancel и защитой чужого подтверждения, channel guide/help/resume и readiness по свежим правам, quiet preview/one-use confirmation/quick UTC. RED→GREEN54PASS/4subtests. Native/OAuth/outbound NOT TESTED; Mini App ссылки сохраняют текущий router contract.
- [x] T6. Помощь с отдельными инструкциями, даты/UTC в ручном и автоматическом отчёте, точные единицы тарифа и paymentOFF до checkout. Free/HTML сохранены; фильтры/категории проверены на изолированных данных. RED5→GREEN70PASS/10subtests, отдельный report/compat run51PASS.
- [ ] T7. Один fresh read-only reviewer; исправления RED→PASS; полный suite финального snapshot, backup/restore/migration-copy, browser/native evidence с честными ограничениями.
- [ ] T8. Clean commit → штатный guarded pinned Testbot release → actual SHA/бот/runtime/paymentOFF/production unchanged. Отчёт и ручной checklist.

## Правила исполнения

Один исполнитель, максимум один read-only reviewer. RED→код→PASS→scoped review→commit на ограниченный пользовательский путь. Никаких production, push/merge, денег, owner OAuth или изменений списка/прав владельца ради проверки. Локальные fake sender/temp DB допустимы; фактическая доставка отдельно от симуляции. HTML/export и исторические артефакты сохраняются. Не менять Mini App вне адресной copy-правки и существующего входа. Не запускать R0–R9/whole graphify заново.

Ruling: не удалять workspace/evidence после ревью — прямое требование владельца сохранять резерв важнее рекомендации executing-plans. Цена: локальные артефакты занимают место.
Ruling: текущие отображаемые названия из catalog («Зритель Plus» / «Стример Plus») не переименовывать в рамках copy-входов; идентификаторы viewer_plus/streamer_plus и цены150/300 сохраняются. Любая смена имен продуктов — отдельное решение.
