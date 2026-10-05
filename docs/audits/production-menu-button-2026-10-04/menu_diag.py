"""Read-only production диагностика system MenuButton.

Запускает код внутри production-контейнера через `railway ssh`, ничего не пишет
на Volume и не меняет Telegram-состояние: только getMe / getChatMenuButton и
чтение локального config. Секреты (токен, chat_id) не печатаются.
"""
import base64
import json
import os
import pathlib
import shutil
import subprocess
import sys
import zlib

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from scripts.staging_deploy import _capture, _load_target, validate_target  # noqa: E402

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


def status() -> dict:
    raw = json.loads(_capture(["railway", "status", "--json"]))
    errors = validate_target(raw, T)
    result = {"project_id": raw.get("id"), "target_mismatches": errors}
    for node in raw["environments"]["edges"]:
        env = node["node"]
        if env["name"] not in ("staging", "production"):
            continue
        entry = {
            "environment_id": env.get("id"),
            "volume_instances": [
                {"id": v["node"].get("id"), "mountPath": v["node"].get("mountPath")}
                for v in env.get("volumeInstances", {}).get("edges", [])
            ],
            "services": [],
        }
        for inst in env.get("serviceInstances", {}).get("edges", []):
            service = inst["node"]
            source = service.get("source")
            entry["services"].append(
                {
                    "service_id": service.get("serviceId"),
                    "name": service.get("serviceName"),
                    "source": None
                    if source is None
                    else {
                        "repo": source.get("repo"),
                        "branch": source.get("branch"),
                        "rootDirectory": source.get("rootDirectory"),
                    },
                    "domains": [
                        d.get("domain")
                        for d in service.get("domains", {}).get("serviceDomains", [])
                    ],
                    "active_deployments": [
                        {
                            "id": d["id"],
                            "status": d["status"],
                            "stopped": d["deploymentStopped"],
                            "createdAt": d["createdAt"],
                            "commitHash": (d.get("meta") or {}).get("commitHash"),
                            "imageDigest": (d.get("meta") or {}).get("imageDigest"),
                            "cliMessage": (d.get("meta") or {}).get("cliMessage"),
                            "cliCaller": (d.get("meta") or {}).get("cliCaller"),
                        }
                        for d in service.get("activeDeployments", [])
                    ],
                }
            )
        result[env["name"]] = entry
    return result


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

REMOTE = """
import main
from aiogram import Bot
from bot.config import load_config
config=load_config()
async def read():
 bot=Bot(config.telegram_bot_token)
 try:
  me=await asyncio.wait_for(bot.get_me(),20)
  glob=await asyncio.wait_for(bot.get_chat_menu_button(),20)
  own=await asyncio.wait_for(bot.get_chat_menu_button(chat_id=config.owner_chat_id),20)
  def shape(m):
   return {'type':m.type,'text':getattr(m,'text',None),'url':getattr(getattr(m,'web_app',None),'url',None)}
  return {'username':me.username,'id':me.id,'is_bot':me.is_bot},shape(glob),shape(own)
 finally:
  await bot.session.close()
bot,menu,owner_menu=asyncio.run(read())
cfg_menu=main._menu_button_for_config(config)
base_url=str(getattr(config,'oauth_public_base_url','') or '').rstrip('/')
result={'deployment':os.environ.get('RAILWAY_DEPLOYMENT_ID'),'bot':bot,'menu':menu,'owner_menu':owner_menu,
 'config_menu':{'type':cfg_menu.type,'text':getattr(cfg_menu,'text',None),'url':getattr(getattr(cfg_menu,'web_app',None),'url',None)},
 'config':{'mini_app_enabled':bool(getattr(config,'mini_app_enabled',False)),
           'pinned_staging':bool(getattr(config,'pinned_staging',False)),
           'production_admitted':bool(getattr(config,'production_admitted',False)),
           'production_contract_present':getattr(config,'production_contract',None) is not None,
           'admin_telegram_bot_username':getattr(config,'admin_telegram_bot_username',None),
           'oauth_public_base_url':base_url,
           'owner_chat_id_present':config.owner_chat_id is not None,
           'owner_chat_id_matches_expected':config.owner_chat_id==int(os.environ.get('OWNER_CHAT_ID') or 0)},
 'admin_panel_key_present':bool(getattr(config,'admin_panel_access_key',None))}
print('TS_QA_JSON='+json.dumps(result))
"""


def remote(code: str) -> dict:
    status()
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
            + repr((result.stdout[-800:], result.stderr[-800:]))
        )
    marker = "TS_QA_JSON="
    lines = [line.split(marker, 1)[1] for line in result.stdout.splitlines() if marker in line]
    if len(lines) != 1:
        raise RuntimeError("SSH QA output incomplete")
    return json.loads(lines[0])


if __name__ == "__main__":
    before = status()
    production = before["production"]["services"][0]["active_deployments"]
    if len(production) != 1 or production[0]["id"] != EXPECTED_DEPLOYMENT:
        raise RuntimeError("Production deployment не совпадает с ожидаемым")
    data = remote(REMOTE)
    after = status()
    out = ROOT / "docs/audits/production-menu-button-2026-10-04/PRODUCTION-menu-diagnostic.json"
    out.write_text(
        json.dumps({"status": before, "read": data, "status_after": after}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(data, ensure_ascii=False, indent=2))
