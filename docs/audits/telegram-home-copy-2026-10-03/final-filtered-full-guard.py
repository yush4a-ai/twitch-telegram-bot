"""Run the complete standard staging gate with evidence-only upload exclusions."""
from contextlib import contextmanager
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT=Path(r'C:/Users/yusha/Desktop/cloude/TG-BOT.(TwtichSignal)').resolve()
os.chdir(ROOT);sys.path.insert(0,str(ROOT))
from scripts import staging_deploy as guard
ops_spec=importlib.util.spec_from_file_location('m1_ops',ROOT/'docs/audits/telegram-home-copy-2026-10-03/twitchsignal-ui-ops.py')
ops=importlib.util.module_from_spec(ops_spec);ops_spec.loader.exec_module(ops)
helper=ROOT/'docs/audits/mini-app-owner-corrections-2026-10-03/transport-retry-720962b.py'
spec=importlib.util.spec_from_file_location('package_only',helper)
package_only=importlib.util.module_from_spec(spec);spec.loader.exec_module(package_only)
original_bundle=guard._committed_bundle
manifest_path=Path(tempfile.gettempdir())/'twitchsignal-home-copy-package-manifest.json'
target=guard._load_target()
production_expected=json.loads((ROOT/'docs/audits/telegram-home-copy-2026-10-03/STAGING-staging-before.json').read_text(encoding='utf-8'))['status']['production']
manifest={}

def production_check():
    status=json.loads(guard._capture(['railway','status','--json']))
    errors=guard.validate_target(status,target,require_health=True)
    prod=next(e for e in guard._nodes(status,'environments') if e['name']=='production')
    deployments=guard._instance(prod,target['service_id'])['activeDeployments']
    actual=[{'id':d['id'],'status':d['status'],'commit':(d.get('meta') or {}).get('commitHash'),'cliMessage':(d.get('meta') or {}).get('cliMessage')} for d in deployments]
    if errors or actual!=production_expected:raise RuntimeError('Pinned target or production baseline mismatch')
    return status

def save():manifest_path.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')

@contextmanager
def filtered_bundle(commit):
    names=set(guard._capture(['git','-c','core.quotepath=false','ls-tree','-r','--name-only',commit]).splitlines())
    with original_bundle(commit) as source:
        destination=source.parent/'upload'
        kept,excluded=package_only.package(source,destination,names)
        required=['railpack.json','railway.json','Procfile','.python-version','main.py','requirements.txt','scripts/staging_target.json']
        if any(name not in kept for name in required):raise RuntimeError('Missing runtime/build file')
        production_check()
        manifest.update(sha=commit,status='UPLOAD_PACKAGE_VERIFIED',excluded_prefixes=list(package_only.EXCLUDED),
            retained_files=kept,excluded_files=excluded,retained_count=len(kept),excluded_count=len(excluded),
            retained_bytes=sum(i['bytes'] for i in kept.values()),excluded_bytes=sum(i['bytes'] for i in excluded.values()),
            byte_verification='Exact path set against Git tree; retained SHA256/size against the unmodified standard committed archive bundle')
        save()
        print(f"Verified upload package: {len(kept)} files / {manifest['retained_bytes']} bytes",flush=True)
        yield destination

if __name__=='__main__':
    if sys.argv[1:] not in (['--check'],['--deploy']):raise RuntimeError('Explicit --check or --deploy required')
    production_check()
    manifest.update(status='STANDARD_GATE_RUNNING',started=datetime.now(timezone.utc).isoformat(),
        intended_sha=guard._capture(['git','rev-parse','HEAD']),full_suite='Runs unchanged guard.main([--deploy]); no test bypass',
        production_before=production_expected,target={k:target[k] for k in ['project_id','staging_environment_id','service_id','staging_domain']})
    save()
    guard._committed_bundle=filtered_bundle
    code=guard.main(sys.argv[1:])
    if code==0 and sys.argv[1:] == ['--deploy']:
        status=production_check();stage=next(e for e in guard._nodes(status,'environments') if e['name']=='staging')
        deployment=guard._instance(stage,target['service_id'])['activeDeployments'][0]
        if deployment['status']!='SUCCESS' or (deployment.get('meta') or {}).get('cliMessage')!='staging '+manifest['sha']:
            raise RuntimeError('Published snapshot mismatch')
        manifest.update(status='ACTIVE_SUCCESS',deployment=deployment['id'],production_after_equal=True,completed=datetime.now(timezone.utc).isoformat())
        save()
    elif code:
        manifest.update(status='STANDARD_GATE_FAILED',exit_code=code);save()
    raise SystemExit(code)
