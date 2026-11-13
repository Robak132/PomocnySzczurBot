from __future__ import annotations

import discord
from discord import app_commands

from .constants import NO_ACCESS_MESSAGE


def is_admin(member: discord.Member, admin_role_id: int | None = None) -> bool:
    """Admin = Discord Administrator permission, or the configured admin role."""
    if member.guild_permissions.administrator:
        return True
    return admin_role_id is not None and any(r.id == admin_role_id for r in member.roles)


def has_access(member: discord.abc.User, access_role_id: int | None) -> bool:
    """With no access role configured everyone may use the bot; otherwise only holders of that role."""
    if access_role_id is None:
        return True
    roles = getattr(member, "roles", None) or []
    return any(r.id == access_role_id for r in roles)


class AccessTree(app_commands.CommandTree):
    """Command tree that ignores everyone without the configured access role."""

    access_role_id: int | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if has_access(interaction.user, self.access_role_id):
            return True
        if interaction.type == discord.InteractionType.application_command and not interaction.response.is_done():
            await interaction.response.send_message(NO_ACCESS_MESSAGE, ephemeral=True)
        return False
