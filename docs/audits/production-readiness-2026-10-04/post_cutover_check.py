"""Read-only post-cutover verification production (Railway SSH, никаких записей).

Проверяет: DB integrity/FK/schema/counts, payment OFF, queue OFF, admin/auth,
Mini App HTTP-поверхности, env-флаги. Секреты и приватные данные не печатаются.
"""
import base64
import json
import pathlib
import subprocess
import sys
import zlib

import os
import shutil

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from scripts.staging_deploy import _capture, _load_target  # noqa: E402

T = _load_target()
EXPECTED_DEPLOYMENT = "44fe69d3-2d89-466c-9202-fcbf56e6c05a"


def _railway_executable() -> list[str]:
    if os.name == "nt":
        shim = pathlib.Path(shutil.which("railway") or "")
        native = shim.parent / "node_modules/@railway/cli/bin/railway.exe"
        if not native.is_file():
            raise RuntimeError("Verified installed Railway native CLI missing")
        return [str(native)]
    return ["railway"]


GUARD = """import os,json,pathlib,hashlib,base64,sys,sqlite3,tempfile,asyncio
expected=%r
assert all(os.environ.get(k)==v for k,v in expected.items()), 'Pinned production runtime mismatch'
""" % {
    "RAILWAY_PROJECT_ID": T["project_id"],
    "RAILWAY_ENVIRONMENT_ID": T["production_environment_id"],
    "RAILWAY_SERVICE_ID": T["service_id"],
    "RAILWAY_ENVIRONMENT_NAME": "production",
    "RAILWAY_VOLUME_MOUNT_PATH": "/data",
    "DB_PATH": "/data/bot.db",
}

SECRET_NAMES = [
    "TELEGRAM_BOT_TOKEN",
    "TWITCH_CLIENT_ID",
    "TWITCH_CLIENT_SECRET",
    "TOKEN_ENCRYPTION_KEY",
    "ADMIN_PANEL_ACCESS_KEY",
]

ENV_BODY = """
names=['PRODUCTION_PRODUCT_ENABLED','PRODUCTION_ADMISSION_FILE','PRODUCTION_ADMISSION_SHA256',
 'PRODUCTION_REPLICA_COUNT','PRODUCTION_WRITER_POLICY','PRODUCTION_PAYMENT_POLICY','PRODUCTION_QUEUE_POLICY',
 'PRODUCTION_LOGIN_CLIENT_POLICY','PRODUCTION_QUEUE_ENABLED','NOTIFICATION_QUEUE_ENABLED',
 'ADMIN_TELEGRAM_BOT_USERNAME','PUBLIC_URL','PORT','RAILWAY_VOLUME_MOUNT_PATH','DB_PATH',
 'RAILWAY_VOLUME_ID','RAILWAY_VOLUME_INSTANCE_ID','RAILWAY_ENVIRONMENT_NAME','PAYMENT_PROVIDER','PLATEGA_MERCHANT_ID']
env={n:(os.environ.get(n) if n not in SECRETS else ('present' if os.environ.get(n) else 'absent')) for n in names}
env['ADMIN_PANEL_ACCESS_KEY']=('present:%d'%len(os.environ['ADMIN_PANEL_ACCESS_KEY'])) if os.environ.get('ADMIN_PANEL_ACCESS_KEY') else 'absent'
env['SECRETS_LENGTHS']={n:len(os.environ[n]) for n in SECRETS if os.environ.get(n)}
from bot.config import load_config, first_release_payment_policy
config=load_config()
policy=first_release_payment_policy()
result={'env':env,'payment_policy':{'mode':policy.mode,'allow_invoice':policy.allow_invoice,'allow_external_create':policy.allow_external_create},
 'queue_enabled_config':config.notification_queue_enabled,
 'mini_app_enabled':config.mini_app_enabled,
 'admission_file_present':pathlib.Path(os.environ.get('PRODUCTION_ADMISSION_FILE') or '/nonexistent').is_file()}
data=pathlib.Path('/data/bot.db').stat()
result['db_file']={'size':data.st_size,'mode':oct(data.st_mode)}
print('TS_QA_JSON='+json.dumps(result))
"""

