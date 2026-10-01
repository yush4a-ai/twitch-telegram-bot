# Advanced Telegram Mini App APIs

`const tg = window.Telegram.WebApp;` is assumed at the top of every example.

---

## 1. Version Gating

Always guard Bot API 8.0+ features. Never assume they exist.

```js
// Canonical pattern — use for every 8.0+ API
if (tg.isVersionAtLeast('8.0')) {
  // safe to use 8.0+ APIs
} else {
  // graceful fallback — show alternative or nothing
  console.log('Feature requires Telegram 8.0+');
}

// Checking specific feature availability (cleaner for individual features):
const canUseLocation   = tg.isVersionAtLeast('8.0') && tg.LocationManager;
const canGoFullscreen  = tg.isVersionAtLeast('8.0') && typeof tg.requestFullscreen === 'function';
const canUseAccel      = tg.isVersionAtLeast('8.0') && tg.Accelerometer;
const canUseSecure     = tg.isVersionAtLeast('9.0') && tg.SecureStorage;
```

### Features added per Bot API version

| Version | Key additions |
|---------|--------------|
| **6.0** | `WebApp` object, `MainButton`, `BackButton`, `HapticFeedback`, `initData`, `initDataUnsafe`, `themeParams`, `expand()`, `close()`, `sendData()`, `openLink()`, `openTelegramLink()`, `showAlert()`, `showConfirm()`, `showPopup()` |
| **6.1** | `openInvoice()` |
| **6.4** | `showScanQrPopup()`, `closeScanQrPopup()` |
| **6.7** | `switchInlineQuery()` |
| **6.9** | `CloudStorage` (key/value store, up to 1024 keys, 4096 chars per value), `setHeaderColor()` hex color support, `requestWriteAccess()`, `requestContact()` |
| **7.0** | `SettingsButton` |
| **7.2** | `BiometricManager` |
| **7.7** | `disableVerticalSwipes()`, `isVerticalSwipesEnabled` |
| **7.8** | `shareToStory()` |
| **7.10** | `SecondaryButton`, `setBottomBarColor()` |
| **8.0** | `LocationManager`, `Accelerometer`, `Gyroscope`, `DeviceOrientation`, `requestFullscreen()`, `exitFullscreen()`, `isFullscreen`, `shareMessage()`, `setEmojiStatus()`, `EmojiStatusManager`, `checkHomeScreenStatus()`, `addToHomeScreen()`, `downloadFile()` |
| **9.0** | `DeviceStorage`, `SecureStorage` |

**Rule of thumb:** any API not in the 6.0 column needs a version check. When in doubt, check.

---

## 2. QR Scanner

```js
// Open QR scanner with descriptive text shown to user
// Callback return value controls auto-close: return true to close, false to keep open.
tg.showScanQrPopup({ text: 'Scan the QR code on your ticket' }, (result) => {
  if (result) {
    console.log('QR result:', result);
    processQrCode(result);
    return true; // returning true auto-closes the scanner
  }
  return false; // keep open if result is empty/invalid
});

// Alternative: event-based (fires on every scan without closing the popup)
// Useful when you want to scan multiple codes in sequence
tg.onEvent('qrTextReceived', ({ data }) => {
  processQrCode(data);
  tg.closeScanQrPopup();
});

// Popup closed by user (cancelled)
tg.onEvent('scanQrPopupClosed', () => {
  console.log('User cancelled QR scan');
});

// Real-world pattern: multi-scan session (scan several items without reopening)
let scannedItems = [];

function startMultiScan() {
  tg.showScanQrPopup({ text: `Scanned: ${scannedItems.length} items. Scan another or close.` });
}

tg.onEvent('qrTextReceived', ({ data }) => {
  if (!scannedItems.includes(data)) {
    scannedItems.push(data);
    console.log('Added:', data);
    // Don't close — let user scan more
    // Optionally update the popup text (not directly supported — reopen with new text)
  }
});

tg.onEvent('scanQrPopupClosed', () => {
  processScannedBatch(scannedItems);
  scannedItems = [];
});
```

