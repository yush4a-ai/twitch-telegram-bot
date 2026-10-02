# Пакет для банковского согласования Platega

Статус: подготовлен локально, NOT READY для отправки. Production и реальная денежная интеграция не включены. Прежние design/legal HTML/drafts/экспорт сохраняются.

## Проверенный путь

- [x] Покупка начинается в TwitchSignalBot / Mini App.
- [x] Plus выбирает предложение по режиму; вторичный Viewer доступен стримеру.
- [x] Viewer Plus150 ₽/месяц; Streamer Plus300 ₽/месяц, Viewer включён.
- [x] Конкретная цена и активная CTA → «Как оплатить?».
- [x] Telegram Stars, СБП, банковская карта видны проверяющему.
- [x] Stars — Telegram; СБП/карта — Platega, без маскировки.
- [x] Выбор метода → «Оплата временно недоступна. Мы заканчиваем подключение платёжной системы.»
- [x] Payment OFF;0 новых order/payment/attempt/grant,0 invoice/внешних payment POST, no fake success.
- [x] Для заглушки merchant credentials не требуются.

Evidence P15: `docs/audits/mini-app-redesign-plus-2026-10-02/P15-PLUS-PURCHASE.md`; loopback/temporary DB/SDK double. Staging snapshot/native проверки ещё впереди P20–P21.

## Документы и контакты

- [x] Canonical исходники `docs/legal/PRIVACY-POLICY.md`, `USER-AGREEMENT.md`, `SUPPORT.md`, `TARIFFS.md`, `PAYMENTS.md` и manifest версии/SHA/catalog.
- [ ] Владелец предоставил необходимые реальные сведения оператора/поддержки/хранения/политик.
- [ ] Владелец принял конкретные Privacy/Agreement редакции; manifest принятие и SHA согласованы.
- [ ] Публичные документы читаются до оплаты без Plus и содержат реальные реквизиты.
- [ ] Реальный личный Telegram username и/или email поддержки доступны; группа не единственный контакт.
- [ ] Merchant/банк получил и подтвердил весь пакет — только по отдельной авторизации владельца.

Публичные routes `/app/legal/privacy|agreement|support|tariffs|payments` показывают503 и честный статус, пока документ неподготовлен. Support/state не выдаёт fake defaults. Примерный контакт и заголовок модального окна не доказывают bank-ready.

## Перед будущими деньгами

- [ ] Подтверждены merchant/test, month/refund/chargeback/upgrade/XTR и чековая схема из OWNER-INPUTS.
- [ ] Проведены отдельно разрешённые native Stars и Platega test-проверки callback/reconcile/refund/no duplicate grant.
- [ ] Подтверждены provider callback credentials, bounded status requests и отсутствие секретов в client/logs.
- [ ] Точная staging identity/SHA и acceptance владельца зафиксированы.

Менеджерский brief допускает кликабельную заглушку до подключения кассы. Этот checklist не утверждает завершённую модерацию, юридическую готовность или проведённую оплату.
