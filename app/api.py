from __future__ import annotations

import json
import secrets
from pathlib import Path
from typing import TYPE_CHECKING

from aiohttp import web

from app.mail_parser import make_local_part, normalize_mailbox, utc_now_iso

if TYPE_CHECKING:
    from app.config import ServiceConfig
    from app.storage import MessageStore

DEFAULT_WEB_DIR = Path(__file__).resolve().parent.parent / "web"


def extract_token(request: web.Request) -> str:
    auth = str(request.headers.get("Authorization", "")).strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return str(request.query.get("token", "")).strip()


def check_token(request: web.Request, config: ServiceConfig) -> bool:
    return extract_token(request) == config.api_token


async def health_handler(request: web.Request) -> web.Response:
    _ = request
    return web.json_response({"ok": True, "time": utc_now_iso()})


async def generate_email_handler(request: web.Request) -> web.Response:
    config: ServiceConfig = request.app["config"]
    if not check_token(request, config):
        return web.json_response({"error": "Unauthorized"}, status=401)
    try:
        payload = await request.json()
    except (json.JSONDecodeError, Exception):
        payload = {}
    domain = str(payload.get("domain") or config.default_domain).strip().lower()
    if not config.allows_domain(domain):
        return web.json_response({"error": f"domain not allowed: {domain}"}, status=400)
    name = str(payload.get("name") or "").strip().lower()
    local_part = make_local_part(name or "mail", secrets.randbelow(100000))
    return web.json_response({"email": f"{local_part}@{domain}", "name": local_part, "domain": domain})


async def get_code_handler(request: web.Request) -> web.Response:
    config: ServiceConfig = request.app["config"]
    store: MessageStore = request.app["store"]
    if not check_token(request, config):
        return web.json_response({"error": "Unauthorized"}, status=401)
    mailbox = normalize_mailbox(str(request.query.get("email") or request.query.get("mailbox") or ""))
    if not mailbox:
        return web.json_response({"error": "mailbox is required"}, status=400)
    if "@" not in mailbox:
        mailbox = f"{mailbox}@{config.default_domain}"
    record = store.latest_code(mailbox)
    if not record:
        return web.json_response({"error": "No OTP found", "mailbox": mailbox}, status=404)
    return web.json_response(
        {
            "mailbox": mailbox,
            "code": record["code"],
            "subject": record["subject"],
            "sender": record["sender"],
            "received_at": record["received_at"],
        }
    )


async def ui_index_handler(request: web.Request) -> web.Response:
    config: ServiceConfig = request.app["config"]
    web_dir: Path = request.app.get("web_dir", DEFAULT_WEB_DIR)
    index_file = web_dir / "index.html"
    if not index_file.exists():
        return web.Response(text="<h1>FlashMail Web UI not found</h1>", content_type="text/html", status=404)
    content = index_file.read_text(encoding="utf-8")
    page = content.replace("__DEFAULT_DOMAIN__", config.default_domain)
    return web.Response(text=page, content_type="text/html", charset="utf-8")


async def ui_messages_handler(request: web.Request) -> web.Response:
    config: ServiceConfig = request.app["config"]
    store: MessageStore = request.app["store"]
    mailbox = normalize_mailbox(str(request.query.get("mailbox", "")))
    if not mailbox:
        return web.json_response({"error": "mailbox required"}, status=400)
    if "@" not in mailbox:
        mailbox = f"{mailbox}@{config.default_domain}"
    msgs = store.list_mailbox_messages(mailbox, limit=50)
    return web.json_response({"mailbox": mailbox, "messages": msgs})


async def ui_message_detail_handler(request: web.Request) -> web.Response:
    store: MessageStore = request.app["store"]
    try:
        msg_id = int(request.match_info["id"])
    except (KeyError, ValueError):
        return web.json_response({"error": "invalid id"}, status=400)
    msg = store.get_message_by_id(msg_id)
    if not msg:
        return web.json_response({"error": "not found"}, status=404)
    return web.json_response(msg)


def build_web_app(
    config: ServiceConfig,
    store: MessageStore,
    web_dir: Path | None = None,
) -> web.Application:
    app = web.Application()
    app["config"] = config
    app["store"] = store
    resolved_web_dir = Path(web_dir or DEFAULT_WEB_DIR).resolve()
    app["web_dir"] = resolved_web_dir

    app.add_routes(
        [
            web.get("/", ui_index_handler),
            web.get("/health", health_handler),
            web.post("/api/emails/generate", generate_email_handler),
            web.get("/get-code", get_code_handler),
            web.get("/api/ui/messages", ui_messages_handler),
            web.get("/api/ui/message/{id}", ui_message_detail_handler),
        ]
    )

    if resolved_web_dir.exists():
        # Direct fallback for root /style.css and /app.js
        style_file = resolved_web_dir / "style.css"
        app_file = resolved_web_dir / "app.js"
        if style_file.exists():
            async def _style_handler(req: web.Request) -> web.StreamResponse:
                return web.FileResponse(style_file)
            app.router.add_get("/style.css", _style_handler)
        if app_file.exists():
            async def _app_handler(req: web.Request) -> web.StreamResponse:
                return web.FileResponse(app_file)
            app.router.add_get("/app.js", _app_handler)
        app.router.add_static("/static/", resolved_web_dir, name="static")

    return app
