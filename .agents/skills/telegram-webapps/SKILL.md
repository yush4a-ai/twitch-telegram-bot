---
name: telegram-webapps
description: Build Telegram Mini Apps (Web Apps) — interactive web applications embedded inside Telegram. Use when the user asks to "create a Telegram Mini App", "build a Telegram Web App", "add a web app to my Telegram bot", "make a TWA", or wants to use window.Telegram.WebApp API, integrate a bot with a frontend, implement initData validation, accept Telegram Stars payments, add navigation between pages, set up local development for a Mini App, or implement proactive messaging to users. Covers HTML boilerplate, mock for browser dev, JavaScript SDK (MainButton, SecondaryButton, BackButton, HapticFeedback, CloudStorage), bot setup via BotFather, server-side initData validation (Node/Python/Go), SPA navigation, Telegram Stars payments, and advanced APIs (location, QR, fullscreen, accelerometer).
metadata:
  author: skill-creator
  version: 0.1.0
---

# Telegram Mini Apps (Web Apps)

Telegram Mini Apps are web pages that run inside Telegram — they get the user's identity, theme, and can send data back to the bot. The experience feels native because Telegram renders it inside the app with native header/footer controls.

## How it Works

1. User opens the Mini App (via bot button, inline button, or direct link)
2. Telegram loads the URL in an in-app browser and injects `window.Telegram.WebApp`
3. The app reads user info, renders UI, user interacts
4. Data goes back to the bot via `sendData()` (keyboard-button mode) or HTTP POST with `initData` as auth

## Step 0: Local Development Setup

**Without a proper local dev setup, every code change requires a full deploy.** Solve this first.

**Copy `assets/miniapp-mock.js` into the project.** Add it as the first `<script>` in `<head>`, before the Telegram SDK:

```html
<script src="./miniapp-mock.js"></script>
<script src="https://telegram.org/js/telegram-web-app.js"></script>
```

The mock activates only when the page is opened in a plain browser (no `tgWebAppData` URL param). Inside Telegram, the real SDK takes over automatically. `window.isTelegramMockActive = true` when the mock is active. The mock:
- Stubs the full API surface: MainButton (real DOM), BackButton, SecondaryButton, HapticFeedback, popups
- Backs CloudStorage/DeviceStorage with localStorage, SecureStorage with sessionStorage
- Respects `prefers-color-scheme` for theme colors
- Fires `viewportChanged` on window resize
- Prints a yellow console warning so it's unmistakable

For HTTPS tunneling (Telegram requires HTTPS for deployed URLs), framework-specific setup (React+Vite, Next.js, SvelteKit), common errors, and **production deployment** (choosing a host, updating BotFather, env vars), see [references/developer-setup.md](references/developer-setup.md).

## Step 1: Start from the Bundled Boilerplate

**Copy `assets/miniapp-boilerplate.html` into the project as the starting point.** Do not write the HTML scaffold from scratch — the boilerplate has the correct script ordering, viewport meta, safe area setup, stable viewport height handling, and theme change listener already wired. Customize from there.

For styling, copy **`assets/theme.css`** (all 14 Telegram CSS variables with dark/light browser fallbacks) and **`assets/miniapp-native.css`** (native touch/feel: tap highlights, momentum scroll, safe-area helpers, skeleton loader, page transitions, list cells, form inputs):

```html
<link rel="stylesheet" href="./theme.css">
<link rel="stylesheet" href="./miniapp-native.css">
```

Key things already in the boilerplate (do not omit when adapting):
- `miniapp-mock.js` loaded before the SDK — mock activates in browser, real SDK in Telegram
- `viewport-fit=cover` in the meta tag — required for safe area CSS vars to work
- `tg.ready()` — removes Telegram's loading spinner; call as soon as essential UI is ready
- `tg.expand()` — expands to full height; almost always desirable
- `viewportStableHeight` for `--app-height` — use this, not `100vh`, to avoid layout jumps
- Both safe area var families applied on the container (see Gotchas)
- `tg.onEvent('themeChanged', ...)` listener — for non-CSS updates (canvas, chart colors)

## Step 2: Core API Patterns

