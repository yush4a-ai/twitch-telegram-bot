/* P18: bounded actual-app matrix, sequential fresh fixtures and native browser zoom.
 * Telegram SDK/identity and outbound sender remain local doubles, not device evidence. */
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),os=require('node:os'),crypto=require('node:crypto');
const {spawn,execFileSync}=require('node:child_process');
const digest=file=>crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
const scenarios=['free-empty','free-six','free-two-hundred','viewer-empty','viewer-six','plus-two-hundred','streamer-empty','streamer-plus','streamer-two-hundred'];
const themes=['light','dark','telegram-light','telegram-dark'];
const widths=[360,390,430,768,1440];
const frames=page=>page.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));

async function setTheme(page,choice){
  await page.evaluate(async choice=>{
    const {theme}=await import('/app/app.js');
    if(choice.startsWith('telegram-')){
      const sdk=Telegram.WebApp,dark=choice.endsWith('dark');sdk.colorScheme=dark?'dark':'light';
      sdk.themeParams=dark?{bg_color:'#211d18',text_color:'#f6ecdc',button_color:'#e4c9a0',button_text_color:'#211d18',link_color:'#e4c9a0'}:
        {bg_color:'#fff5e5',text_color:'#302517',button_color:'#804000',button_text_color:'#ffffff',link_color:'#805500',header_bg_color:'#efe5d5',bottom_bar_bg_color:'#e8dcc8',section_bg_color:'#ffffff',hint_color:'#604d38',section_separator_color:'#c8b79d'};
      for(const fn of window.__qaSdk.events.get('themeChanged'))fn();theme.setChoice('telegram');
    }else theme.setChoice(choice);
  },choice);await frames(page);
}

async function screen(page,mode,tab){
  await page.getByRole('button',{name:mode==='viewer'?'Зритель':'Стример',exact:true}).click();
  await page.locator('#tab-bar').getByRole('button',{name:tab,exact:true}).click();
  if(tab==='Тариф')await page.locator('.plus-benefits').waitFor();
  else if(tab==='Профиль')await page.locator('#content[data-profile-state="ready"]').waitFor();
  else if(mode==='viewer')await page.locator('#content[data-viewer-state="ready"]').waitFor();
  else await page.getByRole('heading',{name:tab,exact:true}).waitFor();
  await frames(page);
}

async function accessible(page){
  const result=await page.evaluate(()=>{
    const visible=node=>{const r=node.getBoundingClientRect();return r.width>0&&r.height>0&&getComputedStyle(node).visibility!=='hidden';};
    const unnamed=[...document.querySelectorAll('button,a,input:not([type=hidden]),select,textarea')].filter(visible).filter(node=>{
      if(node.getAttribute('aria-label')?.trim()||node.getAttribute('aria-labelledby')?.split(' ').some(id=>document.getElementById(id)?.textContent.trim()))return false;
      if(node.labels&&[...node.labels].some(label=>label.textContent.trim()))return false;
      return !node.textContent.trim();
    }).map(node=>node.outerHTML.slice(0,200));
    const css=getComputedStyle(document.documentElement),probe=document.createElement('span');document.body.append(probe);
    const color=token=>{probe.style.color=css.getPropertyValue('--'+token);return getComputedStyle(probe).color.match(/[\d.]+/g).slice(0,3).map(Number);};
    const colors=Object.fromEntries(['text','canvas','secondary','surface','button-bg','button-fg'].map(k=>[k,color(k)]));probe.remove();
    const content=document.getElementById('content').innerText;
    const smallTargets=[...document.querySelectorAll('button,a.text-link,summary')].filter(visible).filter(node=>{const r=node.getBoundingClientRect();return r.width<44||r.height<44;}).map(node=>({label:node.getAttribute('aria-label')||node.textContent.trim(),width:node.getBoundingClientRect().width,height:node.getBoundingClientRect().height}));
    return {unnamed,smallTargets,colors,forbidden:/\b(?:demo|mock|prototype|staging)\b/i.test(content),nav:[...document.querySelectorAll('#tab-bar button')].map(b=>b.getAttribute('aria-label'))};
  });
  assert.deepEqual(result.unnamed,[],'Every visible control has an accessible name');assert(!result.forbidden,'No technical labels in ordinary UI');
  assert.deepEqual(result.smallTargets,[],'Standalone actions have at least 44px touch targets');
  const luminance=rgb=>rgb.map(v=>v/255).map(v=>v<=.04045?v/12.92:((v+.055)/1.055)**2.4).reduce((a,v,i)=>a+v*[.2126,.7152,.0722][i],0);
  const contrasts={};for(const [fg,bg] of [['text','canvas'],['secondary','canvas'],['secondary','surface'],['button-fg','button-bg']]){
    const values=[luminance(result.colors[fg]),luminance(result.colors[bg])];const ratio=(Math.max(...values)+.05)/(Math.min(...values)+.05);contrasts[`${fg}/${bg}`]=ratio;assert(ratio>=4.5,`${fg}/${bg} contrast ${ratio}`);
  }return {nav:result.nav,contrasts};
}

