"""Read-only release audit; no deploy, messages, payments or active DB writes."""
import importlib.util
import json
import os
import pathlib
import sys
import urllib.request
from urllib.parse import urlsplit

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
SHA = '39bc82171114a0a6b20ad4251bd68c4cc2d3f3f0'
DEPLOYMENT = '5211c9f8-6a2f-4834-9aa6-88706a4a34a7'
OLD = ROOT / 'docs/audits/telegram-menu-recovery-2026-10-04'

proxy = urllib.request.getproxies().get('https')
if proxy:
    parsed = urlsplit(proxy)
    assert parsed.hostname in {'127.0.0.1', 'localhost'} and not parsed.username
    os.environ['HTTPS_PROXY'] = proxy
    os.environ['HTTP_PROXY'] = proxy
os.environ['PYTHON_DOTENV_DISABLED'] = '1'
os.environ['PYTHONIOENCODING'] = 'utf-8'
spec = importlib.util.spec_from_file_location('readiness_smoke', OLD / 'twitchsignal-ui-smoke.py')
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)
ops = smoke.ops

assert not ops._capture(['git', 'diff', SHA, '--', 'bot', 'main.py', 'requirements.txt', 'scripts', 'docs/legal', 'tests'])
head = ops._capture(['git', 'rev-parse', 'HEAD'])
before = ops.status()
stage = before['staging'][0]
assert stage['id'] == DEPLOYMENT and stage['status'] == 'SUCCESS'
assert stage['cliMessage'] == 'staging ' + SHA
names = ops._capture(['git', 'ls-files', '--', 'bot', 'main.py', 'requirements.txt', 'scripts', 'docs/legal']).splitlines()
hashes, served = smoke.expected_release_hashes(SHA, names)
versions = json.loads((OLD / 'STAGING-migration-copy.json').read_text(encoding='utf-8'))['schema_versions']
prefix = 'DOMAIN=%r\nHASH_NAMES=%r\nASSETS=%r\nCLI_SHA=%r\n' % (ops.T['staging_domain'], names, list(smoke.ASSET_NAMES), SHA)
result = ops.remote(prefix + smoke.REMOTE)
smoke.verify(result, SHA, hashes, versions, DEPLOYMENT, served)
after = ops.status()
assert after == before
original = json.loads((OLD / 'STAGING-staging-before.json').read_text(encoding='utf-8'))['status']['production']
assert original == after['production']
result.update(status='PASS', source_head=head, deployed_sha=SHA,
              runtime_and_tests_identical=True, metadata_before_after=after,
              production_metadata_unchanged=True, messages_sent=0,
              payment_requests_created=0, native='NOT TESTED', signed_live_api='NOT TESTED')
out = pathlib.Path(__file__).with_name('STAGING-READ-ONLY.json')
out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps({k: result[k] for k in ['status', 'source_head', 'deployed_sha', 'bot', 'integrity', 'money', 'production_metadata_unchanged', 'native']}))
print('runtime_files=' + str(len(hashes)) + ' http_assets=' + str(len(smoke.ASSET_NAMES)))
