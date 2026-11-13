from types import SimpleNamespace

import discord

from bot.checks import AccessTree, has_access
from bot.constants import NO_ACCESS_MESSAGE


def member(*role_ids):
    return SimpleNamespace(roles=[SimpleNamespace(id=r) for r in role_ids])


class FakeResponse:
    def __init__(self):
        self.sent = []

    def is_done(self):
        return bool(self.sent)

    async def send_message(self, content, ephemeral=False):
        self.sent.append((content, ephemeral))


def interaction(user, kind=discord.InteractionType.application_command):
    return SimpleNamespace(user=user, type=kind, response=FakeResponse())


def make_tree(role_id):
    client = discord.Client(intents=discord.Intents.none())
    tree = AccessTree(client)
    tree.access_role_id = role_id
    return tree


def test_no_access_role_allows_everyone():
    assert has_access(member(), None)


def test_access_role_required():
    assert has_access(member(5, 7), 7)
    assert not has_access(member(5), 7)
    assert not has_access(SimpleNamespace(), 7)  # a User without roles (e.g. a DM)


async def test_tree_allows_role_holder():
    tree = make_tree(7)
    inter = interaction(member(7))
    assert await tree.interaction_check(inter)
    assert inter.response.sent == []


async def test_tree_refuses_and_tells_user():
    tree = make_tree(7)
    inter = interaction(member(1))
    assert not await tree.interaction_check(inter)
    assert inter.response.sent == [(NO_ACCESS_MESSAGE, True)]


async def test_tree_refuses_autocomplete_silently():
    tree = make_tree(7)
    inter = interaction(member(1), discord.InteractionType.autocomplete)
    assert not await tree.interaction_check(inter)
    assert inter.response.sent == []
