# mtgDiscordBot

Discord Bot used to automate and manage MTG Rotisserie Draft Leagues.

Requires Python 3.11 or newer. Uses discord.py 2.x application (slash) commands.

--------------------------------------------------------------------------------------------------

Features:
- Can be used to manage an arbitrary number of players conducting a rotisserie draft (aka snake draft).
- (optional) Relays picks into a google spreadsheet derived from Lucky Paper's Rotisserie Draft Template for easy viewing
- Allows for queuing of picks ahead of time.
  - Keep your draft running quickly by queuing multiple picks in a row.

--------------------------------------------------------------------------------------------------

TODO:
- Handle API failures gracefully
- Alert players if their planned pick is taken
- Allow customization around persistence, some users may want the list reset after 1 or 2 picks

--------------------------------------------------------------------------------------------------

Setup:

Create an application and bot in the [Discord Developer Portal](https://discord.com/developers/applications).
Copy `.env.example` to `.env` and set `DISCORD_TOKEN` to the bot token. Never share or commit this file.

Install and run from this directory on Windows:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe bot.py
```

Invite the bot with the `bot` and `applications.commands` OAuth2 scopes. Give it View Channels and Send Messages permission in the draft channel. Privileged Message Content and Server Members intents are not required.

Commands sync automatically at startup. Set the optional `DISCORD_GUILD_ID` to your server ID for immediate, server-only command registration. Leave it empty for global commands, which may take time to appear. Restart after changing this setting. Switching between guild and global registration can leave old command copies in that server; use one scope consistently.

If you are using the spreadsheet export, you need a JWT configured for authentication from Google Cloud API services. Store the key file in `google-cloud-credentials.json`. Here is a guide to set that up: https://docs.gspread.org/en/latest/oauth2.html#service-account. Remember to share edit access on the spreadsheet with your IAM user at Google Cloud.

The app and supporting data file (bot.py, card-list.txt) must be placed in a directory together. The Python app needs to run indefinitely, as long as the draft is running. I would recommend using `screen` or `nohup` to accomplish this.

In the event that the bot crashes due to Discord outages/hiccups, simply restart the app; your draft will be able to resume from its previous position. These issues are thankfully infrequent enough that this shouldn't be an issue.

Type `/` in Discord to discover commands and their options:

- `/register [team_name]`: register before the draft starts.
- `/start_draft [google_sheet_id]`: start after all players register. Requires Manage Server permission.
- `/draft card_name`: add a card to your ordered draft queue.
- `/clear`: empty your draft queue.
- `/search card_name`: search up to five matching cards.

Card arguments provide autocomplete. Picks and completion announcements go to the channel where `/start_draft` was used. Enable DMs from the server to receive turn notifications. Google Sheets is optional; omit `google_sheet_id` to run entirely in Discord.

Local `.env` files, virtual environments, databases, and Google credential JSON files are ignored by Git. Only `.env.example`, containing placeholders, is versioned. Back up your local databases separately. If a secret has ever been committed elsewhere, ignoring it does not remove that history; revoke and replace it.

Run the offline regression tests without a Discord token or Google credentials:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

The separate `pairings/` directory is a legacy pairing prototype, not the slash-command draft bot described here.
