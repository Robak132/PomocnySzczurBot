"""Constants shared across the bot."""
from __future__ import annotations

import discord

# -- Access ----------------------------------------------------------------
NO_ACCESS_MESSAGE = "You don't have access to this bot."

# -- Groups and roles ------------------------------------------------------
GM_ROLE_KEY = "gm_role_id"

MAX_ROLE_NAME = 100


ELEVATED_PERMISSIONS = (
    "administrator",
    "manage_guild",
    "manage_roles",
    "manage_channels",
    "manage_webhooks",
    "manage_messages",
    "manage_threads",
    "manage_nicknames",
    "manage_expressions",
    "manage_events",
    "kick_members",
    "ban_members",
    "moderate_members",
    "mention_everyone",
    "view_audit_log",
)

# -- Messages --------------------------------------------------------------
NO_MENTIONS = discord.AllowedMentions.none()

# Discord's limit is 2000 characters; leave room for the ellipsis and markup.
MAX_MESSAGE_LEN = 1900
