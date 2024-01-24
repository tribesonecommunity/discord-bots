# Misc helper functions
import itertools
import math
import os
import statistics
from datetime import datetime, timezone
from random import choice

import discord
import imgkit
from discord import Colour, DMChannel, Embed, GroupChannel, TextChannel
from discord.ext.commands.context import Context
from matplotlib import image
from PIL import Image
from selenium import webdriver
from selenium.webdriver.firefox.options import Options as FirefoxOptions
from trueskill import Rating, global_env

from discord_bots.bot import bot
from discord_bots.config import LEADERBOARD_CHANNEL, SHOW_TRUESKILL
from discord_bots.models import (
    Map,
    MapVote,
    Player,
    PlayerRegionTrueskill,
    Queue,
    QueueRegion,
    RotationMap,
    Session,
    SkipMapVote,
)

CHANNEL_ID = int(os.getenv("CHANNEL_ID"))

STATS_DIR: str | None = os.getenv("STATS_DIR")


# Convenience mean function that can handle lists of 0 or 1 length
def mean(values: list[any]) -> float:
    if len(values) == 0:
        return -1
    else:
        return statistics.mean(values)


def pretty_format_team(
    team_name: str, win_probability: float, players: list[Player]
) -> str:
    player_names = ", ".join(
        sorted(
            [
                f"**(C) {player.name}**" if i == 0 else player.name
                for i, player in enumerate(players)
            ]
        )
    )
    # TODO: This isn't right for games with regions
    # team_mu = round(mean(list(player.rated_trueskill_mu for player in players)), 2)
    # if SHOW_TRUESKILL:
    #     return f"**{team_name}** ({round(100 * win_probability, 1)}%, mu: {team_mu}): {player_names}\n"
    return f"**{team_name}** ({round(100 * win_probability, 1)}%): {player_names}\n"


def short_uuid(uuid: str) -> str:
    return uuid.split("-")[0]


async def upload_stats_screenshot_selenium(ctx: Context, cleanup=True):
    # Assume the most recently modified HTML file is the correct stat sheet
    if not STATS_DIR:
        return

    html_files = list(filter(lambda x: x.endswith(".html"), os.listdir(STATS_DIR)))
    html_files.sort(
        key=lambda x: os.path.getmtime(os.path.join(STATS_DIR, x)), reverse=True
    )

    opts = FirefoxOptions()
    opts.add_argument("--headless")
    driver = webdriver.Firefox(options=opts)
    if len(html_files) == 0:
        return

    driver.get("file://" + os.path.join(STATS_DIR, html_files[0]))
    image_path = os.path.join(STATS_DIR, html_files[0] + ".png")
    driver.save_screenshot(image_path)
    image = Image.open(image_path)
    # TODO: Un-hardcode these
    cropped = image.crop((0, 0, 750, 650))
    cropped.save(image_path)

    await ctx.message.channel.send(file=discord.File(image_path))

    # Clean up everything
    if cleanup:
        for file_ in os.listdir(STATS_DIR):
            if file_.endswith(".png") or file_.endswith(".html"):
                os.remove(os.path.join(STATS_DIR, file_))


async def upload_stats_screenshot_imgkit(ctx: Context, cleanup=True):
    # Assume the most recently modified HTML file is the correct stat sheet
    if not STATS_DIR:
        return

    html_files = list(filter(lambda x: x.endswith(".html"), os.listdir(STATS_DIR)))
    html_files.sort(
        key=lambda x: os.path.getmtime(os.path.join(STATS_DIR, x)), reverse=True
    )

    if len(html_files) == 0:
        return

    image_path = os.path.join(STATS_DIR, html_files[0] + ".png")
    imgkit.from_file(
        os.path.join(STATS_DIR, html_files[0]),
        image_path,
        options={"enable-local-file-access": None},
    )
    if os.getenv("STATS_WIDTH") and os.getenv("STATS_HEIGHT"):
        image = Image.open(image_path)
        # TODO: Un-hardcode these
        cropped = image.crop(
            (0, 0, int(os.getenv("STATS_WIDTH")), int(os.getenv("STATS_HEIGHT")))
        )
        cropped.save(image_path)

    await ctx.message.channel.send(file=discord.File(image_path))

    # Clean up everything
    if cleanup:
        for file_ in os.listdir(STATS_DIR):
            if file_.endswith(".png") or file_.endswith(".html"):
                os.remove(os.path.join(STATS_DIR, file_))


def win_probability(team0: list[Rating], team1: list[Rating]) -> float:
    """
    Calculate the probability that team0 beats team1
    Taken from https://trueskill.org/#win-probability
    """
    BETA = 4.1666
    delta_mu = sum(r.mu for r in team0) - sum(r.mu for r in team1)
    sum_sigma = sum(r.sigma**2 for r in itertools.chain(team0, team1))
    size = len(team0) + len(team1)
    denom = math.sqrt(size * (BETA * BETA) + sum_sigma)
    trueskill = global_env()

    return trueskill.cdf(delta_mu / denom)


