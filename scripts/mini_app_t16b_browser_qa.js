/* Personal history on a temporary DB and synthetic Telegram SDK only. */
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

async function openViewer(browser, id, viewport, theme) {
  const page = await browser.newPage({ viewport });
  page.setDefaultTimeout(8000);
  await page.route('https://telegram.org/js/telegram-web-app.js*', (route) => route.fulfill({ status: 200, contentType: 'application/javascript', body: '' }));
  await page.addInitScript(({ initData, colorScheme }) => {
    const events = new Map(); window.__events = events;
    window.Telegram = { WebApp: {
      initData, colorScheme, ready() {}, expand() {}, requestFullscreen() {},
      onEvent(name, fn) { events.set(name, fn); }, offEvent(name) { events.delete(name); },
      BackButton: { show() {}, hide() {}, onClick(fn) { window.__back = fn; }, offClick() { window.__back = null; } },
    } };
  }, { initData: signed(id), colorScheme: theme });
  await page.goto(target, { waitUntil: 'networkidle' });
  await page.locator('#tab-bar button').nth(2).click();
  await page.getByRole('heading', { name: 'Профиль' }).waitFor();
  return page;
}

async function noOverflow(page) {
  if (await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)) throw new Error('Horizontal overflow');
}

(async () => {
  fs.mkdirSync(screenshotDir, { recursive: true });
  const browser = await chromium.launch({ headless: true });
  try {
    const own = await openViewer(browser, 501, { width: 390, height: 844 }, 'light');
    await own.getByRole('button', { name: 'Открыть историю' }).click();
    await own.getByRole('heading', { name: 'Личная история' }).waitFor();
    await own.getByText('Оповещение об эфире · Alpha').waitFor();
    await own.getByText(/Отправлено/).waitFor();
    await noOverflow(own);
    await own.screenshot({ path: path.join(screenshotDir, 't16b-390-history-sent-light.png'), fullPage: true });
    await own.evaluate(() => window.__back());
    await own.getByRole('heading', { name: 'Профиль' }).waitFor();
    await own.route('**/app/api/viewer/history', (route) => route.fulfill({
      status: 403, contentType: 'application/json', body: '{"error":"unauthorized"}',
    }));
    await own.getByRole('button', { name: 'Открыть историю' }).click();
    await own.getByText('Сессия Telegram устарела. Закройте и откройте приложение снова.').waitFor();
    await own.unroute('**/app/api/viewer/history');

    const empty = await openViewer(browser, 503, { width: 360, height: 780 }, 'dark');
    await empty.getByRole('button', { name: 'Открыть историю' }).click();
    await empty.getByText('Пока нет событий', { exact: true }).waitFor();
    if (await empty.getByText('Alpha').count()) throw new Error('Other viewer history leaked');
    await noOverflow(empty);
    await empty.screenshot({ path: path.join(screenshotDir, 't16b-360-history-empty-dark.png'), fullPage: true });

    const free = await openViewer(browser, 502, { width: 390, height: 844 }, 'light');
    await free.getByText('Обычные оповещения остаются бесплатными.').waitFor();
    if (await free.getByRole('button', { name: 'Открыть историю' }).count()) throw new Error('Free history button visible');
    console.log(JSON.stringify({ result: 'PASS', ownSent: true, emptyPrivate: true, freeGated: true, back: true, screenshots: 2 }));
  } finally { await browser.close(); }
})().catch((error) => { console.error(error.stack || error.message); process.exitCode = 1; });
