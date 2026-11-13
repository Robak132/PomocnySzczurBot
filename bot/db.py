from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS gms (
    user_id      INTEGER PRIMARY KEY,
    role_id      INTEGER UNIQUE NOT NULL,
    active       INTEGER NOT NULL DEFAULT 1,
    needs_repair INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS group_members (
    gm_user_id INTEGER NOT NULL REFERENCES gms(user_id) ON DELETE CASCADE,
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
    role_id: int
    active: bool
    needs_repair: bool


def _gm(row: aiosqlite.Row | None) -> GmRecord | None:
    if row is None:
        return None
    return GmRecord(row["user_id"], row["role_id"], bool(row["active"]), bool(row["needs_repair"]))


class Database:
    def __init__(self, path: str):
        self.path = path
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA foreign_keys = ON")
        await self._conn.executescript(SCHEMA)
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    # -- helpers -----------------------------------------------------------
    async def _one(self, sql: str, params: tuple = ()):
        cur = await self._conn.execute(sql, params)
        return await cur.fetchone()

    async def _all(self, sql: str, params: tuple = ()):
        cur = await self._conn.execute(sql, params)
        return await cur.fetchall()

    async def _write(self, sql: str, params: tuple = ()) -> int:
        cur = await self._conn.execute(sql, params)
        await self._conn.commit()
        return cur.rowcount

    # -- settings ----------------------------------------------------------
    async def get_setting(self, key: str) -> str | None:
        row = await self._one("SELECT value FROM settings WHERE key = ?", (key,))
        return row["value"] if row else None

    async def set_setting(self, key: str, value: str) -> None:
        await self._write(
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    # -- GMs ---------------------------------------------------------------
    async def get_gm(self, user_id: int, include_inactive: bool = False) -> GmRecord | None:
        sql = "SELECT * FROM gms WHERE user_id = ?"
        if not include_inactive:
            sql += " AND active = 1"
        return _gm(await self._one(sql, (user_id,)))

    async def get_gm_by_role(self, role_id: int) -> GmRecord | None:
        return _gm(await self._one("SELECT * FROM gms WHERE role_id = ? AND active = 1", (role_id,)))

    async def list_gms(self) -> list[GmRecord | None]:
        rows = await self._all("SELECT * FROM gms WHERE active = 1 ORDER BY created_at, user_id")
        return [_gm(r) for r in rows]

    async def upsert_gm(self, user_id: int, role_id: int) -> None:
        """Insert a GM, or reactivate an orphaned one."""
        await self._write(
            "INSERT INTO gms(user_id, role_id, active, needs_repair, created_at) VALUES(?, ?, 1, 0, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET role_id = excluded.role_id, active = 1, needs_repair = 0",
            (user_id, role_id, _now()),
        )

    async def deactivate_gm(self, user_id: int) -> None:
        """Unregister a GM but keep their group rows (orphaned group)."""
        await self._write("UPDATE gms SET active = 0 WHERE user_id = ?", (user_id,))

    async def delete_gm(self, user_id: int) -> None:
        """Delete a GM and (via cascade) their group rows."""
        await self._write("DELETE FROM gms WHERE user_id = ?", (user_id,))

    async def set_role_id(self, user_id: int, role_id: int) -> None:
        await self._write(
            "UPDATE gms SET role_id = ?, needs_repair = 0 WHERE user_id = ?", (role_id, user_id)
        )

    async def set_needs_repair(self, user_id: int, flag: bool) -> None:
        await self._write("UPDATE gms SET needs_repair = ? WHERE user_id = ?", (int(flag), user_id))

    # -- group members -----------------------------------------------------
    async def add_member(self, gm_id: int, player_id: int) -> bool:
        changed = await self._write(
            "INSERT OR IGNORE INTO group_members(gm_user_id, player_id, added_at) VALUES(?, ?, ?)",
            (gm_id, player_id, _now()),
        )
        return changed > 0

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
        row = await self._one("SELECT COUNT(*) AS n FROM group_members WHERE gm_user_id = ?", (gm_id,))
        return row["n"]

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
