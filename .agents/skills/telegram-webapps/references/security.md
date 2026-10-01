# Telegram Mini App Backend Security Guide

## 1. CORS Configuration

Mini Apps send requests from their own origin (e.g. `https://myapp.example.com`) — not from a Telegram origin, not `null`. Configure your backend to allow that specific origin.

### Express / Node.js

```js
const cors = require('cors');

// Single origin from environment variable:
app.use(cors({
  origin: process.env.MINI_APP_ORIGIN, // e.g. 'https://myapp.example.com'
  methods: ['GET', 'POST', 'PUT', 'DELETE'],
  allowedHeaders: ['Content-Type', 'Authorization'],
}));

// For multiple environments (dev + prod):
const allowedOrigins = [
  'https://myapp.example.com',
  'http://localhost:5173', // Vite dev server
];
app.use(cors({
  origin: (origin, cb) => cb(null, !origin || allowedOrigins.includes(origin)),
}));
```

### FastAPI / Python

```python
import os
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=[os.environ["MINI_APP_ORIGIN"], "http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)
```

### Go (net/http)

```go
import (
    "net/http"
    "os"
)

func corsMiddleware(next http.Handler) http.Handler {
    return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
        origin := r.Header.Get("Origin")
        allowed := os.Getenv("MINI_APP_ORIGIN")
        if origin == allowed || origin == "http://localhost:5173" {
            w.Header().Set("Access-Control-Allow-Origin", origin)
            w.Header().Set("Access-Control-Allow-Headers", "Content-Type, Authorization")
            w.Header().Set("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        }
        if r.Method == http.MethodOptions {
            w.WriteHeader(http.StatusNoContent)
            return
        }
        next.ServeHTTP(w, r)
    })
}
```

**CORS gotchas:**
- `Access-Control-Allow-Origin: *` blocks cookies/credentials — use specific origins if you need cookies
- Preflight OPTIONS requests must return 200/204 — ensure your router handles them explicitly
- In local dev: use Vite proxy (`server.proxy` in `vite.config.ts`) to avoid CORS entirely during development

---

## 2. Content Security Policy

Add CSP as an HTTP response header (not a `<meta>` tag — meta tags cannot protect against some injection attacks):

```
Content-Security-Policy:
  default-src 'self';
  script-src 'self' https://telegram.org;
  connect-src 'self' https://your-api.example.com;
  img-src 'self' data: https:;
  style-src 'self' 'unsafe-inline';
  font-src 'self';
  frame-ancestors 'none';
```

Notes:
- `script-src https://telegram.org` — required for the Telegram SDK CDN script
- `'unsafe-inline'` in `style-src` — Telegram injects inline styles for theme variables; this is required
- Adjust `connect-src` to include your API domain and any CDNs you call from the frontend
- Test with `Content-Security-Policy-Report-Only` first to catch violations without breaking the app

### Express (with helmet)

```js
const helmet = require('helmet');

app.use(helmet.contentSecurityPolicy({
  directives: {
    defaultSrc: ["'self'"],
    scriptSrc: ["'self'", "https://telegram.org"],
    connectSrc: ["'self'", process.env.API_ORIGIN],
    imgSrc: ["'self'", "data:", "https:"],
    styleSrc: ["'self'", "'unsafe-inline'"],
    fontSrc: ["'self'"],
    frameAncestors: ["'none'"],
  },
}));
```

### FastAPI / Python

```python
from fastapi import Request
from fastapi.middleware.base import BaseHTTPMiddleware

class CSPMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        api_origin = os.environ.get("API_ORIGIN", "")
        csp = (
            "default-src 'self'; "
            "script-src 'self' https://telegram.org; "
            f"connect-src 'self' {api_origin}; "
            "img-src 'self' data: https:; "
            "style-src 'self' 'unsafe-inline'; "
            "font-src 'self'; "
            "frame-ancestors 'none';"
        )
        response.headers["Content-Security-Policy"] = csp
        return response

app.add_middleware(CSPMiddleware)
```

---

## 3. Rate Limiting by Telegram User ID

After validating `initData`, rate-limit by `req.telegramUser.id` — not by IP. Mobile users share IPs via carrier NAT, so IP-based rate limiting is ineffective and can accidentally block many legitimate users.

### Express (in-memory, single instance)

