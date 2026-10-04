// Local component regression: fake API only, no server, Telegram or credentials.
const fs = require('fs'), path = require('path'), assert = require('node:assert/strict');
const {chromium, webkit} = require('playwright');
const root = path.resolve(__dirname, '..'), ui = path.join(root, 'bot/mini_app_ui');
const out = path.join(root, 'docs/audits/production-readiness-2026-10-04');
const cache = new Map();
function moduleUrl(name) {
  if (cache.has(name)) return cache.get(name);
  const source = fs.readFileSync(path.join(ui, name), 'utf8').replace(/from ['"]\.\/(.*?)['"]/g,
    (_, file) => `from '${moduleUrl(file)}'`);
  const url = 'data:text/javascript;base64,' + Buffer.from(source).toString('base64');
  cache.set(name, url); return url;
}
(async () => {
  const engine = process.env.RETRY_ENGINE || 'chromium';
  const browser = await ({chromium, webkit}[engine]).launch({headless:false});
  try {
    for (const width of [390, 1440]) for (const theme of ['light', 'dark']) {
      const page = await browser.newPage({viewport:{width, height:844}});
      try {
        await page.setContent(`<html data-theme="${theme}"><style>${fs.readFileSync(path.join(ui,'app.css'),'utf8')}</style><main id="content"></main></html>`);
        await page.evaluate(async ({url, apiUrl, themeUrl, theme}) => {
          const {createStreamerFeature} = await import(url), {ApiError} = await import(apiUrl);
          const {createThemeController} = await import(themeUrl);
          window.themeController = createThemeController({storage:{getItem:()=>theme,setItem:()=>{}}});
          window.calls = 0;
          const api = {storage:{getItem:()=>null,setItem:()=>{}}, post:async()=>{
            ++window.calls;
            if (window.calls===1) throw new ApiError(0,'network_error');
            return {connected:false,communities:[],plus_active:false};
          }};
          const route = {mode:'streamer',tab:'channel',detail:null};
          const router = {state:route,refresh:()=>{
            const target=document.querySelector('#content'); target.replaceChildren();
            window.feature.render(target,route);
          }};
          window.feature=createStreamerFeature(api,()=>router,{}); router.refresh();
        }, {url:moduleUrl('streamer.js'),apiUrl:moduleUrl('api.js'),themeUrl:moduleUrl('theme.js'),theme});
        assert.equal(await page.evaluate(()=>document.documentElement.style.colorScheme),theme);
        const retry = page.getByRole('button',{name:'Повторить',exact:true});
        await retry.waitFor({timeout:3000});
        await page.screenshot({path:path.join(out,`retry-${engine}-${width}-${theme}.png`)});
        await retry.click();
        await page.getByRole('heading',{name:'Мой канал',exact:true}).waitFor({timeout:3000});
        assert.equal(await page.evaluate(()=>window.calls),2);
        assert.equal(await retry.count(),0);
        await page.evaluate(()=>{window.feature.dispose();window.themeController.dispose();});
        console.log(`PASS ${engine} ${width} ${theme}: first error -> retry -> loaded`);
      } finally { await page.close(); }
    }
  } finally { await browser.close(); }
})().catch(error=>{console.error(error);process.exitCode=1;});
