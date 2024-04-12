# What is this?

This an open source discord bot which assists with game matchmaking.

This uses Microsoft's
[Trueskill](https://www.microsoft.com/en-us/research/project/trueskill-ranking-system/)
matchmaking algorithm to create fair and balanced teams. Players are assigned a
level of skill (mu) and uncertainty (sigma) which is adjusted after each game.

Games are organized using a queueing system and the bot supports features like
segmentation by region, game type, ranked / unranked, map rotations, voice
channel creation, leaderboards, Twitch integration, dice rolls, coin flips,
raffles, commends, post-game images, and more.

## Installing the bot on your server

See [bot setup](./docs/BOT_SETUP.md) and [app execution](./docs/RUNNING.md) docs
for info on how to host and run the bot for your Discord server.

## Using the bot

See the [command reference](./docs/BOT_COMMANDS.md) for the full set of commands
you can use with the bot when installed.

## Development

See [local dev docs](./docs/LOCAL_DEVELOPMENT.md) for info on how to develop and
run the bot locally for testing.