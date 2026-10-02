# P14: посты, варианты и статистика

База60708977. One executor, один scoped read-only reviewer p09_focus_review.

## Изменения

Posts использует существующие template/example/presets/stats/preview APIs. Отдельные страницы А: обзор, оформление, видео, варианты и статистика; выбранный placement сохраняет свой черновик/версию. Новый streamer_posts.js — модуль существующей Mini App, без отдельного runtime/DB. Asset route строится только из explicit server allowlist.

Draft example сервер валидирует через прежний validator/compose, не сохраняет настройки и не вызывает sender. Post-only/creator права используют общий checker; network503 отличается от permission403, смена типа placement отклоняется. Free стандартный пост с фото сохранён; Viewer-only не получает оформление. Статистика считает подтверждённые IDs собственных публикаций:30d и7/7, без вымышленных views/clicks. Переходы отмечены в разработке.

## Evidence

RED API draft/network,asset404 и UI composition сохранены. P14-pass.log:34 passed,38 subtests,43.98s. Runtime/permissions/CAS/expiry/regrant/unsafe text/URL/foreign placement и сохранённые variants покрыты existing+extended tests.

P14-clean:4 PASS reports,34 PNG; Chromium/WebKit,390x844,JS0/external0,source+imageSHA проверены. API→save→canonical read; pending controls locked; unsafe preview не сохраняется; peer409 сохраняет draft/explicit version; save variant не apply, delete не меняет template; два placement; real confirmed fixture stats1; reload restores valid draft and its server preview; mutation403 invalidates editable cache, visibility rereads expiry; late video A ACK не пишет status/checkbox B. Fake sender calls0.

Scoped reviewer доказал mutation403 cache, restored draft preview и mutable selected в late video ACK. Исправлены и закреплены reusable QA. Статическую гипотезу debounce reviewer не подтвердил, дефектом не считали. QA fixture assumptions исправлены по факту: ORDER chat_id выбирал -1004; select получил explicit accessible label; body-only draft требовал обязательный headline по прежнему validator. Assertions/validator не ослаблены. Toggle сохраняет pending requested value с честным status до ACK, не обещает активацию.

Detector[]; Stop-Slop review пользовательских строк выполнен. Старые HTML/export/commands/group storage сохранены.

## Ограничения

Native Telegram/send/edit/delete/live capture, owner OAuth и staging NOT TESTED. Screenshot/delivery IDs только loopback/TEMP fixture; paid grant явно локальный и не результат purchase. Full suite P19 впереди. Следующий P15.

Перед commit whitespace guard обнаружил trailing space; удалён, browser gate повторён на новых source SHA. P14-release сохранён как предыдущий snapshot. Код закреплён bc5e354.
