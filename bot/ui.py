from __future__ import annotations

import discord

from .constants import MAX_MESSAGE_LEN, NO_MENTIONS
from .exceptions import ServiceError


def _clip(text: str) -> str:
    return text if len(text) <= MAX_MESSAGE_LEN else text[: MAX_MESSAGE_LEN - 1] + "…"


async def reply(interaction: discord.Interaction, content: str, **kwargs) -> None:
    """Send an ephemeral follow-up (the command must have deferred first)."""
    await interaction.followup.send(_clip(content), ephemeral=True, allowed_mentions=NO_MENTIONS, **kwargs)


def chunk_lines(lines: list[str], limit: int = MAX_MESSAGE_LEN) -> list[str]:
    """Join lines into as few messages as possible, never splitting a line between two messages."""
    chunks: list[str] = []
    current = ""
    for line in lines:
        line = _clip(line)
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > limit and current:
            chunks.append(current)
            candidate = line
        current = candidate
    if current:
        chunks.append(current)
    return chunks


async def reply_lines(interaction: discord.Interaction, lines: list[str]) -> None:
    """Send a long list as one or more ephemeral follow-ups (the command must have deferred first)."""
    for chunk in chunk_lines(lines):
        await reply(interaction, chunk)


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


def mention_line(user_ids: list[int]) -> str:
    """All mentions on one line, comma-separated."""
    return ", ".join(f"<@{uid}>" for uid in user_ids)


def mention_list(user_ids: list[int]) -> str:
    """One mention per line."""
    return "\n".join(f"<@{uid}>" for uid in user_ids)


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
