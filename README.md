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
Until I learn how to commit this to the repo, if you use vscode add this to your settings.json:
https://www.emmanuelgautier.com/blog/enable-vscode-python-type-checking
```json
{
  "python.analysis.typeCheckingMode": "basic"
}
```

## Formatting
Use python black: https://github.com/psf/black

## Tests
- `pytest`

# To-do list

Feel free to help out!

MVP
- Queue delay between games (see QueueFinishTimer, Queue.addPlayersWaiting)
- Player subs
- AFK / idle timer (see AFKTimer)
- Bot picks
- Custom commands !createcommand / !deletecommand
- Match history / editing

MVP+
- In-server queue
- Map picking problem