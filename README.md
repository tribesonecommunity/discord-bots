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
- Database locking due to sqlite concurrency issues
- Migrations with Alembic
- Custom commands !createcommand / !deletecommand
- Match history / editing
- Show teams for in-game status

MVP+
- In-server queue
- Map picking problem
- Queue notifications
- Enable strict typing configuration
- Recognizable words for game ids and team names
- Team table? e.g. game -> game_team -> game_team_player
