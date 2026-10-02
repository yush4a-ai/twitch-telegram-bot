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
  if (scenario === 'viewer-free') return viewerFreeJourney(page);
  if (scenario === 'video') return videoJourney(page);
  if (scenario === 'viewer-settings') return viewerSettingsJourney(page);
  if (scenario !== 'baseline') throw new Error(`Journey not yet implemented: ${scenario}`);
  await page.getByRole('heading', { name: 'Главная', exact: true }).waitFor();
  await page.getByRole('button', { name: 'Стримеры', exact: true }).click();
  await page.locator('#content .list-row').first().waitFor();
  assert.equal(await page.locator('#content .list-row').count(), 6, 'Server supplies all six subscriptions');
  await assertLayout(page);
}

async function shellJourney(page) {
  await page.getByRole('heading', { name:'Главная',exact:true }).waitFor();
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
  const control=row.locator('[data-open-streamer]');const label=await control.textContent();
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
  const draft=page.locator('input[name="subscription_search"]');await draft.fill('Длинный сохранённый поисковый запрос');
  await page.getByRole('button',{name:'Профиль',exact:true}).click();await page.evaluate(()=>window.__qaSdk.back());
  assert.equal(await draft.inputValue(),'Длинный сохранённый поисковый запрос','Draft survives internal navigation');
  await page.reload();await page.locator('#content h1').waitFor();await page.getByRole('button',{name:'Стримеры',exact:true}).click();
  await page.locator('input[name="subscription_search"]').waitFor();assert.equal(await draft.inputValue(),'Длинный сохранённый поисковый запрос','Own draft survives reload');
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
    const preserved=document.activeElement===late;router.refresh();router.refresh();await frames();
    const repeated=document.activeElement.dataset.focusKey;router.dispose();
    return {restored,preserved,repeated};
  });
  assert.deepEqual(focusRace,{restored:'home',preserved:true,repeated:'streamers'},'Persistent main allows restore; new user focus and consecutive renders preserve focus');
  await page.reload();await page.locator('#content h1').waitFor();
}

async function viewerFreeJourney(page) {
  await page.getByRole('heading',{name:'Главная',exact:true}).waitFor();
  await page.getByRole('button',{name:'Стримеры',exact:true}).click();
  await page.getByRole('heading',{name:'Стримеры',exact:true}).waitFor();
  const serverState=()=>page.evaluate(async()=>{
    const {createApi}=await import('/app/api.js');return createApi(Telegram.WebApp.initData).post('/app/api/viewer/state');
  });
  const initial=await serverState();
  assert.equal(await page.locator('.streamer-row').count(),initial.subscriptions.length);
  let searchRequests=0;page.on('request',request=>{if(request.url().endsWith('/app/api/viewer/search'))searchRequests++;});
  const local=page.getByRole('searchbox',{name:'Поиск по подпискам',exact:true});
  await local.fill('несуществующий запрос');
  if(initial.subscriptions.length)await page.getByText('В подписках ничего не найдено',{exact:true}).waitFor();
  else await page.getByText('Добавьте первого стримера',{exact:true}).waitFor();
  assert.equal(await page.locator('.streamer-row').count(),0);
  await local.fill('');assert.equal(await page.locator('.streamer-row').count(),initial.subscriptions.length);
  assert.equal(searchRequests,0,'Local subscription search never requests Twitch search');
  if(initial.subscriptions.length){
    assert.equal(initial.subscriptions.find(row=>row.login==='alpha').live.category,'Minecraft');
    await page.locator('[data-row-key="alpha"]').getByText('Minecraft',{exact:true}).waitFor();
    await page.getByRole('heading',{name:'В эфире',exact:true}).waitFor();
    await page.getByRole('heading',{name:'Статус уточняется',exact:true}).waitFor();
    await page.getByRole('heading',{name:'Не в эфире',exact:true}).waitFor();
    const long=initial.subscriptions.find(row=>row.login==='beta');
    await page.getByRole('button',{name:`Открыть ${long.display_name}`,exact:true}).click();
    await page.getByRole('heading',{name:long.display_name,exact:true}).waitFor();
    await page.evaluate(()=>window.__qaSdk.back());
    const notify=page.getByRole('checkbox',{name:`Уведомления ${long.display_name}`,exact:true});
    const was=await notify.isChecked();await notify.setChecked(!was);
    await page.waitForFunction(({login,enabled})=>document.querySelector(`[data-row-key="${login}"] input[type=checkbox]`)?.checked===enabled,{login:'beta',enabled:!was});
    await page.waitForFunction(()=>!document.querySelector('[aria-disabled="true"]'));
    assert.equal((await serverState()).subscriptions.find(row=>row.login==='beta').notify_enabled,!was,'Pause uses shared server rows');
    await page.reload();await page.getByRole('button',{name:'Стримеры',exact:true}).click();
    await page.getByRole('checkbox',{name:`Уведомления ${long.display_name}`,exact:true}).waitFor();
    assert.equal(await page.getByRole('checkbox',{name:`Уведомления ${long.display_name}`,exact:true}).isChecked(),!was,'Pause survives reload');
  }else{
    await page.getByText('Добавьте первого стримера',{exact:true}).waitFor();
    await page.getByRole('button',{name:'Добавить стримера',exact:true}).click();
    const modal=page.getByRole('dialog',{name:'Добавить стримера',exact:true});
    const query=modal.getByRole('searchbox',{name:'Ник или ссылка Twitch',exact:true});
    await query.fill('alpha');await modal.getByRole('button',{name:'Добавить Alpha',exact:true}).click();
    await modal.waitFor({state:'detached'});await page.locator('.streamer-row').waitFor();
    assert.deepEqual((await serverState()).subscriptions.map(row=>row.login),['alpha']);
    await page.getByRole('button',{name:'Открыть Alpha',exact:true}).click();
    page.once('dialog',prompt=>prompt.accept());
    await page.getByRole('button',{name:'Удалить подписку',exact:true}).click();
    await page.getByText('Добавьте первого стримера',{exact:true}).waitFor();
    assert.deepEqual((await serverState()).subscriptions,[]);
  }
  await page.getByRole('button',{name:'Добавить стримера',exact:true}).click();
  const add=page.getByRole('dialog',{name:'Добавить стримера',exact:true}),query=add.getByRole('searchbox',{name:'Ник или ссылка Twitch',exact:true});
  let releaseOld;const delayed=new Promise(resolve=>{releaseOld=resolve;});
  let oldStarted;const started=new Promise(resolve=>{oldStarted=resolve;});
  await page.route('**/app/api/viewer/search',async route=>{
    if(route.request().postDataJSON().query==='alpha'){
      const response=await route.fetch();oldStarted();await delayed;
      try{await route.fulfill({response});}catch{} // Changing the query aborts this earlier request.
    }else await route.continue();
  });
  await query.fill('alpha');await started;await query.fill('beta');
  await add.getByText('Очень длинное русское имя стримера с несколькими словами и подробным описанием',{exact:true}).waitFor();releaseOld();
  await page.waitForTimeout(100);
  assert.equal(await add.getByText('Alpha',{exact:true}).count(),0,'Late alpha cannot replace the newer beta result');
  await page.unroute('**/app/api/viewer/search');
  await page.route('**/app/api/viewer/search',route=>route.abort('failed'));
  await query.fill('gamma');await add.getByText('Поиск пока недоступен. Проверьте ник и попробуйте ещё раз.',{exact:true}).waitFor();
  assert.equal(await query.inputValue(),'gamma','Network failure retains the query');
  await page.unroute('**/app/api/viewer/search');
  await query.fill('delta');await add.getByText('Delta',{exact:true}).waitFor();
  await page.keyboard.press('Escape');await add.waitFor({state:'detached'});
  await page.getByRole('button',{name:'Добавить стримера',exact:true}).click();
  assert.equal(await query.inputValue(),'delta','Closed add dialog keeps own draft');
  await page.keyboard.press('Escape');
  for(const width of [360,390,430,768,1440]){await page.setViewportSize({width,height:844});await assertLayout(page);}
  await page.setViewportSize({width:360,height:440});await page.evaluate(()=>document.documentElement.style.fontSize='200%');await assertLayout(page);
  assert.equal(await page.getByRole('button',{name:'Добавить стримера',exact:true}).isVisible(),true,'Add remains available with large text');
  await page.evaluate(()=>document.documentElement.style.fontSize='');await page.setViewportSize({width:390,height:844});
  await assertLayout(page);
}

