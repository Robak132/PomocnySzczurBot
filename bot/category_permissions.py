"""The standard permissions of a GM category on KRT.

Taken from the GM categories on the server (see the "KRT - standard GM category
permissions" doc). Every GM category gets these five overwrites on the category
itself; its channels stay synced to it.
"""
from __future__ import annotations

import discord

# Roles the standard refers to by name. If a role is missing on the server, its
# overwrite is skipped and the admin is told.
HIDE_ROLE_NAME = "UkryjWszystko"
SEEKER_ROLE_NAME = "Poszukiwacz przygód"

# What a player (holder of the group role) may do in the category.
PLAYER = discord.Permissions(
    view_channel=True,
    create_instant_invite=True,
    send_messages=True,
    send_messages_in_threads=True,
    create_public_threads=True,
    create_private_threads=True,
    embed_links=True,
    attach_files=True,
    add_reactions=True,
    use_external_emojis=True,
    use_external_stickers=True,
    mention_everyone=True,
    read_message_history=True,
    send_voice_messages=True,
    send_polls=True,
    connect=True,
    speak=True,
    stream=True,
    use_soundboard=True,
    use_external_sounds=True,
    use_voice_activation=True,
    set_voice_channel_status=True,
    use_application_commands=True,
    use_embedded_activities=True,
)

# What the GM gets on top of PLAYER, as a member overwrite. Manage Permissions
# is deliberately left out: with it a GM could let anyone into the category.
GM_EXTRA = discord.Permissions(
    manage_channels=True,
    manage_messages=True,
    pin_messages=True,
    bypass_slowmode=True,
    manage_threads=True,
    move_members=True,
    use_external_apps=True,
    create_events=True,
    manage_events=True,
)
GM = PLAYER | GM_EXTRA

# Everything @everyone is denied in the category.
EVERYONE_DENY = GM | discord.Permissions(
    manage_roles=True,  # "Manage Permissions" on a channel
    manage_webhooks=True,
    send_tts_messages=True,
    priority_speaker=True,
    mute_members=True,
    deafen_members=True,
)

HIDE_DENY = discord.Permissions(view_channel=True, read_message_history=True, connect=True)
SEEKER_ALLOW = discord.Permissions(read_message_history=True)

# The bot can only allow or deny permissions it has itself.
REQUIRED_BOT_PERMISSIONS = EVERYONE_DENY


def allow(perms: discord.Permissions) -> discord.PermissionOverwrite:
    return discord.PermissionOverwrite.from_pair(perms, discord.Permissions.none())


def deny(perms: discord.Permissions) -> discord.PermissionOverwrite:
    return discord.PermissionOverwrite.from_pair(discord.Permissions.none(), perms)


def player_overwrite() -> discord.PermissionOverwrite:
    return allow(PLAYER)


def category_overwrites(
    everyone: discord.Role,
    gm: discord.Member,
    group_role: discord.Role,
    hide_role: discord.Role | None = None,
    seeker_role: discord.Role | None = None,
) -> dict:
    """The five standard overwrites for a GM's category."""
    overwrites = {
        everyone: deny(EVERYONE_DENY),
        group_role: allow(PLAYER),
        gm: allow(GM),
    }
    if hide_role is not None:
        overwrites[hide_role] = deny(HIDE_DENY)
    if seeker_role is not None:
        overwrites[seeker_role] = allow(SEEKER_ALLOW)
    return overwrites


# Discord's English names for the permissions above, keyed by discord.py's
# canonical flag names (which is what iterating a Permissions object yields).
LABELS = {
    "create_instant_invite": "Create Invite",
    "manage_channels": "Manage Channels",
    "add_reactions": "Add Reactions",
    "priority_speaker": "Priority Speaker",
    "stream": "Video",
    "read_messages": "View Channels",
    "send_messages": "Send Messages",
    "send_tts_messages": "Send Text-to-Speech Messages",
    "manage_messages": "Manage Messages",
    "embed_links": "Embed Links",
    "attach_files": "Attach Files",
    "read_message_history": "Read Message History",
    "mention_everyone": "Mention @everyone, @here and All Roles",
    "external_emojis": "Use External Emoji",
    "connect": "Connect",
    "speak": "Speak",
    "mute_members": "Mute Members",
    "deafen_members": "Deafen Members",
    "move_members": "Move Members",
    "use_voice_activation": "Use Voice Activity",
    "manage_roles": "Manage Roles",
    "manage_webhooks": "Manage Webhooks",
    "use_application_commands": "Use Application Commands",
    "manage_events": "Manage Events",
    "manage_threads": "Manage Threads",
    "create_public_threads": "Create Public Threads",
    "create_private_threads": "Create Private Threads",
    "external_stickers": "Use External Stickers",
    "send_messages_in_threads": "Send Messages in Threads",
    "use_embedded_activities": "Use Activities",
    "use_soundboard": "Use Soundboard",
    "create_events": "Create Events",
    "use_external_sounds": "Use External Sounds",
    "send_voice_messages": "Send Voice Messages",
    "set_voice_channel_status": "Set Voice Channel Status",
    "send_polls": "Create Polls",
    "use_external_apps": "Use External Apps",
    "pin_messages": "Pin Messages",
    "bypass_slowmode": "Bypass Slowmode",
}


def missing_bot_permissions(perms) -> list[str]:
    """Discord's names of the permissions the bot lacks to apply the standard (empty with Administrator)."""
    if getattr(perms, "administrator", False):
        return []
    return [
        LABELS.get(name, name.replace("_", " ").title())
        for name, value in REQUIRED_BOT_PERMISSIONS
        if value and not getattr(perms, name, False)
    ]
