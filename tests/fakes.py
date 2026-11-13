"""Minimal stand-ins for discord.py objects, enough for the service layer."""
from __future__ import annotations

import itertools
from functools import total_ordering
from types import SimpleNamespace

_ids = itertools.count(1000)


@total_ordering
class FakeRole:
    def __init__(self, guild, id, name, position, colour=None, managed=False):
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


class FakeGuild:
    def __init__(self):
        self.id = 1
        self.roles = {}
        self.members = {}
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

    async def create_role(self, **kwargs):
        self._position += 1
        role = FakeRole(self, next(_ids), kwargs["name"], self._position, kwargs.get("colour"))
        self.roles[role.id] = role
        return role

    def add_member(self, id, name, bot=False, admin=False):
        member = FakeMember(self, id, name, bot=bot, admin=admin)
        self.members[id] = member
        return member

    def remove_member(self, id):
        return self.members.pop(id)
