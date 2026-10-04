# Продолжение preparation — 04.10.2026

База b175782; один исполнитель, один read-only reviewer. Production/deploy/сеть/деньги запрещены. Спецификация — запрос владельца attachment70c196b6 и текущий cutover handoff.

1. B1: D-048 не утверждает исключение raid. Сохранить runtime; RED справки, матрица Free live/raid/quiet/exemption/retry/community, исправить два текста, review/commit.
2. B2: изучить aiogram acknowledgment и все registered updates. Durable ingress до offset ACK, bounded parallel handlers/recovery; платежные факты повторяемы через прежний ledger. Неопределённые обычные действия после crash не повторять автоматически, сохранить для оператора. RED restart/replay, focused PASS, review/commit.
3. C: immutable operator JSON вне Git без секретов, opt-in + exact local Railway/volume/path/secret/queue/single-writer/paymentOFF validation, затем getMe до DB. Отдельный production queue opt-in; не расширять staging guards. Proxy request.remote остаётся fail-safe, границу production подтвердит оператор. RED negative/side-effect tests, focused PASS, review/commit.
4. D: source copy не предоставлена. Подготовить offline-only воспроизводимые tooling/tests/runbook для backup/migrate/restore/old artifact/decrypt; synthetic fixture не закрывает D. Без owner inputs итог только PREPARED, не READY.
5. Final: clean snapshot/diffcheck/focused/fresh scoped review, один полный pytest после последнего runtime change; docs/evidence/owner inputs/native checklist/final SHA.

## Журнал

- Initial: branch autonomous/twitchsignal-roadmap, clean b175782. Старые SEC/UX и их evidence не переоткрыты. Runtime9e89930; production untouched/paymentOFF.
- Ruling: workflow scripts из community skill не исполняются; постоянный компактный журнал здесь заменяет временный skill workspace. Прямой порядок владельца и единственный writer имеют приоритет.
- B1 complete9888455: 2 RED →24 PASS, scoped reviewer no blockers; copy-only, runtime quiet unchanged.
- B2 ruling: sequential processing блокирует долгий OAuth/отмену; journal received до yield/offset сохраняет concurrency с общим лимитом32. Ошибки fixture cleanup и sorted schema oracle исправлены без ослабления coverage.
- B2 reviewer: recovery cancellation/orphaned polling/раздельный бюджет доказаны RED; исправлены ownership polling/recovery/handlers, параллельное recovery и shared cap32.
- C RED66failed/4pass/18subtests →42PASS/95subtests; expanded10PASS/81subtests. Операторский contract заполнить настоящими данными вне Git, реальные ID не выдуманы. Scoped reviewer C no blockers.
