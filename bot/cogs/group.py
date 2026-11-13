"""/group commands, for GMs managing their own group."""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from ..exceptions import NotGM
from ..groups import GroupService
from ..exceptions import ServiceError
from ..ui import ConfirmView, guild_of, mention_line, reply


class GroupCog(commands.GroupCog, group_name="group", group_description="Manage your group of players"):
    def __init__(self, bot: commands.Bot, service: GroupService):
        self.bot = bot
        self.service = service
        super().__init__()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.guild is None:
            raise NotGM("Use this command in the server.")
        if not await self.service.is_gm(interaction.user.id):
            raise NotGM("You are not a GM.")
        await self.service.get_gm_role(interaction.guild)  # fails clearly if the GM role was deleted
        return True

    @app_commands.command(name="add", description="Add a player to your group")
    @app_commands.describe(user="Member to add")
    async def add(self, interaction: discord.Interaction, user: discord.Member) -> None:
        await interaction.response.defer(ephemeral=True)
        await self.service.add_player(guild_of(interaction), interaction.user.id, user, actor_id=interaction.user.id)
        await reply(interaction, f"Added {user.mention} to your group.")

    @app_commands.command(name="remove", description="Remove a player from your group")
    @app_commands.describe(user="Member to remove")
    async def remove(self, interaction: discord.Interaction, user: discord.Member) -> None:
        await interaction.response.defer(ephemeral=True)
        await self.service.remove_player(guild_of(interaction), interaction.user.id, user, actor_id=interaction.user.id)
        await reply(interaction, f"Removed {user.mention} from your group.")

    @app_commands.command(name="remove-all", description="Remove every player from your group")
    async def remove_all(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        count = len(await self.service.list_players(interaction.user.id))
        if count == 0:
            await reply(interaction, "Your group has no players.")
            return
        view = ConfirmView(interaction.user.id)
        await interaction.followup.send(
            f"Remove all {count} players from your group? They will all lose the group role.",
            view=view,
            ephemeral=True,
        )
        await view.wait()
        if view.value is None:
            await interaction.edit_original_response(content="Timed out. Nothing was changed.", view=None)
            return
        if not view.value:
            return
        removed = await self.service.remove_all_players(
            guild_of(interaction), interaction.user.id, actor_id=interaction.user.id
        )
        await reply(interaction, f"Removed {removed} player(s) from your group.")

    @app_commands.command(name="list", description="List the players in your group")
    async def list_players(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        players = await self.service.list_players(interaction.user.id)
        if not players:
            await reply(interaction, "Your group has no players yet.")
            return
        await reply(interaction, f"**Your group ({len(players)}):** {mention_line(players)}")


class PlayerCog(commands.GroupCog, group_name="player", group_description="Manage your own membership"):
    def __init__(self, bot: commands.Bot, service: GroupService):
        self.bot = bot
        self.service = service
        super().__init__()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.guild is None:
            raise NotGM("Use this command in the server.")
        return True

    @app_commands.command(name="remove", description="Leave a GM's group")
    @app_commands.describe(gm="GM whose group you want to leave")
    async def remove(self, interaction: discord.Interaction, gm: str) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            gm_id = int(gm)
        except ValueError:
            raise ServiceError("Pick a GM from the suggestions list.") from None
        guild = guild_of(interaction)
        if gm_id not in await self.service.groups_of(interaction.user.id):
            raise ServiceError("You are not in that GM's group.")
        view = ConfirmView(interaction.user.id)
        await interaction.followup.send(
            f"Leave <@{gm_id}>'s group? You will lose the group role, and <@{gm_id}> will be notified "
            "with a ping in this channel.",
            view=view,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        await view.wait()
        if view.value is None:
            await interaction.edit_original_response(content="Timed out. Nothing was changed.", view=None)
            return
        if not view.value:
            return
        await self.service.leave_group(guild, gm_id, interaction.user)
        await interaction.followup.send(
            f"{interaction.user.mention} left <@{gm_id}>'s group.",
            ephemeral=False,
            allowed_mentions=discord.AllowedMentions(users=[discord.Object(id=gm_id)]),
        )

    @remove.autocomplete("gm")
    async def _gm_autocomplete(self, interaction: discord.Interaction, current: str):
        choices = []
        for gm_id in await self.service.groups_of(interaction.user.id):
            member = interaction.guild.get_member(gm_id) if interaction.guild else None
            label = member.display_name if member else str(gm_id)
            if current.lower() in label.lower():
                choices.append(app_commands.Choice(name=label[:100], value=str(gm_id)))
        return choices[:25]
