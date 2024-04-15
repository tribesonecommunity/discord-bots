# Misc helper functions
import asyncio
import itertools
import logging
import math
import os
import statistics
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional

import discord
import imgkit
import sqlalchemy.orm.session
from discord import (
    Colour,
    DMChannel,
    Embed,
    GroupChannel,
    Guild,
    Interaction,
    Message,
    PartialMessage,
    TextChannel,
    VoiceChannel,
    VoiceState
)
from discord.ext import commands
from discord.ext.commands.context import Context
from discord.member import Member
from PIL import Image
from selenium import webdriver
from selenium.webdriver.firefox.options import Options as FirefoxOptions
from sqlalchemy import func
from sqlalchemy.orm.session import Session as SQLAlchemySession
from trueskill import Rating, global_env, rate

import discord_bots.config as config
from discord_bots.bot import bot
from discord_bots.models import (
    Category,
    FinishedGame,
    FinishedGamePlayer,
    InProgressGame,
    InProgressGameChannel,
    InProgressGamePlayer,
    Map,
    MapVote,
    Player,
    PlayerCategoryTrueskill,
    Queue,
    Rotation,
    RotationMap,
    Session,
    SkipMapVote,
)

_log = logging.getLogger(__name__)


MU_LOWER_UNICODE = "\u03BC"
SIGMA_LOWER_UNICODE = "\u03C3"
DELTA_UPPER_UNICODE = "\u03B4"


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
                f"(C) {player.name}" if i == 0 else player.name
                for i, player in enumerate(players)
            ]
        )
    )
    return f"{team_name} ({round(100 * win_probability, 1)}%): {player_names}\n"


def pretty_format_team_no_format(
    team_name: str, win_probability: float, players: list[Player]
) -> str:
    player_names = ", ".join(
        sorted(
            [
                f"(C) {player.name}" if i == 0 else player.name
                for i, player in enumerate(players)
            ]
        )
    )
    return f"{team_name} ({round(100 * win_probability, 1)}%): {player_names}\n"


def short_uuid(uuid: str) -> str:
    return uuid.split("-")[0]


async def upload_stats_screenshot_selenium(ctx: Context, cleanup=True):
    # Assume the most recently modified HTML file is the correct stat sheet
    if not config.STATS_DIR:
        return

    html_files = list(
        filter(lambda x: x.endswith(".html"), os.listdir(config.STATS_DIR))
    )
    html_files.sort(
        key=lambda x: os.path.getmtime(os.path.join(config.STATS_DIR, x)), reverse=True
    )

    opts = FirefoxOptions()
    opts.add_argument("--headless")
    driver = webdriver.Firefox(options=opts)
    if len(html_files) == 0:
        return

    driver.get("file://" + os.path.join(config.STATS_DIR, html_files[0]))
    image_path = os.path.join(config.STATS_DIR, html_files[0] + ".png")
    driver.save_screenshot(image_path)
    image = Image.open(image_path)
    # TODO: Un-hardcode these
    cropped = image.crop((0, 0, 750, 650))
    cropped.save(image_path)

    await ctx.message.channel.send(file=discord.File(image_path))

    # Clean up everything
    if cleanup:
        for file_ in os.listdir(config.STATS_DIR):
            if file_.endswith(".png") or file_.endswith(".html"):
                os.remove(os.path.join(config.STATS_DIR, file_))


