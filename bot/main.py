from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from .checks import AccessTree
from .cogs.admin import AdminCog
from .cogs.events import EventsCog
from .cogs.group import GroupCog
from .config import Config
from .db import Database
from .exceptions import DatabaseVersionError, ServiceError
from .groups import GroupService
from .ui import respond

log = logging.getLogger(__name__)


class PermissionBot(commands.Bot):
    def __init__(self, config: Config):
        intents = discord.Intents.default()
        intents.members = True
        super().__init__(command_prefix=commands.when_mentioned, intents=intents, tree_cls=AccessTree)
        # noinspection PyUnresolvedReferences
        self.tree.access_role_id = config.access_role_id
        self.config = config
        self.db = Database(config.db_path)
        self.service = GroupService(self.db, admin_role_id=config.admin_role_id, on_audit=self._post_audit)

    async def setup_hook(self) -> None:
        await self.db.connect()
        await self.add_cog(GroupCog(self, self.service))
        await self.add_cog(AdminCog(self, self.service))
        await self.add_cog(EventsCog(self, self.service, self.config.guild_id))
        self.tree.on_error = on_tree_error

        guild = discord.Object(id=self.config.guild_id)
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)

    async def on_ready(self) -> None:
        role_id = self.config.access_role_id
        if role_id is None:
            return
        guild = self.get_guild(self.config.guild_id)
        if guild is not None and guild.get_role(role_id) is None:
            log.warning("ACCESS_ROLE_ID %s does not exist on the server, so nobody can use the bot", role_id)
        else:
            log.info("Bot access limited to members with role %s", role_id)

    async def close(self) -> None:
        await super().close()
        await self.db.close()

    async def _post_audit(self, actor_id, action, target_id, gm_user_id, detail) -> None:
        if not self.config.log_channel_id:
            return
        channel = self.get_channel(self.config.log_channel_id)
        if not isinstance(channel, discord.abc.Messageable):
            log.warning("LOG_CHANNEL_ID %s is not a channel I can post in", self.config.log_channel_id)
            return
        parts = [f"**{action}**"]
        if actor_id:
            parts.append(f"by <@{actor_id}>")
        if target_id:
            parts.append(f"target <@{target_id}>")
        if gm_user_id and gm_user_id != target_id:
            parts.append(f"GM <@{gm_user_id}>")
        if detail:
            parts.append(f"· {detail}")
        await channel.send(embed=discord.Embed(description=" ".join(parts)), allowed_mentions=discord.AllowedMentions.none())


async def on_tree_error(interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
    original = getattr(error, "original", error)
    if isinstance(original, ServiceError):
        message = str(original)
    elif isinstance(error, app_commands.CheckFailure):
        message = str(error) or "You can't use this command."
    else:
        log.exception("Unhandled error in command %s", interaction.command, exc_info=error)
        message = "Something went wrong. Please try again later."
    try:
        await respond(interaction, message)
    except discord.HTTPException:
        log.warning("Could not deliver the error message to the user")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    config = Config.from_env()
    try:
        PermissionBot(config).run(config.token, log_handler=None)
    except DatabaseVersionError as exc:
        log.critical("%s Shutting down.", exc)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
