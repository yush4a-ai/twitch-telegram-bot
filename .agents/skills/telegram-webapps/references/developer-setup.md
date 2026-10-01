# Developer Setup Guide

Everything you need to go from zero to a running Telegram Mini App in your local browser and inside Telegram itself.

---

## 1. The Dev Mock

### What miniapp-mock.js does

When you open your Mini App in a regular browser tab, `window.Telegram` does not exist. `miniapp-mock.js` installs a fake `window.Telegram.WebApp` object so your code does not crash and you can iterate without opening Telegram every time.

It simulates:
- `initData` / `initDataUnsafe` with placeholder user data
- `ready()`, `expand()`, `close()`
- `MainButton` and `BackButton` (visibility, text, click handlers)
- `showAlert()`, `showConfirm()`, `showPopup()` (delegates to native `window.alert` / `window.confirm`)
- `HapticFeedback` methods (logs to console instead of vibrating)
- `themeParams` with sensible light-mode defaults
- `colorScheme` set to `"light"`
- Stub payment methods (`openInvoice`, etc.)

It does NOT simulate:
- Real user identity or cryptographically valid `initData` — backend validation will reject it with 401, which is expected in dev. To test backend flows end-to-end locally, use `scripts/generate_test_init_data.py` to produce a real HMAC-signed fixture with your `BOT_TOKEN`, then paste it into your request headers.
- Camera / QR scanner
- Actual haptic feedback
- Payment flows (stubs only; no checkout UI)
- Biometric auth

### How to load it

**Load the mock before the real SDK** so `window.Telegram.WebApp` is always defined immediately. The mock self-disables when `tgWebAppData` is in the URL (i.e., inside real Telegram), so it is safe to always load:

```html
<!-- index.html — mock first, SDK second -->
<script src="/miniapp-mock.js"></script>
<script src="https://telegram.org/js/telegram-web-app.js"></script>
```

If you want to completely exclude the mock from production bundles (not just disable it), use a conditional dynamic load instead — but load it before the SDK in that case too:

```html
<!-- index.html — conditional approach -->
<script>
  (function () {
    var p = location.search, h = location.hash.slice(1);
    var isTelegram = new URLSearchParams(p).has('tgWebAppData') || new URLSearchParams(h).has('tgWebAppData');
    if (!isTelegram) {
      // Load mock synchronously so window.Telegram.WebApp exists before any inline scripts
      document.write('<script src="/miniapp-mock.js"><\/script>');
    }
  })();
</script>
<script src="https://telegram.org/js/telegram-web-app.js"></script>
```

Or in a module bundler entry point:

```ts
// src/main.ts  (runs before anything else)
const isTelegram = new URLSearchParams(location.search).has('tgWebAppData') || new URLSearchParams(location.hash.slice(1)).has('tgWebAppData');
if (!isTelegram) {
  // dynamic import keeps the mock out of the production bundle
  await import('./miniapp-mock.js');
}
```

### The isTelegramMockActive flag

`miniapp-mock.js` sets a global flag when it loads:

```js
window.isTelegramMockActive = true;
```

Use this anywhere in your code to branch on mock vs real behaviour:

```ts
if (window.isTelegramMockActive) {
  console.warn('Running with Telegram mock — initData validation will fail');
}
```

### Never load in production

The mock must never run inside the real Telegram webview. The detection above handles this automatically: Telegram always appends `tgWebAppData` to the URL, so the mock is skipped. If you use a build flag approach, add an extra guard:

```ts
if (import.meta.env.DEV && !isTelegram) {
  await import('./miniapp-mock.js');
}
```

---

## 2. HTTPS Requirement

Telegram's mobile and desktop webview enforces HTTPS for all Mini App URLs. This is a hard platform requirement — the webview refuses to open plain HTTP pages in production.

The reason is the same as browser mixed-content rules: Telegram passes sensitive user data (`initData`) to your page via the URL. Over HTTP that data is visible in plaintext to anyone on the network path.

**The one exception:** the Telegram test server (see section 5) intentionally allows plain HTTP, including `http://localhost`. Use the test server during early development when you do not yet have a domain or tunnel.

---

## 3. ngrok

ngrok creates a public HTTPS tunnel to your local dev server. It is the quickest way to test inside a real Telegram client.

### Install

```bash
# via npm
npm install -g ngrok

# via Homebrew
brew install ngrok/ngrok/ngrok
```

