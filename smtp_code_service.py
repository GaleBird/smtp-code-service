from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email import policy
from email.parser import BytesParser
from email.utils import parseaddr
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aiohttp import web

CODE_RE = re.compile(r"(?<![#\d])((?:\d[\s-]?){6})(?!\d)")
HTML_STYLE_RE = re.compile(r"(?is)<style\b[^>]*>.*?</style>")
HTML_SCRIPT_RE = re.compile(r"(?is)<script\b[^>]*>.*?</script>")
HTML_TAG_RE = re.compile(r"(?is)<[^>]+>")
LOCAL_PART_RE = re.compile(r"[^a-z0-9._-]+")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def normalize_mailbox(value: str) -> str:
    parsed = parseaddr(str(value or ""))[1] or str(value or "")
    return parsed.strip().strip("<>").strip().lower()


def extract_text_from_message(content: bytes) -> str:
    message = BytesParser(policy=policy.default).parsebytes(content)
    if message.is_multipart():
        parts: list[str] = []
        for part in message.walk():
            if part.get_content_type() != "text/plain":
                continue
            try:
                payload = part.get_content()
            except Exception:
                continue
            if isinstance(payload, str) and payload.strip():
                parts.append(payload)
        return "\n".join(parts)
    try:
        payload = message.get_content()
    except Exception:
        return ""
    return payload if isinstance(payload, str) else ""


def extract_subject(content: bytes) -> str:
    message = BytesParser(policy=policy.default).parsebytes(content)
    return str(message.get("subject", "")).strip()


def extract_sender(content: bytes) -> str:
    message = BytesParser(policy=policy.default).parsebytes(content)
    return str(message.get("from", "")).strip()


def extract_code(text: str) -> str:
    source = str(text or "")
    if not source:
        return ""
    cleaned = HTML_STYLE_RE.sub(" ", source)
    cleaned = HTML_SCRIPT_RE.sub(" ", cleaned)
    plain_text = HTML_TAG_RE.sub(" ", cleaned)
    for haystack in (plain_text, source):
        for match in CODE_RE.finditer(haystack):
            digits = re.sub(r"\D", "", match.group(1))
            if len(digits) == 6:
                return digits
    return ""


def make_local_part(prefix: str, index_seed: int) -> str:
    cleaned = LOCAL_PART_RE.sub("", str(prefix or "").strip().lower()) or "mail"
    return f"{cleaned}{int(time.time())}{index_seed:05d}"


def parse_domains(value: str) -> tuple[str, ...]:
    domains: list[str] = []
    for raw in str(value or "").split(","):
        domain = raw.strip().lower()
        if domain and domain not in domains:
            domains.append(domain)
    return tuple(domains)


@dataclass(frozen=True)
class ServiceConfig:
    domains: tuple[str, ...]
    api_token: str
    db_path: Path

    @property
    def default_domain(self) -> str:
        return self.domains[0]

    def allows_domain(self, domain: str) -> bool:
        return domain.strip().lower() in self.domains


class MessageStore:
    def __init__(self, db_path: Path):
        self._db_path = db_path
        self._lock = threading.Lock()

    def init(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    mailbox TEXT NOT NULL,
                    code TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    sender TEXT NOT NULL,
                    body_text TEXT NOT NULL,
                    received_at TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_mailbox ON messages(mailbox)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_time ON messages(received_at)")
            conn.commit()

    def insert(self, *, mailbox: str, code: str, subject: str, sender: str, body_text: str, received_at: str) -> None:
        with self._lock, sqlite3.connect(self._db_path) as conn:
            conn.execute(
                """
                INSERT INTO messages(mailbox, code, subject, sender, body_text, received_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (mailbox, code, subject, sender, body_text, received_at),
            )
            conn.commit()

    def latest_code(self, mailbox: str) -> dict[str, str] | None:
        with self._lock, sqlite3.connect(self._db_path) as conn:
            row = conn.execute(
                """
                SELECT code, subject, sender, received_at
                FROM messages
                WHERE mailbox = ? AND code <> ''
                ORDER BY id DESC
                LIMIT 1
                """,
                (mailbox,),
            ).fetchone()
        if not row:
            return None
        return {
            "code": str(row[0] or ""),
            "subject": str(row[1] or ""),
            "sender": str(row[2] or ""),
            "received_at": str(row[3] or ""),
        }

    def list_mailbox_messages(self, mailbox: str, limit: int = 50) -> list[dict]:
        with self._lock, sqlite3.connect(self._db_path) as conn:
            rows = conn.execute(
                """
                SELECT id, subject, sender, code, received_at
                FROM messages
                WHERE mailbox = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (mailbox, limit),
            ).fetchall()
        return [
            {"id": r[0], "subject": r[1], "sender": r[2], "code": r[3], "received_at": r[4]}
            for r in rows
        ]

    def get_message_by_id(self, msg_id: int) -> dict | None:
        with self._lock, sqlite3.connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT id, mailbox, subject, sender, code, body_text, received_at FROM messages WHERE id = ?",
                (msg_id,),
            ).fetchone()
        if not row:
            return None
        return {
            "id": row[0], "mailbox": row[1], "subject": row[2],
            "sender": row[3], "code": row[4], "body_text": row[5], "received_at": row[6],
        }


class SMTPHandler:
    def __init__(self, config: ServiceConfig, store: MessageStore):
        self._config = config
        self._store = store

    async def handle_DATA(self, _server, _session, envelope):
        body_text = extract_text_from_message(envelope.content)
        code = extract_code(body_text)
        subject = extract_subject(envelope.content)
        sender = extract_sender(envelope.content)
        received_at = utc_now_iso()
        for raw in envelope.rcpt_tos:
            mailbox = normalize_mailbox(raw)
            if not mailbox:
                continue
            if not self._config.allows_domain(mailbox.split("@")[-1]):
                continue
            self._store.insert(
                mailbox=mailbox,
                code=code,
                subject=subject,
                sender=sender,
                body_text=body_text,
                received_at=received_at,
            )
        return "250 Message accepted for delivery"


