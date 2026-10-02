const {chromium,webkit}=require('playwright');
const fs=require('fs'),path=require('path'),crypto=require('crypto'),assert=require('assert');
const OUT=process.env.PREVIEW_DIR||path.resolve(__dirname,'..');
const BASE=process.env.CONCEPT_URL||'http://127.0.0.1:8766/';
const digest=n=>crypto.createHash('sha256').update(fs.readFileSync(path.join(OUT,n))).digest('hex');
(async()=>{
 const reports=[],images=[];
 for(const [engine,type] of [['Chromium',chromium],['WebKit',webkit]]){
  const b=await type.launch({headless:false});
  try{
   const p=await b.newPage({viewport:{width:390,height:844}}),errors=[],unexpected=[];
   p.on('pageerror',e=>errors.push(e.message));
   p.on('request',r=>{if(r.method()!=='GET'||new URL(r.url()).origin!==new URL(BASE).origin)unexpected.push({method:r.method(),url:r.url()});});
   const go=(screen,plan='viewer')=>p.goto(BASE+'bank-review.html?screen='+screen+'&plan='+plan);
   const checks=[];
   for(const [plan,amount] of [['viewer',150],['streamer',200]]){
    await go(plan);const initial=await p.evaluate(()=>JSON.stringify(window.plusCatalog));
    await p.getByRole('button',{name:`Подключить ${plan==='viewer'?'Viewer':'Streamer'} Plus — ${amount} ₽`,exact:true}).click();
    await p.getByRole('heading',{name:'Как оплатить?',exact:true}).waitFor();
    assert((await p.locator('.approval-price').innerText()).includes(`${amount} ₽`));
    await p.getByRole('button',{name:'Банковская карта',exact:false}).click();
    assert.equal(await p.locator('[data-method=bank_card]').getAttribute('aria-pressed'),'true');
    await p.getByRole('button',{name:`Оплатить ${amount} ₽`,exact:true}).click();
    await p.getByRole('heading',{name:'Оплата временно недоступна',exact:true}).waitFor();
    assert((await p.getByRole('status').innerText()).includes('Мы заканчиваем подключение платёжной системы.'));
    assert.equal(await p.evaluate(()=>JSON.stringify(window.plusCatalog)),initial);
    await p.reload();await p.getByRole('heading',{name:'Оплата временно недоступна',exact:true}).waitFor();
    await p.getByRole('button',{name:'← Способ оплаты',exact:true}).click();
    await p.getByRole('button',{name:'Узнать об оплате Stars',exact:true}).click();
    assert((await p.getByRole('status').innerText()).includes('Цена Stars ещё не утверждена'));
    await p.getByRole('button',{name:'СБП',exact:false}).click();
    await p.getByRole('button',{name:`Оплатить ${amount} ₽`,exact:true}).click();
    await p.getByRole('heading',{name:'Оплата временно недоступна',exact:true}).waitFor();
    checks.push({name:`${plan}: exact RUB/period, both method buttons, honest unavailable/reload/back, Stars TBD, unchanged catalog`,status:'PASS'});
   }
   await go('streamer');
   assert.equal(await p.locator('.approval-include').innerText(),'Все возможности Viewer Plus включены');
   await p.locator('summary').click();
   assert.equal(await p.locator('details li').count(),8);
   assert((await p.locator('main').innerText()).includes('Подтверждённые отправки'));
   await go('viewer');assert((await p.locator('main').innerText()).includes('Тихие часы'));
   for(const [screen,file,title] of [['privacy','PRIVACY-POLICY-DRAFT.md','Политика конфиденциальности'],['agreement','USER-AGREEMENT-DRAFT.md','Пользовательское соглашение']]){
    await p.locator('[data-doc='+screen+']').click();
    await p.waitForFunction(()=>document.querySelector('pre')?.textContent.includes('Редакция для проверки'));
    assert.equal(await p.locator('pre').textContent(),fs.readFileSync(path.join(OUT,'legal',file),'utf8'));
    await p.getByRole('button',{name:'← Документы',exact:true}).click();
    assert.equal(await p.locator('[data-doc]').count(),2);
   }
   await p.getByRole('button',{name:'Открыть поддержку',exact:true}).click();
   await p.getByRole('heading',{name:'Контакт ещё не указан',exact:true}).waitFor();
   assert.equal(await p.locator('main a[href^="mailto:"],main a[href^="https://t.me/"]').count(),0);
   checks.push({name:'Full inherited features, Free quiet hours, both exact draft documents accessible before payment, no fake support contact',status:'PASS'});
   let layoutCases=0;
   for(const width of [360,390])for(const scale of [1,2])for(const screen of ['viewer','streamer','documents','support','methods','unavailable']){
    await p.setViewportSize({width,height:844});await go(screen,'streamer');
    if(scale===2)await p.addStyleTag({content:'html{font-size:200%!important}'});
    const bounds=await p.evaluate(()=>({w:innerWidth,body:document.body.scrollWidth,main:document.querySelector('main').scrollWidth,mainW:document.querySelector('main').clientWidth,buttons:[...document.querySelectorAll('button')].filter(e=>e.getBoundingClientRect().width>0).map(e=>({h:e.getBoundingClientRect().height,l:e.getBoundingClientRect().left,r:e.getBoundingClientRect().right}))}));
    assert(bounds.body<=bounds.w+1,JSON.stringify({width,scale,screen,bounds}));
    assert(bounds.buttons.every(x=>x.h>=44-0.2&&x.l>=-1&&x.r<=width+1));layoutCases++;
   }
   for(const screen of ['privacy','agreement']){
    await p.setViewportSize({width:360,height:844});await go(screen);
    await p.waitForFunction(()=>document.querySelector('pre')?.textContent.includes('Редакция для проверки'));
    await p.addStyleTag({content:'html{font-size:200%!important}'});
    assert(await p.evaluate(()=>document.body.scrollWidth<=innerWidth+1));layoutCases++;
   }
   checks.push({name:'360/390, full text at 100/200%, document reflow and actions at least 44px',status:'PASS',cases:layoutCases});
   await go('methods');await p.locator('[data-pay]').focus();await p.keyboard.press('Enter');
   await p.getByRole('heading',{name:'Оплата временно недоступна',exact:true}).waitFor();
   assert.equal(await p.evaluate(()=>document.activeElement?.textContent),'Оплата временно недоступна');
   checks.push({name:'Keyboard purchase and meaningful focus at result; zero payment/outbound requests',status:'PASS'});
   if(engine==='Chromium'){
    for(const screen of ['board','viewer','streamer','documents','methods','unavailable','support']){
     await p.setViewportSize(screen==='board'?{width:1440,height:1100}:{width:390,height:844});await go(screen,screen==='streamer'?'streamer':'viewer');
     await p.evaluate(()=>document.fonts.ready);
     const file=`bank-${screen}-${screen==='board'?'board':'390'}.png`;
     await p.screenshot({path:path.join(OUT,file),fullPage:true});
     images.push({file,screen,mode:'full page',engine,browser:b.version(),sha256:digest(file)});
    }
   }
   assert.deepEqual(errors,[]);assert.deepEqual(unexpected,[]);
   reports.push({engine,browser:b.version(),status:'PASS',checks,pageErrors:errors,paymentOrOutboundRequests:unexpected});
  }finally{await b.close();}
 }
 const sources=Object.fromEntries(['bank-review.html','selected.css','plus-catalog.js','legal/PRIVACY-POLICY-DRAFT.md','legal/USER-AGREEMENT-DRAFT.md'].map(n=>[n,digest(n)]));
 fs.writeFileSync(path.join(OUT,'bank-review-qa.json'),JSON.stringify({status:'PASS',sources,reports,images,productChanged:false,realPlatega:'NOT TESTED',nativeTelegram:'NOT TESTED',merchantApproval:'NOT READY'},null,2)+'\n');
 console.log(JSON.stringify({status:'PASS',engines:reports.map(r=>r.engine),layoutCases:52,screenshots:images.length,paymentRequests:0}));
})().catch(e=>{console.error(e);process.exit(1)});
