/* Sequential local fixtures; each child verifies its complete source snapshot. */
const fs=require('node:fs'),path=require('node:path'),{spawnSync}=require('node:child_process');
const root=process.cwd(),engine=process.env.DESIGN_QA_ENGINE||'chromium';
const output=path.resolve(process.env.DESIGN_QA_BATCH_OUT||`docs/audits/mini-app-polish-2026-10-03/AFTER/${engine}`);
const cases=[
  ['matrix','free-six'],['shell','free-six'],['theme','free-six'],
  ['viewer-free','free-empty'],['viewer-free','free-six'],['video','plus-two-hundred'],
  ['viewer-settings','free-six','quiet'],['viewer-settings','plus-two-hundred','filter-folder'],
  ['viewer-settings','plus-two-hundred','category'],['viewer-settings','plus-two-hundred','reminder'],
  ['viewer-settings','history','history'],['streamer-connection','streamer-unconnected'],
  ['streamer-connection','channel-permissions'],['streamer-connection','legacy-group'],
  ['streamer-posts','streamer-posts'],['streamer-posts','free-six'],
  ['purchase','free-six'],['purchase','purchase-history'],['legal','legal-unready'],
  ['oauth-results','channel-permissions'],
];
const results=[];
fs.mkdirSync(output,{recursive:true});
for(const [journey,scenario,part] of cases){
  const name=[journey,scenario,part].filter(Boolean).join('-');
  if(process.env.DESIGN_QA_MATCH&&!name.includes(process.env.DESIGN_QA_MATCH))continue;
  const dir=path.join(output,name);fs.mkdirSync(dir,{recursive:true});
  const args=['scripts/mini_app_redesign_browser_qa.cjs','--engine',engine,'--journey',journey,'--scenario',scenario];
  if(part)args.push('--part',part);
  const result=spawnSync(process.execPath,args,{cwd:root,env:{...process.env,MINI_APP_QA_SCREENSHOTS:dir},encoding:'utf8',timeout:240000,windowsHide:true});
  fs.writeFileSync(path.join(dir,'run.log'),(result.stdout||'')+(result.stderr||''));
  const entry={name,exit:result.status,error:result.error?.message};results.push(entry);
  console.log(JSON.stringify(entry));
  fs.writeFileSync(path.join(output,'batch.json'),JSON.stringify({engine,results},null,2));
  if(result.status!==0){process.exitCode=1;break;}
}
