"""Business logic shared by the GM and admin command cogs.

All Discord changes go through this module so both cogs share the same
validation. Every user-facing failure is raised as ServiceError.
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

import discord

from .checks import is_admin
from .constants import (
    ELEVATED_PERMISSIONS,
    GM_ROLE_KEY,
    MAX_ROLE_NAME,
)
from .db import Database, GmRecord
from .exceptions import ServiceError

log = logging.getLogger(__name__)


AuditHook = Callable[[int | None, str, int | None, int | None, str | None], Awaitable[None]]


async def _safe(coro, what: str):
    """Await a Discord call, turning API failures into user-facing errors."""
    try:
        return await coro
    except discord.Forbidden:
        raise ServiceError(
            f"Discord refused to {what}. Check my Manage Roles permission and that my role is above the roles I manage."
        ) from None
    except discord.HTTPException as exc:
        log.warning("Discord API error while trying to %s: %s", what, exc)
        raise ServiceError(f"Discord could not {what}. Try again in a moment.") from exc


class GroupService:
    def __init__(
        self,
        db: Database,
        admin_role_id: int | None = None,
        on_audit: AuditHook | None = None,
    ):
        self.db = db
        self.admin_role_id = admin_role_id
        self.on_audit = on_audit

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    async def audit(
        self,
        actor_id: int | None,
        action: str,
        target_id: int | None = None,
        gm_user_id: int | None = None,
        detail: str | None = None,
    ) -> None:
        await self.db.add_audit(actor_id, action, target_id, gm_user_id, detail)
        if self.on_audit:
            try:
                await self.on_audit(actor_id, action, target_id, gm_user_id, detail)
            except discord.HTTPException:  # logging must never break a command
                log.exception("Audit hook failed")

    async def is_gm(self, user_id: int) -> bool:
        return await self.db.get_gm(user_id) is not None

    async def _require_gm(self, gm_id: int) -> GmRecord:
        gm = await self.db.get_gm(gm_id)
        if gm is None:
            raise ServiceError("That user is not a GM.")
        return gm

    @staticmethod
    def _group_role(guild: discord.Guild, gm: GmRecord) -> discord.Role:
        if gm.role_id is None:
            raise ServiceError(
                f"<@{gm.user_id}> has no group role yet. An admin can set one with `/admin gm set-group-role`."
            )
        role = guild.get_role(gm.role_id)
        if role is None:
            raise ServiceError("This group's role is missing. An admin should run `/admin sync`.")
        return role

    @staticmethod
    def _check_manageable(guild: discord.Guild, role: discord.Role) -> None:
        me = guild.me
        if not me.guild_permissions.manage_roles:
            raise ServiceError("I need the Manage Roles permission.")
        if role >= me.top_role:
            raise ServiceError(
                f"I can't manage the role **{role.name}** because it is above my highest role. "
                "Move my role higher in Server Settings > Roles."
            )

    async def _peek_gm_role(self, guild: discord.Guild) -> discord.Role | None:
        raw = await self.db.get_setting(GM_ROLE_KEY)
        return guild.get_role(int(raw)) if raw else None

    async def _managed_names(self, guild: discord.Guild, exclude_gm: int | None = None) -> set[str]:
        names: set[str] = set()
        for gm in await self.db.list_gms():
            if gm.user_id == exclude_gm or gm.role_id is None:
                continue
            role = guild.get_role(gm.role_id)
            if role:
                names.add(role.name.casefold())
        gm_role = await self._peek_gm_role(guild)
        if gm_role:
            names.add(gm_role.name.casefold())
        return names

    async def _validate_name(self, guild: discord.Guild, name: str, exclude_gm: int | None = None) -> str:
        name = name.strip()
        if not 1 <= len(name) <= MAX_ROLE_NAME:
            raise ServiceError(f"The name must be 1-{MAX_ROLE_NAME} characters.")
        if name.casefold() in await self._managed_names(guild, exclude_gm):
            raise ServiceError(f"A group or GM role named **{name}** already exists.")
        return name

    async def _pick_name(
        self, guild: discord.Guild, name: str | None, member: discord.Member, exclude_gm: int | None = None
    ) -> str:
        if name:
            return await self._validate_name(guild, name, exclude_gm)
        base = (member.display_name or "Group").strip()[:90] or "Group"
        taken = await self._managed_names(guild, exclude_gm)
        candidate, n = base, 1
        while candidate.casefold() in taken:
            n += 1
            candidate = f"{base} {n}"
        return candidate

    async def _grant_to_players(self, guild: discord.Guild, gm_id: int, role: discord.Role) -> None:
        for pid in await self.db.list_members(gm_id):
            member = guild.get_member(pid)
            if member is None:
                await self.db.remove_member(gm_id, pid)
            elif role not in member.roles:
                await _safe(member.add_roles(role, reason="Restoring group role"), "restore the group role")

    # ------------------------------------------------------------------
    # GM role
    # ------------------------------------------------------------------
    async def get_gm_role(self, guild: discord.Guild, *, create: bool = False) -> discord.Role:
        raw = await self.db.get_setting(GM_ROLE_KEY)
        if raw:
            role = guild.get_role(int(raw))
            if role is None:
                raise ServiceError(
                    "The GM role no longer exists. An admin must run `/admin set-role`."
                )
            return role
        if create:
            return await self.create_gm_role(guild)
        raise ServiceError("No GM role is configured yet. An admin must run `/admin gm add` or `/admin set-role`.")

    async def create_gm_role(self, guild: discord.Guild, name: str = "GM", actor_id: int | None = None) -> discord.Role:
        name = name.strip()
        if not 1 <= len(name) <= MAX_ROLE_NAME:
            raise ServiceError(f"The name must be 1-{MAX_ROLE_NAME} characters.")
        if not guild.me.guild_permissions.manage_roles:
            raise ServiceError("I need the Manage Roles permission.")
        role = await _safe(
            guild.create_role(
                name=name,
                permissions=discord.Permissions.none(),
                hoist=False,
                mentionable=False,
                reason="GM role created by the permission bot",
            ),
            "create the GM role",
        )
        await self.db.set_setting(GM_ROLE_KEY, str(role.id))
        await self.audit(actor_id, "gm_role_created", detail=f"{role.name} ({role.id})")
        return role

    async def set_gm_role(
        self, guild: discord.Guild, role: discord.Role, actor_id: int | None = None
    ) -> list[discord.Member]:
        """Designate an existing role as the GM role and register its holders."""
        if role.is_default():
            raise ServiceError("@everyone can't be the GM role.")
        if role.managed:
            raise ServiceError("That role is managed by an integration and can't be used.")
        self._check_manageable(guild, role)
        await self.db.set_setting(GM_ROLE_KEY, str(role.id))
        await self.audit(actor_id, "gm_role_set", detail=f"{role.name} ({role.id})")
        registered = []
        for member in role.members:
            if member.bot or await self.db.get_gm(member.id):
                continue
            await self.register_gm(guild, member, actor_id=actor_id, assign_gm_role=False)
            registered.append(member)
        return registered

    async def _check_adoptable(self, guild: discord.Guild, role: discord.Role, gm_id: int) -> None:
        """Validate an existing role before it becomes a GM's group role."""
        if role.is_default():
            raise ServiceError("@everyone can't be a group role.")
        if role.managed:
            raise ServiceError("That role is managed by an integration and can't be used.")
        gm_role = await self._peek_gm_role(guild)
        if gm_role is not None and role.id == gm_role.id:
            raise ServiceError("The GM role can't also be a group role.")
        owner = await self.db.get_gm_by_role(role.id, include_inactive=True)
        if owner is not None and owner.user_id != gm_id:
            raise ServiceError(f"**{role.name}** is already the group role of <@{owner.user_id}>.")
        risky = [p for p in ELEVATED_PERMISSIONS if getattr(role.permissions, p, False)]
        if risky:
            raise ServiceError(
                f"**{role.name}** has elevated permissions ({', '.join(risky)}). "
                "Its GM could hand them out to players, so remove them from the role first."
            )
        self._check_manageable(guild, role)

    # ------------------------------------------------------------------
    # GM registration
    # ------------------------------------------------------------------
    async def register_gm(
        self,
        guild: discord.Guild,
        member: discord.Member,
        *,
        actor_id: int | None = None,
        assign_gm_role: bool = True,
    ) -> GmRecord:
        """Make a member a GM by giving them the GM role.

        No group role is created. An admin sets or creates one later with
        ``set_group_role``. A GM who was registered before (an orphaned group)
        gets their old group role and players back.
        """
        if member.bot:
            raise ServiceError("Bots can't be GMs.")
        existing = await self.db.get_gm(member.id, include_inactive=True)
        if existing and existing.active:
            raise ServiceError(f"{member.display_name} is already a GM.")
        gm_role = await self.get_gm_role(guild, create=True)
        if assign_gm_role:
            self._check_manageable(guild, gm_role)

        if existing:
            await self.db.reactivate_gm(member.id)
        else:
            await self.db.insert_gm(member.id)
        if assign_gm_role and gm_role not in member.roles:
            try:
                await _safe(member.add_roles(gm_role, reason="Registered as GM"), "assign the GM role")
            except ServiceError:
                if existing:
                    await self.db.deactivate_gm(member.id)
                else:
                    await self.db.delete_gm(member.id)
                raise

        record = await self._require_gm(member.id)
        await self.audit(actor_id, "gm_added", member.id, member.id, "registered without a group role"
                         if record.role_id is None else f"kept group role {record.role_id}")
        return record

    async def set_group_role(
        self,
        guild: discord.Guild,
        gm_id: int,
        *,
        role: discord.Role | None = None,
        name: str | None = None,
        actor_id: int | None = None,
    ) -> tuple[discord.Role, int]:
        """Give a GM a group role: use the existing ``role``, or create a new one named ``name``.

        Recorded players get the new role. With an existing role, everyone already
        holding it (except the GM) becomes a player. A previous group role is never
        deleted; it is only taken away from the players.
        Returns the new group role and how many players were imported from it.
        """
        gm = await self._require_gm(gm_id)
        if role is not None and name:
            raise ServiceError("Give either an existing `role` or a `name` for a new one, not both.")
        if role is not None:
            if role.id == gm.role_id:
                raise ServiceError(f"**{role.name}** is already this GM's group role.")
            await self._check_adoptable(guild, role, gm_id)
            new_role, created = role, False
        else:
            new_role, created = await self._create_group_role(guild, gm_id, name), True

        old_role = guild.get_role(gm.role_id) if gm.role_id is not None else None
        await self.db.set_group_role(gm_id, new_role.id)
        await self._grant_to_players(guild, gm_id, new_role)
        imported = 0 if created else await self._import_holders(gm_id, new_role)
        gm_member = guild.get_member(gm_id)
        if gm_member is not None and new_role in gm_member.roles:  # a GM does not hold their own group role
            await _safe(gm_member.remove_roles(new_role, reason="GMs don't hold their group role"), "update roles")
        if old_role is not None:
            await self._take_role_from_players(gm_id, old_role)

        action = "created" if created else "set"
        await self.audit(actor_id, f"group_role_{action}", gm_user_id=gm_id, detail=f"{new_role.name} ({new_role.id})")
        return new_role, imported

    async def _create_group_role(self, guild: discord.Guild, gm_id: int, name: str | None) -> discord.Role:
        member = guild.get_member(gm_id)
        if name:
            role_name = await self._validate_name(guild, name, exclude_gm=gm_id)
        elif member is not None:
            role_name = await self._pick_name(guild, None, member, exclude_gm=gm_id)
        else:
            raise ServiceError("That GM isn't on the server, so give the new role a `name`.")
        if not guild.me.guild_permissions.manage_roles:
            raise ServiceError("I need the Manage Roles permission.")
        return await _safe(
            guild.create_role(
                name=role_name,
                permissions=discord.Permissions.none(),
                hoist=False,
                mentionable=False,
                reason=f"Group role for GM {member or gm_id}",
            ),
            "create the group role",
        )

    async def _take_role_from_players(self, gm_id: int, role: discord.Role) -> None:
        """Take a former group role away from the group's players. The role itself stays on the server."""
        player_ids = set(await self.db.list_members(gm_id))
        for player in role.members:
            if player.id in player_ids:
                await _safe(player.remove_roles(role, reason="No longer a group role"), "update roles")

    async def _import_holders(self, gm_id: int, role: discord.Role) -> int:
        """Record everyone holding ``role`` (except bots and the GM) as players. Returns how many were new."""
        imported = 0
        for holder in role.members:
            if holder.bot or holder.id == gm_id or await self.db.is_member(gm_id, holder.id):
                continue
            await self.db.add_member(gm_id, holder.id)
            imported += 1
        return imported

    async def unregister_gm(
        self,
        guild: discord.Guild,
        user_id: int,
        *,
        keep_role: bool,
        revoke_gm_role: bool = True,
        actor_id: int | None = None,
    ) -> None:
        gm = await self._require_gm(user_id)
        role = guild.get_role(gm.role_id) if gm.role_id is not None else None
        if keep_role:
            await self.db.deactivate_gm(user_id)  # orphaned: rows and role stay
        else:
            await self.db.delete_gm(user_id)
        if not keep_role and role is not None:
            await _safe(role.delete(reason="GM removed"), "delete the group role")
        if revoke_gm_role:
            member = guild.get_member(user_id)
            gm_role = await self._peek_gm_role(guild)
            if member and gm_role and gm_role in member.roles:
                await _safe(member.remove_roles(gm_role, reason="GM removed"), "remove the GM role")
        await self.audit(
            actor_id, "gm_removed", user_id, user_id, "group role kept" if keep_role else "group role deleted"
        )

    # ------------------------------------------------------------------
    # players
    # ------------------------------------------------------------------
    async def add_player(
        self, guild: discord.Guild, gm_id: int, member: discord.Member, *, actor_id: int | None = None
    ) -> None:
        gm = await self._require_gm(gm_id)
        if member.bot:
            raise ServiceError("Bots can't be added to a group.")
        if member.id == gm_id:
            raise ServiceError("A GM can't be a player in their own group.")
        if is_admin(member, self.admin_role_id):
            raise ServiceError("Admins can't be added as players.")
        gm_role = await self._peek_gm_role(guild)
        if await self.db.get_gm(member.id) or (gm_role and gm_role in member.roles):
            raise ServiceError("GMs can't be added as players.")
        role = self._group_role(guild, gm)
        self._check_manageable(guild, role)
        if await self.db.is_member(gm_id, member.id):
            raise ServiceError(f"{member.display_name} is already in this group.")
        await self.db.add_member(gm_id, member.id)
        try:
            await _safe(member.add_roles(role, reason=f"Added to group of GM {gm_id}"), "assign the group role")
        except ServiceError:
            await self.db.remove_member(gm_id, member.id)
            raise
        await self.audit(actor_id, "player_added", member.id, gm_id)

    async def remove_player(
        self, guild: discord.Guild, gm_id: int, member: discord.Member, *, actor_id: int | None = None
    ) -> None:
        gm = await self._require_gm(gm_id)
        if not await self.db.remove_member(gm_id, member.id):
            raise ServiceError(f"{member.display_name} is not in this group.")
        role = guild.get_role(gm.role_id) if gm.role_id is not None else None
        if role is not None and role in member.roles:
            await _safe(member.remove_roles(role, reason=f"Removed from group of GM {gm_id}"), "remove the group role")
        await self.audit(actor_id, "player_removed", member.id, gm_id)

    async def list_players(self, gm_id: int) -> list[int]:
        await self._require_gm(gm_id)
        return await self.db.list_members(gm_id)

    # ------------------------------------------------------------------
    # sync and health checks
    # ------------------------------------------------------------------
    async def check_hierarchy(self, guild: discord.Guild) -> list[str]:
        problems: list[str] = []
        me = guild.me
        if not me.guild_permissions.manage_roles:
            problems.append("I don't have the Manage Roles permission.")
            return problems
        roles = []
        gm_role = await self._peek_gm_role(guild)
        if gm_role:
            roles.append(gm_role)
        for gm in await self.db.list_gms():
            role = guild.get_role(gm.role_id) if gm.role_id is not None else None
            if role:
                roles.append(role)
        for role in roles:
            if role >= me.top_role:
                problems.append(f"Role **{role.name}** is above my highest role, so I can't manage it.")
        return problems

    async def sync(self, guild: discord.Guild, actor_id: int | None = None) -> list[str]:
        """Reconcile the database with Discord. Returns a list of changes made.

        Discord is the source of truth for who holds which role. The one
        exception is a group role that was deleted: it is recreated and
        handed back to the players recorded in the database.
        """
        report: list[str] = []
        gm_role = await self.get_gm_role(guild)

        for member in gm_role.members:
            if member.bot or await self.db.get_gm(member.id):
                continue
            await self.register_gm(guild, member, actor_id=actor_id, assign_gm_role=False)
            report.append(f"Registered {member.mention} as a GM (they held the GM role).")

        for gm in await self.db.list_gms():
            report.extend(await self._sync_gm(guild, gm, gm_role, actor_id))

        if report:
            await self.audit(actor_id, "sync", detail=f"{len(report)} change(s)")
        return report

    async def _sync_gm(
        self, guild: discord.Guild, gm: GmRecord, gm_role: discord.Role, actor_id: int | None
    ) -> list[str]:
        member = guild.get_member(gm.user_id)
        if member is None or gm_role not in member.roles:
            await self.unregister_gm(guild, gm.user_id, keep_role=True, revoke_gm_role=False, actor_id=actor_id)
            return [f"Unregistered <@{gm.user_id}> (no longer a GM). Their group role was kept."]

        if gm.role_id is None:
            return []
        role = guild.get_role(gm.role_id)
        if role is None:
            role = await self._recreate_group_role(guild, gm, member)
            return [f"Recreated the missing group role **{role.name}** for {member.mention}."]

        report: list[str] = []
        if role in member.roles:
            await _safe(member.remove_roles(role, reason="GMs don't hold their group role"), "update roles")
            report.append(f"Removed the group role from its own GM {member.mention}.")
        report.extend(await self._sync_players(guild, gm, member, role))
        return report

    async def _recreate_group_role(self, guild: discord.Guild, gm: GmRecord, member: discord.Member) -> discord.Role:
        role = await _safe(
            guild.create_role(
                name=await self._pick_name(guild, None, member, exclude_gm=gm.user_id),
                permissions=discord.Permissions.none(),
                hoist=False,
                mentionable=False,
                reason="Recreating missing group role",
            ),
            "recreate the group role",
        )
        await self.db.set_group_role(gm.user_id, role.id)
        await self._grant_to_players(guild, gm.user_id, role)
        return role

    async def _sync_players(
        self, guild: discord.Guild, gm: GmRecord, member: discord.Member, role: discord.Role
    ) -> list[str]:
        """Make the recorded players match who holds the group role in Discord."""
        report: list[str] = []
        players = await self.db.list_members(gm.user_id)
        holders = {m.id for m in role.members if not m.bot}
        for pid in players:
            player = guild.get_member(pid)
            if player is not None and pid in holders:
                continue
            await self.db.remove_member(gm.user_id, pid)
            if player is None:
                report.append(f"Dropped <@{pid}> from {member.mention}'s group (left the server).")
            else:
                report.append(f"Dropped {player.mention} from {member.mention}'s group (no longer holds the role).")
        for pid in sorted(holders - set(players) - {gm.user_id}):
            await self.db.add_member(gm.user_id, pid)
            report.append(f"Added <@{pid}> to {member.mention}'s group (held the role in Discord).")
        return report

    # ------------------------------------------------------------------
    # gateway events
    # ------------------------------------------------------------------
    async def handle_member_remove(self, member: discord.Member) -> None:
        if await self.db.get_gm(member.id):
            await self.unregister_gm(member.guild, member.id, keep_role=True, revoke_gm_role=False)
        gm_ids = await self.db.remove_player_everywhere(member.id)
        for gm_id in gm_ids:
            await self.audit(None, "player_left_server", member.id, gm_id)

    async def handle_role_deleted(self, role: discord.Role) -> None:
        gm = await self.db.get_gm_by_role(role.id)
        if gm:
            await self.audit(None, "group_role_deleted", gm_user_id=gm.user_id, detail=role.name)
        raw = await self.db.get_setting(GM_ROLE_KEY)
        if raw and int(raw) == role.id:
            log.warning("The GM role was deleted. Run /admin set-role.")
            await self.audit(None, "gm_role_deleted", detail=role.name)

    async def handle_member_update(self, before, after: discord.Member) -> None:
        """Register or unregister GMs when the GM role is changed by hand."""
        raw = await self.db.get_setting(GM_ROLE_KEY)
        if not raw or after.bot:
            return
        role_id = int(raw)
        had = any(r.id == role_id for r in before.roles)
        has = any(r.id == role_id for r in after.roles)
        if had == has:
            return
        guild = after.guild
        registered = await self.db.get_gm(after.id)
        if has and not registered:
            await self.register_gm(guild, after, assign_gm_role=False)
        elif had and not has and registered:
            await self.unregister_gm(guild, after.id, keep_role=True, revoke_gm_role=False)
