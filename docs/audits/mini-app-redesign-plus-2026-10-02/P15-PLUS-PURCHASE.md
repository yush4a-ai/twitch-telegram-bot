# P15: Plus и путь покупки

База0891997 после P14 bc5e354. Один исполнитель; один scoped read-only reviewer p09_focus_review.

## Изменения

Один Subscription для Plus/профиля/меню. Серверный каталог определяет предложение режима: Viewer150 ₽/месяц, Streamer300 ₽/месяц. Четыре SVG-блока, полное раскрытие, включённый Viewer и вторичный Viewer у стримера. Активный экран показывает свои продукты/сроки/операции; Free50/фото/quiet/HTML сохранены. Confirm/refund/test-checkout кнопки удалены из обычного UI; прежние gated API сохранены. Добровольное ознакомление7 осталось только existing allowlist, без клиентского grant.

CTA открывает Stars/СБП/карту; Platega названа до выбора. prepare сначала проверяет подпись/точные поля и безусловно возвращает503+согласованный текст+payment_request_created=false. Не создаёт order/payment/attempt/grant и не вызывает money service, в том числе после restart с fake credentials. purchase/state читает только свою существующую операцию; сырые payload/credentials/checkout URL/buyer IDs не возвращаются. Финансовый факт и эффективный доступ разделены. Subscription Streamer читается по frozen buyer после unlink, publishing остаётся отдельным verified binding gate. В личном меню добавлен Plus WebApp deep link; query выбирает только screen.

## Evidence

P15-pass.log:35 passed,57 subtests,47.61s. RED API отсутствующих routes/ownership/menu и browser4blocks сохранены. P15-ready:6 PASS reports/84 PNG, Chromium/WebKit390×844, Free/Streamer/own ledger; errors0/external0. Проверены mode/150–300/secondary/full features/Back/reload/query paid ignores/no grants/все методы503/retry/offline/late method reply, own states и frozen recipient. Точные source/image SHA проверены перед commit.

Reviewer доказал vocabulary mismatch: actual ledger confirmed/canceled, первоначальные fixtures succeeded не были пригодны для этого gate. Исправлены fixtures и mapping; финансовый confirmed остаётся подтверждённым при expired access. Pending checkout expiry меняет derived display, не financial fact. Actual-module RED→PASS6/6 сохранены отдельно.

Own operation cache при повторном входе воспроизведён root; исправлен fresh read. Reviewer дополнительно воспроизвёл held response→Back→expiry→re-entry; per-order generation/controller/abort fence закрыт exact regression:2 reads и expired UI после late pending. Root reusable QA включает этот сценарий, reviewer повторил PASS. Один unresolved buyer index сохранён: fixture5own+1foreign, срок меняется у единственногоpending через loopback-only control. Assertions/индексы не ослаблены.

Ранние P15-first/gate снимки пересекались именем с generic Viewer кадром; их PASS отчёты не считаются финальным image evidence. Новый prefix purchase-flow закрывает пересечение. Исторические снимки/failed reports/HTML сохранены. Detector[] и Stop-Slop пользовательских строк выполнены.

## Граница

Это purchase UX с выключенной кассой. Ledger fixture заранее подготовлен локально, не доказательство реальной оплаты. Fake SDK/temporary SQLite; native Telegram, отправка сообщений, owner OAuth, merchant callback/Stars invoice и staging NOT TESTED. Legal/support продолжаются P16; full suite P19 впереди. Production/деньги/HTML/export не менялись.
