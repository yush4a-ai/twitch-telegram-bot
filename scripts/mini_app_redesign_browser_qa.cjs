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
      initData, initDataUnsafe:{user:{id:999,first_name:'Чужое имя из initDataUnsafe'}}, colorScheme: dark ? 'dark' : 'light', themeParams: {},
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
  if (scenario === 'theme') return themeJourney(page);
  if (scenario === 'shell') return shellJourney(page);
  if (scenario !== 'baseline') throw new Error(`Journey not yet implemented: ${scenario}`);
  await page.getByRole('heading', { name: 'Сейчас в эфире', exact: true }).waitFor();
  await page.getByRole('button', { name: 'Стримеры', exact: true }).click();
  await page.locator('#content .list-row').first().waitFor();
  assert.equal(await page.locator('#content .list-row').count(), 6, 'Server supplies all six subscriptions');
  await assertLayout(page);
}

async function shellJourney(page) {
  await page.getByRole('heading', { name:'Сейчас в эфире',exact:true }).waitFor();
  const labels=()=>page.locator('#tab-bar button').allTextContents();
  assert.deepEqual((await labels()).map(x=>x.trim()),['Главная','Стримеры','Профиль','Plus']);
  for(const width of [360,390,430,768,1440]) {
    await page.setViewportSize({width,height:844});await assertLayout(page);
    assert((await page.locator('.app-shell').boundingBox()).width<=600,'Accepted A max600');
  }
  await page.setViewportSize({width:390,height:844});
  await page.getByRole('button',{name:'Профиль',exact:true}).click();
  await page.getByRole('heading',{name:'Профиль',exact:true}).waitFor();
  await page.locator('#content[data-profile-state="ready"]').waitFor();
  await page.getByText('Очень длинное настоящее имя в локальном сценарии',{exact:true}).waitFor();
  assert.equal(await page.getByText('Чужое имя из initDataUnsafe',{exact:true}).count(),0);
  const accessName=await page.getByRole('button',{name:'Моя подписка',exact:true}).count()?'Моя подписка':'Возможности Plus';
  await page.getByRole('button',{name:accessName,exact:true}).click();
  await page.getByRole('heading',{name:accessName,exact:true}).waitFor();
  assert.equal(await page.locator('#tab-bar [aria-current="page"]').getAttribute('aria-label'),'Plus');
  await page.evaluate(()=>window.__qaSdk.back());
  await page.getByRole('heading',{name:'Профиль',exact:true}).waitFor();
  await page.getByRole('button',{name:'Plus',exact:true}).click();
  await page.getByRole('heading',{name:accessName,exact:true}).waitFor();
  await page.getByRole('button',{name:'Plus',exact:true}).click();
  await page.evaluate(()=>window.__qaSdk.back());
  await page.getByRole('heading',{name:'Профиль',exact:true}).waitFor();
  await page.getByRole('button',{name:'Меню приложения',exact:true}).click();
  const menu=page.getByRole('dialog',{name:'Меню приложения',exact:true});
  await menu.getByRole('button',{name:'Подписка',exact:true}).click();
  await page.getByRole('heading',{name:accessName,exact:true}).waitFor();
  assert.equal(await menu.count(),0,'Menu closes before opening the shared subscription');
  await page.evaluate(()=>window.__qaSdk.back());
  await page.getByRole('heading',{name:'Профиль',exact:true}).waitFor();
  await page.getByRole('button',{name:'Тема приложения',exact:true}).click();
  const dialog=page.getByRole('dialog',{name:'Тема приложения',exact:true});
  await dialog.waitFor();
  await dialog.getByRole('button',{name:'Тёмная',exact:true}).click();
  assert.equal(await page.evaluate(()=>document.documentElement.dataset.theme),'dark');
  await page.keyboard.press('Tab');await page.keyboard.press('Shift+Tab');
  assert(await dialog.evaluate(node=>node.contains(document.activeElement)),'Focus stays inside dialog');
  await page.keyboard.press('Escape');await dialog.waitFor({state:'detached'});
  assert.equal(await page.evaluate(()=>document.activeElement.getAttribute('aria-label')),'Тема приложения');
  await page.getByRole('button',{name:'Тема приложения',exact:true}).click();
  await page.evaluate(()=>window.__qaSdk.back());
  await dialog.waitFor({state:'detached'});
  await page.getByRole('heading',{name:'Профиль',exact:true}).waitFor();
  await page.getByRole('button',{name:'Поддержка',exact:true}).click();
  await page.getByRole('heading',{name:'Поддержка',exact:true}).waitFor();
  await page.evaluate(()=>window.__qaSdk.back());
  await page.getByRole('heading',{name:'Профиль',exact:true}).waitFor();
  await page.getByRole('button',{name:'Стример',exact:true}).click();
  assert.deepEqual((await labels()).map(x=>x.trim()),['Мой канал','Посты','Профиль','Plus']);
  await page.getByRole('button',{name:'Профиль',exact:true}).click();
  await page.setViewportSize({width:360,height:440});
  await page.evaluate(()=>document.documentElement.style.fontSize='200%');await assertLayout(page);
  assert(await page.locator('#tab-bar').evaluate(node=>node.scrollHeight<=node.clientHeight+1),'Full navigation labels reflow');
  await page.evaluate(()=>document.documentElement.style.fontSize='');await page.setViewportSize({width:390,height:844});
  await page.getByRole('button',{name:'Зритель',exact:true}).click();
  await page.getByRole('button',{name:'Стримеры',exact:true}).click();
  await page.locator('#content .list-row').first().waitFor();
  const rows=page.locator('#content .list-row');
  const index=Math.min(80,(await rows.count())-1),row=rows.nth(index);
  await row.scrollIntoViewIfNeeded();
  await row.evaluate(node=>{const bottom=node.getBoundingClientRect().bottom,nav=document.getElementById('tab-bar').getBoundingClientRect().top;if(bottom>nav-16)window.scrollBy(0,bottom-nav+16);});
  const before=await row.boundingBox();
  assert(before.y+before.height<=(await page.locator('#tab-bar').boundingBox()).y,'The tested row is visible above navigation');
  const control=row.getByRole('button');const label=await control.textContent();
  await control.click();
  await page.evaluate(()=>window.__qaSdk.back());
  await page.locator('#content .list-row').first().waitFor();
  try{await page.waitForFunction(label=>document.activeElement?.textContent===label,label);}
  catch{
    const focus=await page.evaluate(()=>({tag:document.activeElement?.tagName,text:document.activeElement?.textContent,label:document.activeElement?.getAttribute('aria-label'),scroll:scrollY}));
    throw new Error(`Back focus expected ${label}; observed ${JSON.stringify(focus)}`);
  }
  const restored=await rows.nth(index).boundingBox();
  assert(Math.abs(restored.y-before.y)<2,`Visible row position restored after detail: ${before.y} -> ${restored.y}`);
  const draft=page.locator('input[name="channel_search"]');await draft.fill('Длинный сохранённый поисковый запрос');
  await page.getByRole('button',{name:'Профиль',exact:true}).click();await page.evaluate(()=>window.__qaSdk.back());
  assert.equal(await draft.inputValue(),'Длинный сохранённый поисковый запрос','Draft survives internal navigation');
  await page.reload();await page.locator('#content h1').waitFor();await page.getByRole('button',{name:'Стримеры',exact:true}).click();
  await page.locator('input[name="channel_search"]').waitFor();assert.equal(await draft.inputValue(),'Длинный сохранённый поисковый запрос','Own draft survives reload');
  const privacy=await page.evaluate(async()=>{
    const {createApi}=await import('/app/api.js');const api=createApi('unused-in-local-storage-test');api.bindIdentity({id:501});api.storage.setItem('private-draft','A');
    const own=api.storage.getItem('private-draft');api.bindIdentity({id:202});return {own,foreign:api.storage.getItem('private-draft'),old:localStorage.getItem('ts-user:501:private-draft')};
  });
  assert.deepEqual(privacy,{own:'A',foreign:null,old:null});
  const focusRace=await page.evaluate(async()=>{
    const {createRouter}=await import('/app/router.js');
    const content=document.getElementById('content');
    const frames=()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
    function render(state){content.replaceChildren();const button=document.createElement('button');button.textContent=state.tab;button.dataset.focusKey=state.tab;content.append(button);}
    const router=createRouter(render);render(router.state);
    content.querySelector('button').focus();router.setTab('streamers');await frames();
    content.focus();router.back();await frames();
    const restored=document.activeElement.dataset.focusKey;
    content.focus();router.setTab('streamers');const late=content.querySelector('button');late.focus();await frames();
    const preserved=document.activeElement===late;router.dispose();
    return {restored,preserved};
  });
  assert.deepEqual(focusRace,{restored:'home',preserved:true},'Persistent main allows restore; a later user focus is preserved');
  await page.reload();await page.locator('#content h1').waitFor();
}