async function focusRegression(page,picture){
  // One-row order history uses this actual shared component and clipping group.
  await page.evaluate(async()=>{const {navigationRow}=await import('/app/components.js');const box=document.createElement('section');box.id='qa-focus-regression';box.className='navigation-group';Object.assign(box.style,{position:'fixed',zIndex:30,top:'180px',left:'16px',width:'300px'});box.append(navigationRow('Viewer Plus','Ожидается подтверждение','history',()=>{}));document.body.append(box);document.activeElement?.blur();});
  await picture('focus-before');await page.locator('#qa-focus-regression button').focus();await page.keyboard.press('Shift');
  const m=await page.locator('#qa-focus-regression button').evaluate(b=>({active:b===document.activeElement,visible:b.matches(':focus-visible'),offset:parseFloat(getComputedStyle(b).outlineOffset),width:parseFloat(getComputedStyle(b).outlineWidth)}));
  assert(m.active&&m.visible,'Keyboard focus is active');assert(m.offset+m.width<=0,'Group cannot clip its entire keyboard focus outline');await picture('focus-after');
  await page.locator('#qa-focus-regression').evaluate(node=>node.remove());
}

async function matrixJourney(page,{root,output,engine,scenario,assertLayout}){
  const pictures=[],checks=[];
  const picture=async label=>{const file=`matrix-${engine}-${label}.png`;await page.screenshot({path:path.join(output,file),animations:'disabled'});pictures.push({file,sha256:digest(path.join(output,file))});};
  await page.getByRole('heading',{name:'Главная',exact:true}).waitFor();await focusRegression(page,picture);
  assert.notEqual(pictures[0].sha256,pictures[1].sha256,'Focused single row has a visible pixel change');
  await page.emulateMedia({reducedMotion:'reduce'});assert(await page.evaluate(()=>matchMedia('(prefers-reduced-motion: reduce)').matches));
  assert.equal(await page.evaluate(()=>getComputedStyle(document.documentElement).scrollBehavior),'auto');assert.equal(await page.locator('#tab-bar button').first().evaluate(b=>getComputedStyle(b).transitionDuration),'0s');await page.emulateMedia({reducedMotion:'no-preference'});
  const expectedCount=scenario.includes('empty')?0:scenario.includes('hundred')?200:6;
  for(const width of widths)for(const theme of themes){
    await page.setViewportSize({width,height:844});await setTheme(page,theme);
    for(const [mode,tab] of [['viewer','Главная'],['viewer','Стримеры'],['viewer','Тариф'],['streamer','Мой канал'],['streamer','Посты'],['streamer','Тариф'],['viewer','Профиль']]){
      await screen(page,mode,tab);await assertLayout(page);const a=await accessible(page);
      assert.deepEqual(a.nav,mode==='viewer'?['Главная','Стримеры','Профиль','Тариф']:['Мой канал','Посты','Профиль','Тариф']);
      if(tab==='Стримеры'){assert.equal(await page.locator('.streamer-row').count(),expectedCount);if(expectedCount){assert(await page.locator('[data-row-key="beta"]').innerText().then(t=>t.length>80),'Long names and stale status are present');}}
      if(tab==='Тариф'){assert.equal(await page.locator('.plus-benefit').count(),4);assert.equal(await page.locator('.plus-offer').getAttribute('data-product'),mode==='viewer'?'viewer_plus':'streamer_plus');await page.getByText(mode==='viewer'?'150 ₽ / месяц':'300 ₽ / месяц',{exact:true}).waitFor();assert.equal(await page.getByText('200 ₽ / месяц',{exact:true}).count(),0);}
      checks.push({width,theme,mode,tab,...a});
      if(width===390&&(tab==='Стримеры'||tab==='Тариф'))await picture(`${theme}-${mode}-${tab==='Тариф'?'plus':'streamers'}`);
      if(width===1440&&theme==='light'&&tab==='Стримеры')await picture('desktop-streamers');
    }
  }
  await setTheme(page,'light');
  for(const width of [360,390]){
    await page.setViewportSize({width,height:844});await page.evaluate(()=>document.documentElement.style.fontSize='200%');
    for(const [mode,tab] of [['viewer','Стримеры'],['viewer','Тариф'],['streamer','Тариф'],['viewer','Профиль']]){await screen(page,mode,tab);await assertLayout(page);await accessible(page);await picture(`text200-${width}-${mode}-${tab==='Тариф'?'plus':tab==='Профиль'?'profile':'streamers'}`);}
    await page.evaluate(()=>document.documentElement.style.fontSize='');
  }
  await page.setViewportSize({width:390,height:440});for(const [mode,tab] of [['viewer','Стримеры'],['streamer','Тариф']]){await screen(page,mode,tab);await assertLayout(page);await picture(`short-${mode}`);}
  fs.writeFileSync(path.join(output,`matrix-${engine}-checks.json`),JSON.stringify({scenario,checks,text200Widths:[360,390],shortViewport:'390x440',status:'PASS'},null,2));
  await page.setViewportSize({width:390,height:844});return pictures;
}

