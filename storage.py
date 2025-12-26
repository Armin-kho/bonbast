import json
import sqlite3
import time
from typing import Any, Dict, List, Optional


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())


class Storage:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        return con

    def _table_columns(self, con: sqlite3.Connection, table: str) -> List[str]:
        cur = con.execute(f"PRAGMA table_info({table})")
        return [r["name"] for r in cur.fetchall()]

    def _init_schema(self) -> None:
        with self._connect() as con:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS chats (
                  chat_id     INTEGER PRIMARY KEY,
                  title       TEXT,
                  chat_type   TEXT,
                  approved    INTEGER DEFAULT 0,
                  config_json TEXT DEFAULT '{}',
                  state_json  TEXT DEFAULT '{}',
                  created_at  TEXT,
                  updated_at  TEXT
                )
                """
            )
            cols = set(self._table_columns(con, "chats"))

            # Migrations (so old DBs won't break)
            if "approved" not in cols:
                con.execute("ALTER TABLE chats ADD COLUMN approved INTEGER DEFAULT 0")
            if "config_json" not in cols:
                con.execute("ALTER TABLE chats ADD COLUMN config_json TEXT DEFAULT '{}'")
            if "state_json" not in cols:
                con.execute("ALTER TABLE chats ADD COLUMN state_json TEXT DEFAULT '{}'")
            if "created_at" not in cols:
                con.execute("ALTER TABLE chats ADD COLUMN created_at TEXT")
            if "updated_at" not in cols:
                con.execute("ALTER TABLE chats ADD COLUMN updated_at TEXT")

            con.commit()

    def upsert_chat(self, chat_id: int, title: str, chat_type: str) -> None:
        now = _now()
        with self._connect() as con:
            con.execute(
                """
                INSERT INTO chats(chat_id, title, chat_type, approved, config_json, state_json, created_at, updated_at)
                VALUES (?, ?, ?, 0, '{}', '{}', ?, ?)
                ON CONFLICT(chat_id) DO UPDATE SET
                  title=excluded.title,
                  chat_type=excluded.chat_type,
                  updated_at=excluded.updated_at
                """,
                (chat_id, title, chat_type, now, now),
            )
            con.commit()

    def delete_chat(self, chat_id: int) -> None:
        with self._connect() as con:
            con.execute("DELETE FROM chats WHERE chat_id=?", (chat_id,))
            con.commit()

    def list_chats(self) -> List[Dict[str, Any]]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT chat_id, title, chat_type, approved, config_json, state_json, created_at, updated_at FROM chats ORDER BY updated_at DESC"
            ).fetchall()
        out: List[Dict[str, Any]] = []
        for r in rows:
            out.append(
                {
                    "chat_id": r["chat_id"],
                    "title": r["title"] or str(r["chat_id"]),
                    "chat_type": r["chat_type"] or "",
                    "approved": bool(r["approved"]),
                    "config": json.loads(r["config_json"] or "{}"),
                    "state": json.loads(r["state_json"] or "{}"),
                }
            )
        return out

    def get_chat(self, chat_id: int) -> Optional[Dict[str, Any]]:
        with self._connect() as con:
            r = con.execute(
                "SELECT chat_id, title, chat_type, approved, config_json, state_json FROM chats WHERE chat_id=?",
                (chat_id,),
            ).fetchone()
        if not r:
            return None
        return {
            "chat_id": r["chat_id"],
            "title": r["title"] or str(r["chat_id"]),
            "chat_type": r["chat_type"] or "",
            "approved": bool(r["approved"]),
            "config": json.loads(r["config_json"] or "{}"),
            "state": json.loads(r["state_json"] or "{}"),
        }

    def set_approved(self, chat_id: int, approved: bool) -> None:
        with self._connect() as con:
            con.execute(
                "UPDATE chats SET approved=?, updated_at=? WHERE chat_id=?",
                (1 if approved else 0, _now(), chat_id),
            )
            con.commit()

    def save_config(self, chat_id: int, config: Dict[str, Any]) -> None:
        with self._connect() as con:
            con.execute(
                "UPDATE chats SET config_json=?, updated_at=? WHERE chat_id=?",
                (json.dumps(config, ensure_ascii=False), _now(), chat_id),
            )
            con.commit()

    def save_state(self, chat_id: int, state: Dict[str, Any]) -> None:
        with self._connect() as con:
            con.execute(
                "UPDATE chats SET state_json=?, updated_at=? WHERE chat_id=?",
                (json.dumps(state, ensure_ascii=False), _now(), chat_id),
            )
            con.commit()
