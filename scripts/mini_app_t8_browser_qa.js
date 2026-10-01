/* Separate category opt-in on a local temporary DB; Telegram SDK is synthetic. */
const { chromium } = require('playwright');
const crypto = require('crypto');
const fs = require('fs');
const path = require('path');

const target = process.env.MINI_APP_QA_URL;
const screenshotDir = process.env.MINI_APP_QA_SCREENSHOTS;
if (!target || !screenshotDir) throw new Error('QA URL and screenshot directory required');
function signed(userId) {
  const fields = { auth_date: String(Math.floor(Date.now() / 1000)), user: JSON.stringify({ id: userId, first_name: 'Test' }) };
  const data = Object.entries(fields).sort(([a], [b]) => a.localeCompare(b)).map(([key, value]) => `${key}=${value}`).join('\n');
  const secret = crypto.createHmac('sha256', 'WebAppData').update('123456:test-telegram-token').digest();
  fields.hash = crypto.createHmac('sha256', secret).update(data).digest('hex');
  return new URLSearchParams(fields).toString();
}
async function openApp(browser, id) {
  const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
  page.setDefaultTimeout(7000);
  await page.route('https://telegram.org/js/telegram-web-app.js*', (route) => route.fulfill({ status: 200, contentType: 'application/javascript', body: '' }));
  await page.addInitScript((initData) => {
    const backs = new Set(); const events = new Map();
    window.__backs = backs; window.__events = events;
    window.Telegram = { WebApp: {
      initData, colorScheme: 'light', ready() {}, expand() {}, requestFullscreen() {},
      onEvent(name, fn) { events.set(name, fn); }, offEvent(name) { events.delete(name); },
      BackButton: { show() {}, hide() {}, onClick(fn) { backs.add(fn); }, offClick(fn) { backs.delete(fn); } },
    } };
  }, signed(id));
  await page.goto(target, { waitUntil: 'networkidle' });
  return page;
}

(async () => {
  fs.mkdirSync(screenshotDir, { recursive: true });
  const browser = await chromium.launch({ headless: true });
  try {
    const plus = await openApp(browser, 501);
    await plus.locator('#tab-bar button').nth(1).click();
    await plus.getByRole('button', { name: 'Настройки Alpha' }).click();
    await plus.getByRole('heading', { name: 'Смена категории' }).waitFor();
    const toggle = plus.getByRole('checkbox', { name: 'Уведомлять о смене категории' });
    if (await toggle.isChecked()) throw new Error('Category alert should default off');
    await toggle.check();
    await plus.getByRole('searchbox', { name: 'Найти категорию Twitch' }).fill('Mine');
    await plus.getByRole('button', { name: 'Найти', exact: true }).click();
    await plus.getByRole('button', { name: 'Minecraft', exact: true }).click();
    await plus.getByRole('button', { name: 'Сохранить сигнал' }).click();
    await plus.getByText('Настройка сохранена.').waitFor();
    await plus.screenshot({ path: path.join(screenshotDir, 't8-390-selected-category.png'), fullPage: true });
    await plus.reload({ waitUntil: 'networkidle' });
    await plus.locator('#tab-bar button').nth(1).click();
    await plus.getByRole('button', { name: 'Настройки Alpha' }).click();
    if (!await plus.getByRole('checkbox', { name: 'Уведомлять о смене категории' }).isChecked()) throw new Error('Saved toggle missing');
    await plus.getByRole('button', { name: 'Убрать Minecraft' }).waitFor();
    await plus.setViewportSize({ width: 360, height: 780 });
    if (await plus.evaluate(() => document.documentElement.scrollWidth > innerWidth)) throw new Error('T8 overflow at 360px');
    await plus.screenshot({ path: path.join(screenshotDir, 't8-360-selected-category.png'), fullPage: true });
    await plus.evaluate(() => { window.Telegram.WebApp.colorScheme = 'dark'; window.__events.get('themeChanged')?.(); });
    await plus.screenshot({ path: path.join(screenshotDir, 't8-360-selected-category-dark.png'), fullPage: true });
    await plus.close();

    const free = await openApp(browser, Date.now());
    await free.locator('#tab-bar button').nth(1).click();
    await free.getByRole('searchbox', { name: 'Ник или ссылка Twitch' }).fill('alpha');
    await free.getByRole('button', { name: 'Добавить Alpha' }).click();
    await free.getByRole('button', { name: 'Настройки Alpha' }).click();
    await free.getByText('Отдельный сигнал доступен с Viewer Plus.').waitFor();
    if (await free.getByRole('checkbox', { name: 'Уведомлять о смене категории' }).count()) throw new Error('Free category toggle exposed');
    await free.screenshot({ path: path.join(screenshotDir, 't8-390-free-category.png'), fullPage: true });
    await free.close();
    console.log(JSON.stringify({ result: 'PASS', savedCategory: 'Minecraft', freeGate: true, screenshots: 4 }));
  } finally { await browser.close(); }
})().catch((error) => { console.error(error.stack || error.message); process.exitCode = 1; });