async def update_next_map_to_map_after_next(rotation_id: str, is_task: bool):
    """
    :is_task: specifies whether this call came from map_rotation_task to display different messages
    """
    session = Session()

    next_rotation_map: RotationMap | None = (
        session.query(RotationMap)
        .filter(RotationMap.rotation_id == rotation_id)
        .filter(RotationMap.is_next == True)
        .first()
    )

    rotation_map_length = (
        session.query(RotationMap)
        .filter(RotationMap.rotation_id == rotation_id)
        .count()
    )
    rotation_map_after_next_ordinal = next_rotation_map.ordinal + 1
    if rotation_map_after_next_ordinal > rotation_map_length:
        rotation_map_after_next_ordinal = 1

    next_rotation_map.is_next = False
    (
        session.query(RotationMap)
        .filter(RotationMap.rotation_id == rotation_id)
        .filter(RotationMap.ordinal == rotation_map_after_next_ordinal)
        .update({"is_next": True})
    )

    map_after_next_name: str | None = (
        session.query(Map.full_name)
        .join(RotationMap, RotationMap.map_id == Map.id)
        .filter(RotationMap.rotation_id == rotation_id)
        .filter(RotationMap.is_next == True)
        .scalar()
    )

    channel = bot.get_channel(CHANNEL_ID)
    if isinstance(channel, discord.TextChannel):
        if is_task:
            rotation_queues = (
                session.query(Queue.name).filter(Queue.rotation_id == rotation_id).all()
            )
            rotation_queue_names = ""
            for name in rotation_queues:
                rotation_queue_names += f"\n- {name[0]}"

            await send_message(
                channel,
                embed_description=f"Map automatically rotated to **{map_after_next_name}**, all votes removed\n\nQueues affected:{rotation_queue_names}",
                colour=Colour.blue(),
            )
        else:
            await send_message(
                channel,
                embed_description=f"Map rotated to **{map_after_next_name}**, all votes removed",
                colour=Colour.blue(),
            )

    session.query(MapVote).delete()
    session.query(SkipMapVote).delete()
    session.commit()
    session.close()


async def send_message(
    channel: (DMChannel | GroupChannel | TextChannel),
    content: str | None = None,
    embed_description: str | None = None,
    colour: Colour | None = None,
    embed_content: bool = True,
    embed_title: str | None = None,
    embed_thumbnail: str | None = None,
):
    """
    :colour: red = fail, green = success, blue = informational
    """
    if content:
        if embed_content:
            content = f"`{content}`"
    embed = None
    if embed_title or embed_thumbnail or embed_description or colour:
        embed = Embed()
    if embed_title:
        embed.title = embed_title
    if embed_thumbnail:
        embed.set_thumbnail(url=embed_thumbnail)
    if embed_description:
        embed.description = embed_description
    if colour:
        embed.colour = colour
    try:
        await channel.send(content=content, embed=embed)
    except Exception as e:
        print("[send_message] exception:", e)


async def print_leaderboard(test_message: str | None = None):
    if not LEADERBOARD_CHANNEL:
        return

    output = "**Leaderboard**"
    session = Session()
    queue_regions: list[QueueRegion] = session.query(QueueRegion).all()
    if len(queue_regions) > 0:
        for queue_region in queue_regions:
            output += f"\n_{queue_region.name}_"
            top_10_prts: list[PlayerRegionTrueskill] = (
                session.query(PlayerRegionTrueskill)
                .filter(PlayerRegionTrueskill.queue_region_id == queue_region.id)
                .order_by(PlayerRegionTrueskill.leaderboard_trueskill.desc())
                .limit(10)
            )
            for i, prt in enumerate(top_10_prts, 1):
                player: Player = (
                    session.query(Player).filter(Player.id == prt.player_id).first()
                )
                output += f"\n{i}. {round(prt.leaderboard_trueskill, 1)} - {player.name} _(mu: {round(prt.rated_trueskill_mu, 1)}, sigma: {round(prt.rated_trueskill_sigma, 1)})_"
            output += "\n"
        pass
    else:
        output = "**Leaderboard**"
        top_40_players: list[Player] = (
            session.query(Player)
            .filter(Player.rated_trueskill_sigma != 5.0)  # Season reset
            .filter(Player.rated_trueskill_sigma != 12.0)  # New player
            .filter(Player.leaderboard_enabled == True)
            # .order_by(Player.leaderboard_trueskill.desc())
            # .limit(20)
        )
        players_adjusted = sorted(
            [
                (player.rated_trueskill_mu - 3 * player.rated_trueskill_sigma, player)
                for player in top_40_players
            ],
            reverse=True,
        )[0:50]
        for i, (_, player) in enumerate(players_adjusted, 1):
            output += f"\n{i}. {round(player.leaderboard_trueskill, 1)} - {player.name} _(mu: {round(player.rated_trueskill_mu, 1)}, sigma: {round(player.rated_trueskill_sigma, 1)})_"
    session.close()
    output += "\n(Ranks calculated using the formula: _mu - 3*sigma_)"
    output += "\n(Leaderboard updates periodically)"
    output += "\n(!disableleaderboard to hide yourself from the leaderboard)"

    if test_message:
        output = test_message

    channel = bot.get_channel(LEADERBOARD_CHANNEL)
    if channel:
        try:
            last_message = await channel.fetch_message(channel.last_message_id)
            if last_message:
                await last_message.edit(embed=Embed(description=output))
        except Exception as e:
            print("caught exception fetching channel last message:", e)
            await send_message(channel, embed_description=output, colour=Colour.blue())
    else:
        await send_message(channel, embed_description=output, colour=Colour.blue())