function sources(root){const names=execFileSync('git',['ls-files','--cached','--others','--exclude-standard','--','bot','main.py','requirements.txt','scripts','docs/legal'],{cwd:root,encoding:'utf8',windowsHide:true}).trim().split(/\r?\n/).filter(Boolean);return Object.fromEntries([...new Set(names)].sort().map(n=>[n,digest(path.join(root,n))]));}
function verifyLeaf(dir,journey,engine,expectedSources){
  const file=path.join(dir,`${journey}-${engine}-qa.json`),report=JSON.parse(fs.readFileSync(file));assert.equal(report.status,'PASS');assert.deepEqual(report.errors,[]);assert.deepEqual(report.externalRequests,[]);
  assert.deepEqual(report.sources,expectedSources,'Leaf report proves the complete frozen runtime snapshot');
  assert.equal(new Set(report.screenshots.map(s=>s.file)).size,report.screenshots.length);for(const s of report.screenshots)assert.equal(digest(path.join(dir,s.file)),s.sha256);
  return {report:path.basename(file),sha256:digest(file),screenshots:report.screenshots.length};
}
async function runAll({root,output,engine}){
  assert(!process.env.MINI_APP_QA_URL,'Full gate needs a fresh temporary fixture per journey');
  const cases=scenarios.map(s=>['matrix',s]);
  cases.push(['zoom','free-six'],['zoom','streamer-two-hundred'],['video-windows','plus-two-hundred'],['theme','free-six'],['shell','free-six'],['shell','plus-two-hundred']);
  for(const j of ['viewer-free','video'])for(const s of ['free-empty','free-six','plus-two-hundred'])cases.push([j,s]);
  for(const [part,scs] of [['quiet',['free-six']],['filter-folder',['plus-two-hundred','free-six']],['category',['plus-two-hundred','free-six']],['reminder',['plus-two-hundred','free-six','reminder-inflight']],['history',['history','free-six']],['pending',['plus-two-hundred']],['quiet-pending',['free-six']]])for(const s of scs)cases.push(['viewer-settings',s,part]);
  for(const s of ['channel-permissions','streamer-unconnected','channel-empty','legacy-group'])cases.push(['streamer-connection',s]);
  cases.push(['oauth-results','channel-permissions'],['streamer-posts','streamer-posts'],['streamer-posts','free-six'],['purchase','free-six'],['purchase','streamer-plus'],['purchase','purchase-history'],['legal','legal-unready'],['legal','legal-ready']);
  const manifest={engine,native:'NOT TESTED',sources:sources(root),cases:[],status:'RUNNING'};const write=()=>fs.writeFileSync(path.join(output,`all-${engine}-qa.json`),JSON.stringify(manifest,null,2));write();
  try{for(const [journey,scenario,part] of cases){
    const name=[journey,scenario,part].filter(Boolean).join('-'),dir=path.join(output,name);fs.mkdirSync(dir,{recursive:true});
    const args=[path.join(root,'scripts/mini_app_redesign_browser_qa.cjs'),'--journey',journey,'--scenario',scenario,'--engine',engine];if(part)args.push('--part',part);
    const result=await new Promise(resolve=>{const child=spawn(process.execPath,args,{cwd:root,env:{...process.env,MINI_APP_QA_SCREENSHOTS:dir},stdio:['ignore','pipe','pipe'],windowsHide:true});let log='',done=false,timedOut=false;const finish=value=>{if(done)return;done=true;clearTimeout(timer);resolve({...value,code:timedOut?-1:value.code});};const timer=setTimeout(()=>{timedOut=true;log+='\nOwned QA process exceeded 180s; stopped with its owned fixture/browser tree.';const kill=spawn('taskkill',['/PID',String(child.pid),'/T','/F'],{windowsHide:true,stdio:'ignore'});kill.once('exit',()=>finish({code:-1,log}));kill.once('error',()=>{child.kill();finish({code:-1,log});});},180000);child.stdout.on('data',b=>{log=(log+b).slice(-5000);});child.stderr.on('data',b=>{log=(log+b).slice(-5000);});child.once('error',e=>finish({code:-1,log:String(e)}));child.once('exit',code=>finish({code,log}));});
    fs.writeFileSync(path.join(dir,'runner.log'),result.log);assert.equal(result.code,0,`${name}: ${result.log}`);
    manifest.cases.push({journey,scenario,part:part||null,directory:name,...verifyLeaf(dir,journey,engine,manifest.sources)});write();console.log(JSON.stringify({engine,complete:manifest.cases.length,total:cases.length,case:name}));
  }assert.deepEqual(sources(root),manifest.sources,'Sources did not change during the full browser gate');manifest.status='PASS';}
  catch(error){manifest.status='FAIL';manifest.failure=error.stack;throw error;}finally{write();}
}

