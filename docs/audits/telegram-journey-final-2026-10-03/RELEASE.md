# Telegram T4–T8 — тестовый выпуск 03.10.2026

## Вход

- https://t.me/TwitchSignalTestbot → «Меню» / постоянная кнопка «Приложение».
- https://worker-staging-2f74.up.railway.app/app — shell; реальные данные требуют подписанного Telegram initData.
- Release `00c6093d87d1e2617ddd19a736c42aab0d14e1d6`, deployment `603eeb44-0bca-4a28-a1e1-4d365b5db377` SUCCESS.

## Изменения

Приветствие и compactHome с тремя ранее одобренными PNG, честные tracking/notify counts и unknownlive. Add ещё/отмена, импорт с ownership/preview/limit, channel guide/help/resume/fresh readiness, quiet local preview и nonce+actor+chat+expiry confirmation. Help о возможностях/импорте/тихих часах/отчётах, сохранённые UTC даты эфира в ручном и автоматическом отчёте, Free fullHTML, точные описания тарифных единиц и paymentOFF до checkout. Review fix: Cancel/expiry/concurrent confirmation после permission wait блокируют delete; import/legacyAdd возвращают8/page.

**Новых изображений: 0.** Approvedwelcome/home/channelguide скопированы без изменения; важные инструкции остаются текстом, подробности доступны по кнопкам. ASSET-IDENTITY.json подтверждает исходники и hashes. Нативных screenshot новых Telegram экранов нет; подмены макетом не делалось.

## Проверки

- Fresh reviewer b294825→1d1cca7,5новыхRED→PASS scenarios, finalfocused52PASS/4subtests. REVIEW.md.
- Финальный штатный full guard:1541PASS/2existingWindowsSkips/3638subtests/1268.11s, FULL-release-PASS.log. Три предыдущих failed attempts сохранены; два прерваны после подтверждённых fixture failures, один полностью завершён красным. Полный PASS повторён на итоговом clean commit, bypass нет.
- Immutable committed package671files/16,081,745bytes, исключены только audit/design upload bytes; исходные артефакты сохранены. RELEASE-PACKAGE.json.
- Actual smoke195runtime SHA256 включая все3PNG,17HTTPassets+shell, bot8859004067/TwitchSignalTestbot, постоянный MenuButtonWebApp«Приложение»,9unsigned401,27schema versions/integrityok/FK0/paymentOFF/livecallback404/legal503. STAGING-staging-smoke.json. Bounded100deploymentlogs patternmatches0, не полный historical audit.
- Fresh stagingbackup3,108,864bytes/integrityok/63tables; remote/local restorePASS. STAGING-backup.json. Migration-copy reuse после13schema/cipher modulebyteidentity, не новый migration run. MIGRATION-IDENTITY.json/STAGING-migration-copy.json.
- Chromium/WebKit T0 по232checks/58PNG reused после18websource blob identity; BROWSER-IDENTITY.json. Это browser emulation, не Telegram client.
- Production `466499d5-6979-4a81-8aee-67efcf628976` / `dc9239eb0b82fb80d1788fc657205740cebc49e9` неизменён. Payments/invoices/providerPOST/grants на staging не выполнялись; outbound testmessages0.

## Открытая клиентская приёмка

NativeDesktop/iOS/Android, OAuth владельца, signed-live app journeys, адресная реальная доставка и визуальное принятие владельцем: **NOT TESTED**. Новых сообщений в Telegram ради QA не отправляли. На попытке наблюдения Desktop был перекрыт приложениями владельца и показывал стороннюю переписку; её в evidence не сохраняли. SCENARIOS.md перечисляет реальные проверки и ожидаемые действия владельца. Production readiness и bank approval не заявляются; legal503/paymentOFF остаются.

## Решения исполнителя и цена ошибки

- Продолжить уже разрешённый план после M1: промежуточный выпуск не отменяет работу. Риск ошибки — лишний обратимый staging объём.
- Сохранить branch/workspace/HTML/export/все исторические evidence; не применять cleanup из skills. Цена — место на диске.
- Вести visible ledger вместо отсутствующих community Bash helpers; не менять global installations. Цена — ручной bookkeeping.
- Nearby Menu завершить вместе с photo Home T4, source/page contract — вместе с paginated T3; staged разрешение сохраняется. Риск — временное различие промежуточных checkpoints, не итогового кода.
- Сохранить текущие catalog product titles «Зритель Plus»/«Стример Plus» и150/300 в copy-entry задаче; не выполнять global replace. Риск — отдельное решение о наименовании останется для владельца.
- Использовать3approvedimages и текстовые инструкции вместо дополнительных иллюстраций на каждое действие. Цена — нет новых generated variants; точные шаги можно менять доступным текстом.
- Reuse browser/migration evidence только при byte identity; свежий backup отдельно. Цена ошибки проверки identity — незамеченная regression; proof hashes сохранены.
- Re-grade42-row import/add result как Important относительно согласованного8/page; исправить в одном review pass. Цена — дополнительные scoped legacy regression tests, без неподтверждённого заявления об API отказе202buttons.
- Исправлять старые fake Message/DB contracts, сохраняя owner isolation/partial selection/request-id/replay assertions. Цена — повтор полного suite; failures не скрыты.
- Оставить ветку autonomous/twitchsignal-roadmap локально без push/merge, соблюдать уже утверждённый pinned staging выбор; не задавать routine confirmation. Цена — интеграция в production остаётся отдельным решением владельца.

Deferred Minor findings: нет; pagination finding re-graded/fixed. Declined external scenarios остаются NOT TESTED, а не удалены из checklist. После release изменяются только документы/evidence; новый docs HEAD не объявляется опубликованным runtime.
