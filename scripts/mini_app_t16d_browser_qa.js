/* Voluntary trial on a temporary DB and synthetic Telegram SDK only. */
const { chromium } = require('playwright');
const crypto = require('crypto');
const fs = require('fs');
const path = require('path');

const target = process.env.MINI_APP_QA_URL;
const screenshotDir = process.env.MINI_APP_QA_SCREENSHOTS;
if (!target || !screenshotDir) throw new Error('QA URL and screenshot directory required');
function signed(id) {
  const fields = { auth_date: String(Math.floor(Date.now() / 1000)), user: JSON.stringify({ id, first_name: 'Test' }) };
  const data = Object.entries(fields).sort(([a], [b]) => a.localeCompare(b)).map(([key, value]) => `${key}=${value}`).join('\n');
  const secret = crypto.createHmac('sha256', 'WebAppData').update('123456:test-telegram-token').digest();
  fields.hash = crypto.createHmac('sha256', secret).update(data).digest('hex');
  return new URLSearchParams(fields).toString();
}
async function openApp(browser, id) {
  const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
  page.setDefaultTimeout(8000);
  await page.route('https://telegram.org/js/telegram-web-app.js*', (route) => route.fulfill({ status: 200, contentType: 'application/javascript', body: '' }));
  await page.addInitScript((initData) => {
    const events = new Map(); window.__events = events;
    window.Telegram = { WebApp: {
      initData, colorScheme: 'light', ready() {}, expand() {}, requestFullscreen() {},
      onEvent(name, fn) { events.set(name, fn); }, offEvent(name) { events.delete(name); },
      BackButton: { show() {}, hide() {}, onClick(fn) { window.__back = fn; }, offClick() { window.__back = null; } },
    } };
  }, signed(id));
  await page.goto(target, { waitUntil: 'networkidle' });
  await page.locator('#tab-bar button').nth(2).click();
  await page.getByRole('button', { name: 'Доступ и история' }).click();
  return page;
}
async function noOverflow(page) {
  if (await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)) throw new Error('Horizontal overflow');
}
(async () => {
  fs.mkdirSync(screenshotDir, { recursive: true });
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await openApp(browser, 504);
    await page.getByRole('button', { name: 'Попробовать 7 дней' }).waitFor();
    await page.getByText('Сейчас без Plus').first().waitFor();
    let releaseState;
    const stateGate = new Promise((resolve) => { releaseState = resolve; });
    await page.route('**/app/api/subscription/state', async (route) => { await stateGate; await route.continue(); });
    await page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));
    await page.getByRole('button', { name: 'Попробовать 7 дней', disabled: true }).waitFor();
    releaseState();
    await page.unroute('**/app/api/subscription/state');
    await page.getByRole('button', { name: 'Попробовать 7 дней', disabled: false }).waitFor();
    await noOverflow(page);
    await page.screenshot({ path: path.join(screenshotDir, 't16d-390-trial-available-light.png'), fullPage: true });
    const before = await page.request.post(new URL('/app/api/subscription/state', target).href, {
      data: { init_data: signed(504) },
    });
    if ((await before.json()).viewer.active) throw new Error('Trial auto activated before click');
    await page.getByRole('button', { name: 'Попробовать 7 дней' }).click();
    await page.getByText('Ознакомление на 7 дней включено. Деньги не списываются и продления нет.').waitFor();
    if (await page.getByRole('button', { name: 'Попробовать 7 дней' }).count()) throw new Error('Trial can start twice');
    const after = await page.request.post(new URL('/app/api/subscription/state', target).href, {
      data: { init_data: signed(504) },
    });
    const state = await after.json();
    if (!state.viewer.active || !state.viewer.test_trial_used || state.money_charged) throw new Error('Trial state mismatch');
    const repeated = await page.request.post(new URL('/app/api/subscription/test-trial', target).href, {
      data: { init_data: signed(504) },
    });
    const same = await repeated.json();
    if (same.expires_at !== state.viewer.test_trial_expires_at || same.started_now) throw new Error('Replay extended trial');
    await page.setViewportSize({ width: 360, height: 780 });
    await page.evaluate(() => { window.Telegram.WebApp.colorScheme = 'dark'; window.__events.get('themeChanged')?.(); });
    await noOverflow(page);
    await page.screenshot({ path: path.join(screenshotDir, 't16d-360-trial-active-dark.png'), fullPage: true });
    await page.route('**/app/api/subscription/state', async (route) => {
      const response = await route.fetch();
      const body = await response.json();
      body.viewer.test_trial_active = false;
      body.viewer.active = true;
      await route.fulfill({ response, json: body });
    });
    await page.getByRole('button', { name: 'Обновить статус' }).click();
    await page.getByText('Тестовое ознакомление использовано. Бесплатные возможности доступны.').waitFor();
    await page.unroute('**/app/api/subscription/state');
    await page.evaluate(() => window.__back());
    await page.getByRole('heading', { name: 'Профиль' }).waitFor();
    const excluded = await openApp(browser, 502);
    if (await excluded.getByRole('button', { name: 'Попробовать 7 дней' }).count()) throw new Error('Non-allowlisted trial visible');
    console.log(JSON.stringify({ result: 'PASS', optIn: true, sevenDaysOnce: true, replay: true, allowlist: true, back: true, screenshots: 2 }));
  } finally { await browser.close(); }
})().catch((error) => { console.error(error.stack || error.message); process.exitCode = 1; });