def extract_token(request: web.Request) -> str:
    auth = str(request.headers.get("Authorization", "")).strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return str(request.query.get("token", "")).strip()


def check_token(request: web.Request, config: ServiceConfig) -> bool:
    return extract_token(request) == config.api_token


async def health_handler(request: "web.Request"):
    from aiohttp import web

    _ = request
    return web.json_response({"ok": True, "time": utc_now_iso()})


async def generate_email_handler(request: "web.Request"):
    from aiohttp import web

    config: ServiceConfig = request.app["config"]
    if not check_token(request, config):
        return web.json_response({"error": "Unauthorized"}, status=401)
    try:
        payload = await request.json()
    except json.JSONDecodeError:
        payload = {}
    domain = str(payload.get("domain") or config.default_domain).strip().lower()
    if not config.allows_domain(domain):
        return web.json_response({"error": f"domain not allowed: {domain}"}, status=400)
    name = str(payload.get("name") or "").strip().lower()
    local_part = make_local_part(name or "mail", secrets.randbelow(100000))
    return web.json_response({"email": f"{local_part}@{domain}", "name": local_part, "domain": domain})


async def get_code_handler(request: "web.Request"):
    from aiohttp import web

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


WEB_UI_HTML = r"""
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>FlashMail</title>
<style>
:root {
  /* Dark Theme Tokens */
  --app-bg: #101217;
  --header-bg: #15181E;
  --sidebar-bg: #16191F;
  --surface: #1B1F26;
  --canvas-bg: #101217;
  --hover-bg: #20252D;
  --selected-bg: #222730;
  --border: #292E36;
  --border-subtle: #1F232B;
  --text-primary: #E8EAED;
  --text-secondary: #9AA3AF;
  --text-muted: #697281;
  --accent: #84B819;
  --accent-hover: #95ce1d;
  --accent-subtle: rgba(132, 184, 25, 0.12);
  --card-bg: #ffffff;
  --card-border: rgba(255, 255, 255, 0.08);
  --card-shadow: 0 4px 16px rgba(0, 0, 0, 0.35);
  --code-pill-bg: #222832;
  --code-pill-text: #84B819;
  --scrollbar: #292E36;
  --scrollbar-thumb: #3B424E;
  --font: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", Roboto, sans-serif;
  --mono: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, "Liberation Mono", monospace;
}

html.light {
  /* Light Theme Tokens */
  --app-bg: #F6F7F9;
  --header-bg: #FAFAFB;
  --sidebar-bg: #F3F4F6;
  --surface: #FFFFFF;
  --canvas-bg: #F6F7F9;
  --hover-bg: #ECEFF3;
  --selected-bg: #E9EDF2;
  --border: #E1E4E8;
  --border-subtle: #E8EBEE;
  --text-primary: #20242C;
  --text-secondary: #667085;
  --text-muted: #717684;
  --accent: #4f7c00;
  --accent-hover: #436a00;
  --accent-subtle: rgba(79, 124, 0, 0.08);
  --card-bg: #ffffff;
  --card-border: rgba(0, 0, 0, 0.08);
  --card-shadow: 0 2px 8px rgba(0, 0, 0, 0.04), 0 1px 2px rgba(0, 0, 0, 0.02);
  --code-pill-bg: #F0F4E8;
  --code-pill-text: #4D7A00;
  --scrollbar: #E1E4E8;
  --scrollbar-thumb: #C8CCD4;
}

* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  background: var(--app-bg);
  color: var(--text-primary);
  font-family: var(--font);
  font-size: 14px;
  height: 100vh;
  overflow: hidden;
  -webkit-font-smoothing: antialiased;
}

/* Custom minimal scrollbar */
::-webkit-scrollbar { width: 6px; height: 6px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: var(--scrollbar-thumb); border-radius: 4px; }
::-webkit-scrollbar-thumb:hover { background: var(--text-muted); }

/* Main app container */
.app {
  display: flex;
  flex-direction: column;
  height: 100vh;
  overflow: hidden;
}

/* ──────────────── Header ──────────────── */
.app-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 16px;
  height: 48px;
  flex-shrink: 0;
  border-bottom: 1px solid var(--border);
  background: var(--header-bg);
}

.header-brand {
  display: flex;
  align-items: center;
  gap: 4px;
  user-select: none;
  width: 220px;
}
.brand-name {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
  letter-spacing: -0.2px;
}
.brand-suffix {
  font-size: 13px;
  color: var(--text-secondary);
  font-weight: 400;
}

.header-center {
  flex: 1;
  max-width: 480px;
  display: flex;
  justify-content: center;
}

.search-box {
  display: flex;
  align-items: center;
  width: 100%;
  background: var(--app-bg);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 3px 4px 3px 10px;
  transition: border-color 120ms ease, box-shadow 120ms ease;
}
.search-box:focus-within {
  border-color: var(--accent);
  box-shadow: 0 0 0 2px var(--accent-subtle);
}
.search-icon {
  color: var(--text-muted);
  flex-shrink: 0;
  margin-right: 8px;
}
.search-box input {
  flex: 1;
  background: transparent;
  border: none;
  outline: none;
  color: var(--text-primary);
  font-size: 13px;
  font-family: var(--font);
  min-width: 0;
}
.search-box input::placeholder {
  color: var(--text-muted);
}
.search-btn {
  background: var(--accent);
  color: #101217;
  border: none;
  border-radius: 4px;
  padding: 4px 12px;
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  font-family: var(--font);
  transition: background 120ms ease, opacity 120ms ease;
  white-space: nowrap;
}
.search-btn:hover {
  background: var(--accent-hover);
}
.search-btn:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 1px;
}
.search-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
html.light .search-btn {
  color: #ffffff;
}

.header-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  width: 220px;
  gap: 8px;
}

.icon-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  border-radius: 6px;
  border: 1px solid var(--border);
  background: transparent;
  color: var(--text-secondary);
  cursor: pointer;
  transition: background 120ms ease, color 120ms ease, border-color 120ms ease;
}
.icon-btn:hover {
  background: var(--hover-bg);
  color: var(--text-primary);
  border-color: var(--text-muted);
}

/* ──────────────── Layout Body ──────────────── */
.app-main {
  flex: 1;
  display: flex;
  overflow: hidden;
  min-height: 0;
}

/* ──────────────── Sidebar ──────────────── */
.sidebar {
  width: 300px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  border-right: 1px solid var(--border);
  background: var(--sidebar-bg);
  overflow: hidden;
  min-height: 0;
}

.sidebar-header {
  padding: 12px 14px 10px;
  flex-shrink: 0;
  border-bottom: 1px solid var(--border);
}

.sidebar-header-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 6px;
}
.sidebar-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.icon-btn-ghost {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  background: transparent;
  border: none;
  border-radius: 4px;
  color: var(--text-muted);
  cursor: pointer;
  transition: color 120ms ease, background 120ms ease;
}
.icon-btn-ghost:hover {
  color: var(--text-primary);
  background: var(--hover-bg);
}
.icon-btn-ghost.spinning svg {
  animation: spin 0.6s linear;
}
@keyframes spin {
  100% { transform: rotate(360deg); }
}

.mailbox-info {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--text-secondary);
  cursor: pointer;
  padding: 3px 6px;
  margin-left: -6px;
  border-radius: 4px;
  transition: background 120ms ease, color 120ms ease;
}
.mailbox-info:hover {
  background: var(--hover-bg);
  color: var(--text-primary);
}
.mailbox-email {
  font-family: var(--mono);
  font-size: 12px;
  max-width: 190px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.mailbox-dot {
  color: var(--text-muted);
}
.mailbox-count {
  font-size: 11px;
  font-weight: 600;
  color: var(--text-muted);
}
.copy-hint-icon {
  opacity: 0;
  transition: opacity 120ms ease;
}
.mailbox-info:hover .copy-hint-icon {
  opacity: 1;
}

/* ──────────────── Mail List ──────────────── */
.mail-list {
  flex: 1;
  overflow-y: auto;
  min-height: 0;
}

.mail-item {
  display: flex;
  flex-direction: column;
  justify-content: center;
  min-height: 74px;
  padding: 12px 14px;
  cursor: pointer;
  border-bottom: 1px solid var(--border-subtle);
  border-left: 2px solid transparent;
  background: transparent;
  transition: background 120ms ease, border-color 120ms ease;
  user-select: none;
}
.mail-item:hover {
  background: var(--hover-bg);
}
.mail-item.active {
  background: var(--selected-bg);
  border-left-color: var(--accent);
}

.mail-item-top {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 4px;
}
.mail-item-subject {
  font-size: 13px;
  font-weight: 500;
  color: var(--text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 1;
}
.mail-item.latest .mail-item-subject {
  font-weight: 600;
}
.mail-item.active .mail-item-subject {
  font-weight: 600;
}
.mail-item-time {
  font-size: 11px;
  color: var(--text-muted);
  flex-shrink: 0;
}

.mail-item-bottom {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.mail-item-sender {
  font-size: 12px;
  color: var(--text-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 1;
}
.mail-item-code {
  font-family: var(--mono);
  font-size: 11px;
  font-weight: 600;
  color: var(--code-pill-text);
  background: var(--code-pill-bg);
  padding: 1px 6px;
  border-radius: 4px;
  letter-spacing: 0.5px;
  flex-shrink: 0;
}

.empty-list {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  color: var(--text-muted);
  font-size: 13px;
  padding: 48px 16px;
  text-align: center;
}

/* ──────────────── Detail View (Right) ──────────────── */
.mail-detail {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  background: var(--app-bg);
  min-width: 0;
  min-height: 0;
}

.detail-placeholder {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--text-muted);
  font-size: 13px;
}

/* Mail Header */
.mail-header-section {
  padding: 16px 28px 14px;
  background: var(--surface);
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
}

.mail-subject-row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 8px;
}
.mail-subject {
  font-size: 18px;
  font-weight: 600;
  line-height: 1.35;
  color: var(--text-primary);
  word-break: break-word;
}
.mail-date-brief {
  font-size: 12px;
  color: var(--text-muted);
  white-space: nowrap;
  padding-top: 3px;
}

.mail-meta-line {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 10px;
}

.sender-group {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  font-size: 13px;
}
.sender-display-name {
  font-weight: 500;
  color: var(--text-primary);
}
.sender-recipient-summary {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  color: var(--text-secondary);
  font-size: 12px;
  cursor: pointer;
  padding: 2px 6px;
  border-radius: 4px;
  transition: background 120ms ease, color 120ms ease;
  user-select: none;
}
.sender-recipient-summary:hover {
  background: var(--hover-bg);
  color: var(--text-primary);
}
.chevron-icon {
  transition: transform 150ms ease;
}
.chevron-icon.open {
  transform: rotate(180deg);
}

.header-code-pill {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  background: var(--code-pill-bg);
  border: 1px solid var(--border);
  padding: 3px 10px;
  border-radius: 6px;
  cursor: pointer;
  user-select: none;
  transition: border-color 120ms ease;
}
.header-code-pill:hover {
  border-color: var(--accent);
}
.code-pill-label {
  font-size: 11px;
  color: var(--text-muted);
}
.code-pill-val {
  font-family: var(--mono);
  font-size: 15px;
  font-weight: 600;
  letter-spacing: 2px;
  color: var(--code-pill-text);
}
.code-pill-btn {
  font-size: 11px;
  color: var(--text-secondary);
  border-left: 1px solid var(--border);
  padding-left: 6px;
}
.header-code-pill:hover .code-pill-btn {
  color: var(--accent);
}

/* Expanded full headers */
.header-expanded-details {
  margin-top: 10px;
  padding-top: 10px;
  border-top: 1px solid var(--border-subtle);
  font-size: 12px;
  line-height: 1.7;
  color: var(--text-secondary);
}
.detail-grid {
  display: grid;
  grid-template-columns: 56px 1fr;
  gap: 2px 8px;
}
.detail-grid .dt-lbl {
  color: var(--text-muted);
}
.detail-grid .dt-val {
  word-break: break-all;
}

/* ──────────────── Reader Toolbar ──────────────── */
.reader-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 28px;
  height: 40px;
  background: var(--surface);
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
  position: relative;
  z-index: 10;
}

.view-tabs {
  display: flex;
  gap: 16px;
  height: 100%;
}
.tab-btn {
  position: relative;
  display: inline-flex;
  align-items: center;
  height: 100%;
  background: transparent;
  border: none;
  font-size: 13px;
  font-weight: 500;
  font-family: var(--font);
  color: var(--text-muted);
  cursor: pointer;
  padding: 0;
  transition: color 120ms ease;
}
.tab-btn:hover {
  color: var(--text-secondary);
}
.tab-btn.active {
  color: var(--text-primary);
  font-weight: 600;
}
.tab-btn.active::after {
  content: "";
  position: absolute;
  bottom: 0;
  left: 0;
  right: 0;
  height: 2px;
  background: var(--accent);
  border-radius: 2px 2px 0 0;
}

.toolbar-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

/* External Links Popover Button */
.links-popover-wrap {
  position: relative;
  display: inline-flex;
}
.toolbar-link-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  background: transparent;
  border: 1px solid var(--border);
  border-radius: 5px;
  padding: 4px 10px;
  font-size: 12px;
  font-weight: 500;
  font-family: var(--font);
  color: var(--text-secondary);
  cursor: pointer;
  transition: background 120ms ease, color 120ms ease, border-color 120ms ease;
}
.toolbar-link-btn:hover {
  background: var(--hover-bg);
  color: var(--text-primary);
  border-color: var(--text-muted);
}
.toolbar-link-btn.active {
  background: var(--selected-bg);
  color: var(--text-primary);
  border-color: var(--text-muted);
}

.links-dropdown {
  position: absolute;
  top: calc(100% + 6px);
  right: 0;
  width: 320px;
  max-height: 380px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 6px;
  box-shadow: var(--card-shadow);
  z-index: 50;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.dropdown-header {
  padding: 8px 12px;
  font-size: 11px;
  font-weight: 600;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.5px;
  border-bottom: 1px solid var(--border-subtle);
  background: var(--header-bg);
}
.dropdown-list {
  padding: 4px 0;
  overflow-y: auto;
}
.dropdown-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 8px 12px;
  text-decoration: none;
  font-size: 12px;
  transition: background 120ms ease;
  overflow: hidden;
}
.dropdown-item:hover {
  background: var(--hover-bg);
}
.dropdown-item-info {
  flex: 1;
  overflow: hidden;
}
.dropdown-item-domain {
  font-weight: 500;
  color: var(--text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.dropdown-item-path {
  font-size: 11px;
  color: var(--text-muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.dropdown-item-arrow {
  color: var(--text-muted);
  flex-shrink: 0;
}
.dropdown-item:hover .dropdown-item-arrow {
  color: var(--accent);
}

/* ──────────────── Reader Canvas & Email Paper ──────────────── */
.reader-canvas {
  flex: 1;
  background: var(--canvas-bg);
  padding: 16px 24px 20px;
  display: flex;
  justify-content: center;
  align-items: stretch;
  overflow: hidden;
  min-height: 0;
}

.email-paper {
  width: 100%;
  max-width: 920px;
  height: 100%;
  flex: 1;
  background: var(--card-bg);
  border-radius: 8px;
  border: 1px solid var(--card-border);
  box-shadow: var(--card-shadow);
  display: flex;
  flex-direction: column;
  overflow: hidden;
  min-height: 0;
  position: relative;
}

.email-iframe {
  width: 100%;
  height: 100%;
  flex: 1;
  border: none;
  background: #ffffff;
  display: block;
  min-height: 0;
}

.email-text-view {
  width: 100%;
  height: 100%;
  flex: 1;
  overflow-y: auto;
  padding: 28px 36px;
  background: var(--surface);
  color: var(--text-primary);
  font-family: var(--mono);
  font-size: 13px;
  line-height: 1.7;
  white-space: pre-wrap;
  word-break: break-word;
  min-height: 0;
}
html.light .email-text-view {
  background: #ffffff;
}

/* ──────────────── Toast ──────────────── */
#toast {
  position: fixed;
  bottom: 24px;
  right: 24px;
  background: var(--surface);
  border: 1px solid var(--border);
  color: var(--text-primary);
  padding: 8px 14px;
  border-radius: 6px;
  font-size: 13px;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.2);
  opacity: 0;
  transform: translateY(6px);
  transition: opacity 150ms ease, transform 150ms ease;
  pointer-events: none;
  z-index: 999;
}
#toast.show {
  opacity: 1;
  transform: translateY(0);
}

/* ──────────────── Responsive ──────────────── */
@media (max-width: 768px) {
  .sidebar { width: 100%; border-right: none; height: 35vh; }
  .app-main { flex-direction: column; }
  .mail-detail { flex: 1; height: 65vh; min-height: 0; }
  .header-brand { width: auto; }
  .header-actions { width: auto; }
  .search-box { width: 180px; }
  .mail-header-section, .reader-toolbar { padding-left: 16px; padding-right: 16px; }
  .reader-canvas { padding: 8px; }
  .email-paper { border-radius: 6px; }
}
</style>
</head>
<body>
<div class="app">
  <header class="app-header">
    <div class="header-brand">
      <span class="brand-name">Flash</span><span class="brand-suffix">Mail</span>
    </div>
    <div class="header-center">
      <div class="search-box">
        <svg class="search-icon" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><circle cx="11" cy="11" r="8"/><path d="M21 21l-4.35-4.35"/></svg>
        <input id="emailInput" type="email" placeholder="输入邮箱地址或前缀，例如 lghagl..." autocomplete="off" spellcheck="false" />
        <button class="search-btn" onclick="loadInbox()">查看</button>
      </div>
    </div>
    <div class="header-actions">
      <button class="icon-btn" onclick="toggleTheme()" id="themeBtn" title="切换深浅主题">
        <svg id="themeIcon" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="5"/><path d="M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42"/></svg>
      </button>
    </div>
  </header>

  <main class="app-main">
    <aside class="sidebar">
      <div class="sidebar-header" id="sidebarHead" style="display:none">
        <div class="sidebar-header-row">
          <span class="sidebar-title">收件箱</span>
          <button class="icon-btn-ghost" onclick="manualRefresh()" id="refreshBtn" title="刷新收件箱 (每8秒自动同步)">
            <svg id="refreshIcon" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M23 4v6h-6M1 20v-6h6M3.51 9a9 9 0 0114.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0020.49 15"/></svg>
          </button>
        </div>
        <div class="mailbox-info" onclick="copyAddr()" title="点击复制完整邮箱地址">
          <span class="mailbox-email" id="mailboxAddr"></span>
          <span class="mailbox-dot">·</span>
          <span class="mailbox-count" id="msgCount">0</span>
          <svg class="copy-hint-icon" width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/></svg>
        </div>
      </div>
      <div class="mail-list" id="msgList">
        <div class="empty-list">输入邮箱地址查看邮件</div>
      </div>
    </aside>

    <section class="mail-detail" id="detail">
      <div class="detail-placeholder">选择左侧邮件以阅读</div>
    </section>
  </main>
</div>

<div id="toast"></div>

<script>
const $ = id => document.getElementById(id);
const DEFAULT_DOMAIN = "__DEFAULT_DOMAIN__";
function resolveMailbox(input){
  let val = String(input || '').trim().toLowerCase();
  if(!val) return '';
  if(!val.includes('@')){
    const dom = (DEFAULT_DOMAIN && !DEFAULT_DOMAIN.startsWith('__')) ? DEFAULT_DOMAIN : 'example.com';
    val = `${val}@${dom}`;
  }
  return val;
}
let currentMailbox = '', currentMsgId = null, pollTimer = null;
let currentMessageData = null;
let headerDetailsExpanded = false;
let linksPopoverOpen = false;

function esc(s){
  return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function showToast(msg){
  const t = $('toast');
  t.textContent = msg;
  t.classList.add('show');
  clearTimeout(t._tid);
  t._tid = setTimeout(() => t.classList.remove('show'), 2000);
}

function fmtTime(iso){
  if(!iso) return '';
  const d = new Date(iso), now = new Date(), diff = (now - d) / 1000;
  if(diff < 55) return '刚刚';
  if(diff < 3600) return `${Math.floor(diff/60)}分钟前`;
  if(diff < 86400 && d.getDate() === now.getDate()) {
    return d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', hour12: false });
  }
  if(d.getFullYear() === now.getFullYear()) {
    return `${d.getMonth() + 1}月${d.getDate()}日`;
  }
  return `${d.getFullYear()}/${d.getMonth() + 1}/${d.getDate()}`;
}

function fmtTimeFull(iso){
  if(!iso) return '';
  return new Date(iso).toLocaleString('zh-CN', {
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
    hour12: false
  });
}

function formatSender(raw){
  raw = String(raw || '').trim();
  const match = raw.match(/^([^<]+)<.*>$/);
  if (match && match[1].trim()) {
    return match[1].trim().replace(/^["']|["']$/g, '');
  }
  if (raw.includes('@')) {
    return raw.split('@')[0];
  }
  return raw || '未知发件人';
}

function extractEmail(raw){
  raw = String(raw || '').trim();
  const match = raw.match(/<([^>]+)>/);
  if (match && match[1]) return match[1];
  return raw;
}

function getDomain(url){
  try { return new URL(url).hostname; } catch{ return url.slice(0, 32); }
}

function getPath(url){
  try {
    const p = new URL(url).pathname;
    return p.length > 1 ? (p.length > 28 ? p.slice(0, 25) + '…' : p) : '';
  } catch{ return ''; }
}

function extractLinks(html){
  const links = [], seen = new Set();
  const re = /href=["'](https?:\/\/[^"'\s>]+)["']/gi;
  let m;
  while((m = re.exec(html)) !== null){
    const u = m[1];
    if(!seen.has(u)){ seen.add(u); links.push(u); }
  }
  return links.filter(u => !u.match(/spacer\.gif|1x1|pixel|tracking|unsubscribe/i));
}

function htmlToText(html){
  if (!html) return '';
  let s = html
    .replace(/<style[\s\S]*?<\/style>/gi, '')
    .replace(/<script[\s\S]*?<\/script>/gi, '');
  s = s.replace(/<(?:br|hr)\s*\/?>/gi, '\n')
       .replace(/<\/?(?:p|div|tr|h[1-6]|table|blockquote|section|article)\b[^>]*>/gi, '\n')
       .replace(/<li\b[^>]*>/gi, '\n• ')
       .replace(/<\/li>/gi, '\n')
       .replace(/<td\b[^>]*>/gi, ' ')
       .replace(/<\/td>/gi, ' ');
  s = s.replace(/<[^>]+>/g, '');
  s = s.replace(/&nbsp;/g, ' ')
       .replace(/&amp;/g, '&')
       .replace(/&lt;/g, '<')
       .replace(/&gt;/g, '>')
       .replace(/&quot;/g, '"')
       .replace(/&#39;/g, "'")
       .replace(/&#(\d+);/g, (_, n) => String.fromCharCode(n))
       .replace(/&#x([0-9a-fA-F]+);/g, (_, n) => String.fromCharCode(parseInt(n, 16)));

  const rawLines = s.split('\n');
  const lines = [];
  for (let i = 0; i < rawLines.length; i++) {
    const line = rawLines[i].replace(/[ \t\u00a0\u3000]+/g, ' ').trim();
    lines.push(line);
  }

  const cleaned = [];
  let prevEmpty = true;
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (!line) {
      if (!prevEmpty) {
        cleaned.push('');
        prevEmpty = true;
      }
    } else {
      cleaned.push(line);
      prevEmpty = false;
    }
  }

  while (cleaned.length && !cleaned[cleaned.length - 1]) {
    cleaned.pop();
  }

  return cleaned.join('\n');
}

function prepareEmailHtml(rawHtml){
  let html = String(rawHtml || '').trim();
  const baseTag = '<base target="_blank">';
  const helperStyle = `<style>
    html, body {
      margin: 0 !important;
      padding: 0 !important;
      width: 100% !important;
      min-height: 100% !important;
      overflow-y: auto !important;
      overflow-x: hidden !important;
      -webkit-overflow-scrolling: touch;
      background-color: #ffffff;
    }
    img { max-width: 100% !important; height: auto; }
    a { cursor: pointer; }
    ::-webkit-scrollbar { width: 7px; height: 7px; }
    ::-webkit-scrollbar-track { background: transparent; }
    ::-webkit-scrollbar-thumb { background: rgba(0,0,0,0.18); border-radius: 4px; }
    ::-webkit-scrollbar-thumb:hover { background: rgba(0,0,0,0.35); }
  </style>`;

  if(/<head\b[^>]*>/i.test(html)){
    return html.replace(/<head\b[^>]*>/i, `$&${baseTag}${helperStyle}`);
  } else if(/<html\b[^>]*>/i.test(html)){
    return html.replace(/<html\b[^>]*>/i, `$&<head>${baseTag}${helperStyle}</head>`);
  } else {
    return `<!DOCTYPE html><html><head><meta charset="utf-8">${baseTag}${helperStyle}<style>body{padding:24px;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;font-size:14px;line-height:1.6;color:#222;background:#fff;}</style></head><body>${html}</body></html>`;
  }
}

async function loadInbox(quiet = false){
  const rawInput = $('emailInput').value.trim();
  if(!rawInput){ if(!quiet) showToast('请输入邮箱地址或前缀'); return; }
  const email = resolveMailbox(rawInput);
  $('emailInput').value = email;
  currentMailbox = email;
  $('mailboxAddr').textContent = currentMailbox;
  $('sidebarHead').style.display = 'block';

  try{
    const r = await fetch(`/api/ui/messages?mailbox=${encodeURIComponent(currentMailbox)}`);
    const data = await r.json();
    if(data.error){ if(!quiet) showToast(data.error); return; }
    renderList(data.messages || []);
    history.replaceState({}, '', `/?email=${encodeURIComponent(currentMailbox)}`);
    startPoll();
  } catch(e){
    if(!quiet) showToast('请求失败');
  }
}

function manualRefresh(){
  const btn = $('refreshBtn');
  btn.classList.add('spinning');
  setTimeout(() => btn.classList.remove('spinning'), 600);
  loadInbox(false);
}

function renderList(msgs){
  $('msgCount').textContent = msgs.length;
  const list = $('msgList');
  if(!msgs.length){
    list.innerHTML = '<div class="empty-list">暂无邮件</div>';
    return;
  }
  list.innerHTML = msgs.map((m, idx) => `
    <div class="mail-item ${m.id === currentMsgId ? 'active' : ''} ${idx === 0 ? 'latest' : ''}" data-id="${m.id}" onclick="loadDetail(${m.id})">
      <div class="mail-item-top">
        <span class="mail-item-subject" title="${esc(m.subject || '（无主题）')}">${esc(m.subject || '（无主题）')}</span>
        <span class="mail-item-time">${fmtTime(m.received_at)}</span>
      </div>
      <div class="mail-item-bottom">
        <span class="mail-item-sender">${esc(formatSender(m.sender))}</span>
        ${m.code ? `<span class="mail-item-code">${esc(m.code)}</span>` : ''}
      </div>
    </div>
  `).join('');
}

async function loadDetail(id){
  currentMsgId = id;
  document.querySelectorAll('.mail-item').forEach(el => el.classList.remove('active'));
  const el = document.querySelector(`.mail-item[data-id="${id}"]`);
  if(el) el.classList.add('active');

  $('detail').innerHTML = '<div class="detail-placeholder">加载中…</div>';

  try{
    const r = await fetch(`/api/ui/message/${id}`);
    const m = await r.json();
    if(m.error){ showToast(m.error); return; }
    currentMessageData = m;
    headerDetailsExpanded = false;
    linksPopoverOpen = false;
    renderDetail(m);
  } catch(e){
    showToast('加载失败');
  }
}

function renderDetail(m){
  const isHTML = /<html|<body|<div|<table/i.test(m.body_text || '');
  const allLinks = extractLinks(m.body_text || '');
  const plainText = htmlToText(m.body_text || '');
  const fullDate = fmtTimeFull(m.received_at);
  const briefDate = fmtTime(m.received_at);

  $('detail').innerHTML = `
    <div class="mail-header-section">
      <div class="mail-subject-row">
        <h1 class="mail-subject">${esc(m.subject || '（无主题）')}</h1>
        <span class="mail-date-brief" title="${esc(fullDate)}">${esc(briefDate)}</span>
      </div>
      <div class="mail-meta-line">
        <div class="sender-group">
          <span class="sender-display-name">${esc(formatSender(m.sender))}</span>
          <span class="sender-recipient-summary" onclick="toggleHeaderDetails()" title="展开收发详情">
            <span>${esc(extractEmail(m.sender))} → ${esc(m.mailbox)}</span>
            <svg class="chevron-icon" id="detailChevron" width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M6 9l6 6 6-6"/></svg>
          </span>
        </div>
        ${m.code ? `
        <div class="header-code-pill" onclick="copyCode(this, '${esc(m.code)}')" title="点击一键复制验证码">
          <span class="code-pill-label">验证码</span>
          <span class="code-pill-val">${esc(m.code)}</span>
          <span class="code-pill-btn">复制</span>
        </div>` : ''}
      </div>
      <div id="expandedDetails" class="header-expanded-details" style="display:none">
        <div class="detail-grid">
          <span class="dt-lbl">发件人：</span><span class="dt-val">${esc(m.sender)}</span>
          <span class="dt-lbl">收件人：</span><span class="dt-val">${esc(m.mailbox)}</span>
          <span class="dt-lbl">时　间：</span><span class="dt-val">${esc(fullDate)}</span>
        </div>
      </div>
    </div>

    <div class="reader-toolbar">
      <div class="view-tabs">
        ${isHTML ? `<button class="tab-btn active" onclick="switchTab('html', this)">HTML 渲染</button>` : ''}
        <button class="tab-btn ${!isHTML ? 'active' : ''}" onclick="switchTab('text', this)">纯文本</button>
      </div>
      <div class="toolbar-actions">
        ${allLinks.length ? `
        <div class="links-popover-wrap">
          <button class="toolbar-link-btn" onclick="toggleLinksPopover(event)" id="linksBtn">
            <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 13v6a2 2 0 01-2 2H5a2 2 0 01-2-2V8a2 2 0 012-2h6M15 3h6v6M10 14L21 3"/></svg>
            <span>${allLinks.length} 个外部链接</span>
            <svg class="chevron-icon" id="linksChevron" width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M6 9l6 6 6-6"/></svg>
          </button>
          <div id="linksDropdown" class="links-dropdown" style="display:none">
            <div class="dropdown-header">外部链接 (${allLinks.length})</div>
            <div class="dropdown-list">
              ${allLinks.map(u => `
                <a href="${esc(u)}" target="_blank" rel="noopener noreferrer" class="dropdown-item">
                  <div class="dropdown-item-info">
                    <div class="dropdown-item-domain">${esc(getDomain(u))}</div>
                    <div class="dropdown-item-path">${esc(getPath(u) || '/')}</div>
                  </div>
                  <svg class="dropdown-item-arrow" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M7 17l9.2-9.2M17 17V8H8"/></svg>
                </a>
              `).join('')}
            </div>
          </div>
        </div>` : ''}
      </div>
    </div>

    <div class="reader-canvas" id="readerCanvas">
      <div class="email-paper">
        ${isHTML ? `<iframe id="bodyFrame" class="email-iframe" sandbox="allow-popups allow-popups-to-escape-sandbox allow-same-origin"></iframe>` : ''}
        <div id="bodyText" class="email-text-view" style="${isHTML ? 'display:none' : ''}">${esc(plainText)}</div>
      </div>
    </div>
  `;

  if(isHTML){
    const f = $('bodyFrame');
    if(f){
      f.contentDocument.open();
      f.contentDocument.write(prepareEmailHtml(m.body_text));
      f.contentDocument.close();
    }
  }
}

function toggleHeaderDetails(){
  headerDetailsExpanded = !headerDetailsExpanded;
  const el = $('expandedDetails');
  const ch = $('detailChevron');
  if(el){ el.style.display = headerDetailsExpanded ? 'block' : 'none'; }
  if(ch){ ch.classList.toggle('open', headerDetailsExpanded); }
}

function toggleLinksPopover(e){
  if(e) e.stopPropagation();
  linksPopoverOpen = !linksPopoverOpen;
  const menu = $('linksDropdown');
  const btn = $('linksBtn');
  const ch = $('linksChevron');
  if(menu){ menu.style.display = linksPopoverOpen ? 'flex' : 'none'; }
  if(btn){ btn.classList.toggle('active', linksPopoverOpen); }
  if(ch){ ch.classList.toggle('open', linksPopoverOpen); }
}

document.addEventListener('click', e => {
  if(linksPopoverOpen){
    const wrap = document.querySelector('.links-popover-wrap');
    if(wrap && !wrap.contains(e.target)){
      linksPopoverOpen = false;
      const menu = $('linksDropdown');
      const btn = $('linksBtn');
      const ch = $('linksChevron');
      if(menu) menu.style.display = 'none';
      if(btn) btn.classList.remove('active');
      if(ch) ch.classList.remove('open');
    }
  }
});

function switchTab(type, btn){
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  const frame = $('bodyFrame');
  const text = $('bodyText');
  if(type === 'html'){
    if(frame) frame.style.display = 'block';
    if(text) text.style.display = 'none';
  } else {
    if(frame) frame.style.display = 'none';
    if(text) text.style.display = 'block';
  }
}

function copyText(t, msg){
  navigator.clipboard.writeText(t).then(() => showToast(msg || '已复制'));
}

function copyCode(btn, code){
  copyText(code, '已复制验证码');
  const actionEl = btn ? btn.querySelector('.code-pill-btn') : null;
  if(actionEl && !actionEl._busy){
    actionEl._busy = true;
    const oldText = actionEl.textContent;
    actionEl.textContent = '已复制 ✓';
    setTimeout(() => {
      actionEl.textContent = oldText;
      actionEl._busy = false;
    }, 1200);
  }
}

function copyAddr(){
  if(currentMailbox) copyText(currentMailbox, '已复制邮箱地址');
}

function startPoll(){
  if(pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(() => {
    loadInbox(true);
  }, 8000);
}

// Search Enter Key
$('emailInput').addEventListener('keydown', e => {
  if(e.key === 'Enter') loadInbox();
});

// URL Param Auto Load
const urlEmail = new URLSearchParams(location.search).get('email');
if(urlEmail){
  const email = resolveMailbox(urlEmail);
  $('emailInput').value = email;
  loadInbox();
}

// Theme Handlers
function applyTheme(t){
  const isLight = t === 'light';
  document.documentElement.classList.toggle('light', isLight);
  const icon = $('themeIcon');
  if(icon){
    if(isLight){
      // 月亮图标
      icon.innerHTML = '<path d="M21 12.79A9 9 0 1111.21 3 7 7 0 0021 12.79z"/>';
    } else {
      // 太阳图标
      icon.innerHTML = '<circle cx="12" cy="12" r="5"/><path d="M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42"/>';
    }
  }
}

function toggleTheme(){
  const next = document.documentElement.classList.contains('light') ? 'dark' : 'light';
  localStorage.setItem('theme', next);
  applyTheme(next);
}

applyTheme(localStorage.getItem('theme') || 'dark');
</script>
</body>
</html>

"""


