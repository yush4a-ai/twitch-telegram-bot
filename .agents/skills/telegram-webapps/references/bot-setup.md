# Bot & Mini App Setup Guide

This guide walks through setting up a Telegram bot and attaching a Mini App to it.

## Prerequisites

- A deployed HTTPS URL for your web app (Telegram requires HTTPS; `localhost` only works in dev with BotFather's test environment)
- Telegram account

## Step 1: Create a Bot

1. Open Telegram and search for [@BotFather](https://t.me/BotFather)
2. Send `/newbot`
3. Choose a display name (e.g. "My Shop Bot")
4. Choose a username ending in `bot` (e.g. `myshop_bot`)
5. Save the **bot token** — looks like `1234567890:ABCdef...`

Store the token in your environment: `BOT_TOKEN=1234567890:ABCdef...`

## Step 2: Create a Mini App

1. In BotFather: `/newapp`
2. Select your bot
3. Enter a short app name (this becomes the URL path: `t.me/botname/appname`)
4. Enter a description
5. Upload a 640×360 image (the app preview)
6. Enter your HTTPS URL (the page that loads your Mini App)

Your app is now accessible at: `https://t.me/yourbot/yourapp`

## Step 3: Set the Menu Button (optional)

Replace the default keyboard icon with your app:

1. BotFather → `/mybots` → select bot → "Bot Settings" → "Menu Button"
2. Or configure via Bot API:
```
POST https://api.telegram.org/bot<TOKEN>/setChatMenuButton
{
  "menu_button": {
    "type": "web_app",
    "text": "Open App",
    "web_app": { "url": "https://yourapp.com" }
  }
}
```

## Step 4: Launch Methods

### From an inline keyboard button (most common)

Use `web_app` type button in your bot's message:

```python
# python-telegram-bot example
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

keyboard = [[
    InlineKeyboardButton(
        "Open App",
        web_app=WebAppInfo(url="https://yourapp.com")
    )
]]
await update.message.reply_text("Click to open:", reply_markup=InlineKeyboardMarkup(keyboard))
```

```javascript
// node-telegram-bot-api example
bot.sendMessage(chatId, "Click to open:", {
  reply_markup: {
    inline_keyboard: [[{
      text: "Open App",
      web_app: { url: "https://yourapp.com" }
    }]]
  }
});
```

### From a custom keyboard button

```python
from telegram import KeyboardButton, ReplyKeyboardMarkup, WebAppInfo

keyboard = [[KeyboardButton("Open App", web_app=WebAppInfo(url="https://yourapp.com"))]]
await update.message.reply_text("Use button:", reply_markup=ReplyKeyboardMarkup(keyboard))
```

When using a keyboard button, `sendData()` works and delivers data as a `web_app_data` message to the bot.

### Deep link (direct URL)

```
https://t.me/yourbot/yourapp?startapp=referral_code
```

The `startapp` value is available in the Mini App as `tg.initDataUnsafe.start_param`.

### From bot profile page

Set via BotFather → `/mybots` → Bot Settings → Configure Mini App.

## Step 5: Receive Data in Your Bot

### From sendData() (keyboard button mode)

```python
from telegram.ext import MessageHandler, filters

async def handle_web_app_data(update, context):
    data = update.message.web_app_data.data  # the string you passed to sendData()
    payload = json.loads(data)
    await update.message.reply_text(f"Received: {payload}")

application.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, handle_web_app_data))
```

### From answerWebAppQuery (inline button mode)

When the Mini App is opened via inline button, it has a `query_id` in `initData`. Your backend calls `answerWebAppQuery` to deliver a message:

```python
await context.bot.answer_web_app_query(
    web_app_query_id=query_id,
    result=InlineQueryResultArticle(
        id="result",
        title="Order submitted",
        input_message_content=InputTextMessageContent("Your order is confirmed!")
    )
)
```

## Development & Testing

### Local development

Telegram requires HTTPS. For local dev use [ngrok](https://ngrok.com) or [Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/):

```bash
ngrok http 3000
# copy the https URL, set it as your Mini App URL in BotFather
```

### Telegram test environment

BotFather operates in both production and a test environment. The test environment uses a separate set of tokens and lets you use `http://` URLs:

1. Access test env: in Telegram desktop settings, click account name 5× fast → "Test Server"
2. Create a test bot in BotFather (within test env)
3. Use `localhost` or HTTP URLs freely

### Debugging

- Open Mini App in Telegram desktop → right-click → "Inspect"
- On mobile: Telegram Settings → Advanced → Experimental → "Enable WebView Inspecting" (iOS: Safari inspector; Android: Chrome devtools via `chrome://inspect`)

## Common Setup Issues

**"Domain not allowed"** — the domain of your Mini App URL must be registered with BotFather. If you change domains, update it: BotFather → `/mybots` → Bot Settings → Configure Mini App → Edit URL.

**App doesn't receive initData** — make sure the SDK script loads before your JavaScript. The `initData` is only populated when the page is actually opened through Telegram (not in a regular browser tab).

**sendData silently does nothing** — `sendData()` only works when the app was opened via a keyboard button (reply keyboard with `web_app` type). If opened via inline button or direct link, use HTTP + initData validation instead.

**Bot token in HTTPS validation returns invalid** — make sure you're using the correct token. Test and production Telegram environments use different bot tokens.

## Proactive Messaging (sending notifications to users)

The Mini App runs in the foreground — to reach users later (order updates, reminders, alerts) your bot needs write access to their chat, granted while they are in the app.

### Step 1 — Request write access (frontend)

```javascript
const tg = window.Telegram.WebApp;

// Guard with version check — requestWriteAccess requires Bot API 6.9+
if (tg.isVersionAtLeast('6.9')) {
  tg.requestWriteAccess(async (granted) => {
    if (!granted) return;  // user declined

    // Persist so you don't ask again
    tg.CloudStorage.setItem('write_access_granted', '1', () => {});

    // Tell your backend — it stores the user's Telegram ID as the chat_id
    await fetch('/api/register-for-notifications', {
      method: 'POST',
      headers: { 'Authorization': `tma ${tg.initData}` },
    });
  });
}
```

### Step 2 — Send a message (backend)

```python
# Python — send a message to a stored chat_id
import httpx, os

async def send_notification(chat_id: int, text: str, **kwargs):
    """kwargs are passed through to sendMessage (e.g. reply_markup, parse_mode override)."""
    async with httpx.AsyncClient() as client:
        await client.post(
            f"https://api.telegram.org/bot{os.environ['BOT_TOKEN']}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML", **kwargs},
        )
```

```javascript
// Node.js — send a message to a stored chat_id
async function sendNotification(chatId, text) {
  await fetch(`https://api.telegram.org/bot${process.env.BOT_TOKEN}/sendMessage`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ chat_id: chatId, text, parse_mode: 'HTML' }),
  });
}
```

### Including a deep link back to the app

```python
await send_notification(
    chat_id=chat_id,
    text='Your order is ready! Open the app to track delivery.',
    reply_markup={
        'inline_keyboard': [[{
            'text': 'Track Order',
            'web_app': {'url': f'{MINI_APP_URL}?startapp=order_{order_id}'}
        }]]
    }
)
```

**Gotchas:**
- Write access persists once granted; check `initDataUnsafe.user.allows_write_to_pm` before prompting to avoid re-asking users who already approved. The user can revoke it at any time in Telegram settings.
- Store the `chat_id` from `initDataUnsafe.user.id` (the Telegram user ID doubles as the direct-message chat ID for bots).
- `sendMessage` fails with 403 if the user has blocked the bot. Catch and deregister gracefully.
- For deep links, encode the `startapp` payload as base64url if it contains special characters; the Mini App receives it as `tg.initDataUnsafe.start_param`.

See [references/advanced-apis.md](advanced-apis.md#13-proactive-messaging-pattern) for the complete pattern including CloudStorage deduplication and error handling.
