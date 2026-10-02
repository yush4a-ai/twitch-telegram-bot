const {chromium}=require('playwright'),fs=require('fs'),path=require('path'),crypto=require('crypto');
const OUT=process.env.PREVIEW_DIR||path.resolve(__dirname,'..'),BASE=process.env.CONCEPT_URL||'http://127.0.0.1:8766/';
(async()=>{const b=await chromium.launch({headless:false}),p=await b.newPage(),entries=[];try{
 const capture=async(name,query,mode='viewport',action)=>{
  await p.setViewportSize(mode==='board'?{width:1440,height:1320}:{width:390,height:844});await p.goto(BASE+'?'+query);await p.locator('.screen').first().waitFor();
  if(action)await action();await p.locator('img').evaluateAll(es=>Promise.all(es.map(async e=>{e.loading='eager';try{await e.decode();}catch{}})));
  if(mode==='full')await p.addStyleTag({content:'.single .screen{height:auto;min-height:100dvh}.single .screen-content{flex:none;overflow:visible}.single .tabbar{position:static}'});
  const file=name+'.png';await p.screenshot({path:path.join(OUT,file),fullPage:mode!=='viewport'});entries.push({file,mode,query,engine:'Chromium',browser:b.version(),sha256:crypto.createHash('sha256').update(fs.readFileSync(path.join(OUT,file))).digest('hex')});
 };
 await capture('themes-own-dark-board','theme=dark&plan=streamer','board');
 await capture('themes-telegram-light-board','theme=telegram&telegram=light&plan=streamer','board');
 await capture('themes-telegram-dark-board','theme=telegram&telegram=dark&plan=streamer','board');
 await capture('plans-free-viewer-streamer-board','view=plan-states&theme=light','board');
 for(const tier of ['viewer','streamer'])for(const mode of ['viewport','full'])await capture(`plans-${tier}-benefits-390-${mode}`,`single=1&screen=profile&route=subscription&plan=free&benefits=${tier}&theme=light`,mode);
 await capture('payment-selection-390-viewport','single=1&screen=profile&route=subscription&plan=free&theme=light','viewport',async()=>{await p.locator('[data-plan-choice=streamer]').click();await p.locator('[data-purchase]').click();await p.locator('[data-period]').selectOption('month');});
 await capture('payment-demo-board','view=payment-states&plan=free&theme=light','board',async()=>{await p.locator('[data-key=payment-choose] [data-period]').selectOption('month');});
 for(const state of ['pending','cancel','error','success'])await capture(`payment-${state}-390-viewport`,'single=1&screen=profile&route=subscription&plan=free&theme=light','viewport',async()=>{await p.locator('[data-purchase]').click();await p.locator('[data-period]').selectOption('month');await p.locator('[data-buy]').click();if(state==='cancel')await p.locator('[data-payment-cancel]').click();if(['error','success'].includes(state))await p.locator(`[data-demo-outcome=${state}]`).click();});
 await capture('payment-upgrade-unagreed-390-viewport','single=1&screen=profile&route=subscription&plan=plus&benefits=streamer&theme=dark','viewport',async()=>{await p.locator('[data-purchase]').click();});
 await capture('plans-text200-360-viewport','single=1&screen=profile&route=subscription&plan=free&benefits=streamer&theme=dark','viewport',async()=>{await p.setViewportSize({width:360,height:844});await p.addStyleTag({content:'html{font-size:200%!important}'});await p.locator('[data-feature=postVideo]').scrollIntoViewIfNeeded();});
 await capture('payment-short-390-440-viewport','single=1&screen=profile&route=subscription&plan=free&theme=dark','viewport',async()=>{await p.setViewportSize({width:390,height:440});await p.locator('[data-purchase]').click();await p.locator('[data-period]').selectOption('month');await p.locator('[data-buy]').scrollIntoViewIfNeeded();});
 const sources=Object.fromEntries(['index.html','selected.css','selected.js','plus-catalog.js','preview.html'].map(n=>[n,crypto.createHash('sha256').update(fs.readFileSync(path.join(OUT,n))).digest('hex')]));fs.writeFileSync(path.join(OUT,'themes-plans-screenshots.json'),JSON.stringify({sources,entries,fullContent:'Full images use only temporary height/overflow CSS to expose original DOM; no compositing or image edits',nativeTelegram:'NOT TESTED'},null,2));console.log(JSON.stringify({captured:entries.length,files:entries.map(e=>e.file)}));
 }finally{await b.close();}})().catch(e=>{console.error(e);process.exit(1)});
