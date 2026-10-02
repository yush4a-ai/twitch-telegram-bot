# P13: Twitch и Telegram-канал

База d4f4b51. Один исполнитель; один read-only reviewer p09_focus_review.

## Реализация

Новые intent/API/prepared/fallback принимают только channel. Legacy groups сохраняются. Structured checker различает ready/absent/member/post-right/user-denied/wrong-type/network. can_post=True при can_edit=False и creator без optional flags проходят; network не утверждает отсутствие прав. Публичный URL приходит только из метаданных того же chat.

Выбор не включает публикации и не вызывает sender. Срок/отмена проходят одну транзакцию с placement; clock проверяется после BEGIN IMMEDIATE, включая ожидание другого соединения. OAuth code приводит к странице проверки; success только после verified token/Helix binding и атомарного сохранения. HttpOnly краткоживущий ticket не содержит token/code/state, public result/status no-store. Старый bot OAuth сохраняет handoff.

## Проверки

RED permission/late cancel/expiry/clock/OAuth/deadline/2-connection-lock и browser композиции сохранены рядом. Финальный P13-pass.log: 48 passed,33 subtests,49.80s. Migration старого intent без reason повторяется без потери записи.

P13-final: 10 PASS reports,94 PNG; Chromium151.0.7922.34/WebKit26.5,390x844,JS0/external0, source/image SHA проверены. Ready/absent/member/missing/network/checking/cancel/expired/not-chosen/Twitch-unavailable/legacy group; повторный выбор сохраняет включённые публикации. OAuth browser страницы проверяют presentation фиксированных public statuses; Python отдельно доказывает verified binding. Late SDK fake experiment теперь не снимает блокировку нового create и не создаёт третий intent. Detector[]; Stop-Slop copy review выполнен без смены продуктовых фактов.

Read-only reviewer подтвердил три найденных дефекта: grace60s, TTL до ожидания DB-lock, старый SDK finally. Исправления закреплены exact assertions. Сообщение повторного выбора строится после fresh profile. QA legacy исходно имела publishing=True; сценарий явно pause→enable, default не изменён.

## Источники и границы

Контракт сверён с официальными Telegram [ChatMemberAdministrator](https://core.telegram.org/bots/api#chatmemberadministrator), [KeyboardButtonRequestChat](https://core.telegram.org/bots/api#keyboardbuttonrequestchat), [requestChat](https://core.telegram.org/bots/webapps#initializing-mini-apps). Прикладная проверка прав не заменяет native send/edit/delete.

Native Telegram SDK/requestChat, реальные account OAuth/send/edit/delete — NOT TESTED. Только loopback/TEMP DB/fake bot. Staging/production/payment/HTML/export не менялись. Full gate P19 впереди. Следующий P14.