**Gotchas:**
- **Callback return value controls auto-close**: returning `true` closes the scanner; returning `false` (or nothing) keeps it open for another scan.
- You can also call `tg.closeScanQrPopup()` imperatively at any time (e.g. from a timeout or cancel button).
- The event-based `qrTextReceived` form does NOT auto-close — always call `closeScanQrPopup()` manually when done.
- Requires camera permission; Telegram handles the system prompt natively.
- Not available on desktop Telegram (no camera access). Gate with a device check or graceful message.
- `text` is the hint shown to the user inside the scanner UI, not a title.

---

## 3. Contact Request

Ask for the user's phone number (their own Telegram phone, not a contact picker).

**Important:** The callback only tells you whether the user shared their contact. The actual phone number is NOT returned to the Mini App — it is sent to your bot as a `contact` message via the bot webhook. Your frontend must poll or subscribe to your backend to get the phone number.

```js
// Must be called from a direct user gesture (click handler)
requestPhoneButton.addEventListener('click', () => {
  tg.requestContact((shared) => {
    if (shared) {
      // User agreed — the phone number is now being delivered to your bot
      // via a Telegram "contact" message in the bot's webhook.
      // Poll your backend or listen via WebSocket for the number.
      waitForPhoneFromBackend(tg.initDataUnsafe.user.id)
        .then((phone) => {
          console.log('Phone received on backend:', phone);
          linkPhoneNumber(phone);
        });
    } else {
      // User denied — show explanation or alternative
      tg.showAlert('Phone number is required for order delivery.');
    }
  });
});

// Full pattern with UX confirmation before requesting
function requestPhoneWithContext() {
  tg.showConfirm(
    'We need your phone number to send order confirmations via SMS. Share it?',
    (confirmed) => {
      if (!confirmed) return;
      tg.requestContact((shared) => {
        if (shared) {
          // Phone delivered to bot webhook — notify user and wait
          tg.showAlert('Thanks! We\'ll link your phone number in a moment.');
        }
      });
    }
  );
}
```

**Bot side — receive the contact:**

```python
# python-telegram-bot: the shared phone arrives as a contact message
from telegram.ext import MessageHandler, filters

async def handle_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    contact = update.message.contact
    # contact.phone_number, contact.first_name, contact.last_name, contact.user_id
    await save_phone(contact.user_id, contact.phone_number)

application.add_handler(MessageHandler(filters.CONTACT, handle_contact))
```

**Gotchas:**
- The phone number goes to the **bot webhook** as a `contact` message — it is NOT passed back to the Mini App in the callback. The callback only carries a boolean.
- Must be triggered by a user gesture (click/tap); calling it programmatically on load will silently fail or be blocked.
- Only works in mobile Telegram (iOS and Android). Desktop returns an error or does nothing.
- The system permission dialog is shown by Telegram natively — you don't need OS permission APIs.

---

## 4. Write Access Request

Required before sending proactive messages to the user outside the current chat context.

```js
// Show a clear value proposition before requesting
tg.showConfirm(
  'Allow order status notifications?',
  (confirmed) => {
    if (!confirmed) return;

    tg.requestWriteAccess((allowed) => {
      if (allowed) {
        // Backend can now send messages to this user via sendMessage API
        // Store user.id in your DB with write_access: true
        markUserAllowedNotifications(tg.initDataUnsafe.user.id);
        tg.showAlert('You will receive updates when your order ships!');
      } else {
        tg.showAlert('Without notifications, check your order status manually in the app.');
      }
    });
  }
);

// Event-based alternative — fires after Telegram's native dialog resolves
tg.onEvent('writeAccessRequested', ({ status }) => {
  if (status === 'allowed') {
    markUserAllowedNotifications(tg.initDataUnsafe.user.id);
  } else if (status === 'cancelled') {
    console.log('User declined write access');
  }
});

// Check before requesting (avoid double-prompting)
// initDataUnsafe.user.allows_write_to_pm is true if write access was already granted:
async function ensureWriteAccess() {
  if (tg.initDataUnsafe?.user?.allows_write_to_pm) return true; // already granted

  return new Promise((resolve) => {
    tg.requestWriteAccess((allowed) => {
      resolve(allowed);
    });
  });
}
```