After installing, authenticate once (free account required):

```bash
ngrok config add-authtoken YOUR_AUTHTOKEN
```

### Run

```bash
ngrok http 3000
```

ngrok prints several URLs. Copy the `https://` one, e.g. `https://abc123.ngrok-free.app`.

### Update BotFather

Every time you get a new tunnel URL you must register it:

1. Open Telegram → @BotFather
2. `/mybots` → select your bot
3. "Bot Settings" → "Configure Mini App" → "Edit Mini App URL"
4. Paste the new https URL

### Skip the ngrok interstitial page

Free ngrok shows a browser warning page before forwarding. Bypass it with a custom request header:

```bash
ngrok http 3000 --request-header-add "ngrok-skip-browser-warning: 1"
```

Telegram's webview never shows the interstitial regardless, but your own browser tests will be faster without it.

### Persistent subdomains (paid)

Free ngrok generates a new random subdomain on every restart, which means you must re-update BotFather each time. ngrok paid plans let you reserve a fixed subdomain:

```bash
ngrok http 3000 --subdomain=myapp
# always gives https://myapp.ngrok.io
```

With a fixed subdomain you update BotFather once and never again.

---

## 4. Cloudflare Tunnel (free, persistent)

Cloudflare Tunnel is free and gives you a stable `*.trycloudflare.com` subdomain that persists across restarts. This is often more convenient than free ngrok because you only update BotFather once.

### Install cloudflared

```bash
# macOS
brew install cloudflare/cloudflare/cloudflared

# Linux
curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 -o cloudflared
chmod +x cloudflared
sudo mv cloudflared /usr/local/bin/
```

### Run

```bash
cloudflared tunnel --url http://localhost:3000
```

After a few seconds cloudflared prints a URL like `https://random-words-here.trycloudflare.com`. That subdomain stays the same as long as the process is running, and it comes back the same after a restart on the same machine (in most cases).

Register this URL once with BotFather. You do not need to re-register on every restart.

**Comparison with free ngrok:**

| | Free ngrok | Cloudflare Tunnel |
|---|---|---|
| Cost | Free tier available | Free |
| Subdomain persistence | Changes on restart | Stable across restarts |
| BotFather updates | Every restart | Once |
| Speed | Fast | Fast |
| Reliability | Good | Generally more stable |

---

## 5. Telegram Test Server

The Telegram test server is a completely separate environment from production. Bots, users, and data on the test server do not exist on production and vice versa.

### Switch to the test server

In Telegram Desktop:
1. Open Settings
2. Click your account name rapidly 5 times
3. A dialog appears — choose "Test Server"
4. Telegram restarts connected to the test environment

Switch back the same way (5-click trick again → "Production Server").

### Create a test bot

While connected to the test server, open @BotFather and run `/newbot`. You get a token that only works against the test API. **Test server Bot API calls use a different URL path:** `https://api.telegram.org/bot<TOKEN>/test/METHOD_NAME` (note `/test/` before the method). This token is completely separate from your production bot token.

Store them separately:

```bash
# .env.development
TELEGRAM_TEST_TOKEN=1234567890:TESTTOKEN...
TELEGRAM_TEST_API_URL=https://api.telegram.org/bot${TELEGRAM_TEST_TOKEN}/test

# .env.production
TELEGRAM_TOKEN=9876543210:PRODTOKEN...
```

### Use HTTP localhost directly

The test server does not enforce HTTPS. You can point your Mini App URL to:

```
http://localhost:3000
```

No ngrok, no tunnel, no certificate. Just register `http://localhost:3000` in BotFather (on the test server) and open your app from Telegram desktop while on the test server.

### What is separate between environments

- Bot tokens — completely different; a production token rejects requests made against test server data
- Users — a "test" account is separate from your production account
- Payments — test server uses Telegram's test payment provider (no real money)
- Files and media

---

## 6. Remote Debugging

### Telegram Desktop (easiest)

Open your Mini App inside Telegram Desktop. Right-click anywhere in the webview and choose **Inspect**. This opens standard Chrome DevTools attached to the Mini App's page.

Use the Network tab to inspect `initData` in request headers, the Console for JS errors, and Elements for layout issues.

### iOS

1. On the iPhone: Telegram → Settings → Advanced → Experimental → turn on **Enable WebView Inspecting**
2. Connect the iPhone to a Mac with a USB cable
3. On the Mac: open Safari → Develop menu → find your device → find the Mini App page
4. Safari's Web Inspector attaches — you get Console, Elements, and a Network tab

