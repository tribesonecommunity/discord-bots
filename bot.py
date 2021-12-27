# This file exists to avoid a circular reference

from discord.ext import commands as discord_commands
import discord

intents = discord.Intents.default()
intents.members = True

BOT = discord_commands.Bot("?", intents=intents)