async def create_in_progress_game_embed(
    session: sqlalchemy.orm.Session,
    game: InProgressGame,
    guild: discord.Guild,
) -> Embed:
    queue: Queue | None = session.query(Queue).filter(Queue.id == game.queue_id).first()
    embed: discord.Embed
    if queue:
        embed = Embed(
            title=f"⏳In Progress Game '{queue.name}' ({short_uuid(game.id)})",
            color=discord.Color.blue(),
        )
    else:
        embed = Embed(
            title=f"⏳In Progress Game ({short_uuid(game.id)})",
            color=discord.Color.blue(),
        )

    aware_db_datetime: datetime = game.created_at.replace(
        tzinfo=timezone.utc
    )  # timezones aren't stored in the DB, so add it ourselves
    timestamp = discord.utils.format_dt(aware_db_datetime, style="R")
    result: list[str] | None = (
        session.query(Player.name)
        .join(InProgressGamePlayer)
        .filter(
            InProgressGamePlayer.in_progress_game_id == game.id,
            InProgressGamePlayer.team == 0,
        )
        .all()
    )
    team0_player_names = [name[0] for name in result if name] if result else []
    result: list[str] | None = (
        session.query(Player.name)
        .join(InProgressGamePlayer)
        .filter(
            InProgressGamePlayer.in_progress_game_id == game.id,
            InProgressGamePlayer.team == 1,
        )
        .all()
    )
    team1_player_names: list[str] = (
        [name[0] for name in result if name] if result else []
    )
    if config.SHOW_CAPTAINS:
        if team0_player_names:
            team0_player_names[0] = "(C) " + team0_player_names[0]
        if team1_player_names:
            team1_player_names[0] = "(C) " + team1_player_names[0]
    # sort the names alphabetically and caselessly to make them easier to read
    team0_player_names.sort(key=str.casefold)
    team1_player_names.sort(key=str.casefold)
    newline = "\n"
    embed.add_field(
        name=f"⬅️ {game.team0_name} ({round(100 * game.win_probability)}%)",
        value=(
            f"\n>>> {newline.join(team0_player_names)}"
            if team0_player_names
            else "> \n** **"
        ),
        inline=True,
    )
    embed.add_field(
        name=f"➡️ {game.team1_name} ({round(100 * (1 - game.win_probability))}%)",
        value=(
            f"\n>>> {newline.join(team1_player_names)}"
            if team1_player_names
            else "> \n** **"
        ),
        inline=True,
    )
    embed.add_field(name="", value="", inline=False)  # newline
    embed.add_field(
        name="🗺️ Map", value=f"{game.map_full_name} ({game.map_short_name})", inline=True
    )
    embed.add_field(name="⏱️ Started", value=f"{timestamp}", inline=True)
    if config.SHOW_TRUESKILL:
        embed.add_field(
            name=f"📊 Average {MU_LOWER_UNICODE}",
            value=round(game.average_trueskill, 2),
            inline=True,
        )
    # TODO: make the commands configurable/toggleable
    embed.add_field(
        name="🔧 Commands",
        value="\n".join(["`/finishgame`", "`/setgamecode`"]),
        inline=True,
    )
    if game.channel_id:
        embed.add_field(name="📺 Channel", value=f"<#{game.channel_id}>", inline=True)
    if game.code:
        embed.add_field(name="🔢 Game Code", value=f"`{game.code}`", inline=True)
    embed_fields_len = len(embed.fields) - 3  # subtract team0 and team1 fields
    if embed_fields_len >= 5 and embed_fields_len % 3 == 2:
        # embeds are allowed 3 "columns" per "row"
        # to line everything up nicely when there's >= 5 fields and only one "column" slot left, we add a blank
        embed.add_field(name="", value="", inline=True)
    return embed


