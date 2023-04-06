import os
from datetime import datetime, timezone
from tempfile import NamedTemporaryFile

import discord
import imgkit
from discord import Colour, Embed, Member, Message, Reaction
from discord.abc import User
from discord.ext.commands import CommandError, Context, UserInputError
from dotenv import load_dotenv
from PIL import Image

from .bot import COMMAND_PREFIX, bot
from .models import CustomCommand, Player, QueuePlayer, QueueWaitlistPlayer, Session
from .tasks import (
    add_player_task,
    afk_timer_task,
    map_rotation_task,
    queue_waitlist_task,
    vote_passed_waitlist_task,
)
from .utils import CHANNEL_ID

add_player_task.start()
afk_timer_task.start()
map_rotation_task.start()
queue_waitlist_task.start()
vote_passed_waitlist_task.start()

load_dotenv()
SEED_ADMIN_IDS = os.getenv("SEED_ADMIN_IDS")
if SEED_ADMIN_IDS:
    session = Session()
    seed_admin_ids = SEED_ADMIN_IDS.split(",")
    for seed_admin_id in seed_admin_ids:
        # There always has to be at least one initial admin to add others!
        player = session.query(Player).filter(Player.id == seed_admin_id).first()
        if player:
            player.is_admin = True
            session.commit()
    session.close()


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")


@bot.event
async def on_command_error(ctx: Context, error: CommandError):
    if isinstance(error, UserInputError):
        if ctx.command.usage:
            await ctx.channel.send(
                embed=Embed(
                    description=f"Usage: {COMMAND_PREFIX}{ctx.command.name} {ctx.command.usage}",
                    colour=Colour.red(),
                )
            )
        else:
            await ctx.channel.send(
                embed=Embed(
                    description=f"Usage: {COMMAND_PREFIX}{ctx.command.name} {ctx.command.signature}",
                    colour=Colour.red(),
                )
            )
    else:
        if ctx.command:
            print("[on_command_error]:", error, ", command:", ctx.command.name)
        else:
            print("[on_command_error]:", error)


@bot.event
async def on_message(message: Message):
    if CHANNEL_ID and message.channel.id == CHANNEL_ID:
        session = Session()
        player: Player | None = (
            session.query(Player).filter(Player.id == message.author.id).first()
        )
        if player:
            player.last_activity_at = datetime.now(timezone.utc)
            if player.name != message.author.display_name:
                player.name = message.author.display_name
        else:
            session.add(
                Player(
                    id=message.author.id,
                    name=message.author.display_name,
                    last_activity_at=datetime.now(timezone.utc),
                )
            )
        session.commit()
        session.close()
        await bot.process_commands(message)

        # Custom commands below
        if not message.content.startswith(COMMAND_PREFIX):
            return

        bot_commands = {command.name for command in bot.commands}
        command_name = message.content.split(" ")[0][1:]
        session = Session()
        if command_name not in bot_commands:
            custom_command: CustomCommand | None = (
                session.query(CustomCommand)
                .filter(CustomCommand.name == command_name)
                .first()
            )
            if custom_command:
                await message.channel.send(content=custom_command.output)
        session.close()
    else:
        # Hardcode allow this command to work in other channels
        if message.content == '!lt':
            query_url = "http://tribesquery.toocrooked.com/hostQuery.php?server=207.148.13.132:28006&port=28006"
            await message.channel.send(query_url)

            ntf = NamedTemporaryFile(delete=True, suffix=".png")
            imgkit.from_url(query_url, ntf.name)
            image = Image.open(ntf.name)
            cropped = image.crop((0, 0, 450, 650))
            cropped.save(ntf.name)
            await message.channel.send(file=discord.File(ntf.name))
            ntf.close()


@bot.event
async def on_reaction_add(reaction: Reaction, user: User | Member):
    session = Session()
    player: Player | None = session.query(Player).filter(Player.id == user.id).first()
    if player:
        player.last_activity_at = datetime.now(timezone.utc)
        session.commit()
    else:
        session.add(
            Player(
                id=reaction.message.author.id,
                name=reaction.message.author.display_name,
                last_activity_at=datetime.now(timezone.utc),
            )
        )
    session.close()


@bot.event
async def on_join(member: Member):
    session = Session()
    player = session.query(Player).filter(Player.id == member.id).first()
    if player:
        player.name = member.name
        session.commit()
    else:
        session.add(Player(id=member.id, name=member.name))
        session.commit()
    session.close()


@bot.event
async def on_leave(member: Member):
    session = Session()
    session.query(QueuePlayer).filter(QueuePlayer.player_id == member.id).delete()
    session.query(QueueWaitlistPlayer).filter(
        QueueWaitlistPlayer.player_id == member.id
    ).delete()
    session.commit()


def main():
    load_dotenv()
    API_KEY = os.getenv("DISCORD_API_KEY")
    if API_KEY:
        bot.run(API_KEY)
    else:
        print("You must define DISCORD_API_KEY!")


if __name__ == "__main__":
    main()
