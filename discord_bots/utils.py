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
from PIL import Image
from matplotlib import image
from selenium import webdriver
from selenium.webdriver.firefox.options import Options as FirefoxOptions
from trueskill import Rating, global_env

from discord_bots.bot import bot
from discord_bots.models import (
    CurrentMap,
    MapVote,
    Player,
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


RANDOM_MAP_ROTATION = bool(os.getenv("RANDOM_MAP_ROTATION"))


async def update_current_map_to_next_map_in_rotation():
    session = Session()
    current_map: CurrentMap = session.query(CurrentMap).first()
    rotation_maps: list[RotationMap] = session.query(RotationMap).order_by(RotationMap.created_at.asc()).all()  # type: ignore
    if len(rotation_maps) > 0:
        if current_map:
            if RANDOM_MAP_ROTATION:
                next_map = choice(rotation_maps)
                while next_map.short_name == current_map.short_name:
                    next_map = choice(rotation_maps)
            else:
                next_rotation_map_index = (current_map.map_rotation_index + 1) % len(
                    rotation_maps
                )
                next_map = rotation_maps[next_rotation_map_index]
                current_map.map_rotation_index = next_rotation_map_index
            current_map.full_name = next_map.full_name
            current_map.short_name = next_map.short_name
            current_map.is_random = next_map.is_random
            current_map.random_probability = next_map.random_probability
            current_map.updated_at = datetime.now(timezone.utc)
            channel = bot.get_channel(CHANNEL_ID)
            if isinstance(channel, discord.TextChannel):
                await send_message(
                    channel,
                    embed_description=f"Map automatically rotated to **{next_map.full_name}**, all votes removed",
                    colour=discord.Colour.blue(),
                )
            session.query(MapVote).delete()
            session.query(SkipMapVote).delete()
        else:
            if RANDOM_MAP_ROTATION:
                next_map = choice(rotation_maps)
                while next_map.short_name == current_map.short_name:
                    next_map = choice(rotation_maps)
            else:
                next_map = rotation_maps[0]
            session.add(
                CurrentMap(
                    0,
                    next_map.full_name,
                    next_map.short_name,
                    is_random=next_map.is_random,
                    random_probability = next_map.random_probability
                )
            )
            channel = bot.get_channel(CHANNEL_ID)
            if isinstance(channel, discord.TextChannel):
                await send_message(
                    channel,
                    embed_description=f"Map rotated to {next_map.full_name}, all votes removed",
                    colour=discord.Colour.blue(),
                )
            session.query(MapVote).delete()
            session.query(SkipMapVote).delete()

        session.commit()


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
