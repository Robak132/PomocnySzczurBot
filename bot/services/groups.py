"""Business logic shared by the GM and admin command cogs.

All Discord changes go through this module so both cogs share the same
validation. Every user-facing failure is raised as ServiceError.
"""
from __future__ import annotations

import logging
import math
import re
import time
from typing import Awaitable, Callable, Optional

import discord

from ..checks import is_admin
from ..db import Database, GmRecord

log = logging.getLogger(__name__)

RATE_LIMIT_SECONDS = 10
GM_ROLE_KEY = "gm_role_id"
MAX_ROLE_NAME = 100

NAMED_COLORS = {
    "red": 0xE74C3C,
    "orange": 0xE67E22,
    "yellow": 0xF1C40F,
    "green": 0x2ECC71,
    "teal": 0x1ABC9C,
    "blue": 0x3498DB,
    "purple": 0x9B59B6,
    "pink": 0xE91E63,
    "grey": 0x95A5A6,
    "gray": 0x95A5A6,
    "white": 0xFFFFFF,
    "black": 0x010101,
}
_HEX = re.compile(r"^#?([0-9a-f]{6})$", re.IGNORECASE)

AuditHook = Callable[[Optional[int], str, Optional[int], Optional[int], Optional[str]], Awaitable[None]]


class ServiceError(Exception):
    """An error whose message is safe to show to the user."""


