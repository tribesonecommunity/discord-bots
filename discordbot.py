# This example requires the 'members' privileged intents

from datetime import datetime, timezone
from typing import List
import os
import re
import traceback

from discord import Message, Reaction
from discord.abc import User
from discord.channel import GroupChannel, TextChannel
from dotenv import load_dotenv
from sqlalchemy.exc import IntegrityError

from bot import bot
from commands import handle_message
from models import Player, QueuePlayer, Session
from tasks import afk_timer_task, create_voice_channel_task, queue_waitlist_task, send_message_task
from test_commands import Member


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    print("------")


@bot.event
async def on_connect():
    try:
        send_message_task.start()
    except RuntimeError as e:
        print("Encountered exception:", e)
    try:
        create_voice_channel_task.start()
    except RuntimeError as e:
        print("Encountered exception:", e)
    try:
        afk_timer_task.start()
    except RuntimeError as e:
        print("Encountered exception:", e)
    try:
        queue_waitlist_task.start()
    except RuntimeError as e:
        print("Encountered exception:", e)




BULLIEST_BOT_ID = 912605788781035541


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
    player: Player = (
        session.query(Player).filter(Player.id == user.id).first()
    )
    if player:
        player.last_activity_at = datetime.now(timezone.utc)
        session.add(player)
        session.commit()


@bot.event
async def on_join(member: Member):
    session = Session()
    try:
        session.add(Player(id=member.id, name=member.name))
        session.commit()
    except IntegrityError:
        pass


@bot.event
async def on_leave(member: Member):
    session = Session()
    session.query(QueuePlayer).filter(QueuePlayer.player_id == member.id).delete()


@bot.event
async def on_message_old(message):
    if message.author.id == 359925573134319628:
        print(message.embeds[0].to_dict()["description"])

    if message.author.id == 359925573134319628 and (
        message.content.startswith("`Game 'LTbot' has begun")
        or message.content.startswith("`Game 'LTunrated' has begun")
        or message.content.startswith("`Game 'LTpug' has begun")
        or message.content.startswith("`Game 'LTsilver' has begun")
        or message.content.startswith("`Game 'LTgold' has begun")
        or message.content.startswith("`Game 'testbot' has begun")
    ):
        print(message.content)
        print(message.embeds)
        print(type(message.embeds))

        # print(message.embeds[0])

        game_started = True

        re1 = ".*?"  # Non-greedy match on filler
        re2 = "(\\d+)"  # Integer Number 1
        re3 = ".*?"  # Non-greedy match on filler
        re4 = "(\\d+)"  # Integer Number 2
        re5 = ".*?"  # Non-greedy match on filler
        re6 = "\\n(.*)"  # Any Single Character 1

        rg = re.compile(re1 + re2 + re3 + re4 + re5 + re6, re.IGNORECASE | re.DOTALL)
        m = rg.search(message.embeds[0].to_dict()["description"])
        if m:
            captain1 = m.group(1)
            captain2 = m.group(2)
            c1 = m.group(3)
            # print("("+int1+")"+"("+int2+")"+"("+c1+")"+"\n")

        player_list = c1.split(", ")

        print(player_list)

        discord_ids = []

        for player in player_list:
            discord_ids.append(int(message.guild.get_member_named(player).id))

        import import_data_best_teams as import_data

        cap1 = captain1.strip()
        cap2 = captain2.strip()

        matchup = import_data.make_teams(int(cap1), int(cap2), discord_ids)

        win_factor = matchup[0]
        team1 = matchup[1]
        team2 = matchup[2]
        team1names = []
        team2names = []

        for player in team1:
            player_gen = message.guild.get_member(player)
            player_name = player_gen.name
            team1names.append(player_name)

        for player in team2:
            player_gen = message.guild.get_member(player)
            player_name = player_gen.name
            team2names.append(player_name)

        await message.channel.send(win_factor)
        await message.channel.send(team1names)
        await message.channel.send(team2names)

    if message.content.startswith("!newteams"):  # and game_started:

        remove_command = message.content.split("!newteams ")
        print(remove_command)
        player_list = remove_command[1].split(", ")
        print(player_list)
        discord_ids = []

        for player in player_list:
            # print(player)
            # print(discord_ids)
            discord_ids.append(int(message.guild.get_member_named(player).id))

        import import_data_best_teams as import_data

        matchup = import_data.make_teams(
            int(discord_ids[0]), int(discord_ids[1]), discord_ids[2:]
        )

        win_factor = matchup[0]
        team1 = matchup[1]
        team2 = matchup[2]
        team1names = []
        team2names = []

        for player in team1:
            player_gen = message.guild.get_member(player)
            player_name = player_gen.name
            team1names.append(player_name)

        for player in team2:
            player_gen = message.guild.get_member(player)
            player_name = player_gen.name
            team2names.append(player_name)

        await message.channel.send(win_factor)
        await message.channel.send(team1names)
        await message.channel.send(team2names)


load_dotenv()

API_KEY = os.getenv("DISCORD_API_KEY")
if API_KEY:
    bot.run(API_KEY)
else:
    print("You must define DISCORD_API_KEY!")
