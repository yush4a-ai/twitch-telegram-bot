# Telegram Web App JavaScript API Reference

Full reference for `window.Telegram.WebApp`. All properties/methods are on the `tg` alias:

```javascript
const tg = window.Telegram.WebApp;
```

## Core Properties

| Property | Type | Description |
|----------|------|-------------|
| `initData` | string | Raw URL-encoded query string — send to backend for validation |
| `initDataUnsafe` | object | Parsed initData — convenient but **never trust for auth** |
| `version` | string | Bot API version (e.g. "8.0") |
| `platform` | string | "android", "ios", "tdesktop", "macos", "web", "weba", "unknown" |
| `colorScheme` | string | "light" or "dark" |
| `themeParams` | object | Theme color hex values |
| `isExpanded` | boolean | Whether app is at max height |
| `viewportHeight` | number | Current visible height (changes during gestures) |
| `viewportStableHeight` | number | Height after gesture settles — use for pinned elements |
| `isActive` | boolean | Whether app is in foreground (Bot API 8.0+) |
| `isFullscreen` | boolean | Whether in fullscreen mode (Bot API 8.0+) |
| `safeAreaInset` | object | `{ top, bottom, left, right }` px values for device safe area (Bot API 8.0+) |
| `contentSafeAreaInset` | object | `{ top, bottom, left, right }` px values excluding Telegram chrome (Bot API 8.0+) |

## initDataUnsafe Fields

```typescript
interface InitDataUnsafe {
  user?: {
    id: number;
    first_name: string;
    last_name?: string;
    username?: string;
    language_code?: string;
    is_premium?: boolean;
    photo_url?: string;
    allows_write_to_pm?: boolean;    // true if user has already granted write access
    added_to_attachment_menu?: boolean;
  };
  chat?: { id: number; type: string; title: string; /* ... */ };
  start_param?: string;       // value from ?startapp=VALUE in direct link
  query_id?: string;          // present when launch supports answerWebAppQuery (inline button, attachment menu, direct link); absent in keyboard-button mode
  auth_date: number;          // unix timestamp
  hash: string;               // HMAC signature
}
```

`start_param` is the value from a `t.me/botname/appname?startapp=VALUE` deep link — great for passing context (referral codes, item IDs) into the app.

## Core Methods

### App Lifecycle

```javascript
tg.ready()                   // Call when UI is ready; removes loading spinner
tg.expand()                  // Expand to maximum height
tg.close()                   // Close the Mini App
tg.requestFullscreen()       // Enter fullscreen (Bot API 8.0+)
tg.exitFullscreen()          // Exit fullscreen (Bot API 8.0+)
tg.enableClosingConfirmation()  // Ask user before closing
tg.disableClosingConfirmation()
tg.lockOrientation()         // Lock to current orientation (Bot API 8.0+)
tg.unlockOrientation()       // (Bot API 8.0+)
```

### Sending Data

```javascript
// Keyboard-button mode only — delivers data to bot via web_app_data update
tg.sendData(stringData)

// Opens inline query in any chat (inline-mode bots)
tg.switchInlineQuery(query, chatTypes?)
// chatTypes: array of "users" | "bots" | "groups" | "channels"
```

### Navigation & Links

```javascript
tg.openLink(url, { try_instant_view: false })  // External browser
tg.openTelegramLink(url)                        // Telegram internal link
tg.openInvoice(url, callback)                   // Payment invoice
```

### Clipboard

```javascript
// Only works in attachment menu mode; requires user interaction
tg.readTextFromClipboard((text) => {
  if (text !== null) console.log(text);
});
```

### Alerts and Popups

```javascript
tg.showAlert("Message", () => { /* after dismissed */ })

tg.showConfirm("Sure?", (confirmed) => { /* boolean */ })

tg.showPopup({
  title: "Title",         // optional
  message: "Body text",
  buttons: [
    { id: "ok", type: "ok" },
    { id: "cancel", type: "cancel" },
    { id: "destroy", type: "destructive", text: "Delete" },
    { id: "custom", type: "default", text: "Custom" }
  ]
}, (buttonId) => { /* string id of pressed button */ })

tg.showScanQrPopup({ text: "Scan QR" }, (result) => {
  tg.closeScanQrPopup();
  // result: string or null
})
```

## UI Components

