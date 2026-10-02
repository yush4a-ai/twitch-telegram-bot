# Редизайн Mini App — выполнение P01–P21

Основание: финальный промпт владельца `650afe6a-f60f-4201-bc2e-938a49c9d84a`. План: `docs/superpowers/plans/2026-10-02-mini-app-redesign-plus-staging.md`.
Исходный HEAD `e0d44c315a1c81dc5b310dd1f3c7f3169915c985`, ветка `autonomous/twitchsignal-roadmap`. Один исполнитель. Production и деньги запрещены; staging после всех gates разрешён.

## Видимый план

| Этап | Статус |
|---|---|
| Сохранить основу и обязательные поправки плана | Commit `3f4fec5` |
| P01 — временная БД, сценарии и baseline | PASS: 11 tests/21 subtests, Chromium/WebKit 6 PNG |
| P02–P07 — схема, каталог, наследование, provider/lifecycle | P02–P07 PASS |
| P08–P12 — темы, shell и Viewer | P08 PASS; далее P09 |
| P13–P17 — Streamer, Plus, purchase, legal, compatibility | Ожидают P12 |
| P18–P19 — browser/media, полный suite, review, backup | Ожидают P17 |
| P20–P21 — guarded staging и самостоятельная приёмка | Ожидают gates |

Viewer150 ₽; Streamer300 ₽. Режим определяет основное предложение Plus; основной внутренний переключатель тарифов удаляется. Первый release без внешних payment POST/invoices/grants из заглушки. Bank NOT READY; реальные Telegram/OAuth/оплаты/native NOT TESTED.

## Рабочие решения

- Ruling: сохраняем явно выбранные владельцем папку/ветку — новый worktree не нужен — цена ошибки: смешивание файлов; staged проверяется перед каждым commit.
- Ruling: используем существующие зависимости — глобальные установки запрещены и runtime уже есть — цена ошибки: остановка focused gate.
- Ruling: ledger/briefs ведём напрямую в PowerShell/Python — installed executing-plans helpers требуют Bash — тот же последовательный процесс без второго исполнителя.
- Ruling: scoped tests в задачах, full suite финального snapshot — прямой план владельца имеет приоритет над общим советом полного suite на каждом шаге — новые изменения инвалидируют final evidence.

## Предварительная сверка интерфейсов

P01 временный fixture/QA → все browser tasks; P02 Money/ProductSnapshot/attempt/store → P03/P05/P06; P03 каталог/policy → purchase/legal; P04 effective predicate/resolver → все API/SQL/media; P05 adapter → P06 ledger/reconcile; P06 common apply → P07 Stars; P08 тема/SDK → P09 shell; P09 router/profile → P10–P17; P13 structured permissions → Streamer/Posts; P16 canonical legal/support → P17/P18; P18 QA → P19 gate → P20 deploy → P21 owner package.

Новые межзадачные имена берём из §4.1 плана. Legacy adapters/readers сохраняют совместимость; client mode/paid не источник прав.

## P01 complete

BASE `3f4fec5` → отдельный QA commit; evidence `docs/audits/mini-app-redesign-plus-2026-10-02/BASELINE.md`. RED/PASS/scoped review выполнены. Продуктовый runtime ещё прежний; следующий P02.

## P02 complete

BASE `ac37c8c` → отдельный migration commit. PASS28tests/5subtests; `P02-MIGRATION.md`. Scoped storage/migration review выполнен, staging DB не менялась. Следующий P03.

## P03 complete

BASE `e962a0b` → catalog commit. RED10 → PASS25tests/22subtests; `P03-CATALOG.md`. Каталог150/300, все методы недоступны для денег; следующий P04.

## P04 complete

BASE `4be57d4` → inheritance commit. RED → PASS130tests/43subtests; `P04-INHERITANCE.md`. Frozen buyer/общие SQL/media gates/непрерывный срок; scoped security/data/media review. Два старых refund assertions уточнены с проверкой конкретного продукта, по прямому решению владельца. Следующий P05.

## P05 complete

BASE `8474eb8` → provider commit. RED→PASS32tests/57subtests; `P05-PROVIDER.md`, 13/13 официальных SHA совпали. Только network-free injected transport, реальные transport/routes не включены. Card canonical enum и metadata/merchant admission не выдуманы; live запрещён. Scoped provider/security review выполнен. Следующий P06.

## P06 complete

BASE `93461aa` → lifecycle commit. RED→PASS72tests/57subtests; `P06-LEDGER.md`. Durable inbox/attempt, общий apply, frozen права, monotonic facts, refund/reopen, cross-key guard и shared lease concurrency1 проверены. Четвёртая миграция той же БД; runtime/callback OFF. Scoped ledger/security review; следующий P07 Stars с fake sender.

## P07 complete

BASE `7f8b920` → Stars commit. RED→PASS53tests/74subtests + compatibility52tests/11subtests; `P07-STARS.md`. Typed Bot API/frozen charge/common apply, precheckout без grant, no repeat unknown и OFF wiring. RED старого throttle закрыт, money env не активируют release. XTR/период только явная fixture; `/paysupport` P16. Следующий P08.

## P08 complete

BASE `2a71f82` → theme commit. Python9tests/20subtests; Chromium/WebKit по7PNG, errors/external0. `P08-THEME.md`; light default/own neutral dark/actual ThemeParams/events/denied storage/safe4/viewport/dispose/fallback. PNG viewed, transition кадры сохранены перед пересъёмкой. Native NOT TESTED. Следующий P09.
