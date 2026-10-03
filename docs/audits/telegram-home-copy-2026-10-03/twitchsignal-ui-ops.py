import base64, hashlib, json, os, pathlib, shutil, subprocess, sys, uuid, zlib
ROOT=pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
from scripts.staging_deploy import _capture,_load_target,validate_target,_nodes,_instance,_resolve_executable
T=_load_target()
_original_resolve_executable = _resolve_executable
def _resolve_executable(argv):
 if os.name == 'nt' and argv[0] == 'railway':
  shim=pathlib.Path(shutil.which('railway') or '')
  native=shim.parent/'node_modules/@railway/cli/bin/railway.exe'
  if not native.is_file():raise RuntimeError('Verified installed Railway native CLI missing')
  return [str(native),*argv[1:]]
 return _original_resolve_executable(argv)
import scripts.staging_deploy as _guard_module
_guard_module._resolve_executable = _resolve_executable
GUARD='''import os,json,pathlib,hashlib,base64,sys,sqlite3,tempfile,asyncio
expected=%r
assert all(os.environ.get(k)==v for k,v in expected.items()), 'Pinned staging runtime mismatch'
''' % {'RAILWAY_PROJECT_ID':T['project_id'],'RAILWAY_ENVIRONMENT_ID':T['staging_environment_id'],'RAILWAY_SERVICE_ID':T['service_id'],'RAILWAY_ENVIRONMENT_NAME':'staging','RAILWAY_VOLUME_MOUNT_PATH':'/data','DB_PATH':'/data/bot.db','OWNER_CHAT_ID':'425785231'}
def status():
 s=json.loads(_capture(['railway','status','--json'])); errors=validate_target(s,T)
 if errors: raise RuntimeError(';'.join(errors))
 return {n['name']:[{'id':d['id'],'status':d['status'],'commit':(d.get('meta') or {}).get('commitHash'),'cliMessage':(d.get('meta') or {}).get('cliMessage')} for d in (_instance(n,T['service_id']) or {}).get('activeDeployments',[])] for n in _nodes(s,'environments') if n['name'] in ('staging','production')}
def remote(code):
 status()
 encoded=base64.b64encode(zlib.compress((GUARD+code).encode())).decode()
 command='python -c "import base64,zlib;exec(zlib.decompress(base64.b64decode(\''+encoded+'\')))"'
 assert len(command)<7900,'Windows CLI command bound'
 argv=['railway','ssh','--project',T['project_id'],'--service',T['service_id'],'--environment',T['staging_environment_id'],command]
 result=subprocess.run(_resolve_executable(argv),cwd=ROOT,capture_output=True,text=True,timeout=120,encoding='utf-8')
 if result.returncode:
  import re
  diagnostics=[]
  for line in (result.stdout+'\n'+result.stderr).splitlines():
   if re.match(r'^(NameError|SyntaxError|FileNotFoundError|ValueError|TypeError|AssertionError|PermissionError|OSError|sqlite3\.\w+):',line): diagnostics.append(line[:300])
  diagnostics.append({'command_chars':len(command),'stdout_chars':len(result.stdout),'stderr_chars':len(result.stderr),'stderr_prefix':result.stderr[:400]})
  raise RuntimeError('Pinned SSH failed code '+str(result.returncode)+' '+repr(diagnostics))
 marker='TS_QA_JSON='
 lines=[line.split(marker,1)[1] for line in result.stdout.splitlines() if marker in line]
 if len(lines)!=1: raise RuntimeError('SSH QA output incomplete')
 return json.loads(lines[0])
def save(name,result):
 out=ROOT/'docs/audits/telegram-home-copy-2026-10-03'/name
 out.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 print(json.dumps(result,ensure_ascii=False))
