/* Free viewer journey against isolated local DB and fake Twitch, no sends. */
const { chromium } = require('playwright');
const crypto = require('crypto');
const fs = require('fs');
const path = require('path');

const target = process.env.MINI_APP_QA_URL;
const screenshotDir = process.env.MINI_APP_QA_SCREENSHOTS;
if (!target || !screenshotDir) throw new Error('Mini App browser fixture URL and screenshots path are required');
const fields = { auth_date: String(Math.floor(Date.now() / 1000)), user: JSON.stringify({ id: Date.now(), first_name: 'Test' }) };
const data = Object.entries(fields).sort(([a], [b]) => a.localeCompare(b)).map(([k, v]) => `${k}=${v}`).join('\n');
const secret = crypto.createHmac('sha256', 'WebAppData').update('123456:test-telegram-token').digest();
fields.hash = crypto.createHmac('sha256', secret).update(data).digest('hex');
const signed = new URLSearchParams(fields).toString();

(async () => {
  fs.mkdirSync(screenshotDir, { recursive: true });
  const browser = await chromium.launch({ headless: false });
  const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  page.on('dialog', (dialog) => dialog.accept());
  await page.route('https://telegram.org/js/telegram-web-app.js*', (route) => route.fulfill({ status: 200, contentType: 'application/javascript', body: '' }));
  await page.addInitScript((initData) => {
    const events = new Map();
    window.__viewerEvents = events;
    window.__writeAccessRequests = 0;
    window.Telegram = { WebApp: {
      initData, colorScheme: 'light', ready() {}, expand() {}, requestFullscreen() {},
      requestWriteAccess(callback) { window.__writeAccessRequests++; callback(true); },
      onEvent(name, handler) { events.set(name, handler); }, offEvent(name) { events.delete(name); },
      BackButton: { show() {}, hide() {}, onClick() {}, offClick() {} },
    } };
  }, signed);
  try {
    await page.goto(target, { waitUntil: 'networkidle' });
    await page.getByRole('button', { name: 'Найти стримера' }).click();
    const search = page.getByRole('searchbox', { name: 'Ник или ссылка Twitch' });
    await search.fill('https://twitch.tv/alpha');
    await page.getByRole('button', { name: 'Добавить Alpha' }).click();
    await page.getByText('Alpha', { exact: true }).waitFor();
    if (await page.evaluate(() => window.__writeAccessRequests) !== 1) throw new Error('Write access was not requested on follow action');
    await page.screenshot({ path: path.join(screenshotDir, 't5-390-followed-light.png') });
    await page.evaluate(() => { window.Telegram.WebApp.colorScheme = 'dark'; window.__viewerEvents.get('themeChanged')?.(); });
    await page.waitForTimeout(220);
    await page.screenshot({ path: path.join(screenshotDir, 't5-390-followed-dark.png') });
    await page.evaluate(() => { window.Telegram.WebApp.colorScheme = 'light'; window.__viewerEvents.get('themeChanged')?.(); });
    await page.setViewportSize({ width: 1440, height: 844 });
    await page.waitForTimeout(220);
    if (await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)) throw new Error('Viewer list overflows 1440px');
    await page.screenshot({ path: path.join(screenshotDir, 't5-1440-followed-light.png') });
    await page.setViewportSize({ width: 390, height: 844 });
    const retrySearch = page.getByRole('searchbox', { name: 'Ник или ссылка Twitch' });
    await page.route('**/app/api/viewer/search', async (route) => {
      await new Promise((resolve) => setTimeout(resolve, 700));
      try { await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ results: [{ login: 'beta', display_name: 'Beta', is_live: false }] }) }); } catch {}
    });
    await retrySearch.fill('beta');
    await page.waitForTimeout(400);
    await retrySearch.fill('b');
    await page.waitForTimeout(800);
    if (await page.getByRole('button', { name: 'Добавить Beta' }).count()) throw new Error('Cancelled search rendered stale result');
    await page.unroute('**/app/api/viewer/search');
    await page.route('**/app/api/viewer/search', (route) => route.abort());
    await retrySearch.fill('beta');
    await page.getByText('Поиск пока недоступен. Проверьте ник и попробуйте ещё раз.').waitFor();
    if (await retrySearch.inputValue() !== 'beta') throw new Error('Search draft was lost on network failure');
    await page.unroute('**/app/api/viewer/search');
    await retrySearch.fill('');
    await retrySearch.fill('beta');
    await page.getByRole('button', { name: /Добавить Очень длинное русское имя/ }).waitFor();
    await page.setViewportSize({ width: 360, height: 780 });
    if (await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)) throw new Error('Long Russian name overflows 360px');
    await page.screenshot({ path: path.join(screenshotDir, 't5-360-long-name-light.png') });
    await retrySearch.fill('');
    await page.getByRole('button', { name: 'Настройки Alpha' }).click();
    const notifications = page.getByRole('checkbox', { name: 'Уведомлять об эфирах' });
    await page.route('**/app/api/viewer/notify', (route) => route.abort());
    await notifications.uncheck();
    await page.getByText('Не удалось сохранить. Попробуйте ещё раз.').waitFor();
    if (!await notifications.isChecked()) throw new Error('Notification toggle did not roll back');
    await page.unroute('**/app/api/viewer/notify');
    await notifications.uncheck();
    await page.getByText('Уведомления выключены').waitFor();
    await page.getByRole('button', { name: 'Удалить подписку' }).click();
    await page.getByText('Добавьте первого стримера').waitFor();
    if (errors.length) throw new Error(errors.join(' | '));
    console.log(JSON.stringify({ result: 'PASS', journey: 'search-follow-pause-unfollow', networkDraft: true, toggleRollback: true, screenshots: 4 }));
  } finally { await browser.close(); }
})().catch((error) => { console.error(error.stack || error.message); process.exitCode = 1; });
