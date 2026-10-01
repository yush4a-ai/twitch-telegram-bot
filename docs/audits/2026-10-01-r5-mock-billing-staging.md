# R5 — mock billing на staging

Дата: 2026-10-01. Ветка `autonomous/twitchsignal-roadmap`. Production `main`, deployment, variables и DB не менялись.

## Граница реализации

`PaymentProvider` protocol и сетево-изолированный `MockPaymentProvider` с HMAC-SHA256 проверкой raw webhook. `BillingService` и SQLite ledger сохраняют order, payment, webhook replay, audit и idempotent refund request. Тестовые `TEST` units не имеют денежной стоимости. Доступ Streamer Plus выдаётся лишь после проверенного capture; verified refund отзывает только связанный `source='mock'` grant. Нет публичных checkout/webhook маршрутов, реального provider adapter, денег и выбора коммерческих условий.

## Подготовка

- Target до backup/deploy: точные staging project/environment/service и Volume instance IDs, одна replica, CLI source, production `main`; `active_target_ok=true`, `errors=[]`. Рабочее дерево commit `fd17d340bcac` чистое, `git diff --cached --check` прошёл.
- Новый online backup `/data/backups/2026-10-01-r5-pre-billing.db`: `integrity=ok`, 606 208 байт, restore 31 таблицы. Внешняя копия `%LOCALAPPDATA%\TwitchSignalBot\staging-backups\2026-10-01-r5-pre-billing.db` проверена тем же restore drill. SHA-256 обеих копий `08c22cf1f6de8547d9486006fb62b4b92126f1128d16f0e5b1506178d92ca1fc`.
- Миграция на отдельной копии внешнего backup: `r5_001_billing_ledger` присутствует, 8 schema versions, 36 таблиц, `integrity_check=ok`; hash исходного backup после теста не изменился.
- Локальный полный suite 1082 passed, 2 skipped, 317 subtests за 384,90 с. Deploy guard повторил полный suite: 1082 passed, 2 skipped, 317 subtests за 382,64 с. Отдельно 11 R5 lifecycle/drill tests и 7 subtests прошли после проверки старого capture replay после refund.

## Deployment и smoke

- Commit `fd17d340bcac`, staging deployment `fa758200-bb09-47d0-9cca-fdeab33b204f` terminal `SUCCESS`. Независимый Railway status: `active_target_ok=true`, активен тот же deployment, `errors=[]`. Production active commit остался `6074744aefe2ee6a314760d86f95684733f8c05f` со статусом `SUCCESS`.
- Staging `/healthz` и `/streamer` → 200. Без сессии `/streamer/api/profile`, `/streamer/api/stats`, `/admin/api/snapshot` → 401. Staging `getMe` → `TwitchSignalTestbot`.
- Активная staging DB после миграции: `r5_001` имеется, `PRAGMA integrity_check=ok`, billing orders/payments/events/refund requests = 0. Закрытый `python -m scripts.staging_mock_billing` запущен внутри точного staging runtime и отдельной временной DB вне `/data`: один paid/capture, один refunded/revoke, один cancelled, один expired, один mock grant, injected rollback подтверждён, `integrity=ok`. После drill активная staging DB сохранила 0 billing rows и `integrity=ok`.

## Граница приёмки

Это инженерный mock сценарий, без платёжного UI и реального provider webhook. Реальные транзакции, возвраты и выбор провайдера не проверялись и требуют отдельного решения владельца. Telegram Login Widget на staging по-прежнему сообщает `Bot domain invalid`; положительный owner/streamer UI E2E с реальным Telegram/Twitch аккаунтом не заменяется синтетической подписью. R6 разрешён как отдельная staging simulation; пользовательский pilot на 5–10 реальных стримерах не заявлен.
