/* Local design preview only. No authenticated API, Telegram or payments. */
const {chromium,webkit}=require('playwright');
const assert=require('assert/strict'),fs=require('fs'),path=require('path'),crypto=require('crypto');
const OUT=process.env.PREVIEW_DIR,BASE=process.env.CONCEPT_URL||'http://127.0.0.1:8766/';
assert(OUT,'PREVIEW_DIR is required');
const reports=[],images=[];
const digest=file=>crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
const sourceNames=['index.html','selected.css','selected.js','plus-catalog.js','preview.html'];
const sources=()=>Object.fromEntries(sourceNames.map(name=>[name,digest(path.join(OUT,name))]));
let current='start';
async function layout(page,{fourColumns=false}={}){
 await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
 const result=await page.evaluate(()=>{
  const screen=document.querySelector('.screen'),bounds=screen.getBoundingClientRect();
  const buttons=[...screen.querySelectorAll('.tabbar button')].map(button=>{
   const rect=button.getBoundingClientRect();
   return {label:button.innerText.trim(),x:rect.x,y:rect.y,width:rect.width,height:rect.height,
    fits:rect.left>=bounds.left-.5&&rect.right<=bounds.right+.5&&rect.bottom<=bounds.bottom+.5,
    unclipped:button.scrollWidth<=button.clientWidth+1};
  });
  const content=screen.querySelector('.screen-content');
  return {buttons,overflow:document.documentElement.scrollWidth>innerWidth+1,
   contentHeight:content.clientHeight,contentFits:content.scrollWidth<=content.clientWidth+1};
 });
 assert.equal(result.buttons.length,4,'В нижнем меню должны быть четыре пункта');
 assert(result.buttons.every(b=>b.fits&&b.unclipped&&b.width>=44&&b.height>=44),'Пункты меню видимы, читаемы и доступны');
 assert(!result.overflow&&result.contentFits,'Нет горизонтального переполнения');
 assert(result.contentHeight>=140,'Прокрутка основного содержания остаётся доступной');
 if(fourColumns){
  assert(result.buttons.every(b=>Math.abs(b.y-result.buttons[0].y)<1),'Обычный размер текста: одна строка меню');
  assert(Math.max(...result.buttons.map(b=>b.width))-Math.min(...result.buttons.map(b=>b.width))<2,'Четыре равные области меню');
 }
 return result;
}
async function capture(page,name){
 const file=`plus-navigation-${name}.png`;
 await page.screenshot({path:path.join(OUT,file),fullPage:true});
 images.push({file,sha256:digest(path.join(OUT,file))});
}
(async()=>{
 let failure=null;
 try{
  for(const [engine,type] of [['Chromium',chromium],['WebKit',webkit]]){
   const browser=await type.launch({headless:false});
   const page=await browser.newPage({viewport:{width:390,height:844}}),errors=[],external=[];
   page.on('pageerror',error=>errors.push(error.message));
   page.on('request',request=>{
    const url=new URL(request.url());
    if(request.method()!=='GET'||(!['file:','data:'].includes(url.protocol)&&url.origin!==new URL(BASE).origin))external.push(request.url());
   });
   const go=(params)=>page.goto(BASE+'?single=1&'+new URLSearchParams(params));
   try{
    for(const plan of ['free','viewer','streamer']){
     current=`${engine}/${plan}/viewer-plus-profile-back`;
     await go({screen:'viewer',plan,theme:'light'});
     const menu=page.locator('.tabbar');
     assert.deepEqual(await menu.locator('button').allTextContents(),['Главная','Стримеры','Профиль','Plus']);
     const before=await page.evaluate(()=>JSON.stringify(window.plusCatalog)+window.selectedPrototype.plan());
     await page.locator('.screen-content').evaluate(element=>{element.scrollTop=230;});
     const scroll=await page.locator('.screen-content').evaluate(element=>element.scrollTop);
     await menu.getByRole('button',{name:'Plus',exact:true}).click();
     assert.equal(await page.locator('.screen-content').getAttribute('data-route'),'subscription');
     await page.getByRole('heading',{name:plan==='free'?'Возможности Plus':'Моя подписка',exact:true}).waitFor();
     assert.equal(await menu.locator('[aria-current=page]').innerText(),'Plus');
     assert.equal(await menu.locator('[aria-current=page]').count(),1);
     if(plan!=='free'){
      assert((await page.locator('.subscription-plan').innerText()).includes(plan==='viewer'?'Viewer Plus':'Streamer Plus'));
      assert((await page.locator('.subscription-plan').innerText()).includes('9 октября 2026'));
      assert((await page.locator('.subscription-plan').innerText()).includes('Активен'));
      await page.locator('.operation-history summary').click();
      assert.equal(await page.locator('.operation-history').getAttribute('open'),'');
     }
     for(const [choice,amount]of [['viewer',150],['streamer',200]]){
      await page.locator(`[data-plan-choice=${choice}]`).click();
      assert((await page.locator('.catalog-price').innerText()).includes(`${amount} ₽`));
      assert(await page.locator('.benefit-list .free-line').count()>0);
      if(choice==='streamer')assert((await page.locator('.included').innerText()).includes('Все возможности Viewer Plus уже включены'));
     }
     await page.locator('[data-purchase]').click();
     assert.equal(await page.locator('.screen-content').getAttribute('data-route'),'purchase');
     assert.equal(await menu.locator('[aria-current=page]').innerText(),'Plus');
     await menu.getByRole('button',{name:'Plus',exact:true}).click();
     assert.equal(await page.locator('.screen-content').getAttribute('data-route'),'subscription');
     await menu.getByRole('button',{name:'Plus',exact:true}).click();
     await page.locator('[data-back]').click();
     assert.equal(await page.locator('.screen-content').getAttribute('data-route'),'list');
     assert.equal(await page.evaluate(()=>document.activeElement.dataset.tab),'plus');
     assert.equal(await page.locator('.screen-content').evaluate(element=>element.scrollTop),scroll);
     await menu.getByRole('button',{name:'Профиль',exact:true}).click();
     await page.locator('[data-dialog=subscription]').click();
     assert.equal(await menu.locator('[aria-current=page]').innerText(),'Plus');
     await page.locator('[data-back]').click();
     assert.equal(await page.locator('.screen-content').getAttribute('data-route'),'profile');
     assert.equal(await page.evaluate(()=>document.activeElement.dataset.dialog),'subscription');
     await menu.getByRole('button',{name:'Профиль',exact:true}).focus();
     await page.keyboard.press('Tab');
     assert.equal(await page.evaluate(()=>document.activeElement.dataset.tab),'plus');
     await page.keyboard.press('Enter');
     assert.equal(await page.locator('.screen-content').getAttribute('data-route'),'subscription');
     assert.equal(await page.evaluate(()=>JSON.stringify(window.plusCatalog)+window.selectedPrototype.plan()),before);
     reports.push({engine,plan,journey:'viewer/profile/purchase/back/keyboard',status:'PASS'});
     if(engine==='Chromium')await capture(page,`${plan}-390`);
     current=`${engine}/${plan}/streamer-plus-back`;
     await go({screen:'channel',plan,theme:'light'});
     assert.deepEqual(await page.locator('.tabbar button').allTextContents(),['Мой канал','Посты','Профиль','Plus']);
     await page.locator('.tabbar [data-tab=plus]').click();
     assert.equal(await page.locator('[data-mode=streamer]').getAttribute('aria-pressed'),'true');
     assert.equal(await page.locator('.screen-content').getAttribute('data-route'),'subscription');
     await page.locator('[data-back]').click();
     assert.equal(await page.locator('.screen-content').getAttribute('data-route'),'channel');
     reports.push({engine,plan,journey:'streamer/plus/back',status:'PASS'});
    }
    current=`${engine}/channel-action-focus-on-resize`;
    await page.setViewportSize({width:360,height:844});
    await go({screen:'channel',plan:'streamer',theme:'light'});
    await layout(page);
    await page.locator('[data-channel-action]').focus();
    await page.setViewportSize({width:360,height:440});
    await layout(page);
    assert.equal(await page.evaluate(()=>document.activeElement.dataset.channelAction),'rights','Изменение окна сохраняет фокус действия');
    await page.addStyleTag({content:'html{font-size:200% !important;}'});
    await layout(page);
    assert.equal(await page.evaluate(()=>document.activeElement.dataset.channelAction),'rights','Перенос действия в прокрутку сохраняет фокус');
    reports.push({engine,journey:'channel-action-focus-on-resize',status:'PASS'});
    for(const width of [360,390,430,768,1440])for(const theme of ['light','dark','telegram']){
     current=`${engine}/${width}/${theme}/layout`;
     await page.setViewportSize({width,height:844});
     await go({screen:'viewer',plan:'free',theme});
     reports.push({engine,width,theme,text:100,screen:'viewer',...(await layout(page,{fourColumns:true}))});
     await page.locator('.tabbar [data-tab=plus]').click();
     reports.push({engine,width,theme,text:100,screen:'subscription',...(await layout(page,{fourColumns:true}))});
     if(engine==='Chromium'&&width===1440&&theme==='light')await capture(page,'desktop-1440');
     if(engine==='Chromium'&&width===390&&theme==='dark')await capture(page,'dark-390');
    }
    for(const width of [360,390])for(const theme of ['light','dark'])for(const screen of ['viewer','channel','profile']){
     current=`${engine}/${width}/${theme}/${screen}/text200-short`;
     await page.setViewportSize({width,height:440});
     await go({screen,plan:'streamer',theme});
     await page.addStyleTag({content:'html{font-size:200% !important;}'});
     reports.push({engine,width,theme,text:200,height:440,screen,...(await layout(page))});
     await page.locator('.tabbar [data-tab=plus]').click();
     reports.push({engine,width,theme,text:200,height:440,screen:'subscription',...(await layout(page))});
     if(engine==='Chromium'&&width===360&&theme==='light'&&screen==='viewer')await capture(page,'text200-360-short');
    }
    current=`${engine}/board-and-portable`;
    await page.setViewportSize({width:1440,height:1000});
    await page.goto(BASE+'?plan=free');
    for(const key of ['viewer','channel','profile']){
     const screen=page.locator(`.screen[data-key=${key}]`);
     await screen.locator('.tabbar [data-tab=plus]').click();
     assert.equal(await screen.locator('.screen-content').getAttribute('data-route'),'subscription');
    }
    await page.goto('file:///'+path.join(OUT,'preview.html').replaceAll('\\','/')+'?single=1&plan=free');
    await page.locator('.tabbar [data-tab=plus]').click();
    assert.equal(await page.locator('.screen-content').getAttribute('data-route'),'subscription');
    assert.equal(external.length,0,'Нет внешних запросов/платежей');
    assert.equal(errors.length,0,'Нет JavaScript ошибок');
    reports.push({engine,journey:'board/portable',status:'PASS',external:external.length,errors:errors.length,browser:browser.version()});
   }finally{await browser.close();}
  }
 }catch(error){failure={check:current,message:error.message};}
 const status=failure?'RED':'PASS';
 const report={status,failure,reports,images,sources:sources(),product_changed:false,deployed:false,native:'NOT TESTED'};
 fs.writeFileSync(path.join(OUT,failure?'plus-navigation-red.json':'plus-navigation-qa.json'),JSON.stringify(report,null,2)+'\n');
 console.log(JSON.stringify({status,checks:reports.length,failure,screenshots:images.length}));
 if(failure)process.exitCode=1;
})();
