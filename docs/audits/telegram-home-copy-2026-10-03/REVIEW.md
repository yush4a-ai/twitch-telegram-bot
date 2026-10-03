# Узкий review главной

Объём: `build_home`, presentation assertions и новый HTML-caption regression. Один исполнитель; review выполнен им же, независимый reviewer в этой небольшой правке не запускался.

- Значения идут из прежнего read-only `HomeState`; `load_home_state`, ownership, callback checks, per-bot cache и клавиатуры не изменены.
- Только Telegram HTML `b`/`i`; основной Bot уже использует `ParseMode.HTML`. Внешние login/category режутся до прежних 60/100 символов и экранируются до обёртки тегами. Parser regression проверяет literal строки, balanced allowed tags и длину caption.
- Показаны не более трёх эфиров, общий live count и остаток сохранены. Последняя проверка обозначена отдельно. Ошибка live query не превращается в offline, выключенные notify остаются явными.
- Stored community count по-прежнему не доказывает права публикации; предложение проверить их через «Я стример» сохранено.
- Exact offline/live/count assertions изменены только вслед за новым согласованным оформлением, проверки actor isolation и denial сохранены. Новых skips/bypass нет.
- Макет просмотрен в Chromium на 390/1440: все PNG загрузились, горизонтального переполнения нет. Длинные ники/категории переносятся. Это типографический макет, не подтверждение отображения в Telegram Desktop/iOS/Android.
- Stop-slop выполнен по изменённым текстам: нет новой продуктовой/коммерческой правды; повтор имени и общая подсказка добавления удалены у возвращающегося пользователя.

Замечаний, блокирующих focused checks, не найдено. Финальный suite, выпуск и native acceptance отмечаются отдельно по факту.

## Review V2

Нативный blockquote подтверждён официальным Bot API: https://core.telegram.org/bots/api#html-style . Цвет/шрифт выбирает клиент; browser CSS только показывает намерение группировки. В caption один blockquote, без вложенного quote. Внутри bold Twitch links + обычные категории, отдельные абзацы; summary/footer снаружи. Source layout scan0 findings,390/1440 browser no overflow. Внешние данные HTML-escaped, URL полностью percent-encoded после фиксированного https://www.twitch.tv/; новый отрицательный тест блокирует уход с origin/внедрение HTML. Parser допускает лишьb/i/a/blockquote и правильные attrs. Новыйfocused33PASS; full ещё впереди. Независимыйreviewer/nativeTelegram не запускались.
