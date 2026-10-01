/* Folder journey on a temporary DB and synthetic Telegram SDK only. */
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
        showConfirm(_message, callback) { callback(true); },
        BackButton: { show() {}, hide() {}, onClick(fn) { window.__back = fn; }, offClick() { window.__back = null; } },
      } };
    }, signed(501));
    await page.goto(target, { waitUntil: 'networkidle' });
    await page.locator('#tab-bar button').nth(1).click();
    await page.getByRole('textbox', { name: 'Название новой папки' }).fill('Игры');
    await page.getByRole('button', { name: 'Создать папку' }).click();
    await page.getByRole('heading', { name: 'Общее правило' }).waitFor();
    await page.getByRole('textbox', { name: 'Категории' }).fill('Minecraft');
    await page.getByRole('button', { name: 'Добавить' }).first().click();
    await page.getByRole('button', { name: 'Сохранить правило' }).click();
    await page.getByText('Правило сохранено').waitFor();
    await noOverflow(page);
    await page.screenshot({ path: path.join(screenshotDir, 't16a-390-folder-rule-light.png'), fullPage: true });

    const base = target.replace(/\/app$/, '');
    const ownState = await (await page.request.post(`${base}/app/api/viewer/state`, {
      data: { init_data: signed(501) },
    })).json();
    const folder = ownState.folders[0];
    const concurrent = await page.request.post(`${base}/app/api/viewer/folder/rule`, {
      data: { init_data: signed(501), folder_id: folder.id,
        expected_version: folder.version, games: ['Just Chatting'],
        title_keywords: [], exclude_keywords: [] },
    });
    if (concurrent.status() !== 200) throw new Error('Concurrent rule update failed');
    await page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));
    await page.getByRole('heading', { name: 'Папка изменилась' }).waitFor();
    if (!(await page.getByRole('button', { name: 'Сохранить правило' }).isDisabled())) throw new Error('Stale save is enabled');
    await page.getByRole('button', { name: 'Загрузить текущую версию' }).click();
    await page.getByText('Just Chatting', { exact: true }).waitFor();

    await page.evaluate(() => window.__back());
    await page.getByRole('button', { name: 'Настройки Alpha' }).click();
    await page.getByRole('combobox', { name: 'Папка стримера' }).selectOption({ label: 'Игры' });
    await page.route('**/app/api/viewer/folder/move', (route) => route.abort());
    await page.getByRole('button', { name: 'Сохранить папку' }).click();
    await page.getByText('Не удалось сохранить папку. Попробуйте ещё раз.').waitFor();
    await page.unroute('**/app/api/viewer/folder/move');
    await Promise.all([
      page.waitForResponse((response) => response.url().endsWith('/app/api/viewer/folder/move') && response.status() === 200),
      page.getByRole('button', { name: 'Сохранить папку' }).click(),
    ]);
    await page.getByText('Не удалось сохранить папку. Попробуйте ещё раз.').waitFor({ state: 'detached' });
    await page.evaluate(() => window.__back());
    await page.getByText('Папка: Игры').waitFor();
    await page.getByRole('button', { name: /Игры · 1/ }).click();
    await page.setViewportSize({ width: 360, height: 780 });
    await page.evaluate(() => { window.Telegram.WebApp.colorScheme = 'dark'; window.__events.get('themeChanged')?.(); });
    await page.getByRole('button', { name: 'Alpha' }).waitFor();
    await noOverflow(page);
    await page.screenshot({ path: path.join(screenshotDir, 't16a-360-folder-member-dark.png'), fullPage: true });
    await page.getByRole('button', { name: 'Alpha' }).click();
    await page.getByRole('button', { name: 'Настроить фильтр' }).click();
    await page.getByRole('button', { name: 'Сохранить фильтр' }).click();
    await page.getByRole('button', { name: 'Использовать правило папки' }).click();
    await page.getByText(/Правило папки «Игры»/).waitFor();
    await page.evaluate(() => window.__back());
    await page.getByRole('button', { name: 'Удалить папку' }).click();
    await page.getByRole('button', { name: 'Настройки Alpha' }).waitFor();
    await page.getByText('Папка: Игры').waitFor({ state: 'detached' });
    console.log(JSON.stringify({ result: 'PASS', create: true, rule: true, conflict: true, move: true, filterReset: true, networkRollback: true, deleteKeepsSubscription: true, screenshots: 2 }));
  } finally { await browser.close(); }
})().catch((error) => { console.error(error.stack || error.message); process.exitCode = 1; });