**Important:** write access lets your bot send messages to the user's private chat. It does NOT grant access to send to groups or channels. The user can revoke it in Telegram settings at any time.

---

## 5. File Download

Trigger a native download from the Mini App (e.g., receipt PDF, generated image).

```js
// Trigger download of a file from URL (Bot API 8.0+)
tg.downloadFile(
  { url: 'https://yourapi.com/receipt/123.pdf', file_name: 'receipt-123.pdf' },
  (success) => {
    if (success) tg.showAlert('Receipt downloaded!');
    else tg.showAlert('Download failed. Try again.');
  }
);

// Generate and download on the fly (from a Blob)
function downloadBlob(blob, fileName) {
  const url = URL.createObjectURL(blob);
  tg.downloadFile({ url, file_name: fileName }, (success) => {
    URL.revokeObjectURL(url); // clean up immediately after — don't leak memory
    if (!success) tg.showAlert('Download failed.');
  });
}

// Practical example: download a canvas as PNG
async function downloadCanvasAsPng(canvas, fileName = 'image.png') {
  const blob = await new Promise((resolve) => canvas.toBlob(resolve, 'image/png'));
  downloadBlob(blob, fileName);
}

// Practical example: download a JSON export
function downloadJson(data, fileName = 'export.json') {
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
  downloadBlob(blob, fileName);
}
```

**Gotchas:**
- The URL must be accessible from Telegram's servers (not localhost or a private network).
- `file_name` controls what Telegram saves the file as — always set it explicitly.
- The callback fires with `false` on network errors, permission errors, or user cancellation — handle all three the same way.
- Added in Bot API 8.0 — version-gate if you target older clients: `if (tg.isVersionAtLeast('8.0')) { ... }`.
- Blob URLs created with `URL.createObjectURL` are short-lived — revoke them immediately in the callback.

---

## 6. Add to Home Screen

The highest-leverage retention mechanism — pins the app to the user's phone home screen as a standalone icon.

```js
// Check status before prompting (Bot API 8.0+)
tg.checkHomeScreenStatus((status) => {
  // status: 'added' | 'missed' | 'unknown' | 'unsupported'
  if (status === 'missed') {
    // Not added yet — show prompt after demonstrating value
    showAddToHomeScreenPrompt();
  } else if (status === 'added') {
    // Already on home screen — hide any "Add to home screen" UI
    document.getElementById('add-home-btn')?.remove();
  } else if (status === 'unknown') {
    // iOS always returns this (OS limitation) — still worth prompting
    showAddToHomeScreenPrompt();
  } else if (status === 'unsupported') {
    // Device doesn't support it — hide the option entirely
    document.getElementById('add-home-btn')?.remove();
  }
});

function showAddToHomeScreenPrompt() {
  tg.showConfirm(
    'Add this app to your home screen for quick access?',
    (confirmed) => {
      if (confirmed) tg.addToHomeScreen();
    }
  );
}

// Events fired after the attempt
tg.onEvent('homeScreenAdded', () => {
  console.log('Added to home screen successfully');
  // Update your UI — hide the prompt, show a thank-you message
  document.getElementById('add-home-btn')?.remove();
});

// Gating: checkHomeScreenStatus and addToHomeScreen both require Bot API 8.0+
if (tg.isVersionAtLeast('8.0')) {
  tg.checkHomeScreenStatus((status) => {
    if (status === 'missed' || status === 'unknown') {
      showAddToHomeScreenPrompt();
    }
  });
}
```

