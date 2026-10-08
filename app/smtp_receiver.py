from __future__ import annotations

from typing import TYPE_CHECKING
from aiosmtpd.controller import Controller

from app.mail_parser import (
    extract_code,
    extract_sender,
    extract_subject,
    extract_text_from_message,
    normalize_mailbox,
    utc_now_iso,
)

if TYPE_CHECKING:
    from app.config import ServiceConfig
    from app.storage import MessageStore


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


def create_smtp_controller(config: ServiceConfig, store: MessageStore) -> Controller:
    handler = SMTPHandler(config, store)
    return Controller(handler, hostname=config.smtp_host, port=config.smtp_port)
