/* Local temporary DB, fake Telegram SDK and fake community callback. No messages sent. */
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

async function openApp(browser, id, width = 390, height = 844) {
  const page = await browser.newPage({ viewport: { width, height } });
  page.setDefaultTimeout(7000);
  await page.route('https://telegram.org/js/telegram-web-app.js*', (route) => route.fulfill({ status: 200, contentType: 'application/javascript', body: '' }));
  await page.addInitScript((initData) => {
    const events = new Map(); window.__events = events;
    window.Telegram = { WebApp: {
      initData, colorScheme: 'light', ready() {}, expand() {}, requestFullscreen() {},
      requestChat(_id, callback) { callback(true); },
      openTelegramLink() {}, openLink() {},
      onEvent(name, fn) { events.set(name, fn); }, offEvent(name) { events.delete(name); },
      BackButton: { show() {}, hide() {}, onClick() {}, offClick() {} },
    } };
  }, signed(id));
  await page.goto(target, { waitUntil: 'networkidle' });
  await page.getByRole('button', { name: 'Стример', exact: true }).click();
  return page;
}

async function assertNoOverflow(page, label) {
  if (await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)) {
    throw new Error(`Horizontal overflow: ${label}`);
  }
}

(async () => {
  fs.mkdirSync(screenshotDir, { recursive: true });
  const browser = await chromium.launch({ headless: true });
  try {
    const owner = await openApp(browser, 601);
    await owner.getByText('Twitch подключён').waitFor();
    await owner.getByText('Пока нет подключённых сообществ.').waitFor();
    await owner.getByRole('button', { name: 'Выбрать группу' }).click();
    await owner.getByText('Выбор отправлен боту.').waitFor();
    const intentId = await owner.evaluate(() => localStorage.getItem('ts-streamer-community-intent'));
    if (!intentId) throw new Error('Community intent was not persisted for app return');
    const completed = await owner.request.post(new URL('/_qa/complete-community', target).href, { data: { intent_id: intentId } });
    if (!(await completed.json()).connected) throw new Error('Fake Telegram callback did not connect community');
    await owner.reload({ waitUntil: 'networkidle' });
    await owner.getByText('Тестовое сообщество').waitFor();
    await owner.getByText('Публикации выключены').waitFor();
    await owner.getByRole('button', { name: 'Включить публикации' }).click();
    await owner.getByText('Публикации включены', { exact: true }).waitFor();
    await assertNoOverflow(owner, '390 light');
    await owner.screenshot({ path: path.join(screenshotDir, 't9-390-community-light.png'), fullPage: true });
    let cancelledIntent = '';
    owner.on('request', (request) => {
      if (request.url().endsWith('/app/api/streamer/community-intent/cancel')) {
        cancelledIntent = request.postDataJSON().intent_id;
      }
    });
    await owner.evaluate(() => { window.Telegram.WebApp.requestChat = (_id, callback) => callback(false); });
    await owner.getByRole('button', { name: 'Выбрать канал' }).click();
    await owner.getByText('Выбор отменён. Сообщество не подключено.').waitFor();
    if (!cancelledIntent || await owner.evaluate(() => localStorage.getItem('ts-streamer-community-intent'))) {
      throw new Error('Cancelled chat selection remained pending in UI');
    }
    const late = await owner.request.post(new URL('/_qa/complete-community', target).href, { data: { intent_id: cancelledIntent } });
    if ((await late.json()).connected) throw new Error('Late callback connected cancelled community');
    await owner.locator('#tab-bar button').nth(1).click();
    await owner.getByText('Это локальный пример.').waitFor();
    await owner.screenshot({ path: path.join(screenshotDir, 't9-390-posts-light.png'), fullPage: true });
    await owner.locator('#tab-bar button').nth(2).click();
    await owner.getByText('Обычный пост и подключение сообщества доступны бесплатно.').waitFor();
    await owner.setViewportSize({ width: 360, height: 780 });
    await owner.evaluate(() => { window.Telegram.WebApp.colorScheme = 'dark'; window.__events.get('themeChanged')?.(); });
    await assertNoOverflow(owner, '360 dark');
    await owner.screenshot({ path: path.join(screenshotDir, 't9-360-profile-dark.png'), fullPage: true });
    await owner.setViewportSize({ width: 768, height: 900 });
    await assertNoOverflow(owner, '768 dark');
    await owner.screenshot({ path: path.join(screenshotDir, 't9-768-profile-dark.png'), fullPage: true });
    await owner.setViewportSize({ width: 1440, height: 900 });
    await assertNoOverflow(owner, '1440 dark');
    await owner.screenshot({ path: path.join(screenshotDir, 't9-1440-profile-dark.png'), fullPage: true });
    await owner.close();

    const unlinked = await openApp(browser, 602);
    await unlinked.getByRole('heading', { name: 'Подключите Twitch' }).waitFor();
    await unlinked.getByRole('button', { name: 'Подключить Twitch' }).click();
    await unlinked.getByText('Подключение Twitch сейчас недоступно.').waitFor();
    await unlinked.screenshot({ path: path.join(screenshotDir, 't9-390-unlinked-error.png'), fullPage: true });
    await unlinked.close();
    console.log(JSON.stringify({ result: 'PASS', communityConnected: true, cancelledCallbackRejected: true, freePublishing: true, disconnectedError: true, screenshots: 6 }));
  } finally { await browser.close(); }
})().catch((error) => { console.error(error.stack || error.message); process.exitCode = 1; });
