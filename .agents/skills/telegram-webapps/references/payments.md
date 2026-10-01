# Telegram Stars Payments Guide for Mini Apps

## Overview

Telegram Stars (currency code `XTR`) are used exclusively for digital goods. No payment provider token is needed — Telegram handles the payment processing entirely. Physical goods use regular Telegram Payments (provider token required, different flow). This guide covers Stars only.

Full end-to-end flow:
1. Bot creates an invoice link via Bot API
2. Mini App calls `openInvoice()` with the link
3. User pays in the Telegram payment UI
4. Bot receives `pre_checkout_query` — must answer within 10 seconds
5. Bot receives `successful_payment` — authoritative confirmation
6. Bot delivers the digital good

## Creating an Invoice Link (Bot Side)

```python
# python-telegram-bot
from telegram import LabeledPrice

async def create_payment_link(context, user_id: int, stars: int, payload: str) -> str:
    """
    stars: number of Stars (integer, minimum 1)
    payload: your internal order/product ID (returned in successful_payment)
    Returns: an invoice link URL the Mini App can open
    """
    link = await context.bot.create_invoice_link(
        title="Premium Access",          # shown to user
        description="30-day premium",    # shown to user
        payload=payload,                 # your internal data, NOT shown to user
        currency="XTR",                  # Stars only
        provider_token="",               # empty string for Stars
        prices=[LabeledPrice("Premium", stars)],  # LabeledPrice, not a dict; amount = Stars
    )
    return link
```

```js
// Node.js — direct Bot API call
async function createInvoiceLink({ title, description, payload, stars }) {
  const res = await fetch(`https://api.telegram.org/bot${process.env.BOT_TOKEN}/createInvoiceLink`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      title, description, payload,
      currency: 'XTR',
      provider_token: '',
      prices: [{ label: title, amount: stars }],
    }),
  });
  const data = await res.json();
  if (!data.ok) throw new Error(data.description);
  return data.result; // the invoice link URL
}
```

**Stars restrictions:**
- Only ONE price item allowed — no multi-item line items
- No shipping info, email, or phone collection
- No provider token — leave it as an empty string or omit it
- Amount is in whole Stars (1 = 1 Star). Minimum is 1 Star
- Invoice links expire — generate a fresh one for each purchase attempt

## Opening the Invoice in the Mini App (Frontend)

```js
const tg = window.Telegram.WebApp;

async function purchasePremium() {
  // 1. Get invoice link from your backend (which creates it via Bot API)
  tg.MainButton.showProgress();
  const { invoiceLink } = await callApi('/api/create-invoice', {
    product: 'premium_30d',
    userId: tg.initDataUnsafe.user.id,
  });
  tg.MainButton.hideProgress();

  // 2. Open the payment UI
  tg.openInvoice(invoiceLink, (status) => {
    // status: 'paid' | 'cancelled' | 'failed' | 'pending'
    if (status === 'paid') {
      // Payment confirmed client-side — but always verify server-side too
      showSuccessScreen();
    } else if (status === 'cancelled') {
      // User closed the payment screen
      tg.showAlert('Payment cancelled.');
    } else if (status === 'failed') {
      tg.showAlert('Payment failed. Please try again.');
    } else if (status === 'pending') {
      // Payment processing — wait for backend webhook
      tg.showAlert('Payment is processing. You\'ll be notified shortly.');
    }
  });
}

// Alternative: event-based
tg.onEvent('invoiceClosed', ({ url, status }) => {
  console.log('Invoice closed:', url, status);
});
```

**Important:** The `paid` status in the frontend callback is NOT authoritative. Always confirm payment on the backend via the `successful_payment` update. The callback fires optimistically from the client.

## pre_checkout_query Handler (Bot Side)

The bot receives this BEFORE payment is processed. You MUST respond within 10 seconds. Use this to validate stock, eligibility, and pricing. Return `ok=True` to proceed, `ok=False` to reject.

```python
# python-telegram-bot
from telegram.ext import PreCheckoutQueryHandler

async def pre_checkout_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.pre_checkout_query

    # Validate before accepting
    try:
        # Check: is this product still available?
        product_id = query.invoice_payload  # your payload from createInvoiceLink
        is_valid = await validate_product_available(product_id)

        if is_valid:
            await query.answer(ok=True)
        else:
            await query.answer(ok=False, error_message="This item is no longer available.")
    except Exception as e:
        # MUST always answer — if you don't, the transaction auto-cancels after 10s
        await query.answer(ok=False, error_message="Something went wrong. Please try again.")

