# P10 — Главная и подписки Viewer

BASE `f5549b007333eb2f48fab1f183eeb07f333d0a0b`. Один исполнитель, локальная временная БД, исходящие fake. Новых внешних запросов/оплат/deployment нет.

## Проверки

- `P10-names-red.log`: missing metadata/helper → server cache/route → `P10-server-pass.log`: **25 tests / 23 subtests**, 50.01 s. Общие bot/app rows, actor isolation, 51-й Free/201-й Plus, downgrade/priority/manual mute, metadata fallback/TTL300/bound2000/concurrent lookup/timeout5, fixture/CSP shell.
- `P10-browser-red.log`: отсутствующая «Главная» → новая композиция. `P10-free-empty`, `P10-free-six`, `P10-plus-two-hundred`: **Chromium151/WebKit26.5 PASS**, 0/6/200, add/follow/unfollow через реальные signed API и shared DB, pause→reload, длинное имя/full detail, live/stale/offline, настоящая category из seed sample. Поиск своих подписок не вызывает Twitch search; добавление отдельным dialog. Поздний alpha не заменяет beta, сеть сохраняет query, закрытие сохраняет свой draft.
- Пять widths360/390/430/768/1440, text200 на360×440, видимые controls, no horizontal overflow. `P10-shell`: два движка на200 повторили nav4/Back/visible row focus/position/draft/shared Plus/dialog/auth/loading; exact assertions сохранены. Всего **24 итоговых PNG**. JSON содержит UI, backend, fixture и QA SHA; screenshots просмотрены.
- Escape в search input Chromium изначально очищал поле, оставляя dialog. Общий capture handler закрывает dialog и сохраняет draft; новый assertion проходит. Первое QA ожидание «нет совпадений» в совершенно пустом списке уточнено на «Добавьте первого стримера»; число строк0 и весь mutation путь продолжают проверяться.
- `P10-design-detector.json`: findings[]. Scoped мобильный/Operate/Stop-Slop review: grouped rows/initials/две строки имени с full accessible name/44px pause/Free без потери уведомлений. Никаких выдуманных avatars или метрик.

## Реализация

Главная использует реальные counts/текущие подтверждённые эфиры/сохранённые scheduled reminders. Стримеры сгруппированы отдельно по live/stale/offline; состояние уведомлений отличается от статуса эфира. Long name сокращается только в строке, полностью доступен в detail и aria-label.

Публичные имена запрашиваются только для own rows, существующий Twitch client пакетирует по100. Общий bounded cache не хранит личные права; timeout включает ожидание lock, ошибки возвращают настоящий login, не вымышленное имя. Права/time snapshot вычисляются после metadata wait. Новых detector/queue engines нет. Search abort/version и dispose убирают поздние UI callbacks.

## Граница

Viewer settings/папки/detail формы ещё прежние — P12. Полный top video picker — P11. Полные Plus/legal/Streamer — P13–P17. P18/P19 выполнят окончательную матрицу и full suite. Native Telegram/write access/send/OAuth/payment, staging новая версия **NOT TESTED**. Production не менялась. HTML/экспорт и legacy groups сохранены. Следующий P11.
