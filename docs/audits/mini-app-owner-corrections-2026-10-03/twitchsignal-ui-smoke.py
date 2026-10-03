import importlib.util,json,pathlib,sys,hashlib,subprocess,unittest
ops_path=pathlib.Path(__file__).with_name('twitchsignal-ui-ops.py')
spec=importlib.util.spec_from_file_location('ops',ops_path);ops=importlib.util.module_from_spec(spec);spec.loader.exec_module(ops)
ROOT=ops.ROOT;T=ops.T
ASSET_NAMES=('app.css','app.js','telegram.js','theme.js','router.js','api.js','components.js','viewer.js','streamer.js','streamer_posts.js','subscription.js','purchase.js','profile.js','support.js','reports.js','plus-mascot.png','mascot-cutout.png')
UNSIGNED_NAMES=('/app/api/bootstrap','/app/api/viewer/state','/app/api/streamer/profile','/app/api/purchase/prepare','/app/api/viewer/reports','/app/api/streamer/reports','/app/api/viewer/favorite','/app/api/viewer/unfollow/undo','admin')
def verify(result,sha,hashes,versions,deployment,http_hashes=None):
 if http_hashes is None:http_hashes=hashes
 assert result['deployment']==deployment
 assert result['cli_sha']==sha
 assert result['bot']['username']=='TwitchSignalTestbot' and result['bot']['is_bot'] is True
 assert result['menu']=={'type':'web_app','text':'Приложение','url':'https://'+T['staging_domain']+'/app'}
 assert result['integrity']=='ok' and result['foreign_key_check']==[] and result['versions']==versions
 assert result['money']=={'mode':'offline','external_create':False,'invoice':False,'live_callback':404}
 assert result['file_hashes']==hashes
 assert result['health']=={'status':'ok','http':200}
 assert result['app']=={'http':200,'xfo':None,'ancestors':'https://web.telegram.org','hash':http_hashes['bot/mini_app_ui/index.html']}
 assert result['unsigned']=={n:401 for n in UNSIGNED_NAMES}
 assert result['http_asset_hashes']=={n:http_hashes['bot/mini_app_ui/'+n] for n in ASSET_NAMES}
 assert result['legal_http']=={n:503 for n in ['privacy','agreement','support','tariffs','payments']}
 assert result['trial_owner_allowlisted'] is True
 return True
def expected_release_hashes(sha,names):
 from scripts.staging_deploy import _committed_bundle
 with _committed_bundle(sha) as bundle:
  raw={n:hashlib.sha256((bundle/n).read_bytes()).hexdigest() for n in names}
  served_names=['bot/mini_app_ui/index.html']+['bot/mini_app_ui/'+n for n in ASSET_NAMES]
  served={n:hashlib.sha256(((bundle/n).read_bytes() if n.endswith('.png') else (bundle/n).read_text(encoding='utf-8').encode('utf-8'))).hexdigest() for n in served_names}
 return raw,served
def fake():
 hashes={'bot/mini_app_ui/index.html':'shell',**{'bot/mini_app_ui/'+n:'asset' for n in ASSET_NAMES}}
 return hashes,{'deployment':'d','cli_sha':'s','bot':{'username':'TwitchSignalTestbot','is_bot':True},'menu':{'type':'web_app','text':'Приложение','url':'https://'+T['staging_domain']+'/app'},'integrity':'ok','foreign_key_check':[],'versions':['v'],'money':{'mode':'offline','external_create':False,'invoice':False,'live_callback':404},'file_hashes':hashes,'health':{'status':'ok','http':200},'app':{'http':200,'xfo':None,'ancestors':'https://web.telegram.org','hash':'shell'},'unsigned':{n:401 for n in UNSIGNED_NAMES},'http_asset_hashes':{n:'asset' for n in ASSET_NAMES},'legal_http':{n:503 for n in ['privacy','agreement','support','tariffs','payments']},'trial_owner_allowlisted':True}
