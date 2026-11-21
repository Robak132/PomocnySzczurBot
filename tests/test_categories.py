from types import SimpleNamespace

import pytest
import pytest_asyncio

from bot import category_permissions as std
from bot.db import Database
from bot.exceptions import ServiceError
from bot.groups import GroupService

from .fakes import FakeGuild


@pytest_asyncio.fixture
async def env(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    await db.connect()
    guild = FakeGuild()
    guild.me.guild_permissions.administrator = True  # creating categories needs it (or every permission)
    hide = guild.add_role(std.HIDE_ROLE_NAME)
    seeker = guild.add_role(std.SEEKER_ROLE_NAME)
    yield SimpleNamespace(db=db, guild=guild, svc=GroupService(db), hide=hide, seeker=seeker)
    await db.close()


def pair(overwrite):
    allow, deny = overwrite.pair()
    return allow.value, deny.value


async def make_gm(env, id=10, name="Alice", role_name="GM-A"):
    member = env.guild.add_member(id, name)
    await env.svc.register_gm(env.guild, member)
    role, _ = await env.svc.set_group_role(env.guild, id, name=role_name)
    return member, role


async def test_setup_creates_category_with_standard_permissions(env):
    alice, role = await make_gm(env)
    result = await env.svc.setup_gm(env.guild, alice)

    category = result.category
    assert result.group_role is role and result.missing_roles == []
    assert category.name == "Alice"
    ow = category.overwrites
    assert set(ow) == {env.guild.default_role, role, alice, env.hide, env.seeker}
    assert pair(ow[env.guild.default_role]) == (0, std.EVERYONE_DENY.value)
    assert pair(ow[role]) == (std.PLAYER.value, 0)
    assert pair(ow[alice]) == (std.GM.value, 0)
    assert pair(ow[env.hide]) == (0, std.HIDE_DENY.value)
    assert pair(ow[env.seeker]) == (std.SEEKER_ALLOW.value, 0)
    assert await env.svc.get_category(env.guild, 10) is category
    assert role not in alice.roles  # the GM still doesn't hold their own group role


async def test_gm_does_not_get_manage_permissions(env):
    assert not std.GM.manage_roles and not std.PLAYER.manage_roles
    assert std.EVERYONE_DENY.manage_roles


async def test_setup_with_custom_name(env):
    bob, _ = await make_gm(env, 11, "Bob", "GM-B")
    result = await env.svc.setup_gm(env.guild, bob, name="Skaveny")
    assert result.category.name == "Skaveny"


async def test_missing_standard_roles_are_skipped_and_reported(env):
    env.guild.roles.pop(env.hide.id)
    env.guild.roles.pop(env.seeker.id)
    alice, _ = await make_gm(env)
    result = await env.svc.setup_gm(env.guild, alice)
    assert result.missing_roles == [std.HIDE_ROLE_NAME, std.SEEKER_ROLE_NAME]
    assert len(result.category.overwrites) == 3


async def test_non_gm_is_refused(env):
    pat = env.guild.add_member(20, "Pat")
    with pytest.raises(ServiceError, match="isn't a GM"):
        await env.svc.setup_gm(env.guild, pat)
    assert env.guild.categories == [] and await env.db.get_gm(20) is None


async def test_gm_without_group_role_is_refused(env):
    alice = env.guild.add_member(10, "Alice")
    await env.svc.register_gm(env.guild, alice)
    with pytest.raises(ServiceError, match="no group role yet"):
        await env.svc.setup_gm(env.guild, alice)
    assert env.guild.categories == []


async def test_gm_whose_group_role_was_deleted_is_refused(env):
    alice, role = await make_gm(env)
    env.guild.roles.pop(role.id)
    with pytest.raises(ServiceError, match="role is missing"):
        await env.svc.setup_gm(env.guild, alice)
    assert env.guild.categories == []


async def test_second_category_is_refused(env):
    alice, _ = await make_gm(env)
    await env.svc.setup_gm(env.guild, alice)
    with pytest.raises(ServiceError, match="already has the category"):
        await env.svc.setup_gm(env.guild, alice, name="Another")


async def test_duplicate_category_name_is_refused(env):
    alice, _ = await make_gm(env)
    bob, _ = await make_gm(env, 11, "Bob", "GM-B")
    await env.svc.setup_gm(env.guild, alice)
    with pytest.raises(ServiceError, match="already exists"):
        await env.svc.setup_gm(env.guild, bob, name="alice")
    assert await env.svc.get_category(env.guild, 11) is None


async def test_missing_bot_permissions_change_nothing(env):
    alice, _ = await make_gm(env)
    env.guild.me.guild_permissions.administrator = False
    with pytest.raises(ServiceError, match="need Administrator"):
        await env.svc.setup_gm(env.guild, alice)
    assert env.guild.categories == []


async def test_failed_category_creation_stores_nothing(env):
    alice, _ = await make_gm(env)
    env.guild.fail_category = True
    with pytest.raises(ServiceError, match="refused to create the category"):
        await env.svc.setup_gm(env.guild, alice)
    assert await env.svc.get_category(env.guild, 10) is None
    assert await env.db.get_gm(10) is not None  # still a GM, nothing else changed


async def test_changing_group_role_moves_category_overwrite_and_resyncs_channels(env):
    alice, old = await make_gm(env)
    category = (await env.svc.setup_gm(env.guild, alice)).category
    synced = category.add_channel("sesja")
    custom = category.add_channel("notatki-gm", synced=False)
    custom_before = dict(custom.overwrites)

    new, _ = await env.svc.set_group_role(env.guild, 10, name="Dragons")
    assert old not in category.overwrites
    assert pair(category.overwrites[new]) == (std.PLAYER.value, 0)
    assert synced.overwrites == category.overwrites  # synced channel follows the category
    assert custom.overwrites == custom_before  # a channel the GM changed is left alone


async def test_missing_bot_permissions_lists_names():
    perms = SimpleNamespace(administrator=False, manage_channels=True)
    missing = std.missing_bot_permissions(perms)
    assert "Manage Channels" not in missing and "View Channels" in missing
    assert len(missing) == 38
    assert std.missing_bot_permissions(SimpleNamespace(administrator=True)) == []