DB_BODY = """
db=sqlite3.connect('file:/data/bot.db?mode=ro',uri=True)
db.execute('PRAGMA query_only=ON')
tables=[r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
counts={}
for name in COUNT_TABLES:
 if name in tables:
  counts[name]=db.execute('SELECT count(*) FROM "%s"'%name).fetchone()[0]
result={'integrity':db.execute('PRAGMA integrity_check').fetchone()[0],
 'foreign_key_check':db.execute('PRAGMA foreign_key_check').fetchall(),
 'versions':[r[0] for r in db.execute('SELECT version FROM schema_migrations ORDER BY version')],
 'table_count':len(tables),'counts':counts,
 'journal_mode':db.execute('PRAGMA journal_mode').fetchone()[0]}
db.close()
print('TS_QA_JSON='+json.dumps(result))
"""

HTTP_BODY = """
result={}
def http(path,post=None,headers=None):
 req=urllib.request.Request(base+path,data=post,headers=headers or ({'Content-Type':'application/json'} if post is not None else {}))
 try:r=urllib.request.urlopen(req,timeout=25)
 except urllib.error.HTTPError as e:r=e
 with r:
  body=r.read()
  return r.status,dict(r.headers),body
status,headers,body=http('/healthz');result['healthz']={'http':status,'body':json.loads(body)}
status,headers,body=http('/app');result['app']={'http':status,'bytes':len(body),'xfo':{k.lower():v for k,v in headers.items()}.get('x-frame-options')}
result['unsigned']={}
for path in ['/app/api/bootstrap','/app/api/viewer/state','/app/api/streamer/profile','/app/api/purchase/prepare','/admin/api/snapshot']:
 result['unsigned'][path]=http(path,b'{}')[0]
result['payment_endpoints']={'platega_callback':http('/payments/platega/callback',b'{}')[0]}
result['legal']={n:http('/app/legal/'+n)[0] for n in ['privacy','agreement','support','tariffs','payments']}
print('TS_QA_JSON='+json.dumps(result))
"""

COUNT_TABLES = [
    "known_private_users",
    "tracked_channels",
    "telegram_channels",
    "twitch_user_tokens",
    "stream_history",
    "chat_settings",
    "subscriptions",
    "notification_queue",
    "payment_orders",
    "billing_orders",
]


def remote(code: str) -> dict:
    payload = base64.b64encode(zlib.compress((GUARD + code).encode())).decode()
    command = (
        "python -c \"import base64,zlib;exec(zlib.decompress(base64.b64decode('"
        + payload
        + "')))\""
    )
    assert len(command) < 7900, "Windows CLI command bound"
    argv = [
        *_railway_executable(),
        "ssh",
        "--project", T["project_id"],
        "--service", T["service_id"],
        "--environment", T["production_environment_id"],
        command,
    ]
    result = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True, timeout=180, encoding="utf-8")
    if result.returncode:
        raise RuntimeError(
            "Pinned SSH failed code "
            + str(result.returncode)
            + " "
            + repr((result.stdout[-600:], result.stderr[-600:]))
        )
    marker = "TS_QA_JSON="
    lines = [line.split(marker, 1)[1] for line in result.stdout.splitlines() if marker in line]
    if len(lines) != 1:
        raise RuntimeError("SSH QA output incomplete")
    return json.loads(lines[0])


if __name__ == "__main__":
    payload = {"env": remote("SECRETS=%r\n" % SECRET_NAMES + ENV_BODY)}
    print("env: ok", flush=True)
    payload["db"] = remote(
        "COUNT_TABLES=%r\n" % COUNT_TABLES + DB_BODY
    )
    print("db: ok", flush=True)
    payload["http"] = remote(
        "import urllib.request,urllib.error\nbase='http://127.0.0.1:'+os.environ.get('PORT','8765')\n" + HTTP_BODY
    )
    print("http: ok", flush=True)
    out = ROOT / "docs/audits/production-readiness-2026-10-04/POST-CUTOVER-VERIFICATION.json"
    existing = json.loads(out.read_text(encoding="utf-8")) if out.is_file() else {}
    existing["deployment"] = EXPECTED_DEPLOYMENT
    existing["read_only_checks"] = payload
    out.write_text(json.dumps(existing, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
