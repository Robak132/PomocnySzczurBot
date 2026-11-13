"""Exceptions raised by the bot."""
from __future__ import annotations

from discord import app_commands


class NotAdmin(app_commands.CheckFailure):
    """Raised when a non-admin runs an admin command."""


class NotGM(app_commands.CheckFailure):
    """Raised when a non-GM runs a GM command."""


class DatabaseVersionError(Exception):
    """The database file was created for a different schema version."""


class ServiceError(Exception):
    """An error whose message is safe to show to the user."""
