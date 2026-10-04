const {chromium, webkit}=require('playwright');
const fs=require('fs');
const assert=require('assert');
const TARGET_URL=process.env.TARGET_URL||'http://127.0.0.1:8874/';
const OUT='C:/Users/yusha/Desktop/cloude/TG-BOT.(TwtichSignal)/docs/audits/telegram-copy-audit-2026-10-03';
(async()=>{
 const results=[];
 for(const [name,engine] of [['chromium',chromium],['webkit',webkit]]) {
  const browser=await engine.launch({headless:false});
  const page=await browser.newPage();const errors=[];
  page.on('pageerror',e=>errors.push(String(e)));await page.goto(TARGET_URL);
  const count=await page.locator('[data-screen]').count();assert.equal(count,19);
  for(const width of [360,390,768,1440])for(const theme of ['dark','light']) {
   await page.setViewportSize({width,height:844});await page.selectOption('#theme',theme);
   for(let i=0;i<count;i++) {
    await page.locator(`[data-screen="${i}"]`).click();
    const view=page.locator(`[data-view="${i}"]`);assert(await view.isVisible());
    assert.equal(await page.locator('[data-view]:visible').count(),1);
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
    assert(await view.locator('.caption').innerText());
    for(const img of await view.locator('img').all())assert(await img.evaluate(e=>e.complete&&e.naturalWidth>0));
    if([2,3,4].includes(i))assert.equal(await view.locator('blockquote').count(),i===3?8:4);
    if(i===2)assert((await view.innerText()).includes('150 ₽'));
    if(i===3)assert((await view.innerText()).includes('300 ₽'));
    if(i===4)assert((await view.innerText()).includes('09.10.2026 05:28'));
    if(name==='chromium'&&width===390&&[1,2,4,6,14,15,17].includes(i))
     await page.locator('main').screenshot({path:`${OUT}/${name}-${theme}-screen-${i}.png`});
    results.push({browser:name,width,theme,screen:i,status:'PASS'});
   }
  }
  assert.deepEqual(errors,[]);await browser.close();
 }
 fs.writeFileSync(`${OUT}/BROWSER-QA.json`,JSON.stringify({status:'PASS',checks:results.length,screenshots:14,native:'NOT TESTED',scope:'Actual handler text in browser gallery; static Telegram buttons; interactive gallery navigation; no outbound or payments',results},null,2));
 console.log(JSON.stringify({status:'PASS',checks:results.length,screenshots:14,native:'NOT TESTED'}));
})().catch(e=>{console.error(e);process.exit(1)});
