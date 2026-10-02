"""Build the local, standalone design preview; no bot or network operations."""

import base64
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
html = (ROOT / "index.html").read_text(encoding="utf-8")
css = (ROOT / "selected.css").read_text(encoding="utf-8")
js = (ROOT / "selected.js").read_text(encoding="utf-8")
catalog = (ROOT / "plus-catalog.js").read_text(encoding="utf-8")
for name in ("twitch.png", "monstercat.png"):
    data = (ROOT / "assets" / name).read_bytes()
    mime = "image/png" if data.startswith(b"\x89PNG") else "image/jpeg"
    uri = f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"
    js = js.replace(f"assets/{name}", uri)
assert "</script>" not in js.lower()
assert "</script>" not in catalog.lower()
html = html.replace('<link rel="stylesheet" href="selected.css">', f"<style>\n{css}\n</style>")
html = html.replace('<script src="selected.js" defer></script>', "")
html = html.replace('<script src="plus-catalog.js" defer></script>', "")
html = html.replace("</body>", f"<script>\n{catalog}\n{js}\n</script>\n</body>")
(ROOT / "preview.html").write_text(html, encoding="utf-8", newline="\n")
snapshot = {
    "date": "2026-10-02",
    "phase": "approved A design; fourth Plus navigation in local preview; implementation plan pending",
    "branch": "autonomous/twitchsignal-roadmap",
    "base_head": "e0d44c315a1c81dc5b310dd1f3c7f3169915c985",
    "files": {
        name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
        for name in ("index.html", "selected.css", "selected.js", "plus-catalog.js", "preview.html")
    },
    "product_changed": False,
    "deployment_performed": False,
}
(ROOT / "selected-snapshot.json").write_text(json.dumps(snapshot, indent=2) + "\n", encoding="utf-8")
print("Standalone preview and five source hashes saved")
