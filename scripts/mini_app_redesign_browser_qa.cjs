/* Actual local /app + temporary SQLite. Signed identity/SDK double, no external sends. */
const { chromium, webkit } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { spawn } = require('node:child_process');

const root = process.env.MINI_APP_QA_ROOT || process.cwd();
const output = process.env.MINI_APP_QA_SCREENSHOTS || path.join(root, 'docs/audits/mini-app-redesign-plus-2026-10-02');
const argument = (name, fallback) => {
  const i = process.argv.indexOf(`--${name}`);
  return i < 0 ? fallback : process.argv[i + 1];
};
const digest = file => crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');

function signedIdentity(id = 501) {
  const fields = {
    auth_date: String(Math.floor(Date.now() / 1000)),
    user: JSON.stringify({ id, first_name: 'Очень длинное настоящее имя в локальном сценарии', username: 'fixture_owner' }),
  };
  const data = Object.keys(fields).sort().map(k => `${k}=${fields[k]}`).join('\n');
  const secret = crypto.createHmac('sha256', 'WebAppData').update('123456:test-telegram-token').digest();
  fields.hash = crypto.createHmac('sha256', secret).update(data).digest('hex');
  return new URLSearchParams(fields).toString();
}

async function fixtureServer(scenario) {
  const child = spawn(path.join(root, '.venv/Scripts/python.exe'), ['-m', 'scripts.mini_app_browser_fixture'], {
    cwd: root, env: { ...process.env, MINI_APP_QA_SCENARIO: scenario }, stdio: ['ignore', 'pipe', 'pipe'], windowsHide: true,
  });
  const address = await new Promise((resolve, reject) => {
    let buffer = '', errors = '';
    const timeout = setTimeout(() => { child.kill(); reject(new Error('Local fixture startup timeout')); }, 15000);
    child.stderr.on('data', data => { errors = (errors + data).slice(-2000); });
    child.once('exit', code => { clearTimeout(timeout); reject(new Error(`Fixture exited ${code}: ${errors}`)); });
    child.stdout.on('data', data => {
      buffer += data;
      if (!buffer.includes('\n')) return;
      try { const value = JSON.parse(buffer.split('\n')[0]); clearTimeout(timeout); resolve(value.url); }
      catch (error) { clearTimeout(timeout); child.kill(); reject(error); }
    });
  });
  return { url: address, async close() {
    if (child.exitCode !== null) return;
    const exit = new Promise(resolve => child.once('exit', resolve));
    await fetch(new URL('/_qa/shutdown', address), { method: 'POST' });
    await exit;
  } };
}

async function installSdk(page, { initData = signedIdentity(), dark = false } = {}) {
  await page.addInitScript(({ initData, dark }) => {
    const events = new Map();
    window.__qaSdk = { events, back: null, counters: { ready: 0, expand: 0, fullscreen: 0 } };
    window.Telegram = { WebApp: {
      initData, colorScheme: dark ? 'dark' : 'light', themeParams: {},
      safeAreaInset: {}, contentSafeAreaInset: {},
      ready() { window.__qaSdk.counters.ready++; },
      expand() { window.__qaSdk.counters.expand++; },
      requestFullscreen() { window.__qaSdk.counters.fullscreen++; },
      onEvent(name, fn) { if (!events.has(name)) events.set(name, new Set()); events.get(name).add(fn); },
      offEvent(name, fn) { events.get(name)?.delete(fn); },
      BackButton: {
        show() {}, hide() {}, onClick(fn) { window.__qaSdk.back = fn; },
        offClick(fn) { if (window.__qaSdk.back === fn) window.__qaSdk.back = null; },
      },
      requestWriteAccess(callback) { callback(true); },
      openLink() { throw new Error('External navigation requires an explicit QA scenario'); },
    } };
  }, { initData, dark });
}

