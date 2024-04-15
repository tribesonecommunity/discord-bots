# Setting up the bot with Discord

To use the bot for either local dev or production, you will need to create a
Discord App and install it on your target Discord server. This will give you a
bot token / API key for use with the application.

## Creating the Discord App

1. Create a new app in the [Discord developer
   portal](https://discord.com/developers) for your bot
2. Go to the `Bot` tab of your app, and enable all Privileged Gateway Intents:
   `Presence`, `Server Members` and `Message Contents`
3. In the `OAuth2` tab's `OAuth2 URL Generator` section, select the `bot` scope,
   and `Administrator` permissions, and copy the generated installation URL
4. In a text channel of your target discord server, paste the installation URL
   and click it; follow the prompts to install your bot
5. Back in the `Bot` tab of the developer portal, generate a new `Token` for
   your bot and keep this handy. You will need to specify it in your `.env` file
   as the `DISCORD_API_KEY`

At this point, your bot is now set up from a Discord perspective - you just need
to actually run it!

## Setting up the .env file

To actually run the bot, you will need to provide it config to interact with
Discord.

In particular you will need to provide your bot's token as the
`DISCORD_API_KEY`, and various channel IDs.

Instructions for how to obtain these is in the comments of the `.env.example`
file, which you should copy for your own `.env`.

## Next steps

From here, you may want to look at [how to run the bot](./RUNNING.md).