**Best practices:**
- Only prompt after the user has experienced value (e.g., after completing a first purchase, not on app open).
- Don't prompt on every session — it's annoying and users will dismiss it reflexively.
- iOS always returns `'unknown'` for `checkHomeScreenStatus` (OS limitation), but `addToHomeScreen()` still works.
- There is no `homeScreenFailed` event — if you need to confirm failure, call `checkHomeScreenStatus()` again after a short delay.
- Treat a dismissed confirm (user taps "No") as a permanent preference — don't re-prompt that session.

---

## 7. Location

```js
function initLocation() {
  // Version gate: use browser Geolocation as fallback for older clients
  if (!tg.isVersionAtLeast('8.0')) {
    if (navigator.geolocation) {
      navigator.geolocation.getCurrentPosition(
        (pos) => handleLocation(pos.coords),
        (err) => console.warn('Geolocation error:', err.message)
      );
    }
    return;
  }

  // Bot API 8.0+: use LocationManager
  tg.LocationManager.init(() => {
    if (!tg.LocationManager.isLocationAvailable) {
      // Device has no GPS (unlikely on modern mobile)
      tg.showAlert('Location is not available on this device.');
      return;
    }

    if (!tg.LocationManager.isAccessGranted) {
      // User previously denied — show a helpful message
      // Telegram will show a system prompt automatically when you call getLocation(),
      // but if they denied it at the OS level they need to go to Settings
      tg.showAlert('Please allow location access in Telegram settings to use this feature.');
      return;
    }

    tg.LocationManager.getLocation((data) => {
      if (!data) {
        tg.showAlert('Could not get your location. Please try again.');
        return;
      }
      handleLocation(data);
      // data shape:
      // {
      //   latitude: number,
      //   longitude: number,
      //   altitude?: number,          // meters above sea level
      //   speed?: number,             // m/s
      //   course?: number,            // degrees (0–360, clockwise from north)
      //   horizontal_accuracy?: number, // meters
      //   vertical_accuracy?: number,   // meters
      //   course_accuracy?: number,     // degrees
      //   speed_accuracy?: number,      // m/s
      // }
    });
  });
}
initLocation();

function handleLocation({ latitude, longitude }) {
  // Send coordinates to your backend or display on a map
  nearbySearch(latitude, longitude);
}

// Continuous tracking (e.g., delivery driver view)
// Returns a stop() function via the onReady callback — LocationManager.init is async.
function startLocationTracking(onUpdate, onReady) {
  if (!tg.isVersionAtLeast('8.0')) { onReady?.(null); return; }
  tg.LocationManager.init(() => {
    if (!tg.LocationManager.isAccessGranted) { onReady?.(null); return; }
    // Poll every 5 seconds — LocationManager doesn't have a push/watch mode
    const interval = setInterval(() => {
      tg.LocationManager.getLocation((data) => {
        if (data) onUpdate(data);
      });
    }, 5000);
    const stop = () => clearInterval(interval);
    onReady?.(stop);
  });
}

// Usage:
// startLocationTracking(handleLocation, (stop) => {
//   if (!stop) return; // no access
//   document.getElementById('stop-btn').onclick = stop;
// });

// Stop polling when done — saves battery
// (LocationManager has no close() method; simply stop calling getLocation)
```

**Gotchas:**
- `isAccessGranted` reflects the last-known permission state at init time. If the user denied it previously, calling `getLocation()` may show a system prompt or silently fail depending on OS.
- `altitude`, `speed`, `course` are only present if the device GPS supports them and the user is moving.
- `LocationManager` has no `close()` method — stop polling by clearing your interval/timeout when done.
- Do not cache coordinates for more than ~30 seconds for location-sensitive features (delivery, nearby search).

---

## 8. Fullscreen Mode

For games, media players, and immersive experiences. Bot API 8.0+.

