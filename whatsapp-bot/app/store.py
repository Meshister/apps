"""SQLite storage for journal entries and unfinished entries (so a restart doesn't lose your place)."""

import json
import sqlite3
from pathlib import Path


class Store:
    def __init__(self, path: Path | str):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                phone TEXT NOT NULL,
                created_at TEXT NOT NULL,
                data TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS entries_phone ON entries (phone, created_at);
            CREATE TABLE IF NOT EXISTS state (
                phone TEXT PRIMARY KEY,
                data TEXT NOT NULL
            );
            """
        )

    def get_state(self, phone: str) -> dict | None:
        row = self._db.execute("SELECT data FROM state WHERE phone = ?", (phone,)).fetchone()
        return json.loads(row[0]) if row else None

    def set_state(self, phone: str, state: dict) -> None:
        with self._db:
            self._db.execute(
                "INSERT INTO state (phone, data) VALUES (?, ?) ON CONFLICT(phone) DO UPDATE SET data = excluded.data",
                (phone, json.dumps(state)),
            )

    def clear_state(self, phone: str) -> None:
        with self._db:
            self._db.execute("DELETE FROM state WHERE phone = ?", (phone,))

    def add_entry(self, phone: str, created_at: str, data: dict) -> None:
        with self._db:
            self._db.execute(
                "INSERT INTO entries (phone, created_at, data) VALUES (?, ?, ?)",
                (phone, created_at, json.dumps(data, ensure_ascii=False)),
            )

    def entries(self, phone: str, last: int | None = None) -> list[dict]:
        """Entries oldest first; `last` limits to the most recent N."""
        sql = "SELECT created_at, data FROM entries WHERE phone = ? ORDER BY created_at DESC, id DESC"
        params: tuple = (phone,)
        if last:
            sql += " LIMIT ?"
            params += (last,)
        rows = self._db.execute(sql, params).fetchall()
        return [{"created_at": c, **json.loads(d)} for c, d in reversed(rows)]

    def close(self) -> None:
        self._db.close()