def create_finished_game_embed(
    session: sqlalchemy.orm.Session,
    finished_game_id: str,
    guild_id: int,
    name_tuple: Optional[tuple[str, str]] = None,  # (user_name, display_name)
) -> Embed:
    # assumes that the FinishedGamePlayers have already been comitted
    finished_game = (
        session.query(FinishedGame).filter(FinishedGame.id == finished_game_id).first()
    )
    if not finished_game:
        _log.error(
            f"[create_finished_game_embed] Could not find finished_game with id={finished_game_id}"
        )
        return discord.Embed(
            description=f"Oops! Could not find the Finished Game...️☹️",
            color=discord.Color.red(),
        )
    guild: discord.Guild | None = bot.get_guild(guild_id)
    if not guild:
        _log.error(
            f"[create_finished_game_embed] Could not find guild with id={guild_id}"
        )
        return discord.Embed(
            description=f"Oops! Could not find the Finished Game...️☹️",
            color=discord.Color.red(),
        )
    embed = Embed(
        title=f"✅ Game '{finished_game.queue_name}' ({short_uuid(finished_game.game_id)}) Results",
        color=Colour.green(),
    )
    if name_tuple is not None:
        user_name, display_name = name_tuple[0], name_tuple[1]
        embed.set_footer(text=f"Finished by {display_name} ({user_name})")
    result = (
        session.query(FinishedGamePlayer.player_name)
        .filter(
            FinishedGamePlayer.finished_game_id == finished_game.id,
            FinishedGamePlayer.team == 0,
        )
        .all()
    )
    team0_player_names: list[str] = [p.player_name for p in result] if result else []
    result = (
        session.query(FinishedGamePlayer.player_name)
        .filter(
            FinishedGamePlayer.finished_game_id == finished_game.id,
            FinishedGamePlayer.team == 1,
        )
        .all()
    )
    team1_player_names: list[str] = [p.player_name for p in result] if result else []
    # sort the names alphabetically and caselessly to make them easier to read
    team0_player_names.sort(key=str.casefold)
    team1_player_names.sort(key=str.casefold)
    if finished_game.winning_team == 0:
        be_str = f"🥇 {finished_game.team0_name}"
        ds_str = f"🥈 {finished_game.team1_name}"
    elif finished_game.winning_team == 1:
        be_str = f"🥈 {finished_game.team0_name}"
        ds_str = f"🥇 {finished_game.team1_name}"
    else:
        be_str = f"🥈 {finished_game.team0_name}"
        ds_str = f"🥈 {finished_game.team1_name}"
    newline = "\n"
    embed.add_field(
        name=f"{be_str} ({round(100 * finished_game.win_probability)}%)",
        value=(
            ""
            if not team0_player_names
            else f"\n>>> {newline.join(team0_player_names)}"
        ),
        inline=True,
    )
    embed.add_field(
        name=f"{ds_str} ({round(100*(1 - finished_game.win_probability))}%)",
        value=(
            ""
            if not team1_player_names
            else f"\n>>> {newline.join(team1_player_names)}"
        ),
        inline=True,
    )
    embed.add_field(name="", value="", inline=False)  # newline
    embed.add_field(
        name="🗺️️ Map",
        value=f"{finished_game.map_full_name} ({finished_game.map_short_name})",
        inline=True,
    )
    duration: timedelta = finished_game.finished_at.replace(
        tzinfo=timezone.utc
    ) - finished_game.started_at.replace(tzinfo=timezone.utc)
    embed.add_field(
        name="⏱️ Duration",
        value=f"{duration.seconds // 60} minutes",
        inline=True,
    )
    if config.SHOW_TRUESKILL:
        embed.add_field(
            name=f"📊 Average {MU_LOWER_UNICODE}",
            value=round(finished_game.average_trueskill, 2),
            inline=True,
        )
    return embed


