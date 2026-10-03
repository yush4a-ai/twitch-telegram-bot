# Главная при отсутствии эфиров

Замечание владельца: на присланном iPhone-скриншоте главной нет персонажа, хотя сравнение с концептом показывало его. Причина в renderHome: общий баннер ошибочно находился внутри ветки live. Сравнение было неполным — live и ноль подписок не покрывали subscribed/no-live.

Исправление: один общий верхний блок с полным персонажем во всех загруженных состояниях. Ниже — только реальные эфиры, сообщение об отсутствии подтверждённых эфиров, уточнение статуса или предложение добавить первого стримера. Первое добавление больше не дублирует персонажа. Loading/error до данных, фильтрация, сортировка, ссылки и действия сохранены.

Регрессия перед исправлением: `HOME-RED-confirmed-chromium/report.json` — 8 ожидаемых ошибок отсутствующего/дублированного персонажа. После исправления отдельный прогон — 73 PASS. Проверяются live → offline → stale → пусто → live, обе темы, реальные router-переходы и открытие добавления, 320 px и текст 200%, целый персонаж без пересечения текста, отсутствие вымышленных статусов/каталога. Снимки сопоставлены в [HOME-STATE-COMPARISON.html](HOME-STATE-COMPARISON.html).

Дополнительные замечания владельца: сообщение с отменой держалось слишком долго, свайп было трудно выполнить. Toast теперь показывается 6 секунд, не дублируется над списком и сохраняется при фокусе/выполняющейся отмене. Повторный запрос блокируется; после ошибки фокус и возможность повторить сохранены. Серверный срок одноразового токена остаётся 60 секунд.

Свайп теперь начинается и на правых элементах строки. Движение влево на 28 px раскрывает удаление; карточка следует за указателем, pointer capture сохраняет жест за её границами. Вертикальная прокрутка, простые нажатия, отмена жеста и обязательное отдельное нажатие «Удалить» сохранены. Interaction RED воспроизвёл исходные дефекты. Проверка клавиатуры с настоящим router и Enter: прежний disabled дал RED двух focus checks, aria-disabled с блокировкой повторного запроса — GREEN; assertions не ослаблены.

Финальные `STATES-RELEASE-chromium` и `STATES-RELEASE-webkit`: **по232 PASS / 58 PNG**, 0 JS errors и API/auth network. В default matrix входят все home states и22 interaction checks, включая trusted pointer capture и обычные favorite/notify taps. WebKit обнаружил пересечение персонажа с текстом200; адаптация контейнера дала GREEN. Shell/copy: 9 PASS / 3030 subtests. Один scoped reviewer подтвердил исправления и сохранение полного gate в ограниченном packaging helper.

Исправление опубликовано: **SHA `560f3ccac05fe8f6c4b1221a28b926b7358edfef`**, tree `a8d56fd8acee41c2523313f600b9c7cf3c5118bd`, deployment `631a94d5-f6a2-4d11-b10d-d620c8f6dc07`, active SUCCESS. Вход: [@TwitchSignalTestbot](https://t.me/TwitchSignalTestbot) → «Приложение». Старое окно Mini App нужно закрыть и открыть заново.

Неизменный штатный guard выполнил полный pytest на этом чистом snapshot: **1491 passed / 2 прежних Windows skips / 3634 subtests**, 1134,19 с. Из upload исключены только docs/audits и docs/design; retained663files/12761251bytes проверены побайтно против стандартного committed archive и по полному набору путей. Helper меняет только упаковку, не пропускает tests/target checks/upload/wait; production baseline перепроверен. Доказательства: STAGING-states-full-guard.log и STAGING-states-package-manifest.json.

Actual smoke нового SHA PASS: bot8859004067/menu,192 runtime-file hashes,17 HTTP-assets и shell,9auth401,27migrations/integrity/FK, paymentOFF и production before/after equality. STAGING-staging-smoke.json — актуальный результат; STAGING-smoke-720962b.json сохраняет предыдущий выпуск. Browser matrix привязана к новому committed UI через STAGING-states-source-confirmation.json; PNG совпадают точно, JS/CSS учитывают переносы строк Git/Windows.

Native touch-свайп в Telegram на iPhone/Android и окончательная визуальная приёмка ожидаются. Signed live API/OAuth/реальные отправки вручную не проверялись; проверочный сценарий не отправлял сообщений и не создавал платежей. Legal503 и отключённая оплата сохранены. Production остался deployment466499d5/SHA dc9239e. Последующий commit документов не заменяет развёрнутый SHA560f3cc.
