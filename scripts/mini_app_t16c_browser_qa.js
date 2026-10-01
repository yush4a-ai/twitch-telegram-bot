/* Preset journey on a temporary DB and synthetic Telegram SDK only. */
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
      showConfirm(_message, callback) { callback(true); },
      BackButton: { show() {}, hide() {}, onClick() {}, offClick() {} },
    } };
  }, signed(id));
  await page.goto(target, { waitUntil: 'networkidle' });
  await page.getByRole('button', { name: 'Стример', exact: true }).click();
  await page.locator('#tab-bar button').nth(1).click();
  return page;
}
async function noOverflow(page) {
  if (await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)) throw new Error('Horizontal overflow');
}
(async () => {
  fs.mkdirSync(screenshotDir, { recursive: true });
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await openApp(browser, 603);
    await page.getByRole('heading', { name: 'Пример · Сообщество Plus' }).waitFor();
    await page.getByLabel('Заголовок').fill('Вечер с beta');
    await page.getByLabel('Текст').fill('Заходите на эфир');
    await page.getByLabel('Название варианта').fill('Вечер');
    await page.getByRole('button', { name: 'Сохранить вариант' }).click();
    await page.getByText('Вариант сохранён. Текущее оформление и опубликованные посты не изменились.').waitFor();
    const before = await page.request.post(new URL('/app/api/streamer/template', target).href, {
      data: { init_data: signed(603), chat_id: -1003 },
    });
    if ((await before.json()).version !== 0) throw new Error('Saving preset changed active template');
    await page.getByRole('button', { name: 'Применить' }).click();
    await page.getByText('Применено ваше оформление.').waitFor();
    await page.locator('.post-preview-text').waitFor();
    if (!(await page.locator('.post-preview-text').innerText()).includes('Вечер с beta')) {
      throw new Error('Applied preset missing in local example');
    }
    await page.getByText('Публикации: последние 7 дней и предыдущие 7').waitFor();
    await noOverflow(page);
    await page.screenshot({ path: path.join(screenshotDir, 't16c-390-preset-applied-light.png'), fullPage: true });
    await page.setViewportSize({ width: 360, height: 780 });
    await page.evaluate(() => { window.Telegram.WebApp.colorScheme = 'dark'; window.__events.get('themeChanged')?.(); });
    await noOverflow(page);
    await page.screenshot({ path: path.join(screenshotDir, 't16c-360-preset-applied-dark.png'), fullPage: true });
    const concurrent = await page.request.post(new URL('/app/api/streamer/template', target).href, {
      data: { init_data: signed(603), chat_id: -1003, version: 1,
        headline: 'Другое окно', body: 'Изменено отдельно', buttons: [] },
    });
    if (concurrent.status() !== 200) throw new Error('Concurrent edit failed');
    await page.getByRole('button', { name: 'Применить' }).click();
    await page.getByText('Оформление изменилось в другом окне. Обновите версию перед применением варианта.').waitFor();
    await page.getByRole('button', { name: 'Обновить версию' }).click();
    await page.getByRole('button', { name: 'Применить' }).click();
    await page.getByText('Применено ваше оформление.').waitFor();
    const newer = await page.request.post(new URL('/app/api/streamer/template', target).href, {
      data: { init_data: signed(603), chat_id: -1003, version: 3,
        headline: 'Ещё одно окно', body: 'Изменено отдельно', buttons: [] },
    });
    if (newer.status() !== 200) throw new Error('Second concurrent edit failed');
    let releaseApply;
    const applyResponse = new Promise((resolve) => { releaseApply = resolve; });
    await page.route('**/app/api/streamer/presets/apply', async (route) => {
      await applyResponse;
      await route.fulfill({ status: 409, contentType: 'application/json',
        body: '{"error":"stale_template"}' });
    });
    await Promise.all([
      page.waitForRequest((request) => request.url().endsWith('/app/api/streamer/presets/apply')),
      page.getByRole('button', { name: 'Применить' }).click(),
    ]);
    await page.getByLabel('Сообщество').selectOption({ label: 'Второе сообщество' });
    await page.getByRole('heading', { name: 'Пример · Второе сообщество' }).waitFor();
    releaseApply();
    await page.waitForTimeout(150);
    if (await page.getByRole('button', { name: 'Обновить версию' }).count()) throw new Error('Old community conflict leaked');
    await page.unroute('**/app/api/streamer/presets/apply');
    await page.getByLabel('Сообщество').selectOption({ label: 'Сообщество Plus' });
    await page.getByRole('heading', { name: 'Пример · Сообщество Plus' }).waitFor();
    const changedAgain = await page.request.post(new URL('/app/api/streamer/template', target).href, {
      data: { init_data: signed(603), chat_id: -1003, version: 4,
        headline: 'Третье окно', body: 'Изменено отдельно', buttons: [] },
    });
    if (changedAgain.status() !== 200) throw new Error(`Third concurrent edit failed: ${changedAgain.status()} ${await changedAgain.text()}`);
    let releaseEditedConflict;
    const editedConflict = new Promise((resolve) => { releaseEditedConflict = resolve; });
    await page.route('**/app/api/streamer/presets/apply', async (route) => {
      const response = await route.fetch();
      await editedConflict;
      await route.fulfill({ response });
    });
    await Promise.all([
      page.waitForRequest((request) => request.url().endsWith('/app/api/streamer/presets/apply')),
      page.getByRole('button', { name: 'Применить' }).click(),
    ]);
    await page.getByLabel('Заголовок').fill('Черновик во время конфликта');
    releaseEditedConflict();
    await page.getByRole('button', { name: 'Обновить версию' }).waitFor();
    if (await page.getByLabel('Заголовок').inputValue() !== 'Черновик во время конфликта') {
      throw new Error('Conflict lost edited draft');
    }
    await page.getByRole('button', { name: 'Обновить версию' }).click();
    await page.unroute('**/app/api/streamer/presets/apply');
    let releaseLateSuccess;
    const lateSuccess = new Promise((resolve) => { releaseLateSuccess = resolve; });
    await page.route('**/app/api/streamer/presets/apply', async (route) => {
      const response = await route.fetch();
      await lateSuccess;
      await route.fulfill({ response });
    });
    await Promise.all([
      page.waitForRequest((request) => request.url().endsWith('/app/api/streamer/presets/apply')),
      page.getByRole('button', { name: 'Применить' }).click(),
    ]);
    await page.getByLabel('Сообщество').selectOption({ label: 'Второе сообщество' });
    await page.getByRole('heading', { name: 'Пример · Второе сообщество' }).waitFor();
    await page.getByLabel('Сообщество').selectOption({ label: 'Сообщество Plus' });
    await page.getByLabel('Заголовок').fill('Новый черновик');
    releaseLateSuccess();
    await page.waitForTimeout(150);
    if (await page.getByLabel('Заголовок').inputValue() !== 'Новый черновик') {
      throw new Error('Late apply response deleted a newer draft');
    }
    await page.unroute('**/app/api/streamer/presets/apply');
    await page.getByRole('button', { name: 'Удалить' }).click();
    await page.getByText('Вариант удалён. Текущее оформление сохранено.').waitFor();
    await page.getByText('Применено ваше оформление.').waitFor();
    let releaseCreate;
    const delayedResponse = new Promise((resolve) => { releaseCreate = resolve; });
    await page.route('**/app/api/streamer/presets/create', async (route) => {
      const response = await route.fetch();
      await delayedResponse;
      await route.fulfill({ response });
    });
    await page.getByLabel('Название варианта').fill('После обновления');
    await Promise.all([
      page.waitForRequest((request) => request.url().endsWith('/app/api/streamer/presets/create')),
      page.getByRole('button', { name: 'Сохранить вариант' }).click(),
    ]);
    await Promise.all([
      page.waitForResponse((response) => response.url().endsWith('/app/api/streamer/profile')),
      page.evaluate(() => document.dispatchEvent(new Event('visibilitychange'))),
    ]);
    releaseCreate();
    await page.getByText('Вариант сохранён. Текущее оформление и опубликованные посты не изменились.').waitFor();
    await page.getByText('После обновления', { exact: true }).waitFor();
    await page.unroute('**/app/api/streamer/presets/create');
    const free = await openApp(browser, 604);
    await free.getByText('Обычный пост доступен бесплатно').waitFor();
    if (await free.getByRole('button', { name: 'Сохранить вариант' }).count()) throw new Error('Free preset action visible');
    console.log(JSON.stringify({ result: 'PASS', inertSave: true, applied: true, conflictRecovered: true, lateConflictScoped: true, editedConflictRecovered: true, lateSuccessPreservesDraft: true, refreshRace: true, deletedKeepsTemplate: true, comparison: true, freeGate: true, screenshots: 2 }));
  } finally { await browser.close(); }
})().catch((error) => { console.error(error.stack || error.message); process.exitCode = 1; });
