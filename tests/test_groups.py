from types import SimpleNamespace

import pytest
import pytest_asyncio

from bot.db import Database
from bot.services.groups import RATE_LIMIT_SECONDS, GroupService, ServiceError, parse_color

from .fakes import FakeGuild


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest_asyncio.fixture
async def env(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    await db.connect()
    guild = FakeGuild()
    clock = Clock()
    service = GroupService(db, clock=clock)
    yield SimpleNamespace(db=db, guild=guild, clock=clock, svc=service)
    await db.close()


async def make_gm(env, id=10, name="Alice", **kwargs):
    member = env.guild.add_member(id, name)
    record = await env.svc.register_gm(env.guild, member, **kwargs)
    return member, record, env.guild.get_role(record.role_id)


# ---- colors ---------------------------------------------------------------
def test_parse_color_hex_and_names():
    assert parse_color("#FF8800").value == 0xFF8800
    assert parse_color("ff8800").value == 0xFF8800
    assert parse_color("Red").value == 0xE74C3C


def test_parse_color_rejects_garbage():
    with pytest.raises(ServiceError):
        parse_color("not-a-color")


# ---- GM registration --------------------------------------------------------
async def test_register_gm_creates_roles_and_gm_does_not_hold_group_role(env):
    member, record, group_role = await make_gm(env)
    gm_role = await env.svc.get_gm_role(env.guild)
    assert gm_role in member.roles
    assert group_role is not None and group_role.name == "Alice"
    assert group_role not in member.roles
    assert (await env.db.get_gm(10)).role_id == group_role.id


async def test_register_gm_custom_name_and_color(env):
    member = env.guild.add_member(11, "Bob")
    record = await env.svc.register_gm(env.guild, member, name="Dragons", color="#00FF00")
    role = env.guild.get_role(record.role_id)
    assert role.name == "Dragons"
    assert role.colour.value == 0x00FF00


async def test_register_gm_twice_fails(env):
    member, _, _ = await make_gm(env)
    with pytest.raises(ServiceError, match="already a GM"):
        await env.svc.register_gm(env.guild, member)


async def test_bots_cannot_be_gms(env):
    bot = env.guild.add_member(50, "SomeBot", bot=True)
    with pytest.raises(ServiceError):
        await env.svc.register_gm(env.guild, bot)


async def test_invalid_color_changes_nothing(env):
    member = env.guild.add_member(11, "Bob")
    with pytest.raises(ServiceError):
        await env.svc.register_gm(env.guild, member, color="nope")
    assert await env.db.get_gm(11) is None
    assert member.roles == []


async def test_default_names_do_not_collide(env):
    await make_gm(env, id=10, name="Sam")
    _, _, role = await make_gm(env, id=11, name="Sam")
    assert role.name == "Sam 2"


async def test_explicit_duplicate_name_rejected(env):
    await make_gm(env, id=10, name="Alice")
    member = env.guild.add_member(11, "Bob")
    with pytest.raises(ServiceError, match="already exists"):
        await env.svc.register_gm(env.guild, member, name="alice")


async def test_unregister_deletes_group_role_by_default(env):
    member, _, role = await make_gm(env)
    env.guild.add_member(20, "Pat")
    await env.svc.add_player(env.guild, 10, env.guild.get_member(20))
    await env.svc.unregister_gm(env.guild, 10, keep_role=False)
    assert await env.db.get_gm(10, include_inactive=True) is None
    assert env.guild.get_role(role.id) is None
    assert await env.db.list_members(10) == []
    assert await env.svc.get_gm_role(env.guild) not in member.roles


async def test_unregister_keep_role_orphans_group_and_reactivation_restores_it(env):
    member, _, role = await make_gm(env)
    pat = env.guild.add_member(20, "Pat")
    await env.svc.add_player(env.guild, 10, pat)
    await env.svc.unregister_gm(env.guild, 10, keep_role=True)
    assert await env.db.get_gm(10) is None
    assert env.guild.get_role(role.id) is not None
    assert await env.db.list_members(10) == [20]
    assert role in pat.roles

    record = await env.svc.register_gm(env.guild, member)
    assert record.role_id == role.id
    assert await env.db.list_members(10) == [20]


# ---- GM role ----------------------------------------------------------------
async def test_set_gm_role_registers_existing_holders(env):
    existing = await env.guild.create_role(name="Game Masters")
    holder = env.guild.add_member(30, "Holder")
    await holder.add_roles(existing)
    registered = await env.svc.set_gm_role(env.guild, existing)
    assert [m.id for m in registered] == [30]
    assert await env.db.get_gm(30) is not None
    assert await env.svc.get_gm_role(env.guild) == existing


async def test_set_gm_role_rejects_role_above_bot(env):
    high = await env.guild.create_role(name="High")
    high.position = 500
    with pytest.raises(ServiceError, match="above my highest role"):
        await env.svc.set_gm_role(env.guild, high)


async def test_deleted_gm_role_gives_clear_error(env):
    await make_gm(env)
    (await env.svc.get_gm_role(env.guild)).guild.roles.pop((await env.svc.get_gm_role(env.guild)).id)
    with pytest.raises(ServiceError, match="gm-role"):
        await env.svc.get_gm_role(env.guild)


async def test_role_assigned_by_hand_registers_gm_and_removal_orphans(env):
    gm_role = await env.svc.get_gm_role(env.guild, create=True)
    bob = env.guild.add_member(40, "Bob")

    before = SimpleNamespace(roles=list(bob.roles))
    await bob.add_roles(gm_role)
    await env.svc.handle_member_update(before, bob)
    record = await env.db.get_gm(40)
    assert record is not None
    group_role = env.guild.get_role(record.role_id)
    assert group_role not in bob.roles

    before = SimpleNamespace(roles=list(bob.roles))
    await bob.remove_roles(gm_role)
    await env.svc.handle_member_update(before, bob)
    assert await env.db.get_gm(40) is None
    assert (await env.db.get_gm(40, include_inactive=True)).active is False
    assert env.guild.get_role(record.role_id) is not None


async def test_member_update_is_idempotent_for_bot_driven_changes(env):
    member, _, _ = await make_gm(env)
    gm_role = await env.svc.get_gm_role(env.guild)
    before = SimpleNamespace(roles=[])
    await env.svc.handle_member_update(before, member)  # bot assigned the GM role itself
    assert len(await env.db.list_gms()) == 1
    assert gm_role in member.roles


# ---- players ----------------------------------------------------------------
async def test_add_and_remove_player(env):
    _, _, role = await make_gm(env)
    pat = env.guild.add_member(20, "Pat")
    await env.svc.add_player(env.guild, 10, pat)
    assert role in pat.roles
    assert await env.svc.list_players(10) == [20]
    await env.svc.remove_player(env.guild, 10, pat)
    assert role not in pat.roles
    assert await env.svc.list_players(10) == []


async def test_player_can_be_in_several_groups(env):
    _, _, role_a = await make_gm(env, id=10, name="Alice")
    _, _, role_b = await make_gm(env, id=11, name="Bob")
    pat = env.guild.add_member(20, "Pat")
    await env.svc.add_player(env.guild, 10, pat)
    await env.svc.add_player(env.guild, 11, pat)
    assert role_a in pat.roles and role_b in pat.roles
    await env.svc.remove_player(env.guild, 10, pat)
    assert role_a not in pat.roles and role_b in pat.roles


async def test_duplicate_player_rejected(env):
    await make_gm(env)
    pat = env.guild.add_member(20, "Pat")
    await env.svc.add_player(env.guild, 10, pat)
    with pytest.raises(ServiceError, match="already in this group"):
        await env.svc.add_player(env.guild, 10, pat)


async def test_gms_admins_and_bots_cannot_be_players(env):
    await make_gm(env, id=10, name="Alice")
    other_gm, _, _ = await make_gm(env, id=11, name="Bob")
    admin = env.guild.add_member(21, "Root", admin=True)
    bot = env.guild.add_member(22, "Botty", bot=True)
    for target in (other_gm, admin, bot):
        with pytest.raises(ServiceError):
            await env.svc.add_player(env.guild, 10, target)


async def test_gm_cannot_add_themselves(env):
    member, _, _ = await make_gm(env)
    with pytest.raises(ServiceError):
        await env.svc.add_player(env.guild, 10, member)


async def test_remove_player_not_in_group(env):
    await make_gm(env)
    pat = env.guild.add_member(20, "Pat")
    with pytest.raises(ServiceError, match="not in this group"):
        await env.svc.remove_player(env.guild, 10, pat)


async def test_non_gm_cannot_manage_group(env):
    pat = env.guild.add_member(20, "Pat")
    with pytest.raises(ServiceError, match="not a GM"):
        await env.svc.add_player(env.guild, 99, pat)


# ---- rank cosmetics ---------------------------------------------------------
async def test_rename_and_recolor(env):
    _, _, role = await make_gm(env)
    await env.svc.rename_group(env.guild, 10, "  Dragons ")
    assert role.name == "Dragons"
    env.clock.now += RATE_LIMIT_SECONDS
    await env.svc.recolor_group(env.guild, 10, "#112233")
    assert role.colour.value == 0x112233


async def test_rename_rate_limited(env):
    await make_gm(env)
    await env.svc.rename_group(env.guild, 10, "One")
    with pytest.raises(ServiceError, match="wait"):
        await env.svc.rename_group(env.guild, 10, "Two")
    env.clock.now += RATE_LIMIT_SECONDS
    await env.svc.rename_group(env.guild, 10, "Two")


async def test_rename_must_be_unique_and_valid(env):
    await make_gm(env, id=10, name="Alice")
    await make_gm(env, id=11, name="Bob")
    with pytest.raises(ServiceError, match="already exists"):
        await env.svc.rename_group(env.guild, 10, "bob")
    with pytest.raises(ServiceError):
        await env.svc.rename_group(env.guild, 10, "   ")
    with pytest.raises(ServiceError):
        await env.svc.rename_group(env.guild, 10, "x" * 101)


async def test_group_commands_blocked_when_bot_below_role(env):
    _, _, role = await make_gm(env)
    role.position = 500
    with pytest.raises(ServiceError, match="above my highest role"):
        await env.svc.rename_group(env.guild, 10, "New")
    assert await env.svc.check_hierarchy(env.guild)


# ---- events and sync --------------------------------------------------------
async def test_member_leaving_is_removed_from_groups(env):
    await make_gm(env)
    pat = env.guild.add_member(20, "Pat")
    await env.svc.add_player(env.guild, 10, pat)
    env.guild.remove_member(20)
    await env.svc.handle_member_remove(pat)
    assert await env.db.list_members(10) == []


async def test_gm_leaving_orphans_group(env):
    member, _, role = await make_gm(env)
    env.guild.remove_member(10)
    await env.svc.handle_member_remove(member)
    assert await env.db.get_gm(10) is None
    assert env.guild.get_role(role.id) is not None


async def test_deleted_group_role_flagged_then_sync_recreates_it(env):
    _, record, role = await make_gm(env)
    pat = env.guild.add_member(20, "Pat")
    await env.svc.add_player(env.guild, 10, pat)

    await role.delete()
    await env.svc.handle_role_deleted(role)
    assert (await env.db.get_gm(10)).needs_repair

    report = await env.svc.sync(env.guild)
    assert any("Recreated" in line for line in report)
    new_role = env.guild.get_role((await env.db.get_gm(10)).role_id)
    assert new_role is not None and new_role in pat.roles
    assert not (await env.db.get_gm(10)).needs_repair


async def test_sync_follows_discord_for_role_holders(env):
    _, _, role = await make_gm(env)
    lost = env.guild.add_member(20, "Lost")
    await env.svc.add_player(env.guild, 10, lost)
    extra = env.guild.add_member(21, "Extra")
    await extra.add_roles(role)  # given the role by hand
    await lost.remove_roles(role)  # role taken away by hand

    report = await env.svc.sync(env.guild)
    assert await env.db.list_members(10) == [21]
    assert len(report) == 2

    assert await env.svc.sync(env.guild) == []


async def test_sync_drops_players_who_left(env):
    await make_gm(env)
    gone = env.guild.add_member(20, "Gone")
    await env.svc.add_player(env.guild, 10, gone)
    env.guild.remove_member(20)
    report = await env.svc.sync(env.guild)
    assert await env.db.list_members(10) == []
    assert any("left the server" in line for line in report)


async def test_sync_registers_hand_assigned_gm(env):
    gm_role = await env.svc.get_gm_role(env.guild, create=True)
    bob = env.guild.add_member(40, "Bob")
    await bob.add_roles(gm_role)
    report = await env.svc.sync(env.guild)
    assert await env.db.get_gm(40) is not None
    assert any("Registered" in line for line in report)


async def test_audit_log_and_hook(env, tmp_path):
    calls = []

    async def hook(*args):
        calls.append(args)

    env.svc.on_audit = hook
    await make_gm(env)
    assert any(call[1] == "gm_added" for call in calls)