### MainButton / SecondaryButton

Both share the same interface. `MainButton` is the primary bottom button; `SecondaryButton` appears alongside it.

```javascript
const btn = tg.MainButton;

btn.setText("Click me")         // returns btn for chaining
btn.show()
btn.hide()
btn.enable()
btn.disable()
btn.showProgress(leaveActive?)  // show spinner; leaveActive keeps button clickable
btn.hideProgress()
btn.onClick(callback)
btn.offClick(callback)          // remove listener
btn.setParams({
  text: "Submit",
  color: "#ff0000",
  text_color: "#ffffff",
  is_active: true,
  is_visible: true,
  has_shine_effect: true,       // shiny animation (Bot API 7.10+)
  position: "left",             // "left"|"right"|"top"|"bottom" (SecondaryButton, Bot API 7.10+)
})
```

### BackButton

```javascript
tg.BackButton.show()
tg.BackButton.hide()
tg.BackButton.onClick(callback)
tg.BackButton.offClick(callback)
```

### SettingsButton

```javascript
tg.SettingsButton.show()
tg.SettingsButton.hide()
// Listen for clicks via:
tg.onEvent("settingsButtonClicked", handler)
```

### HapticFeedback

```javascript
tg.HapticFeedback.impactOccurred(style)
// style: "light" | "medium" | "heavy" | "rigid" | "soft"

tg.HapticFeedback.notificationOccurred(type)
// type: "error" | "success" | "warning"

tg.HapticFeedback.selectionChanged()  // for picker scroll ticks
```

## Storage APIs

### CloudStorage (cross-device, synced)

- Limit: 1,024 keys, 4 KB per value

```javascript
tg.CloudStorage.setItem(key, value, callback?)
// callback: (error, stored: boolean) => void

tg.CloudStorage.getItem(key, callback)
// callback: (error, value: string) => void

tg.CloudStorage.getItems(keys[], callback)
// callback: (error, values: Record<string, string>) => void

tg.CloudStorage.removeItem(key, callback?)
tg.CloudStorage.removeItems(keys[], callback?)
tg.CloudStorage.getKeys(callback)
// callback: (error, keys: string[]) => void
```

### DeviceStorage (local, 5 MB)

```javascript
tg.DeviceStorage.setItem(key, value, callback?)
tg.DeviceStorage.getItem(key, callback)
tg.DeviceStorage.removeItem(key, callback?)
tg.DeviceStorage.clear(callback?)
```

### SecureStorage (encrypted, 10 items max)

```javascript
tg.SecureStorage.setItem(key, value, callback?)
tg.SecureStorage.getItem(key, callback)
tg.SecureStorage.restoreItem(key, callback)   // restore from cloud backup
tg.SecureStorage.removeItem(key, callback?)
tg.SecureStorage.clear(callback?)
```

## Events

```javascript
tg.onEvent(eventType, handler)
tg.offEvent(eventType, handler)
```

| Event | Handler Receives | Notes |
|-------|-----------------|-------|
| `themeChanged` | — | Update colors |
| `viewportChanged` | `{ isStateStable: boolean }` | Use `isStateStable` to avoid mid-gesture redraws |
| `mainButtonClicked` | — | Same as MainButton.onClick |
| `secondaryButtonClicked` | — | |
| `backButtonClicked` | — | Same as BackButton.onClick |
| `settingsButtonClicked` | — | |
| `invoiceClosed` | `{ url, status }` | status: "paid", "cancelled", "failed", "pending" |
| `popupClosed` | `{ button_id: string \| null }` | null if dismissed without button |
| `qrTextReceived` | `{ data: string }` | QR scanner result |
| `scanQrPopupClosed` | — | |
| `clipboardTextReceived` | `{ data: string \| null }` | |
| `writeAccessRequested` | `{ status: "allowed" \| "cancelled" }` | |
| `contactRequested` | `{ status: "sent" \| "cancelled" }` | |
| `activated` | — | App comes to foreground (Bot API 8.0+) |
| `deactivated` | — | App goes to background (Bot API 8.0+) |
| `fullscreenChanged` | — | Bot API 8.0+ |
| `fullscreenFailed` | `{ error: string }` | Bot API 8.0+ |
| `locationRequested` | — | Location access requested; data is delivered via `LocationManager.getLocation()` callback (Bot API 8.0+) |
| `locationManagerUpdated` | — | LocationManager state changed (access granted/revoked) (Bot API 8.0+) |
| `accelerometerChanged` | — | Bot API 8.0+ |
| `deviceOrientationChanged` | — | Bot API 8.0+ |
| `gyroscopeChanged` | — | Bot API 8.0+ |
| `homeScreenAdded` | — | Home screen shortcut added (Bot API 8.0+) |
| `homeScreenChecked` | `{ status: string }` | Result of `checkHomeScreenStatus()` (Bot API 8.0+) |
| `safeAreaChanged` | — | Device safe area insets changed (Bot API 8.0+) |
| `contentSafeAreaChanged` | — | Content safe area insets changed (Bot API 8.0+) |