application.add_handler(PreCheckoutQueryHandler(pre_checkout_callback))
```

```js
// Telegraf
bot.on('pre_checkout_query', async (ctx) => {
  const { invoice_payload, total_amount, currency } = ctx.preCheckoutQuery;
  try {
    const valid = await validateOrder(invoice_payload);
    if (valid) {
      await ctx.answerPreCheckoutQuery(true);
    } else {
      await ctx.answerPreCheckoutQuery(false, 'Item no longer available');
    }
  } catch (err) {
    await ctx.answerPreCheckoutQuery(false, 'Server error, try again');
  }
});
```

**CRITICAL:** The 10-second deadline is hard. Always add a timeout to your validation logic:

```python
import asyncio

async def pre_checkout_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.pre_checkout_query
    try:
        result = await asyncio.wait_for(
            validate_order(query.invoice_payload),
            timeout=8.0  # leave 2s buffer before the 10s hard deadline
        )
        await query.answer(ok=result)
    except asyncio.TimeoutError:
        await query.answer(ok=False, error_message="Timeout validating order")
    except Exception:
        await query.answer(ok=False, error_message="Something went wrong. Please try again.")
```

## successful_payment Handler (Bot Side)

This fires AFTER the user pays. Deliver the digital good here. This is the authoritative confirmation.

```python
# python-telegram-bot
from telegram.ext import MessageHandler, filters

async def successful_payment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    payment = update.message.successful_payment

    # payment.invoice_payload: your original payload
    # payment.telegram_payment_charge_id: Telegram's charge ID (save for refunds)
    # payment.total_amount: Stars paid
    # payment.currency: 'XTR'

    # 1. Record the payment in your DB
    await record_payment(
        user_id=update.effective_user.id,
        payload=payment.invoice_payload,
        charge_id=payment.telegram_payment_charge_id,  # save for refundStarPayment
    )

    # 2. Activate the digital good
    await activate_product(payment.invoice_payload, update.effective_user.id)

    # 3. Confirm to user
    await update.message.reply_text(
        "Payment received! Your premium access is now active.",
        parse_mode='HTML'
    )

application.add_handler(
    MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_callback)
)
```

```js
// Telegraf
bot.on('message', async (ctx) => {
  if (!ctx.message.successful_payment) return;
  const { telegram_payment_charge_id, invoice_payload, total_amount } = ctx.message.successful_payment;

  await db.payments.insert({
    user_id: ctx.from.id,
    payload: invoice_payload,
    charge_id: telegram_payment_charge_id, // save for refunds
    stars: total_amount,
    created_at: new Date(),
  });

  await activateProduct(invoice_payload, ctx.from.id);
  await ctx.reply('Payment received! Your access is now active.');
});
```

## Issuing a Refund

```python
# python-telegram-bot — refund a Stars payment
await context.bot.refund_star_payment(
    user_id=user_id,
    telegram_payment_charge_id="saved_charge_id_from_successful_payment"
)
```

```js
// Node.js — direct Bot API call
await fetch(`https://api.telegram.org/bot${process.env.BOT_TOKEN}/refundStarPayment`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    user_id,
    telegram_payment_charge_id: chargeId,
  }),
});
```

## Testing Payments

- Use the Telegram test server (5-click trick on the version number in desktop Settings)
- Create a test bot with BotFather inside the test environment
- Test Stars payments do not charge real Stars
- Always test all four paths: successful payment, user cancellation, `pre_checkout_query` rejection, timeout handling
- Do NOT use production bot tokens for payment testing — test and production environments are completely separate

## answerWebAppQuery (Inline-Button Mode)

If your Mini App was opened via an inline keyboard button, it has a `query_id` in `tg.initDataUnsafe`. Use `answerWebAppQuery` to send a result message back to the chat after the user completes an action.

```js
// Frontend: pass query_id to backend when action is complete
const queryId = tg.initDataUnsafe.query_id; // only present in inline-button mode
if (queryId) {
  await callApi('/api/answer-query', { queryId, result: 'Order placed!' });
  tg.close(); // close after answering
}
```

```python
# Backend
from telegram import InlineQueryResultArticle, InputTextMessageContent

async def answer_query(query_id: str, text: str, bot):
    await bot.answer_web_app_query(
        web_app_query_id=query_id,
        result=InlineQueryResultArticle(
            id='1',
            title='Order Placed',
            input_message_content=InputTextMessageContent(text)
        )
    )
```

Copy-ready implementations are in `assets/answer-web-app-query.js` and `assets/answer_web_app_query.py`.

## Gotchas

- **10-second pre_checkout deadline** — always add a timeout (8s recommended) in your validation logic; the transaction auto-cancels if you don't answer in time
- **Stars only allow one price item** — no line-item breakdown
- **`paid` in the frontend callback is not authoritative** — always confirm via the `successful_payment` update on the backend
- **Save `telegram_payment_charge_id`** — you need it for `refundStarPayment`; you cannot retrieve it later
- **Test environment is completely separate** — never mix test and production bot tokens
- **Invoice links expire** — generate a fresh one for each purchase attempt; do not cache and reuse
- **`provider_token` should be an empty string or omitted** for Stars — do not pass a payment provider token
