/* Viewer Free profile and Plus filter on an isolated local fixture. */
const { chromium } = require('playwright');
const crypto = require('crypto');
const fs = require('fs');
const path = require('path');

const target = process.env.MINI_APP_QA_URL;
const screenshotDir = process.env.MINI_APP_QA_SCREENSHOTS;
if (!target || !screenshotDir) throw new Error('Mini App QA URL and screenshots path are required');
function signed(userId) {
  const fields = { auth_date: String(Math.floor(Date.now() / 1000)), user: JSON.stringify({ id: userId, first_name: 'Test' }) };
  const data = Object.entries(fields).sort(([a], [b]) => a.localeCompare(b)).map(([k, v]) => `${k}=${v}`).join('\n');
  const secret = crypto.createHmac('sha256', 'WebAppData').update('123456:test-telegram-token').digest();
  fields.hash = crypto.createHmac('sha256', secret).update(data).digest('hex');
  return new URLSearchParams(fields).toString();
}
async function openApp(browser, id) {
  const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
  page.setDefaultTimeout(5000);
  await page.route('https://telegram.org/js/telegram-web-app.js*', (route) => route.fulfill({ status: 200, contentType: 'application/javascript', body: '' }));
  await page.addInitScript((initData) => {
    const backs = new Set();
    const events = new Map();
    window.__backs = backs;
    window.__events = events;
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
  const browser = await chromium.launch({ headless: false });
  try {
    const free = await openApp(browser, Date.now());
    await free.locator('#tab-bar button').nth(2).click();
    await free.getByLabel('Начало тихих часов').fill('23:00');
    await free.getByLabel('Конец тихих часов').fill('08:00');
    await free.getByRole('button', { name: 'Сохранить тихие часы' }).click();
    await free.getByRole('checkbox', { name: 'Сводка после тихих часов' }).check();
    await free.getByText('Сводка включена').waitFor();
    await free.evaluate(() => scrollTo(0, 0));
    await free.screenshot({ path: path.join(screenshotDir, 't6-390-free-profile.png'), fullPage: true });
    await free.locator('#tab-bar button').nth(1).click();
    await free.getByRole('searchbox', { name: 'Ник или ссылка Twitch' }).fill('alpha');
    await free.getByRole('button', { name: 'Добавить Alpha' }).click();
    await free.getByRole('button', { name: 'Настройки Alpha' }).click();
    await free.getByText('Фильтр эфиров · Viewer Plus').waitFor();
    if (await free.getByRole('button', { name: 'Настроить фильтр' }).count()) throw new Error('Free UI exposed Plus form');
    await free.close();

    const plus = await openApp(browser, 501);
    await plus.locator('#tab-bar button').nth(1).click();
    await plus.getByRole('button', { name: 'Настройки Alpha' }).click();
    await plus.getByRole('button', { name: 'Настроить фильтр' }).click();
    for (const term of ['Minecraft', 'speedrun']) {
      const remove = plus.getByRole('button', { name: new RegExp(`Убрать ${term}`) });
      if (await remove.count()) await remove.click();
    }
    await plus.getByLabel('Категория').fill('Minecraft');
    await plus.getByRole('button', { name: 'Добавить категорию' }).click();
    await plus.getByLabel('Слова в названии').fill('speedrun');
    await plus.getByRole('button', { name: 'Добавить слово' }).click();
    await plus.getByRole('button', { name: 'Сохранить фильтр' }).click();
    await plus.getByText('Фильтр сохранён').waitFor();
    await plus.evaluate(() => scrollTo(0, 0));
    await plus.screenshot({ path: path.join(screenshotDir, 't6-390-plus-filter.png'), fullPage: true });
    await plus.evaluate(() => { window.Telegram.WebApp.colorScheme = 'dark'; window.__events.get('themeChanged')?.(); });
    await plus.waitForTimeout(220);
    await plus.screenshot({ path: path.join(screenshotDir, 't6-390-plus-filter-dark.png'), fullPage: true });
    await plus.evaluate(() => { window.Telegram.WebApp.colorScheme = 'light'; window.__events.get('themeChanged')?.(); });
    await plus.setViewportSize({ width: 360, height: 780 });
    await plus.waitForTimeout(220);
    if (await plus.evaluate(() => document.documentElement.scrollWidth > innerWidth)) throw new Error('Plus filter overflows 360px');
    await plus.screenshot({ path: path.join(screenshotDir, 't6-360-plus-filter.png'), fullPage: true });
    await plus.getByRole('button', { name: 'Сохранить фильтр' }).click();
    await plus.getByText('Фильтр сохранён').waitFor();
    await plus.evaluate(() => [...window.__backs][0]());
    await plus.getByRole('heading', { name: 'Alpha' }).waitFor();
    if (await plus.evaluate(() => window.__backs.size) !== 1) throw new Error('BackButton handler duplicated');
    await plus.getByRole('button', { name: 'Настроить фильтр' }).click();
    await plus.getByRole('button', { name: /Убрать Minecraft/ }).waitFor();
    await plus.close();
    console.log(JSON.stringify({ result: 'PASS', freeProfile: true, plusFilter: true, contextRestored: true, screenshots: 4 }));
  } finally { await browser.close(); }
})().catch((error) => { console.error(error.stack || error.message); process.exitCode = 1; });
