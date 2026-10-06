/* Локальная браузерная проверка оплаты и мобильной вёрстки мини-аппа.
 *
 * Стенд поднимается только на 127.0.0.1 (scripts.mini_app_browser_fixture),
 * запросы к провайдерам не уходят, реальные платежи не выполняются. Проверяются:
 * E1 (таймаут подготовки оплаты не «нет связи», а восстановление того же заказа),
 * E3 (после перезагрузки возвращается экран последней операции),
 * E4/E5 (постоянная недоступность и отказ в доступе — без кнопки «Повторить»),
 * E2/E11 (нижняя навигация и модалка слушают --keyboard-inset).
 *
 * Запуск: node scripts/mini_app_mobile_qa.cjs
 * Требуется установленный локально Playwright (см. NODE_PATH в отчёте).
 */
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const path = require('node:path');
const { spawn } = require('node:child_process');

const root = process.env.MINI_APP_QA_ROOT || process.cwd();
const BOT_TOKEN = '123456:test-telegram-token';
const PREPARE = '**/app/api/purchase/prepare';
// Заказ, который стенд сценария purchase-history создаёт для актора 501.
const FIXTURE_ORDER = '0'.repeat(31) + '1';

function signedIdentity(id = 501) {
  const fields = {
    auth_date: String(Math.floor(Date.now() / 1000)),
    user: JSON.stringify({ id, first_name: 'Локальная проверка', username: 'fixture_owner' }),
  };
  const data = Object.keys(fields).sort().map(key => `${key}=${fields[key]}`).join('\n');
  const secret = crypto.createHmac('sha256', 'WebAppData').update(BOT_TOKEN).digest();
  fields.hash = crypto.createHmac('sha256', secret).update(data).digest('hex');
  return new URLSearchParams(fields).toString();
}

async function fixtureServer() {
  const child = spawn(path.join(root, '.venv/Scripts/python.exe'), ['-m', 'scripts.mini_app_browser_fixture'], {
    cwd: root,
    env: { ...process.env, MINI_APP_QA_SCENARIO: 'purchase-history' },
    stdio: ['ignore', 'pipe', 'pipe'], windowsHide: true,
  });
  const url = await new Promise((resolve, reject) => {
    let buffer = '', errors = '';
    const timeout = setTimeout(() => { child.kill(); reject(new Error('Локальный стенд не стартовал за 20 с')); }, 20000);
    child.stderr.on('data', data => { errors = (errors + data).slice(-2000); });
    child.once('exit', code => { clearTimeout(timeout); reject(new Error(`Стенд завершился (${code}): ${errors}`)); });
    child.stdout.on('data', data => {
      buffer += data;
      if (!buffer.includes('\n')) return;
      try { const value = JSON.parse(buffer.split('\n')[0]); clearTimeout(timeout); resolve(value.url); }
      catch (error) { clearTimeout(timeout); child.kill(); reject(error); }
    });
  });
  return {
    url,
    async close() {
      if (child.exitCode !== null) return;
      const exit = new Promise(resolve => child.once('exit', resolve));
      try { await fetch(new URL('/_qa/shutdown', url), { method: 'POST' }); } catch {}
      await exit;
    },
  };
}

async function installSdk(page) {
  // Настоящий telegram-web-app.js в браузере без Telegram затирает двойника и
  // приложение остаётся без initData: отдаём пустую заглушку.
  await page.route('https://telegram.org/**', route => route.fulfill({
    status: 200, contentType: 'application/javascript', body: '',
  }));
  await page.addInitScript(({ initData }) => {
    const events = new Map();
    const opened = { links: [], invoices: [] };
    let backHandler = null;
    window.__qaSdk = { opened, back: () => backHandler?.() };
    window.Telegram = { WebApp: {
      initData, colorScheme: 'light', themeParams: {},
      safeAreaInset: { top: 0, bottom: 0, left: 0, right: 0 },
      contentSafeAreaInset: { top: 0, bottom: 0, left: 0, right: 0 },
      viewportHeight: window.innerHeight, viewportStableHeight: window.innerHeight,
      ready() {}, expand() {}, requestFullscreen() {},
      onEvent(name, handler) { if (!events.has(name)) events.set(name, []); events.get(name).push(handler); },
      offEvent(name, handler) { const list = events.get(name) || []; const index = list.indexOf(handler); if (index >= 0) list.splice(index, 1); },
      openLink(url) { opened.links.push(url); },
      openInvoice(url, callback) { opened.invoices.push(url); callback?.('pending'); },
      BackButton: { show() {}, hide() {}, onClick(handler) { backHandler = handler; }, offClick() { backHandler = null; } },
      setHeaderColor() {}, setBackgroundColor() {}, setBottomBarColor() {},
    } };
  }, { initData: signedIdentity() });
}

