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
- C production-admission runtime3fa8b649e737693eef94c3912c22313834f57586: expanded11PASS/85subtests, reserved URL placeholders fail closed. Queue/getMe/OS writer-lock guards проверены без внешних API.
- Дополнительные B2/Proxy: replay после фактической ledger mutation сохраняет один grant/order и refund; spoofed Forwarded/XFF не обходит peer quota и не удаляет valid state. Только sandbox fixture/local HTTP.
- D tooling: RED для отсутствующего инструмента, Windows runner/network guard, sealed-WAL sidecars, unexpected additions и backup stale WAL. Scoped reviewer независимо подтвердил IOCP причину, source sidecar mutation и неверный прежний порядок rollback. Исправлены audit-hook без замены socket type, immutable sealed reader/SQLite backup API, expected-additions allowlist, restore+old-open после new migration/reopen. Synthetic migration/key/HTML/rows/rollback проходят; это не representative production D.
- Финальные focused110PASS/118subtests,80.94s: B1/B2/C/D tool, proxy, real ledger, queue gate, legacy и mock/paymentOFF. Новых skips нет. Логи очищены только от trailing whitespace для git diffcheck, результаты/assertions не изменены.

- Первый final full5474 RED1634PASS/2FAIL/2existingWindowsSkips/3741subtests/1144.44s. Новый whole-Git tar in-memory повышал lifetime RSS выше256MiB; старый copy audit требовал ложного raid exemption. Scoped reviewer подтвердил rootcause, combinedRED2fail20pass, streaming exact archive и строгая canonical copy дали22PASS/6subtests. Bot runtime unchanged; обязательный новый clean tooling/full gate после correction, не повтор старого validated результата.

## Итоговая проверка8690094 — 04.10.2026

Итоговый gate CONSOLIDATED PASS на869009401fc638405694297b8449e04c1d96abe8: полный1636PASS/2existingWindowsSkips/3739subtests/1136.57s, exit1 из-за двух byte-invariant subtests. Managed checkout с core.autocrlf=true добавил CR в .python-version/Procfile; immutable Git blobs и primary checkout уже имели правильные LF/хеши. Только validation bytes восстановлены из HEAD; свежий whole packaging15PASS/12subtests/0.40s, exit0. Код/tests/assertions/HEAD/deps/global Git config не изменялись. Scoped reviewer подтвердил reuse1636PASS без третьего full. Distinct consolidated1636PASS/3741subtests/2existingSkips; overlap15tests/10subtests не суммируется. FULL-BC.log/FULL-SECOND-RED.json сохраняют полный RED как RED, не fullPASS. FULL-GATE.json, PACKAGING-RECHECK.log и CHECKOUT-BYTES.json связывают correction и PASS. Первый code-related RED также сохранён. Далее только docs/evidence.

Полный прогон выполнен в отдельном clean validation checkout того же коммита только для тестов, без второго разработчика. Чужие untracked output/imagegen assets в основной папке сохранены без move/delete/stage; вся рабочая папка с ними не объявляется clean. Материализованный handoff с literal final HEAD находится рядом с внешним production-checkpoint.json.

B/C закрыты локально; D/production identity/external restore/native/OAuth — OWNER INPUT REQUIRED. Production untouched YES, payments OFF YES, cutover NO.