async def ui_index_handler(request: "web.Request"):
    from aiohttp import web
    config: ServiceConfig = request.app["config"]
    page = WEB_UI_HTML.replace("__DEFAULT_DOMAIN__", config.default_domain)
    return web.Response(text=page, content_type="text/html", charset="utf-8")


async def ui_messages_handler(request: "web.Request"):
    from aiohttp import web
    config: ServiceConfig = request.app["config"]
    store: MessageStore = request.app["store"]
    mailbox = normalize_mailbox(str(request.query.get("mailbox", "")))
    if not mailbox:
        return web.json_response({"error": "mailbox required"}, status=400)
    if "@" not in mailbox:
        mailbox = f"{mailbox}@{config.default_domain}"
    msgs = store.list_mailbox_messages(mailbox, limit=50)
    return web.json_response({"mailbox": mailbox, "messages": msgs})


async def ui_message_detail_handler(request: "web.Request"):
    from aiohttp import web
    store: MessageStore = request.app["store"]
    try:
        msg_id = int(request.match_info["id"])
    except (KeyError, ValueError):
        return web.json_response({"error": "invalid id"}, status=400)
    msg = store.get_message_by_id(msg_id)
    if not msg:
        return web.json_response({"error": "not found"}, status=404)
    return web.json_response(msg)


