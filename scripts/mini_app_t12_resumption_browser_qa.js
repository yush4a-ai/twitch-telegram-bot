/* Two-window video selection, stale write recovery and reload on a temporary DB. */
const { chromium } = require('playwright');
const crypto = require('crypto');
const fs = require('fs');
const path = require('path');

const target = process.env.MINI_APP_QA_URL;
const screenshotDir = process.env.MINI_APP_QA_SCREENSHOTS;
if (!target || !screenshotDir) throw new Error('QA URL and screenshots path required');

function signed(userId) {
  const fields = {
    auth_date: String(Math.floor(Date.now() / 1000)),
    user: JSON.stringify({ id: userId, first_name: 'Test' }),
  };
  const data = Object.entries(fields).sort(([a], [b]) => a.localeCompare(b))
    .map(([key, value]) => `${key}=${value}`).join('\n');
  const secret = crypto.createHmac('sha256', 'WebAppData')
    .update('123456:test-telegram-token').digest();
  fields.hash = crypto.createHmac('sha256', secret).update(data).digest('hex');
  return new URLSearchParams(fields).toString();
}

async function openApp(browser) {
  const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
  page.setDefaultTimeout(7000);
  await page.route('https://telegram.org/js/telegram-web-app.js*', (route) =>
    route.fulfill({ status: 200, contentType: 'application/javascript', body: '' }));
  await page.addInitScript((initData) => {
    const backs = new Set();
    window.__backs = backs;
    window.Telegram = { WebApp: {
      initData, colorScheme: 'light', ready() {}, expand() {}, requestFullscreen() {},
      onEvent() {}, offEvent() {},
      BackButton: {
        show() {}, hide() {},
        onClick(fn) { backs.add(fn); },
        offClick(fn) { backs.delete(fn); },
      },
    } };
  }, signed(501));
  await page.goto(target, { waitUntil: 'networkidle' });
  await page.locator('#tab-bar button').nth(1).click();
  return page;
}

async function detail(page, login) {
  await page.getByRole('button', { name: `Настройки ${login}` }).click();
}

async function back(page) {
  if (await page.evaluate(() => window.__backs.size) !== 1) {
    throw new Error('BackButton handler count changed');
  }
  await page.evaluate(() => [...window.__backs][0]());
}

(async () => {
  fs.mkdirSync(screenshotDir, { recursive: true });
  const browser = await chromium.launch({ headless: true });
  try {
    const first = await openApp(browser);
    for (const login of ['Alpha', 'Beta', 'Gamma', 'Delta']) {
      await detail(first, login);
      await first.getByRole('button', { name: 'Выбрать видео' }).click();
      await first.getByRole('button', { name: 'Выключить видео' }).waitFor();
      await back(first);
    }
    await first.getByText('Видеопревью: 4 из 5').waitFor();
    const second = await openApp(browser);
    await second.getByText('Видеопревью: 4 из 5').waitFor();
    await detail(first, 'Epsilon');
    await first.getByRole('button', { name: 'Выбрать видео' }).click();
    await first.getByRole('button', { name: 'Выключить видео' }).waitFor();

    await detail(second, 'Zeta');
    await second.getByRole('button', { name: 'Выбрать видео' }).click();
    await second.getByText('Выбор изменился в другой сессии. Проверьте список и повторите действие.').waitFor();
    await second.getByText('Все пять мест заняты. Выберите, кого заменить.').waitFor();
    await second.screenshot({
      path: path.join(screenshotDir, 't12-390-two-window-conflict.png'), fullPage: true,
    });
    await second.getByRole('button', { name: 'Заменить Alpha' }).click();
    await second.getByRole('button', { name: 'Выключить видео' }).waitFor();
    await second.reload({ waitUntil: 'networkidle' });
    await second.locator('#tab-bar button').nth(1).click();
    await detail(second, 'Zeta');
    await second.getByRole('button', { name: 'Выключить видео' }).waitFor();
    if (await second.evaluate(() => window.__backs.size) !== 1) {
      throw new Error('BackButton handler duplicated after reload');
    }
    await second.setViewportSize({ width: 360, height: 780 });
    if (await second.evaluate(() => document.documentElement.scrollWidth > innerWidth)) {
      throw new Error('Selection detail overflows at 360px');
    }
    await second.screenshot({
      path: path.join(screenshotDir, 't12-360-reloaded-selection.png'), fullPage: true,
    });
    await back(second);
    await detail(second, 'Alpha');
    await second.getByText('Сейчас используется фото.').waitFor();
    await first.close();
    await second.close();
    console.log(JSON.stringify({
      result: 'PASS', twoWindows: true, staleWriteRejected: true,
      replacement: 'Alpha→Zeta', reloadPersists: true, screenshots: 2,
    }));
  } finally {
    await browser.close();
  }
})().catch((error) => { console.error(error.stack || error.message); process.exitCode = 1; });
