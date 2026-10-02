# P01 — исходный настоящий Mini App

Основа документационного commit `3f4fec509b0d630d9c1a3e39aa9f6384a866b36e`; runtime пока прежний `e0d44c3`.

- RED: отсутствие scenario builder, 12 failed/1 passed (`P01-red.log`).
- Исправлена ошибка самого нового теста: путь временной БД берётся из fixture-owned directory, без нового публичного поля Database и без ослабления assertions.
- PASS: 11 tests/21 subtests; `test_mini_app_redesign_fixture`, existing shell/auth. `P01-pass.log`.
- Chromium151.0.7922.34/WebKit26.5: baseline actual `/app`, шесть подписок, Streamer/Profile, layout390×844, по3 PNG; JS0/external0. Sources и image SHA в `baseline-*-qa.json`.
- Сценарии0/6/200; реальные temp SQLite, signed local Telegram identity, fake Twitch/SDK/sender. Незнакомый сценарий rejected; owner checkout403/order count0. Legacy group отдельным seed, product installer без `/_qa/`.
- Mapping §5 плана сверён с существующими readers/routes: функциональность переносится дальше, старые HTML/commands/groups сохранены.
- Scoped review: fixture не читает env secrets/active DB, builder cleanup закрывает connection/TemporaryDirectory; CLI bind127.0.0.1; graceful QA shutdown только loopback; существующий CLI без scenario сохраняет прежние fixtures. Server API/auth не менялись.

Ruling: QA рабочая копия исполняется из Windows TEMP с установленным Playwright runtime напрямую — universal runner пишет execution file в глобальную skill folder, пользователь запретил менять глобальные installations. Ничего не установлено.

Native Telegram/OAuth/sends/платежи/staging: NOT TESTED. Это снимки предыдущей композиции, не принятие редизайна. Далее P02.
