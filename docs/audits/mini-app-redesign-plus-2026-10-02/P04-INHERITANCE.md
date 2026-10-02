# P04 — общий личный доступ Viewer

BASE `4be57d4`; branch `autonomous/twitchsignal-roadmap`. Одна существующая схема БД; тесты используют только временные файлы.

## Что изменено

- `entitlements.py`: единый внутренний SQL predicate и resolver. Viewer принадлежит своему Telegram ID; Streamer даёт Viewer только неизменному beneficiary. Actor, нынешний Twitch owner и участники сообщества не получают личных прав.
- Новые test Streamer grants связываются с явно указанным verified buyer; legacy unbound остаются без личного наследования. Mock capture сохраняет frozen buyer заказа; Viewer/trial тоже записывают собственный beneficiary.
- Все действующие Viewer SQL gates: video, filter CAS, tracked snapshot, category INSERT SELECT, history write/read, trial eligibility. Остальные API/folders/reminders/commands/runtime пользуются `has_viewer_plus` либо общей destination проверкой. Group Streamer placement по-прежнему требует своего verified broadcaster/placement.
- Server subscription state возвращает оба источника и конец непрерывного доступа. Future grant через разрыв не обещает действующий срок. Reader конкретного продукта сохранён.

## RED → PASS

- RED: отсутствие `entitlements` — `P04-red.log`.
- Расширенный первый run: 129 passed / 43 subtests, один старый тест ожидал исчезновения Viewer после его refund при всё ещё активном Streamer. `P04-compat-red.log` сохранён.
- Это ожидание изменено по утверждённому наследованию: конкретный Viewer grant отсутствует, effective Viewer остаётся от Streamer, его следующий refund выключает оба права. Проверки ownership/refund/history не удалены. Fixture Streamer также теперь явно bound и ожидает Viewer.
- Финальный focused run: **130 passed / 43 subtests, 0 failed**, 133.90 s — `P04-pass.log`. Включает inheritance/capabilities, limits/slots/media, filters/delivery/folders/history/reminders/trial/category, Streamer access, billing/Subscription, fixture/migration.
- Два соединения: CAS выбирает одного победителя; revoke после capture и перед animation edit запрещает edit. Реальный код LivePostUpdater с fake sender возвращает существующую animation в photo после expiry, сохраняя message ID/выбор. Старые callbacks и шестой слот остаются закрыты.

## Scoped review

Просмотрен diff; scoped `rg` всех `subject_kind=viewer` оставил только конкретный reader/revoke, миграцию и исторический growth funnel. Correlated SQL context проверен непосредственно SQLite и сценариями category/history/media. Общий predicate принимает только внутренние identifier/placeholder, без client interpolation. Эффекты в BEGIN IMMEDIATE не обходят права. Независимый Viewer survives Streamer revoke; unbound/stranger/actor/future gap отрицательные случаи пройдены. Никаких skips или ослабления финансовых/placement assertions.

## Границы

Full final suite ещё не запускался. Этот checkpoint не является bank/Telegram/native/OAuth acceptance. Staging/production/реальные платежи/отправки не изменялись. UI пока прежний; следующий P05 — локальный Platega adapter.