async function zoomJourney({root,output,engine,scenario,fixtureServer,installSdk,assertLayout}){
  const core=require(path.join(path.dirname(require.resolve('playwright-core/package.json')),'lib/coreBundle.js'));
  const pw=core.inprocess.createInProcessPlaywright(),server=await fixtureServer(scenario),temp=fs.mkdtempSync(path.join(os.tmpdir(),'twitchsignal-p18-zoom-'));
  const report={journey:'zoom',scenario,engine,native:'NOT TESTED',screenshots:[],errors:[],externalRequests:[],sources:sources(root)};let context,zoom;
  try{
    if(engine==='chromium'){
      const extension=path.join(temp,'extension');fs.mkdirSync(extension);fs.writeFileSync(path.join(extension,'manifest.json'),JSON.stringify({manifest_version:3,name:'Local TwitchSignal zoom QA',version:'1.0',host_permissions:['http://127.0.0.1/*'],background:{service_worker:'worker.js'}}));fs.writeFileSync(path.join(extension,'worker.js'),'chrome.runtime.onInstalled.addListener(()=>{});');
      context=await pw.chromium.launchPersistentContext(path.join(temp,'profile'),{headless:false,viewport:null,channel:'chromium',args:[`--disable-extensions-except=${extension}`,`--load-extension=${extension}`,'--window-size=1000,900']});
      const worker=context.serviceWorkers()[0]||await context.waitForEvent('serviceworker');zoom=async factor=>worker.evaluate(async({url,factor})=>{const tabs=await chrome.tabs.query({url});if(tabs.length!==1)throw new Error('Exactly one own tab required');await chrome.tabs.setZoomSettings(tabs[0].id,{mode:'automatic',scope:'per-tab'});await chrome.tabs.setZoom(tabs[0].id,factor);return{zoom:await chrome.tabs.getZoom(tabs[0].id),settings:await chrome.tabs.getZoomSettings(tabs[0].id)};},{url:server.url,factor});
    }else context=await pw.webkit.launchPersistentContext(path.join(temp,'profile'),{headless:false,viewport:null});
    const page=context.pages()[0]||await context.newPage(),origin=new URL(server.url).origin;
    page.on('pageerror',e=>report.errors.push(e.message));await page.route('**/*',r=>{const u=new URL(r.request().url());if(u.hostname==='telegram.org')return r.fulfill({status:200,contentType:'application/javascript',body:''});if(u.origin===origin)return r.continue();report.externalRequests.push(u.origin+u.pathname);return r.abort();});await installSdk(page);await page.goto(server.url);await page.getByRole('heading',{name:'Главная',exact:true}).waitFor();
    if(engine==='webkit'){const bi=pw._connection.toImpl(context.browser()),pi=pw._connection.toImpl(page);zoom=async factor=>{await bi._browserSession.send('Playwright.setPageZoomFactor',{pageProxyId:pi.delegate._pageProxySession.sessionId,zoomFactor:factor});return{zoom:factor,method:'Playwright.setPageZoomFactor'};};}
    const measure=()=>page.evaluate(()=>({width:innerWidth,height:innerHeight,dpr:devicePixelRatio,visualScale:visualViewport.scale,rootZoom:getComputedStyle(document.documentElement).zoom,bodyZoom:getComputedStyle(document.body).zoom,rootTransform:getComputedStyle(document.documentElement).transform,bodyTransform:getComputedStyle(document.body).transform}));
    report.baseline=await measure();report.control=await zoom(2);await page.waitForFunction(w=>innerWidth<=w/1.8,report.baseline.width);await frames(page);report.zoomed=await measure();
    assert.equal(report.control.zoom,2);assert.equal(report.zoomed.dpr,report.baseline.dpr*2);assert(Math.abs(report.baseline.width/report.zoomed.width-2)<.02);assert.equal(report.zoomed.visualScale,1);for(const k of ['rootZoom','bodyZoom'])assert.equal(report.zoomed[k],'1');for(const k of ['rootTransform','bodyTransform'])assert.equal(report.zoomed[k],'none');
    report.version=context.browser().version();
    for(const [mode,tab,label] of [['viewer','Стримеры','streamers'],['viewer','Тариф','viewer-plus'],['streamer','Мой канал','channel'],['streamer','Посты','posts'],['streamer','Тариф','streamer-plus'],['viewer','Профиль','profile']]){
      await screen(page,mode,tab);await assertLayout(page);await accessible(page);let bytes;
      if(engine==='chromium'){const cdp=await context.newCDPSession(page);try{const result=await cdp.send('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});bytes=Buffer.from(result.data,'base64');}finally{await cdp.detach();}}
      else{const pi=pw._connection.toImpl(page);const result=await pi.delegate._session.send('Page.snapshotRect',{x:0,y:0,width:report.baseline.width,height:report.baseline.height,coordinateSystem:'Viewport',omitDeviceScaleFactor:false});bytes=Buffer.from(result.dataURL.split(',')[1],'base64');}
      const file=`zoom-${engine}-${label}-200.png`;fs.writeFileSync(path.join(output,file),bytes);report.screenshots.push({file,sha256:digest(path.join(output,file)),browserZoom:2,physicalViewport:`${report.baseline.width}x${report.baseline.height}`,cssViewport:`${report.zoomed.width}x${report.zoomed.height}`});
      assert.equal(bytes.readUInt32BE(16),report.baseline.width,'Full physical viewport PNG width');assert.equal(bytes.readUInt32BE(20),report.baseline.height,'Full physical viewport PNG height');
    }
    await zoom(1);await page.waitForFunction(w=>innerWidth===w,report.baseline.width);report.reset=await measure();assert.deepEqual(report.reset,report.baseline,'Browser zoom resets exactly');assert.deepEqual(report.errors,[]);assert.deepEqual(report.externalRequests,[]);report.status='PASS';
  }catch(error){report.status='FAIL';report.failure=error.stack;throw error;}finally{fs.writeFileSync(path.join(output,`zoom-${engine}-qa.json`),JSON.stringify(report,null,2));await context?.close();await server.close();console.log(JSON.stringify({engine,journey:'zoom',status:report.status,screenshots:report.screenshots.length}));}
}
async function videoWindowsJourney(page,{root,output,engine,installSdk,assertLayout}){
  const other=await page.context().newPage(),origin=new URL(page.url()).origin,external=[],errors=[],pictures=[];let release;
  other.on('pageerror',e=>errors.push(e.message));await other.route('**/*',r=>{const u=new URL(r.request().url());if(u.hostname==='telegram.org')return r.fulfill({contentType:'application/javascript',body:''});if(u.origin===origin)return r.continue();external.push(u.origin+u.pathname);return r.abort();});await installSdk(other);
  const open=async p=>{await p.getByRole('button',{name:'Стримеры',exact:true}).click();await p.getByRole('button',{name:'Видео · 3/5',exact:true}).click();await p.getByRole('heading',{name:'Видеопревью',exact:true}).waitFor();};
  const canonical=()=>page.evaluate(async()=>{const {createApi}=await import('/app/api.js');return (await createApi(Telegram.WebApp.initData).post('/app/api/viewer/state')).video_selection;});
  try{
    await page.getByRole('heading',{name:'Главная',exact:true}).waitFor();await open(page);await other.goto(page.url());await other.getByRole('heading',{name:'Главная',exact:true}).waitFor();await open(other);
    const original=await canonical();assert.equal(original.selected_logins.length,3);
    let seen;const held=new Promise(r=>release=r),started=new Promise(r=>seen=r);let frozen;
    await page.route('**/app/api/viewer/video-selection',async r=>{frozen=r.request().postDataJSON();seen();await held;await r.continue();});
    await page.locator('[data-video-login="delta"] input').click();await started;assert.equal(frozen.expected_version,original.version);
    await other.locator('[data-video-login="epsilon"] input').click();await other.getByText('Выбор сохранён',{exact:true}).waitFor();const changed=await canonical();assert.equal(changed.version,original.version+1);assert.deepEqual(changed.selected_logins,[...original.selected_logins,'epsilon']);
    release();await page.getByText('Выбор изменился в другой сессии. Проверьте список и повторите действие.',{exact:true}).waitFor();await page.unroute('**/app/api/viewer/video-selection');assert.deepEqual(await canonical(),changed,'Held stale window cannot overwrite newer selection');
    await page.locator('[data-video-login="delta"] input').click();await page.getByText('Выбрано 5 из 5',{exact:true}).waitFor();await page.getByText('Выбор сохранён',{exact:true}).waitFor();const five=await canonical();assert.equal(five.selected_logins.length,5);assert(five.selected_logins.includes('gamma'),'Offline slot remains counted');
    await other.evaluate(()=>document.dispatchEvent(new Event('visibilitychange')));await other.getByText('Выбрано 5 из 5',{exact:true}).waitFor();await other.locator('[data-video-login="zeta"] input').click();await other.getByRole('dialog',{name:'Все пять мест заняты',exact:true}).waitFor();
    await page.locator('[data-video-login="delta"] input').click();await page.getByText('Выбрано 4 из 5',{exact:true}).waitFor();await page.getByText('Выбор сохранён',{exact:true}).waitFor();const four=await canonical();
    await other.getByRole('button',{name:'Заменить Alpha',exact:true}).click();await other.getByText('Выбор изменился в другой сессии. Проверьте список и повторите действие.',{exact:true}).waitFor();assert.deepEqual(await canonical(),four,'Stale replacement dialog from real second window cannot overwrite');
    for(const [p,label] of [[page,'window-a'],[other,'window-b']]){await assertLayout(p);const file=`video-windows-${engine}-${label}.png`;await p.screenshot({path:path.join(output,file),animations:'disabled'});pictures.push({file,sha256:digest(path.join(output,file))});}
    assert.deepEqual(external,[]);assert.deepEqual(errors,[]);return pictures;
  }finally{release?.();await page.unroute('**/app/api/viewer/video-selection');await other.close();}
}
module.exports={matrixJourney,runAll,zoomJourney,videoWindowsJourney,sources};
