"""Public document reader and signed contact state on the existing app server."""

import html
from dataclasses import asdict
from pathlib import Path

from aiohttp import web

from .admin_web import SECURITY_HEADERS
from .legal_documents import DEFAULT_STORE, DOCUMENTS, get_support_state
from .mini_app_auth import verified_payload
from .plan_catalog import catalog_payload


UI_DIR = Path(__file__).with_name("legal_ui")


def install_legal_routes(app, bot_token, *, owner_config=None, store=None):
    store = store or DEFAULT_STORE

    def secure(response):
        for key, value in SECURITY_HEADERS.items():
            response.headers[key] = value
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Robots-Tag"] = "noindex, nofollow"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    async def support(request):
        _, values, status = await verified_payload(request, bot_token)
        if status != 200:
            return secure(web.json_response({"error": "unauthorized"}, status=status))
        if set(values) != {"init_data"}:
            return secure(web.json_response({"error": "invalid_support_request"}, status=400))
        return secure(web.json_response(asdict(get_support_state(owner_config, store=store))))

    async def document(request):
        document_id = request.match_info["document_id"]
        if document_id not in DOCUMENTS:
            raise web.HTTPNotFound()
        result = store.get(document_id, catalog_payload(), owner_config)
        if request.query.get("format") == "json":
            return secure(web.json_response(asdict(result), status=200 if result.ready else 503))
        template = (UI_DIR / "index.html").read_text(encoding="utf-8")
        for key, value in {"title": html.escape(result.title), "version": html.escape(result.version or ""),
                           "body": result.body_html}.items():
            template = template.replace("{{" + key + "}}", value)
        return secure(web.Response(text=template, content_type="text/html", status=200 if result.ready else 503))

    async def css(_request):
        return secure(web.Response(text=(UI_DIR / "legal.css").read_text(encoding="utf-8"), content_type="text/css"))

    app.router.add_post("/app/api/support/state", support)
    app.router.add_get("/app/legal/legal.css", css)
    app.router.add_get("/app/legal/{document_id}", document)
