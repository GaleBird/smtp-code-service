from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from email import policy
from email.parser import BytesParser
from email.utils import parseaddr

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
