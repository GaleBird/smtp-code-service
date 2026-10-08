from __future__ import annotations

import sqlite3
import threading
from pathlib import Path


class MessageStore:
    def __init__(self, db_path: Path):
        self._db_path = Path(db_path)
        self._lock = threading.Lock()

    def init(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
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

    def insert(
        self,
        *,
        mailbox: str,
        code: str,
        subject: str,
        sender: str,
        body_text: str,
        received_at: str,
    ) -> int:
        with self._lock, sqlite3.connect(self._db_path) as conn:
            cursor = conn.execute(
                """
                INSERT INTO messages(mailbox, code, subject, sender, body_text, received_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (mailbox, code, subject, sender, body_text, received_at),
            )
            conn.commit()
            return cursor.lastrowid or 0

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
            "id": row[0],
            "mailbox": row[1],
            "subject": row[2],
            "sender": row[3],
            "code": row[4],
            "body_text": row[5],
            "received_at": row[6],
        }
