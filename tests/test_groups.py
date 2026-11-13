from types import SimpleNamespace

import pytest
import pytest_asyncio

from bot.db import Database
from bot.exceptions import ServiceError
from bot.groups import GroupService

from .fakes import FakeGuild


@pytest_asyncio.fixture
async def env(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    await db.connect()
    guild = FakeGuild()
    service = GroupService(db)
    yield SimpleNamespace(db=db, guild=guild, svc=service)
    await db.close()


async def make_gm(env, id=10, name="Alice", **kwargs):
    member = env.guild.add_member(id, name)
    record = await env.svc.register_gm(env.guild, member, **kwargs)
    return member, record, env.guild.get_role(record.role_id)


# ---- GM registration --------------------------------------------------------
async def test_register_gm_creates_roles_and_gm_does_not_hold_group_role(env):
    member, _, group_role = await make_gm(env)
    gm_role = await env.svc.get_gm_role(env.guild)
    assert gm_role in member.roles
    assert group_role is not None and group_role.name == "Alice"
    assert group_role not in member.roles
    assert (await env.db.get_gm(10)).role_id == group_role.id


async def test_register_gm_custom_name(env):
    member = env.guild.add_member(11, "Bob")
    record = await env.svc.register_gm(env.guild, member, name="Dragons")
    role = env.guild.get_role(record.role_id)
    assert role.name == "Dragons"


async def test_register_gm_twice_fails(env):
    member, _, _ = await make_gm(env)
    with pytest.raises(ServiceError, match="already a GM"):
        await env.svc.register_gm(env.guild, member)


async def test_bots_cannot_be_gms(env):
    bot = env.guild.add_member(50, "SomeBot", bot=True)
    with pytest.raises(ServiceError):
        await env.svc.register_gm(env.guild, bot)


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


# ---- role cosmetics ---------------------------------------------------------
def test_bot_never_renames_or_recolors_roles():
    assert not hasattr(GroupService, "rename_group")
    assert not hasattr(GroupService, "recolor_group")


async def test_group_commands_blocked_when_bot_below_role(env):
    _, _, role = await make_gm(env)
    role.position = 500
    pat = env.guild.add_member(20, "Pat")
    with pytest.raises(ServiceError, match="above my highest role"):
        await env.svc.add_player(env.guild, 10, pat)
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
    _, _, role = await make_gm(env)
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


# ---- adopting an existing role as the group role -----------------------------
async def test_register_gm_adopts_existing_role_and_imports_holders(env):
    import discord

    campaign = env.guild.add_role("Dragons", permissions=discord.Permissions(send_messages=True))
    gm = env.guild.add_member(10, "Alice")
    p1 = env.guild.add_member(20, "Bob")
    p2 = env.guild.add_member(21, "Cid")
    bot = env.guild.add_member(50, "SomeBot", bot=True)
    for m in (gm, p1, p2, bot):
        m.roles.append(campaign)

    record = await env.svc.register_gm(env.guild, gm, role=campaign)

    assert record.role_id == campaign.id
    assert len(env.guild.roles) == 3  # bot role, GM role, Dragons: nothing new created
    assert campaign not in gm.roles  # GM does not hold their own group role
    assert await env.db.list_members(10) == [20, 21]
    assert campaign.name == "Dragons"


async def test_adopted_role_is_never_renamed(env):
    campaign = env.guild.add_role("old")
    gm = env.guild.add_member(10, "Alice")
    with pytest.raises(ServiceError, match="doesn't rename roles"):
        await env.svc.register_gm(env.guild, gm, role=campaign, name="Dragons")
    assert campaign.name == "old"
    assert await env.db.get_gm(10) is None


async def test_reregistered_gm_role_is_never_renamed(env):
    alice, _, role = await make_gm(env)
    await env.svc.unregister_gm(env.guild, 10, keep_role=True)
    with pytest.raises(ServiceError, match="doesn't rename roles"):
        await env.svc.register_gm(env.guild, alice, name="New")
    assert role.name == "Alice"


async def test_adopted_role_works_like_a_normal_group(env):
    campaign = env.guild.add_role("Dragons")
    gm = env.guild.add_member(10, "Alice")
    await env.svc.register_gm(env.guild, gm, role=campaign)
    newbie = env.guild.add_member(30, "Dee")
    await env.svc.add_player(env.guild, 10, newbie)
    assert campaign in newbie.roles
    assert 30 in await env.db.list_members(10)


async def test_cannot_adopt_another_gms_group_role(env):
    _, _, alice_role = await make_gm(env)
    bob = env.guild.add_member(11, "Bob")
    with pytest.raises(ServiceError, match="already the group role"):
        await env.svc.register_gm(env.guild, bob, role=alice_role)
    assert await env.db.get_gm(11) is None


async def test_cannot_adopt_gm_role_everyone_or_managed(env):
    await make_gm(env)
    gm_role = await env.svc.get_gm_role(env.guild)
    bob = env.guild.add_member(11, "Bob")
    with pytest.raises(ServiceError, match="GM role"):
        await env.svc.register_gm(env.guild, bob, role=gm_role)
    everyone = env.guild.add_role("@everyone")
    everyone.id = env.guild.id
    with pytest.raises(ServiceError, match="@everyone"):
        await env.svc.register_gm(env.guild, bob, role=everyone)
    managed = env.guild.add_role("Integration", managed=True)
    with pytest.raises(ServiceError, match="managed"):
        await env.svc.register_gm(env.guild, bob, role=managed)



async def test_cannot_adopt_role_above_the_bot(env):
    high = env.guild.add_role("High", position=500)
    bob = env.guild.add_member(11, "Bob")
    with pytest.raises(ServiceError, match="above my highest role"):
        await env.svc.register_gm(env.guild, bob, role=high)
    assert await env.db.get_gm(11) is None


async def test_cannot_adopt_role_with_elevated_permissions(env):
    import discord

    mods = env.guild.add_role("Mods", permissions=discord.Permissions(kick_members=True))
    bob = env.guild.add_member(11, "Bob")
    with pytest.raises(ServiceError, match="elevated permissions"):
        await env.svc.register_gm(env.guild, bob, role=mods)
    assert await env.db.get_gm(11) is None


async def test_adopted_role_may_have_ordinary_permissions(env):
    import discord

    chat = env.guild.add_role("Chat", permissions=discord.Permissions(send_messages=True, attach_files=True))
    bob = env.guild.add_member(11, "Bob")
    record = await env.svc.register_gm(env.guild, bob, role=chat)
    assert record.role_id == chat.id
