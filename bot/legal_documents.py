"""Allowlisted canonical documents. Missing approval/data never publishes a draft."""

from __future__ import annotations

import hashlib
import html
import ipaddress
import json
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from .plan_catalog import catalog_payload


CANONICAL_DIR = Path(__file__).resolve().parents[1] / "docs" / "legal"
DOCUMENTS = {
    "privacy": ("PRIVACY-POLICY.md", "Политика конфиденциальности"),
    "agreement": ("USER-AGREEMENT.md", "Пользовательское соглашение"),
    "support": ("SUPPORT.md", "Поддержка"),
    "tariffs": ("TARIFFS.md", "Тарифы"),
    "payments": ("PAYMENTS.md", "Оплата"),
}
MANDATORY_INPUTS = {
    "privacy": ("operator", "operator_address", "retention", "support_contact"),
    "agreement": ("operator", "operator_address", "support_contact", "refund_policy", "chargeback_policy"),
    "support": ("support_contact",),
    "tariffs": (),
    "payments": ("support_contact", "refund_policy", "chargeback_policy"),
}


def support_username(value):
    if not isinstance(value, str):
        return None
    value = value.strip().removeprefix("@")
    return value if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{4,31}", value) else None


def support_email(value):
    if not isinstance(value, str) or any(c.isspace() for c in value) or len(value) > 254:
        return None
    return value if re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,63}", value) else None


def _safe_link(value):
    if len(value) > 2048 or "\\" in value or any(c.isspace() or ord(c) < 32 for c in value):
        return False
    try:
        parsed = urlsplit(value)
        if parsed.scheme == "mailto":
            return not parsed.query and not parsed.fragment and support_email(parsed.path) is not None
        host = (parsed.hostname or "").lower()
        if (parsed.scheme != "https" or parsed.username is not None or parsed.password is not None
            or parsed.port is not None or "." not in host
            or not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+", host)
            or re.fullmatch(r"(?:0x[0-9a-f]+|[0-9]+)(?:\.(?:0x[0-9a-f]+|[0-9]+))*", host)
            or host.endswith((".local", ".localhost", ".internal", ".test", ".invalid"))):
            return False
        try:
            ipaddress.ip_address(host)
            return False
        except ValueError:
            return True
    except ValueError:
        return False


def _inline(text):
    result = []
    end = 0
    for match in re.finditer(r"\[([^\]\n]{1,256})\]\(([^)\n]{1,2048})\)", text):
        result.append(html.escape(text[end:match.start()]))
        label, url = match.groups()
        if _safe_link(url):
            result.append(f'<a href="{html.escape(url, quote=True)}" rel="noopener noreferrer">{html.escape(label)}</a>')
        else:
            result.append(html.escape(label))
        end = match.end()
    result.append(html.escape(text[end:]))
    return re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", "".join(result))


def render_markdown(text):
    if not isinstance(text, str) or len(text) > 65536:
        raise ValueError("legal document too large")
    blocks, paragraph, items = [], [], []
    def flush():
        if paragraph:
            blocks.append("<p>" + _inline(" ".join(paragraph)) + "</p>")
            paragraph.clear()
        if items:
            blocks.append("<ul>" + "".join("<li>" + _inline(item) + "</li>" for item in items) + "</ul>")
            items.clear()
    for line in text.splitlines():
        line = line.strip()
        if not line:
            flush()
        elif line.startswith("#") and (match := re.fullmatch(r"(#{1,3})\s+(.+)", line)):
            flush()
            level = min(3, len(match[1]) + 1)
            blocks.append(f"<h{level}>" + _inline(match[2]) + f"</h{level}>")
        elif line.startswith("- "):
            if paragraph:
                flush()
            items.append(line[2:])
        else:
            if items:
                flush()
            paragraph.append(line)
    flush()
    return "\n".join(blocks)


@dataclass(frozen=True)
class LegalDocument:
    id: str
    version: str | None
    title: str
    ready: bool
    body_html: str


@dataclass(frozen=True)
class LegalDocumentSummary:
    id: str
    title: str
    version: str | None
    ready: bool
    url: str


@dataclass(frozen=True)
class SupportState:
    available: bool
    telegram_url: str | None
    email: str | None
    documents: tuple[LegalDocumentSummary, ...]
    bank_ready: bool