async function themeJourney(page) {
  const pictures=[];
  async function capture(label) {
    const file=`theme-${argument('engine','chromium')}-${label}-390.png`;
    await page.screenshot({path:path.join(output,file),fullPage:false,animations:'disabled'});
    pictures.push({file,sha256:digest(path.join(output,file)),viewport:'390x844'});
  }
  await page.getByRole('heading', { name: 'Главная', exact: true }).waitFor();
  const canvas = () => page.evaluate(() => getComputedStyle(document.body).backgroundColor);
  assert.equal(await canvas(), 'rgb(245, 246, 248)', 'First visit is own light even in dark Telegram');
  await capture('light');
  await page.evaluate(async () => (await import('/app/app.js')).theme.setChoice('dark'));
  assert.equal(await canvas(), 'rgb(23, 23, 23)', 'Own dark is neutral #171717');
  await capture('dark');
  await page.reload();
  await page.getByRole('heading', { name: 'Главная', exact: true }).waitFor();
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
  await page.getByRole('heading', { name: 'Главная', exact: true }).waitFor();
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

async function videoJourney(page){
  const pictures=[];async function capture(label){const file=`video-${argument('engine','chromium')}-${label}-390.png`;await page.screenshot({path:path.join(output,file),animations:'disabled'});pictures.push({file,sha256:digest(path.join(output,file)),viewport:'390x844'});}
  await page.getByRole('heading',{name:'Главная',exact:true}).waitFor();await page.getByRole('button',{name:'Стримеры',exact:true}).click();
  const call=(path,values={})=>page.evaluate(async({path,values})=>{const {createApi}=await import('/app/api.js');try{return {status:200,body:await createApi(Telegram.WebApp.initData).post(`/app/api/viewer/${path}`,values)};}catch(error){return {status:error.status,code:error.code};}},{path,values});
  const initial=(await call('state')).body;
  if(!initial.viewer_plus_active){
    await page.getByRole('button',{name:'Видео · Plus',exact:true}).click();await page.getByRole('heading',{name:'Возможности Plus',exact:true}).waitFor();
    assert.equal((await call('video-selection',{selected_logins:['alpha'],expected_version:initial.video_selection.version})).status,403);
    assert.deepEqual((await call('state')).body.video_selection.selected_logins,[]);
    await capture('free-plus');return pictures;
  }
  await page.getByRole('button',{name:`Видео · ${initial.video_selection.selected_logins.length}/5`,exact:true}).click();
  await page.getByRole('heading',{name:'Видеопревью',exact:true}).waitFor();
  assert.equal(await page.locator('#content .video-choice input[type=checkbox]').count(),initial.subscriptions.length);
  const checkbox=login=>page.locator(`[data-video-login="${login}"] input[type=checkbox]`);
  const count=n=>page.getByText(`Выбрано ${n} из 5`,{exact:true});
  await capture('picker');
  let releaseSave,saveStarted;const pendingSave=new Promise(resolve=>{releaseSave=resolve;}),saveSeen=new Promise(resolve=>{saveStarted=resolve;});
  await page.route('**/app/api/viewer/video-selection',async route=>{const response=await route.fetch();saveStarted();await pendingSave;await route.fulfill({response});});
  await checkbox('delta').click();await saveSeen;await count(3).waitFor();assert.equal(await checkbox('delta').isChecked(),false,'Pending save is not presented as applied');
  const query=page.getByRole('searchbox',{name:'Найти стримера для видео',exact:true});await query.focus();await capture('pending');releaseSave();
  await count(4).waitFor();await page.getByText('Выбор сохранён',{exact:true}).waitFor();
  try{await page.waitForFunction(()=>document.activeElement?.getAttribute('aria-label')==='Найти стримера для видео');}
  catch{throw new Error(`Late save focus: ${JSON.stringify(await page.evaluate(()=>({tag:document.activeElement?.tagName,label:document.activeElement?.getAttribute('aria-label'),text:document.activeElement?.textContent?.slice(0,100)})))}`);}
  await page.unroute('**/app/api/viewer/video-selection');
  await checkbox('epsilon').click();await count(5).waitFor();await page.getByText('Выбор сохранён',{exact:true}).waitFor();await capture('five');
  const fifth=(await call('state')).body.video_selection;
  assert.equal(fifth.selected_logins.length,5);assert(fifth.selected_logins.includes('gamma'),'Offline gamma occupies a slot');
  await query.fill('zeta');await count(5).waitFor();
  assert.equal(await page.locator('.video-choice').count(),1,'Search only filters visible choices');
  await checkbox('zeta').click();const replace=page.getByRole('dialog',{name:'Все пять мест заняты',exact:true});await replace.waitFor();
  await capture('replace');
  assert.deepEqual((await call('state')).body.video_selection,fifth,'Sixth choice has not mutated the saved five');
  await replace.getByRole('button',{name:'Закрыть',exact:true}).click();assert.deepEqual((await call('state')).body.video_selection,fifth,'Cancel is mutation-free');
  await checkbox('zeta').click();await replace.getByRole('button',{name:'Заменить Alpha',exact:true}).click();await replace.waitFor({state:'detached'});await page.getByText('Выбор сохранён',{exact:true}).waitFor();
  const swapped=(await call('state')).body.video_selection;assert.equal(swapped.selected_logins.length,5);assert(swapped.selected_logins.includes('zeta'));assert(!swapped.selected_logins.includes('alpha'));
  assert.deepEqual(swapped.selected_logins.filter(login=>login!=='zeta'),fifth.selected_logins.filter(login=>login!=='alpha'),'Swap preserves all other choices');
  assert.equal((await call('video-selection',{selected_logins:[...swapped.selected_logins,'alpha'],expected_version:swapped.version})).code,'video_limit','Server rejects a direct sixth choice');
  await query.fill('alpha');await checkbox('alpha').click();await replace.waitFor();
  const other=await call('video-selection',{selected_logins:swapped.selected_logins.filter(login=>login!=='beta'),expected_version:swapped.version});assert.equal(other.status,200);
  await page.evaluate(()=>document.dispatchEvent(new Event('visibilitychange')));await count(4).waitFor();
  await replace.getByRole('button',{name:`Заменить ${initial.subscriptions.find(row=>row.login==='beta').display_name}`,exact:true}).click();
  await replace.waitFor({state:'detached'});await page.getByText('Выбор изменился в другой сессии. Проверьте список и повторите действие.',{exact:true}).waitFor();await count(4).waitFor();
  assert.deepEqual((await call('state')).body.video_selection,other.body,'Open replacement dialog keeps its original version fence');
  await query.fill('zeta');
  assert.equal(await checkbox('zeta').isChecked(),true,'Conflict restores the canonical server selection');
  await query.fill('');await checkbox('gamma').click();await count(3).waitFor();await page.getByText('Выбор сохранён',{exact:true}).waitFor();
  await page.reload();await page.getByRole('button',{name:'Стримеры',exact:true}).click();await page.getByRole('button',{name:'Видео · 3/5',exact:true}).click();await count(3).waitFor();assert.equal(await checkbox('gamma').isChecked(),false);
  await checkbox('alpha').click();await count(4).waitFor();await page.getByText('Выбор сохранён',{exact:true}).waitFor();
  let mediaStatus='limited';
  await page.route('**/app/api/viewer/state',async route=>{const response=await route.fetch(),body=await response.json();body.subscriptions.find(row=>row.login==='alpha').video_delivery_status=mediaStatus;await route.fulfill({response,json:body});});
  // UI presentation of provider states is simulated; media pipeline uses separate fake-sender tests.
  for(const [value,label] of [
    ['limited','Перегрузка · пока показываем фото'],['unavailable','Видео недоступно · пока показываем фото'],
    ['preparing','Готовим видео · пока может показываться фото'],['unknown','Доставка видео пока не подтверждена'],
    ['photo','Сейчас фото · выбор видео сохранён'],['video','Видео в текущем сообщении'],['returning_photo','Возвращаем фото в текущее сообщение'],
  ]){mediaStatus=value;await page.evaluate(()=>document.dispatchEvent(new Event('visibilitychange')));await page.locator('[data-video-login="alpha"]').getByText(label,{exact:true}).waitFor();if(value==='limited')await capture('limited');}
  await page.unroute('**/app/api/viewer/state');
  await page.evaluate(()=>document.dispatchEvent(new Event('visibilitychange')));
  await page.locator('[data-video-login="alpha"]').getByText('Доставка видео пока не подтверждена',{exact:true}).waitFor();
  for(const width of [360,390,430,768,1440]){await page.setViewportSize({width,height:844});await assertLayout(page);}
  await page.setViewportSize({width:360,height:440});await page.evaluate(()=>document.documentElement.style.fontSize='200%');await assertLayout(page);
  assert((await page.locator('.video-choice').first().boundingBox()).height>=44,'Entire choice label is a touch target');
  await page.evaluate(()=>document.documentElement.style.fontSize='');await page.setViewportSize({width:390,height:844});
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

async function viewerSettingsJourney(page){
  const part=argument('part','quiet');
  await page.getByRole('heading',{name:'Главная',exact:true}).waitFor();
  if(part==='filter-folder')return filterFolderJourney(page);
  if(part==='category')return categoryJourney(page);
  if(part==='reminder')return reminderJourney(page);
  if(part==='history')return historyJourney(page);
  if(part==='pending')return settingsPendingJourney(page);
  if(part==='quiet-pending')return quietPendingJourney(page);
  if(part!=='quiet')throw new Error(`Settings part not implemented yet: ${part}`);
  await page.getByRole('button',{name:'Профиль',exact:true}).click();await page.getByRole('button',{name:'Уведомления',exact:true}).click();
  await page.getByRole('heading',{name:'Уведомления',exact:true}).waitFor();
  await page.getByLabel('Начало тихих часов',{exact:true}).fill('22:15');await page.getByLabel('Конец тихих часов',{exact:true}).fill('07:45');
  await page.getByRole('button',{name:'Сохранить тихие часы',exact:true}).click();await page.getByText('Тихие часы сохранены',{exact:true}).waitFor();
  assert.equal(await page.getByRole('checkbox',{name:'Сводка после тихих часов',exact:true}).isChecked(),true,'Free digest default is preserved');
  await page.getByRole('checkbox',{name:'Сводка после тихих часов',exact:true}).click();await page.getByText('Сводка выключена',{exact:true}).waitFor();
  await page.getByRole('checkbox',{name:'Сводка после тихих часов',exact:true}).click();await page.getByText('Сводка включена',{exact:true}).waitFor();
  const saved=await page.evaluate(async()=>{const {createApi}=await import('/app/api.js');return createApi(Telegram.WebApp.initData).post('/app/api/viewer/state');});
  assert.equal(saved.quiet_hours.digest_enabled,true);
  const time=await page.evaluate(()=>-new Date().getTimezoneOffset());
  assert.equal(saved.quiet_hours.start_minute,(22*60+15-time+1440)%1440);assert.equal(saved.quiet_hours.end_minute,(7*60+45-time+1440)%1440);
  await page.reload();await page.getByRole('button',{name:'Профиль',exact:true}).click();await page.getByRole('button',{name:'Уведомления',exact:true}).click();
  assert.equal(await page.getByLabel('Начало тихих часов',{exact:true}).inputValue(),'22:15');assert.equal(await page.getByRole('checkbox',{name:'Сводка после тихих часов',exact:true}).isChecked(),true);
  await page.getByRole('button',{name:'Выключить',exact:true}).click();await page.getByText('Тихие часы выключены',{exact:true}).waitFor();assert.equal(await page.getByRole('checkbox',{name:'Сводка после тихих часов',exact:true}).isEnabled(),false);
  const pictures=[await settingsPicture(page,'quiet-free')];
  await page.evaluate(()=>window.__qaSdk.back());await page.getByRole('heading',{name:'Профиль',exact:true}).waitFor();
  await assertLayout(page);return pictures;
}

async function filterFolderJourney(page){
  const pictures=[];
  const call=(path,values={})=>page.evaluate(async({path,values})=>{const {createApi}=await import('/app/api.js');return createApi(Telegram.WebApp.initData).post(`/app/api/viewer/${path}`,values);},{path,values});
  await page.getByRole('button',{name:'Стримеры',exact:true}).click();await page.getByRole('button',{name:'Папки',exact:true}).click();await page.getByRole('heading',{name:'Папки',exact:true}).waitFor();
  await page.getByRole('textbox',{name:'Название новой папки',exact:true}).fill('Игры и общение');await page.getByRole('button',{name:'Создать папку',exact:true}).click();
  await page.getByRole('heading',{name:'Игры и общение',exact:true}).waitFor();
  await page.getByRole('textbox',{name:'Категории',exact:true}).fill('Minecraft');await page.getByRole('button',{name:'Добавить категорию папки',exact:true}).click();
  await page.getByRole('button',{name:'Сохранить правило',exact:true}).click();await page.getByText('Правило сохранено',{exact:true}).waitFor();
  pictures.push(await settingsPicture(page,'folder-rule'));
  const folder=(await call('state')).folders.find(row=>row.name==='Игры и общение');assert.deepEqual(folder.games,['Minecraft']);
  await page.getByRole('button',{name:'Стримеры',exact:true}).click();await page.getByRole('button',{name:'Открыть Alpha',exact:true}).click();
  await page.getByRole('button',{name:'Папка',exact:true}).click();await page.getByRole('combobox',{name:'Папка стримера',exact:true}).selectOption(folder.id);
  await page.getByRole('button',{name:'Сохранить папку',exact:true}).click();await page.getByText('Папка сохранена',{exact:true}).waitFor();
  assert.equal((await call('state')).subscriptions.find(row=>row.login==='alpha').folder_id,folder.id);
  await page.evaluate(()=>window.__qaSdk.back());await page.getByRole('button',{name:'Фильтры уведомлений',exact:true}).click();await page.getByRole('heading',{name:'Фильтры',exact:true}).waitFor();
  await page.getByRole('button',{name:'Сохранить фильтр',exact:true}).click();await page.getByText('Фильтр сохранён',{exact:true}).waitFor();
  let row=(await call('state')).subscriptions.find(row=>row.login==='alpha');assert.deepEqual(row.filter.games,[],'Explicit empty personal rule overrides folder');
  await page.getByRole('textbox',{name:'Слова в названии',exact:true}).fill('Очень длинное слово для уведомления');await page.getByRole('button',{name:'Добавить слово',exact:true}).click();
  await call('filter',{login:'alpha',expected_version:row.filter.version,games:['Art'],title_keywords:[],exclude_keywords:[]});
  await page.getByRole('button',{name:'Сохранить фильтр',exact:true}).click();await page.getByText('Правило изменилось в другом окне. Проверьте значения и сохраните ещё раз.',{exact:true}).waitFor();
  await page.getByText('Очень длинное слово для уведомления',{exact:true}).waitFor();
  await page.getByRole('button',{name:'Сохранить фильтр',exact:true}).click();await page.getByText('Фильтр сохранён',{exact:true}).waitFor();
  row=(await call('state')).subscriptions.find(row=>row.login==='alpha');assert.deepEqual(row.filter.title_keywords,['Очень длинное слово для уведомления']);pictures.push(await settingsPicture(page,'filter-personal'));
  await page.getByRole('button',{name:'Использовать правило папки',exact:true}).click();await page.getByRole('heading',{name:'Alpha',exact:true}).waitFor();assert.equal((await call('state')).subscriptions.find(row=>row.login==='alpha').filter,null);
  await page.getByRole('button',{name:'Стримеры',exact:true}).click();await page.getByRole('button',{name:'Папки',exact:true}).click();await page.getByRole('button',{name:'Игры и общение · 1',exact:true}).click();
  await page.getByRole('textbox',{name:'Название папки',exact:true}).fill('Мой черновик');
  const current=(await call('state')).folders.find(row=>row.id===folder.id);await call('folder/rename',{folder_id:folder.id,name:'Другое окно',expected_version:current.version});
  await page.getByRole('button',{name:'Сохранить название',exact:true}).click();await page.getByText('Папка изменилась в другом окне. Черновик не сохранён.',{exact:true}).waitFor();assert.equal(await page.getByRole('textbox',{name:'Название папки',exact:true}).inputValue(),'Мой черновик');
  await page.getByRole('button',{name:'Загрузить текущую версию',exact:true}).click();assert.equal(await page.getByRole('textbox',{name:'Название папки',exact:true}).inputValue(),'Другое окно');
  page.once('dialog',prompt=>prompt.accept());await page.getByRole('button',{name:'Удалить папку',exact:true}).click();await page.getByRole('heading',{name:'Папки',exact:true}).waitFor();
  const final=await call('state');assert.equal(final.folders.length,0);assert.equal(final.subscriptions.find(row=>row.login==='alpha').folder_id,null);assert.equal(final.subscriptions.length,6);
  await assertLayout(page);return pictures;
}

async function categoryJourney(page){
  await page.getByRole('button',{name:'Стримеры',exact:true}).click();await page.getByRole('button',{name:'Открыть Alpha',exact:true}).click();await page.getByRole('button',{name:'Категории',exact:true}).click();
  await page.getByRole('heading',{name:'Категории',exact:true}).waitFor();
  const query=page.getByRole('searchbox',{name:'Найти категорию Twitch',exact:true});
  let release,started;const gate=new Promise(resolve=>{release=resolve;}),seen=new Promise(resolve=>{started=resolve;});
  await page.route('**/app/api/viewer/category-search',async route=>{const response=await route.fetch();started();await gate;try{await route.fulfill({response});}catch{} /* New query aborts the old request. */});
  await query.fill('Minecraft');await page.getByRole('button',{name:'Найти',exact:true}).click();await seen;
  await query.fill('Art');release();await page.waitForTimeout(100);
  assert.equal(await page.getByRole('button',{name:'Minecraft',exact:true}).count(),0,'A late category result cannot replace the new query');
  await page.unroute('**/app/api/viewer/category-search');
  await page.getByRole('button',{name:'Найти',exact:true}).click();await page.getByRole('button',{name:'Art',exact:true}).click();
  await page.getByRole('checkbox',{name:'Уведомлять о смене категории',exact:true}).check();await page.getByRole('button',{name:'Сохранить сигнал',exact:true}).click();await page.getByText('Настройка сохранена.',{exact:true}).waitFor();
  const state=await page.evaluate(async()=>{const {createApi}=await import('/app/api.js');return createApi(Telegram.WebApp.initData).post('/app/api/viewer/state');});
  assert.deepEqual(state.subscriptions.find(row=>row.login==='alpha').category_alert.category_ids,['300']);assert.equal(state.subscriptions.find(row=>row.login==='alpha').category_alert.enabled,true);
  await page.reload();await page.getByRole('button',{name:'Стримеры',exact:true}).click();await page.getByRole('button',{name:'Открыть Alpha',exact:true}).click();await page.getByRole('button',{name:'Категории',exact:true}).click();
  await page.getByRole('button',{name:'Убрать Art',exact:true}).waitFor();assert.equal(await page.getByRole('checkbox',{name:'Уведомлять о смене категории',exact:true}).isChecked(),true);
  await page.getByRole('button',{name:'Убрать Art',exact:true}).click();await page.getByRole('checkbox',{name:'Уведомлять о смене категории',exact:true}).uncheck();await page.getByRole('button',{name:'Сохранить сигнал',exact:true}).click();await page.getByText('Настройка сохранена.',{exact:true}).waitFor();
  const pictures=[await settingsPicture(page,'category-disabled')];await page.evaluate(()=>window.__qaSdk.back());await page.getByRole('heading',{name:'Alpha',exact:true}).waitFor();await assertLayout(page);return pictures;
}

async function settingsPicture(page,label){
  const file=`settings-${argument('engine','chromium')}-${label}-390.png`;
  await assertLayout(page);await page.screenshot({path:path.join(output,file),fullPage:false,animations:'disabled'});
  return {file,sha256:digest(path.join(output,file)),viewport:'390x844'};
}
async function viewerCall(page,path,values={}){
  return page.evaluate(async({path,values})=>{const {createApi}=await import('/app/api.js');try{return {ok:true,data:await createApi(Telegram.WebApp.initData).post(`/app/api/viewer/${path}`,values)};}catch(error){return {ok:false,status:error.status,code:error.code};}},{path,values});
}
async function reminderJourney(page){
  await page.getByRole('button',{name:'Стримеры',exact:true}).click();await page.getByRole('button',{name:'Открыть Alpha',exact:true}).click();await page.getByRole('button',{name:'Напоминание',exact:true}).click();
  await page.getByRole('heading',{name:'Напоминание',exact:true}).waitFor();
  if(argument('scenario','')==='reminder-inflight'){
    await page.getByText('Отправляем напоминание. Сейчас изменить его нельзя.',{exact:true}).waitFor();
    assert.equal(await page.getByRole('button',{name:'Через 15 минут',exact:true}).count(),0);assert.equal(await page.getByRole('button',{name:'Отменить напоминание',exact:true}).count(),0);
    for(const [path,values] of [['reminder',{login:'alpha',delay_minutes:30}],['reminder/cancel',{login:'alpha'}]])assert.deepEqual(await viewerCall(page,path,values),{ok:false,status:409,code:'reminder_in_flight'});
    await page.reload();await page.getByRole('button',{name:'Стримеры',exact:true}).click();await page.getByRole('button',{name:'Открыть Alpha',exact:true}).click();await page.getByRole('button',{name:'Напоминание',exact:true}).click();
    await page.getByText('Отправляем напоминание. Сейчас изменить его нельзя.',{exact:true}).waitFor();
    return [await settingsPicture(page,'reminder-sending')];
  }
  await page.getByRole('button',{name:'Через 15 минут',exact:true}).click();await page.getByRole('button',{name:'Отменить напоминание',exact:true}).waitFor();
  let state=(await viewerCall(page,'state')).data;let saved=state.subscriptions.find(row=>row.login==='alpha').reminder;assert.equal(saved.delay_minutes,15);assert.equal(saved.status,'scheduled');
  assert.equal(await page.locator('time[data-reminder-due]').getAttribute('datetime'),new Date(saved.due_at*1000).toISOString(),'The actual saved due time is visible');
  let release,observed;const held=new Promise(resolve=>release=resolve),seen=new Promise(resolve=>observed=resolve);
  await page.route('**/app/api/viewer/reminder',async route=>{observed();await held;await route.continue();});
  await page.getByRole('button',{name:'Через 30 минут',exact:true}).click();await seen;
  assert.equal(await page.getByRole('button',{name:'Через 15 минут',exact:true}).isDisabled(),true);assert.equal(await page.getByRole('button',{name:'Отменить напоминание',exact:true}).isDisabled(),true);
  release();await page.unroute('**/app/api/viewer/reminder');await page.getByText('Запланировано через 30 минут от выбора. Если эфир закончится, сообщение не придёт.',{exact:true}).waitFor();
  state=(await viewerCall(page,'state')).data;saved=state.subscriptions.find(row=>row.login==='alpha').reminder;assert.equal(saved.delay_minutes,30);
  const pictures=[await settingsPicture(page,'reminder-scheduled')];
  await page.reload();await page.getByRole('button',{name:'Стримеры',exact:true}).click();await page.getByRole('button',{name:'Открыть Alpha',exact:true}).click();await page.getByRole('button',{name:'Напоминание',exact:true}).click();
  await page.getByRole('button',{name:'Отменить напоминание',exact:true}).click();await page.getByText('Напоминание отменено.',{exact:true}).waitFor();assert.equal((await viewerCall(page,'state')).data.subscriptions.find(row=>row.login==='alpha').reminder.status,'cancelled');
  pictures.push(await settingsPicture(page,'reminder-cancelled'));await page.evaluate(()=>window.__qaSdk.back());await page.getByRole('heading',{name:'Alpha',exact:true}).waitFor();return pictures;
}
async function historyJourney(page){
  await page.getByRole('button',{name:'Профиль',exact:true}).click();
  await page.getByRole('button',{name:'История уведомлений',exact:true}).click();
  await page.getByRole('heading',{name:'История уведомлений',exact:true}).waitFor();
  if(argument('scenario','')==='free-six'){
    await page.getByText('Viewer Plus неактивен',{exact:true}).waitFor();assert.equal(await page.locator('[data-history-event]').count(),0);
    await page.getByRole('button',{name:'Посмотреть доступ',exact:true}).click();await page.getByRole('heading',{name:'Возможности Plus',exact:true}).waitFor();await page.evaluate(()=>window.__qaSdk.back());await page.evaluate(()=>window.__qaSdk.back());
    await page.getByRole('heading',{name:'Профиль',exact:true}).waitFor();await page.getByRole('button',{name:'Уведомления',exact:true}).click();await page.getByRole('button',{name:'Сохранить тихие часы',exact:true}).waitFor();return [await settingsPicture(page,'free-notifications')];
  }
  await page.locator('[data-history-event]').first().waitFor();assert.equal(await page.locator('#content h1').count(),1);
  assert.equal(await page.locator('[data-history-event]').count(),20);assert.equal(await page.getByText(/foreign/).count(),0);
  for(const label of ['Отправлено','Не отправлено','Доставка не подтверждена'])assert(await page.getByText(new RegExp(label)).count()>0);
  const pictures=[await settingsPicture(page,'history-page-one')];
  await page.route('**/app/api/viewer/history',route=>route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({error:'temporary_network'})}));
  await page.getByRole('button',{name:'Показать ещё',exact:true}).click();await page.getByText('Не удалось загрузить историю. Попробуйте ещё раз.',{exact:true}).waitFor();assert.equal(await page.locator('[data-history-event]').count(),20);
  await page.unroute('**/app/api/viewer/history');await page.getByRole('button',{name:'Показать ещё',exact:true}).click();await page.waitForFunction(()=>document.querySelectorAll('[data-history-event]').length===25);
  assert.equal(new Set(await page.locator('[data-history-event]').evaluateAll(nodes=>nodes.map(node=>node.dataset.historyEvent))).size,25);assert.equal(await page.getByRole('button',{name:'Показать ещё',exact:true}).count(),0);
  pictures.push(await settingsPicture(page,'history-complete'));
  await page.getByRole('button',{name:'Обновить историю',exact:true}).click();await page.waitForFunction(()=>document.querySelectorAll('[data-history-event]').length===20);
  let release,observed;const held=new Promise(resolve=>release=resolve),seen=new Promise(resolve=>observed=resolve);
  await page.route('**/app/api/viewer/history',async route=>{if(route.request().postDataJSON().before_id){const response=await route.fetch();observed();await held;try{await route.fulfill({response});}catch{}}else await route.continue();});
  await page.getByRole('button',{name:'Показать ещё',exact:true}).click();await seen;await page.getByRole('button',{name:'Обновить историю',exact:true}).click();await page.waitForFunction(()=>document.querySelectorAll('[data-history-event]').length===20);release();await page.waitForTimeout(150);
  assert.equal(await page.locator('[data-history-event]').count(),20,'A late page cannot append to a refreshed history');await page.unroute('**/app/api/viewer/history');
  await page.evaluate(()=>window.__qaSdk.back());await page.getByRole('heading',{name:'Профиль',exact:true}).waitFor();await page.getByRole('button',{name:'Стример',exact:true}).click();await page.getByRole('button',{name:'Профиль',exact:true}).click();await page.getByRole('button',{name:'История уведомлений',exact:true}).click();await page.getByRole('heading',{name:'История уведомлений',exact:true}).waitFor();await page.locator('[data-history-event]').first().waitFor();assert.equal(await page.locator('#content h1').count(),1);
  await page.reload();await page.getByRole('button',{name:'Профиль',exact:true}).click();await page.getByRole('button',{name:'История уведомлений',exact:true}).click();await page.locator('[data-history-event]').first().waitFor();assert.equal(await page.locator('[data-history-event]').count(),20);return pictures;
}