```js
function initFullscreen() {
  if (!tg.isVersionAtLeast('8.0')) {
    // Fallback: expand to full Telegram sheet height (not true fullscreen)
    tg.expand();
    return;
  }

  // Enter fullscreen — hides Telegram's header and status bar overlay
  tg.requestFullscreen();

  // Listen for result
  tg.onEvent('fullscreenChanged', () => {
    console.log('Fullscreen:', tg.isFullscreen);
    if (tg.isFullscreen) {
      // Telegram chrome is hidden — use the full screen
      // Account for safe areas (iPhone notch, Android status bar)
      document.documentElement.style.setProperty(
        '--safe-top', `${tg.safeAreaInset?.top ?? 0}px`
      );
      document.body.classList.add('fullscreen-mode');
    } else {
      document.body.classList.remove('fullscreen-mode');
    }
  });

  tg.onEvent('fullscreenFailed', ({ error }) => {
    // error: 'UNSUPPORTED' | 'ALREADY_FULLSCREEN'
    console.warn('Fullscreen failed:', error);
    if (error === 'UNSUPPORTED') {
      // Device/platform doesn't support fullscreen — use expand() instead
      tg.expand();
    }
  });
}
initFullscreen();

// Exit fullscreen when the user taps a close/exit button in your UI:
// document.getElementById('exit-fs-btn').addEventListener('click', () => tg.exitFullscreen());

// CSS: handle safe areas in fullscreen mode
// In your stylesheet:
// .fullscreen-mode {
//   padding-top: var(--safe-top, 0px);  /* notch / status bar */
//   padding-bottom: env(safe-area-inset-bottom, 0px); /* home indicator */
// }
```

**When to use fullscreen:**
- Games that need the entire screen.
- Video or media players.
- Map views where every pixel counts.
- Immersive onboarding flows.

**When NOT to use fullscreen:** standard commerce or utility flows — Telegram's native chrome (back button, header) provides useful navigation context that you'd have to rebuild yourself.

---

## 9. Accelerometer, Gyroscope, and Device Orientation

For games and motion-responsive experiences. Bot API 8.0+.

```js
if (!tg.isVersionAtLeast('8.0')) {
  // No fallback via web APIs — they're blocked in Telegram's WebView
  console.warn('Motion sensors require Bot API 8.0+');
} else {

// --- Accelerometer: linear acceleration in m/s² ---
// refresh_rate: milliseconds between readings (valid range: 20–1000ms; lower = faster but more battery drain)
tg.Accelerometer.start({ refresh_rate: 50 }); // 20 readings/sec

tg.onEvent('accelerometerChanged', () => {
  const { x, y, z } = tg.Accelerometer;
  // x: left(-) / right(+), y: down(-) / up(+), z: into screen(-) / out(+)
  // Gravity contributes ~9.8 m/s² on the dominant axis when stationary
  // Typical range: -10 to +10 m/s²
  updateTiltUI(x, y);
  detectShake(x, y, z);
});

// Shake detection example
let lastMagnitude = 0;
function detectShake(x, y, z) {
  const magnitude = Math.sqrt(x * x + y * y + z * z);
  const delta = Math.abs(magnitude - lastMagnitude);
  lastMagnitude = magnitude;
  if (delta > 15) { // threshold in m/s² — tune to taste
    onShakeDetected();
  }
}

// Stop when not needed — saves battery immediately
tg.Accelerometer.stop();

// --- Gyroscope: angular velocity in rad/s ---
tg.Gyroscope.start({ refresh_rate: 50 });

tg.onEvent('gyroscopeChanged', () => {
  const { x, y, z } = tg.Gyroscope;
  // x: pitch rate, y: yaw rate, z: roll rate — all in rad/s
  // Use for steering controls in games, or detecting rotation gestures
});

tg.Gyroscope.stop();

// --- DeviceOrientation: absolute orientation in degrees ---
// need_absolute: true = relative to magnetic north (compass); false = relative to gravity
tg.DeviceOrientation.start({ refresh_rate: 50, need_absolute: true });

tg.onEvent('deviceOrientationChanged', () => {
  const { alpha, beta, gamma } = tg.DeviceOrientation;
  // alpha: compass heading 0–360° (0 = north, 90 = east)
  // beta:  front-back tilt -180°–180° (0 = flat, 90 = upright portrait)
  // gamma: left-right tilt -90°–90° (0 = flat, +90 = tilted right)
  renderCompass(alpha);
  updateHorizonIndicator(beta, gamma);
});

tg.DeviceOrientation.stop();

// Full game loop pattern: combine accelerometer + orientation
function startGameLoop() {
  if (!tg.isVersionAtLeast('8.0')) return;

  tg.Accelerometer.start({ refresh_rate: 33 }); // ~30 fps
  tg.DeviceOrientation.start({ refresh_rate: 33, need_absolute: false });

  tg.onEvent('accelerometerChanged', () => {
    gameState.tilt.x = tg.Accelerometer.x;
    gameState.tilt.y = tg.Accelerometer.y;
  });

  tg.onEvent('deviceOrientationChanged', () => {
    gameState.orientation.beta  = tg.DeviceOrientation.beta;
    gameState.orientation.gamma = tg.DeviceOrientation.gamma;
  });
}

function stopGameLoop() {
  tg.Accelerometer.stop();
  tg.DeviceOrientation.stop();
}

} // end Bot API 8.0+ version gate
```