def create_cancelled_game_embed(
    session: sqlalchemy.orm.Session,
    in_progress_game: InProgressGame,
    user_name: Optional[str] = None,
) -> Embed:
    # TODO: merge with create_finished_game_embed
    queue: Queue | None = (
        session.query(Queue).filter(Queue.id == in_progress_game.queue_id).first()
    )
    queue_name = f"'{queue.name}'" if queue is not None else ""
    embed = Embed(
        title=f"❌ Game {queue_name} ({short_uuid(in_progress_game.id)}) Cancelled",
        color=Colour.red(),
    )
    if user_name is not None:
        embed.set_footer(text=f"Cancelled by {user_name}")
    else:
        embed.set_footer(text="Cancelled")
    team0_fg_players: list[InProgressGamePlayer] = (
        session.query(InProgressGamePlayer)
        .filter(
            InProgressGamePlayer.in_progress_game_id == in_progress_game.id,
            InProgressGamePlayer.team == 0,
        )
        .all()
    )
    team1_fg_players: list[InProgressGamePlayer] = (
        session.query(InProgressGamePlayer)
        .filter(
            InProgressGamePlayer.in_progress_game_id == in_progress_game.id,
            InProgressGamePlayer.team == 1,
        )
        .all()
    )
    embed.add_field(
        name="📍 Map",
        value=f"{in_progress_game.map_full_name} ({in_progress_game.map_short_name})",
        inline=False,
    )
    # probably don't need duration for cancelled games, but might as well add it
    duration: timedelta = discord.utils.utcnow() - in_progress_game.created_at.replace(
        tzinfo=timezone.utc
    )
    embed.add_field(
        name="⏱️ Duration",
        value=f"{duration.seconds // 60} minutes",
        inline=False,
    )
    if config.SHOW_TRUESKILL:
        embed.add_field(
            name=f"📊 Average {MU_LOWER_UNICODE}",
            value=round(in_progress_game.average_trueskill, 2),
            inline=False,
        )
    embed.add_field(name="", value="", inline=False)  # newline
    embed.add_field(
        name=f"{in_progress_game.team0_name} ({round(100 * in_progress_game.win_probability)}%)",
        value="\n".join([f"> <@{player.player_id}>" for player in team0_fg_players]),
        inline=True,
    )
    embed.add_field(
        name=f"{in_progress_game.team1_name} ({round(100*(1 - in_progress_game.win_probability))}%)",
        value="\n".join([f"> <@{player.player_id}>" for player in team1_fg_players]),
        inline=True,
    )
    return embed


"""
This version uploads it to an interaction. When we have a shared match history
channel this will be wanted behavior, but until then players prefer if its
uploaded to a centralized channel
"""


async def upload_stats_screenshot_imgkit_interaction(
    interaction: discord.Interaction, cleanup=True
):
    # Assume the most recently modified HTML file is the correct stat sheet
    if not config.STATS_DIR:
        return

    html_files = list(
        filter(lambda x: x.endswith(".html"), os.listdir(config.STATS_DIR))
    )
    html_files.sort(
        key=lambda x: os.path.getmtime(os.path.join(config.STATS_DIR, x)), reverse=True
    )

    if len(html_files) == 0:
        return

    image_path = os.path.join(config.STATS_DIR, html_files[0] + ".png")
    imgkit.from_file(
        os.path.join(config.STATS_DIR, html_files[0]),
        image_path,
        options={"enable-local-file-access": None},
    )
    if config.STATS_WIDTH and config.STATS_HEIGHT:
        image = Image.open(image_path)
        cropped = image.crop((0, 0, config.STATS_WIDTH, config.STATS_HEIGHT))
        cropped.save(image_path)

    await interaction.channel.send(
        file=discord.File(image_path)
    )  # ideally edit the original resonse, but sending to the channel is fine

    # Clean up everything
    if cleanup:
        for file_ in os.listdir(config.STATS_DIR):
            if file_.endswith(".png") or file_.endswith(".html"):
                os.remove(os.path.join(config.STATS_DIR, file_))


"""
Temporary function until we have a shared match history channel
"""