async function heldMutation(page,path,click,verify){
  let release,observed;const held=new Promise(resolve=>release=resolve),seen=new Promise(resolve=>observed=resolve);
  let calls=0;await page.route(`**/app/api/viewer/${path}`,async route=>{calls++;observed();await held;try{await route.continue();}catch{}});
  let timer;try{await click();await Promise.race([seen,new Promise((_,reject)=>timer=setTimeout(()=>reject(new Error(`Expected mutation did not start: ${path}`)),10000))]);await verify();assert.equal(calls,1);}finally{clearTimeout(timer);release();await page.unroute(`**/app/api/viewer/${path}`);}
}
async function quietPendingJourney(page){
  await page.getByRole('button',{name:'Профиль',exact:true}).click();await page.getByRole('button',{name:'Уведомления',exact:true}).click();await page.getByLabel('Начало тихих часов',{exact:true}).fill('21:00');await page.getByLabel('Конец тихих часов',{exact:true}).fill('08:00');
  await heldMutation(page,'quiet-hours',()=>page.getByRole('button',{name:'Сохранить тихие часы',exact:true}).click(),async()=>{assert.equal(await page.getByLabel('Начало тихих часов',{exact:true}).isDisabled(),true);assert.equal(await page.getByRole('button',{name:'Сохранить тихие часы',exact:true}).isDisabled(),true);});
  await page.getByText('Тихие часы сохранены',{exact:true}).waitFor();assert.equal(await page.getByLabel('Начало тихих часов',{exact:true}).isEnabled(),true);
  assert.equal(await page.getByRole('checkbox',{name:'Сводка после тихих часов',exact:true}).isChecked(),true,'New Free quiet-hours preserve enabled digest default');
  await page.getByRole('checkbox',{name:'Сводка после тихих часов',exact:true}).click();await page.getByText('Сводка выключена',{exact:true}).waitFor();assert.equal((await viewerCall(page,'state')).data.quiet_hours.digest_enabled,false);
  await heldMutation(page,'digest',()=>page.getByRole('checkbox',{name:'Сводка после тихих часов',exact:true}).click(),async()=>{assert.equal(await page.getByLabel('Начало тихих часов',{exact:true}).isDisabled(),true);assert.equal(await page.getByRole('button',{name:'Выключить',exact:true}).isDisabled(),true);});
  await page.getByText('Сводка включена',{exact:true}).waitFor();assert.equal(await page.getByLabel('Начало тихих часов',{exact:true}).isEnabled(),true);assert.equal((await viewerCall(page,'state')).data.quiet_hours.digest_enabled,true);return [await settingsPicture(page,'quiet-digest-pending-complete')];
}