**Gotchas:**
- All three sensors are independent — start/stop them separately.
- Always stop sensors when the user navigates away or closes the feature; they drain battery even in background.
- `need_absolute: true` requires a hardware magnetometer. If unavailable, the device may fall back to relative or return zero for `alpha`.
- Reading sensors every 16ms (60fps) is aggressive — 33–50ms (20–30fps) is usually sufficient for games and saves significant battery.

---

## 10. Header and Chrome Colors

Match Telegram's native chrome to your app's theme for a seamless look.

```js
// setHeaderColor accepts theme keys or exact hex colors
tg.setHeaderColor('bg_color');             // matches your app's primary background
tg.setHeaderColor('secondary_bg_color');   // matches your app's secondary surface
tg.setHeaderColor('#1a1a2e');              // exact hex — use for branded headers

// Page background and bottom bar
tg.setBackgroundColor('secondary_bg_color');  // WebView page background
tg.setBottomBarColor('bg_color');             // bottom navigation bar (Bot API 7.10+)
tg.setBottomBarColor('#1a1a2e');              // exact hex

// Re-apply on theme change (user switches light/dark mid-session)
tg.onEvent('themeChanged', () => {
  applyTheme();
});

function applyTheme() {
  const isDark = tg.colorScheme === 'dark';
  tg.setHeaderColor(isDark ? '#0d0d1a' : 'bg_color');
  tg.setBackgroundColor('secondary_bg_color');
  if (tg.isVersionAtLeast('7.10')) {
    tg.setBottomBarColor('bg_color');
  }
  // Also update your CSS variables to match
  document.documentElement.setAttribute('data-theme', isDark ? 'dark' : 'light');
}

// Initialize immediately on load
applyTheme();

// Access all theme colors for styling your app
const {
  bg_color,
  secondary_bg_color,
  text_color,
  hint_color,
  link_color,
  button_color,
  button_text_color,
  header_bg_color,
  accent_text_color,
  section_bg_color,
  section_header_text_color,
  subtitle_text_color,
  destructive_text_color,
} = tg.themeParams;

// Inject as CSS custom properties (match the --tg-theme-* namespace Telegram uses)
Object.entries(tg.themeParams).forEach(([key, value]) => {
  document.documentElement.style.setProperty(`--tg-theme-${key.replace(/_/g, '-')}`, value);
});
// Usage in CSS: background: var(--tg-theme-bg-color);
```

---

## 11. Opening Links

