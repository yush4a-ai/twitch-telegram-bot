# TwitchSignalBot production cutover — 04.10.2026

## Итог

**PRODUCTION CUTOVER SUCCESS. Payments OFF.**

- Production runtime source: `1c49330e773ded22d47a185866dd3921b1e00806`.
- Railway deployment: `44fe69d3-2d89-466c-9202-fcbf56e6c05a` — SUCCESS.
- Bot: `@TwitchSignalBot`, ID 8707370390.
- Health: `/healthz` → 200 `{"status":"ok"}`.
- Mini App: `/app` → 200.
- Runtime upload bundle: 164 files / 9,584,073 bytes, manifest `57a0472d07c31f73c3097e3686ca1395a0f6d017056fe872ab1a89b02cac7bad`.
- Pre-release full gate on the exact source commit: 1648 PASS / 3741 subtests / 2 existing Windows skips / exit 0.

## Fresh cutover backup

Перед запуском нового runtime старый writer был заменён maintenance-процессом, который не открывал SQLite.

В quiet state отсутствовали `bot.db-wal` и `bot.db-shm`. Fresh source DB был скачан вне Railway Volume:

- bytes: 13,180,928;
- SHA256: `55e9c12e6e2dd11e55cf7bda4ebc5524e5820f95707a471928d45ef43060a27b`;
- `integrity_check=ok`;
- foreign-key errors: 0;
- source tables: 21;
- selected source counts: known_private_users 67, tracked_channels 112, telegram_channels 4, twitch_user_tokens 6, stream_history 1642;
- local sealed file помечен read-only.

Rollback artifact остаётся `dc9239eb0b82fb80d1788fc657205740cebc49e9`.

## Admission

Owner разрешил production cutover в текущем чате.

Production contract:
- payment policy: OFF;
- queue: OFF;
- writer policy: exclusive lock;
- login client policy: peer_shared_fail_safe;
- replica: 1;
- emergency admin key: configured, >=32 chars; значение не сохранялось в документах.

Первый запуск нового runtime `e344b9ee-32b1-4d25-b66b-fe5311cfa525` остановился fail-closed **до открытия DB**: Railway не экспортировал `RAILWAY_VOLUME_INSTANCE_ID` в runtime. Exact previously verified production volume-instance ID был pinned как production metadata variable без автодеплоя. После этого тот же exact runtime bundle был повторно развёрнут и прошёл admission.

## Post-deploy smoke

Fresh runtime evidence:

- writer lock `/data/.twitchsignal-writer.lock` существует;
- polling запущен для `@TwitchSignalBot`;
- unsigned Mini App endpoints → 401;
- signed owner `/app/api/bootstrap` → 200;
- signed viewer state → 200;
- signed streamer profile → 200;
- signed subscription state/catalog → 200;
- signed purchase prepare → 503, без checkout/order/invoice;
- обычный Telegram WebApp-вход владельца в admin → session created, panel → 200;
- Platega callback `/payments/platega/callback` → 404;
- admin snapshot: Telegram `ok`, polling=true, database error=null;
- EventSub running=true, ready 5/6;
- Twitch overall state=`degraded` из-за одного auth-blocked Twitch login;
- preview state=`unknown` сразу после запуска; это не health blocker.

После прогрева Railway health стал 200 и deployment перешёл в SUCCESS.

## Известные остаточные пункты

Это не откат и не blocker текущего запуска, но остаётся проверить/доделать отдельно:

1. Native owner acceptance на реальных Desktop/iOS/Android не закрыта полностью.
2. Один Twitch login в operational snapshot требует повторной авторизации; отдельные follower-count запросы для ряда каналов также логируют TwitchAuthError, при этом основной poller/Telegram health остаётся рабочим.
3. Legal/support owner inputs для банковского/денежного запуска ещё не приняты; денежная интеграция остаётся OFF.
4. Долгосрочная offsite backup/retention policy остаётся отдельной эксплуатационной задачей. Fresh cutover backup сохранён локально вне Railway Volume.

## Rollback

При критическом новом дефекте:
- остановить новый writer;
- не удалять WAL/SHM вслепую;
- сохранить post-failure snapshot;
- восстановить fresh pre-cutover image по SHA выше;
- запустить совместимый old artifact `dc9239eb...`;
- перепроверить identity, integrity/FK, key и health.

На момент записи release evidence production работает на новом runtime и rollback не требуется.
