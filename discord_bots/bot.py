# This file exists to avoid a circular reference

import os

import discord
from discord.ext import commands
from dotenv import load_dotenv

intents = discord.Intents.default()
intents.members = True

load_dotenv()
COMMAND_PREFIX: str = os.getenv("COMMAND_PREFIX") or "!"


bot = commands.Bot(
    case_insensitive=True,
    command_prefix=COMMAND_PREFIX,
    help_command=commands.DefaultHelpCommand(
        width=108, verify_checks=False, dm_help=True
    ),
    intents=intents,
)
