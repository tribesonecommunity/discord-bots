from datetime import datetime, timezone
import os
import traceback

from discord import Member, Message, Reaction
from discord.abc import User
from discord.channel import GroupChannel, TextChannel
from dotenv import load_dotenv

from .bot import bot
from .commands import handle_message
from .models import Player, QueuePlayer, Session
from .tasks import (
    afk_timer_task,
    queue_waitlist_task,
)

afk_timer_task.start()
queue_waitlist_task.start()


BULLIEST_BOT_ID = 912605788781035541
LYON_ID = 193359832340889600
OPSAYO_MEMBER_ID = 115204465589616646

session = Session()
# There always has to be at least one initial admin to add others!
player = session.query(Player).filter(Player.id == OPSAYO_MEMBER_ID).first()
if player:
    player.is_admin = True
    session.commit()
player = session.query(Player).filter(Player.id == LYON_ID).first()
if player:
    player.is_admin = True
    session.commit()
session.close()



@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")


@bot.event
async def on_message(message: Message):
    if type(message.channel) is TextChannel or type(message.channel) is GroupChannel:
        if (
            message.channel.name == "bullies-bot"
            and message.author.id != BULLIEST_BOT_ID
        ):
            print("[on_message]", message)
            try:
                await handle_message(message)
            except Exception as e:
                print(e)
                traceback.print_exc()
                await message.channel.send(f"Encountered exception: {e}")


@bot.event
async def on_reaction_add(reaction: Reaction, user: User | Member):
    session = Session()
    player: Player | None = session.query(Player).filter(Player.id == user.id).first()
    if player:
        player.last_activity_at = datetime.now(timezone.utc)
        session.commit()


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


@bot.event
async def on_leave(member: Member):
    session = Session()
    session.query(QueuePlayer).filter(QueuePlayer.player_id == member.id).delete()
    session.commit()


def main():
    load_dotenv()
    API_KEY = os.getenv("DISCORD_API_KEY")
    if API_KEY:
        bot.run(API_KEY)
    else:
        print("You must define DISCORD_API_KEY!")