async function openPurchaseScreen(page, base) {
  await page.goto(base);
  await page.waitForFunction(() => document.querySelectorAll('#tab-bar button').length > 0);
  await page.getByRole('button', { name: 'Тариф' }).click();
  // На локальном стенде платёжная политика закрыта, поэтому кнопка называется
  // «Способы оплаты»; на открытой политике — «Оформить/Продлить Viewer Plus».
  await page.getByRole('button', { name: /Оформить Viewer Plus|Продлить Viewer Plus|Способы оплаты/ }).click();
  await page.getByRole('heading', { name: 'Как оплатить?' }).waitFor({ timeout: 15000 });
}

async function run() {
  const server = await fixtureServer();
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2 });
  const page = await context.newPage();
  const report = { url: server.url, checks: [] };
  const check = (name, detail) => { report.checks.push({ name, ok: true, detail }); console.log(`  ok  ${name}: ${detail}`); };
  try {
    await installSdk(page);

    // E3: перезагрузка возвращает на экран последней операции, а не «нет связи».
    await page.goto(server.url);
    await page.waitForFunction(() => document.querySelectorAll('#tab-bar button').length > 0);
    await page.evaluate(order => sessionStorage.setItem('ts-app-purchase-operation', JSON.stringify({
      request_key: 'qa-restore-1', product: 'viewer_plus', method: 'sbp', order_id: order,
      payment_url: '', link_opened: true, finished: false, saved_at: Date.now(), user_id: 501,
    })), FIXTURE_ORDER);
    await page.reload();
    await page.getByRole('heading', { name: 'Моя операция' }).waitFor({ timeout: 15000 });
    await page.getByText('Ожидает оплаты').first().waitFor({ timeout: 15000 });
    assert.equal(await page.getByText('Нет связи. Попробуйте ещё раз.').count(), 0, 'восстановление не выглядит обрывом связи');
    check('E3 восстановление операции', 'после reload открыт экран «Моя операция» со статусом');

    // E1: таймаут подготовки → «Проверяем статус платежа…» → повтор с тем же ключом.
    const keys = [];
    let prepareCalls = 0;
    await page.evaluate(() => sessionStorage.clear());
    await page.route(PREPARE, async route => {
      prepareCalls += 1;
      keys.push(route.request().postDataJSON().request_key);
      if (prepareCalls === 1) {
        // Сервер «думает» дольше клиентского таймаута: ответ не приходит.
        await new Promise(resolve => setTimeout(resolve, 26000));
        try { await route.abort(); } catch {}
        return;
      }
      await new Promise(resolve => setTimeout(resolve, 1500));
      await route.fulfill({
        status: 200, contentType: 'application/json',
        body: JSON.stringify({ state: 'pending', order_id: FIXTURE_ORDER, payment_url: 'https://t.me/invoice/qa-timeout-1' }),
      });
    });
    await openPurchaseScreen(page, server.url);
    await page.getByRole('button', { name: 'СБП' }).click();
    await page.getByText('Проверяем статус платежа…').first().waitFor({ timeout: 30000 });
    check('E1 сообщение при таймауте', 'показано «Проверяем статус платежа…» вместо «Нет связи»');
    await page.getByRole('heading', { name: 'Моя операция' }).waitFor({ timeout: 20000 });
    assert.equal(keys.length, 2, `ожидались две попытки, было ${keys.length}`);
    assert.equal(keys[0], keys[1], 'повтор идёт с тем же ключом запроса');
    const saved = await page.evaluate(() => JSON.parse(sessionStorage.getItem('ts-app-purchase-operation') || 'null'));
    assert.equal(saved.order_id, FIXTURE_ORDER, 'восстановлен тот же заказ');
    assert.equal(saved.request_key, keys[0], 'ключ сохранён в sessionStorage');
    const openedLinks = await page.evaluate(() => window.__qaSdk.opened.links.length);
    assert.equal(openedLinks, 1, 'окно оплаты открыто ровно один раз');
    check('E1 восстановление по ключу', `ключ ${keys[0]} повторён, ссылка открыта один раз`);
    await page.unroute(PREPARE);

    // E4: постоянная недоступность — текст причины, без кнопки «Повторить».
    await page.evaluate(() => sessionStorage.clear());
    await page.route(PREPARE, route => route.fulfill({
      status: 503, contentType: 'application/json',
      body: JSON.stringify({
        state: 'unavailable', payment_request_created: false, reason_code: 'payments_unavailable',
        message: 'Оплата временно недоступна. Мы заканчиваем подключение платёжной системы.',
      }),
    }));
    await openPurchaseScreen(page, server.url);
    await page.getByRole('button', { name: 'Telegram Stars' }).click();
    await page.getByText('Оплата временно недоступна. Мы заканчиваем подключение платёжной системы.').waitFor({ timeout: 15000 });
    assert.equal(await page.getByRole('button', { name: 'Повторить' }).count(), 0, 'постоянная недоступность не предлагает повтор');
    assert.equal(await page.getByRole('button', { name: 'Telegram Stars' }).isDisabled(), false, 'способ оплаты остаётся доступен');
    check('E4 постоянная недоступность', 'текст причины без кнопки «Повторить»');
    await page.unroute(PREPARE);

    // E5: отказ в доступе объясняется словами, а не «проверьте связь».
    await page.evaluate(() => sessionStorage.clear());
    await page.route(PREPARE, route => route.fulfill({
      status: 403, contentType: 'application/json', body: JSON.stringify({ error: 'order_denied' }),
    }));
    await openPurchaseScreen(page, server.url);
    await page.getByRole('button', { name: 'Банковская карта' }).click();
    const denied = page.getByText(/Нет доступа к этому действию/);
    await denied.waitFor({ timeout: 15000 });
    assert.equal(await page.getByText('Нет связи. Попробуйте ещё раз.').count(), 0, '403 не выглядит обрывом связи');
    assert.equal(await page.getByRole('button', { name: 'Повторить' }).count(), 0, 'отказ в доступе не предлагает повтор');
    check('E5 отказ в доступе', 'показан текст про доступ и поддержку');
    await page.unroute(PREPARE);

    // E2/E11: клавиатура поднимает навигацию и модалку, мёртвой переменной нет.
    await openPurchaseScreen(page, server.url);
    const layout = await page.evaluate(() => {
      const root = document.documentElement;
      const nav = document.getElementById('tab-bar');
      const keyboardInset = root.style.getPropertyValue('--keyboard-inset');
      const before = nav.getBoundingClientRect().bottom;
      root.style.setProperty('--keyboard-inset', '280px');
      const withKeyboard = nav.getBoundingClientRect().bottom;
      root.style.removeProperty('--keyboard-inset');
      const restored = nav.getBoundingClientRect().bottom;
      return {
        before, withKeyboard, restored,
        viewportHeight: root.style.getPropertyValue('--viewport-height'),
        stableHeight: root.style.getPropertyValue('--viewport-stable-height'),
        keyboardInset,
        offsetTop: root.style.getPropertyValue('--viewport-offset-top'),
      };
    });
    assert.ok(Math.abs((layout.before - layout.withKeyboard) - 280) <= 2,
      `навигация должна подняться на высоту клавиатуры: ${layout.before} → ${layout.withKeyboard}`);
    assert.ok(Math.abs(layout.restored - layout.before) <= 1, 'без клавиатуры навигация возвращается на место');
    assert.ok(layout.viewportHeight, 'адаптер задаёт --viewport-height');
    assert.equal(layout.stableHeight, '', 'мёртвая переменная высоты удалена');
    assert.equal(layout.keyboardInset.trim(), '0px', 'без клавиатуры смещение равно нулю');
    check('E2/E11 навигация', `высота клавиатуры учтена (${layout.before} → ${layout.withKeyboard})`);

    await page.getByRole('button', { name: 'Меню приложения' }).click();
    const dialog = await page.evaluate(() => {
      const root = document.documentElement;
      const box = document.querySelector('dialog.app-dialog');
      const before = box.getBoundingClientRect().bottom;
      root.style.setProperty('--keyboard-inset', '280px');
      const withKeyboard = box.getBoundingClientRect().bottom;
      root.style.removeProperty('--keyboard-inset');
      return { before, withKeyboard, viewportHeight: window.innerHeight };
    });
    // Модалка центрируется, поэтому проверяем главное: её низ не уходит под
    // клавиатуру и при её появлении поднимается выше.
    assert.ok(dialog.withKeyboard < dialog.before,
      `модалка должна подняться: ${dialog.before} → ${dialog.withKeyboard}`);
    assert.ok(dialog.withKeyboard <= dialog.viewportHeight - 280 - 16 + 1,
      `низ модалки (${dialog.withKeyboard}) остаётся выше клавиатуры (${dialog.viewportHeight - 280})`);
    check('E2 модалка', `низ модалки над клавиатурой (${dialog.before} → ${dialog.withKeyboard})`);

    report.ok = true;
    console.log(JSON.stringify({ url: report.url, checks: report.checks.length, ok: true }, null, 2));
  } finally {
    await browser.close();
    await server.close();
  }
}

run().catch(error => {
  console.error('Локальная проверка мини-аппа не прошла:', error.message);
  process.exitCode = 1;
});