```js
// External URL — opens in the system browser (Safari, Chrome, etc.)
tg.openLink('https://example.com');

// External URL with Instant View (Telegram's built-in reading mode, if article supports it)
tg.openLink('https://example.com/article', { try_instant_view: true });

// Telegram link — navigates within Telegram natively (channel, user, bot, group)
tg.openTelegramLink('https://t.me/username');
tg.openTelegramLink('https://t.me/+inviteHash');      // private group invite
tg.openTelegramLink('https://t.me/yourbot?start=ref'); // start a bot with a parameter

// Open another Mini App from your Mini App (Bot API 7.0+)
tg.openTelegramLink('https://t.me/yourbot/yourapp?startapp=deeplink');

// NEVER use these for Telegram links — they open in a browser tab instead of Telegram's native UI:
// window.location.href = 'https://t.me/username'; // WRONG
// window.open('https://t.me/username');            // WRONG

// External link with a confirmation prompt (useful for untrusted URLs)
function safeOpenLink(url) {
  tg.showConfirm(`Open ${url} in your browser?`, (confirmed) => {
    if (confirmed) tg.openLink(url);
  });
}
```

---

## 12. Deep Link Routing

See also [navigation.md](navigation.md) for full routing patterns.

```js
// Reading the startapp parameter (passed via ?startapp=... in the Mini App link)
const startParam = tg.initDataUnsafe?.start_param || '';

// --- Simple string routes ---
// Link: https://t.me/yourbot/yourapp?startapp=product_123
if (startParam.startsWith('product_')) {
  const productId = startParam.replace('product_', '');
  navigateTo('product', { id: productId });
}

// --- Complex routes: base64url-encoded JSON ---
// Generating deep links from your backend:
// Max 512 chars; allowed characters: A–Z a–z 0–9 - _
function createDeepLink(payload) {
  const json = JSON.stringify(payload);
  // Node.js / backend:
  const encoded = Buffer.from(json).toString('base64url');
  return `https://t.me/yourbot/yourapp?startapp=${encoded}`;
}
// Example:
// createDeepLink({ page: 'product', id: 123, ref: 'friend_abc' })
// → https://t.me/yourbot/yourapp?startapp=eyJwYWdlIjoicHJvZHVjdCIsImlkIjoxMjMsInJlZiI6ImZyaWVuZF9hYmMifQ

// Decoding in the Mini App (browser-side, no Buffer):
function decodeStartParam(param) {
  if (!param) return null;
  try {
    // base64url → base64: replace - with + and _ with /
    const b64 = param.replace(/-/g, '+').replace(/_/g, '/');
    // Add padding
    const padded = b64 + '='.repeat((4 - b64.length % 4) % 4);
    return JSON.parse(atob(padded));
  } catch {
    return null; // Not a JSON payload — treat as simple string route
  }
}

const routeData = decodeStartParam(startParam);
if (routeData) {
  navigateTo(routeData.page, routeData);
} else if (startParam) {
  navigateTo(startParam); // simple string route
}

// Full routing bootstrap — call once on app load
function bootstrapRouting() {
  const param = tg.initDataUnsafe?.start_param;
  const parsed = decodeStartParam(param);

  if (parsed?.page) {
    navigateTo(parsed.page, parsed);
    return;
  }

  if (param) {
    // Fallback: treat as page name
    navigateTo(param);
    return;
  }

  // No deep link — show default home screen
  navigateTo('home');
}
```

---

## 13. Proactive Messaging Pattern

After the user interacts with your Mini App, you have their Telegram user ID. Here's how to send them follow-up messages.

```js
// Frontend — request write access at the right moment (e.g., after first order)
async function placeOrder(orderData) {
  const result = await callApi('/api/orders', orderData);

  // Request write access so we can send shipping/status updates
  tg.requestWriteAccess((allowed) => {
    if (allowed) {
      // Persist this flag — don't re-prompt every session
      tg.CloudStorage?.setItem('notifications_allowed', 'true', () => {});
      showToast("You'll receive a notification when your order ships!");
    }
  });

  return result;
}

// Frontend — check before requesting (avoid annoying re-prompts)
function maybeRequestWriteAccess() {
  if (!tg.CloudStorage) {
    // CloudStorage not available — always prompt
    tg.requestWriteAccess(() => {});
    return;
  }
  tg.CloudStorage.getItem('notifications_allowed', (err, value) => {
    if (value === 'true') return; // already granted
    tg.requestWriteAccess((allowed) => {
      if (allowed) tg.CloudStorage.setItem('notifications_allowed', 'true', () => {});
    });
  });
}
```

```python
# Backend — send a message to the user's private chat
# The user's chat_id for private messages = their user_id
import httpx
import os