```js
const userRateLimits = new Map();

function telegramUserRateLimit(maxRequests, windowMs) {
  return (req, res, next) => {
    const userId = req.telegramUser?.id;
    if (!userId) return next(); // let auth middleware handle missing user

    const key = String(userId);
    const now = Date.now();
    const record = userRateLimits.get(key) || { count: 0, resetAt: now + windowMs };

    if (now > record.resetAt) {
      record.count = 0;
      record.resetAt = now + windowMs;
    }
    record.count++;
    userRateLimits.set(key, record);

    if (record.count > maxRequests) {
      return res.status(429).json({ error: 'Too many requests' });
    }
    next();
  };
}

// Usage — place AFTER requireTelegramAuth:
app.post('/api/order',
  requireTelegramAuth,
  telegramUserRateLimit(5, 60_000), // 5 orders per minute per user
  handleOrder
);
```

### Redis-backed rate limiting (production — survives restarts, works across multiple instances)

```js
const { RateLimiterRedis } = require('rate-limiter-flexible');
const Redis = require('ioredis');

const redis = new Redis(process.env.REDIS_URL);

const orderLimiter = new RateLimiterRedis({
  storeClient: redis,
  keyPrefix: 'tma:rl:order',
  points: 5,        // max requests
  duration: 60,     // per 60 seconds
});

async function telegramUserRateLimitRedis(limiter) {
  return async (req, res, next) => {
    const userId = req.telegramUser?.id;
    if (!userId) return next();

    try {
      await limiter.consume(String(userId));
      next();
    } catch (err) {
      res.status(429).json({ error: 'Too many requests', retryAfter: Math.ceil(err.msBeforeNext / 1000) });
    }
  };
}

// Usage:
app.post('/api/order',
  requireTelegramAuth,
  await telegramUserRateLimitRedis(orderLimiter),
  handleOrder
);
```

### FastAPI / Python (with slowapi)

```python
from slowapi import Limiter
from slowapi.util import get_remote_address
from fastapi import Request, Depends

# Key by Telegram user ID instead of IP:
def get_telegram_user_id(request: Request) -> str:
    user = getattr(request.state, "telegram_user", None)
    return str(user["id"]) if user else get_remote_address(request)

limiter = Limiter(key_func=get_telegram_user_id)

@app.post("/api/order")
@limiter.limit("5/minute")
async def place_order(request: Request):
    ...
```

---

## 4. Replay Attack Mitigation

**The problem:** A valid `initData` string can be captured (via network sniff or app code) and replayed to any endpoint within the `auth_date` window. A 1-hour window means a stolen `initData` can be used 3,600 times.

**Risk level by operation:**

| Operation | Recommended window | Nonce |
|---|---|---|
| Read-only display (profile, catalog) | 1 hour | No |
| Write operations (order, review) | 5 minutes | Optional |
| Financial operations (payment, transfer) | 1 minute | Yes |

### Option A: Short auth_date window

```js
// Pass maxAge in seconds to your validate function:
const { valid, user } = validateInitData(rawInitData, process.env.BOT_TOKEN, 300); // 5 minutes
```

```python
# Python equivalent — check auth_date freshness:
import time

def validate_init_data(raw_init_data: str, bot_token: str, max_age_seconds: int = 3600) -> dict:
    # ... parse and verify HMAC (see HMAC-SHA256 section above) ...
    auth_date = int(parsed_data.get("auth_date", 0))
    if time.time() - auth_date > max_age_seconds:
        raise ValueError("initData expired")
    return parsed_data
```

### Option B: One-time nonce with Redis

After a successful validation, mark the `initData` hash as used. Subsequent requests with the same `initData` are rejected.

```js
// Node.js / Express
async function validateAndConsume(rawInitData, botToken, redis) {
  const { valid, user, data } = validateInitData(rawInitData, botToken);
  if (!valid) return { valid: false, error: 'Invalid initData' };

  // The hash field is unique per initData string — use it as the nonce key
  const nonceKey = `tma:nonce:${data.hash}`;
  const alreadyUsed = await redis.get(nonceKey);
  if (alreadyUsed) return { valid: false, error: 'initData already used' };

  // Mark as used; expire after the auth_date window so Redis does not grow unbounded
  await redis.set(nonceKey, '1', 'EX', 3600);
  return { valid: true, user };
}

// Middleware:
async function requireTelegramAuthOnce(req, res, next) {
  const header = req.headers.authorization ?? '';
  if (!header.startsWith('tma ')) return res.status(401).json({ error: 'Missing or invalid Authorization scheme (expected: tma <initData>)' });
  const raw = header.slice(4);
  const result = await validateAndConsume(raw, process.env.BOT_TOKEN, redis);
  if (!result.valid) return res.status(401).json({ error: result.error });
  req.telegramUser = result.user;
  next();
}
```

