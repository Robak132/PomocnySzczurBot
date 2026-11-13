"""Gateway event listeners that keep the database in step with Discord."""
from __future__ import annotations

import logging

import discord
from discord.ext import commands

from ..services.groups import GroupService, ServiceError

log = logging.getLogger(__name__)


class EventsCog(commands.Cog):
    def __init__(self, bot: commands.Bot, service: GroupService, guild_id: int):
        self.bot = bot
        self.service = service
        self.guild_id = guild_id

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        guild = self.bot.get_guild(self.guild_id)
        if guild is None:
            log.error("The bot is not in the server with ID %s. Invite it first.", self.guild_id)
            return
        try:
            await self.service.get_gm_role(guild, create=True)
        except (ServiceError, discord.HTTPException) as exc:
            log.warning("Could not prepare the GM role: %s", exc)
        for problem in await self.service.check_hierarchy(guild):
            log.warning(problem)
        log.info("Ready as %s", self.bot.user)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        if member.guild.id == self.guild_id:
            await self.service.handle_member_remove(member)

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role) -> None:
        if role.guild.id == self.guild_id:
            await self.service.handle_role_deleted(role)

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        if after.guild.id != self.guild_id:
            return
        try:
            await self.service.handle_member_update(before, after)
        except ServiceError as exc:
            log.warning("Could not process role change for %s: %s", after.id, exc)
