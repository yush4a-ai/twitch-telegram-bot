/* Local reminder journey: temp DB, fake Telegram SDK, no external message. */
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
async function noOverflow(page) {
  if (await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)) throw new Error('Horizontal overflow');
}
(async () => {
  fs.mkdirSync(screenshotDir, { recursive: true });
  const browser = await chromium.launch({ headless: true });
  try {
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
    }, signed(501));
    await page.goto(target, { waitUntil: 'networkidle' });
    await page.locator('#tab-bar button').nth(1).click();
    await page.getByRole('button', { name: 'Настройки Alpha' }).click();
    await page.getByRole('heading', { name: 'Напоминание об эфире' }).scrollIntoViewIfNeeded();
    await page.getByRole('button', { name: 'Через 15 минут' }).click();
    await page.getByText(/Запланировано через 15 минут/).waitFor();
    await noOverflow(page);
    await page.screenshot({ path: path.join(screenshotDir, 't15-390-reminder-light.png'), fullPage: true });

    await page.route('**/app/api/viewer/reminder', (route) => route.abort());
    await page.getByRole('button', { name: 'Через 30 минут' }).click();
    await page.getByText('Не удалось сохранить напоминание. Попробуйте ещё раз.').waitFor();
    await page.getByText(/Запланировано через 15 минут/).waitFor();
    await page.unroute('**/app/api/viewer/reminder');
    await page.getByRole('button', { name: 'Через 30 минут' }).click();
    await page.getByText(/Запланировано через 30 минут/).waitFor();
    await page.setViewportSize({ width: 360, height: 780 });
    await page.evaluate(() => { window.Telegram.WebApp.colorScheme = 'dark'; window.__events.get('themeChanged')?.(); });
    await noOverflow(page);
    await page.screenshot({ path: path.join(screenshotDir, 't15-360-reminder-dark.png'), fullPage: true });
    await page.getByRole('button', { name: 'Отменить напоминание' }).click();
    await page.getByText('Напоминание отменено.').waitFor();
    await page.evaluate(() => window.__back());
    await page.getByRole('button', { name: 'Настройки Alpha' }).click();
    await page.getByText('Напоминание отменено.').waitFor();
    console.log(JSON.stringify({ result: 'PASS', schedule: true, reschedule: true, cancel: true, networkRollback: true, back: true, screenshots: 2 }));
  } finally { await browser.close(); }
})().catch((error) => { console.error(error.stack || error.message); process.exitCode = 1; });
