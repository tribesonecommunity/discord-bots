# How to run the bot application

This document covers running the bot application for either prod or local dev
use-cases.

## Pre-requisites

Before attempting to run the bot, you should have run through the [bot
setup](./BOT_SETUP.md) and have:
* a Discord developer portal app
* A filled out `.env` file including the `DISCORD_API_KEY` from the above app

## Running the bot

For both prod and local dev use-cases, you have two options for how to run the
bot:
1. [Running via docker-compose (recommended), which requires minimal system
   setup](./running/RUNNING_WITH_DOCKER_COMPOSE.md)
2. [Running the app directly on your system, which requires some dependency
   setup](./running/RUNNING_DIRECTLY.md)