def build_web_app(config: ServiceConfig, store: MessageStore):
    from aiohttp import web

    app = web.Application()
    app["config"] = config
    app["store"] = store
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
    return app


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="SMTP 接码服务（SMTP + HTTP）")
    parser.add_argument("--domain", required=True, help="接收域名，多个用英文逗号分隔")
    parser.add_argument("--api-token", default="", help="HTTP API token（为空时读取环境变量 API_TOKEN）")
    parser.add_argument("--db-path", default="/var/lib/smtp-code-service/messages.db", help="SQLite 路径")
    parser.add_argument("--smtp-host", default="0.0.0.0", help="SMTP 监听地址")
    parser.add_argument("--smtp-port", type=int, default=25, help="SMTP 监听端口")
    parser.add_argument("--http-host", default="0.0.0.0", help="HTTP 监听地址")
    parser.add_argument("--http-port", type=int, default=8081, help="HTTP 监听端口")
    return parser.parse_args()


def main() -> int:
    from aiosmtpd.controller import Controller
    from aiohttp import web

    args = parse_args()
    api_token = str(args.api_token or "").strip() or str(os.environ.get("API_TOKEN", "")).strip()
    config = ServiceConfig(domains=parse_domains(args.domain), api_token=api_token, db_path=Path(args.db_path))
    if not config.domains or not config.api_token:
        raise RuntimeError("domain/api-token 不能为空")
    store = MessageStore(config.db_path)
    store.init()
    smtp = Controller(SMTPHandler(config, store), hostname=args.smtp_host, port=args.smtp_port)
    smtp.start()
    app = build_web_app(config, store)
    try:
        web.run_app(app, host=args.http_host, port=args.http_port, print=None)
    finally:
        smtp.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
