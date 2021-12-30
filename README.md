
# Installation

## Mac

### Homebrew 

Install Homebrew: https://brew.sh/#install
### Python 3.10.0
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
- `run-discord-bot`

## Linux

The steps are mostly similar, but you have to install Python another way

- Install Python 3
- Still recommended to use a virtualenv
- `pip install -e .`. This allows local changes to be picked up without needing
to reinstall
- `cp .env.example .env`. Modify `.env` by adding your API key
- `run-discord-bot`

# Development
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

## Migrations
Migrations are handled by Alembic: https://alembic.sqlalchemy.org/.

- Make your changes in `models.py`
- Generate a migration file: `alembic revision --autogenerate -m "Your migration name here"`. Alembic will automatically pick up changes. Your migration file will be in `alembic/migrations`.
- Apply your migration to the database: `alembic upgrade head`
- Commit your migration

Alembic does not pick up certain changes - for these changes you'll need to manually edit the migration file. Examples of changes Alembic detects incorrectly are renaming tables or columns - Alembic will think the old thing was deleted and the new thing is brand new. This is important! See here for a full list of changes Alembic will not detect correctly: https://alembic.sqlalchemy.org/en/latest/autogenerate.html#what-does-autogenerate-detect-and-what-does-it-not-detect


See here for detailed instructions: https://alembic.sqlalchemy.org/en/latest/tutorial.html

# To-do list

Feel free to help out!

MVP
- Queue eligibility
- Check admin by role
- Match history / editing
- Custom commands !createcommand / !deletecommand
- Migrations with Alembic: https://alembic.sqlalchemy.org/en/latest/autogenerate.html
- add queue by integer (e.g. add 1 2 3)
- queue locking / unlocking

Nice to have
- setup.py

MVP+
- Recognizable words for game ids and team names
- In-server queue
- Map picking problem
- Queue notifications
- Enable strict typing configuration
- Shazbucks
- Expose API for frontends
