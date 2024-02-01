# What is this?

This an open source discord bot which assists with game matchmaking.

This uses Microsoft's [Trueskill](https://www.microsoft.com/en-us/research/project/trueskill-ranking-system/)
matchmaking algorithm to create fair and
balanced teams. Players are assigned a level of skill (mu) and uncertainty (sigma) which is adjusted after each game.

Games are organized using a queueing system and the bot supports numerous features like delineation by region, unranked
queues, admins / bans, map pools / rotations / voting, database backups, post-game images, dice rolls, coin
flips, Twitch stream integration, voice channel creation, leaderboards, etc.

See [commands.py](https://github.com/dwayneyuen/discord-bots/blob/master/discord_bots/commands.py) for a full list of
commands.

## Installation

The bot is written in Python 3 and uses sqlite for persistence. You will need a `DISCORD_API_KEY` at a minimum to run
the bot.

### MacOS

1. Install Python 3.10.0: https://docs.python-guide.org/starting/install3/osx/

    - (optional) Install Python with pyenv instead:
    - `brew install pyenv`
    - `pyenv install 3.10.0`
    - `pyenv global 3.10.0`

1. Setup a virtual env:
    - `cd discord-bots`
    - `python3 -m venv .venv`
    - `source .venv/bin/activate`
1. `pip install -U .`
1. `cp .env.example .env`. Modify `.env` by adding your API key
1. Setup the database: `alembic upgrade head`

### Linux

TODO

### Windows

1. Install WSL
1. Install Python
1. Set up a virtualenv

- `python3 -m venv .venv`
- `source .venv/bin/activate`. If you see this error:
  ```
  File <>\discord-bots\.venv\Scripts\Activate.ps1 cannot be loaded because running scripts is disabled on this system. For more information, see about_Execution_Policies at https:/go.microsoft.com/fwlink/?LinkID=135170.
  ```
  You may need to adjust your windows execution policy: https://stackoverflow.com/a/18713789

## .env file configuration

The following are required

- `DISCORD_API_KEY`
- `CHANNEL_ID` - The discord id of the channel the bot will live in
- `TRIBES_VOICE_CATEGORY_CHANNEL_ID` - The id of the voice channel category (so the bot can make voice channels)
- `SEED_ADMIN_IDS` - Discord ids of players that will start off as admin. You'll need at least one in order to create
  more

The following are optional

- `TWITCH_GAME_NAME`, `TWITCH_CLIENT_ID`, `TWITCH_CLIENT_SECRET` - These enable the `streams` command to list current
  streams of the specified game
- `COMMAND_PREFIX` - Use a different prefix instead of `!`
- `DEFAULT_TRUESKILL_MU`, `DEFAULT_TRUESKILL_SIGMA` - Customize the default trueskill for new players
- `SHOW_TRUESKILL` - Shows player trueskill when making teams, enables the trueskill leaderboard, etc.
- `REQUIRED_ADD_TARGET` - Players have to specify a queue to add to
- `ALLOW_VULGAR_NAMES` - Allow dirtier team names

## Running the bot

1. `cd discord-bots`
1. `source .venv/bin/activate`
1. `python -m discord_bots.main`

# Development

The bot is written in Python 3 with types as much as possible (enforced by Pylance). We use SQLAlchemy as the ORM
and `alembic` to handle migrations.

## Installation

The steps are the same but use `pip install -e .` instead. This allows local changes to be picked up automatically.

## Editor

Recommend using vscode. If you do, install these vscode plugins:

- Python
- Pylance

## Type checking

If you use vscode add this to your settings.json (if anyone knows how to commit
this to the project lmk!):
https://www.emmanuelgautier.com/blog/enable-vscode-python-type-checking

```json
{
  "python.analysis.typeCheckingMode": "basic"
}
```

This enforces type checks for the types declared

## Formatting

Use python black: https://github.com/psf/black

- Go to vscode preferences (cmd + `,` on mac, ctrl + `,` on windows)
- Type "python formatting" in the search bar
- For the option `Python > Formatting: Provider` select `black`

### Pre-commit hook

This project uses `darker` for formatting in a pre-commit hook. Darker documentation: https://pypi.org/project/darker/. pre-commit documentation: https://pre-commit.com/#installation

- `pip install darker`
- `pip install pre-commit`
- `pre-commit install`
- `pre-commit autoupdate`

## Migrations

Migrations are handled by Alembic: https://alembic.sqlalchemy.org/. See here for a
tutorial: https://alembic.sqlalchemy.org/en/latest/tutorial.html.

To apply migrations:

- `alembic upgrade head`

To create new migrations:

- Make your changes in `models.py`
- Generate a migration file: `alembic revision --autogenerate -m "Your migration name here"`. Your migration file will
  be in `alembic/versions`.
- Apply your migration to the database: `alembic upgrade head`
- Commit your migration: `git add alembic/versions`

Common issues:

- Alembic does not pick up certain changes like renaming tables or columns
  correctly. For these changes you'll need to manually edit the migration file.
  See here for a full list of changes Alembic will not detect correctly:
  https://alembic.sqlalchemy.org/en/latest/autogenerate.html#what-does-autogenerate-detect-and-what-does-it-not-detect
- To set a default value for a column, you'll need to use `server_default`:
  https://docs.sqlalchemy.org/en/14/core/defaults.html#server-defaults. This sets
  a default on the database side.
- Alembic also sometimes has issues with constraints and naming. If you run into
  an issue like this, you may need to hand edit the migration. See here:
  https://alembic.sqlalchemy.org/en/latest/naming.html

# Bugs

# Wishlist

- Map-specific trueskill
- Position-specific trueskill
- Skip game map or queue map
- Convert from sqlite to postgres
- Refactor commands to use cogs
- Game betting
- Flask API

## Good first tickets

- Store player display name alongside regular name
- Allow voting for multiple maps at once
- Add created_at timestamps to all tables (esp finished_game_player)
- Store total games played, win/loss/tie record

# Full list of commands

```
add
addadmin
addadminrole
addmap
addqueueregion
addqueuerole
addrotationmap
ban
cancelgame
changegamemap
changequeuemap
clearqueue
coinflip
createcommand
createdbbackup
createqueue
decayplayer
del
delplayer
editcommand
editgamewinner
finishgame
gamehistory
help
imagetest
imagetest2
isolatequeue
leaderboard
listadminroles
listadmins
listbans
listdbbackups
listmaprotation
listmaps
listnotifications
listplayerdecays
listqueueregions
listqueueroles
lockqueue
lt
map
mockrandomqueue
notify
pug
randommap
removeadmin
removeadminrole
removecommand
removedbbackup
removemap
removenotifications
removequeue
removequeueregion
removequeuerole
removerotationmap
resetplayertrueskill
restart
roll
setadddelay
setbias
setcommandprefix
setmapvotethreshold
setqueuerated
setqueueregion
setqueueunrated
showgame
showgamedebug
stats
status
streams
sub
trueskill
unban
unisolatequeue
unlockqueue
unsetqueueregion
unvote
unvotemap
unvoteskip
votemap
voteskip
```
