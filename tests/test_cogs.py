import discord
from discord.ext import commands

from bot.cogs.admin import AdminCog
from bot.cogs.group import GroupCog, PlayerCog
from bot.db import Database
from bot.groups import GroupService

EXPECTED = {
    "group add",
    "group remove",
    "group list",
    "group remove-all",
    "player remove",
    "admin gm add",
    "admin gm set-group-role",
    "admin gm remove",
    "admin list",
    "admin set-role",
    "admin player add",
    "admin player remove",
    "admin gm list",
    "admin sync",
}


async def test_command_tree_matches_spec(tmp_path):
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.default())
    service = GroupService(Database(str(tmp_path / "x.db")))
    group_cog = GroupCog(bot, service)
    admin_cog = AdminCog(bot, service)
    await bot.add_cog(group_cog)
    await bot.add_cog(admin_cog)
    await bot.add_cog(PlayerCog(bot, service))

    names = {
        c.qualified_name for c in bot.tree.walk_commands() if isinstance(c, discord.app_commands.Command)
    }
    assert names == EXPECTED

    admin = bot.tree.get_command("admin")
    assert admin.default_permissions is not None and admin.default_permissions.administrator
    assert bot.tree.get_command("group").default_permissions is None

    # Subcommands are bound to the cog instance, so `self` works at call time.
    add = admin.get_command("gm").get_command("add")
    assert add.binding is admin_cog
    assert admin.get_command("player").get_command("add").binding is admin_cog
    assert bot.tree.get_command("group").get_command("add").binding is group_cog

    # The `gm` argument of /admin player * has autocomplete.
    for group, name in (("player", "add"), ("player", "remove"), ("gm", "list")):
        command = admin.get_command(group).get_command(name)
        assert command._params["gm"].autocomplete is not None
