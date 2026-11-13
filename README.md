# Discord Permission Bot

Manages GMs and their groups of players on one Discord server. Full design: [docs/spec.md](docs/spec.md).

- **Admins** assign the shared **GM** role and manage every group.
- Each **GM** can have one **group role** (their "rank"). Players hold it; the GM does not. A new GM has none until an admin sets or creates one.
- **GMs** add, remove and list their own players.
- A player can be in several groups. Admins and GMs can't be added as players.

## Setup

1. In the [Developer Portal](https://discord.com/developers/applications) create an application and a bot.
   Under **Bot → Privileged Gateway Intents** enable **Server Members Intent**.
2. Invite the bot with the scopes `bot` and `applications.commands` and the **Manage Roles** permission.
3. In Server Settings → Roles, drag the bot's role **above** the GM role and all group roles it will manage.
   The bot warns at startup (and in `/admin sync`) if a role is out of reach.
4. Install and configure:

   ```bash
   python3 -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   cp .env.example .env      # fill in DISCORD_TOKEN and GUILD_ID
   python -m bot
   ```

On first start the bot creates a role named **GM** (or use `/admin set-role role:@Role` to pick an existing role).

## Commands

**GMs**

| Command | What it does |
|---|---|
| `/group add user` | Add a player to your group (grants your group role) |
| `/group remove user` | Remove a player from your group |
| `/group remove-all` | Remove all players from your group (asks for confirmation) |
| `/group list` | List your players |
| `/player remove gm` | Leave a GM's group you are in (asks for confirmation). The GM is pinged in the channel |

**Admins** (visible only to members with the Administrator permission, plus the optional `ADMIN_ROLE_ID` role)

| Command | What it does |
|---|---|
| `/admin gm add user` | Give the GM role. No group role is created |
| `/admin gm set-group-role user [role] [name]` | With `role`: use that existing role as the GM's group role, and its current holders become players. Without it: create a new role (named `name`, or the GM's display name). Players move to the new role; the old role stays on the server and is only taken from the players |
| `/admin gm remove user [keep_role]` | Remove a GM. Deletes their group role unless `keep_role` is set |
| `/admin list` | List all GMs with their group role, player count and players |
| `/admin set-role [role] [name]` | With `role`: use that existing role as the GM role and register its holders as GMs. Without it: create a new GM role (named `name`, default GM) |
| `/admin player add\|remove gm user` | Add or remove a player in any GM's group (pick the GM from autocomplete) |
| `/admin gm list gm` | List the players of a GM's group |
| `/admin sync` | Reconcile the bot's records with Discord |

## Behavior worth knowing

- Giving someone the GM role by hand in Discord (or `/admin set-role role:@Role`) registers them as a GM, without a group role.
  Taking it away unregisters them but keeps the group role and players, so a slip loses nothing.
- If a member leaves the server they are dropped from every group. If a GM leaves, their group is kept orphaned.
- If a group role is deleted, the bot flags it. `/admin sync` recreates it and gives it back to the group's players.
- The bot never renames or recolors existing roles. Admins do that in Discord's server settings.
- Every change is stored in the `audit_log` table, and posted to `LOG_CHANNEL_ID` if set.
- If `ACCESS_ROLE_ID` is set, only members with that role can use any command (admins included).
  Everyone else gets "You don't have access to this bot." and autocomplete stays empty for them.

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

The tests cover the service layer with fake Discord objects and check the slash command tree against discord.py.
They don't connect to Discord, so do a quick manual run on a test server first (checklist in the spec, section 11).
