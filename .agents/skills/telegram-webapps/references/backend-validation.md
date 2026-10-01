# Backend initData Validation

Telegram Mini Apps authenticate users by embedding a signed `initData` string in the web app. Your backend must verify this before trusting any user identity. A user can craft arbitrary `initDataUnsafe` values in their browser — the HMAC check is what makes the data trustworthy.

## The Algorithm

```
data_check_string = alphabetically sorted key=value pairs (excluding "hash"),
                    joined with "\n"

secret_key = HMAC-SHA256(key="WebAppData", message=bot_token)
expected_hash = HMAC-SHA256(key=secret_key, message=data_check_string)

valid = constant_time_compare(expected_hash, received_hash)
        AND (now - auth_date) < max_age_seconds
```

## Node.js (TypeScript)

```typescript
import * as crypto from "crypto";

export function validateInitData(
  rawInitData: string,
  botToken: string,
  maxAgeSeconds = 3600
): { valid: boolean; data: Record<string, string> } {
  const params = new URLSearchParams(rawInitData);
  const hash = params.get("hash");
  if (!hash) return { valid: false, data: {} };

  params.delete("hash");

  // Sort and build check string
  const checkString = [...params.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([k, v]) => `${k}=${v}`)
    .join("\n");

  // HMAC chain
  const secretKey = crypto
    .createHmac("sha256", "WebAppData")
    .update(botToken)
    .digest();

  const expectedHash = crypto
    .createHmac("sha256", secretKey)
    .update(checkString)
    .digest("hex");

  // Constant-time compare
  const hashBuffer = Buffer.from(hash, "hex");
  const expectedBuffer = Buffer.from(expectedHash, "hex");
  if (hashBuffer.length !== expectedBuffer.length) return { valid: false, data: {} };

  const valid = crypto.timingSafeEqual(hashBuffer, expectedBuffer);

  // Check freshness
  const authDate = Number(params.get("auth_date") ?? 0);
  const age = Math.floor(Date.now() / 1000) - authDate;
  if (age > maxAgeSeconds) return { valid: false, data: {} };

  return { valid, data: Object.fromEntries(params) };
}

// Express middleware example
app.post("/api/action", (req, res) => {
  const rawInitData = req.headers.authorization?.replace("tma ", "");
  if (!rawInitData) return res.status(401).json({ error: "Missing auth" });

  const { valid, data } = validateInitData(rawInitData, process.env.BOT_TOKEN!);
  if (!valid) return res.status(401).json({ error: "Invalid initData" });

  const user = JSON.parse(data.user); // safe to use now
  res.json({ ok: true, userId: user.id });
});
```

## Python

```python
import hashlib
import hmac
import time
from urllib.parse import parse_qsl, urlencode

def validate_init_data(raw_init_data: str, bot_token: str, max_age: int = 3600) -> dict | None:
    """Returns parsed data dict if valid, None if invalid."""
    params = dict(parse_qsl(raw_init_data, keep_blank_values=True))
    received_hash = params.pop("hash", None)
    if not received_hash:
        return None

    # Sort and build check string
    check_string = "\n".join(
        f"{k}={v}" for k, v in sorted(params.items())
    )

    # HMAC chain
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected_hash = hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()

    # Constant-time compare
    if not hmac.compare_digest(expected_hash, received_hash):
        return None

    # Check freshness
    auth_date = int(params.get("auth_date", 0))
    if time.time() - auth_date > max_age:
        return None

    return params

# FastAPI example
from fastapi import Header, HTTPException
import json, os

@app.post("/api/action")
async def action(authorization: str = Header(...)):
    raw = authorization.removeprefix("tma ")
    data = validate_init_data(raw, os.environ["BOT_TOKEN"])
    if not data:
        raise HTTPException(401, "Invalid initData")

    user = json.loads(data["user"])
    return {"ok": True, "user_id": user["id"]}
```

## Go

```go
package tgauth

import (
    "crypto/hmac"
    "crypto/sha256"
    "encoding/hex"
    "fmt"
    "net/url"
    "sort"
    "strconv"
    "strings"
    "time"
)

func ValidateInitData(rawInitData, botToken string, maxAge int64) (url.Values, error) {
    params, err := url.ParseQuery(rawInitData)
    if err != nil {
        return nil, err
    }

    receivedHash := params.Get("hash")
    if receivedHash == "" {
        return nil, fmt.Errorf("missing hash")
    }
    params.Del("hash")

    // Sort and build check string
    keys := make([]string, 0, len(params))
    for k := range params {
        keys = append(keys, k)
    }
    sort.Strings(keys)

    var lines []string
    for _, k := range keys {
        lines = append(lines, k+"="+params.Get(k))
    }
    checkString := strings.Join(lines, "\n")

    // HMAC chain
    secretKey := hmacSHA256([]byte("WebAppData"), []byte(botToken))
    expectedHash := hex.EncodeToString(hmacSHA256(secretKey, []byte(checkString)))

    // Constant-time compare
    if !hmac.Equal([]byte(expectedHash), []byte(receivedHash)) {
        return nil, fmt.Errorf("invalid hash")
    }

    // Check freshness
    authDate, _ := strconv.ParseInt(params.Get("auth_date"), 10, 64)
    if time.Now().Unix()-authDate > maxAge {
        return nil, fmt.Errorf("initData expired")
    }

    return params, nil
}

func hmacSHA256(key, data []byte) []byte {
    h := hmac.New(sha256.New, key)
    h.Write(data)
    return h.Sum(nil)
}
```

## Common Mistakes

**Using string equality instead of constant-time compare** — timing attacks can leak hash bytes. Always use `timingSafeEqual` / `hmac.compare_digest` / `hmac.Equal`.

**Skipping the auth_date check** — a stolen initData is valid forever if you don't check freshness. For sensitive actions (payments, data writes) use 5–10 minutes; for read-only display 1 hour is fine.

**Parsing initData with a general JSON parser** — it's a URL-encoded query string, not JSON. Use `URLSearchParams`, `parse_qsl`, or `url.ParseQuery`.

**Trusting initDataUnsafe.user directly** — `initDataUnsafe` is parsed in the browser with no verification. Never use it as an auth token; always validate `initData` server-side.

**Double-encoding the user field** — `initData.user` is a URL-encoded JSON string. Parse the query string first, then JSON.parse the `user` value.
