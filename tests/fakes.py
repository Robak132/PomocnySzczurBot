"""Minimal stand-ins for discord.py objects, enough for the service layer."""
from __future__ import annotations

import itertools
from functools import total_ordering
from types import SimpleNamespace

import discord

_ids = itertools.count(1000)


@total_ordering
class FakeRole:
    def __init__(self, guild, id, name, position, colour=None, managed=False, permissions=None):
        self.permissions = permissions or discord.Permissions.none()
        self.guild = guild
        self.id = id
        self.name = name
        self.position = position
        self.colour = colour
        self.managed = managed

    def __eq__(self, other):
        return isinstance(other, FakeRole) and self.id == other.id

    def __hash__(self):
        return hash(self.id)

    def __lt__(self, other):
        return self.position < other.position

    def is_default(self):
        return self.id == self.guild.id

    @property
    def members(self):
        return [m for m in self.guild.members.values() if self in m.roles]

    async def edit(self, reason=None, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)

    async def delete(self, reason=None):
        self.guild.roles.pop(self.id, None)
        for member in self.guild.members.values():
            if self in member.roles:
                member.roles.remove(self)


class FakeMember:
    def __init__(self, guild, id, name, bot=False, admin=False):
        self.guild = guild
        self.id = id
        self.display_name = name
        self.bot = bot
        self.roles = []
        self.guild_permissions = SimpleNamespace(administrator=admin, manage_roles=False)

    @property
    def mention(self):
        return f"<@{self.id}>"

    def __str__(self):
        return self.display_name

    async def add_roles(self, *roles, reason=None):
        for role in roles:
            if role not in self.roles:
                self.roles.append(role)

    async def remove_roles(self, *roles, reason=None):
        for role in roles:
            if role in self.roles:
                self.roles.remove(role)


class _Roles(dict):
    """Roles keyed by ID; iterating yields the roles themselves, like ``Guild.roles``."""

    def __iter__(self):
        return iter(list(self.values()))


class FakeChannel:
    def __init__(self, guild, id, name, category=None, synced=True):
        self.guild = guild
        self.id = id
        self.name = name
        self.category = category
        self.overwrites = dict(category.overwrites) if category else {}
        self._synced = synced

    @property
    def permissions_synced(self):
        return self._synced and self.category is not None and self.overwrites == self.category.overwrites

    async def edit(self, sync_permissions=False, reason=None, **kwargs):
        if sync_permissions and self.category is not None:
            self.overwrites = dict(self.category.overwrites)
            self._synced = True


class FakeCategory:
    def __init__(self, guild, id, name, overwrites):
        self.guild = guild
        self.id = id
        self.name = name
        self.overwrites = dict(overwrites)
        self.channels = []

    async def set_permissions(self, target, *, overwrite=None, reason=None):
        if overwrite is None:
            self.overwrites.pop(target, None)
        else:
            self.overwrites[target] = overwrite

    async def delete(self, reason=None):
        self.guild.channels.pop(self.id, None)

    def add_channel(self, name, synced=True):
        channel = FakeChannel(self.guild, next(_ids), name, category=self, synced=synced)
        self.channels.append(channel)
        self.guild.channels[channel.id] = channel
        return channel


class FakeGuild:
    def __init__(self):
        self.id = 1
        self.roles = _Roles()
        self.members = {}
        self.channels = {}
        self.default_role = FakeRole(self, self.id, "@everyone", 0)
        self.fail_category = False
        self._position = 1
        self.me = FakeMember(self, 999, "Bot", bot=True)
        self.me.guild_permissions.manage_roles = True
        self.me.top_role = FakeRole(self, 998, "BotRole", 100)
        self.roles[998] = self.me.top_role
        self.members[999] = self.me

    def get_role(self, role_id):
        return self.roles.get(role_id)

    def get_member(self, member_id):
        return self.members.get(member_id)

    def get_channel(self, channel_id):
        return self.channels.get(channel_id)

    @property
    def categories(self):
        return [c for c in self.channels.values() if isinstance(c, FakeCategory)]

    async def create_category(self, name, *, overwrites=None, reason=None, position=None):
        if self.fail_category:
            raise discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "Missing Permissions")
        category = FakeCategory(self, next(_ids), name, overwrites or {})
        self.channels[category.id] = category
        return category

    async def create_role(self, **kwargs):
        self._position += 1
        role = FakeRole(self, next(_ids), kwargs["name"], self._position, kwargs.get("colour"))
        self.roles[role.id] = role
        return role

    def add_role(self, name, position=None, permissions=None, managed=False):
        self._position += 1
        role = FakeRole(self, next(_ids), name, position or self._position, permissions=permissions, managed=managed)
        self.roles[role.id] = role
        return role

    def add_member(self, id, name, bot=False, admin=False):
        member = FakeMember(self, id, name, bot=bot, admin=admin)
        self.members[id] = member
        return member

    def remove_member(self, id):
        return self.members.pop(id)
