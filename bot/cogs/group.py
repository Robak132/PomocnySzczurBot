"""/group commands, for GMs managing their own group."""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from ..exceptions import NotGM
from ..groups import GroupService
from ..ui import guild_of, mention_list, reply


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

    @app_commands.command(name="list", description="List the players in your group")
    async def list_players(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        players = await self.service.list_players(interaction.user.id)
        if not players:
            await reply(interaction, "Your group has no players yet.")
            return
        await reply(interaction, f"**Your group ({len(players)}):** {mention_list(players)}")