async def upload_stats_screenshot_imgkit_channel(
    channel: TextChannel | DMChannel | GroupChannel, cleanup=True
):
    # Assume the most recently modified HTML file is the correct stat sheet
    if not config.STATS_DIR:
        return

    html_files = list(
        filter(lambda x: x.endswith(".html"), os.listdir(config.STATS_DIR))
    )
    html_files.sort(
        key=lambda x: os.path.getmtime(os.path.join(config.STATS_DIR, x)), reverse=True
    )

    if len(html_files) == 0:
        return

    image_path = os.path.join(config.STATS_DIR, html_files[0] + ".png")
    imgkit.from_file(
        os.path.join(config.STATS_DIR, html_files[0]),
        image_path,
        options={"enable-local-file-access": None},
    )
    if config.STATS_WIDTH and config.STATS_HEIGHT:
        image = Image.open(image_path)
        cropped = image.crop((0, 0, config.STATS_WIDTH, config.STATS_HEIGHT))
        cropped.save(image_path)

    await channel.send(file=discord.File(image_path))

    # Clean up everything
    if cleanup:
        for file_ in os.listdir(config.STATS_DIR):
            if file_.endswith(".png") or file_.endswith(".html"):
                os.remove(os.path.join(config.STATS_DIR, file_))


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


async def update_next_map_to_map_after_next(rotation_id: str, is_verbose: bool):
    """
    :is_verbose: specifies if we want to see queues affected in the bot response.
                 currently passing in False for when game pops, True for everything else.
    """
    session: sqlalchemy.orm.Session
    with Session() as session:
        rotation: Rotation | None = (
            session.query(Rotation).filter(Rotation.id == rotation_id).first()
        )

        next_rotation_map: RotationMap | None = (
            session.query(RotationMap)
            .filter(RotationMap.rotation_id == rotation_id)
            .filter(RotationMap.is_next == True)
            .first()
        )

        if rotation.is_random:
            rotation_map_after_next: RotationMap | None = (
                session.query(RotationMap)
                .filter(RotationMap.rotation_id == rotation_id)
                .filter(RotationMap.is_next == False)
                .order_by(func.random())
                .first()
            )
        else:
            rotation_map_length = (
                session.query(RotationMap)
                .filter(RotationMap.rotation_id == rotation_id)
                .count()
            )
            rotation_map_after_next_ordinal = next_rotation_map.ordinal + 1
            if rotation_map_after_next_ordinal > rotation_map_length:
                rotation_map_after_next_ordinal = 1

            rotation_map_after_next: RotationMap | None = (
                session.query(RotationMap)
                .filter(RotationMap.rotation_id == rotation_id)
                .filter(RotationMap.ordinal == rotation_map_after_next_ordinal)
                .first()
            )

        next_rotation_map.is_next = False
        rotation_map_after_next.is_next = True

        map_after_next_name: str | None = (
            session.query(Map.full_name)
            .join(RotationMap, RotationMap.map_id == Map.id)
            .filter(RotationMap.id == rotation_map_after_next.id)
            .scalar()
        )

        channel = bot.get_channel(config.CHANNEL_ID)
        if isinstance(channel, discord.TextChannel):
            if is_verbose:
                rotation_queues = (
                    session.query(Queue.name)
                    .filter(Queue.rotation_id == rotation_id)
                    .all()
                )
                rotation_queue_names = ""
                for name in rotation_queues:
                    rotation_queue_names += f"\n- {name[0]}"

                await send_message(
                    channel,
                    embed_description=f"Map rotated to **{map_after_next_name}**, all votes removed\n\nQueues affected:{rotation_queue_names}",
                    colour=Colour.blue(),
                )
            else:
                await send_message(
                    channel,
                    embed_description=f"Map rotated to **{map_after_next_name}**, all votes removed",
                    colour=Colour.blue(),
                )

        map_votes = (
            session.query(MapVote)
            .join(RotationMap, RotationMap.id == MapVote.rotation_map_id)
            .filter(RotationMap.rotation_id == rotation_id)
            .all()
        )
        for map_vote in map_votes:
            session.delete(map_vote)
        session.query(SkipMapVote).filter(
            SkipMapVote.rotation_id == rotation_id
        ).delete()
        session.commit()