class SmokeGuards(unittest.TestCase):
 def test_asset_headers_are_case_insensitive_and_keep_exact_deny(self):
  import ast
  tree=ast.parse(REMOTE)
  loop=next(n for n in tree.body if isinstance(n,ast.For) and isinstance(n.iter,ast.Name) and n.iter.id=='ASSETS')
  compiled=compile(ast.Module(body=[loop],type_ignores=[]),'actual_asset_collector','exec')
  for name in ['X-Frame-Options','x-frame-options','X-fRaMe-OpTiOnS']:
   with self.subTest(header=name):
    result={'http_asset_hashes':{}}
    namespace={'ASSETS':ASSET_NAMES,'http':lambda path:(200,{name:'DENY'},b'asset'),'result':result,'hashlib':hashlib}
    exec(compiled,namespace)
    self.assertEqual(result['http_asset_hashes'],{n:hashlib.sha256(b'asset').hexdigest() for n in ASSET_NAMES})
  for headers in [{},{'x-frame-options':'SAMEORIGIN'}]:
   with self.subTest(rejected=headers):
    namespace={'ASSETS':ASSET_NAMES,'http':lambda path:(200,headers,b'asset'),'result':{'http_asset_hashes':{}},'hashlib':hashlib}
    with self.assertRaises(AssertionError):exec(compiled,namespace)
 def test_expected_hashes_use_actual_committed_bundle_bytes(self):
  import ast
  from scripts.staging_deploy import _committed_bundle
  sha=ops._capture(['git','rev-parse','HEAD']);names=['bot/database.py','bot/mini_app_ui/index.html','bot/mini_app_ui/app.css']
  with _committed_bundle(sha) as bundle:
   expected={n:hashlib.sha256((bundle/n).read_bytes()).hexdigest() for n in names}
  tree=ast.parse(pathlib.Path(__file__).read_text(encoding='utf-8'))
  main=next(n for n in tree.body if isinstance(n,ast.If))
  assignment=next(n for n in ast.walk(main) if isinstance(n,ast.Assign) and any(isinstance(x,ast.Name) and x.id=='hashes' for target in n.targets for x in ast.walk(target)))
  namespace={'sha':sha,'names':names,'ROOT':ROOT,'hashlib':hashlib,'subprocess':subprocess,'expected_release_hashes':globals().get('expected_release_hashes')}
  exec(compile(ast.Module(body=[assignment],type_ignores=[]),'actual_expected_hashes','exec'),namespace)
  self.assertEqual(namespace['hashes'],expected)
 def test_raw_artifact_and_normalized_http_hashes_remain_separate(self):
  import copy
  served,result=fake();raw={n:'raw-'+h for n,h in served.items()};result['file_hashes']=raw
  self.assertTrue(verify(result,'s',raw,['v'],'d',served))
  for field in ['file_hashes','app']:
   bad=copy.deepcopy(result)
   if field=='file_hashes':bad[field]=served
   else:bad[field]['hash']=raw['bot/mini_app_ui/index.html']
   with self.subTest(field=field):
    with self.assertRaises(AssertionError):verify(bad,'s',raw,['v'],'d',served)
 def test_collector_uses_existing_config_fields(self):
  import ast,dataclasses
  from bot.config import Config
  fields={f.name for f in dataclasses.fields(Config)}
  used={n.attr for n in ast.walk(ast.parse(REMOTE)) if isinstance(n,ast.Attribute) and isinstance(n.value,ast.Name) and n.value.id=='config'}
  self.assertEqual(used-fields,set())
 def test_exact_surfaces_and_trial_flag(self):
  for mutation in ['missing_admin','substituted_asset','trial_false']:
   with self.subTest(mutation=mutation):
    hashes,result=fake()
    if mutation=='missing_admin':result['unsigned'].pop('admin')
    if mutation=='substituted_asset':result['http_asset_hashes'].pop('app.js');result['http_asset_hashes']['index.html']='shell'
    if mutation=='trial_false':result['trial_owner_allowlisted']=False
    with self.assertRaises(AssertionError):verify(result,'s',hashes,['v'],'d')
 def test_valid_all_evidence(self):
  hashes,result=fake();self.assertTrue(verify(result,'s',hashes,['v'],'d'))
 def test_rejects_foreign_bot_wrong_sha_asset_mismatch_and_fake_health_success(self):
  for key in ['bot','cli_sha','file_hashes','health','app','menu','versions','money','unsigned','http_asset_hashes']:
   with self.subTest(key=key):
    hashes,result=fake();result[key]={'status':'ok'} if isinstance(result[key],dict) else 'wrong'
    with self.assertRaises((AssertionError,KeyError,TypeError)):verify(result,'s',hashes,['v'],'d')
