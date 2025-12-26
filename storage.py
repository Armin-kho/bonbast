import json
import os
import sqlite3
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple


def _utc_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


class Storage:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._lock = threading.RLock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.db_path, timeout=30)
        con.row_factory = sqlite3.Row
        return con

    def _table_exists(self, con: sqlite3.Connection, name: str) -> bool:
        row = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (name,),
        ).fetchone()
        return row is not None

    def _get_cols(self, con: sqlite3.Connection, table: str) -> List[str]:
        rows = con.execute(f"PRAGMA table_info({table})").fetchall()
        return [r["name"] for r in rows]

    def _create_chats_table(self, con: sqlite3.Connection, name: str = "chats") -> None:
        con.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {name} (
                chat_id     INTEGER PRIMARY KEY,
                title       TEXT    DEFAULT '',
                type        TEXT    DEFAULT '',
                approved    INTEGER DEFAULT 0,
                config_json TEXT    DEFAULT '{{}}',
                state_json  TEXT    DEFAULT '{{}}',
                created_at  TEXT    DEFAULT '',
                updated_at  TEXT    DEFAULT ''
            );
            """
        )

    def _rebuild_chats_table(self, con: sqlite3.Connection) -> None:
        """
        Rebuild chats table into the expected schema, copying over whatever columns exist.
        This avoids the "no such column" crashes after upgrades.
        SQLite's recommended safe approach for complex schema changes is rebuild+copy. :contentReference[oaicite:1]{index=1}
        """
        old_cols = set(self._get_cols(con, "chats"))

        self._create_chats_table(con, name="chats_new")

        # Map old column names to new schema fields
        # Support older variants if you had them
        title_expr = "title" if "title" in old_cols else "''"
        type_expr = (
            "type" if "type" in old_cols else
            "chat_type" if "chat_type" in old_cols else
            "''"
        )
        approved_expr = "approved" if "approved" in old_cols else "0"
        config_expr = (
            "config_json" if "config_json" in old_cols else
            "config" if "config" in old_cols else
            "'{}'"
        )
        state_expr = (
            "state_json" if "state_json" in old_cols else
            "state" if "state" in old_cols else
            "'{}'"
        )
        created_expr = "created_at" if "created_at" in old_cols else "''"
        updated_expr = "updated_at" if "updated_at" in old_cols else "''"

        # chat_id must exist in any sane schema; if it doesn't, we rebuild empty.
        if "chat_id" in old_cols:
            con.execute(
                f"""
                INSERT INTO chats_new (chat_id, title, type, approved, config_json, state_json, created_at, updated_at)
                SELECT
                    chat_id,
                    {title_expr},
                    {type_expr},
                    {approved_expr},
                    {config_expr},
                    {state_expr},
                    {created_expr},
                    {updated_expr}
                FROM chats
                """
            )

        con.execute("DROP TABLE chats")
        con.execute("ALTER TABLE chats_new RENAME TO chats")

        # Normalize JSON columns if null
        con.execute("UPDATE chats SET config_json='{}' WHERE config_json IS NULL OR config_json=''")
        con.execute("UPDATE chats SET state_json='{}' WHERE state_json IS NULL OR state_json=''")
        con.execute("UPDATE chats SET created_at=? WHERE created_at IS NULL OR created_at=''", (_utc_iso(),))
        con.execute("UPDATE chats SET updated_at=? WHERE updated_at IS NULL OR updated_at=''", (_utc_iso(),))

    def _init_db(self) -> None:
        with self._lock:
            con = self._connect()
            try:
                if not self._table_exists(con, "chats"):
                    self._create_chats_table(con)
                    return

                cols = set(self._get_cols(con, "chats"))
                required = {"chat_id", "title", "type", "approved", "config_json", "state_json", "created_at", "updated_at"}
                if not required.issubset(cols):
                    self._rebuild_chats_table(con)
            finally:
                con.close()

    # -------- Chat ops --------

    def upsert_chat(self, chat_id: int, title: str, chat_type: str) -> None:
        now = _utc_iso()
        with self._lock:
            con = self._connect()
            try:
                con.execute(
                    """
                    INSERT INTO chats (chat_id, title, type, approved, config_json, state_json, created_at, updated_at)
                    VALUES (?, ?, ?, 0, '{}', '{}', ?, ?)
                    ON CONFLICT(chat_id) DO UPDATE SET
                      title=excluded.title,
                      type=excluded.type,
                      updated_at=excluded.updated_at
                    """,
                    (chat_id, title or "", chat_type or "", now, now),
                )
            finally:
                con.close()

    def remove_chat(self, chat_id: int) -> None:
        with self._lock:
            con = self._connect()
            try:
                con.execute("DELETE FROM chats WHERE chat_id=?", (chat_id,))
            finally:
                con.close()

    def list_chats(self) -> List[Dict[str, Any]]:
        with self._lock:
            con = self._connect()
            try:
                rows = con.execute(
                    """
                    SELECT chat_id, title, type, approved, config_json, state_json, created_at, updated_at
                    FROM chats
                    ORDER BY updated_at DESC
                    """
                ).fetchall()
                out: List[Dict[str, Any]] = []
                for r in rows:
                    out.append(
                        {
                            "chat_id": int(r["chat_id"]),
                            "title": r["title"] or "",
                            "type": r["type"] or "",
                            "approved": int(r["approved"] or 0),
                            "config": json.loads(r["config_json"] or "{}"),
                            "state": json.loads(r["state_json"] or "{}"),
                        }
                    )
                return out
            finally:
                con.close()

    def get_chat(self, chat_id: int) -> Optional[Dict[str, Any]]:
        with self._lock:
            con = self._connect()
            try:
                r = con.execute(
                    """
                    SELECT chat_id, title, type, approved, config_json, state_json
                    FROM chats WHERE chat_id=?
                    """,
                    (chat_id,),
                ).fetchone()
                if not r:
                    return None
                return {
                    "chat_id": int(r["chat_id"]),
                    "title": r["title"] or "",
                    "type": r["type"] or "",
                    "approved": int(r["approved"] or 0),
                    "config": json.loads(r["config_json"] or "{}"),
                    "state": json.loads(r["state_json"] or "{}"),
                }
            finally:
                con.close()

    def set_approved(self, chat_id: int, approved: bool) -> None:
        now = _utc_iso()
        with self._lock:
            con = self._connect()
            try:
                con.execute(
                    "UPDATE chats SET approved=?, updated_at=? WHERE chat_id=?",
                    (1 if approved else 0, now, chat_id),
                )
            finally:
                con.close()

    def set_config(self, chat_id: int, config: Dict[str, Any]) -> None:
        now = _utc_iso()
        with self._lock:
            con = self._connect()
            try:
                con.execute(
                    "UPDATE chats SET config_json=?, updated_at=? WHERE chat_id=?",
                    (json.dumps(config, ensure_ascii=False), now, chat_id),
                )
            finally:
                con.close()

    def set_state(self, chat_id: int, state: Dict[str, Any]) -> None:
        now = _utc_iso()
        with self._lock:
            con = self._connect()
            try:
                con.execute(
                    "UPDATE chats SET state_json=?, updated_at=? WHERE chat_id=?",
                    (json.dumps(state, ensure_ascii=False), now, chat_id),
                )
            finally:
                con.close()
