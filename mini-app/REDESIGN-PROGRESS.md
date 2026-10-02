# Редизайн Mini App — выполнение P01–P21

Основание: финальный промпт владельца `650afe6a-f60f-4201-bc2e-938a49c9d84a`. План: `docs/superpowers/plans/2026-10-02-mini-app-redesign-plus-staging.md`.
Исходный HEAD `e0d44c315a1c81dc5b310dd1f3c7f3169915c985`, ветка `autonomous/twitchsignal-roadmap`. Один исполнитель. Production и деньги запрещены; staging после всех gates разрешён.

## Видимый план

| Этап | Статус |
|---|---|
| Сохранить основу и обязательные поправки плана | В работе |
| P01 — временная БД, сценарии и baseline | Следующий |
| P02–P07 — схема, каталог, наследование, provider/lifecycle | Ожидают P01 |
| P08–P12 — темы, shell и Viewer | Ожидают P07 |
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