async function settingsPendingJourney(page){
  await page.getByRole('button',{name:'Стримеры',exact:true}).click();await page.getByRole('button',{name:'Папки',exact:true}).click();await page.getByRole('textbox',{name:'Название новой папки',exact:true}).fill('Правило с ожиданием');
  await heldMutation(page,'folder/create',()=>page.getByRole('button',{name:'Создать папку',exact:true}).click(),async()=>{assert.equal(await page.getByRole('textbox',{name:'Название новой папки',exact:true}).isDisabled(),true);assert.equal(await page.getByRole('button',{name:'Создать папку',exact:true}).isDisabled(),true);});
  await page.getByRole('heading',{name:'Правило с ожиданием',exact:true}).waitFor();await page.getByRole('textbox',{name:'Категории',exact:true}).fill('Minecraft');await page.getByRole('button',{name:'Добавить категорию папки',exact:true}).click();
  await heldMutation(page,'folder/rule',()=>page.getByRole('button',{name:'Сохранить правило',exact:true}).click(),async()=>{assert.equal(await page.getByRole('textbox',{name:'Название папки',exact:true}).isDisabled(),true);assert.equal(await page.getByRole('button',{name:'Сохранить правило',exact:true}).isDisabled(),true);assert.equal(await page.getByRole('button',{name:'Убрать Minecraft из категории',exact:true}).isDisabled(),true);});
  await page.getByText('Правило сохранено',{exact:true}).waitFor();assert.equal(await page.getByRole('textbox',{name:'Название папки',exact:true}).isEnabled(),true);
  await page.getByRole('button',{name:'Стримеры',exact:true}).click();await page.getByRole('button',{name:'Открыть Alpha',exact:true}).click();await page.getByRole('button',{name:'Папка',exact:true}).click();
  const folder=(await viewerCall(page,'state')).data.folders.find(row=>row.name==='Правило с ожиданием');await page.getByRole('combobox',{name:'Папка стримера',exact:true}).selectOption(folder.id);
  await heldMutation(page,'folder/move',()=>page.getByRole('button',{name:'Сохранить папку',exact:true}).click(),async()=>{assert.equal(await page.getByRole('combobox',{name:'Папка стримера',exact:true}).isDisabled(),true);assert.equal(await page.getByRole('button',{name:'Сохранить папку',exact:true}).isDisabled(),true);});
  await page.getByText('Папка сохранена',{exact:true}).waitFor();assert.equal((await viewerCall(page,'state')).data.subscriptions.find(row=>row.login==='alpha').folder_id,folder.id);await page.evaluate(()=>window.__qaSdk.back());await page.getByRole('button',{name:'Фильтры уведомлений',exact:true}).click();
  await page.getByRole('textbox',{name:'Слова в названии',exact:true}).fill('общение');await page.getByRole('button',{name:'Добавить слово',exact:true}).click();
  await heldMutation(page,'filter',()=>page.getByRole('button',{name:'Сохранить фильтр',exact:true}).click(),async()=>{assert.equal(await page.getByRole('textbox',{name:'Слова в названии',exact:true}).isDisabled(),true);assert.equal(await page.getByRole('button',{name:'Сохранить фильтр',exact:true}).isDisabled(),true);});
  await page.getByText('Фильтр сохранён',{exact:true}).waitFor();assert.deepEqual((await viewerCall(page,'state')).data.subscriptions.find(row=>row.login==='alpha').filter.title_keywords,['общение']);
  assert.equal(await page.getByRole('textbox',{name:'Слова в названии',exact:true}).isEnabled(),true);
  await page.evaluate(()=>window.__qaSdk.back());await page.getByRole('button',{name:'Категории',exact:true}).click();
  await heldMutation(page,'category-alert',()=>page.getByRole('button',{name:'Сохранить сигнал',exact:true}).click(),async()=>{assert.equal(await page.getByRole('button',{name:'Сохранить сигнал',exact:true}).isDisabled(),true);assert.equal(await page.getByRole('checkbox',{name:'Уведомлять о смене категории',exact:true}).isDisabled(),true);});
  await page.getByText('Настройка сохранена.',{exact:true}).waitFor();assert.equal(await page.getByRole('button',{name:'Сохранить сигнал',exact:true}).isEnabled(),true);return [await settingsPicture(page,'category-saved')];
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
      ['viewer', async () => { await page.getByRole('button',{name:'Зритель',exact:true}).click();await page.getByRole('button',{name:'Стримеры',exact:true}).click();await page.getByRole('heading',{name:'Стримеры',exact:true}).waitFor();await page.locator('#content[data-viewer-state="ready"]').waitFor(); }],
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
    for(const source of ['bot/mini_app_viewer.py','scripts/mini_app_browser_fixture.py','scripts/mini_app_redesign_browser_qa.cjs'])report.sources[source]=digest(path.join(root,source));
    fs.writeFileSync(path.join(output, `${journey}-${engineName}-qa.json`), JSON.stringify(report, null, 2));
    await browser?.close();
    await server.close();
    console.log(JSON.stringify({ status: report.status, engine: engineName, screenshots: report.screenshots.length, errors: report.errors.length }));
  }
}

module.exports = { runJourney, assertLayout, installSdk, fixtureServer, signedIdentity };
if (require.main === module) main().catch(error => { console.error(error.message); process.exitCode = 1; });
