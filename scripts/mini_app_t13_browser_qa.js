/* Five offline selections, explicit sixth replacement and Free photo on temp DB. */
const { chromium } = require('playwright');
const crypto = require('crypto');
const fs = require('fs');
const path = require('path');

const target = process.env.MINI_APP_QA_URL;
const screenshotDir = process.env.MINI_APP_QA_SCREENSHOTS;
if (!target || !screenshotDir) throw new Error('QA URL and screenshots path required');
function signed(userId) {
  const fields = { auth_date: String(Math.floor(Date.now() / 1000)), user: JSON.stringify({ id: userId, first_name: 'Test' }) };
  const data = Object.entries(fields).sort(([a], [b]) => a.localeCompare(b)).map(([k, v]) => `${k}=${v}`).join('\n');
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
  const browser = await chromium.launch({ headless: false });
  try {
    const plus = await openApp(browser, 501);
    const initialSelection = await plus.evaluate(async () => {
      const response = await fetch('/app/api/viewer/state', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ init_data: window.Telegram.WebApp.initData }),
      });
      return (await response.json()).video_selection.selected_logins;
    });
    if (initialSelection.length) throw new Error('T13 QA requires a fresh temporary browser fixture');
    await plus.locator('#tab-bar button').nth(1).click();
    for (const login of ['Alpha', 'Beta', 'Gamma', 'Delta', 'Epsilon']) {
      await plus.getByRole('button', { name: `Настройки ${login}` }).click();
      await plus.getByRole('button', { name: 'Выбрать видео' }).click();
      await plus.getByRole('button', { name: 'Выключить видео' }).waitFor();
      await plus.evaluate(() => [...window.__backs][0]());
    }
    await plus.getByText('Видеопревью: 5 из 5').waitFor();
    await plus.screenshot({ path: path.join(screenshotDir, 't13-390-five-selected.png'), fullPage: true });
    await plus.getByRole('button', { name: 'Настройки Zeta' }).click();
    await plus.getByText('Все пять мест заняты. Выберите, кого заменить.').waitFor();
    await plus.screenshot({ path: path.join(screenshotDir, 't13-390-sixth-choice.png'), fullPage: true });
    for (const width of [768, 1440]) {
      await plus.setViewportSize({ width, height: 900 });
      if (await plus.evaluate(() => document.documentElement.scrollWidth > innerWidth)) throw new Error(`T13 overflow at ${width}`);
      await plus.screenshot({ path: path.join(screenshotDir, `t13-${width}-sixth-choice.png`), fullPage: true });
    }
    await plus.setViewportSize({ width: 390, height: 844 });
    await plus.getByRole('button', { name: 'Заменить Alpha' }).click();
    await plus.getByRole('button', { name: 'Выключить видео' }).waitFor();
    await plus.setViewportSize({ width: 360, height: 780 });
    if (await plus.evaluate(() => document.documentElement.scrollWidth > innerWidth)) throw new Error('T13 overflow at 360');
    await plus.screenshot({ path: path.join(screenshotDir, 't13-360-replaced.png'), fullPage: true });
    await plus.evaluate(() => { window.Telegram.WebApp.colorScheme = 'dark'; window.__events.get('themeChanged')?.(); });
    await plus.waitForTimeout(200);
    await plus.screenshot({ path: path.join(screenshotDir, 't13-360-replaced-dark.png'), fullPage: true });
    await plus.evaluate(() => [...window.__backs][0]());
    await plus.getByRole('button', { name: 'Настройки Alpha' }).click();
    await plus.getByText('Сейчас используется фото.').waitFor();
    if (await plus.evaluate(() => window.__backs.size) !== 1) throw new Error('BackButton duplicated');
    await plus.evaluate(() => [...window.__backs][0]());
    await plus.getByRole('button', { name: 'Настройки Zeta' }).click();
    plus.once('dialog', (dialog) => dialog.accept());
    await plus.getByRole('button', { name: 'Удалить подписку' }).click();
    await plus.getByText('Видеопревью: 4 из 5').waitFor();
    await plus.setViewportSize({ width: 390, height: 844 });
    await plus.evaluate(() => { window.Telegram.WebApp.colorScheme = 'light'; window.__events.get('themeChanged')?.(); });
    await plus.screenshot({ path: path.join(screenshotDir, 't13-390-slot-freed.png'), fullPage: true });
    await plus.close();

    const free = await openApp(browser, Date.now());
    await free.locator('#tab-bar button').nth(1).click();
    await free.getByRole('searchbox', { name: 'Ник или ссылка Twitch' }).fill('alpha');
    await free.getByRole('button', { name: 'Добавить Alpha' }).click();
    await free.getByRole('button', { name: 'Настройки Alpha' }).click();
    await free.getByText('Фото остаётся по умолчанию.').waitFor();
    if (await free.getByRole('button', { name: 'Выбрать видео' }).count()) throw new Error('Free can select video');
    await free.close();

    const downgraded = await openApp(browser, 502);
    await downgraded.locator('#tab-bar button').nth(1).click();
    await downgraded.getByRole('button', { name: 'Настройки Track050' }).click();
    await downgraded.getByRole('heading', { name: 'Приостановлено по лимиту' }).waitFor();
    await downgraded.getByRole('button', { name: 'Включить в активные 50' }).click();
    await downgraded.getByRole('heading', { name: 'Приостановлено по лимиту' }).waitFor({ state: 'detached' });
    await downgraded.screenshot({ path: path.join(screenshotDir, 't13-390-active-fifty.png'), fullPage: true });
    await downgraded.evaluate(() => [...window.__backs][0]());
    await downgraded.getByRole('button', { name: 'Настройки Track049' }).click();
    await downgraded.getByRole('heading', { name: 'Приостановлено по лимиту' }).waitFor();
    await downgraded.close();
    console.log(JSON.stringify({ result: 'PASS', selections: 5, replacement: 'Alpha→Zeta', unfollowFreedSlot: true, freePhoto: true, activeFifty: true, screenshots: 8 }));
  } finally { await browser.close(); }
})().catch((error) => { console.error(error.stack || error.message); process.exitCode = 1; });
