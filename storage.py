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

    def _has_column(self, table: str, col: str) -> bool:
        cur = self._conn.cursor()
        rows = cur.execute(f"PRAGMA table_info({table})").fetchall()
        return any(r["name"] == col for r in rows)

    def _try_add_column(self, table: str, ddl: str) -> None:
        """
        SQLite has no 'ADD COLUMN IF NOT EXISTS', so we either:
        - check PRAGMA table_info first, or
        - run ALTER TABLE and ignore duplicate-column errors.
        :contentReference[oaicite:1]{index=1}
        """
        cur = self._conn.cursor()
        try:
            cur.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")
            self._conn.commit()
        except sqlite3.OperationalError as e:
            # If column already exists or table is in old format, ignore duplicates safely.
            if "duplicate column name" in str(e).lower():
                return
            raise

    def _init_db(self) -> None:
        cur = self._conn.cursor()

        # Create table if fresh install
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

        # Migrate old installs (your case)
        # Add any missing columns one-by-one
        if not self._has_column("chats", "approved"):
            self._try_add_column("chats", "approved INTEGER DEFAULT 0")
        if not self._has_column("chats", "enabled"):
            self._try_add_column("chats", "enabled INTEGER DEFAULT 0")
        if not self._has_column("chats", "config_json"):
            self._try_add_column("chats", "config_json TEXT DEFAULT '{}'")
        if not self._has_column("chats", "last_snapshot_json"):
            self._try_add_column("chats", "last_snapshot_json TEXT DEFAULT NULL")
        if not self._has_column("chats", "updated_at"):
            self._try_add_column("chats", "updated_at INTEGER")

        # Normalize NULLs from old rows
        now = int(time.time())
        cur.execute("UPDATE chats SET config_json='{}' WHERE config_json IS NULL")
        cur.execute("UPDATE chats SET approved=0 WHERE approved IS NULL")
        cur.execute("UPDATE chats SET enabled=0 WHERE enabled IS NULL")
        cur.execute("UPDATE chats SET updated_at=? WHERE updated_at IS NULL", (now,))
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
            cfg_raw = r["config_json"] if "config_json" in r.keys() else "{}"
            out.append(
                ChatRow(
                    chat_id=int(r["chat_id"]),
                    title=r["title"] or str(r["chat_id"]),
                    chat_type=r["chat_type"] or "",
                    approved=bool(r["approved"]) if "approved" in r.keys() else False,
                    enabled=bool(r["enabled"]) if "enabled" in r.keys() else False,
                    config=json.loads(cfg_raw or "{}"),
                )
            )
        return out

    def get_chat(self, chat_id: int) -> Optional[ChatRow]:
        cur = self._conn.cursor()
        r = cur.execute("SELECT * FROM chats WHERE chat_id=?", (chat_id,)).fetchone()
        if not r:
            return None
        cfg_raw = r["config_json"] if "config_json" in r.keys() else "{}"
        return ChatRow(
            chat_id=int(r["chat_id"]),
            title=r["title"] or str(r["chat_id"]),
            chat_type=r["chat_type"] or "",
            approved=bool(r["approved"]) if "approved" in r.keys() else False,
            enabled=bool(r["enabled"]) if "enabled" in r.keys() else False,
            config=json.loads(cfg_raw or "{}"),
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
