import json
import sqlite3
import time
from typing import Any, Dict, List, Optional


def _now() -> int:
    return int(time.time())


class Storage:
    def __init__(self, path: str) -> None:
        self.path = path
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path)
        con.row_factory = sqlite3.Row
        return con

    def _init_db(self) -> None:
        with self._conn() as con:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS chats (
                    chat_id     INTEGER PRIMARY KEY,
                    title       TEXT,
                    type        TEXT,
                    approved    INTEGER DEFAULT 0,
                    config_json TEXT DEFAULT '{}',
                    state_json  TEXT DEFAULT '{}',
                    created_at  INTEGER,
                    updated_at  INTEGER
                )
                """
            )
            con.commit()

        # Migrations for old installs
        with self._conn() as con:
            cols = {r["name"] for r in con.execute("PRAGMA table_info(chats)").fetchall()}
            if "approved" not in cols:
                con.execute("ALTER TABLE chats ADD COLUMN approved INTEGER DEFAULT 0")
            if "config_json" not in cols:
                con.execute("ALTER TABLE chats ADD COLUMN config_json TEXT DEFAULT '{}'")
            if "state_json" not in cols:
                con.execute("ALTER TABLE chats ADD COLUMN state_json TEXT DEFAULT '{}'")
            if "created_at" not in cols:
                con.execute("ALTER TABLE chats ADD COLUMN created_at INTEGER")
            if "updated_at" not in cols:
                con.execute("ALTER TABLE chats ADD COLUMN updated_at INTEGER")
            con.commit()

    def upsert_chat(self, chat_id: int, title: str, chat_type: str) -> None:
        now = _now()
        with self._conn() as con:
            con.execute(
                """
                INSERT INTO chats(chat_id, title, type, approved, config_json, state_json, created_at, updated_at)
                VALUES (?, ?, ?, 0, '{}', '{}', ?, ?)
                ON CONFLICT(chat_id) DO UPDATE SET
                    title=excluded.title,
                    type=excluded.type,
                    updated_at=excluded.updated_at
                """,
                (chat_id, title, chat_type, now, now),
            )
            con.commit()

    def delete_chat(self, chat_id: int) -> None:
        with self._conn() as con:
            con.execute("DELETE FROM chats WHERE chat_id=?", (chat_id,))
            con.commit()

    def list_chats(self) -> List[Dict[str, Any]]:
        with self._conn() as con:
            rows = con.execute("SELECT * FROM chats ORDER BY updated_at DESC").fetchall()
        out: List[Dict[str, Any]] = []
        for r in rows:
            out.append(
                {
                    "chat_id": int(r["chat_id"]),
                    "title": r["title"] or str(r["chat_id"]),
                    "type": r["type"] or "",
                    "approved": bool(int(r["approved"] or 0)),
                    "config": json.loads(r["config_json"] or "{}"),
                    "state": json.loads(r["state_json"] or "{}"),
                }
            )
        return out

    def get_chat(self, chat_id: int) -> Optional[Dict[str, Any]]:
        with self._conn() as con:
            r = con.execute("SELECT * FROM chats WHERE chat_id=?", (chat_id,)).fetchone()
        if not r:
            return None
        return {
            "chat_id": int(r["chat_id"]),
            "title": r["title"] or str(r["chat_id"]),
            "type": r["type"] or "",
            "approved": bool(int(r["approved"] or 0)),
            "config": json.loads(r["config_json"] or "{}"),
            "state": json.loads(r["state_json"] or "{}"),
        }

    def set_approved(self, chat_id: int, approved: bool) -> None:
        with self._conn() as con:
            con.execute("UPDATE chats SET approved=?, updated_at=? WHERE chat_id=?", (1 if approved else 0, _now(), chat_id))
            con.commit()

    def save_config(self, chat_id: int, cfg: Dict[str, Any]) -> None:
        with self._conn() as con:
            con.execute("UPDATE chats SET config_json=?, updated_at=? WHERE chat_id=?", (json.dumps(cfg, ensure_ascii=False), _now(), chat_id))
            con.commit()

    def save_state(self, chat_id: int, st: Dict[str, Any]) -> None:
        with self._conn() as con:
            con.execute("UPDATE chats SET state_json=?, updated_at=? WHERE chat_id=?", (json.dumps(st, ensure_ascii=False), _now(), chat_id))
            con.commit()
