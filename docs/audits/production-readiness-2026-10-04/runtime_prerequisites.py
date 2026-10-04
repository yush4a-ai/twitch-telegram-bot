"""Read-only pinned staging binary/disk/queue prerequisites, not an E2E send."""
import importlib.util
import json
import os
import pathlib
import sys
import urllib.request
from urllib.parse import urlsplit

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
proxy = urllib.request.getproxies().get('https')
if proxy:
    parsed = urlsplit(proxy)
    assert parsed.hostname in {'127.0.0.1', 'localhost'} and not parsed.username
    os.environ['HTTPS_PROXY'] = proxy
    os.environ['HTTP_PROXY'] = proxy
spec = importlib.util.spec_from_file_location('readiness_ops', ROOT / 'docs/audits/telegram-menu-recovery-2026-10-04/twitchsignal-ui-ops.py')
ops = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ops)
before = ops.status()
assert before['staging'][0]['id'] == '5211c9f8-6a2f-4834-9aa6-88706a4a34a7'
result = ops.remote('''
import shutil,subprocess
def binary(name):
 path=shutil.which(name)
 if not path:return {'path':None,'available':False}
 p=subprocess.run([path,'-version'],capture_output=True,text=True,timeout=5)
 return {'path':path,'available':p.returncode==0,'version':p.stdout.splitlines()[0][:200] if p.stdout else None}
disk=shutil.disk_usage('/data')
db=sqlite3.connect('file:/data/bot.db?mode=ro',uri=True)
tables=[r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
jobs={r[0]:r[1] for r in db.execute('SELECT status,COUNT(*) FROM notification_jobs GROUP BY status')}
db.close()
result={'deployment':os.environ.get('RAILWAY_DEPLOYMENT_ID'),'ffmpeg':binary('ffmpeg'),'ffprobe':binary('ffprobe'),'volume_disk':{'total':disk.total,'used':disk.used,'free':disk.free,'note':'filesystem view, not Railway plan quota'},'table_count':len(tables),'notification_job_states':jobs,'external_sends':0,'capture_or_render_performed':False}
print('TS_QA_JSON='+json.dumps(result))
''')
assert ops.status() == before
assert result['deployment'] == before['staging'][0]['id']
pathlib.Path(__file__).with_name('RUNTIME-PREREQUISITES.json').write_text(
    json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps(result, ensure_ascii=True))
