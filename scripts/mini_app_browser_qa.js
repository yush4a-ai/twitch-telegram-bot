/* Browser checks for the isolated Mini App fixture; run through installed Playwright. */
const { chromium } = require('playwright');
const crypto = require('crypto');
const fs = require('fs');
const path = require('path');

const target = process.env.MINI_APP_QA_URL;
const screenshotDir = process.env.MINI_APP_QA_SCREENSHOTS;
if (!target || !screenshotDir) throw new Error('MINI_APP_QA_URL and MINI_APP_QA_SCREENSHOTS are required');
const fields = {
  auth_date: String(Math.floor(Date.now() / 1000)),
  user: JSON.stringify({ id: 101, first_name: 'Test' }),
};
const checkString = Object.entries(fields).sort(([a], [b]) => a.localeCompare(b)).map(([k, v]) => `${k}=${v}`).join('\n');
const secret = crypto.createHmac('sha256', 'WebAppData').update('123456:test-telegram-token').digest();
fields.hash = crypto.createHmac('sha256', secret).update(checkString).digest('hex');
const initData = new URLSearchParams(fields).toString();

(async () => {
  fs.mkdirSync(screenshotDir, { recursive: true });
  const browser = await chromium.launch({ headless: false });
  const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.route('https://telegram.org/js/telegram-web-app.js*', (route) => route.fulfill({ status: 200, contentType: 'application/javascript', body: '' }));
  await page.addInitScript((signed) => {
    const handlers = new Set();
    const events = new Map();
    window.__miniAppFixture = { handlers, events, fullscreen: 0, ready: 0, expand: 0 };
    window.Telegram = { WebApp: {
      initData: signed,
      colorScheme: 'light',
      safeAreaInset: { top: 0, bottom: 0 },
      contentSafeAreaInset: { top: 0, bottom: 0 },
      ready() { window.__miniAppFixture.ready++; },
      expand() { window.__miniAppFixture.expand++; },
      requestFullscreen() { window.__miniAppFixture.fullscreen++; },
      onEvent(name, handler) { events.set(name, handler); },
      offEvent(name) { events.delete(name); },
      BackButton: {
        show() {}, hide() {},
        onClick(handler) { handlers.add(handler); },
        offClick(handler) { handlers.delete(handler); },
      },
    } };
  }, initData);
  try {
    await page.goto(target, { waitUntil: 'networkidle' });
    await page.getByRole('heading', { name: 'Сейчас в эфире' }).waitFor();
    const sdk = await page.evaluate(() => ({ ready: window.__miniAppFixture.ready, expand: window.__miniAppFixture.expand, fullscreen: window.__miniAppFixture.fullscreen }));
    if (sdk.ready !== 1 || sdk.expand !== 1 || sdk.fullscreen !== 1) throw new Error(`SDK lifecycle: ${JSON.stringify(sdk)}`);
    const insets = await page.evaluate(() => {
      window.Telegram.WebApp.safeAreaInset = { top: 12, bottom: 22 };
      window.Telegram.WebApp.contentSafeAreaInset = { top: 16, bottom: 10 };
      window.__miniAppFixture.events.get('safeAreaChanged')?.();
      const style = document.documentElement.style;
      return [style.getPropertyValue('--top-inset'), style.getPropertyValue('--bottom-inset')];
    });
    if (insets[0] !== '16px' || insets[1] !== '22px') throw new Error(`Safe insets: ${JSON.stringify(insets)}`);
    await page.evaluate(() => {
      window.Telegram.WebApp.safeAreaInset = { top: 0, bottom: 0 };
      window.Telegram.WebApp.contentSafeAreaInset = { top: 0, bottom: 0 };
      window.__miniAppFixture.events.get('safeAreaChanged')?.();
    });
    if (await page.locator('#tab-bar button').count() !== 3) throw new Error('Viewer needs three tabs');
    await page.locator('#mode-switch button').getByText('Стример').click();
    await page.getByRole('heading', { name: 'Мой канал' }).waitFor();
    if (await page.locator('#tab-bar button').count() !== 3) throw new Error('Streamer needs three tabs');
    await page.locator('#mode-switch button').getByText('Зритель').click();
    for (let i = 0; i < 20; i++) {
      await page.locator('#tab-bar button').nth((i % 2) + 1).click();
      const count = await page.evaluate(() => window.__miniAppFixture.handlers.size);
      if (count !== 1) throw new Error(`BackButton handlers after transition ${i}: ${count}`);
    }
    await page.locator('#tab-bar button').first().click();
    await page.waitForTimeout(100);
    await page.evaluate(() => {
      const tail = document.createElement('div');
      tail.id = 'scroll-test-tail';
      tail.setAttribute('aria-hidden', 'true');
      for (let i = 0; i < 80; i++) {
        const line = document.createElement('p');
        line.textContent = 'Проверка прокрутки';
        tail.append(line);
      }
      document.body.append(tail);
    });
    const scrollAttempt = await page.evaluate(() => {
      window.scrollTo(0, 420);
      return { y: window.scrollY, top: document.documentElement.scrollTop };
    });
    await page.waitForTimeout(100);
    const beforeSwitch = await page.evaluate(() => window.scrollY);
    await page.locator('#tab-bar button').nth(2).click();
    const afterSwitch = await page.evaluate(() => window.scrollY);
    await page.evaluate(() => [...window.__miniAppFixture.handlers][0]());
    await page.waitForTimeout(100);
    const restored = await page.evaluate(() => window.scrollY);
    if (Math.abs(restored - 420) > 5) throw new Error(`Scroll restore: attempt=${JSON.stringify(scrollAttempt)} before=${beforeSwitch} switched=${afterSwitch} restored=${restored} height=${JSON.stringify(await page.evaluate(() => ({ html: document.documentElement.scrollHeight, body: document.body.scrollHeight, viewport: innerHeight })))} heading=${await page.locator('h1').textContent()}`);
    await page.evaluate(() => { document.getElementById('scroll-test-tail').remove(); window.scrollTo(0, 0); });
    await page.waitForTimeout(100);
    for (const width of [360, 390, 768, 1440]) {
      await page.setViewportSize({ width, height: 844 });
      for (const theme of ['light', 'dark']) {
        await page.evaluate((value) => {
          window.Telegram.WebApp.colorScheme = value;
          window.__miniAppFixture.events.get('themeChanged')?.();
        }, theme);
        await page.waitForTimeout(220);
        if (theme === 'dark') {
          const tone = await page.locator('#tab-bar button').nth(1).evaluate((node) => ({
            color: getComputedStyle(node).color,
            theme: document.documentElement.dataset.theme,
            rootMatch: document.documentElement.matches(':root[data-theme="dark"]'),
            secondary: getComputedStyle(document.documentElement).getPropertyValue('--secondary'),
            selectorMatch: node.matches(':root[data-theme="dark"] .tab-bar button:not([aria-current="page"])'),
          }));
          if (tone.color !== 'rgb(203, 214, 231)') throw new Error(`Dark navigation contrast: ${JSON.stringify(tone)}`);
        }
        const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
        if (overflow) throw new Error(`Horizontal overflow at ${width}/${theme}`);
        await page.screenshot({ path: path.join(screenshotDir, `t4-${width}-${theme}.png`), fullPage: false });
      }
    }
    if (errors.length) throw new Error(`Browser errors: ${errors.join(' | ')}`);
    console.log(JSON.stringify({ result: 'PASS', sdk, transitions: 20, scrollRestored: restored, screenshots: 8, widths: [360, 390, 768, 1440], themes: ['light', 'dark'] }));
  } finally {
    await browser.close();
  }
})().catch((error) => { console.error(error.stack || error.message); process.exitCode = 1; });