```python
# Python / FastAPI equivalent
import hashlib
import redis as redis_lib

r = redis_lib.from_url(os.environ["REDIS_URL"])

async def validate_and_consume(raw_init_data: str, bot_token: str) -> dict:
    data = validate_init_data(raw_init_data, bot_token)  # raises on failure
    nonce_key = f"tma:nonce:{data['hash']}"
    if r.exists(nonce_key):
        raise ValueError("initData already used")
    r.set(nonce_key, "1", ex=3600)
    return data
```

### Option C: Session token exchange (best for multi-step flows)

On the first valid `initData`, issue a short-lived JWT or session token. Use that token for all subsequent requests. `initData` is consumed exactly once.

```js
const jwt = require('jsonwebtoken');

// Exchange endpoint — called once on app load:
app.post('/api/auth/exchange', async (req, res) => {
  const raw = (req.headers.authorization ?? '').replace(/^tma /, '');
  const result = await validateAndConsume(raw, process.env.BOT_TOKEN, redis);
  if (!result.valid) return res.status(401).json({ error: result.error });

  const sessionToken = jwt.sign(
    { sub: result.user.id, username: result.user.username },
    process.env.JWT_SECRET,
    { expiresIn: '1h' }
  );
  res.json({ token: sessionToken });
});

// All subsequent requests use Bearer <sessionToken>:
function requireSession(req, res, next) {
  const token = (req.headers.authorization ?? '').replace(/^Bearer /, '');
  try {
    req.session = jwt.verify(token, process.env.JWT_SECRET);
    next();
  } catch {
    res.status(401).json({ error: 'Invalid or expired session' });
  }
}
```

---

## 5. Handling Empty initData (Browser Detection)

When someone opens the Mini App URL directly in a browser, `tg.initData` is an empty string. Your frontend should detect this and show a clear message rather than sending a failing API call.

### Frontend check

```js
const tg = window.Telegram?.WebApp;

// Guard at app startup — before any API calls:
if (!tg?.initData && !window.isTelegramMockActive) {
  document.body.innerHTML = `
    <div style="padding:32px;text-align:center;font-family:system-ui">
      <h2>Open in Telegram</h2>
      <p>This app only works inside Telegram.<br>
         <a href="https://t.me/yourbot/yourapp">Open it here &rarr;</a></p>
    </div>
  `;
  throw new Error('Not running in Telegram — initData is empty');
}
```

### React / TypeScript version

```tsx
// src/components/TelegramGuard.tsx
import { useEffect, useState } from 'react';

export function TelegramGuard({ children }: { children: React.ReactNode }) {
  const [isReady, setIsReady] = useState(false);

  useEffect(() => {
    const tg = (window as any).Telegram?.WebApp;
    if (!tg?.initData && !(window as any).isTelegramMockActive) {
      // Not inside Telegram — render nothing, show the fallback below
      return;
    }
    setIsReady(true);
  }, []);

  if (!isReady) {
    return (
      <div style={{ padding: 32, textAlign: 'center', fontFamily: 'system-ui' }}>
        <h2>Open in Telegram</h2>
        <p>This app only works inside Telegram.<br />
          <a href="https://t.me/yourbot/yourapp">Open it here →</a>
        </p>
      </div>
    );
  }

  return <>{children}</>;
}
```

### Backend guard

```js
// requireTelegramAuth already returns 401 on empty/missing initData,
// but log empty initData separately to distinguish browser visits from real auth failures:
function requireTelegramAuth(req, res, next) {
  const header = req.headers.authorization ?? '';

  if (!header.startsWith('tma ')) {
    return res.status(401).json({ error: 'Missing Authorization header' });
  }

  const rawInitData = header.slice(4); // strip 'tma '

  if (rawInitData === '') {
    // Empty initData — user opened the URL in a browser, not Telegram
    console.info('Empty initData — likely a browser visit, not Telegram');
    return res.status(401).json({ error: 'Open this app inside Telegram' });
  }

  const { valid, user } = validateInitData(rawInitData, process.env.BOT_TOKEN);
  if (!valid) {
    return res.status(401).json({ error: 'Invalid initData' });
  }

  req.telegramUser = user;
  next();
}
```

