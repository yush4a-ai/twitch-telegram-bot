# P12 — настройки зрителя

BASE `9bb081f42dec8d3a81763bc736764f7183c0883e` → отдельный checkpoint commit. Только локальный runtime `/app`, signed fixture actor501, временная SQLite; внешних отправок/платежей/OAuth/deploy нет.

## RED → PASS

- Quiet: прежний экран профиля → отдельные «Уведомления». Сохранение UTC/offset, reload, выключение, сводка остаются Free. DEFAULT сводки включён сохранён.
- Filter/folders: отдельные формы, личное пустое правило, reset к папке, собственный черновик после409, явный повторный save, folder CAS/reload/delete сохраняют подписки.
- Category: воспроизведён late response старого запроса; AbortController/version устраняют подмену результатов. Verified category IDs, reset и reload проверены.
- Reminder: RED отсутствующего сохранённого времени → actual due_at. 15→30→cancel/reload, disabled controls до ACK. Отдельная fixture проходит настоящий begin_delivery fence без sender: reschedule/cancel409, статус sending без действий.
- History: RED отсутствующего входа → один экран из обоих профилей. 20+5 своих terminal outcomes sent/suppressed/unknown, foreign исключён, retry без дублирования, initial render без reentrancy, reset/late pagination abort. Free не получает историю; quiet остаётся доступным.
- Pending: RED editable полей папки/фильтра/category/quiet/move → общий guard формы, canonical refresh после ACK. Поздняя мутация не возвращает покинутый detail.

Python: **66 tests, 34 subtests PASS**, `P12-pass.log`,89.55s. Additional fixture RED→PASS23tests/18subtests до общего focused набора. Backend detector/queue/retention/лимиты не переписаны.

Chromium151.0.7922.34 и WebKit26.5: **18 PASS reports,78 PNG** в `P12-final-*`, JS errors0/external requests0. SHA всех sources и PNG сопоставлены текущему snapshot. 9 bounded parts × 2 engines; viewport390×844. Конечные screenshots viewed для quiet, filter, folder, reminder, history; большой viewport/theme/text200 gate следует P18.

## Scoped review

Один read-only reviewer `/root/p09_focus_review` установил ошибочный исходный DEFAULT1 в quiet-pending QA: .check не отправлял запрос, .click выключал сводку, ожидание старого true принималось за ACK. Независимый TEMP trace: requestfalse/ACKfalse/statefalse/settledUIfalse согласованы. QA теперь сначала явно UI false+canonical false, затем held click true+reenabled+canonical true. Default/assertions продукта не ослаблены. Исходные FAIL reports/logs сохранены; исправление heading→exact text для panel соответствует actual strong DOM, не убирает наличие обязательного текста.

Self review: owner-only/API/CAS preserved; busy/drafts/back/fresh reads/category/history disposal проверены. Impeccable detector findings[]; изменённые пользовательские строки проверены stop-slop. Полный cross-surface copy gate P17, полный suite P19, независимый final review P19 ещё впереди. HTML/export/архивы сохранены.

Native Telegram/real queue delivery/SDK/native keyboard/OAuth/production/staging — **NOT TESTED** этим этапом. Fixture terminal sent — подтверждение fake sender, не реального Telegram. Следующий этап P13.
