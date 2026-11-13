from __future__ import annotations

import discord

from .constants import MAX_MESSAGE_LEN, NO_MENTIONS
from .exceptions import ServiceError


def _clip(text: str) -> str:
    return text if len(text) <= MAX_MESSAGE_LEN else text[: MAX_MESSAGE_LEN - 1] + "…"


async def reply(interaction: discord.Interaction, content: str, **kwargs) -> None:
    """Send an ephemeral follow-up (the command must have deferred first)."""
    await interaction.followup.send(_clip(content), ephemeral=True, allowed_mentions=NO_MENTIONS, **kwargs)


async def respond(interaction: discord.Interaction, content: str) -> None:
    """Send an ephemeral message whether the command already deferred."""
    if interaction.response.is_done():
        await reply(interaction, content)
    else:
        await interaction.response.send_message(_clip(content), ephemeral=True, allowed_mentions=NO_MENTIONS)


def guild_of(interaction: discord.Interaction) -> discord.Guild:
    """The server a command was used in. Commands don't work in DMs."""
    if interaction.guild is None:
        raise ServiceError("Use this command in the server.")
    return interaction.guild


def mention_list(user_ids: list[int]) -> str:
    return ", ".join(f"<@{uid}>" for uid in user_ids)


class ConfirmView(discord.ui.View):
    """Confirm / Cancel buttons that only the invoking user can press."""

    def __init__(self, user_id: int, timeout: float = 60):
        super().__init__(timeout=timeout)
        self.user_id = user_id
        self.value: bool | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This confirmation isn't for you.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.value = True
        self.stop()
        await interaction.response.edit_message(content="Confirmed.", view=None)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.value = False
        self.stop()
        await interaction.response.edit_message(content="Cancelled.", view=None)