---

## 6. Bot Token Rotation

If your `BOT_TOKEN` is leaked (committed to git, exposed in logs, captured in a bug report, etc.):

1. **Deploy the new token first.** Update `BOT_TOKEN` in your production environment before revoking. After revocation, all validation with the old token fails instantly — the new token must already be live.
2. **Revoke the old token.** In BotFather: `/mybots` → select bot → API Token → Revoke.
3. **Audit all locations.** If the token was stored in multiple places (CI secrets, staging, monitoring config), rotate every copy.
4. **Invalidate active sessions.** If you issue session tokens after `initData` validation, force re-auth for all users by rotating `JWT_SECRET` or flushing your session store.
5. **Check for secondary exposure.** Tokens in git history remain accessible after deletion — rotate secrets that appear in any commit, not just the latest file state.

**Prevention:**
- Store secrets in a secret manager (AWS Secrets Manager, GCP Secret Manager, HashiCorp Vault) — never in source control or `.env` files committed to git
- Use `git-secrets` or `trufflehog` in CI to catch accidental commits before they reach the remote
- Set up alerts for unusual bot API usage patterns (sudden spikes in `getMe` calls from unknown IPs)

```bash
# Scan git history for leaked secrets before pushing:
trufflehog git file://. --only-verified

# Or with git-secrets:
git secrets --scan-history
```

---

## 7. Ed25519 Third-Party Validation (Advanced)

Use this approach only if you are a **third-party service** that receives `initData` forwarded from someone else's Mini App — and you cannot or should not hold their bot token.

**How it works:** Telegram signs the `initData` with an Ed25519 private key and includes the signature in the `signature` field (base64url-encoded). Telegram publishes the corresponding public key at a well-known endpoint. You verify the signature against that public key without needing the bot token.

**Reference:** https://core.telegram.org/bots/webapps#validating-data-for-third-party-use-new

**When to use:**
- You are building a platform where Mini App developers integrate your service into their bots
- You receive forwarded `initData` and must verify it without holding the developer's bot token
- You need to verify identity across multiple bots with a single verification path

**When NOT to use:**
- You own the bot — use HMAC-SHA256 validation with your own bot token (see the HMAC-SHA256 section in this file)

### Node.js (using @noble/ed25519)

```js
// npm install @noble/ed25519@^2 @noble/hashes
const ed25519 = require('@noble/ed25519');
const { sha512 } = require('@noble/hashes/sha512');

// Required for @noble/ed25519 v2 synchronous verify:
ed25519.etc.sha512Sync = sha512;

// Telegram publishes static Ed25519 public keys per environment.
// Hard-code these — they change only on key rotation events.
// Keep an eye on https://core.telegram.org/bots/webapps for announcements.
const TELEGRAM_PUBLIC_KEYS = {
  production: Buffer.from('e7bf03a2fa4602af4580703d88dda5bb59f32ed8b02a56c187fe7d34caed242d', 'hex'),
  test:       Buffer.from('40055058a4ee38156a06562e52eece92a771bcd8346a8c4615cb7376eddf72ec', 'hex'),
};

function getTelegramPublicKey(env = 'production') {
  const key = TELEGRAM_PUBLIC_KEYS[env];
  if (!key) throw new Error(`Unknown Telegram environment: ${env}`);
  return key;
}

// botId: your bot's numeric ID (e.g. 123456789) — required by the spec
async function verifyInitDataEd25519(rawInitData, botId) {
  if (!botId) throw new Error('botId is required for Ed25519 validation');
  const params = new URLSearchParams(rawInitData);
  const signature = params.get('signature');
  if (!signature) throw new Error('No signature field in initData');

  // Build the check string per the official spec:
  //   "<bot_id>:WebAppData\n<sorted key=value pairs, excluding signature and hash>"
  params.delete('signature');
  params.delete('hash'); // also excluded per spec (unlike HMAC where hash is the output)
  const checkString = `${botId}:WebAppData\n` +
    Array.from(params.entries())
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([k, v]) => `${k}=${v}`)
      .join('\n');

  const publicKey = getTelegramPublicKey();

  // signature is base64url-encoded
  const sigBytes = Buffer.from(signature, 'base64url');
  const msgBytes = Buffer.from(checkString, 'utf8');

  const valid = ed25519.verify(sigBytes, msgBytes, publicKey);
  if (!valid) throw new Error('Ed25519 signature verification failed');

  // Also check auth_date freshness:
  const authDate = parseInt(params.get('auth_date') ?? '0', 10);
  if (Date.now() / 1000 - authDate > 3600) throw new Error('initData expired');

  return JSON.parse(params.get('user') ?? '{}');
}

// Express middleware — pass BOT_ID (numeric) from environment.
// For true third-party use (no bot token), set BOT_ID directly.
// BOT_TOKEN fallback is only for first-party deployments where you own the bot.
const BOT_ID = parseInt(process.env.BOT_ID ?? process.env.BOT_TOKEN?.split(':')[0] ?? '0', 10);

async function requireTelegramAuthEd25519(req, res, next) {
  const raw = (req.headers.authorization ?? '').replace(/^tma /, '');
  if (!raw) return res.status(401).json({ error: 'Missing initData' });

  try {
    req.telegramUser = await verifyInitDataEd25519(raw, BOT_ID);
    next();
  } catch (err) {
    res.status(401).json({ error: err.message });
  }
}
```

