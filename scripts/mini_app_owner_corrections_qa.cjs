// Isolated component QA: no Telegram SDK, credentials, app server or API requests.
const fs=require('fs'),path=require('path'),crypto=require('crypto'),{execFileSync}=require('child_process');
const {chromium,webkit}=require('playwright');
const root=path.resolve(__dirname,'..'),ui=path.join(root,'bot/mini_app_ui');
const engine=process.env.CORRECTIONS_ENGINE||'chromium';
const fast=process.env.CORRECTIONS_FAST==='1';
const out=path.resolve(process.env.CORRECTIONS_OUT||path.join(root,'docs/audits/mini-app-owner-corrections-2026-10-03',engine));
fs.mkdirSync(out,{recursive:true});
const catalog=JSON.parse(execFileSync(process.env.CORRECTIONS_PYTHON||path.join(root,'.venv/Scripts/python.exe'),['-c','import json; from bot.plan_catalog import catalog_payload; print(json.dumps(catalog_payload()))'],{cwd:root,encoding:'utf8'}));
const cache=new Map();
function moduleUrl(name){if(cache.has(name))return cache.get(name);const source=fs.readFileSync(path.join(ui,name),'utf8').replace(/from ['"]\.\/(.*?)['"]/g,(_,file)=>`from '${moduleUrl(file)}'`);const url='data:text/javascript;base64,'+Buffer.from(source).toString('base64');cache.set(name,url);return url;}
const files=fs.readdirSync(ui).filter(f=>/\.(js|css|png)$/.test(f));
const hash=f=>crypto.createHash('sha256').update(fs.readFileSync(path.join(ui,f))).digest('hex');
const report={engine,kind:'isolated-components-no-auth-no-api',checks:[],errors:[],network:[],screenshots:[],sources:Object.fromEntries(files.map(f=>[f,hash(f)]))};
const check=(name,pass,details)=>report.checks.push({name,pass:Boolean(pass),...(details===undefined?{}:{details})});
let browser;
async function pageFor(kind,mode='viewer',theme='light',access={}){
 const p=await browser.newPage({viewport:{width:390,height:844},deviceScaleFactor:1});p.on('pageerror',e=>report.errors.push(e.message));
 await p.route('**/*',r=>{report.network.push(r.request().url());return r.abort();});
 let html=fs.readFileSync(path.join(ui,'index.html'),'utf8').replace(/<script[\s\S]*?<\/script>/g,'').replace(/<link[^>]*>/g,'');
 await p.setContent(html);await p.addStyleTag({content:fs.readFileSync(path.join(ui,'app.css'),'utf8')});
 await p.evaluate(async({url,apiUrl,componentsUrl,themeUrl,routerUrl,catalog,kind,mode,theme,access,mascotData,cutoutData})=>{
  const {element,icon}=await import(componentsUrl),module=await import(url),{ApiError}=await import(apiUrl);
  const {createThemeController}=await import(themeUrl);const themeController=createThemeController({storage:{getItem:()=>theme,setItem:()=>{}}});
  document.documentElement.style.setProperty('--top-inset','80px');document.documentElement.style.setProperty('--bottom-inset','24px');
  window.qa={calls:[],hold:false,failure:0,release:null,route:{mode,tab:kind==='channel'?'channel':kind==='viewer'?'streamers':kind==='profile'||kind==='reports'?'profile':'subscription',detail:kind==='channel'?'channel:-1001':kind==='viewer'?null:kind==='reports'?'reports':'subscription'},
   profile:{connected:true,twitch_login:'streamer',communities:[{chat_id:-1001,chat_type:'channel',title:'Канал стримера',publishing:true,permission_ok:true,permission_status:'ready',public_url:'https://t.me/examplechannel'}]},
   state:{viewer:{active:false,sources:[],test_trial_available:false,...access.viewer},streamer:{active:false,linked:true,publishing_access:true,sources:[],...access.streamer},history:access.history||[]},
   viewer:{channel_limit:50,viewer_plus_active:false,folders:[],video_selection:{selected_logins:[],limit:0},subscriptions:[
    {login:'alpha',display_name:'Alpha',status:'live',notify_enabled:true,is_favorite:false},
    {login:'longstreamerloginabcdefghijklmn',display_name:'ДлинноеИмяСтримераБезПробелов',status:'live',notify_enabled:true,is_favorite:false},
    {login:'zeta',display_name:'Zeta',status:'offline',notify_enabled:false,is_favorite:true,paused_by_plan:true}]}};
  const q=window.qa;q.report={login:'alpha',chat_id:mode==='viewer'?101:-1001,title:mode==='viewer'?'Alpha':'Канал стримера',available:true,enabled:false,format:'brief',recipient:mode==='viewer'?'self':'channel'};
  const api={user:{id:101,display_name:'Тестовый зритель',username:'demo_viewer'},storage:{getItem:()=>null,setItem:()=>{}},post:async(route,fields,options={})=>{q.calls.push(route);if(route.endsWith('/reports/save')){q.report.enabled=fields.enabled;q.report.format=fields.format;return {item:structuredClone(q.report)};}if(route.endsWith('/reports'))return {items:structuredClone(q.reportItems||[q.report])};if(q.hold&&(!q.holdRoute||route.endsWith(q.holdRoute)))await new Promise((resolve,reject)=>{q.release=resolve;options.signal?.addEventListener('abort',()=>reject(new DOMException('Aborted','AbortError')),{once:true});});if(q.failure)throw new ApiError(q.failure,'network_error');if(route.endsWith('/unfollow/undo')){q.viewer.subscriptions.push(q.removed);return {restored:true,login:q.removed.login};}if(route.endsWith('/favorite')){q.viewer.subscriptions.find(x=>x.login===fields.login).is_favorite=fields.is_favorite;return {is_favorite:fields.is_favorite};}if(route.endsWith('/notify')){q.viewer.subscriptions.find(x=>x.login===fields.login).notify_enabled=fields.enabled;return {notify_enabled:fields.enabled};}if(route.endsWith('/unfollow')){q.removed=structuredClone(q.viewer.subscriptions.find(x=>x.login===fields.login));q.viewer.subscriptions=q.viewer.subscriptions.filter(x=>x.login!==fields.login);q.viewer.subscriptions.forEach(x=>x.paused_by_plan=false);return {removed:true,video_selection:q.viewer.video_selection,undo_token:'x'.repeat(43),undo_expires_at:Date.now()/1000+60};}return structuredClone(route.endsWith('/catalog')?catalog:route==='/app/api/viewer/state'?q.viewer:route.endsWith('/state')?q.state:q.profile);}};
  let router={get state(){return q.route;},refresh:()=>{const target=document.querySelector('#content');target.replaceChildren();if(kind==='reports'&&(typeof q.route.detail==='object'?q.route.detail?.name:q.route.detail)!=='reports'){q.feature.deactivate?.();return;}q.feature.render(target,q.route);document.querySelectorAll('.plus-mascot,.profile-mascot,.empty-mascot,.home-mascot').forEach(image=>image.src=image.classList.contains('plus-mascot')?mascotData:cutoutData);},openDetail:detail=>{q.previousDetail=q.route.detail;q.route.detail=detail;router.refresh();},back:()=>{q.route.detail=q.previousDetail;router.refresh();}};
  q.feature=kind==='channel'?module.createStreamerFeature(api,()=>router,{openTelegramLink:()=>{}}):kind==='viewer'?module.createViewerFeature(api,()=>router,{}):kind==='reports'?module.createReportsFeature(api,()=>router):kind==='profile'?module.createProfileFeature(api,()=>router,themeController):module.createSubscriptionFeature(api,()=>router,()=>{});q.render=router.refresh;q.installRouter=async()=>{const {createRouter}=await import(routerUrl);const paint=router.refresh;router=createRouter(state=>{q.route=state;paint();});q.realRouter=router;router.setTab('streamers');};
  for(const [key,title]of [['viewer','Зритель'],['streamer','Стример']]){const button=element('button','',title);button.setAttribute('aria-pressed',String(key===mode));document.querySelector('#mode-switch').append(button);}
  document.querySelector('#app-menu').append(icon('more'));
  for(const [key,title,name]of mode==='viewer'?[['home','Главная','home'],['streamers','Стримеры','people'],['profile','Профиль','profile'],['subscription','Тариф','plus']]:[['channel','Мой канал','channel'],['posts','Посты','posts'],['profile','Профиль','profile'],['subscription','Тариф','plus']]){const button=element('button','');button.setAttribute('aria-current',key===q.route.tab?'page':'false');button.append(icon(name),element('span','',title));document.querySelector('#tab-bar').append(button);}
  router.refresh();
 },{url:moduleUrl(kind==='channel'?'streamer.js':kind==='viewer'?'viewer.js':kind==='profile'?'profile.js':kind==='reports'?'reports.js':'subscription.js'),apiUrl:moduleUrl('api.js'),componentsUrl:moduleUrl('components.js'),themeUrl:moduleUrl('theme.js'),routerUrl:moduleUrl('router.js'),catalog,kind,mode,theme,access,cutoutData:'data:image/png;base64,'+fs.readFileSync(path.join(ui,'mascot-cutout.png')).toString('base64'),mascotData:fs.existsSync(path.join(ui,'plus-mascot.png'))?'data:image/png;base64,'+fs.readFileSync(path.join(ui,'plus-mascot.png')).toString('base64'):''});
 await p.getByRole('heading',{name:kind==='channel'?/Канал стримера|Telegram-канал/:kind==='viewer'?'Стримеры':kind==='profile'?'Профиль':kind==='reports'?'Отчёты об эфирах':/Тариф|Моя подписка/}).waitFor();
 if(fs.existsSync(path.join(ui,'plus-mascot.png')))await p.evaluate(data=>{document.querySelectorAll('.plus-mascot').forEach(i=>i.src=data);},'data:image/png;base64,'+fs.readFileSync(path.join(ui,'plus-mascot.png')).toString('base64'));
 return p;
}
async function shot(p,name){if(name!=='streamers-swipe')await p.evaluate(()=>scrollTo(0,0));await p.evaluate(()=>Promise.all([...document.images].map(i=>i.decode().catch(()=>{}))));const file=name+'.png';await p.screenshot({path:path.join(out,file),fullPage:false,animations:'disabled'});report.screenshots.push(file);}
async function settle(p){await p.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));}
async function homeStates(){
 for(const theme of fast?['light']:['light','dark']){
  const page=await pageFor('viewer','viewer',theme);
  await page.evaluate(async()=>{qa.homeRows=structuredClone(qa.viewer.subscriptions);qa.homeRows[1].is_favorite=true;qa.homeRows.push({login:'unknown',display_name:'Статус пока неизвестен',status:'stale',notify_enabled:true,is_favorite:false});await qa.installRouter();qa.realRouter.setTab('home');document.querySelectorAll('#tab-bar button').forEach((b,i)=>b.setAttribute('aria-current',i===0?'page':'false'));});
  for(const state of ['live','offline','stale','empty','live-again']){
   await page.evaluate(async state=>{qa.viewer.subscriptions=state==='empty'?[]:structuredClone(qa.homeRows).map(row=>['offline','stale'].includes(state)?{...row,status:state}:row);await qa.feature.refresh({fresh:true});},state);await settle(page);
   const label=`home-${state}-${theme}`,live=state.startsWith('live');
   check(label+'-one-hero',await page.locator('.home-live-heading').count()===1);
   check(label+'-one-whole-character',await page.locator('.home-mascot').count()===1&&await page.locator('.empty-mascot').count()===0);
   check(label+'-honest-content',await page.locator('.home-streamers .streamer-row').count()===(live?2:0)&&await page.locator('.watch-link').count()===(live?2:0));
   check(label+'-no-search-or-fake-notifications',await page.getByRole('searchbox').count()===0&&await page.getByText('Уведомления включены',{exact:true}).count()===0);
   if(live){check(label+'-favorites-and-count',await page.locator('.home-streamers .streamer-row').first().getAttribute('data-row-key')==='longstreamerloginabcdefghijklmn'&&await page.locator('.section-head .muted').textContent()==='2');}
   else {check(label+'-correct-explanation',await page.getByText(state==='offline'?'Пока нет подтверждённого эфира':state==='stale'?'Проверяем статус эфиров':'Ваш первый стример',{exact:true}).count()===1);}
   if(state!=='live-again')await shot(page,label);
   if(['offline','empty'].includes(state)){
    for(const [width,font]of fast?[[320,16]]:[[320,16],[390,32]]){
     await page.setViewportSize({width,height:844});await page.evaluate(size=>document.documentElement.style.fontSize=size+'px',font);await settle(page);
     check(`${label}-${width}-text${font}-no-overflow`,await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
     check(`${label}-${width}-text${font}-character-clear`,await page.evaluate(()=>{const image=document.querySelector('.home-mascot');if(!image)return false;const a=image.getBoundingClientRect(),copy=document.querySelector('.home-hero-copy'),walker=document.createTreeWalker(copy,NodeFilter.SHOW_TEXT),rects=[];let node;while(node=walker.nextNode()){const range=document.createRange();range.selectNodeContents(node);rects.push(...range.getClientRects());}return a.left>=0&&a.right<=innerWidth&&a.top>=document.querySelector('.app-header').getBoundingClientRect().bottom&&rects.every(b=>a.left>=b.right||a.right<=b.left||a.bottom<=b.top||a.top>=b.bottom);}));
     await shot(page,`${label}-${width}-text${font}`);
    }
    await page.setViewportSize({width:390,height:844});await page.evaluate(()=>document.documentElement.style.fontSize='16px');
   }
   if(state==='offline'){await page.getByRole('button',{name:'Мои стримеры',exact:true}).click();check(label+'-real-router-all-streamers',await page.getByRole('heading',{name:'Стримеры',exact:true}).count()===1);await page.evaluate(()=>qa.realRouter.setTab('home'));}
   if(state==='empty'){await page.getByRole('button',{name:'Добавить стримера',exact:true}).click();check(label+'-add-opens',await page.getByRole('dialog',{name:'Добавить стримера',exact:true}).isVisible());await page.keyboard.press('Escape');}
  }
  await page.close();
 }
 check('home-states-no-js-errors',report.errors.length===0,report.errors);
 check('home-states-no-api-or-auth-requests',!report.network.some(x=>/api|telegram|init_data/.test(x)),report.network);
 check('home-states-source-snapshot-unchanged',files.every(f=>report.sources[f]===hash(f)));
}
async function swipeAndUndo(){
 const page=await pageFor('viewer');
 const gesture=async(selector,dx,dy,finish=true)=>page.locator(selector).evaluate((node,{dx,dy,finish})=>{node.dispatchEvent(new PointerEvent('pointerdown',{bubbles:true,pointerId:1,clientX:210,clientY:200}));node.dispatchEvent(new PointerEvent('pointermove',{bubbles:true,cancelable:true,pointerId:1,clientX:210+dx,clientY:200+dy}));if(finish)node.dispatchEvent(new PointerEvent('pointerup',{bubbles:true,pointerId:1,clientX:210+dx,clientY:200+dy}));},{dx,dy,finish});
 await gesture('.swipe-shell[data-row-key=alpha] .favorite-toggle',-34,7);
 check('swipe-starts-on-right-side-control',await page.locator('.swipe-shell[data-row-key=alpha]').evaluate(node=>node.classList.contains('swipe-open')));
 await page.locator('.swipe-shell[data-row-key=alpha] .favorite-toggle').evaluate(node=>node.click());await settle(page);
 check('swipe-does-not-toggle-favorite-or-open-detail',await page.evaluate(()=>!qa.viewer.subscriptions.find(row=>row.login==='alpha').is_favorite&&qa.route.detail===null));
 await gesture('.swipe-shell[data-row-key=alpha]',35,2);
 check('swipe-right-closes',await page.locator('.swipe-shell[data-row-key=alpha]').evaluate(node=>!node.classList.contains('swipe-open')));
 await gesture('.swipe-shell[data-row-key=alpha] .quick-notify',-34,8);
 await page.locator('.swipe-shell[data-row-key=alpha] input').evaluate(node=>node.click());await settle(page);
 check('swipe-starts-on-notify-without-toggling',await page.locator('.swipe-shell[data-row-key=alpha]').evaluate(node=>node.classList.contains('swipe-open'))&&await page.evaluate(()=>qa.viewer.subscriptions.find(row=>row.login==='alpha').notify_enabled));
 await gesture('.swipe-shell[data-row-key=alpha]',35,2);
 await gesture('.swipe-shell[data-row-key=alpha] .streamer-open',-18,3,false);
 check('swipe-card-follows-finger',await page.locator('.swipe-shell[data-row-key=alpha] .streamer-row').evaluate(node=>new DOMMatrixReadOnly(getComputedStyle(node).transform).m41<-10));
 await page.locator('.swipe-shell[data-row-key=alpha]').evaluate(node=>node.dispatchEvent(new PointerEvent('pointercancel',{bubbles:true,pointerId:1})));
 check('swipe-cancel-restores-card',await page.locator('.swipe-shell[data-row-key=alpha]').evaluate(node=>!node.classList.contains('swipe-open')&&!node.classList.contains('swipe-dragging')));
 await gesture('.swipe-shell[data-row-key=alpha]',-12,70);
 check('swipe-keeps-vertical-scroll',await page.locator('.swipe-shell[data-row-key=alpha]').evaluate(node=>!node.classList.contains('swipe-open')));
 await gesture('.swipe-shell[data-row-key=alpha] .streamer-open',-32,11);
 check('swipe-short-diagonal-reveals-without-delete',await page.locator('.swipe-shell[data-row-key=alpha]').evaluate(node=>node.classList.contains('swipe-open'))&&await page.evaluate(()=>qa.viewer.subscriptions.length===3));
 await shot(page,'swipe-short-right-edge');await page.clock.install();
 await gesture('.swipe-shell[data-row-key=alpha]',-90,2);await page.locator('.swipe-shell[data-row-key=alpha] .swipe-delete').click();await settle(page);
 check('undo-notice-one-copy',await page.getByText('Alpha удалён из подписок',{exact:true}).count()===2&&await page.locator('[data-feedback]').getByText('Alpha удалён из подписок',{exact:true}).count()===0);
 await page.clock.runFor(5500);check('undo-notice-enough-time',await page.getByRole('button',{name:'Отменить удаление',exact:true}).count()===1);
 await page.clock.runFor(700);check('undo-notice-hides-after-six-seconds',await page.getByRole('button',{name:'Отменить удаление',exact:true}).count()===0&&await page.getByText('Alpha удалён из подписок',{exact:true}).count()===0);await page.close();
 const pending=await pageFor('viewer');await pending.clock.install();await gestureOn(pending);await pending.locator('.swipe-shell[data-row-key=alpha] .swipe-delete').click();await settle(pending);
 await pending.clock.runFor(5000);await pending.evaluate(()=>{qa.hold=true;qa.holdRoute='/unfollow/undo';});await pending.getByRole('button',{name:'Отменить удаление',exact:true}).click();await pending.clock.runFor(8000);
 check('undo-in-flight-remains-visible',await pending.getByRole('button',{name:'Отменить удаление',exact:true}).count()===1&&await pending.locator('.undo-notice:not(.undo-reserve)').getByText('Возвращаем…',{exact:true}).count()===1);
 await pending.evaluate(()=>{qa.hold=false;qa.release();});await settle(pending);check('undo-in-flight-restores-server-row',await pending.locator('.swipe-shell[data-row-key=alpha]').count()===1);await pending.close();
 async function gestureOn(p){await p.locator('.swipe-shell[data-row-key=alpha]').evaluate(node=>{for(const[type,x]of[['pointerdown',210],['pointermove',110],['pointerup',110]])node.dispatchEvent(new PointerEvent(type,{bubbles:true,pointerId:1,clientX:x,clientY:200}));});}
 const focused=await pageFor('viewer');await focused.evaluate(()=>qa.installRouter());await settle(focused);await focused.clock.install();await gestureOn(focused);await focused.locator('.swipe-shell[data-row-key=alpha] .swipe-delete').click();await settle(focused);await focused.clock.runFor(5000);await focused.getByRole('button',{name:'Отменить удаление',exact:true}).focus();await focused.evaluate(()=>qa.realRouter.refresh());await focused.clock.runFor(2500);check('undo-keeps-keyboard-focus',await focused.getByRole('button',{name:'Отменить удаление',exact:true}).count()===1&&await focused.evaluate(()=>document.activeElement?.dataset.focusKey==='undo-restore'));
 await focused.evaluate(()=>{qa.hold=true;qa.holdRoute='/unfollow/undo';qa.failure=503;});await focused.getByRole('button',{name:'Отменить удаление',exact:true}).press('Enter');await focused.clock.runFor(250);check('undo-pending-keeps-focus',await focused.evaluate(()=>document.activeElement?.dataset.focusKey==='undo-restore'),await focused.evaluate(()=>({active:document.activeElement.outerHTML.slice(0,300),buttons:[...document.querySelectorAll('.undo-notice button')].map(node=>({key:node.dataset.focusKey,disabled:node.disabled,inert:!!node.closest('[inert]')}))})));await focused.getByRole('button',{name:'Отменить удаление',exact:true}).evaluate(node=>node.click());check('undo-busy-does-not-repeat-request',await focused.evaluate(()=>qa.calls.filter(route=>route.endsWith('/unfollow/undo')).length===1));await focused.evaluate(()=>{qa.hold=false;qa.release();});await focused.clock.runFor(250);check('undo-error-keeps-focus-and-retry',await focused.evaluate(()=>document.activeElement?.dataset.focusKey==='undo-restore')&&await focused.locator('.undo-notice:not(.undo-reserve)').getByText('Не удалось вернуть подписку. Проверьте связь и повторите.',{exact:true}).count()===1,await focused.evaluate(()=>({active:document.activeElement.outerHTML.slice(0,300)})));
 await focused.getByRole('searchbox',{name:'Поиск по подпискам',exact:true}).focus();await focused.clock.runFor(6200);check('undo-hides-after-focus-leaves',await focused.getByRole('button',{name:'Отменить удаление',exact:true}).count()===0);await focused.close();
 const actual=await pageFor('viewer');await actual.clock.install();const shell=actual.locator('.swipe-shell[data-row-key=alpha]');await shell.evaluate(node=>{qa.captured=false;node.addEventListener('gotpointercapture',()=>qa.captured=true);});
 const star=await shell.locator('.favorite-toggle').boundingBox();await actual.mouse.move(star.x+star.width/2,star.y+star.height/2);await actual.mouse.down();await actual.mouse.move(star.x+star.width/2-35,star.y+star.height/2+4,{steps:5});await actual.mouse.up();await settle(actual);
 check('swipe-trusted-pointer-capture-and-control-start',await actual.evaluate(()=>qa.captured&&!qa.viewer.subscriptions.find(row=>row.login==='alpha').is_favorite)&&await shell.evaluate(node=>node.classList.contains('swipe-open')));
 await actual.clock.runFor(450);await shell.getByRole('button',{name:'Добавить Alpha в избранное',exact:true}).click();await settle(actual);check('swipe-preserves-normal-favorite-tap',await actual.getByRole('button',{name:'Убрать Alpha из избранного',exact:true}).count()===1);await actual.getByRole('checkbox',{name:'Уведомления Alpha',exact:true}).uncheck();await settle(actual);check('swipe-preserves-normal-notify-tap',!await actual.getByRole('checkbox',{name:'Уведомления Alpha',exact:true}).isChecked());await actual.close();
 check('swipe-undo-no-js-errors',report.errors.length===0,report.errors);
}
(async()=>{
 browser=await({chromium,webkit}[engine]).launch({headless:false});
 if(process.env.CORRECTIONS_INTERACTION_ONLY==='1'){await swipeAndUndo();report.status=report.checks.every(x=>x.pass)?'PASS':'FAIL';return;}
 await homeStates();
 if(process.env.CORRECTIONS_HOME_ONLY==='1'){report.status=report.checks.every(x=>x.pass)?'PASS':'FAIL';return;}
 await swipeAndUndo();
 const p=await pageFor('channel','streamer');
 check('channel-name-is-heading',await p.getByRole('heading',{name:'Канал стримера',exact:true}).count()===1);
 await p.clock.install();await p.evaluate(()=>qa.hold=true);await p.getByRole('button',{name:'Проверить права',exact:true}).click();await p.clock.runFor(6100);check('rights-slow-valid-server-budget',await p.getByRole('button',{name:'Проверяем…',exact:true}).count()===1);await p.clock.resume();
 check('rights-check-visible-pending',await p.getByRole('button',{name:'Проверяем…',exact:true}).count()===1);
 await shot(p,'channel-pending');
 await p.evaluate(()=>{qa.hold=false;qa.release();});await settle(p);
 check('rights-check-unchanged-success',await p.getByRole('status').filter({hasText:'Права проверены. Бот может публиковать.'}).count()===1);
 check('rights-check-does-not-toggle-publishing',await p.evaluate(()=>qa.profile.communities[0].publishing&&!qa.calls.some(x=>x.includes('/toggle'))));
 await shot(p,'channel-ready');
 await p.evaluate(()=>qa.failure=503);await p.getByRole('button',{name:'Проверить права',exact:true}).click();await settle(p);
 check('rights-check-error-near-control',await p.locator('.permission-check [role=status]').filter({hasText:'Не удалось проверить права'}).count()===1);
 check('rights-error-no-success',await p.getByText('Права проверены. Бот может публиковать.',{exact:true}).count()===0);await shot(p,'channel-error');
 await p.evaluate(()=>{qa.failure=0;qa.profile.communities[0].permission_status='missing_post_right';qa.profile.communities[0].permission_ok=false;});await p.getByRole('button',{name:'Проверить права',exact:true}).click();await settle(p);
 check('rights-missing-specific-result',await p.locator('.permission-check [role=status]').filter({hasText:'Нет права публиковать сообщения'}).count()===1);await shot(p,'channel-no-rights');
 await p.evaluate(()=>{qa.hold=true;qa.profile.communities[0].permission_status='ready';qa.profile.communities[0].permission_ok=true;});await p.getByRole('button',{name:'Проверить права',exact:true}).click();
 await p.evaluate(()=>{qa.feature.clearFeedback();qa.route.detail=null;qa.render();qa.hold=false;qa.release();});await settle(p);
 check('rights-late-response-stays-in-context',await p.getByText('Права проверены. Бот может публиковать.',{exact:true}).count()===0);await p.close();
 const v=await pageFor('viewer');
 check('favorites-first-across-live-offline',await v.locator('#content [data-row-key]').first().getAttribute('data-row-key')==='zeta');
 check('favorite-controls-present',await v.locator('.favorite-toggle').count()===3);check('favorite-filter-present',await v.getByRole('button',{name:'Только избранные',exact:true}).count()===1);if(await v.getByRole('button',{name:'Только избранные',exact:true}).count()){await v.getByRole('button',{name:'Только избранные',exact:true}).click();check('filter-only-favorite',await v.locator('.swipe-shell').count()===1);await v.evaluate(()=>qa.render());check('favorite-filter-survives-refresh',await v.getByRole('button',{name:'Только избранные',exact:true}).getAttribute('aria-pressed')==='true');await v.getByRole('button',{name:'Только избранные',exact:true}).click();}
 if(await v.locator('.favorite-toggle').count()){
  await v.getByRole('button',{name:'Добавить Alpha в избранное',exact:true}).click();await settle(v);
  check('favorite-independent-notifications',await v.evaluate(()=>qa.viewer.subscriptions.find(x=>x.login==='alpha').is_favorite&&qa.viewer.subscriptions.find(x=>x.login==='alpha').notify_enabled&&!qa.calls.includes('/app/api/viewer/notify')));
  await v.getByRole('checkbox',{name:'Уведомления Alpha',exact:true}).uncheck();await settle(v);
  check('notifications-independent-favorite',await v.evaluate(()=>qa.viewer.subscriptions.find(x=>x.login==='alpha').is_favorite&&!qa.viewer.subscriptions.find(x=>x.login==='alpha').notify_enabled));
  await v.evaluate(()=>{qa.hold=true;qa.holdRoute='/notify';});await v.getByRole('checkbox',{name:'Уведомления Alpha',exact:true}).check();
  await v.evaluate(()=>qa.feature.refresh({fresh:true}));await v.evaluate(()=>{qa.hold=false;qa.release();});await settle(v);
  check('notify-completion-survives-concurrent-state',await v.getByRole('checkbox',{name:'Уведомления Alpha',exact:true}).isChecked());
  await v.evaluate(()=>qa.failure=503);await v.getByRole('button',{name:'Убрать Alpha из избранного',exact:true}).click();await settle(v);
  check('favorite-failure-retains-star',await v.getByRole('button',{name:'Убрать Alpha из избранного',exact:true}).getAttribute('aria-pressed')==='true');await v.evaluate(()=>qa.failure=0);
 }
 check('swipe-shell-present',await v.locator('.swipe-shell').count()===3);
 if(await v.locator('.swipe-shell').count()){
  const row=v.locator('.swipe-shell[data-row-key=alpha]');
  const gesture=async(dx,dy)=>row.evaluate((node,{dx,dy})=>{node.dispatchEvent(new PointerEvent('pointerdown',{bubbles:true,pointerId:1,clientX:210,clientY:200}));node.dispatchEvent(new PointerEvent('pointermove',{bubbles:true,pointerId:1,clientX:210+dx,clientY:200+dy}));node.dispatchEvent(new PointerEvent('pointerup',{bubbles:true,pointerId:1,clientX:210+dx,clientY:200+dy}));},{dx,dy});
  await gesture(-10,90);check('vertical-scroll-does-not-reveal-delete',!await row.locator('.swipe-delete').isVisible());
  await gesture(-90,4);check('swipe-reveals-without-deleting',await row.locator('.swipe-delete').isVisible()&&await v.evaluate(()=>qa.viewer.subscriptions.length===3));
  await shot(v,'streamers-swipe');await v.evaluate(()=>qa.failure=503);await row.getByRole('button',{name:'Удалить',exact:true}).click();await settle(v);
  check('delete-failure-keeps-row',await v.locator('.swipe-shell[data-row-key=alpha]').count()===1);await v.evaluate(()=>qa.failure=0);
  await gesture(-90,4);await row.getByRole('button',{name:'Удалить',exact:true}).click();await settle(v);
  check('delete-refreshes-released-plan-slot',await v.locator('.swipe-shell[data-row-key=zeta]').getByText('Приостановлено по лимиту',{exact:true}).count()===0);
  check('delete-click-removes-only-target',await v.evaluate(()=>qa.viewer.subscriptions.length===2&&!qa.viewer.subscriptions.some(x=>x.login==='alpha')));
  check('undo-visible-after-delete',await v.getByRole('button',{name:'Отменить удаление',exact:true}).count()===1);
  if(await v.getByRole('button',{name:'Отменить удаление',exact:true}).count()){await v.getByRole('button',{name:'Отменить удаление',exact:true}).click();await settle(v);check('undo-server-restores-favorite-and-notify',await v.getByRole('button',{name:'Убрать Alpha из избранного',exact:true}).count()===1&&await v.getByRole('checkbox',{name:'Уведомления Alpha',exact:true}).isChecked());}
 }
 await v.setViewportSize({width:320,height:844});check('favorite-touch-target-44',await v.locator('.favorite-toggle').evaluateAll(items=>items.every(item=>item.getBoundingClientRect().width>=44&&item.getBoundingClientRect().height>=44)));check('long-name-and-controls-fit-320',await v.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await shot(v,'streamers-320');await v.close();
 const race=await pageFor('viewer');race.on('dialog',dialog=>dialog.accept());
 const reveal=async page=>page.locator('.swipe-shell[data-row-key=alpha]').evaluate(node=>{for(const [type,x]of [['pointerdown',210],['pointermove',110],['pointerup',110]])node.dispatchEvent(new PointerEvent(type,{bubbles:true,pointerId:1,clientX:x,clientY:200}));});
 await race.evaluate(()=>{qa.hold=true;qa.holdRoute='/favorite';});await race.getByRole('button',{name:'Убрать Zeta из избранного',exact:true}).click();await reveal(race);await race.locator('.swipe-shell[data-row-key=alpha] .swipe-delete').click();await settle(race);await race.evaluate(()=>{qa.hold=false;qa.release();});await settle(race);
 check('delete-during-other-favorite-reloads-after-settle',await race.locator('.swipe-shell[data-row-key=zeta]').getByText('Приостановлено по лимиту',{exact:true}).count()===0);
 await race.evaluate(()=>{qa.hold=true;qa.holdRoute='/notify';});await race.getByRole('checkbox',{name:'Уведомления Zeta',exact:true}).evaluate(node=>node.scrollIntoView({block:'center'}));await race.getByRole('checkbox',{name:'Уведомления Zeta',exact:true}).check();await race.getByRole('button',{name:'Отменить удаление',exact:true}).click();await settle(race);await race.evaluate(()=>{qa.hold=false;qa.release();});await settle(race);
 check('undo-during-other-notify-restores-visible-row',await race.locator('.swipe-shell[data-row-key=alpha]').count()===1);await race.close();
 const detail=await pageFor('viewer');detail.on('dialog',dialog=>dialog.accept());await detail.getByRole('button',{name:'Открыть Alpha',exact:true}).click();await detail.evaluate(()=>{qa.hold=true;qa.holdRoute='/notify';});await detail.getByRole('checkbox',{name:'Уведомлять об эфирах',exact:true}).uncheck();await detail.evaluate(async()=>{qa.route.detail=null;qa.render();await qa.feature.refresh({fresh:true});qa.hold=false;qa.release();});await settle(detail);
 check('detail-notify-back-and-refresh-keeps-result',!await detail.getByRole('checkbox',{name:'Уведомления Alpha',exact:true}).isChecked());await detail.getByRole('button',{name:'Открыть Alpha',exact:true}).click();await detail.getByRole('button',{name:'Удалить подписку',exact:true}).click();await settle(detail);
 check('detail-delete-has-undo',await detail.getByRole('button',{name:'Отменить удаление',exact:true}).count()===1);check('detail-delete-refreshes-active-limit',await detail.locator('.swipe-shell[data-row-key=zeta]').getByText('Приостановлено по лимиту',{exact:true}).count()===0);await detail.close();
  for(const theme of fast?['light']:['light','dark']){const viewer=await pageFor('viewer','viewer',theme);await shot(viewer,'streamers-'+theme);await viewer.evaluate(()=>{qa.route.tab='home';qa.render();document.querySelectorAll('#tab-bar button').forEach((b,i)=>b.setAttribute('aria-current',i===0?'page':'false'));});check('home-'+theme+'-only-live',await viewer.locator('.streamer-row').count()===2);check('home-'+theme+'-no-extra-search',await viewer.getByRole('searchbox').count()===0);await shot(viewer,'home-'+theme);await viewer.evaluate(async()=>{qa.viewer.subscriptions=[];await qa.feature.refresh({fresh:true});});check('empty-'+theme+'-one-shared-mascot',await viewer.locator('.home-mascot').count()===1&&await viewer.locator('.empty-mascot').count()===0);await shot(viewer,'home-empty-'+theme);await viewer.close();}
 const routed=await pageFor('viewer');await routed.evaluate(async()=>{qa.viewer.subscriptions=Array.from({length:100},(_,i)=>({login:'channel'+i,display_name:'Канал '+i,status:i%2?'offline':'live',notify_enabled:true,is_favorite:i%5===0}));await qa.feature.refresh({fresh:true});await qa.installRouter();});await settle(routed);await routed.getByRole('searchbox',{name:'Поиск по подпискам',exact:true}).fill('Канал');await routed.getByRole('button',{name:'Только избранные',exact:true}).click();await routed.getByRole('button',{name:'Открыть Канал 40',exact:true}).scrollIntoViewIfNeeded();const beforeScroll=await routed.evaluate(()=>scrollY);await routed.getByRole('button',{name:'Открыть Канал 40',exact:true}).click();await settle(routed);await routed.evaluate(()=>qa.realRouter.back());await settle(routed);check('router-list-scroll-restored',Math.abs(await routed.evaluate(()=>scrollY)-beforeScroll)<3);check('router-search-filter-restored',await routed.getByRole('searchbox',{name:'Поиск по подпискам',exact:true}).inputValue()==='Канал'&&await routed.getByRole('button',{name:'Только избранные',exact:true}).getAttribute('aria-pressed')==='true');await routed.close();
 for(const mode of ['viewer','streamer'])for(const theme of fast?['light']:['light','dark']){
  const page=await pageFor('subscription',mode,theme);const product=mode==='viewer'?'viewer_plus':'streamer_plus';
  check(`${mode}-${theme}-four-benefits`,await page.locator('[data-benefit-block]').count()===4);
  check(`${mode}-${theme}-mascot`,await page.locator('.plus-mascot').count()===1);
  check(`${mode}-${theme}-price`,(await page.locator('.subscription-price').textContent()).includes(mode==='viewer'?'150 ₽':'300 ₽'));
  check(`${mode}-${theme}-payment-off-upfront`,await page.getByText('Оформление пока недоступно',{exact:true}).count()===1);
  check(`${mode}-${theme}-details-entry`,await page.getByRole('button',{name:'О тарифе',exact:true}).count()===1);
  if(await page.getByRole('button',{name:'О тарифе',exact:true}).count()){
   await page.getByRole('button',{name:'О тарифе',exact:true}).click();await settle(page);
   check(`${mode}-${theme}-all-paid-features-explained`,JSON.stringify(await page.locator('[data-plus-feature]').evaluateAll(items=>items.map(item=>item.dataset.plusFeature).sort()))===JSON.stringify([...catalog.products.find(item=>item.product_id===product).feature_ids].sort()));
   await shot(page,`${mode}-${theme}-details`);
   await page.getByRole('button',{name:'К тарифу',exact:true}).click();check(mode+'-'+theme+'-details-return',await page.locator('.plus-offer').count()===1);
  }
  for(const width of fast?[390]:[320,390,768]){await page.setViewportSize({width,height:844});await settle(page);check(`${mode}-${theme}-${width}-no-overflow`,await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await shot(page,`${mode}-${theme}-${width}`);}
  await page.setViewportSize({width:390,height:844});await page.evaluate(()=>scrollTo(0,0));
  check(`${mode}-${theme}-cta-first-screen`,await page.locator('.plus-purchase').evaluate(b=>b.getBoundingClientRect().bottom<document.querySelector('#tab-bar').getBoundingClientRect().top).catch(()=>false));
  await page.evaluate(()=>document.documentElement.style.fontSize='32px');await settle(page);check(`${mode}-${theme}-text200-no-overflow`,await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await shot(page,`${mode}-${theme}-text200`);
  await page.evaluate(()=>document.documentElement.style.fontSize='16px');
  check(`${mode}-${theme}-all-features-route-kept`,await page.getByRole('button',{name:'О тарифе',exact:true}).count()===1);
  await page.locator('.plus-offer button').filter({hasText:/Оформить|Способы оплаты/}).first().click();check(`${mode}-${theme}-purchase-keeps-role`,await page.evaluate(id=>qa.route.detail.name==='purchase'&&qa.route.detail.id===id,product));await page.close();
 }
 for(const kind of ['viewer','profile'])for(const theme of fast?['light']:['light','dark']){const revised=await pageFor(kind,'viewer',theme);if(kind==='viewer')await revised.evaluate(()=>{qa.route.tab='home';qa.render();});check(kind+'-'+theme+'-transparent-unmasked-mascot',await revised.locator(kind==='viewer'?'.home-mascot':'.profile-mascot').evaluate(img=>getComputedStyle(img).maskImage==='none'&&getComputedStyle(img).borderRadius==='0px'&&img.naturalWidth===1254));for(const width of fast?[390]:[320,390,768]){await revised.setViewportSize({width,height:844});check(kind+'-'+theme+'-'+width+'-revised-no-overflow',await revised.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));}await revised.setViewportSize({width:390,height:844});await revised.evaluate(()=>document.documentElement.style.fontSize='32px');check(kind+'-'+theme+'-revised-text200',await revised.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await shot(revised,kind+'-'+theme+'-revised-text200');await revised.close();}
  for(const theme of fast?['light']:['light','dark']){const profile=await pageFor('profile','viewer',theme);check('profile-'+theme+'-compact-account',await profile.locator('.profile-account').count()===1);check('profile-'+theme+'-inline-theme',await profile.locator('.profile-theme button').count()===3);check('profile-'+theme+'-reports-meaning',await profile.getByRole('button',{name:/Отчёты об эфирах/}).count()===1);await shot(profile,'profile-'+theme);await profile.close();}
  for(const mode of ['viewer','streamer']){const reports=await pageFor('reports',mode);await reports.getByRole('button',{name:mode==='viewer'?/Alpha/:/Канал стримера/}).click();await reports.getByRole('checkbox',{name:'Автоотчёт после эфира',exact:true}).check();await reports.getByLabel('Формат отчёта',{exact:true}).selectOption('full');await reports.getByRole('button',{name:'Сохранить',exact:true}).click();await settle(reports);check(mode+'-reports-save',await reports.getByText('Настройки отчёта сохранены',{exact:true}).count()===1);check(mode+'-reports-independent-notify',await reports.evaluate(()=>!qa.calls.some(x=>x.endsWith('/notify')||x.endsWith('/toggle'))));await shot(reports,'reports-'+mode);await reports.close();}
 const returned=await pageFor('reports');await returned.evaluate(async()=>{await qa.installRouter();qa.reportItems=[];qa.realRouter.setTab('profile');qa.realRouter.openDetail('reports');});await settle(returned);await returned.evaluate(()=>{qa.realRouter.setTab('streamers');qa.reportItems=[qa.report,{...qa.report,login:'zeta',title:'Zeta',enabled:true,format:'full'}];qa.realRouter.setTab('profile');qa.realRouter.openDetail('reports');});await settle(returned);check('reports-reentry-refreshes-empty-list',await returned.getByRole('button',{name:/Zeta/}).count()===1);if(await returned.getByRole('button',{name:/Zeta/}).count()){await returned.getByRole('button',{name:/Zeta/}).click();check('reports-same-owner-distinct-streamer',await returned.getByRole('checkbox',{name:'Автоотчёт после эфира',exact:true}).isChecked()&&await returned.getByLabel('Формат отчёта',{exact:true}).inputValue()==='full');}await returned.close();
 for(const theme of ['light','dark']){const colors=await pageFor('viewer','viewer',theme);const ratios=await colors.evaluate(()=>{const s=getComputedStyle(document.documentElement),lum=key=>{const h=s.getPropertyValue('--'+key).trim();const v=[1,3,5].map(i=>parseInt(h.slice(i,i+2),16)/255).map(x=>x<=.04045?x/12.92:((x+.055)/1.055)**2.4);return v[0]*.2126+v[1]*.7152+v[2]*.0722;};return [['text','canvas'],['text','surface'],['secondary','canvas'],['secondary','surface'],['accent','soft'],['button-fg','button-bg'],['danger','surface']].map(([a,b])=>({pair:a+'/'+b,ratio:(Math.max(lum(a),lum(b))+.05)/(Math.min(lum(a),lum(b))+.05)}));});check(theme+'-semantic-contrast',ratios.every(x=>x.ratio>=4.5),ratios);await colors.close();}
 const reduced=await pageFor('subscription');reduced.setDefaultTimeout(2000);await reduced.emulateMedia({reducedMotion:'reduce'});await reduced.getByRole('button',{name:'О тарифе',exact:true}).click();await reduced.getByRole('button',{name:'Показать лимит Plus',exact:true}).click();await settle(reduced);check('reduced-motion-example-state-without-animation',await reduced.locator('.plus-example-change').evaluate(node=>getComputedStyle(node).animationName==='none'&&node.textContent.includes('200')));await reduced.close();
 const inherited=await pageFor('subscription','viewer','light',{viewer:{active:true,sources:[{product_id:'streamer_plus'}]},streamer:{active:true,expires_at:1800000000,linked:false}});
 check('inherited-viewer-explained',await inherited.getByText('Зритель Plus доступен в составе Стример Plus.',{exact:true}).count()===1);
 check('unlinked-streamer-retains-subscription',await inherited.getByText('Подписка сохранена. Для публикаций подключите свой Twitch-канал.',{exact:true}).count()===1);
 await shot(inherited,'viewer-inherited');await inherited.close();
 check('no-js-errors',report.errors.length===0,report.errors);check('no-api-or-auth-requests',!report.network.some(x=>/api|telegram|init_data/.test(x)),report.network);
 check('source-snapshot-unchanged',files.every(f=>report.sources[f]===hash(f)));
 report.status=report.checks.every(x=>x.pass)?'PASS':'FAIL';
})().catch(e=>{report.status='ERROR';report.failure=e.stack;}).finally(async()=>{await browser?.close();fs.writeFileSync(path.join(out,'report.json'),JSON.stringify(report,null,2));console.log(JSON.stringify({status:report.status,checks:report.checks.length,failed:report.checks.filter(x=>!x.pass).map(x=>x.name),error:report.failure,screenshots:report.screenshots.length}));process.exitCode=report.status==='PASS'?0:1;});
