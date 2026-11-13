# Discord Permission Bot: Specification

## 1. Purpose
A Discord bot that manages membership and role cosmetics for a tabletop-style server. Users fall into two categories: **GMs** and **players**. Each GM owns a group of players, represented by a Discord role (the GM's "rank"). The GM does not hold their own group role; only their players do. GMs manage their own group; admins manage all groups.

## 2. Stack
- Python 3.11+, `discord.py` 2.x (slash commands via `app_commands`)
- SQLite (via `aiosqlite`) for persistent state
- Config via `.env`: `DISCORD_TOKEN`, `GUILD_ID`, `ADMIN_ROLE_ID` (optional), `ACCESS_ROLE_ID` (optional), `DB_PATH`

## 3. Concepts
| Term | Meaning |
|---|---|
| Admin | Member with the Discord "Administrator" permission, or holding the configured admin role |
| GM role | The shared Discord role (default name "GM") that marks Game Masters. Assigned and removed by admins |
| GM | Member holding the GM role. Owns exactly one group and one group role |
| Group | A GM's team: the set of players the GM has added |
| Rank | The group's Discord role. Held by the group's players, **not** by the GM. The bot names it when creating it and never renames or recolors it afterwards |
| Player | Any member added to a GM's group |

### Assumptions (change if wrong)
- A player may belong to **several groups at once** (one membership row per GM).
- Being a GM means holding the **GM role**. Admins assign it with `/admin gm add` (or by hand in Discord; the bot notices and registers the GM). Admins can point the bot at an existing role with `/admin gm-role set`, otherwise the bot creates a role named "GM" on first use (no extra permissions).
- Every GM has exactly one group role, created when the GM is registered.
- The GM does **not** hold their own group role. The role is assigned to players only.
- The bot only manages roles it created or that an admin designated as the GM role. It never touches other roles.

## 4. Data model (SQLite)
```
gms(
  user_id      INTEGER PRIMARY KEY,
  role_id      INTEGER UNIQUE NOT NULL,
  created_at   TEXT NOT NULL
)
settings(
  key TEXT PRIMARY KEY, value TEXT       -- e.g. gm_role_id
)
group_members(
  gm_user_id   INTEGER NOT NULL REFERENCES gms(user_id),  -- no cascade: group rows are deleted explicitly
  player_id    INTEGER NOT NULL,
  added_at     TEXT NOT NULL,
  PRIMARY KEY (gm_user_id, player_id)
)
-- a player may appear under several gm_user_id values
audit_log(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  actor_id INTEGER, action TEXT, target_id INTEGER, gm_user_id INTEGER,
  detail TEXT, created_at TEXT
)
```
The schema version is stored in SQLite's `PRAGMA user_version` (currently 1). A new, empty file gets the schema and the current version. A file with any other version is never upgraded: the bot logs the mismatch and exits, and the admin backs up and removes the file or runs the matching bot version.

Discord is the source of truth for role membership. The DB records ownership and group structure. A `/admin sync` command reconciles differences.

## 5. Commands

### 5.1 GM commands (caller must be a registered GM)
| Command | Behavior |
|---|---|
| `/group add user:@member` | Adds the member to the caller's group and grants the group role |
| `/group remove user:@member` | Removes the member and revokes the group role. Only members of the caller's own group |
| `/group list` | Lists the caller's players (ephemeral) |

### 5.2 Admin commands (caller must be admin)
| Command | Behavior |
|---|---|
| `/admin gm add user:@member [name] [role:@role]` | Assigns the GM role, registers the user, and creates their group role, named `name` (1-100 chars, unique among bot-managed roles) or the user's display name. With `role`, that existing role becomes the group role instead (see 5.3). The GM does not receive the group role |
| `/admin gm remove user:@member [keep_role]` | Revokes the GM role, unregisters the GM, and **deletes the group role by default** (`keep_role:true` keeps it). Requires confirmation button |
| `/admin gm list` | Lists all GMs with group name and player count |
| `/admin gm-role set role:@role` | Designates an existing Discord role as the GM role. Existing holders are registered as GMs |
| `/admin gm-role create [name]` | Creates a new GM role (default name "GM") and designates it |
| `/admin player add gm:@gm user:@member` | Adds a player to that GM's group |
| `/admin player remove gm:@gm user:@member` | Removes a player from that GM's group |
| `/admin player list gm:@gm` | Lists players of that GM's group |
| `/admin sync` | Reconciles DB with actual Discord role membership and reports drift |

Autocomplete for the `gm` argument lists registered GMs. The argument is a text field filled from the suggestions, because Discord can't autocomplete user pickers.

### 5.3 Adopting an existing role
`/admin gm add ... role:@role` turns a role that already exists on the server into the GM's group role.
- Everyone already holding the role (except bots and the GM) is recorded as a player. The GM loses the role.
- The adopted role keeps its name and color. `name` is refused together with `role`, because the bot never renames existing roles. The same applies when re-registering a GM whose old group role still exists.
- Refused for roles with elevated permissions (Administrator, Manage Server/Roles/Channels/Messages/Threads/Nicknames/Webhooks/Expressions/Events, Kick, Ban, Timeout, Mention Everyone, View Audit Log), because the GM could hand those to players. Ordinary permissions such as Send Messages are fine.
- Also refused where it can't work: @everyone and integration-managed roles (Discord won't assign them), roles above the bot, the GM role, and another GM's group role (active or orphaned).
- After adoption it behaves like any group role. `/admin gm remove` deletes it unless `keep_role` is set.