If the Develop menu is not visible in Safari: Safari → Settings → Advanced → check "Show features for web developers".

### Android

1. On the Android device: Settings → Developer Options → enable **USB debugging**
2. Connect to your computer with a USB cable
3. Open Chrome on the computer and navigate to `chrome://inspect`
4. Your Android device appears under "Remote Target" — click **inspect** next to the Mini App webview

### What to look for

| Symptom | Where to look |
|---|---|
| Blank white screen | Console — JS error before `tg.ready()` |
| `initData` empty | Console — you are in a browser, not Telegram; load the mock |
| API calls failing | Network tab — check for CORS errors or 401s |
| Layout overflow | Elements tab — check `viewport-fit`, `safe-area-inset` |
| App not full height | Console — confirm `tg.expand()` was called |

---

## 7. Framework Setup

### Vanilla JS / no framework

No build step required. Load the Telegram SDK, conditionally load the mock, then write your logic:

```html
<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <script src="https://telegram.org/js/telegram-web-app.js"></script>
  <script>
    (function () {
      var _p = location.search, _h = location.hash.slice(1);
      if (!new URLSearchParams(_p).has('tgWebAppData') && !new URLSearchParams(_h).has('tgWebAppData')) {
        var s = document.createElement('script');
        s.src = '/miniapp-mock.js';
        s.onload = init;
        document.head.appendChild(s);
      } else {
        document.addEventListener('DOMContentLoaded', init);
      }
    })();

    function init() {
      var tg = window.Telegram.WebApp;
      tg.ready();
      tg.expand();
      document.getElementById('user').textContent =
        tg.initDataUnsafe.user?.first_name ?? 'Unknown';
    }
  </script>
</head>
<body>
  <p>Hello, <span id="user"></span></p>
</body>
</html>
```

### React + Vite

Initialize the `tg` object at module level in a dedicated file, before any component runs:

```ts
// src/telegram.ts
// This module is imported first — tg is ready before any component mounts.
const isTelegram = new URLSearchParams(location.search).has('tgWebAppData') || new URLSearchParams(location.hash.slice(1)).has('tgWebAppData');
if (!isTelegram && import.meta.env.DEV) {
  // Mock must already be loaded via index.html <script> tag
  // OR use a synchronous import here if you bundle the mock
}

export const tg = window.Telegram.WebApp;
```

```tsx
// src/App.tsx
import { useEffect } from 'react';
import { tg } from './telegram';

export default function App() {
  useEffect(() => {
    tg.ready();
    tg.expand();
  }, []);

  const user = tg.initDataUnsafe.user;

  return <h1>Hello, {user?.first_name ?? 'guest'}</h1>;
}
```

```tsx
// src/main.tsx
import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
// Import telegram.ts first so tg exists before React renders anything
import './telegram';

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
```

For Vite dev with HTTPS (needed if you want to test some APIs that require a secure origin):

```bash
vite --https
# generates a self-signed cert; accept the browser warning in your desktop browser
```

> **Warning:** `vite --https` uses a self-signed certificate. Desktop browsers let you click through the certificate warning, but Telegram's **mobile WebView will silently refuse the connection** and show a blank page. For testing inside a real Telegram client, use ngrok or Cloudflare Tunnel (see above) instead of `--https`.

No special `vite.config.ts` changes are needed for a basic Mini App.

### Next.js

`window` does not exist on the server. All access to `window.Telegram.WebApp` must be inside Client Components, deferred until after mount:

```tsx
// src/hooks/useTelegram.ts
'use client';
import { useEffect, useState } from 'react';

type TelegramWebApp = typeof window.Telegram.WebApp;

export function useTelegram(): TelegramWebApp | null {
  const [tg, setTg] = useState<TelegramWebApp | null>(null);

  useEffect(() => {
    setTg(window.Telegram?.WebApp ?? null);
  }, []);

  return tg;
}
```

```tsx
// src/components/MiniApp.tsx
'use client';
import { useEffect } from 'react';
import { useTelegram } from '@/hooks/useTelegram';

export function MiniApp() {
  const tg = useTelegram();

  useEffect(() => {
    if (!tg) return;
    tg.ready();
    tg.expand();
  }, [tg]);

  if (!tg) return <div>Loading...</div>;

  return <h1>Hello, {tg.initDataUnsafe.user?.first_name}</h1>;
}
```

