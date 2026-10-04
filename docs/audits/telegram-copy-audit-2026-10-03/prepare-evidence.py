"""Verify unchanged schema/web/assets before reusing their previous evidence."""
import hashlib,json,shutil,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3];OUT=Path(__file__).resolve().parent
OLD=ROOT/'docs/audits/telegram-journey-final-2026-10-03'
HOME=ROOT/'docs/audits/telegram-home-copy-2026-10-03'
def save(name,value):
    (OUT/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
for name in ['final-filtered-full-guard.py','twitchsignal-ui-ops.py','twitchsignal-ui-smoke.py']:
    text=(OLD/name).read_text(encoding='utf-8').replace('telegram-journey-final-2026-10-03','telegram-copy-audit-2026-10-03').replace('twitchsignal-journey-final-package-manifest','twitchsignal-copy-audit-package-manifest')
    (OUT/name).write_text(text,encoding='utf-8')
for name in ['STAGING-staging-before.json','STAGING-migration-copy.json']:
    shutil.copyfile(OLD/name,OUT/name)
migration=json.loads((HOME/'MIGRATION-IDENTITY.json').read_text(encoding='utf-8'))
for name,digest in migration['module_hashes'].items():
    committed=subprocess.check_output(['git','show','HEAD:'+name],cwd=ROOT)
    baseline=subprocess.check_output(['git','show',migration['runtime_base']+':'+name],cwd=ROOT)
    assert committed==baseline and hashlib.sha256(committed).hexdigest()==digest,name
    actual=subprocess.check_output(['git','hash-object','--path='+name,name],cwd=ROOT,text=True).strip()
    expected=subprocess.check_output(['git','rev-parse','HEAD:'+name],cwd=ROOT,text=True).strip()
    assert actual==expected,name
migration.update(base_head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),note='Copy-only changes. Thirteen schema/cipher/catalog Git blobs byte-identical and working files match under Git attributes (Windows CRLF). Migration-copy reused explicitly, not newly executed. Artifact byte identity is separately checked by the release guard/smoke.')
save('MIGRATION-IDENTITY.json',migration)
browser=json.loads((OLD/'BROWSER-IDENTITY.json').read_text(encoding='utf-8'))
for name,blob in browser['git_blob_ids'].items():
    actual=subprocess.check_output(['git','hash-object','--path='+name,name],cwd=ROOT,text=True).strip()
    assert actual==blob,name
browser.update(status='PASS_REUSED_IDENTICAL_SOURCE',note='Eighteen Mini App files unchanged. Existing Chromium/WebKit component evidence reused; new Telegram browser gallery separately checked in BROWSER-QA.json. Native NOT TESTED.')
save('BROWSER-IDENTITY.json',browser)
assets={}
for name in ['telegram-welcome.png','telegram-home.png','telegram-channel-guide.png']:
    path='bot/assets/'+name;current=(ROOT/path).read_bytes()
    previous=subprocess.check_output(['git','show','00c6093d87d1e2617ddd19a736c42aab0d14e1d6:'+path],cwd=ROOT)
    assert current==previous,path
    assets[path]=hashlib.sha256(current).hexdigest()
save('ASSET-IDENTITY.json',{'status':'PASS','new_generated_images':0,'sha256':assets})
print(json.dumps({'schema_modules':13,'unchanged_web_files':18,'approved_assets':3,'guards':'unchanged; only evidence paths updated'}))