```javascript
const tg = window.Telegram.WebApp;

// User info — for display only, not authorization
const user = tg.initDataUnsafe?.user;
// { id, first_name, last_name?, username?, language_code?, is_premium? }

// For authorization, send this string to your backend
const rawInitData = tg.initData;

// Theme
tg.colorScheme;    // "light" | "dark"
tg.themeParams;    // { bg_color, text_color, button_color, ... }
```

### CSS Variables (use these for all colors)

```css
body        { background: var(--tg-theme-bg-color); color: var(--tg-theme-text-color); }
.surface    { background: var(--tg-theme-secondary-bg-color); }
.btn        { background: var(--tg-theme-button-color); color: var(--tg-theme-button-text-color); }
.hint       { color: var(--tg-theme-hint-color); }
.danger     { color: var(--tg-theme-destructive-text-color); }
```

See [references/js-api.md](references/js-api.md) for the full list of CSS variable names and their matching `themeParams` keys.

## Step 3: UI Components

### MainButton — native bottom CTA

```javascript
tg.MainButton
  .setText("Submit Order")
  .show()
  .onClick(() => {
    tg.MainButton.showProgress();
    submitOrder()
      .then(() => tg.close())
      .catch(() => tg.MainButton.hideProgress());
  });
```

### SecondaryButton — second native button above MainButton

```javascript
tg.SecondaryButton
  .setText("Save Draft")
  .setParams({ color: tg.themeParams.secondary_bg_color, text_color: tg.themeParams.text_color })
  .show()
  .onClick(() => saveDraft());
```

`SecondaryButton` requires Bot API 7.10+. Guard: `if (tg.isVersionAtLeast('7.10') && tg.SecondaryButton) { ... }`

### BackButton — header back arrow

```javascript
// Use a single-handler pattern to avoid stacking bug
let _backHandler = null;
function setBackHandler(fn) {
  if (_backHandler) tg.BackButton.offClick(_backHandler);
  _backHandler = fn;
  if (fn) { tg.BackButton.show(); tg.BackButton.onClick(fn); }
  else tg.BackButton.hide();
}

setBackHandler(() => goBack());  // show
setBackHandler(null);            // hide (root page)
```

**Never call `tg.BackButton.onClick()` more than once without calling `offClick()` first** — handlers stack and fire multiple times.

### SettingsButton — gear icon in header

```javascript
tg.SettingsButton.show();
tg.SettingsButton.onClick(() => showSettingsPage());
// Requires Bot API 7.0+
```

### HapticFeedback

```javascript
tg.HapticFeedback.selectionChanged();              // item tap / picker scroll
tg.HapticFeedback.impactOccurred("medium");        // physical action (light/medium/heavy/rigid/soft)
tg.HapticFeedback.notificationOccurred("success"); // result feedback (success/error/warning)
```

### Popups

```javascript
tg.showPopup({
  title: "Confirm",
  message: "Submit this order?",
  buttons: [{ id: "ok", type: "ok" }, { id: "cancel", type: "cancel" }]
}, (buttonId) => { if (buttonId === "ok") submitOrder(); });

tg.showAlert("Done!");
tg.showConfirm("Sure?", (confirmed) => { /* boolean */ });
```

## Step 4: Sending Data to the Bot

### Method A: sendData() — keyboard-button mode only

```javascript
// Only works when opened from a reply keyboard button (web_app type)
// Closes the app and delivers data as a web_app_data update to the bot
tg.sendData(JSON.stringify({ action: "order", items: cart }));
```

### Method B: HTTP POST (works in all launch modes)

```javascript
// Send initData as auth — your backend validates it
await fetch("https://yourapi.com/submit", {
  method: "POST",
  headers: { "Authorization": `tma ${tg.initData}`, "Content-Type": "application/json" },
  body: JSON.stringify({ items: cart })
});
```

The `tma` prefix is the established community convention (from the Telegram Mini Apps ecosystem); the official Telegram docs define the validation algorithm but not the HTTP transport format.

The boilerplate includes a `callApi()` helper for Method B.

## Step 5: Backend Validation (never skip this)

**Copy `scripts/validate-init-data.js` (Node.js) or `scripts/validate_init_data.py` (Python) directly into the project.** Do not rewrite the algorithm — the HMAC chain is exact and easy to get subtly wrong.

```javascript
// Node.js / Express
const { requireTelegramAuth } = require('./validate-init-data');
app.post('/api/submit', requireTelegramAuth, (req, res) => {
  console.log(req.telegramUser.id); // verified
});
```