### Python (using PyNaCl)

```python
# pip install pynacl
import base64
import time
import urllib.parse
import nacl.signing
import nacl.exceptions

# Telegram publishes static Ed25519 public keys per environment.
# Hard-code these — they change only on key rotation events.
# Check https://core.telegram.org/bots/webapps for announcements.
TELEGRAM_PUBLIC_KEYS = {
    "production": bytes.fromhex("e7bf03a2fa4602af4580703d88dda5bb59f32ed8b02a56c187fe7d34caed242d"),
    "test":       bytes.fromhex("40055058a4ee38156a06562e52eece92a771bcd8346a8c4615cb7376eddf72ec"),
}

def get_telegram_public_key(env: str = "production") -> bytes:
    key = TELEGRAM_PUBLIC_KEYS.get(env)
    if key is None:
        raise ValueError(f"Unknown Telegram environment: {env}")
    return key


def verify_init_data_ed25519(raw_init_data: str, bot_id: int, max_age: int = 3600) -> dict:
    """bot_id: your bot's numeric ID — required by the spec."""
    params = dict(urllib.parse.parse_qsl(raw_init_data, keep_blank_values=True))
    signature_b64 = params.pop("signature", None)
    if not signature_b64:
        raise ValueError("No signature field in initData")
    params.pop("hash", None)  # also excluded per spec (unlike HMAC where hash is the output)

    # Build check string per the official spec:
    #   "<bot_id>:WebAppData\n<sorted key=value pairs, excluding signature and hash>"
    check_string = f"{bot_id}:WebAppData\n" + "\n".join(
        f"{k}={v}" for k, v in sorted(params.items())
    )

    public_key_bytes = get_telegram_public_key()
    verify_key = nacl.signing.VerifyKey(public_key_bytes)

    # Signature is base64url-encoded
    sig_bytes = base64.urlsafe_b64decode(signature_b64 + "=" * (-len(signature_b64) % 4))
    try:
        verify_key.verify(check_string.encode(), sig_bytes)
    except nacl.exceptions.BadSignatureError:
        raise ValueError("Ed25519 signature verification failed")

    auth_date = int(params.get("auth_date", 0))
    if time.time() - auth_date > max_age:
        raise ValueError("initData expired")

    import json
    return json.loads(params.get("user", "{}"))
```

**Important notes for Ed25519 validation:**
- Available from **Bot API 8.0+** — third-party initData sharing was introduced with the `shareMessage()` / forwarding flow in 8.0
- The check string format is `"<bot_id>:WebAppData\n<sorted key=value pairs>"` — the numeric bot ID prefix is mandatory per the official spec
- **Both `signature` and `hash` are excluded** from the check string (per the official Telegram docs); all other fields are included
- The bot ID can be derived from the bot token: the numeric part before the first `:` (e.g., token `123456789:ABCdef...` → bot_id `123456789`)
- The public keys above are static committed constants published by Telegram — do not fetch them dynamically from a URL
- These keys are treated as permanent; if Telegram ever rotates them, they will announce it with a migration guide at https://core.telegram.org/bots/webapps
- If signature verification fails unexpectedly across all requests, check whether Telegram announced a key change before assuming a code bug
- For `@noble/ed25519` v2, set `ed25519.etc.sha512Sync = sha512` (direct assignment — no wrapper needed)
