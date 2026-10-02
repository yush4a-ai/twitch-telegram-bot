# P17: пользовательские тексты и совместимость

Базаf1ed26ab. Один исполнитель и один scoped read-only reviewer.

## Изменения

Полный literal-copy gate для Mini App JS, новых Python сообщений, каталога, видимых HTML text nodes и canonical legal. Английские подписи Demo/Mock проверяются отдельной регрессией; Cyrillic-only фильтр убран после RED. Test-checkout/confirm/refund endpoints отсутствуют во всех обычных UI модулях; gated trial остаётся отдельным добровольным ознакомлением.

В Agreement техническая подпись заменена описанием аккаунтов, которым оператор открыл ознакомление. Семь дней/без денег/без автопродления сохранены. Canonical SHA обновлён; owner_accepted=false. Каталог проверяется по actual product_id/price_label/RUB15000–30000/one_month/«1 месяц»/unapproved/NoneXTR/noauto. Первые неверные предположения теста о названиях полей сохранены в RED logs; reviewer подтвердил реальные контракты до окончательной правки. Assertions проверяют точные утверждённые цены и период.

Исторические README/DESIGN sections обозначены историей. Старые цены200/прототипы остаются архивом, текущая схема150/300 уже выделена выше. HTML/экспорт не удалены. build_report_html/report_delivery неизменны относительно базы3f4fec5; вход /report в профиле копирует команду, без автоматической отправки.

## Evidence

- P17-compat-pass.log:387passed50subtests318.67s. Старые callback scopes, private/group-admin legacy toggle denied, sixth denied, raid/live/rename quiet и retry/recheck, MenuButton pinned identity, HTML bytes/send_document/24h fallback/private routing, old import50/200/выбор/отмена и legacy groups проверены fake sender/TEMP DB.
- P17-copy-media-pass.log:40passed2667subtests36.87s. Catalog/copy/legal/version/contact, общий media gate, refund/expiry animation→photo и независимый Viewer/Streamer inheritance. Число subtests включает проверку литералов; это не число отдельных пользовательских сценариев.
- P17-copy:4PASS reports44уникальных PNG с current source/image SHA, Chromium/WebKit ready TEMP/unready canonical,360/390/text200/Back/keyboard/full text/до оплаты. JS/errors0/external0. P16 evidence после legal-copy изменения историческая; финальная browser matrix P18 проверяет новый snapshot.
- Один reviewer подтвердил actual contracts, copy смысл, URI/old scopes и отсутствие продуктовых compatibility findings. Его coverage рекомендации English/all UI endpoints/catalog/HTML включены. Detector[]; batched Stop-Slop/Impeccable copy review выполнен без смены дизайна.

Новая структура первого входа и расширенных отчётов не внедрялась. Production/реальная оплата/отправки/OAuth не проверялись и не изменялись. Native Telegram NOT TESTED, bank NOT READY, money OFF. Следующий P18.
