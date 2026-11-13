from __future__ import annotations

import datetime as dt
import sqlite3
from dataclasses import dataclass

import aiosqlite

from .exceptions import DatabaseVersionError

# Bump this whenever SCHEMA changes. The bot refuses to start on a database
# with a different version instead of trying to upgrade it.
SCHEMA_VERSION = 3

SCHEMA = """
CREATE TABLE IF NOT EXISTS gms (
    user_id      INTEGER PRIMARY KEY,
    role_id      INTEGER UNIQUE,              -- NULL until an admin gives the GM a group role
    active       INTEGER NOT NULL DEFAULT 1,
    needs_repair INTEGER NOT NULL DEFAULT 0,  -- unused; kept so the schema version stays the same
    created_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS group_members (
    gm_user_id INTEGER NOT NULL REFERENCES gms(user_id),
    player_id  INTEGER NOT NULL,
    added_at   TEXT NOT NULL,
    PRIMARY KEY (gm_user_id, player_id)
);
CREATE INDEX IF NOT EXISTS idx_members_player ON group_members(player_id);
CREATE TABLE IF NOT EXISTS audit_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_id   INTEGER,
    action     TEXT NOT NULL,
    target_id  INTEGER,
    gm_user_id INTEGER,
    detail     TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class GmRecord:
    user_id: int
    role_id: int | None  # None: the GM has no group role yet
    active: bool


def _gm(row: aiosqlite.Row) -> GmRecord:
    return GmRecord(row["user_id"], row["role_id"], bool(row["active"]))


def _gm_or_none(row: aiosqlite.Row | None) -> GmRecord | None:
    return None if row is None else _gm(row)


class Database:
    def __init__(self, path: str):
        self.path = path
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        """Open the database, creating the schema if the file is new.

        Raises DatabaseVersionError if the file was made for a different schema version.
        """
        conn = await aiosqlite.connect(self.path)
        conn.row_factory = aiosqlite.Row
        self._conn = conn
        try:
            await conn.execute("PRAGMA foreign_keys = ON")
            version = await self._value("PRAGMA user_version")
            is_empty = await self._one("SELECT 1 FROM sqlite_master WHERE type = 'table'") is None
            if version == 0 and is_empty:
                await conn.executescript(SCHEMA + f"PRAGMA user_version = {SCHEMA_VERSION};")
                await conn.commit()
            elif version != SCHEMA_VERSION:
                raise DatabaseVersionError(
                    f"Database {self.path} has schema version {version}, but this bot needs version "
                    f"{SCHEMA_VERSION}. Back it up and remove it, or run the bot version that created it."
                )
        except (DatabaseVersionError, sqlite3.Error):
            await self.close()
            raise

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("The database is not connected. Call connect() first.")
        return self._conn

    # -- helpers -----------------------------------------------------------
    async def _value(self, sql: str, params: tuple = ()):
        """Return the first column of a query that always yields one row (COUNT, PRAGMA)."""
        row = await self._one(sql, params)
        if row is None:
            raise RuntimeError(f"Query returned no rows: {sql}")
        return row[0]

    async def _one(self, sql: str, params: tuple = ()):
        cur = await self.conn.execute(sql, params)
        return await cur.fetchone()

    async def _all(self, sql: str, params: tuple = ()):
        cur = await self.conn.execute(sql, params)
        return await cur.fetchall()

    async def _write(self, sql: str, params: tuple = ()) -> int:
        cur = await self.conn.execute(sql, params)
        await self.conn.commit()
        return cur.rowcount

    # -- settings ----------------------------------------------------------
    async def get_setting(self, key: str) -> str | None:
        row = await self._one("SELECT value FROM settings WHERE key = ?", (key,))
        return row["value"] if row else None

    async def set_setting(self, key: str, value: str) -> None:
        if await self._one("SELECT 1 FROM settings WHERE key = ?", (key,)):
            await self._write("UPDATE settings SET value = ? WHERE key = ?", (value, key))
        else:
            await self._write("INSERT INTO settings(key, value) VALUES(?, ?)", (key, value))

    # -- GMs ---------------------------------------------------------------
    async def get_gm(self, user_id: int, include_inactive: bool = False) -> GmRecord | None:
        sql = "SELECT * FROM gms WHERE user_id = ?"
        if not include_inactive:
            sql += " AND active = 1"
        return _gm_or_none(await self._one(sql, (user_id,)))

    async def get_gm_by_role(self, role_id: int, include_inactive: bool = False) -> GmRecord | None:
        sql = "SELECT * FROM gms WHERE role_id = ?"
        if not include_inactive:
            sql += " AND active = 1"
        return _gm_or_none(await self._one(sql, (role_id,)))

    async def list_gms(self) -> list[GmRecord]:
        rows = await self._all("SELECT * FROM gms WHERE active = 1 ORDER BY created_at, user_id")
        return [_gm(r) for r in rows]

    async def insert_gm(self, user_id: int) -> None:
        """Register a new GM without a group role. The caller must check that no record exists yet."""
        await self._write(
            "INSERT INTO gms(user_id, role_id, active, created_at) VALUES(?, NULL, 1, ?)",
            (user_id, _now()),
        )

    async def reactivate_gm(self, user_id: int) -> None:
        """Re-register an orphaned GM. Their group role and players stay as they were."""
        await self._write("UPDATE gms SET active = 1 WHERE user_id = ?", (user_id,))

    async def deactivate_gm(self, user_id: int) -> None:
        """Unregister a GM but keep their group rows (orphaned group)."""
        await self._write("UPDATE gms SET active = 0 WHERE user_id = ?", (user_id,))

    async def delete_gm(self, user_id: int) -> None:
        """Delete a GM together with their group rows, explicitly and in one transaction."""
        try:
            await self.conn.execute("DELETE FROM group_members WHERE gm_user_id = ?", (user_id,))
            await self.conn.execute("DELETE FROM gms WHERE user_id = ?", (user_id,))
            await self.conn.commit()
        except sqlite3.Error:
            await self.conn.rollback()
            raise

    async def set_group_role(self, user_id: int, role_id: int) -> None:
        await self._write("UPDATE gms SET role_id = ? WHERE user_id = ?", (role_id, user_id))

    # -- group members -----------------------------------------------------
    async def is_member(self, gm_id: int, player_id: int) -> bool:
        row = await self._one(
            "SELECT 1 FROM group_members WHERE gm_user_id = ? AND player_id = ?", (gm_id, player_id)
        )
        return row is not None

    async def add_member(self, gm_id: int, player_id: int) -> None:
        """Record a player in a group. The caller must check is_member first."""
        await self._write(
            "INSERT INTO group_members(gm_user_id, player_id, added_at) VALUES(?, ?, ?)",
            (gm_id, player_id, _now()),
        )

    async def remove_member(self, gm_id: int, player_id: int) -> bool:
        changed = await self._write(
            "DELETE FROM group_members WHERE gm_user_id = ? AND player_id = ?", (gm_id, player_id)
        )
        return changed > 0

    async def list_members(self, gm_id: int) -> list[int]:
        rows = await self._all(
            "SELECT player_id FROM group_members WHERE gm_user_id = ? ORDER BY added_at, player_id",
            (gm_id,),
        )
        return [r["player_id"] for r in rows]

    async def count_members(self, gm_id: int) -> int:
        return await self._value("SELECT COUNT(*) FROM group_members WHERE gm_user_id = ?", (gm_id,))

    async def remove_player_everywhere(self, player_id: int) -> list[int]:
        rows = await self._all("SELECT gm_user_id FROM group_members WHERE player_id = ?", (player_id,))
        gm_ids = [r["gm_user_id"] for r in rows]
        if gm_ids:
            await self._write("DELETE FROM group_members WHERE player_id = ?", (player_id,))
        return gm_ids

    # -- audit -------------------------------------------------------------
    async def add_audit(
        self,
        actor_id: int | None,
        action: str,
        target_id: int | None,
        gm_user_id: int | None,
        detail: str | None,
    ) -> None:
        await self._write(
            "INSERT INTO audit_log(actor_id, action, target_id, gm_user_id, detail, created_at) "
            "VALUES(?, ?, ?, ?, ?, ?)",
            (actor_id, action, target_id, gm_user_id, detail, _now()),
        )
