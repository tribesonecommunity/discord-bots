# Development

## Python 3.10.0
With pyenv:
- `brew install pyenv`
- `pyenv install 3.10.0`
- `pyenv global 3.10.0`

With homebrew:
- TODO

(Recommended) Use a `virtualenv`
- `cd discord-bots`
- `python3 -m venv .venv`
- `source .venv/bin/activate`

After the above:
- `pip install -r requirements.txt`
- `cp .env.example .env`. Modify `.env` by adding your API key


## Editor
Recommend using vscode. If you do, install these plugins
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
- Recognizable words for game ids and team names
- Check admin by role
- Notify @ user when afk removed
- Case insensitive queue names (!add)

MVP+
- In-server queue
- Map picking problem
- Queue notifications
- Enable strict typing configuration
- Shazbucks
- Expose API for frontends