async def send_in_guild_message(
    guild: Guild,
    user_id: int,
    message_content: Optional[str] = None,
    embed: Optional[Embed] = None,
):
    # use asyncio.gather to run this coroutine in parallel, else each send has to await for the previous one to finish
    if not config.DISABLE_PRIVATE_MESSAGES:
        member: Member | None = guild.get_member(user_id)
        if member:
            try:
                await member.send(content=message_content, embed=embed)
            except Exception:
                _log.exception("[send_in_guild_message] exception:")


def get_guild_partial_message(
    guild: Guild,
    channel_id: int,
    message_id: int,
) -> PartialMessage | None:
    """
    Helper funcction to get a PartialMessage from a given Guild and channel_id.
    Using discord.Guild.get_channel and discord.Channel.get_partial_message avoids emitting any API calls to discord
    Note: discord.Guild.get_channel may emit an API call if the channel is not in the bot's cache
    Partial Messages have less functionality than Messages:
    https://discordpy.readthedocs.io/en/stable/api.html?highlight=get%20message#discord.PartialMessage
    """
    channel: discord.abc.GuildChannel | None = guild.get_channel(channel_id)
    if isinstance(channel, discord.TextChannel):
        message: discord.PartialMessage = channel.get_partial_message(message_id)
        return message
    return None


async def send_message(
    channel: DMChannel | GroupChannel | TextChannel,
    content: str | None = None,
    embed_description: str | None = None,
    colour: Colour | None = None,
    embed_content: bool = True,
    embed_title: str | None = None,
    embed_thumbnail: str | None = None,
    delete_after: float | None = None,
) -> Message | None:
    """
    :colour: red = fail, green = success, blue = informational
    """
    message: Message | None = None
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
        message = await channel.send(
            content=content, embed=embed, delete_after=delete_after
        )
    except Exception:
        _log.exception("[send_message] Ignoring exception:")
    return message


async def print_leaderboard():
    output = "**Leaderboard**"
    session: SQLAlchemySession
    with Session() as session:
        categories: list[Category] = (
            session.query(Category).filter(Category.is_rated == True).all()
        )
        if len(categories) > 0:
            for category in categories:
                output += f"\n_{category.name}_"
                top_10_pcts: list[PlayerCategoryTrueskill] = (
                    session.query(PlayerCategoryTrueskill)
                    .filter(PlayerCategoryTrueskill.category_id == category.id)
                    .order_by(PlayerCategoryTrueskill.rank.desc())
                    .limit(10)
                )
                for i, pct in enumerate(top_10_pcts, 1):
                    player: Player = (
                        session.query(Player).filter(Player.id == pct.player_id).first()
                    )
                    output += f"\n{i}. {round(pct.rank, 1)} - <@{player.id}> _(mu: {round(pct.mu, 1)}, sigma: {round(pct.sigma, 1)})_"
            pass

        if config.ECONOMY_ENABLED:
            output += f"\n\n**{config.CURRENCY_NAME}**"
            top_10_player_currency: list[Player] = (
                session.query(Player)
                .order_by(Player.currency.desc())
                .limit(10)
            )
            for i, player_currency in enumerate(top_10_player_currency, 1):
                output += f"\n{i}. {player_currency.currency} - <@{player_currency.id}>"

    output += "\n"
    output += "\n(Ranks calculated using the formula: _mu - 3*sigma_)"
    output += "\n(Leaderboard updates periodically)"
    output += "\n(!disableleaderboard to hide yourself from the leaderboard)"

    if config.LEADERBOARD_CHANNEL:
        leaderboard_channel = bot.get_channel(config.LEADERBOARD_CHANNEL)
        if leaderboard_channel and isinstance(leaderboard_channel, TextChannel):
            try:
                if leaderboard_channel.last_message_id:
                    last_message: Message = await leaderboard_channel.fetch_message(
                        leaderboard_channel.last_message_id
                    )
                if last_message:
                    embed = Embed(
                        description=output,
                        colour=Colour.blue(),
                        timestamp=discord.utils.utcnow(),
                    )
                    embed.set_footer(text="Last updated")
                    await last_message.edit(embed=embed)
                    return
            except Exception as e:
                _log.exception("[print_leaderboard] exception")
            await send_message(
                leaderboard_channel, embed_description=output, colour=Colour.blue()
            )
            return


