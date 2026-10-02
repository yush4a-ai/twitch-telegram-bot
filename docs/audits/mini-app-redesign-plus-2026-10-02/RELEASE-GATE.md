# P19 — gate инженерного выпуска

## Текущий статус

P19 PASS: финальный повтор полного pytest завершён с exit0 — 1403passed,2skipped,3248subtests за903.06s. Код релиза основан на c995b90c; после него изменено только точное ожидание списка миграций в test_growth_schema. Штатный guard на чистом committed snapshot повторил suite1403passed/2skipped/3248subtests/850.45s и завершил deploy active SUCCESS `fba34520-f2a7-4d5d-aa86-1e8e3b0072b1`, код `52e56e9633a77f7e5025a1be7dacbbf8c0769970`. Actual smoke PASS; STAGING-ACCEPTANCE.md фиксирует P20/P21 и ограничения.

## Закрытые проверки

- P18:92browserjourneys/2520matrixchecks/984PNG/174runtimeSHA; Chromium/WebKit, реальные browser controls zoom200 отдельно от text200; fake SDK/native NOT TESTED. Новая media-нагрузка с сохранёнными пределами;1×1000 RESOURCE_STOP25s не переименован PASS.
- Один reviewer провёл whole-change diff относительно3f4fec и trace auth/IDOR/ledger/OAuth/media/frozen beneficiary/DOM/legal. F1 inheritedXFO исправлен только `/app`; exact official Web origin допускается, чужой блокируется. Header/auth/admin/OAuth/legal31tests44subtests PASS, все4PNG/SHA independently checked. [Telegram описывает iframe](https://core.telegram.org/api/web-events); [WebA/WebK](https://telegram.org/apps) обосновывают exact origin.
- `mini-app-threat-model.md` и `P19-READONLY-REVIEW.md` фиксируют границы, угрозы и пределы. Нет новых подтверждённых runtime defects после F1. Полный исторический production аудит не заявлен.
- Backup remote/local/restore и миграция копии54→60tables/25versions PASS; две Fernet fields проверены действующим staging key внутри контейнера; allold data/reopen/failure rollback preserved, backup retained. `MIGRATION-COPY.md`.
-49focused schema/migration/backup/guard/packaging tests и19subtests PASS. Static asset allowlist/imports/strict headers/packaging защищены тестами.
- Деньги OFF в immutable first-release policy и purchase prepare503, live Platega callback не устанавливается. Owner-only прежние QA routes/добровольный one-time trial отделены от обычного purchase flow; автоматически grants не выдавались.
- HTML/export/legacygroups/Free/старые callbacks/common media/6th/quiet-raids/MenuButton сохранены и покрыты финальным suite.

## Первый полный gate RED

`P19-full-suite-red.log`:2failed в двух old_schema subcases одного `test_growth_schema`;1403passed,2skipped,3246subtests,1008.75s. Единственная причина — старый exact20versions/count после утверждённых R11_001–005. Добавлены пять явных значений, reopen теперь сравнивает точный ранее проверенный список вместо количества. Tables/query-plan/duplicate/rollback assertions сохранены, scoped reviewer подтвердил отсутствие ослабления. Runtime этим исправлением не менялся. Обязательный повтор завершён: `P19-full-suite-final.log`, exit0,1403passed,2skipped,3248subtests,903.06s. Все прежние проверки таблиц/плана запросов/повторной миграции/rollback сохранены.

Оба существующих skips — WinError1314 при symlink в concat/input tests; новых skips нет. Linux/native прохождение этих двух filesystem случаев не заявлено.

## Подготовка P20

Используется существующий штатный `scripts/staging_deploy.py`, без обхода чистого tree, pins, full suite или raw railway up. Отдельный одноразовый operational probe сохраняется как evidence, live API bypass не создаётся. До actual smoke его selftests не означают live PASS.

TEMP smoke guards: foreignbot/wrongSHA/asset/health rejects; reviewer обнаружил gaps missingadmin/substitutedasset/trialfalse. RED3fails→exact5unsigned/exact14canonicalassets/trialTrue PASS. Collector Config field typo дополнительно RED→ASTcontract PASS; actual read uses telegram_bot_token. История RED/PASS сохранена. После actual header/archive findings одноразовый probe исправлен адресно:7selftests PASS, exactDENY/полные наборы/раздельные raw artifact и server-normalized HTTP hashes защищены. Настоящий collector прошёл PASS; runtime не менялся.

Actual probe после deploy: pinned runtime, deployment/meta SHA/all174Gitfile bytes/14HTTPassets/getMe/MenuButton/health25migrations/integrity/foreignkeys/unsigned401/moneyOFF/callback404/legal503, zero secret matches в HTTP. Bounded последние100deploymentlogs проверяются только в памяти по credential patterns, наружу выходят count/SHA/identity. Это не гарантия всех исторических logs. Signed live API/ownerOAuth/реальные send/provider/native остаются NOT TESTED.

## Пределы release

Инженерный staging с ожидающей owner acceptance; production не трогать. Merchant/month/XTR/refund/chargeback/upgrade/operator/support/retention/НПД gates остаются. Реальные credentials не нужны заглушке и не запрашиваются. Bank package NOT READY; подготовленные legal sources не равны принятию владельцем. Старый binary не безопасный автоматический rollback после новой схемы.

## Итог P20–P21

Guard повторный full gate и actual pinned smoke PASS; [STAGING-ACCEPTANCE](STAGING-ACCEPTANCE.md). getMe/menu/174artifactfiles/14HTTPassets/25migrations/integrity/unsigned401/paymentOFF/production compare доказаны. Native/signed live API/ownerOAuth/real send/payments NOT TESTED; legal503/support absent/bank NOT READY. Final audit-only commit не меняет runtime и не заменяет deployed release SHA.
