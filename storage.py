from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class ChatConfig:
    chat_id: int
    title: str
    chat_type: str

    approved: bool = False
    enabled: bool = False

    interval_min: int = 5
    quiet_start: str = ""  # "HH:MM" or empty
    quiet_end: str = ""    # "HH:MM" or empty

    only_on_change: bool = False
    post_mode: str = "new"   # "new" | "edit"
    price_side: str = "sell" # "sell" | "buy"

    selected_fx: List[str] = None
    selected_coins: List[str] = None
    selected_markets: List[str] = None

    trigger_items: List[str] = None  # which items trigger "only_on_change"
    min_abs_change: int = 0          # toman
    min_pct_change: float = 0.0

    last_posted: Dict[str, Any] = None
    last_message_id: Optional[int] = None

    updated_at: int = 0

    def __post_init__(self):
        self.selected_fx = self.selected_fx or []
        self.selected_coins = self.selected_coins or []
        self.selected_markets = self.selected_markets or []
        self.trigger_items = self.trigger_items or []
        self.last_posted = self.last_posted or {}
        self.updated_at = self.updated_at or int(time.time())


class Storage:
    def __init__(self, path: str = "bot.db") -> None:
        self.path = path
        self._init()

    def _conn(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path)
        con.row_factory = sqlite3.Row
        return con

    def _init(self) -> None:
        with self._conn() as con:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS chats (
                    chat_id INTEGER PRIMARY KEY,
                    title TEXT NOT NULL,
                    chat_type TEXT NOT NULL,

                    approved INTEGER NOT NULL DEFAULT 0,
                    enabled INTEGER NOT NULL DEFAULT 0,

                    interval_min INTEGER NOT NULL DEFAULT 5,
                    quiet_start TEXT NOT NULL DEFAULT '',
                    quiet_end TEXT NOT NULL DEFAULT '',

                    only_on_change INTEGER NOT NULL DEFAULT 0,
                    post_mode TEXT NOT NULL DEFAULT 'new',
                    price_side TEXT NOT NULL DEFAULT 'sell',

                    selected_fx TEXT NOT NULL DEFAULT '[]',
                    selected_coins TEXT NOT NULL DEFAULT '[]',
                    selected_markets TEXT NOT NULL DEFAULT '[]',

                    trigger_items TEXT NOT NULL DEFAULT '[]',
                    min_abs_change INTEGER NOT NULL DEFAULT 0,
                    min_pct_change REAL NOT NULL DEFAULT 0.0,

                    last_posted TEXT NOT NULL DEFAULT '{}',
                    last_message_id INTEGER,

                    updated_at INTEGER NOT NULL DEFAULT 0
                )
                """
            )

    def upsert_chat(self, chat_id: int, title: str, chat_type: str) -> ChatConfig:
        existing = self.get_chat(chat_id)
        with self._conn() as con:
            if existing:
                con.execute(
                    "UPDATE chats SET title=?, chat_type=?, updated_at=? WHERE chat_id=?",
                    (title, chat_type, int(time.time()), chat_id),
                )
                return self.get_chat(chat_id)  # type: ignore
            else:
                con.execute(
                    "INSERT INTO chats (chat_id, title, chat_type, updated_at) VALUES (?, ?, ?, ?)",
                    (chat_id, title, chat_type, int(time.time())),
                )
                return self.get_chat(chat_id)  # type: ignore

    def get_chat(self, chat_id: int) -> Optional[ChatConfig]:
        with self._conn() as con:
            row = con.execute("SELECT * FROM chats WHERE chat_id=?", (chat_id,)).fetchone()
            if not row:
                return None
            return self._row_to_cfg(row)

    def list_chats(self, only_approved: Optional[bool] = None) -> List[ChatConfig]:
        q = "SELECT * FROM chats"
        args: list = []
        if only_approved is not None:
            q += " WHERE approved=?"
            args.append(1 if only_approved else 0)
        q += " ORDER BY updated_at DESC"
        with self._conn() as con:
            rows = con.execute(q, args).fetchall()
            return [self._row_to_cfg(r) for r in rows]

    def save(self, cfg: ChatConfig) -> None:
        with self._conn() as con:
            con.execute(
                """
                UPDATE chats SET
                    title=?, chat_type=?,
                    approved=?, enabled=?,
                    interval_min=?, quiet_start=?, quiet_end=?,
                    only_on_change=?, post_mode=?, price_side=?,
                    selected_fx=?, selected_coins=?, selected_markets=?,
                    trigger_items=?, min_abs_change=?, min_pct_change=?,
                    last_posted=?, last_message_id=?,
                    updated_at=?
                WHERE chat_id=?
                """,
                (
                    cfg.title, cfg.chat_type,
                    1 if cfg.approved else 0,
                    1 if cfg.enabled else 0,
                    int(cfg.interval_min),
                    cfg.quiet_start or "",
                    cfg.quiet_end or "",
                    1 if cfg.only_on_change else 0,
                    cfg.post_mode,
                    cfg.price_side,
                    json.dumps(cfg.selected_fx, ensure_ascii=False),
                    json.dumps(cfg.selected_coins, ensure_ascii=False),
                    json.dumps(cfg.selected_markets, ensure_ascii=False),
                    json.dumps(cfg.trigger_items, ensure_ascii=False),
                    int(cfg.min_abs_change),
                    float(cfg.min_pct_change),
                    json.dumps(cfg.last_posted, ensure_ascii=False),
                    cfg.last_message_id,
                    int(time.time()),
                    cfg.chat_id,
                ),
            )

    def _row_to_cfg(self, r: sqlite3.Row) -> ChatConfig:
        return ChatConfig(
            chat_id=int(r["chat_id"]),
            title=str(r["title"]),
            chat_type=str(r["chat_type"]),
            approved=bool(r["approved"]),
            enabled=bool(r["enabled"]),
            interval_min=int(r["interval_min"]),
            quiet_start=str(r["quiet_start"] or ""),
            quiet_end=str(r["quiet_end"] or ""),
            only_on_change=bool(r["only_on_change"]),
            post_mode=str(r["post_mode"]),
            price_side=str(r["price_side"]),
            selected_fx=json.loads(r["selected_fx"] or "[]"),
            selected_coins=json.loads(r["selected_coins"] or "[]"),
            selected_markets=json.loads(r["selected_markets"] or "[]"),
            trigger_items=json.loads(r["trigger_items"] or "[]"),
            min_abs_change=int(r["min_abs_change"]),
            min_pct_change=float(r["min_pct_change"]),
            last_posted=json.loads(r["last_posted"] or "{}"),
            last_message_id=r["last_message_id"],
            updated_at=int(r["updated_at"] or 0),
        )