async function assertLayout(page) {
  const result = await page.evaluate(() => ({
    overflow: document.documentElement.scrollWidth > innerWidth + 1,
    controls: [...document.querySelectorAll('#tab-bar button')].map(button => {
      const r = button.getBoundingClientRect();
      return { label: button.getAttribute('aria-label'), width: r.width, height: r.height, visible: r.x >= -1 && r.right <= innerWidth + 1 };
    }),
  }));
  assert(!result.overflow, 'No horizontal overflow');
  assert(result.controls.every(c => c.width >= 44 && c.height >= 44 && c.visible), 'Bottom actions remain visible and accessible');
  return result;
}

async function runJourney(page, scenario) {
  if (scenario !== 'baseline') throw new Error(`Journey not yet implemented: ${scenario}`);
  await page.getByRole('heading', { name: 'Сейчас в эфире', exact: true }).waitFor();
  await page.getByRole('button', { name: 'Стримеры', exact: true }).click();
  await page.locator('#content .list-row').first().waitFor();
  assert.equal(await page.locator('#content .list-row').count(), 6, 'Server supplies all six subscriptions');
  await assertLayout(page);
}

async function main() {
  const engineName = argument('engine', 'chromium');
  const journey = argument('journey', 'baseline');
  const scenario = argument('scenario', 'free-six');
  assert(['chromium', 'webkit'].includes(engineName));
  fs.mkdirSync(output, { recursive: true });
  const server = process.env.MINI_APP_QA_URL ? { url: process.env.MINI_APP_QA_URL, close: async () => {} } : await fixtureServer(scenario);
  const target = new URL(server.url);
  assert(['127.0.0.1', 'localhost'].includes(target.hostname), 'Fixture auth is loopback only');
  const engine = engineName === 'webkit' ? webkit : chromium;
  let browser;
  const report = { journey, scenario, engine: engineName, native: 'NOT TESTED', screenshots: [], errors: [], externalRequests: [], sources: {} };
  try {
    browser = await engine.launch({ headless: false });
    report.version = browser.version();
    const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
    page.on('pageerror', error => report.errors.push(error.message));
    await page.route('https://telegram.org/**', route => route.fulfill({ status: 200, contentType: 'application/javascript', body: '' }));
    await page.route('**/*', route => {
      const url = new URL(route.request().url());
      if (url.hostname === 'telegram.org') return route.fulfill({ status: 200, contentType: 'application/javascript', body: '' });
      if (url.origin === target.origin) return route.continue();
      report.externalRequests.push(`${url.origin}${url.pathname}`);
      return route.abort();
    });
    await installSdk(page);
    await page.goto(server.url);
    await runJourney(page, journey);
    for (const [name, action] of [
      ['viewer', async () => {}],
      ['channel', async () => { await page.getByRole('button', { name: 'Стример', exact: true }).click(); await page.getByRole('heading', { name: 'Мой канал', exact: true }).waitFor(); }],
      ['profile', async () => { await page.getByRole('button', { name: 'Профиль', exact: true }).click(); await page.getByRole('heading', { name: 'Профиль', exact: true }).waitFor(); }],
    ]) {
      await action();
      await assertLayout(page);
      const file = `${journey}-${engineName}-${name}-390.png`;
      await page.screenshot({ path: path.join(output, file), fullPage: false });
      report.screenshots.push({ file, sha256: digest(path.join(output, file)), viewport: '390x844' });
    }
    assert.deepEqual(report.errors, []);
    assert.deepEqual(report.externalRequests, []);
    report.status = 'PASS';
  } catch (error) { report.status = 'FAIL'; report.failure = error.stack; throw error; }
  finally {
    for (const name of fs.readdirSync(path.join(root, 'bot/mini_app_ui'))) {
      const file = path.join(root, 'bot/mini_app_ui', name);
      if (fs.statSync(file).isFile()) report.sources[name] = digest(file);
    }
    fs.writeFileSync(path.join(output, `${journey}-${engineName}-qa.json`), JSON.stringify(report, null, 2));
    await browser?.close();
    await server.close();
    console.log(JSON.stringify({ status: report.status, engine: engineName, screenshots: report.screenshots.length, errors: report.errors.length }));
  }
}

module.exports = { runJourney, assertLayout, installSdk, fixtureServer, signedIdentity };
if (require.main === module) main().catch(error => { console.error(error.message); process.exitCode = 1; });
