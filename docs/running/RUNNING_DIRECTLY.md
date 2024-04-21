# Running the bot directly

While [running with docker-compose](./RUNNING_WITH_DOCKER_COMPOSE.md) is usually
simpler, you can run the bot on your server or local system.

## Pre-requisites

* Python 3.10 or above
* Virtualenv
* Docker for running Postgres (or alternately, run it directly on your host too)

## Installing dependencies

### System dependencies

You will need a couple of specific system packages installed:
* `libpq` as a dependency of the `psycopg2` pip package
* `libjpeg-dev` as a dependency of the `Pillow` pip package
  * (this is only here as a dependency of scipy, which is only used in the plotting script and not the running app)

You can install these on various OSes as described below:

#### Ubuntu (or Ubuntu WSL)
```
sudo apt-get install libpq-dev libjpeg-dev
```

#### Arch linux
```
pacman -S postgresql-libs libjpeg-turbo
```

#### MacOS
```
brew install postgresql jpeg
```

### Python dependencies

The steps below assume MacOS, Linux or WSL on Windows.

```
# Set up a new virtualenv (only needed first time)
python3 -m venv .venv
# Activate the virtualenv
source .venv/bin/activate
# Install python dependencies
pip install -e .
```

## Running the database

Start a local database with:
```
sudo docker run --name postgres -e POSTGRES_PASSWORD=password -d -p 5432:5432 postgres
```

To restart an existing docker container, use:
```
sudo docker restart postgres
```

Install postgresql-client
```
sudo apt-get install postgresql-client
```

To connect with PSQL:
```
PGPASSWORD=password psql -h localhost -U postgres -d postgres -p 5432
```

In `.env`:
```
DATABASE_URI=postgresql://postgres:password@localhost:5432/postgres
```

## Running the application

From the root directory, with the database running,
```
# Activate the virtualenv
source .venv/bin/activate
# Run migrations and start the app
./startup.sh
```

### Pulling in updates to the app

```
git pull
alembic upgrade head
```

Then restart the python process.

### Running on Windows

Running within WSL and following the unix-like instructions is recommended.

If you see this error when activating the venv:
  ```
  File <>\discord-bots\.venv\Scripts\Activate.ps1 cannot be loaded because running scripts is disabled on this system. For more information, see about_Execution_Policies at https:/go.microsoft.com/fwlink/?LinkID=135170.
  ```
You may need to adjust your windows execution policy:
https://stackoverflow.com/a/18713789