def parse_color(text: str) -> discord.Colour:
    value = text.strip().lower()
    if value in NAMED_COLORS:
        return discord.Colour(NAMED_COLORS[value])
    match = _HEX.match(value)
    if match:
        return discord.Colour(int(match.group(1), 16))
    raise ServiceError(
        "Invalid color. Use a hex code like #FF8800 or one of: " + ", ".join(sorted(NAMED_COLORS)) + "."
    )


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
        clock: Callable[[], float] = time.monotonic,
    ):
        self.db = db
        self.admin_role_id = admin_role_id
        self.on_audit = on_audit
        self._clock = clock
        self._last_change: dict[int, float] = {}

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
            except Exception:  # logging must never break a command
                log.exception("Audit hook failed")

    async def is_gm(self, user_id: int) -> bool:
        return await self.db.get_gm(user_id) is not None

    async def _require_gm(self, gm_id: int) -> GmRecord:
        gm = await self.db.get_gm(gm_id)
        if gm is None:
            raise ServiceError("That user is not a GM.")
        return gm

    def _group_role(self, guild: discord.Guild, gm: GmRecord) -> discord.Role:
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
            if gm.user_id == exclude_gm:
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

    def _check_rate(self, gm_id: int) -> None:
        last = self._last_change.get(gm_id)
        if last is not None:
            elapsed = self._clock() - last
            if elapsed < RATE_LIMIT_SECONDS:
                wait = math.ceil(RATE_LIMIT_SECONDS - elapsed)
                raise ServiceError(f"Please wait {wait}s before changing this role again.")

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
                    "The GM role no longer exists. An admin must run `/admin gm-role create` or `/admin gm-role set`."
                )
            return role
        if create:
            return await self.create_gm_role(guild)
        raise ServiceError("No GM role is configured yet. An admin must run `/admin gm add` or `/admin gm-role create`.")

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
        for member in list(role.members):
            if member.bot or await self.db.get_gm(member.id):
                continue
            await self.register_gm(guild, member, actor_id=actor_id, assign_gm_role=False)
            registered.append(member)
        return registered

    # ------------------------------------------------------------------
    # GM registration
    # ------------------------------------------------------------------
    async def register_gm(
        self,
        guild: discord.Guild,
        member: discord.Member,
        *,
        name: str | None = None,
        color: str | None = None,
        actor_id: int | None = None,
        assign_gm_role: bool = True,
    ) -> GmRecord:
        """Make a member a GM: give them the GM role and create their group role.

        The GM never receives their own group role; only their players do.
        """
        if member.bot:
            raise ServiceError("Bots can't be GMs.")
        existing = await self.db.get_gm(member.id, include_inactive=True)
        if existing and existing.active:
            raise ServiceError(f"{member.display_name} is already a GM.")

        colour = parse_color(color) if color else None  # validate before changing anything
        gm_role = await self.get_gm_role(guild, create=True)
        if assign_gm_role:
            self._check_manageable(guild, gm_role)

        role = guild.get_role(existing.role_id) if existing else None
        created = False
        if role is None:
            role_name = await self._pick_name(guild, name, member)
            role = await _safe(
                guild.create_role(
                    name=role_name,
                    colour=colour or discord.Colour.default(),
                    permissions=discord.Permissions.none(),
                    hoist=False,
                    mentionable=False,
                    reason=f"Group role for GM {member}",
                ),
                "create the group role",
            )
            created = True
        else:
            self._check_manageable(guild, role)
            edits: dict = {}
            if name:
                edits["name"] = await self._pick_name(guild, name, member, exclude_gm=member.id)
            if colour:
                edits["colour"] = colour
            if edits:
                await _safe(role.edit(reason="GM re-registered", **edits), "update the group role")

        await self.db.upsert_gm(member.id, role.id)
        if created and existing:
            await self._grant_to_players(guild, member.id, role)

        if assign_gm_role and gm_role not in member.roles:
            try:
                await _safe(member.add_roles(gm_role, reason="Registered as GM"), "assign the GM role")
            except ServiceError:
                if existing:
                    await self.db.deactivate_gm(member.id)
                else:
                    await self.db.delete_gm(member.id)
                if created:
                    try:
                        await role.delete(reason="Rolling back GM registration")
                    except discord.HTTPException:
                        log.warning("Could not delete role %s while rolling back", role.id)
                raise
        if role in member.roles:  # a GM does not hold their own group role
            await _safe(member.remove_roles(role, reason="GMs don't hold their group role"), "update roles")

        await self.audit(actor_id, "gm_added", member.id, member.id, f"group role {role.name} ({role.id})")
        return GmRecord(member.id, role.id, True, False)

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
        role = guild.get_role(gm.role_id)
        if keep_role:
            await self.db.deactivate_gm(user_id)  # orphaned: rows and role stay
        else:
            await self.db.delete_gm(user_id)
        if revoke_gm_role:
            member = guild.get_member(user_id)
            gm_role = await self._peek_gm_role(guild)
            if member and gm_role and gm_role in member.roles:
                await _safe(member.remove_roles(gm_role, reason="GM removed"), "remove the GM role")
        if not keep_role and role is not None:
            await _safe(role.delete(reason="GM removed"), "delete the group role")
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
        if not await self.db.add_member(gm_id, member.id):
            raise ServiceError(f"{member.display_name} is already in this group.")
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
        role = guild.get_role(gm.role_id)
        if role is not None and role in member.roles:
            await _safe(member.remove_roles(role, reason=f"Removed from group of GM {gm_id}"), "remove the group role")
        await self.audit(actor_id, "player_removed", member.id, gm_id)

    async def list_players(self, gm_id: int) -> list[int]:
        await self._require_gm(gm_id)
        return await self.db.list_members(gm_id)

    # ------------------------------------------------------------------
    # rank cosmetics
    # ------------------------------------------------------------------
    async def rename_group(
        self, guild: discord.Guild, gm_id: int, name: str, *, actor_id: int | None = None
    ) -> str:
        gm = await self._require_gm(gm_id)
        role = self._group_role(guild, gm)
        self._check_rate(gm_id)
        new_name = await self._validate_name(guild, name, exclude_gm=gm_id)
        self._check_manageable(guild, role)
        await _safe(role.edit(name=new_name, reason=f"Renamed by {actor_id}"), "rename the role")
        self._last_change[gm_id] = self._clock()
        await self.audit(actor_id, "group_renamed", gm_user_id=gm_id, detail=new_name)
        return new_name

    async def recolor_group(
        self, guild: discord.Guild, gm_id: int, color: str, *, actor_id: int | None = None
    ) -> discord.Colour:
        gm = await self._require_gm(gm_id)
        role = self._group_role(guild, gm)
        self._check_rate(gm_id)
        colour = parse_color(color)
        self._check_manageable(guild, role)
        await _safe(role.edit(colour=colour, reason=f"Recolored by {actor_id}"), "change the role color")
        self._last_change[gm_id] = self._clock()
        await self.audit(actor_id, "group_recolored", gm_user_id=gm_id, detail=str(colour))
        return colour

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
            role = guild.get_role(gm.role_id)
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

        for member in list(gm_role.members):
            if member.bot or await self.db.get_gm(member.id):
                continue
            await self.register_gm(guild, member, actor_id=actor_id, assign_gm_role=False)
            report.append(f"Registered {member.mention} as a GM (they held the GM role).")

        for gm in await self.db.list_gms():
            member = guild.get_member(gm.user_id)
            if member is None or gm_role not in member.roles:
                await self.unregister_gm(guild, gm.user_id, keep_role=True, revoke_gm_role=False, actor_id=actor_id)
                report.append(f"Unregistered <@{gm.user_id}> (no longer a GM). Their group role was kept.")
                continue

            role = guild.get_role(gm.role_id)
            players = await self.db.list_members(gm.user_id)
            if role is None:
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
                await self.db.set_role_id(gm.user_id, role.id)
                await self._grant_to_players(guild, gm.user_id, role)
                report.append(f"Recreated the missing group role **{role.name}** for {member.mention}.")
                continue

            if role in member.roles:
                await _safe(member.remove_roles(role, reason="GMs don't hold their group role"), "update roles")
                report.append(f"Removed the group role from its own GM {member.mention}.")

            holders = {m.id for m in role.members if not m.bot}
            for pid in players:
                player = guild.get_member(pid)
                if player is None:
                    await self.db.remove_member(gm.user_id, pid)
                    report.append(f"Dropped <@{pid}> from {member.mention}'s group (left the server).")
                elif pid not in holders:
                    await self.db.remove_member(gm.user_id, pid)
                    report.append(f"Dropped {player.mention} from {member.mention}'s group (no longer holds the role).")
            for pid in sorted(holders - set(players) - {gm.user_id}):
                await self.db.add_member(gm.user_id, pid)
                report.append(f"Added <@{pid}> to {member.mention}'s group (held the role in Discord).")

        if report:
            await self.audit(actor_id, "sync", detail=f"{len(report)} change(s)")
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
            await self.db.set_needs_repair(gm.user_id, True)
            await self.audit(None, "group_role_deleted", gm_user_id=gm.user_id, detail=role.name)
        raw = await self.db.get_setting(GM_ROLE_KEY)
        if raw and int(raw) == role.id:
            log.warning("The GM role was deleted. Run /admin gm-role create or set.")
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
