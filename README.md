
# Running the bot

## macOS Instructions
### Python 3.10.0
Install Homebrew: https://brew.sh/#install

With pyenv:
- `brew install pyenv`
- `pyenv install 3.10.0`
- `pyenv global 3.10.0`

Without pyenv:
- https://docs.python-guide.org/starting/install3/osx/

With a virtualenv (optional but recommended):
- `cd discord-bots`
- `python3 -m venv .venv`
- `source .venv/bin/activate`

After:
- `pip install -U .`
- `cp .env.example .env`. Modify `.env` by adding your API key

Run the bot:
- `python scripts/run.py`

## Anything not macOS

The steps are the same except you have to install Python another way
# Development

## Installation
Installation steps are the same but use `pip install -e .`.  This allows local changes to be picked up without needing to reinstall the package every time.

## Pre-commit hook
This project uses `darker` for formatting in a pre-commit hook. Install using `pre-commit install`
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

## Tests
- `pytest`

I haven't setup alembic to cooperate with the test database. If you add a new
migration, delete the test db and it should work.

## Migrations
Migrations are handled by Alembic: https://alembic.sqlalchemy.org/.

To apply migrations to an existing database:
- `alembic upgrade head`

To create new migrations:
- Make your changes in `models.py`
- Generate a migration file: `alembic revision --autogenerate -m "Your migration name here"`. Alembic will automatically pick up changes. Your migration file will be in `alembic/versions`.
- Apply your migration to the database: `alembic upgrade head`
- Commit your migration: `git add alembic/versions`

Alembic does not pick up certain changes like renaming tables or columns correctly. For these changes you'll need to manually edit the migration file. See here for a full list of changes Alembic will not detect correctly: https://alembic.sqlalchemy.org/en/latest/autogenerate.html#what-does-autogenerate-detect-and-what-does-it-not-detect

Alembic also sometimes has issues with constraints and naming. If you run into an issue like this, you may need to hand edit the migration. See here: https://alembic.sqlalchemy.org/en/latest/naming.html

See here for detailed instructions on how to use Alembic: https://alembic.sqlalchemy.org/en/latest/tutorial.html

# To-do list

Feel free to help out!

Nice to have
- CI for Pyright: https://github.com/microsoft/pyright/blob/main/docs/command-line.md
- CI for Tests
- Strict typing configuration

MVP+
- Recognizable words for game ids and team names
- In-server queue
- Map picking problem
- Queue notifications
- Shazbucks
- Expose Flask API: https://flask.palletsprojects.com/en/2.0.x/
