/* T11 local browser journey: temporary SQLite, signed test identity, fake Telegram SDK. */
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
async function open(browser, id, width = 390, dark = false) {
  const page = await browser.newPage({ viewport: { width, height: 844 } });
  page.setDefaultTimeout(8000);
  await page.route('https://telegram.org/js/telegram-web-app.js*', (route) => route.fulfill({ status: 200, contentType: 'application/javascript', body: '' }));
  await page.addInitScript(({ initData, dark }) => {
    const events = new Map(); window.__events = events;
    window.Telegram = { WebApp: {
      initData, colorScheme: dark ? 'dark' : 'light', ready() {}, expand() {}, requestFullscreen() {},
      onEvent(name, fn) { events.set(name, fn); }, offEvent(name) { events.delete(name); },
      BackButton: {
        show() {}, hide() {},
        onClick(fn) { window.__back = fn; },
        offClick() { window.__back = null; },
      },
    } };
  }, { initData: signed(id), dark });
  await page.goto(target, { waitUntil: 'networkidle' });
  return page;
}
async function noOverflow(page) {
  if (await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)) throw new Error('Horizontal overflow');
}
async function profile(page) {
  await page.locator('#tab-bar button').nth(2).click();
  await page.getByRole('button', { name: 'Доступ и история' }).click();
  await page.getByRole('heading', { name: 'Доступ' }).waitFor();
}
(async () => {
  fs.mkdirSync(screenshotDir, { recursive: true });
  const browser = await chromium.launch({ headless: true });
  try {
    const viewer = await open(browser, 501);
    await profile(viewer);
    await viewer.getByText('Тестовый доступ', { exact: false }).first().waitFor();
    await noOverflow(viewer);
    await viewer.screenshot({ path: path.join(screenshotDir, 't11-390-viewer-light.png'), fullPage: true });
    await viewer.getByRole('button', { name: 'Создать тестовый заказ' }).first().click();
    await viewer.getByText('Ожидает подтверждения', { exact: false }).waitFor();
    await viewer.screenshot({ path: path.join(screenshotDir, 't11-390-pending-light.png'), fullPage: true });
    await viewer.getByRole('button', { name: 'Отменить', exact: true }).click();
    await viewer.locator('.subscription-order small').filter({ hasText: 'Отменён' }).waitFor();
    await viewer.getByRole('button', { name: 'Создать тестовый заказ' }).first().click();
    await viewer.getByRole('button', { name: 'Подтвердить тест' }).click();
    await viewer.getByText('Подтверждён', { exact: false }).waitFor();
    await viewer.getByRole('button', { name: 'Отозвать тестовый доступ' }).click();
    await viewer.getByText('Возврат', { exact: false }).waitFor();
    await viewer.close();

    const streamer = await open(browser, 603, 360, true);
    await streamer.getByRole('button', { name: 'Стример', exact: true }).click();
    await profile(streamer);
    await streamer.getByText('Streamer Plus', { exact: true }).first().waitFor();
    await noOverflow(streamer);
    await streamer.screenshot({ path: path.join(screenshotDir, 't11-360-streamer-dark.png'), fullPage: true });
    await streamer.setViewportSize({ width: 768, height: 900 });
    await noOverflow(streamer);
    await streamer.screenshot({ path: path.join(screenshotDir, 't11-768-streamer-dark.png'), fullPage: true });
    await streamer.close();

    const upgrade = await open(browser, 605);
    await upgrade.getByRole('button', { name: 'Стример', exact: true }).click();
    await upgrade.locator('#tab-bar button').nth(1).click();
    await upgrade.getByText('Обычный пост доступен бесплатно').waitFor();
    if (await upgrade.getByLabel('Заголовок').count()) throw new Error('Free editor visible before grant');
    await profile(upgrade);
    await upgrade.getByRole('button', { name: 'Создать тестовый заказ' }).first().click();
    await upgrade.getByRole('button', { name: 'Подтвердить тест' }).click();
    await upgrade.getByText('Подтверждён', { exact: false }).waitFor();
    await upgrade.evaluate(() => window.__back());
    await upgrade.getByRole('heading', { name: 'Профиль' }).waitFor();
    await upgrade.locator('#tab-bar button').nth(1).click();
    await upgrade.getByLabel('Заголовок').waitFor();
    await upgrade.getByLabel('Заголовок').fill('Черновик без перезапуска');
    await upgrade.locator('#tab-bar button').nth(2).click();
    await profile(upgrade);
    await upgrade.evaluate(() => window.__back());
    await upgrade.locator('#tab-bar button').nth(1).click();
    if (await upgrade.getByLabel('Заголовок').inputValue() !== 'Черновик без перезапуска') throw new Error('Unsaved draft lost after subscription');
    await noOverflow(upgrade);
    await upgrade.screenshot({ path: path.join(screenshotDir, 't11-390-unlocked-draft-light.png'), fullPage: true });
    await upgrade.close();

    const free = await open(browser, 604, 1440);
    await free.getByRole('button', { name: 'Стример', exact: true }).click();
    await profile(free);
    await free.getByText('Покупка в приложении пока недоступна.').waitFor();
    if (await free.getByRole('button', { name: 'Создать тестовый заказ' }).count()) throw new Error('Test checkout visible to non-tester');
    await free.route('**/app/api/subscription/state', (route) => route.abort());
    await free.getByRole('button', { name: 'Обновить статус' }).click();
    await free.getByText('Нет связи. Показан последний загруженный статус.').waitFor();
    await free.unroute('**/app/api/subscription/state');
    await free.getByRole('button', { name: 'Обновить статус' }).click();
    await free.getByText('Нет связи. Показан последний загруженный статус.').waitFor({ state: 'hidden' });
    await noOverflow(free);
    await free.screenshot({ path: path.join(screenshotDir, 't11-1440-free-light.png'), fullPage: true });
    await free.evaluate(() => window.__back());
    await free.getByRole('heading', { name: 'Профиль' }).waitFor();
    await free.close();
    console.log(JSON.stringify({ result: 'PASS', testOrder: true, cancel: true, confirm: true, refund: true, freeGate: true, errorRecovery: true, unlockWithoutRestart: true, draftPreserved: true, back: true, screenshots: 6 }));
  } finally { await browser.close(); }
})().catch((error) => { console.error(error.stack || error.message); process.exitCode = 1; });