REMOTE='''
import urllib.request,urllib.error,re
from aiogram import Bot
from bot.config import load_config,first_release_payment_policy
config=load_config();assert config.pinned_staging and config.mini_app_enabled
base='https://'+DOMAIN
async def bot_read():
 bot=Bot(config.telegram_bot_token)
 try:
  me=await asyncio.wait_for(bot.get_me(),15);menu=await asyncio.wait_for(bot.get_chat_menu_button(),15)
  return {'username':me.username,'id':me.id,'is_bot':me.is_bot},{'type':menu.type,'text':getattr(menu,'text',None),'url':getattr(getattr(menu,'web_app',None),'url',None)}
 finally:await bot.session.close()
def http(path,post=None):
 req=urllib.request.Request(base+path,data=post,headers={'Content-Type':'application/json'} if post is not None else {})
 try:r=urllib.request.urlopen(req,timeout=20)
 except urllib.error.HTTPError as e:r=e
 with r:
  body=r.read()
  secrets=[config.telegram_bot_token,os.environ.get('TOKEN_ENCRYPTION_KEY'),os.environ.get('TWITCH_CLIENT_SECRET'),os.environ.get('PLATEGA_SECRET')]
  assert not any(s and len(s)>8 and s.encode() in body for s in secrets),'Secret in HTTP response'
  return r.status,dict(r.headers),body
root=pathlib.Path('/app').resolve();file_hashes={}
for name in HASH_NAMES:
 p=(root/name).resolve();assert p.is_relative_to(root) and p.is_file();file_hashes[name]=hashlib.sha256(p.read_bytes()).hexdigest()
bot,menu=asyncio.run(bot_read());db=sqlite3.connect('file:/data/bot.db?mode=ro',uri=True)
result={'deployment':os.environ.get('RAILWAY_DEPLOYMENT_ID'),'cli_sha':CLI_SHA,'bot':bot,'menu':menu,'file_hashes':file_hashes,'integrity':db.execute('PRAGMA integrity_check').fetchone()[0],'foreign_key_check':db.execute('PRAGMA foreign_key_check').fetchall(),'versions':[r[0] for r in db.execute('SELECT version FROM schema_migrations ORDER BY version')]};db.close()
policy=first_release_payment_policy()
status,headers,body=http('/healthz');result['health']={**json.loads(body),'http':status}
status,headers,body=http('/app');headers={k.lower():v for k,v in headers.items()};csp=headers.get('content-security-policy','');ancestors=re.findall(r'(?:^|;)\\s*frame-ancestors\\s+([^;]+)',csp)
result['app']={'http':status,'xfo':headers.get('x-frame-options'),'ancestors':ancestors[0] if len(ancestors)==1 else None,'hash':hashlib.sha256(body).hexdigest()}
result['http_asset_hashes']={}
for name in ASSETS:
 status,headers,body=http('/app/'+name);headers={k.lower():v for k,v in headers.items()};assert status==200 and headers.get('x-frame-options')=='DENY'
 result['http_asset_hashes'][name]=hashlib.sha256(body).hexdigest()
result['unsigned']={}
for path in ['/app/api/bootstrap','/app/api/viewer/state','/app/api/streamer/profile','/app/api/purchase/prepare','/app/api/viewer/reports','/app/api/streamer/reports','/app/api/viewer/favorite','/app/api/viewer/unfollow/undo']:
 status,headers,body=http(path,b'{}');result['unsigned'][path]=status
status,headers,body=http('/admin/api/snapshot');result['unsigned']['admin']=status
result['legal_http']={n:http('/app/legal/'+n)[0] for n in ['privacy','agreement','support','tariffs','payments']}
result['money']={'mode':policy.mode,'external_create':policy.allow_external_create,'invoice':policy.allow_invoice,'live_callback':http('/payments/platega/callback',b'{}')[0]}
result['trial_owner_allowlisted']=config.owner_chat_id==425785231 and config.admin_telegram_bot_username.lower()=='twitchsignaltestbot'
result['preview_config']={'enabled':config.preview.enabled,'max_concurrent_jobs':config.preview.max_concurrent_jobs,'max_active_sessions':config.preview.max_active_sessions,'initial_delay_seconds':config.preview.initial_delay_seconds,'interval_seconds':config.preview.interval_seconds}
result['client_secret_matches']=0
print('TS_QA_JSON='+json.dumps(result))
'''
if __name__=='__main__':
 if len(sys.argv)<2 or sys.argv[1]=='selftest':unittest.main(argv=[sys.argv[0]])
 elif sys.argv[1]=='actual':
  sha=ops._capture(['git','rev-parse','HEAD']);before=ops.status();stage=before['staging'][0]
  assert stage['cliMessage']=='staging '+sha and stage['status']=='SUCCESS'
  names=ops._capture(['git','ls-files','--','bot','main.py','requirements.txt','scripts','docs/legal']).splitlines()
  hashes,http_hashes=expected_release_hashes(sha,names)
  from bot.mini_app_web import _ASSETS
  assert set(_ASSETS)==set(ASSET_NAMES)
  versions=json.loads((ROOT/'docs/audits/mini-app-owner-corrections-2026-10-03/STAGING-migration-copy.json').read_text(encoding='utf-8'))['schema_versions']
  prefix='DOMAIN=%r\nHASH_NAMES=%r\nASSETS=%r\nCLI_SHA=%r\n'%(T['staging_domain'],list(hashes),list(_ASSETS),sha)
  result=ops.remote(prefix+REMOTE)
  verify(result,sha,hashes,versions,stage['id'],http_hashes);after=ops.status();assert after==before
  original=json.loads((ROOT/'docs/audits/mini-app-owner-corrections-2026-10-03/STAGING-staging-before.json').read_text(encoding='utf-8'))['status']['production']
  assert after['production']==original,'Production metadata changed since initial checkpoint'
  result.update(status='PASS',sha=sha,production_before_after_equal=True,source_note='Exact committed Git archive artifact bytes; HTTP text uses the server read_text UTF-8/universal-newline normalization',native='NOT TESTED',signed_live_api='NOT TESTED',external_payments=0,outbound_messages=0)
  import re
  logs=ops._capture(['railway','logs',stage['id'],'--project',T['project_id'],'--environment',T['staging_environment_id'],'--service',T['service_id'],'--lines','100','--json'])
  entries=[json.loads(line) for line in logs.splitlines() if line.strip()]
  raw='\n'.join(json.dumps(e,ensure_ascii=True) for e in entries)
  patterns=[r'\b(?:bot)?\d{6,12}:[A-Za-z0-9_-]{30,}\b',r'(?i)(?:access_token|refresh_token|PLATEGA_SECRET|TOKEN_ENCRYPTION_KEY)\s*[=:]\s*["\']?[A-Za-z0-9_-]{20,}',r'(?i)authorization\s*[=:]\s*["\']?Bearer\s+[A-Za-z0-9_-]{20,}']
  counts=[len(re.findall(p,raw)) for p in patterns];assert counts==[0,0,0],'Credential-shaped value in bounded staging log sample'
  result['log_scan']={'deployment':stage['id'],'sample_entries':len(entries),'credential_pattern_matches':counts,'raw_exported':False,'scope':'last100 deployment logs; no guarantee for all historical logs'}
  ops.save('STAGING-staging-smoke.json',result)
 else:raise ValueError('Unknown mode')
