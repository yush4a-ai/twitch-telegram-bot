# P05 — локальный строгий контракт Platega

BASE `8474eb8`; ветка `autonomous/twitchsignal-roadmap`.

## Изменения и проверка

Сохранён старый mock API/TEST/HMAC; добавлен нормализованный provider Protocol, совместимые defaults CheckoutSession, notice/HTTP/refund/unknown DTO. PlategaProvider получает transport явно. Любой transport без `network_free is True` и verified sandbox policy отвергается до обращения; сетевой HTTP transport не реализован и runtime provider не подключён.

Fake transport проверяет серверные RUB150/300, методы2/11, orderId/payload и metadata реального verified fixture buyer, fixed server return/failed URLs. Без known buyer display name и заданного возврата create блокируется; фиктивного antifraud fallback нет. Hosted URL только HTTPS/exact host, без userinfo/secret/merchant/постороннего port. V2 `url` нормализуется, но endpoint/основной UX остаётся v1 с выбранным методом.

Canonical GET требует `id`, `mechantId`, gross RUB/точные minor units, известный method/status и server correlation. JSON duplicate/bool/NaN/fractional minor/чужие currency/merchant/order/method/oversize не подтверждают оплату. Callback: unique case-normalized headers, constant-time credentials, body≤8192, authenticated notice без money/evidence/grant. GET response≤65536, timeout5s.

POST outcome unknown не повторяется этим adapter; durable межпроцессная гарантия — P06. Refund accepted/manual/declined/unknown не равны completed. Local contract attempts/refunds ограничены4096, не вытесняются ради повтора. HTTP429 numeric/HTTP-date Retry-After без раннего повтора. Ошибки не включают raw body/headers; внешние exception chains не передаются из create/JSON.

RED absent contract → implementation → PASS31tests/44subtests. Scoped review добавил обязательные return URLs/HTTP-date/invalid immutable subject guards; RED новых интерфейсов сохранён. Итоговый snapshot: **32 passed, 57 subtests passed**, 16.71s, 0 skips/failed — `P05-pass.log`.

## Официальные источники

13 нужных документов повторно получены по опубликованному [индексу](https://docs.platega.io/llms.txt); **13/13 SHA256 совпали** с прежними метаданными. `P05-sources.json` хранит только URL/hash/bytes. SDK и примеры не исполнялись, реальные credentials/аккаунт не использовались.

[Основной endpoint](https://docs.platega.io/создание-платежной-ссылки-с-заданным-методом-29203843e0) описывает orderId/metadata, которых нет в сокращённой отдельной schema. [GET schema](https://docs.platega.io/transactionstatusresponse-13226219d0) содержит `mechantId` и пример SBPQR. Card string enum не доказан опубликованной GET schema: нет guessed default; дополнительный method mapping в тесте явно fixture-only. Подтверждение merchant schema остаётся частью допуска. [Callback](https://docs.platega.io/callback-об-изменении-статуса-транзакции-29209725e0) — credentials, не mock HMAC. [Возврат](https://docs.platega.io/отмена-транзакции-38225949e0) — принятие запроса, не доказательство окончания.

## Scoped review / границы

Просмотрены новый module/diff и все DTO consumers; legacy tests проходят. Нет H2H/recurring/cards storage/payout/provider SDK. Адаптер ничего не выдаёт сам; notices и evidence применяются только будущим P06 validator. In-memory sticky guard не объявляется межпроцессной идемпотентностью. OFF policy и отсутствие сетевого transport остаются жёсткими границами первого release.

Никаких действий merchant/Railway/Telegram/money. Bank NOT READY; настоящие callback/GET/card mapping/refund/native NOT TESTED. Следующий P06: durable inbox/reconciliation/common apply.