def _owner_inputs(config):
    username = support_username(getattr(config, "support_username", None))
    email = support_email(getattr(config, "support_email", None))
    values = {key: getattr(config, field, None) for key, field in {
        "operator": "legal_operator", "operator_address": "legal_operator_address",
        "retention": "legal_retention", "refund_policy": "legal_refund_policy",
        "chargeback_policy": "legal_chargeback_policy",
    }.items()}
    values = {key: value.strip() if isinstance(value, str) and 0 < len(value.strip()) <= 4096 else None
              for key, value in values.items()}
    values["support_contact"] = " · ".join(value for value in (
        "@" + username if username else None, email) if value) or None
    return values


class LegalDocumentStore:
    def __init__(self, root=CANONICAL_DIR):
        self.root = Path(root).resolve()
        self._manifest_digest = None
        self._manifest_rows = {}

    def _manifest(self):
        try:
            path = self.root / "manifest.json"
            if path.resolve().parent != self.root or path.stat().st_size > 32768:
                return {}
            raw = path.read_bytes()
            digest = hashlib.sha256(raw).hexdigest()
            if digest != self._manifest_digest:
                data = json.loads(raw)
                if not isinstance(data, dict) or not isinstance(data.get("version"), str) or not isinstance(data.get("documents"), list):
                    return {}
                rows = {}
                for row in data["documents"]:
                    if not isinstance(row, dict) or row.get("id") not in DOCUMENTS or row["id"] in rows:
                        return {}
                    rows[row["id"]] = row
                self._manifest_digest, self._manifest_rows = digest, rows
            return self._manifest_rows
        except (OSError, ValueError, TypeError):
            return {}

    def get(self, document_id, catalog, owner_config):
        if document_id not in DOCUMENTS:
            raise KeyError("unknown legal document")
        filename, title = DOCUMENTS[document_id]
        row = self._manifest().get(document_id, {})
        version = row.get("version")
        if not isinstance(version, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", version):
            version = None
        unavailable = LegalDocument(document_id, version, title, False,
                                    "<p>Документ пока недоступен. Мы готовим актуальную редакцию.</p>")
        required = row.get("required_owner_inputs")
        inputs = _owner_inputs(owner_config)
        if (row.get("filename") != filename or row.get("owner_accepted") is not True
            or version is None or row.get("catalog_version") != catalog.get("version")
            or not isinstance(required, list) or any(not isinstance(key, str) or not inputs.get(key) for key in required)):
            return unavailable
        required = tuple(dict.fromkeys((*MANDATORY_INPUTS[document_id], *required)))
        if any(not inputs.get(key) for key in required):
            return unavailable
        try:
            path = self.root / filename
            if path.resolve().parent != self.root or path.stat().st_size > 65536:
                return unavailable
            text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
            if hashlib.sha256(text.encode()).hexdigest() != row.get("source_sha256"):
                return unavailable
            if document_id in {"agreement", "tariffs", "payments"}:
                if any(product["price_label"] not in text for product in catalog["products"]):
                    return unavailable
            body = render_markdown(text)
            if required:
                body += "\n<h2>Сведения этой редакции</h2>" + "".join(
                    "<p>" + html.escape(inputs[key]) + "</p>" for key in required)
            return LegalDocument(document_id, version, title, True, body)
        except (OSError, ValueError, TypeError, KeyError):
            return unavailable


DEFAULT_STORE = LegalDocumentStore()


def get_legal_document(document_id, catalog, owner_config):
    return DEFAULT_STORE.get(document_id, catalog, owner_config)


def get_support_state(config, *, store=None, catalog=None):
    store = store or DEFAULT_STORE
    catalog = catalog or catalog_payload()
    username = support_username(getattr(config, "support_username", None))
    email = support_email(getattr(config, "support_email", None))
    documents = tuple(LegalDocumentSummary(document.id, document.title, document.version,
                                          document.ready, "/app/legal/" + document.id)
                      for document in (store.get(key, catalog, config) for key in DOCUMENTS))
    available = username is not None or email is not None
    return SupportState(available, "https://t.me/" + username if username else None, email,
                        documents, available and all(doc.ready for doc in documents if doc.id in {"privacy", "agreement"}))