async function themeJourney(page) {
  const pictures=[];
  async function capture(label) {
    const file=`theme-${argument('engine','chromium')}-${label}-390.png`;
    await page.screenshot({path:path.join(output,file),fullPage:false,animations:'disabled'});
    pictures.push({file,sha256:digest(path.join(output,file)),viewport:'390x844'});
  }
  await page.getByRole('heading', { name: 'Сейчас в эфире', exact: true }).waitFor();
  const canvas = () => page.evaluate(() => getComputedStyle(document.body).backgroundColor);
  assert.equal(await canvas(), 'rgb(245, 246, 248)', 'First visit is own light even in dark Telegram');
  await capture('light');
  await page.evaluate(async () => (await import('/app/app.js')).theme.setChoice('dark'));
  assert.equal(await canvas(), 'rgb(23, 23, 23)', 'Own dark is neutral #171717');
  await capture('dark');
  await page.reload();
  await page.getByRole('heading', { name: 'Сейчас в эфире', exact: true }).waitFor();
  assert.equal(await canvas(), 'rgb(23, 23, 23)', 'Explicit dark survives reload');
  await page.evaluate(() => {
    const sdk = window.Telegram.WebApp;
    sdk.colorScheme = 'light';
    sdk.themeParams = { bg_color: '#fff5e5', text_color: '#302517', button_color: '#804000', button_text_color: '#ffffff',
      link_color: '#805500', header_bg_color: '#efe5d5', bottom_bar_bg_color: '#e8dcc8', section_bg_color: '#ffffff',
      hint_color: '#604d38', section_separator_color: '#c8b79d' };
    for (const fn of window.__qaSdk.events.get('themeChanged')) fn();
  });
  assert.equal(await canvas(), 'rgb(23, 23, 23)', 'Theme event preserves an explicit choice');
  await page.evaluate(async () => (await import('/app/app.js')).theme.setChoice('telegram'));
  const tokens = await page.evaluate(() => {
    const css = getComputedStyle(document.documentElement);
    return Object.fromEntries(['canvas', 'text', 'button-bg', 'button-fg', 'link', 'header', 'bottom'].map(k => [k, css.getPropertyValue(`--${k}`).trim()]));
  });
  assert.deepEqual(tokens, { canvas:'#fff5e5', text:'#302517', 'button-bg':'#804000','button-fg':'#ffffff',link:'#805500',header:'#efe5d5',bottom:'#e8dcc8' });
  assert.deepEqual(await page.evaluate(()=>[getComputedStyle(document.querySelector('.app-header')).backgroundColor,getComputedStyle(document.querySelector('#tab-bar')).backgroundColor]),['rgb(239, 229, 213)','rgb(232, 220, 200)']);
  await capture('telegram-light');
  await page.evaluate(() => {
    const sdk = window.Telegram.WebApp;
    sdk.colorScheme = 'dark';
    sdk.themeParams = {bg_color:'#211d18',text_color:'#f6ecdc',button_color:'#e4c9a0',button_text_color:'#211d18',link_color:'#e4c9a0'};
    for (const fn of window.__qaSdk.events.get('themeChanged')) fn();
    sdk.safeAreaInset={top:12,right:18,bottom:24,left:5};
    sdk.contentSafeAreaInset={top:23,right:7,bottom:11,left:15};
    sdk.viewportHeight=600; sdk.viewportStableHeight=650;
    for (const name of ['safeAreaChanged','contentSafeAreaChanged','viewportChanged']) for (const fn of window.__qaSdk.events.get(name)) fn();
  });
  assert.equal(await canvas(), 'rgb(33, 29, 24)', 'Telegram event updates actual custom background');
  await capture('telegram-dark');
  await page.reload();
  await page.getByRole('heading', { name: 'Сейчас в эфире', exact: true }).waitFor();
  // Each reload installs a fresh SDK; the user's telegram choice remains stored.
  assert.equal(await page.evaluate(() => document.documentElement.dataset.themeChoice), 'telegram');
  const lifecycle = await page.evaluate(async () => {
    const {createThemeController} = await import('/app/theme.js');
    const {createTelegramAdapter} = await import('/app/telegram.js');
    const before = Object.fromEntries([...window.__qaSdk.events].map(([k,v])=>[k,v.size]));
    const adapter = createTelegramAdapter(()=>{});
    let last;
    const denied = {getItem(){throw new Error('denied');},setItem(){throw new Error('denied');}};
    const controller = createThemeController({storage:denied,telegram:adapter,applyTokens:value=>{last=value;}});
    const first=controller.getChoice(); controller.setChoice('dark');
    window.Telegram.WebApp.themeParams={bg_color:'url(https://invalid.test)',text_color:'#171717',button_color:'#171717',button_text_color:'#171717'};
    for(const fn of window.__qaSdk.events.get('themeChanged')) fn();
    const kept=controller.getChoice(); controller.setChoice('telegram');
    const safe=Object.values(last).every(value=>typeof value !== 'string'||!value.includes('url('));
    const sdk=window.Telegram.WebApp;
    sdk.safeAreaInset={top:12,right:18,bottom:24,left:5};sdk.contentSafeAreaInset={top:23,right:7,bottom:11,left:15};sdk.viewportHeight=600;sdk.viewportStableHeight=650;
    for(const name of ['safeAreaChanged','contentSafeAreaChanged','viewportChanged']) for(const fn of window.__qaSdk.events.get(name))fn();
    const css=document.documentElement.style;
    const insets=['top','right','bottom','left'].map(k=>css.getPropertyValue(`--${k}-inset`));
    const viewport=css.getPropertyValue('--viewport-height');
    adapter.syncBack(true);adapter.syncBack(true);adapter.syncBack(false);
    controller.dispose();adapter.dispose();adapter.dispose();
    const after=Object.fromEntries([...window.__qaSdk.events].map(([k,v])=>[k,v.size]));
    return {before,after,first,kept,safe,insets,viewport};
  });
  assert.deepEqual(lifecycle.after,lifecycle.before,'Repeated adapters clean their own handlers');
  assert.equal(lifecycle.first,'light');assert.equal(lifecycle.kept,'dark');assert(lifecycle.safe);
  for(const [i,value] of [23,18,24,15].entries()) assert(lifecycle.insets[i].includes(`${value}px`),'Safe/content max, no sum');
  assert.equal(lifecycle.viewport,'600px');
  // Fullscreen rejected by an older client must still leave a usable adapter.
  await page.evaluate(async()=>{window.Telegram.WebApp.requestFullscreen=()=>{throw new Error('unsupported');};const {createTelegramAdapter}=await import('/app/telegram.js');const adapter=createTelegramAdapter(()=>{});adapter.dispose();});
  await page.evaluate(async()=>{const {theme}=await import('/app/app.js');theme.setChoice('light');});
  await assertLayout(page);
  return pictures;
}

