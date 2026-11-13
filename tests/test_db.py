import sqlite3

import pytest

from bot.db import SCHEMA_VERSION, Database
from bot.exceptions import DatabaseVersionError

OLD_SCHEMA = """
CREATE TABLE gms (
    user_id INTEGER PRIMARY KEY, role_id INTEGER UNIQUE NOT NULL,
    active INTEGER NOT NULL DEFAULT 1, needs_repair INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
);
CREATE TABLE group_members (
    gm_user_id INTEGER NOT NULL REFERENCES gms(user_id) ON DELETE CASCADE,
    player_id INTEGER NOT NULL, added_at TEXT NOT NULL,
    PRIMARY KEY (gm_user_id, player_id)
);
INSERT INTO gms VALUES (10, 100, 1, 0, 'x');
INSERT INTO group_members VALUES (10, 20, 'x'), (10, 21, 'x');
"""


async def _has_cascade(db):
    cur = await db._conn.execute("PRAGMA foreign_key_list(group_members)")
    return any(r["on_delete"].upper() == "CASCADE" for r in await cur.fetchall())


async def test_schema_has_no_cascade(tmp_path):
    db = Database(str(tmp_path / "x.db"))
    await db.connect()
    assert not await _has_cascade(db)
    await db.close()


async def test_deleting_gm_row_with_players_is_refused(tmp_path):
    db = Database(str(tmp_path / "x.db"))
    await db.connect()
    await db.insert_gm(10, 100)
    await db.add_member(10, 20)
    with pytest.raises(sqlite3.IntegrityError):
        await db._write("DELETE FROM gms WHERE user_id = 10")
    await db._conn.rollback()
    assert await db.list_members(10) == [20]
    await db.close()


async def test_delete_gm_removes_players_explicitly(tmp_path):
    db = Database(str(tmp_path / "x.db"))
    await db.connect()
    await db.insert_gm(10, 100)
    await db.add_member(10, 20)
    await db.delete_gm(10)
    assert await db.get_gm(10, include_inactive=True) is None
    assert await db.list_members(10) == []
    await db.close()


async def test_new_database_gets_current_version(tmp_path):
    path = str(tmp_path / "x.db")
    db = Database(path)
    await db.connect()
    await db.close()
    con = sqlite3.connect(path)
    assert con.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    con.close()
    db = Database(path)  # reopening a current database works
    await db.connect()
    await db.close()


async def test_old_unversioned_database_is_refused_and_left_untouched(tmp_path):
    path = str(tmp_path / "old.db")
    con = sqlite3.connect(path)
    con.executescript(OLD_SCHEMA)
    con.close()
    with pytest.raises(DatabaseVersionError, match="schema version 0"):
        await Database(path).connect()
    con = sqlite3.connect(path)
    assert "CASCADE" in con.execute("SELECT sql FROM sqlite_master WHERE name = 'group_members'").fetchone()[0]
    assert con.execute("SELECT COUNT(*) FROM group_members").fetchone()[0] == 2
    con.close()


async def test_other_version_is_refused(tmp_path):
    path = str(tmp_path / "x.db")
    con = sqlite3.connect(path)
    con.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    con.execute("CREATE TABLE gms (user_id INTEGER)")
    con.close()
    with pytest.raises(DatabaseVersionError):
        await Database(path).connect()


async def test_settings_insert_then_update(tmp_path):
    db = Database(str(tmp_path / "x.db"))
    await db.connect()
    await db.set_setting("k", "1")
    await db.set_setting("k", "2")
    assert await db.get_setting("k") == "2"
    await db.close()


async def test_duplicate_member_insert_is_an_error(tmp_path):
    db = Database(str(tmp_path / "x.db"))
    await db.connect()
    await db.insert_gm(10, 100)
    await db.add_member(10, 20)
    assert await db.is_member(10, 20)
    with pytest.raises(sqlite3.IntegrityError):
        await db.add_member(10, 20)
    await db.close()


async def test_reactivate_gm_switches_role(tmp_path):
    db = Database(str(tmp_path / "x.db"))
    await db.connect()
    await db.insert_gm(10, 100)
    await db.deactivate_gm(10)
    await db.reactivate_gm(10, 200)
    gm = await db.get_gm(10)
    assert gm.active and gm.role_id == 200
    await db.close()
