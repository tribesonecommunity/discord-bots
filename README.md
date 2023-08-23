# How to run the bot

## Forks

Sauon and sh4z have both maintained separate forks with some improvements - check them out!
- https://github.com/Sauon/discord-bots
- https://github.com/sh4zbot/discord-bots

## Installation

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

### Windows
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
- `SEED_ADMIN_IDS` - Discord ids of players that will start off as admin. You'll need at least one in order to create more

The following are optional
- `STATS_DIR`, `STATS_WIDTH`, `STATS_HEIGHT` - The bot assumes the last file dumped here is html file of the stats of the last game finished. It will take a screenshot and upload an image to the channel and delete it
- `TWITCH_GAME_NAME`, `TWITCH_CLIENT_ID`, `TWITCH_CLIENT_SECRET` - These enable the `streams` command to list current streams of the specified game
- `COMMAND_PREFIX` - Use a different prefix instead of `!`
- `RANDOM_MAP_ROTATION` - Declare this for the map rotation to be random
- `DEFAULT_TRUESKILL_MU`, `DEFAULT_TRUESKILL_SIGMA` - Declare this to set the default trueskill value for new players
- `SHOW_TRUESKILL` - Shows player trueskill when making teams, enables the trueskill leaderboard, etc.


## Running the bot

1. `cd discord-bots`
1. `source .venv/bin/activate`
1. `python -m discord_bots.main`

# Development

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

This project uses `darker` for formatting in a pre-commit hook. Install using `pre-commit install`

## Migrations

Migrations are handled by Alembic: https://alembic.sqlalchemy.org/. See here for a tutorial: https://alembic.sqlalchemy.org/en/latest/tutorial.html.

To apply migrations:

- `alembic upgrade head`

To create new migrations:

- Make your changes in `models.py`
- Generate a migration file: `alembic revision --autogenerate -m "Your migration name here"`. Your migration file will be in `alembic/versions`.
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

# To-do list

- Map-specific trueskill rating
- Start map rotation only after game finishes
- Convert from sqlite to postgres
- Refactor commands file
- Automatically show rotation maps in voteable maps and when a rotation map is voted, just rotate to it
- Fix tests

## Good first tickets
- Store player display name alongside regular name
- Store total games played
- Allow voting for multiple maps at once
- Store total games played, win/loss/tie record
- Add created_at timestamps to all tables (esp finished_game_player)

MVP+

- Shazbucks
- Expose Flask API: https://flask.palletsprojects.com/en/2.0.x/
