# Running the bot with docker-compose

Docker-compose is the most straightforward way to run the app.

### Requirements

* Docker
* Docker compose (usually included with Docker)

Docker avoids the need to install other requirements on your host system.

### Caveats

This is not yet well-tested for production use-cases. It was created with the
intent of helping new contributors to 'just have something working' right away,
so that they can learn about the bot quickly.

In theory, you could use it for a live bot - but until the Docker method is more
polished, you're on your own with that one!

### Executing

With a suitable .env filled out, run in the project root directory:

- `docker compose build && docker compose up`

You will probably get errors the first time you try to do this. Simply CTRL+C
out, let it stop, and then start it again. It seems like the database will
initialize on the first run, but not in time to actually function. Everything
should work on the second boot.

There are two Docker containers: one for the postgres database (`db`) and one
for the bot itself (`tribesbot`).

The first time you spin up the `tribesbot` container, your system will probably
ask you to share the `discord_bots` and `scripts` folders. The container is
configured to treat these folders as bind mounts, meaning that the container is
reaching directly into your host system to get the files for the bot.

This allows you to make code changes without having to rebuild the image each
time you change anything. It also means that you can just use the !restart
command in Discord to put new code into action!

#### Executing with sqlite

There is an alternate compose file, `docker-compose-sqlite.yml`, that allows the
use of sqlite instead of postgres. This is not recommended for new databases,
and is only there to grandfather in old sqlite dbs. See the comments in that
file for more info on how to use it.