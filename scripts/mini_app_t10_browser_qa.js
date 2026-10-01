/* T10 local browser journey: temporary DB, fake Telegram SDK, no outbound messages. */
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
  page.setDefaultTimeout(7000);
  await page.route('https://telegram.org/js/telegram-web-app.js*', (route) => route.fulfill({ status: 200, contentType: 'application/javascript', body: '' }));
  await page.addInitScript((initData) => {
    const events = new Map(); window.__events = events;
    window.Telegram = { WebApp: {
      initData, colorScheme: 'light', ready() {}, expand() {}, requestFullscreen() {},
      onEvent(name, fn) { events.set(name, fn); }, offEvent(name) { events.delete(name); },
      BackButton: { show() {}, hide() {}, onClick() {}, offClick() {} },
    } };
  }, signed(id));
  await page.goto(target, { waitUntil: 'networkidle' });
  await page.getByRole('button', { name: 'Стример', exact: true }).click();
  await page.locator('#tab-bar button').nth(1).click();
  return page;
}

async function noOverflow(page, label) {
  if (await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)) throw new Error(`Overflow ${label}`);
}

(async () => {
  fs.mkdirSync(screenshotDir, { recursive: true });
  const browser = await chromium.launch({ headless: true });
  try {
    const plus = await openApp(browser, 603);
    await plus.getByRole('heading', { name: 'Пример · Сообщество Plus' }).waitFor();
    await plus.getByLabel('Заголовок').fill('Сейчас у beta');
    await plus.reload({ waitUntil: 'networkidle' });
    await plus.locator('#tab-bar button').nth(1).click();
    await plus.getByLabel('Заголовок').waitFor();
    if (await plus.getByLabel('Заголовок').inputValue() !== 'Сейчас у beta') throw new Error('Template draft lost after reopen');
    await plus.getByLabel('Текст').fill('Приходите на эфир');
    await plus.getByLabel('Кнопка 1 · название').fill('Сайт');
    await plus.getByLabel('Кнопка 1 · HTTPS-адрес').fill('http://localhost/');
    await plus.getByRole('button', { name: 'Сохранить оформление' }).click();
    await plus.getByText('Проверьте длину текста и адреса кнопок').waitFor();
    await plus.getByLabel('Кнопка 1 · HTTPS-адрес').fill('https://example.com/watch');
    await plus.getByRole('button', { name: 'Сохранить оформление' }).click();
    await plus.getByText('Применено ваше оформление.').waitFor();
    await plus.getByText('Сейчас у beta', { exact: false }).waitFor();
    await plus.getByLabel('Заголовок').fill('Черновик после конфликта');
    const competing = await plus.request.post(new URL('/app/api/streamer/template', target).href, {
      data: { init_data: signed(603), chat_id: -1003, version: 1,
        headline: 'Другое окно', body: 'Изменено отдельно', buttons: [] },
    });
    if (competing.status() !== 200) throw new Error('Competing template update failed');
    await plus.getByRole('button', { name: 'Сохранить оформление' }).click();
    await plus.getByText('Оформление изменилось в другом окне.').waitFor();
    await plus.getByRole('button', { name: 'Обновить версию' }).click();
    if (await plus.getByLabel('Заголовок').inputValue() !== 'Черновик после конфликта') {
      throw new Error('Conflict recovery lost draft');
    }
    await plus.getByRole('button', { name: 'Сохранить оформление' }).click();
    await plus.getByText('Черновик после конфликта', { exact: false }).waitFor();
    await plus.locator('#tab-bar button').nth(0).click();
    await plus.getByText('Сообщество Plus').waitFor();
    if (await plus.getByRole('button', { name: 'Включить публикации' }).count()) {
      await plus.getByRole('button', { name: 'Включить публикации' }).click();
    }
    await plus.getByText('Публикации включены', { exact: true }).waitFor();
    await plus.locator('#tab-bar button').nth(1).click();
    await plus.getByLabel('Включить для этого сообщества').check();
    await plus.getByText('Живое превью включено для этого сообщества.').waitFor();
    await noOverflow(plus, '390 plus light');
    await plus.screenshot({ path: path.join(screenshotDir, 't10-390-plus-posts-light.png'), fullPage: true });
    await plus.setViewportSize({ width: 360, height: 780 });
    await plus.evaluate(() => { window.Telegram.WebApp.colorScheme = 'dark'; window.__events.get('themeChanged')?.(); });
    await noOverflow(plus, '360 plus dark');
    await plus.screenshot({ path: path.join(screenshotDir, 't10-360-plus-posts-dark.png'), fullPage: true });
    await plus.setViewportSize({ width: 768, height: 900 });
    await noOverflow(plus, '768 plus dark');
    await plus.screenshot({ path: path.join(screenshotDir, 't10-768-plus-posts-dark.png'), fullPage: true });
    await plus.close();

    const free = await openApp(browser, 604);
    await free.getByRole('heading', { name: 'Пример · Бесплатное сообщество' }).waitFor();
    await free.getByText('Обычный пост доступен бесплатно').waitFor();
    if (await free.getByLabel('Заголовок').count()) throw new Error('Free editor visible');
    if (await free.getByLabel('Включить для этого сообщества').count()) throw new Error('Free animation toggle visible');
    await noOverflow(free, '390 free light');
    await free.screenshot({ path: path.join(screenshotDir, 't10-390-free-posts-light.png'), fullPage: true });
    await free.setViewportSize({ width: 1440, height: 900 });
    await noOverflow(free, '1440 free light');
    await free.screenshot({ path: path.join(screenshotDir, 't10-1440-free-posts-light.png'), fullPage: true });
    await free.close();
    console.log(JSON.stringify({ result: 'PASS', draftRestored: true, invalidUrlRejected: true, conflictRecovered: true, customApplied: true, animationEnabled: true, freeGate: true, screenshots: 5 }));
  } finally { await browser.close(); }
})().catch((error) => { console.error(error.stack || error.message); process.exitCode = 1; });
