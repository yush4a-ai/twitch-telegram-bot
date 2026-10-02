/* Actual server headers/assets, local HTTPS-origin emulation, no Telegram account. */
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict'),crypto=require('node:crypto');
const root=process.cwd();process.env.NODE_PATH='C:/Users/yusha/.agents/skills/playwright-skill/node_modules';require('node:module').Module._initPaths();
const pw=require(path.join(path.dirname(require.resolve('playwright-core/package.json')),'lib/coreBundle.js')).inprocess.createInProcessPlaywright();const {chromium,webkit}=pw;
const {fixtureServer,installSdk}=require(path.join(root,'scripts/mini_app_redesign_browser_qa.cjs'));
const {sources}=require(path.join(root,'scripts/mini_app_redesign_matrix_qa.cjs'));
const engine=process.argv[2]||'chromium',output=process.env.MINI_APP_QA_SCREENSHOTS||__dirname;
fs.mkdirSync(output,{recursive:true});
const report={engine,native:'NOT TESTED',source:'actual /app behind local HTTPS-origin interception',sources:sources(root),errors:[],externalRequests:[],screenshots:[],cases:[]};
(async()=>{const server=await fixtureServer('free-six');let browser;
try{
  assert.equal(new URL(server.url).hostname,'127.0.0.1');browser=await({chromium,webkit})[engine].launch({headless:false});report.version=browser.version();const context=await browser.newContext({viewport:{width:430,height:900}});
  const appOrigin='https://mini-app.qa.invalid';
  await context.route('**/*',async route=>{
    const u=new URL(route.request().url());
    if(['https://web.telegram.org','https://attacker.qa.invalid'].includes(u.origin))return route.fulfill({contentType:'text/html',body:`<!doctype html><html lang="ru"><meta charset="utf-8"><title>Local frame policy QA</title><iframe id="miniapp" title="TwitchSignalBot" src="${appOrigin}/app" style="width:390px;height:844px;border:0"></iframe></html>`});
    if(u.hostname==='telegram.org')return route.fulfill({contentType:'application/javascript',body:''});
    if(u.origin===appOrigin){const own=new URL(u.pathname+u.search,server.url);assert.equal(own.origin,new URL(server.url).origin);const method=route.request().method(),body=route.request().postData();const response=await fetch(own,{method,headers:{'Content-Type':route.request().headers()['content-type']||'application/json'},...(body===null?{}:{body})});return route.fulfill({status:response.status,headers:Object.fromEntries(response.headers),body:Buffer.from(await response.arrayBuffer())});}
    report.externalRequests.push(u.origin+u.pathname);return route.abort();
  });
  for(const [parent,allowed] of [['https://web.telegram.org/qa',true],['https://attacker.qa.invalid/qa',false]]){
    const page=await context.newPage(),messages=[];page.on('pageerror',e=>report.errors.push(e.message));page.on('console',m=>{if(m.type()==='error')messages.push(m.text());});await installSdk(page);await page.goto(parent);
    const frame=page.frameLocator('#miniapp');let loaded=false;
    try{await frame.getByRole('heading',{name:'Главная',exact:true}).waitFor({timeout:allowed?10000:2000});loaded=true;}catch{}
    report.cases.push({parent,allowed,loaded,messages});assert.equal(loaded,allowed,'Only exact official Telegram Web origin may embed /app');
    if(allowed){await frame.getByRole('button',{name:'Plus',exact:true}).click();await frame.getByText('150 ₽ / месяц',{exact:true}).waitFor();assert.equal(await frame.locator('#tab-bar button').count(),4);assert.deepEqual(messages,[]);}
    else assert(messages.some(m=>m.includes('frame-ancestors')),'Foreign origin is rejected by CSP before application execution');
    const file=`frame-${engine}-${allowed?'telegram-origin':'foreign-blocked'}.png`;if(engine==="webkit"){const pi=pw._connection.toImpl(page);const shot=await pi.delegate._session.send("Page.snapshotRect",{x:0,y:0,width:430,height:900,coordinateSystem:"Viewport",omitDeviceScaleFactor:false});fs.writeFileSync(path.join(output,file),Buffer.from(shot.dataURL.split(",")[1],"base64"));}else await page.screenshot({path:path.join(output,file),caret:"initial"});if(allowed)assert.deepEqual(messages,[],"Allowed frame stays console-clean after screenshot");report.screenshots.push({file,sha256:crypto.createHash('sha256').update(fs.readFileSync(path.join(output,file))).digest('hex')});await page.close();
  }
  assert.deepEqual(report.errors,[]);assert.deepEqual(report.externalRequests,[]);assert.deepEqual(sources(root),report.sources);report.status='PASS';
}catch(error){report.status='FAIL';report.failure=error.stack;throw error;}
finally{await browser?.close();await server.close();fs.writeFileSync(path.join(output,`frame-${engine}-qa.json`),JSON.stringify(report,null,2));console.log(JSON.stringify({engine,status:report.status,cases:report.cases}));}
})().catch(e=>{console.error(e.message);process.exitCode=1;});
