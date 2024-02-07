# This file exists to avoid a circular reference

import discord
from discord.ext import commands
import discord_bots.config as config

intents = discord.Intents.default()
intents.members = True

bot = commands.Bot(
    case_insensitive=True,
    command_prefix=config.COMMAND_PREFIX,
    help_command=commands.DefaultHelpCommand(
        width=108, verify_checks=False, dm_help=True
    ),
    intents=intents,
)
