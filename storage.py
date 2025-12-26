import json
import os
import sqlite3
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class ChatRow:
    chat_id: int
    title: str
    chat_type: str
    approved: bool
    enabled: bool
    config: Dict[str, Any]


class Storage:
    def __init__(self, path: str):
        self.path = path
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self) -> None:
        cur = self._conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS chats (
              chat_id INTEGER PRIMARY KEY,
              title TEXT,
              chat_type TEXT,
              approved INTEGER DEFAULT 0,
              enabled INTEGER DEFAULT 0,
              config_json TEXT DEFAULT '{}',
              last_snapshot_json TEXT DEFAULT NULL,
              updated_at INTEGER
            )
            """
        )
        self._conn.commit()

    def upsert_chat(self, chat_id: int, title: str, chat_type: str) -> None:
        now = int(time.time())
        cur = self._conn.cursor()
        cur.execute(
            """
            INSERT INTO chats (chat_id, title, chat_type, approved, enabled, config_json, updated_at)
            VALUES (?, ?, ?, 0, 0, '{}', ?)
            ON CONFLICT(chat_id) DO UPDATE SET
              title=excluded.title,
              chat_type=excluded.chat_type,
              updated_at=excluded.updated_at
            """,
            (chat_id, title, chat_type, now),
        )
        self._conn.commit()

    def list_chats(self) -> List[ChatRow]:
        cur = self._conn.cursor()
        rows = cur.execute("SELECT * FROM chats ORDER BY updated_at DESC").fetchall()
        out: List[ChatRow] = []
        for r in rows:
            out.append(
                ChatRow(
                    chat_id=int(r["chat_id"]),
                    title=r["title"] or str(r["chat_id"]),
                    chat_type=r["chat_type"] or "",
                    approved=bool(r["approved"]),
                    enabled=bool(r["enabled"]),
                    config=json.loads(r["config_json"] or "{}"),
                )
            )
        return out

    def get_chat(self, chat_id: int) -> Optional[ChatRow]:
        cur = self._conn.cursor()
        r = cur.execute("SELECT * FROM chats WHERE chat_id=?", (chat_id,)).fetchone()
        if not r:
            return None
        return ChatRow(
            chat_id=int(r["chat_id"]),
            title=r["title"] or str(r["chat_id"]),
            chat_type=r["chat_type"] or "",
            approved=bool(r["approved"]),
            enabled=bool(r["enabled"]),
            config=json.loads(r["config_json"] or "{}"),
        )

    def set_approved(self, chat_id: int, approved: bool) -> None:
        cur = self._conn.cursor()
        cur.execute(
            "UPDATE chats SET approved=?, updated_at=? WHERE chat_id=?",
            (1 if approved else 0, int(time.time()), chat_id),
        )
        self._conn.commit()

    def set_enabled(self, chat_id: int, enabled: bool) -> None:
        cur = self._conn.cursor()
        cur.execute(
            "UPDATE chats SET enabled=?, updated_at=? WHERE chat_id=?",
            (1 if enabled else 0, int(time.time()), chat_id),
        )
        self._conn.commit()

    def set_config(self, chat_id: int, cfg: Dict[str, Any]) -> None:
        cur = self._conn.cursor()
        cur.execute(
            "UPDATE chats SET config_json=?, updated_at=? WHERE chat_id=?",
            (json.dumps(cfg, ensure_ascii=False), int(time.time()), chat_id),
        )
        self._conn.commit()
