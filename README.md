
# Installation

## Mac

### Homebrew 

https://brew.sh/#install
### Python 3.10.0
With pyenv:
- `brew install pyenv`
- `pyenv install 3.10.0`
- `pyenv global 3.10.0`

Without pyenv:
https://docs.python-guide.org/starting/install3/osx/

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
- `pip install -U .`
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

# To-do list

Feel free to help out!

MVP
- Custom commands !createcommand / !deletecommand
- Match history / editing
- Migrations with Alembic: https://alembic.sqlalchemy.org/en/latest/autogenerate.html
- Show teams for in-game status
- Queue eligibility
- Check admin by role
- Notify @ user when afk removed
- Case insensitive queue names (!add)
- add queue by integer (e.g. add 1 2 3)

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