def code_block(content: str, language: str = "autohotkey") -> str:
    return "\n".join(["```" + language, content, "```"])


def get_team_name_diff(
    team0_player_names_before: list[str] | None,
    team0_player_names_after: list[str] | None,
    team1_player_names_before: list[str] | None,
    team1_player_names_after: list[str] | None,
) -> tuple[str, str]:
    """
    Given lists of player names for team0 and team1 (before and after), returns a tuple[str, str] that represents the diff in discord markdown format
    Useful for displaying after a rebalance, typically after a sub or autosub.
    Note: the returned string is a vertical list
    """
    if (
        not team0_player_names_before
        or not team0_player_names_after
        or not team1_player_names_before
        or not team1_player_names_after
    ):
        return "", ""
    players_added_to_team0: list[str] = list(
        set(team0_player_names_after) - set(team0_player_names_before)
    )
    players_added_to_team1: list[str] = list(
        set(team1_player_names_after) - set(team1_player_names_before)
    )
    team0_diff_vaules: list[str] = []
    team1_diff_vaules: list[str] = []
    # sort the names alphabetically and caselessly to make them easier to read
    team0_player_names_after.sort(key=str.casefold)
    team1_player_names_after.sort(key=str.casefold)
    for name in team0_player_names_after:
        if name in players_added_to_team0:
            team0_diff_vaules.append(f"+ {name}")
        else:
            team0_diff_vaules.append(f" {name}")
    for name in team1_player_names_after:
        if name in players_added_to_team1:
            team1_diff_vaules.append(f"+ {name}")
        else:
            team1_diff_vaules.append(f"  {name}")
    newline = "\n"
    team0_diff_str: str = (
        f">>> ```diff\n{newline.join(team0_diff_vaules)}```"
        if team0_diff_vaules
        else "> \n** **"  # creates an empty quote
    )
    team1_diff_str: str = (
        f">>> ```diff\n{newline.join(team1_diff_vaules)}```"
        if team1_diff_vaules
        else "> \n** **"  # creates an empty quote
    )
    return team0_diff_str, team1_diff_str

