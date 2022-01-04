# How to run the bot

## Installation

1. Install Python 3.1.0: https://docs.python-guide.org/starting/install3/osx/

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
1. (optional) Import match history:
   - `curl "http://50.116.36.119/api/server/127155819698454529/games/1546300801000" > out.json`
   - `python scripts/import_match_history.py`

## Running the bot

1. `cd discord-bots`
1. `source .venv/bin/activate`
1. `python scripts/run.py`

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

- Go to vscode preferences (cmd + `,` on mac)
- Type "python formatting" in the search bar
- For the option `Python > Formatting: Provider` select `black`

### Pre-commit hook

This project uses `darker` for formatting in a pre-commit hook. Install using `pre-commit install`

## Tests

- `pytest`

I haven't setup alembic to cooperate with the test database. If you add a new
migration, delete the test db (`rm tribes.test.db`) and the code will migrate your new database.

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

# To-do list

- Buffer voters into queue when vote succeeds?
- Give queuers a chance to del when vote succeeds?

Feel free to help out!

- Surface current map, next map, vote status in status and game history
- Update migration to rename / alter column rather than drop/replace: `alembic/versions/20220103111554_fdb66df22216_add_rated_and_unrated_trueskill.py`
- Mark mock games as mock games for easier deletion
- De-dupe pretty-printing for in progress games

MVP+

- In-server queue
- Queue notifications
- Shazbucks
- Expose Flask API: https://flask.palletsprojects.com/en/2.0.x/
- CI for Pyright: https://github.com/microsoft/pyright/blob/main/docs/command-line.md
- CI for Tests

Maybe?

- Strict typing configuration