## Device APIs (Bot API 8.0+)

Always check version support first:

```javascript
if (tg.isVersionAtLeast("8.0")) {
  // safe to use 8.0+ APIs
}
```

### Location

```javascript
tg.LocationManager.init(() => {
  console.log("available:", tg.LocationManager.isLocationAvailable);
  if (tg.LocationManager.isAccessGranted) {
    tg.LocationManager.getLocation((data) => {
      // data: { latitude, longitude, altitude?, speed?, course?,
      //         horizontal_accuracy?, vertical_accuracy?, course_accuracy?, speed_accuracy? }
    });
  }
});
```

### Accelerometer / Gyroscope / Orientation

```javascript
tg.Accelerometer.start({ refresh_rate: 100 });  // ms between updates
tg.onEvent("accelerometerChanged", () => {
  console.log(tg.Accelerometer.x, tg.Accelerometer.y, tg.Accelerometer.z);
});
tg.Accelerometer.stop();

// Same interface for DeviceOrientation (need_absolute option) and Gyroscope
```

## Miscellaneous

```javascript
tg.isVersionAtLeast(version)   // boolean version check
tg.setHeaderColor(colorKey)    // "bg_color" | "secondary_bg_color" | hex
tg.setBackgroundColor(colorKey)
tg.setBottomBarColor(colorKey)
tg.requestContact(callback)    // callback: (shared: boolean) => void — phone goes to bot webhook, NOT to Mini App
tg.requestWriteAccess(callback) // callback: (granted: boolean) => void
tg.downloadFile({ url, file_name }, callback)  // trigger native download
tg.addToHomeScreen()           // add app shortcut to home screen
tg.checkHomeScreenStatus(callback)
// callback value: "unsupported" | "unknown" | "added" | "missed"
```

## ThemeParams Color Keys

All values are hex strings like `"#1c1c1e"`:

- `bg_color` — primary background
- `text_color` — primary text
- `hint_color` — placeholder / hint text
- `link_color` — hyperlinks
- `button_color` — primary button background
- `button_text_color` — primary button text
- `secondary_bg_color` — secondary surface (cards, sheets)
- `header_bg_color` — header background
- `bottom_bar_bg_color` — bottom bar background
- `accent_text_color` — accent / highlight text
- `section_bg_color` — section container background
- `section_header_text_color` — section header text
- `section_separator_color` — divider lines
- `subtitle_text_color` — secondary/muted text
- `destructive_text_color` — delete/danger actions

## CSS Variables Cheat Sheet

All `themeParams` keys are available as `--tg-theme-<key-with-dashes>`:

```css
--tg-theme-bg-color
--tg-theme-text-color
--tg-theme-hint-color
--tg-theme-link-color
--tg-theme-button-color
--tg-theme-button-text-color
--tg-theme-secondary-bg-color
--tg-theme-header-bg-color
--tg-theme-accent-text-color
--tg-theme-section-bg-color
--tg-theme-section-header-text-color
--tg-theme-section-separator-color
--tg-theme-subtitle-text-color
--tg-theme-destructive-text-color
--tg-theme-bottom-bar-bg-color

/* Viewport */
--tg-viewport-height
--tg-viewport-stable-height

/* Safe areas (device + Telegram UI) */
--tg-safe-area-inset-top
--tg-safe-area-inset-bottom
--tg-safe-area-inset-left
--tg-safe-area-inset-right
--tg-content-safe-area-inset-top
--tg-content-safe-area-inset-bottom
--tg-content-safe-area-inset-left
--tg-content-safe-area-inset-right
```