async def move_game_players(
    game_id: str, interaction: Interaction | None = None, guild: Guild | None = None
):
    session: sqlalchemy.orm.Session
    with Session() as session:
        message: Message | None = None
        if interaction:
            message = interaction.message
            guild = interaction.guild
        elif guild:
            message = None
        else:
            raise Exception("No Interaction or Guild on _movegameplayers")

        in_progress_game = (
            session.query(InProgressGame)
            .filter(InProgressGame.id.startswith(game_id))
            .first()
        )
        if not in_progress_game:
            if message:
                await send_message(
                    message.channel,
                    embed_description=f"Could not find game: {game_id}",
                    colour=Colour.red(),
                )
                return
            return

        team0_ipg_players: list[InProgressGamePlayer] = session.query(
            InProgressGamePlayer
        ).filter(
            InProgressGamePlayer.in_progress_game_id == in_progress_game.id,
            InProgressGamePlayer.team == 0,
        )
        team1_ipg_players: list[InProgressGamePlayer] = session.query(
            InProgressGamePlayer
        ).filter(
            InProgressGamePlayer.in_progress_game_id == in_progress_game.id,
            InProgressGamePlayer.team == 1,
        )
        team0_player_ids = set(map(lambda x: x.player_id, team0_ipg_players))
        team1_player_ids = set(map(lambda x: x.player_id, team1_ipg_players))
        team0_players: list[Player] = session.query(Player).filter(Player.id.in_(team0_player_ids))  # type: ignore
        team1_players: list[Player] = session.query(Player).filter(Player.id.in_(team1_player_ids))  # type: ignore

        be_voice_channel: VoiceChannel | None = None
        ds_voice_channel: VoiceChannel | None = None
        ipg_channels: list[InProgressGameChannel] | None = (
            session.query(InProgressGameChannel)
            .filter(InProgressGameChannel.in_progress_game_id == in_progress_game.id)
            .all()
        )
        for ipg_channel in ipg_channels or []:
            discord_channel: discord.abc.GuildChannel | None = guild.get_channel(
                ipg_channel.channel_id
            )
            if isinstance(discord_channel, VoiceChannel):
                # This is suboptimal solution but it's good enough for now. We should keep track of each team's VC in the database
                if discord_channel.name == in_progress_game.team0_name:
                    be_voice_channel = discord_channel
                elif discord_channel.name == in_progress_game.team1_name:
                    ds_voice_channel = discord_channel

        coroutines = []
        for player in team0_players:
            if player.move_enabled and be_voice_channel:
                member: Member | None = guild.get_member(player.id)
                if member:
                    member_voice: VoiceState | None = member.voice
                    if member_voice:
                        if member_voice.channel:
                            try:
                                coroutines.append(
                                    member.move_to(
                                        be_voice_channel,
                                        reason=f"Game {game_id} started",
                                    )
                                )
                            except Exception:
                                _log.exception(
                                    f"Caught exception moving player to voice channel"
                                )

        for player in team1_players:
            if player.move_enabled and ds_voice_channel:
                member: Member | None = guild.get_member(player.id)
                if member:
                    member_voice: VoiceState | None = member.voice
                    if member_voice:
                        if member_voice.channel:
                            try:
                                coroutines.append(
                                    member.move_to(
                                        ds_voice_channel,
                                        reason=f"Game {game_id} started",
                                    )
                                )
                            except Exception:
                                _log.exception(
                                    f"Caught exception moving player to voice channel"
                                )
    # use gather to run the moves concurrently in the event loop
    # note: a member has to be in a voice channel already for them to be moved, else it throws an exception
    results = await asyncio.gather(*coroutines, return_exceptions=True)
    for result in results:
        # results should be empty unless an exception occured when moving a player
        if isinstance(result, BaseException):
            _log.exception("Ignored exception when moving a gameplayer:")

async def move_game_players_lobby(
    game_id: str, guild: Guild
):
    session: sqlalchemy.orm.Session
    with Session() as session:
        in_progress_game = (
            session.query(InProgressGame)
            .filter(InProgressGame.id == game_id)
            .first()
        )
        if not in_progress_game:
            return
        
        voice_lobby: discord.abc.GuildChannel | None = guild.get_channel(
                config.VOICE_MOVE_LOBBY
        )
        if not isinstance(voice_lobby, VoiceChannel) or not voice_lobby:
            _log.exception("VOICE_MOVE_LOBBY not found")
            return

        ipg_channels: list[InProgressGameChannel] | None = (
                session.query(InProgressGameChannel)
                .filter(InProgressGameChannel.in_progress_game_id == in_progress_game.id)
                .all()
            )
        
        coroutines = []
        for ipg_channel in ipg_channels or []:
            discord_channel: discord.abc.GuildChannel | None = guild.get_channel(
                ipg_channel.channel_id
            )
            if isinstance(discord_channel, VoiceChannel):
                members: list[Member] = discord_channel.members
                if len(members) > 0:
                    for member in members:
                        try:
                            coroutines.append(
                                member.move_to(
                                    voice_lobby,
                                    reason=f"Game {short_uuid(game_id)} finished",
                                )
                            )
                        except Exception:
                            _log.exception(
                                    f"Caught exception moving player to voice lobby"
                                )
    
    results = await asyncio.gather(*coroutines, return_exceptions=True)
    for result in results:
        # results should be empty unless an exception occured when moving a player
        if isinstance(result, BaseException):
            _log.exception("Ignored exception when moving a gameplayer to lobby:")