Never do this in a Server Component or at module level:

```ts
// BAD — crashes during SSR
import { tg } from './telegram'; // window is undefined on server
```

No special `next.config.js` changes are needed for a basic Mini App.

### Vue 3

Initialize in `main.ts` before `app.mount()`, then provide the `tg` object to the whole component tree:

```ts
// src/main.ts
import { createApp } from 'vue';
import App from './App.vue';

// tg must be initialized before any component mounts
const tg = window.Telegram.WebApp;
tg.ready();
tg.expand();

const app = createApp(App);
app.provide('tg', tg);
app.mount('#app');
```

```vue
<!-- src/components/UserGreeting.vue -->
<script setup lang="ts">
import { inject } from 'vue';

const tg = inject('tg') as typeof window.Telegram.WebApp;
const user = tg.initDataUnsafe.user;
</script>

<template>
  <h1>Hello, {{ user?.first_name ?? 'guest' }}</h1>
</template>
```

### SvelteKit

SvelteKit runs on the server side for SSR. Use the `browser` guard from `$app/environment`:

```ts
// src/lib/telegram.ts
import { browser } from '$app/environment';

// tg is null during SSR, real object in the browser
export const tg = browser ? (window.Telegram?.WebApp ?? null) : null;
```

```svelte
<!-- src/routes/+page.svelte -->
<script lang="ts">
  import { onMount } from 'svelte';
  import { tg } from '$lib/telegram';

  let userName = '';

  onMount(() => {
    if (!tg) return;
    tg.ready();
    tg.expand();
    userName = tg.initDataUnsafe.user?.first_name ?? 'guest';
  });
</script>

<h1>Hello, {userName}</h1>
```

---

## 8. Environment Variables

Your Mini App will call a backend API. The URL differs between dev and production.

### React / Vite

Prefix variables with `VITE_` to expose them to the browser bundle:

```bash
# .env.development
VITE_API_URL=http://localhost:8000

# .env.production
VITE_API_URL=https://api.yourapp.com
```

```ts
// src/api.ts
const API_URL = import.meta.env.VITE_API_URL;

export async function fetchOrders() {
  const res = await fetch(`${API_URL}/orders`, {
    headers: { 'Authorization': `tma ${window.Telegram.WebApp.initData}` },
  });
  return res.json();
}
```

Vite replaces `import.meta.env.VITE_*` at build time. Never put a secret in a `VITE_` variable — it ends up in the browser bundle.

### Next.js

Prefix variables with `NEXT_PUBLIC_` to expose them client-side:

```bash
# .env.local (development)
NEXT_PUBLIC_API_URL=http://localhost:8000

# .env.production
NEXT_PUBLIC_API_URL=https://api.yourapp.com
```

```ts
// src/lib/api.ts
const API_URL = process.env.NEXT_PUBLIC_API_URL!;

export async function fetchOrders(initData: string) {
  const res = await fetch(`${API_URL}/orders`, {
    headers: { 'Authorization': `tma ${initData}` },
  });
  return res.json();
}
```

### What never goes in frontend env vars

```bash
# NEVER put these in VITE_ or NEXT_PUBLIC_ variables
BOT_TOKEN=1234567890:ABCdef...     # backend only
DATABASE_URL=postgres://...         # backend only
SECRET_KEY=supersecret              # backend only
```

These end up in your JavaScript bundle where anyone can read them. The bot token in particular lets anyone impersonate your bot. Keep it server-side only.

---

## 9. Cache Busting

### Default behaviour (Vite / webpack)

Both Vite and webpack add a content hash to output filenames by default:

```
dist/assets/index-a1b2c3d4.js
dist/assets/index-e5f6g7h8.css
```

When you deploy a new build, the filenames change, so browsers fetch fresh files. Your `index.html` always gets fetched fresh (it has no hash). This is correct and requires no extra configuration.

### When you change the Mini App URL in BotFather

If you change your Mini App's registered URL (e.g. new domain or new path), some clients have the old URL cached in the Telegram client itself. Append a version query string to force a fresh fetch:

```
https://yourapp.com/?v=2
```

Update this in BotFather: `/mybots` → Bot Settings → Configure Mini App → Edit Mini App URL.

### Service workers