async def notify_user(user_id: int, message: str):
    bot_token = os.environ["BOT_TOKEN"]
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            json={
                "chat_id": user_id,
                "text": message,
                "parse_mode": "HTML",
            }
        )
    return response.json()

# Works only if:
# 1. User has previously started the bot (sent /start), OR
# 2. User granted write access via tg.requestWriteAccess()

# Practical: order shipped notification
async def notify_order_shipped(user_id: int, order_id: str, tracking_url: str):
    message = (
        f"Your order <b>#{order_id}</b> has shipped! "
        f'<a href="{tracking_url}">Track your package</a>'
    )
    await notify_user(user_id, message)

# Practical: include a deep link back to the Mini App
async def notify_with_app_link(user_id: int, text: str, start_param: str, bot_username: str, app_name: str):
    import json, base64
    payload = base64.urlsafe_b64encode(json.dumps({"page": "order", "id": start_param}).encode()).decode().rstrip("=")
    app_url = f"https://t.me/{bot_username}/{app_name}?startapp={payload}"
    message = f"{text}\n\n<a href='{app_url}'>Open in App</a>"
    await notify_user(user_id, message)
```

**Backend checklist:**
1. Extract `user_id` from `initData` (always validate the hash server-side — see `references/security.md`).
2. Store `user_id` + `write_access: true` in your database after the user grants access.
3. Use `chat_id = user_id` for private messages — this is always the same value for a user.
4. Handle `403 Forbidden` (user blocked the bot) and `400 Bad Request` (user never started the bot) gracefully — remove their record from your notification list.

---

## Quick Reference: Which APIs Need Version Gates?

| API | Min version | Gate expression |
|-----|-------------|-----------------|
| `CloudStorage` | 6.9 | `tg.isVersionAtLeast('6.9') && tg.CloudStorage` |
| `showScanQrPopup` | 6.4 | `tg.isVersionAtLeast('6.4')` |
| `requestContact` | 6.9 | `tg.isVersionAtLeast('6.9')` |
| `requestWriteAccess` | 6.9 | `tg.isVersionAtLeast('6.9')` |
| `SecondaryButton` | 7.10 | `tg.isVersionAtLeast('7.10') && tg.SecondaryButton` |
| `SettingsButton` | 7.0 | `tg.isVersionAtLeast('7.0') && tg.SettingsButton` |
| `BiometricManager` | 7.2 | `tg.isVersionAtLeast('7.2') && tg.BiometricManager` |
| `setBottomBarColor` | 7.10 | `tg.isVersionAtLeast('7.10')` |
| `disableVerticalSwipes` | 7.7 | `tg.isVersionAtLeast('7.7')` |
| `addToHomeScreen` | 8.0 | `tg.isVersionAtLeast('8.0')` |
| `downloadFile` | 8.0 | `tg.isVersionAtLeast('8.0')` |
| `LocationManager` | 8.0 | `tg.isVersionAtLeast('8.0') && tg.LocationManager` |
| `Accelerometer` | 8.0 | `tg.isVersionAtLeast('8.0') && tg.Accelerometer` |
| `Gyroscope` | 8.0 | `tg.isVersionAtLeast('8.0') && tg.Gyroscope` |
| `DeviceOrientation` | 8.0 | `tg.isVersionAtLeast('8.0') && tg.DeviceOrientation` |
| `requestFullscreen` | 8.0 | `tg.isVersionAtLeast('8.0') && typeof tg.requestFullscreen === 'function'` |
| `SecureStorage` | 9.0 | `tg.isVersionAtLeast('9.0') && tg.SecureStorage` |
| `DeviceStorage` | 9.0 | `tg.isVersionAtLeast('9.0') && tg.DeviceStorage` |
