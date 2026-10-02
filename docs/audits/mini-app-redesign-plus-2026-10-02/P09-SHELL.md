# P09 — shell, профиль и общий вход в Plus

BASE `a229df3ec313be4bb008ad3d8eca09982ba2cb5e`, ветка `autonomous/twitchsignal-roadmap`. Один исполнитель; один read-only reviewer использован после повторного WebKit focus RED, без параллельных правок.

## RED → PASS

- Signed identity/assets: `P09-auth-assets-red.log` → `P09-pass.log`: **13 tests / 37 subtests**, 24.37 s на итоговом backend/UI snapshot.
- Старые три пункта и emoji заменены четырьмя точными SVG-пунктами. Profile/menu/Plus используют один Subscription. Профиль получает только проверенные HMAC имя/username/ID; optional metadata не меняет auth/freshness/лимит. Подмена имени, duplicate fields, bool ID, чужой initDataUnsafe отвергаются.
- Browser: final `shell-*-qa.json`, `P09-two-hundred/shell-*-qa.json`: Chromium151.0.7922.34 и WebKit26.5, сценарии6/200, четыре меню, max600, widths360/390/430/768/1440, text200 на360×440, shared Plus, Profile/Support/Back, native dialog Tab/ShiftTab/Escape/origin, SDK-double Back сначала закрывает диалог. Missing auth не делает private reads; delayed bootstrap/loading/503 дают один ясный экран. JS errors0/external requests0.
- `P09-theme/theme-*-qa.json`: оба движка повторно проверили темы/SDK lifecycle на новом shell. Всего **26 PNG** итогового кода; SHA источников/PNG в JSON. Profile и Viewer PNG просмотрены. Старые P08 кадры сохранены.
- `P09-focus-diagnostic.log`: WebKit оставлял focus на persistent MAIN#content после pointerdown, guard isConnected отменял возврат к кнопке. Reviewer подтвердил event trace. Узкий guard допускает прежний MAIN, сохраняет новый focus; exact row focus/position assertion и regression позднего пользовательского focus проходят в обоих движках.
- `P09-scroll-red.log`: QA изначально измерял строку под fixed nav; теперь есть явная precondition её полной видимости до click. Tolerance возврата строки <2 px сохранён.
- Chromium reload выявил два h1 «Загружаем подписки…»: синхронный load→refresh внутри render. Initial Viewer/Streamer load теперь начинается после завершения render, повторный запрос не создаётся. Exact single-heading ожидание сохранено.
- `P09-modal-back-red.log`: Back менял страницу под открытым диалогом. Общий dialog stack закрывает верхний диалог и синхронизирует SDK Back visibility; route history не меняется до следующего Back.

## Scoped review

Проверены allowlist/CSP/no arbitrary assets, signed identity wrapper, textContent/SVG paths без HTML injection, actor-bound draft keys, очистка прежнего пользователя, denied storage, dialog focus/scroll lock, mode/tab/detail/back, loading reentrancy и фактические screenshots. Новые подписи просмотрены по Stop-Slop; Impeccable Operate/mobile-app guidance использованы для shell. Full gate/security/все поверхности — P18/P19/P17.

## Граница

Это shell checkpoint. Viewer/Streamer композиция и полный UX переносятся P10–P16. Plus пока использует прежние product cards с новой ценой; role-aware четыре блока/три метода/active copy — P15/P17. Support пока честно сообщает ошибку отсутствующего API; контент/реальный контакт — P16. Старые HTML/экспорт сохранены.

Локальные fake SDK/sender и временная SQLite. Native Telegram/iOS/Android, OAuth, write access, send, payments и новая staging версия **NOT TESTED**. Production/Railway/реальные деньги не менялись. Следующий P10.