Avoid adding a service worker to your Mini App unless you have a specific offline-first requirement and understand the implications. Stale service workers are notoriously hard to debug in general, and inside Telegram's embedded webview the normal browser "Update on reload" developer tools behaviour may not work. If you must use one, always include a network-first strategy for your `index.html`:

```js
// service-worker.js — only if you absolutely need one
self.addEventListener('fetch', (event) => {
  if (event.request.url.endsWith('/')) {
    // Always go to network for the root page
    event.respondWith(fetch(event.request));
    return;
  }
  // Cache-first for hashed assets
  event.respondWith(
    caches.match(event.request).then(cached => cached ?? fetch(event.request))
  );
});
```

---

## 10. Common First-Run Errors

### "Domain not allowed"

The domain in your Mini App URL must be registered with BotFather for that bot. If you change your tunnel URL or deploy to a new domain:

BotFather → `/mybots` → select bot → "Bot Settings" → "Configure Mini App" → "Edit Mini App URL"

Register the new domain. The error goes away immediately — no waiting period.

### Blank white screen

A JavaScript error is crashing the page before `tg.ready()` runs. Open DevTools (right-click → Inspect in Telegram Desktop) and check the Console tab. Common causes:
- `Cannot read properties of undefined (reading 'WebApp')` — the SDK script did not load before your code ran; check script tag order
- SyntaxError — a bundler misconfiguration producing invalid JS

### initData is empty string

`window.Telegram.WebApp.initData === ""` means the page was opened in a regular browser, not inside Telegram. Either:
- Load the mock (see section 1) so you can develop in the browser
- Or open the page through an actual Telegram client

If you see this inside Telegram, the SDK script probably failed to load — check the Network tab for a 404 on `telegram-web-app.js`.

### Wrong token / invalid initData signature

```
{"ok":false,"error":"Invalid initData"}
```

You are checking `initData` signed by a test server token against a production token (or vice versa). Test and production Telegram environments use completely different bot tokens. Make sure your backend uses the same token as the environment that opened the Mini App.

### CORS error on API calls

```
Access to fetch at 'https://api.yourapp.com/orders' from origin 'https://yourapp.com' has been blocked by CORS policy
```

Your backend is not returning `Access-Control-Allow-Origin` for your Mini App's origin. Configure CORS on the backend to allow your Mini App domain. See `references/security.md` for the recommended CORS setup.

### App not expanding to full height

The Mini App opens in a compact view by default. Call `tg.expand()` after `tg.ready()`:

```ts
const tg = window.Telegram.WebApp;
tg.ready();
tg.expand(); // request full-screen height
```

If the app still does not expand, check that you are calling `expand()` in a user gesture handler or directly on page load — some older Telegram clients ignore it if called too late.

---

## Production Deployment

### 1. Choose a host

| Track | Recommended hosts | Notes |
|-------|------------------|-------|
| Static frontend only | Vercel, Netlify, Cloudflare Pages | Free tiers available; zero config for Vite/Next.js/SvelteKit |
| Full-stack (frontend + backend) | Railway, Render, Fly.io | Needed if your backend is Node/Python/Go |

Any host that serves your app over HTTPS on a public domain works — Telegram only requires a valid TLS cert.

### 2. Deploy

**Vercel:**
```bash
vercel --prod
```

**Netlify:**
```bash
netlify deploy --prod --dir=dist   # adjust dist/ to your build output folder
```

**Railway / Render:** push to your connected git branch — CI triggers automatically.

Note your deployed URL (e.g. `https://myapp.vercel.app`).

### 3. Register or update the URL in BotFather

**First deploy** — you already entered the URL during `/newapp`. You're set.

**URL changed** (new host, new domain):
```
@BotFather → /mybots → select your bot
→ Bot Settings → Configure Mini App → Edit URL
→ paste the new HTTPS URL
```

Skipping this step gives every user a "Domain not allowed" error. There is no grace period — the old URL stops working the moment Telegram's CDN doesn't recognize it.

### 4. Environment variables checklist

| Side | Variable | Value |
|------|----------|-------|
| Frontend | `VITE_API_URL` / `NEXT_PUBLIC_API_URL` | Your deployed backend URL |
| Backend | `BOT_TOKEN` | From BotFather — never hardcode or commit |

### 5. Test vs production

Telegram's test server is a completely separate environment — test bots, test users, test Stars (no real money). Never use a production `BOT_TOKEN` against test endpoints or vice versa. If you registered your bot on the test server during development, create a fresh bot on production before launch.
