"""/admin commands, for managing GMs and every group."""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from ..checks import is_admin
from ..exceptions import NotAdmin, ServiceError
from ..groups import GroupService
from ..ui import ConfirmView, guild_of, mention_list, reply


@app_commands.default_permissions(administrator=True)
class AdminCog(commands.GroupCog, group_name="admin", group_description="Server administration"):
    gm = app_commands.Group(name="gm", description="Manage GMs")
    gm_role = app_commands.Group(name="gm-role", description="Choose or create the GM role")
    player = app_commands.Group(name="player", description="Manage the players of any GM")

    def __init__(self, bot: commands.Bot, service: GroupService):
        self.bot = bot
        self.service = service
        super().__init__()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            raise NotAdmin("Use this command in the server.")
        if not is_admin(interaction.user, self.service.admin_role_id):
            raise NotAdmin("Admins only.")
        return True

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _gm_id(value: str) -> int:
        try:
            return int(value)
        except ValueError:
            raise ServiceError("Pick a GM from the suggestions list.") from None

    async def _gm_autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        choices = []
        for gm in await self.service.db.list_gms():
            member = interaction.guild.get_member(gm.user_id) if interaction.guild else None
            label = member.display_name if member else str(gm.user_id)
            if current.lower() in label.lower():
                choices.append(app_commands.Choice(name=label[:100], value=str(gm.user_id)))
        return choices[:25]

    # ------------------------------------------------------------------
    # /admin gm ...
    # ------------------------------------------------------------------
    @gm.command(name="add", description="Make a member a GM and create or assign their group role")
    @app_commands.describe(
        user="Member to promote",
        name="Name for the new group role (default: their display name). Not allowed with role",
        role="Use this existing role as the group role; its current holders become players",
    )
    async def gm_add(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        name: str | None = None,
        role: discord.Role | None = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        record = await self.service.register_gm(
            guild_of(interaction), user, name=name, role=role, actor_id=interaction.user.id
        )
        group_role = guild_of(interaction).get_role(record.role_id)
        role_name = group_role.name if group_role else str(record.role_id)
        message = f"{user.mention} is now a GM. Their group role is **{role_name}**."
        if role is not None:
            players = await self.service.db.count_members(user.id)
            message += f" It now has {players} player(s), including everyone who already held the role."
        await reply(interaction, message)

    @gm.command(name="remove", description="Remove a GM and, by default, delete their group role")
    @app_commands.describe(user="GM to remove", keep_role="Keep the group role and its players")
    async def gm_remove(self, interaction: discord.Interaction, user: discord.Member, keep_role: bool = False) -> None:
        await interaction.response.defer(ephemeral=True)
        if not await self.service.is_gm(user.id):
            raise ServiceError(f"{user.display_name} is not a GM.")
        consequence = (
            "Their group role will be kept."
            if keep_role
            else "Their group role will be **deleted**, so all their players lose it."
        )
        view = ConfirmView(interaction.user.id)
        await interaction.followup.send(
            f"Remove {user.mention} as a GM? {consequence}",
            view=view,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        await view.wait()
        if view.value is None:
            await interaction.edit_original_response(content="Timed out. Nothing was changed.", view=None)
            return
        if not view.value:
            return
        await self.service.unregister_gm(
            guild_of(interaction), user.id, keep_role=keep_role, actor_id=interaction.user.id
        )
        await reply(interaction, f"{user.mention} is no longer a GM.")

    @gm.command(name="list", description="List all GMs")
    async def gm_list(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        gms = await self.service.db.list_gms()
        if not gms:
            await reply(interaction, "There are no GMs yet.")
            return
        lines = []
        for gm in gms:
            role = guild_of(interaction).get_role(gm.role_id)
            count = await self.service.db.count_members(gm.user_id)
            role_text = f"**{role.name}**" if role else "⚠ role missing (run /admin sync)"
            lines.append(f"<@{gm.user_id}> · {role_text} · {count} player(s)")
        await reply(interaction, "\n".join(lines))

    # ------------------------------------------------------------------
    # /admin gm-role ...
    # ------------------------------------------------------------------
    @gm_role.command(name="set", description="Use an existing role as the GM role")
    @app_commands.describe(role="The role that marks GMs")
    async def gm_role_set(self, interaction: discord.Interaction, role: discord.Role) -> None:
        await interaction.response.defer(ephemeral=True)
        registered = await self.service.set_gm_role(guild_of(interaction), role, actor_id=interaction.user.id)
        text = f"**{role.name}** is now the GM role."
        if registered:
            text += f" Registered {len(registered)} existing holder(s) as GMs: {mention_list([m.id for m in registered])}."
        await reply(interaction, text)

    @gm_role.command(name="create", description="Create a new GM role and use it")
    @app_commands.describe(name="Role name (default: GM)")
    async def gm_role_create(self, interaction: discord.Interaction, name: str = "GM") -> None:
        await interaction.response.defer(ephemeral=True)
        role = await self.service.create_gm_role(guild_of(interaction), name, actor_id=interaction.user.id)
        await reply(interaction, f"Created the GM role **{role.name}**.")

    # ------------------------------------------------------------------
    # /admin player ...
    # ------------------------------------------------------------------
    @player.command(name="add", description="Add a player to a GM's group")
    @app_commands.describe(gm="The GM whose group to add to", user="Member to add")
    async def player_add(self, interaction: discord.Interaction, gm: str, user: discord.Member) -> None:
        await interaction.response.defer(ephemeral=True)
        gm_id = self._gm_id(gm)
        await self.service.add_player(guild_of(interaction), gm_id, user, actor_id=interaction.user.id)
        await reply(interaction, f"Added {user.mention} to <@{gm_id}>'s group.")

    @player.command(name="remove", description="Remove a player from a GM's group")
    @app_commands.describe(gm="The GM whose group to remove from", user="Member to remove")
    async def player_remove(self, interaction: discord.Interaction, gm: str, user: discord.Member) -> None:
        await interaction.response.defer(ephemeral=True)
        gm_id = self._gm_id(gm)
        await self.service.remove_player(guild_of(interaction), gm_id, user, actor_id=interaction.user.id)
        await reply(interaction, f"Removed {user.mention} from <@{gm_id}>'s group.")

    @player.command(name="list", description="List the players of a GM's group")
    @app_commands.describe(gm="The GM whose group to list")
    async def player_list(self, interaction: discord.Interaction, gm: str) -> None:
        await interaction.response.defer(ephemeral=True)
        gm_id = self._gm_id(gm)
        players = await self.service.list_players(gm_id)
        if not players:
            await reply(interaction, f"<@{gm_id}>'s group has no players.")
            return
        await reply(interaction, f"**<@{gm_id}>'s group ({len(players)}):** {mention_list(players)}")

    @player_add.autocomplete("gm")
    async def _player_add_gm(self, interaction: discord.Interaction, current: str):
        return await self._gm_autocomplete(interaction, current)

    @player_remove.autocomplete("gm")
    async def _player_remove_gm(self, interaction: discord.Interaction, current: str):
        return await self._gm_autocomplete(interaction, current)

    @player_list.autocomplete("gm")
    async def _player_list_gm(self, interaction: discord.Interaction, current: str):
        return await self._gm_autocomplete(interaction, current)

    # ------------------------------------------------------------------
    # /admin sync
    # ------------------------------------------------------------------
    @app_commands.command(name="sync", description="Reconcile the bot's records with Discord's roles")
    async def sync(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        report = await self.service.sync(guild_of(interaction), actor_id=interaction.user.id)
        problems = await self.service.check_hierarchy(guild_of(interaction))
        parts = ["\n".join(f"• {line}" for line in report) if report else "Everything is in sync."]
        if problems:
            parts.append("**Problems:**\n" + "\n".join(f"• {p}" for p in problems))
        await reply(interaction, "\n\n".join(parts))