async function shellAuthGates(browser,url) {
  const page=await browser.newPage({viewport:{width:390,height:440}}),privateReads=[];
  await page.route('https://telegram.org/**',route=>route.fulfill({status:200,contentType:'application/javascript',body:''}));
  page.on('request',request=>{if(request.url().includes('/app/api/'))privateReads.push(new URL(request.url()).pathname);});
  await installSdk(page,{initData:''});await page.goto(url);
  await page.getByRole('heading',{name:'Откройте приложение из Telegram',exact:true}).waitFor();
  assert.deepEqual(privateReads,[],'Missing signed initData never requests private services');
  await page.close();
  const offline=await browser.newPage({viewport:{width:390,height:440}});
  await offline.route('https://telegram.org/**',route=>route.fulfill({status:200,contentType:'application/javascript',body:''}));
  let release;const gate=new Promise(resolve=>{release=resolve;});
  await offline.route('**/app/api/bootstrap',async route=>{await gate;await route.fulfill({status:503,contentType:'application/json',body:'{"error":"unavailable"}'});});
  await installSdk(offline);await offline.goto(url,{waitUntil:'domcontentloaded'});
  await offline.getByText('Проверяем вход…',{exact:true}).waitFor();release();
  await offline.getByText('Связь прервалась. Откройте приложение заново, когда сеть восстановится.',{exact:true}).waitFor();
  assert.equal(await offline.locator('#content h1').count(),1,'Bootstrap error has one clear heading');
  await assertLayout(offline);await offline.close();
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
    await installSdk(page, {dark:journey === 'theme'});
    if(journey==='theme')await page.emulateMedia({colorScheme:'dark'});
    await page.goto(server.url);
    const journeyPictures=await runJourney(page, journey);
    if(journey==='shell')await shellAuthGates(browser,server.url);
    if(journeyPictures)report.screenshots.push(...journeyPictures);
    for (const [name, action] of [
      ['viewer', async () => { await page.getByRole('button',{name:'Зритель',exact:true}).click();await page.getByRole('button',{name:'Стримеры',exact:true}).click();await page.locator('#content .list-row').first().waitFor(); }],
      ['channel', async () => { await page.getByRole('button', { name: 'Стример', exact: true }).click();await page.getByRole('button',{name:'Мой канал',exact:true}).click(); await page.getByRole('heading', { name: 'Мой канал', exact: true }).waitFor(); }],
      ['profile', async () => { await page.getByRole('button', { name: 'Профиль', exact: true }).click(); await page.getByRole('heading', { name: 'Профиль', exact: true }).waitFor(); }],
    ]) {
      await action();
      await assertLayout(page);
      const file = `${journey}-${engineName}-${name}-390.png`;
      await page.screenshot({ path: path.join(output, file), fullPage: false, animations:'disabled' });
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