## 6. Rules and validation
1. Permission checks run before any action. Failures return an ephemeral error, never a partial change.
2. A user cannot be added to a group they are already in, but may be in other groups. Cannot add a bot account. A GM cannot add an admin or another GM as a player.
3. Removing a GM via `/admin gm remove` deletes their group role (unless `keep_role:true`) and their `group_members` rows. If the role is kept, rows are kept and the group is marked orphaned.
3a. If someone gains the GM role in Discord outside the bot, `on_member_update` registers them as a GM and creates their group role (default name: display name). If they lose it, they are unregistered like `/admin gm remove`, but their group role is kept (orphaned) and an audit entry is written, so an accidental removal loses nothing.
4. If a member leaves the server, they are removed from `group_members` (`on_member_remove`).
4a. If a GM leaves the server, they are unregistered and their group is kept orphaned (role and players stay) until an admin cleans it up or re-adds them.
5. If a group role is deleted manually in Discord, the bot detects it (`on_guild_role_delete`), logs it, and marks the group as needing repair. `/admin sync` recreates the role and re-grants it to the group's players. If the GM role itself is deleted, `/admin gm-role create` or `set` must be run again; GM commands are disabled until then.
6. The bot never renames or recolors existing roles. New group roles get the given name (or the GM's display name) and Discord's default color.
7. All responses are ephemeral except confirmations an admin chooses to make public.

## 7. Discord permissions and hierarchy
- Bot requires: **Manage Roles**. Server Members intent enabled.
- The bot's top role must sit **above** the GM role and all group roles in the role hierarchy. Otherwise Discord rejects edits. The bot checks this at startup and reports clearly.
- Group roles are created with no extra permissions and are not mentionable by default (optional setting).
- Command visibility: `/group *` is visible to all, but enforced by GM check. `/admin *` uses `default_permissions` set to Administrator and is also checked at runtime.

## 8. Audit and logging
- Every mutating action writes to `audit_log`.
- Optional `LOG_CHANNEL_ID`: bot posts a one-line embed per action (who, what, target).

### Access role
- Optional `ACCESS_ROLE_ID`: when set, every command and autocomplete is refused for members without this role, admins included. The caller gets an ephemeral "You don't have access to this bot." Gateway events (member leaves, role changes) are still handled for everyone.
- Application logs via `logging`, INFO by default.

## 9. Error handling
- Missing permission: "You are not a GM." / "Admins only."
- Hierarchy failure: "I can't manage this role because it is above mine."
- Target not found / not in group: explicit message naming the target.
- Discord API errors are caught, logged, and returned as a generic failure without leaking internals.

## 10. Project layout
```
bot/
  main.py            # startup, intents, command sync
  config.py          # env loading
  db.py              # schema + queries
  checks.py          # is_admin, is_gm
  constants.py       # shared constants (limits, permissions, messages)
  exceptions.py      # all exception classes
  groups.py          # GroupService: role create/edit, add/remove logic shared by all cogs
  cogs/
    group.py         # GM commands
    admin.py         # admin commands
    events.py        # member leave, role delete
requirements.txt
.env.example
```
All cogs call the same `GroupService`, so GM and admin actions share validation.

## 11. Testing
- Unit tests for the service layer with a mocked guild and in-memory SQLite.
- Manual test checklist: assign GM role (via bot and by hand), confirm the GM does not receive their own group role, add/remove/list as GM, admin add/remove/list per GM, member leaves, role deleted manually, hierarchy misconfigured.

## 12. Out of scope (v1)
Channel-level permission overwrites, multiple roles per GM, web dashboard, scheduled cleanup, multi-server support.

## 13. Decisions
1. Players may be in several groups: yes.
2. GMs hold a shared "GM" role, assignable by admins: yes. Each GM has a group role, but the GM does not hold it.
3. Group roles are hoisted (shown separately in the member list): no.
4. Removing a GM deletes their group role by default: yes.