if __name__=='__main__':
 mode=sys.argv[1]
 if mode=='inspect':
  before=status()
  result=remote('''
from importlib.metadata import version
db=sqlite3.connect('file:/data/bot.db?mode=ro',uri=True)
result={'guard':True,'cwd':os.getcwd(),'database_exists':pathlib.Path('/data/bot.db').is_file(),'integrity':db.execute('PRAGMA integrity_check').fetchone()[0],'versions':[r[0] for r in db.execute('SELECT version FROM schema_migrations ORDER BY version')],'key_present':bool(os.environ.get('TOKEN_ENCRYPTION_KEY')),'python':sys.version.split()[0],'packages':{p:version(p) for p in ['aiogram','aiohttp','aiosqlite','cryptography']}}
db.close()
print('TS_QA_JSON='+json.dumps(result))
''')
  save('STAGING-staging-before.json',{'status':before,'runtime':result})
 elif mode=='backup':
  tag='telegram-ui-20261003-'+uuid.uuid4().hex
  dest='/data/backups/'+tag+'.db'
  backup_source=(ROOT/'scripts/sqlite_backup.py').read_text(encoding='utf-8')
  source64=base64.b64encode(zlib.compress(backup_source.encode())).decode()
  result=remote("""
ns={'__name__':'qa_backup'}
import zlib
exec(zlib.decompress(base64.b64decode(%r)),ns)
source=pathlib.Path('/data/bot.db'); dest=pathlib.Path(%r)
ns['ensure_staging_path'](source,'staging');ns['ensure_staging_path'](dest,'staging')
created=ns['backup_database'](source,dest)
restored=ns['verify_backup'](dest)
payload=dest.read_bytes()
print('TS_QA_JSON='+json.dumps({'backup':str(dest),'created':created,'restore':restored,'sha256':hashlib.sha256(payload).hexdigest(),'payload_base64':base64.b64encode(payload).decode()}))
""" % (source64,dest))
  payload=base64.b64decode(result.pop('payload_base64'),validate=True)
  assert hashlib.sha256(payload).hexdigest()==result['sha256']
  store=pathlib.Path(os.environ['LOCALAPPDATA'])/'TwitchSignalBot/staging-backups'
  store.mkdir(parents=True,exist_ok=True); local=store/(tag+'.db')
  with local.open('xb') as f:f.write(payload)
  from scripts.sqlite_backup import verify_backup
  result['local_export']=str(local); result['local_restore']=verify_backup(local)
  result['status']='PASS';save('STAGING-backup.json',result)
 elif mode=='migrate':
  import io, zipfile
  names=['bot/'+n+'.py' for n in ['__init__','database','billing_models','billing_migrations','billing_provider','deep_links','streamer_template','viewer_filter','viewer_preferences','viewer_undo','plan_catalog','entitlements']]
  commit=_capture(['git','rev-parse','HEAD']); stream=io.BytesIO(); hashes={}
  with zipfile.ZipFile(stream,'w',compression=zipfile.ZIP_DEFLATED) as archive:
   for n in names:
    data=subprocess.check_output(['git','show',commit+':'+n],cwd=ROOT)
    assert data.decode().replace('\r\n','\n')==(ROOT/n).read_text(encoding='utf-8').replace('\r\n','\n')
    hashes[n]=hashlib.sha256(data).hexdigest(); archive.writestr(n,data)
  payload=stream.getvalue(); digest=hashlib.sha256(payload).hexdigest(); upload='/tmp/twitchsignal-telegram-ui-'+uuid.uuid4().hex
  remote("p=pathlib.Path(%r);p.mkdir(mode=0o700);print('TS_QA_JSON='+json.dumps({'created':True}))"%upload)
  encoded=base64.b64encode(payload).decode();chunks=[encoded[i:i+3000] for i in range(0,len(encoded),3000)]
  for i,part in enumerate(chunks):
   remote("p=pathlib.Path(%r);assert p.parent==pathlib.Path('/tmp');f=p/%r;f.write_text(%r,encoding='ascii');print('TS_QA_JSON='+json.dumps({'part':%r}))"%(upload,f'part-{i:03d}',part,i))
   print(json.dumps({'upload_part':i+1,'total':len(chunks)}),flush=True)
  backup=json.loads((ROOT/'docs/audits/telegram-home-copy-2026-10-03/STAGING-backup.json').read_text(encoding='utf-8'))
  body=pathlib.Path(__file__).with_name('migration-copy-remote.py').read_text(encoding='utf-8')
  prefix='UPLOAD=%r\nZIP_SHA=%r\nBACKUP=%r\nBACKUP_SHA=%r\nSOURCE_HASHES=%r\n'%(upload,digest,backup['backup'],backup['sha256'],hashes)
  result=remote(prefix+body); result['source_commit']=commit;result['source_hashes']=hashes
  save('STAGING-migration-copy.json',result)
 else: raise ValueError('Unknown action')
