from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class Config:
    token: str
    guild_id: int
    admin_role_id: int | None = None
    access_role_id: int | None = None
    log_channel_id: int | None = None
    db_path: str = "bot.db"

    @classmethod
    def from_env(cls) -> "Config":
        load_dotenv()

        def required(name: str) -> str:
            value = os.getenv(name, "").strip()
            if not value:
                raise SystemExit(f"Missing required setting {name}. See .env.example.")
            return value

        def as_int(name: str, value: str) -> int:
            try:
                return int(value)
            except ValueError:
                raise SystemExit(f"{name} must be a number.") from None

        def optional_int(name: str) -> int | None:
            value = os.getenv(name, "").strip()
            return as_int(name, value) if value else None

        return cls(
            token=required("DISCORD_TOKEN"),
            guild_id=as_int("GUILD_ID", required("GUILD_ID")),
            admin_role_id=optional_int("ADMIN_ROLE_ID"),
            access_role_id=optional_int("ACCESS_ROLE_ID"),
            log_channel_id=optional_int("LOG_CHANNEL_ID"),
            db_path=os.getenv("DB_PATH", "").strip() or "bot.db",
        )