```python
# FastAPI
from validate_init_data import telegram_auth
@app.post("/api/submit")
async def submit(user=Depends(telegram_auth)):
    print(user["id"])  # verified
```

For Go: **copy `scripts/validate_init_data.go`**. Rename `package main` to your module's package name and remove the `main()` function. Do the same if you also copied `bot_go_handler.go` — both files default to `package main` with a `func main()` for standalone testing; they cannot coexist in the same directory without renaming.

Set `BOT_TOKEN` in the environment. CLI test:
```bash
python validate_init_data.py "$BOT_TOKEN" "$RAW_INIT_DATA"
go run scripts/validate_init_data.go "$BOT_TOKEN" "$RAW_INIT_DATA"
```

### Testing the auth flow

```bash
# Generate valid initData
INIT=$(python3 scripts/generate_test_init_data.py "$BOT_TOKEN")

# Smoke-test a running endpoint (checks valid/expired/tampered/missing auth)
BOT_TOKEN="..." ENDPOINT="http://localhost:3000/api/action" bash scripts/smoke_test_endpoint.sh
```

For other languages, see [references/security.md](references/security.md) (HMAC-SHA256 and Ed25519 sections).

## Step 6: Navigation (SPA Patterns)

For multi-page apps, use div-based page stacking or a hash router — **never** `window.location.href` or page reloads (they destroy the Telegram context).

The critical pattern is the **single BackButton handler** (shown in Step 3). Without `offClick`, every page push stacks another handler and the back button fires multiple times.

See [references/navigation.md](references/navigation.md) for:
- Div-based page stack with push/pop
- Hash router integration
- React Router / Vue Router / SvelteKit setup
- Scroll position preservation between pages
- Deep link routing via `tg.initDataUnsafe.start_param`

## Step 7: Bot Setup

See [references/bot-setup.md](references/bot-setup.md) for the full BotFather walkthrough, launch methods (keyboard button, inline button, direct link, menu button), receiving data in the bot, and proactive messaging. Quick version:

1. `/newbot` in [@BotFather](https://t.me/BotFather) → get token
2. `/newapp` → attach the Mini App URL
3. Copy a bot handler from `assets/bot-handlers/`:
   - `bot_python_ptb.py` — python-telegram-bot ≥ 20.x (async)
   - `bot_node_telegraf.js` — Telegraf.js v4
   - `bot_node_telegram_bot_api.js` — node-telegram-bot-api
   - `bot_go_handler.go` — go-telegram-bot-api v5

All handlers include `/start` (sends the Mini App button) and `web_app_data` handling.

For **answerWebAppQuery** (inline-bot mode, where the Mini App sends a result into the chat instead of calling your backend):
- Frontend: copy `assets/answer-web-app-query.js`
- Backend: copy `assets/answer_web_app_query.py` (FastAPI + Flask)

## Step 8: Payments (Telegram Stars)

Telegram Stars (XTR) is the built-in payment system — no payment provider needed.

```javascript
// 1. Frontend: request an invoice link from your backend, then open it
const { invoiceUrl } = await callApi('/api/create-invoice', { item: 'premium' });
tg.openInvoice(invoiceUrl, (status) => {
  if (status === 'paid') {
    // Note: "paid" here is not authoritative — verify server-side via successful_payment
    showThankYou();
  }
});
```

```python
# 2. Backend: create invoice link
invoice_link = await bot.create_invoice_link(
    title="Premium Access",
    description="Unlock all features for 30 days",
    payload="premium_30d",
    currency="XTR",         # Telegram Stars
    prices=[LabeledPrice("Premium", 100)],  # 100 Stars
)
```

```python
# 3. Bot: answer pre_checkout_query within 10 seconds (hard Telegram deadline)
async def pre_checkout_handler(update, context):
    await asyncio.wait_for(
        context.bot.answer_pre_checkout_query(update.pre_checkout_query.id, ok=True),
        timeout=8.0  # 2 seconds of margin
    )

# 4. Bot: handle successful_payment and activate the purchase
async def payment_success(update, context):
    charge_id = update.message.successful_payment.telegram_payment_charge_id
    # save charge_id for potential refunds, then activate purchase
```

See [references/payments.md](references/payments.md) for the complete flow including refunds, test mode, and `answerWebAppQuery` integration.

## Step 9: Advanced APIs

All advanced APIs require version gating. Check before use:

```javascript
if (!tg.isVersionAtLeast('8.0')) {
  // fallback for older clients
}
```

| API | Min version | Method |
|-----|-------------|--------|
| QR scanner | 6.4 | `tg.showScanQrPopup()` |
| Contact request | 6.9 | `tg.requestContact()` |
| Write access | 6.9 | `tg.requestWriteAccess()` |
| File download | 8.0 | `tg.downloadFile()` |
| Add to home screen | 8.0 | `tg.addToHomeScreen()` |
| Location | 8.0 | `tg.LocationManager` |
| Fullscreen | 8.0 | `tg.requestFullscreen()` |
| Accelerometer/Gyroscope | 8.0 | `tg.Accelerometer` |

See [references/advanced-apis.md](references/advanced-apis.md) for complete implementations of each API including version gates, fallbacks, and gotchas.

## Storage

| API | Capacity | Use for |
|-----|----------|---------|
| `tg.CloudStorage` | 1,024 keys × 4096 chars | Cross-device preferences |
| `tg.DeviceStorage` | 5 MB | Local cache, drafts (Bot API 9.0+) |
| `tg.SecureStorage` | 10 items | Sensitive tokens (Bot API 9.0+) |

```javascript
tg.CloudStorage.setItem("pref", "dark", (err) => {});
tg.CloudStorage.getItem("pref", (err, value) => applyTheme(value));
```

The mock backs all three with localStorage/sessionStorage so they work in browser dev.

## Security

The key security primitives are covered in [references/security.md](references/security.md):
- **CORS** — configure `Access-Control-Allow-Origin` to your exact frontend origin, not `*`
- **CSP** — use `unsafe-inline` only where Telegram theme injection requires it
- **Rate limiting** — limit by Telegram user ID, not IP (mobile clients share NAT)
- **Replay attacks** — short `auth_date` window (≤ 1h), Redis nonce for sensitive ops, or session token exchange
- **Empty initData** — detect browser visits (empty string) vs. missing header separately
- **Ed25519** — for validating initData without the bot token (third-party services)
- **Bot token rotation** — runbook for rotating compromised tokens

## Gotchas

**`initDataUnsafe` is unvalidated** — fine for greeting the user by name, never for authorization. Always validate `initData` server-side (use the bundled script).

**`sendData()` only works in keyboard-button mode** — has no effect if the app was opened via inline button, direct link, or menu button. Default to HTTP + initData for anything beyond the simplest flows.

**BackButton handlers stack** — calling `onClick()` multiple times stacks handlers; each fires on every back press. Always call `offClick(fn)` before registering a new handler. Use the single-handler pattern from Step 3.

**Safe areas need both var families** — `--tg-safe-area-inset-bottom` covers the device home bar; `--tg-content-safe-area-inset-bottom` covers Telegram's own bottom bar. Missing the second leaves content hidden behind Telegram's UI.

**Use `viewportStableHeight`, not `100vh`** — `viewportHeight` jumps during keyboard/gesture animations; `viewportStableHeight` only changes when the animation settles.

**`openLink()` requires a user gesture** — cannot be called from a timeout or async chain not originating from a click.

**Theme CSS vars are read-only** — Telegram injects them; setting `--tg-theme-*` yourself has no effect.

**CORS** — Mini Apps run in Telegram's webview, a different origin from your backend. Add `Access-Control-Allow-Origin` headers.

**Version gating** — fullscreen, accelerometer, gyroscope, location require Bot API 8.0+. Guard with `tg.isVersionAtLeast("8.0")`.

**Test on real mobile** — Telegram desktop and the in-browser dev preview render differently from iOS/Android. Ship only after testing on a real device.

**`pre_checkout_query` has a 10-second hard deadline** — answer it within 8 seconds (leave margin). If your stock check takes longer, answer optimistically then verify async.

**`SecondaryButton` requires Bot API 7.10+** — older clients ignore it silently. Always version-gate.

## Full API Reference

See [references/js-api.md](references/js-api.md) for all `window.Telegram.WebApp` methods, events, properties, storage APIs, and CSS variable names.
