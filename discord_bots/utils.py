# Misc helper functions
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
    TextChannel,
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
    team0_players: list[Player] = (
        session.query(Player)
        .join(InProgressGamePlayer)
        .filter(
            InProgressGamePlayer.in_progress_game_id == game.id,
            InProgressGamePlayer.team == 0,
        )
        .all()
    )
    team1_players: list[Player] = (
        session.query(Player)
        .join(InProgressGamePlayer)
        .filter(
            InProgressGamePlayer.in_progress_game_id == game.id,
            InProgressGamePlayer.team == 1,
        )
        .all()
    )
    team0_names: list[str] = []
    for player in team0_players:
        member: discord.Member | None = guild.get_member(player.id)
        if member:
            team0_names.append(member.name)
        else:
            team0_names.append(player.name)
    team1_names: list[str] = []
    for player in team1_players:
        member: discord.Member | None = guild.get_member(player.id)
        if member:
            team1_names.append(member.display_name)
        else:
            team1_names.append(player.name)

    embed.add_field(
        name="🗺️ Map", value=f"{game.map_full_name} ({game.map_short_name})", inline=True
    )
    embed.add_field(name="⏱️Started", value=f"{timestamp}", inline=True)
    if config.SHOW_TRUESKILL:
        embed.add_field(
            name=f"📊 Average {MU_LOWER_UNICODE}",
            value=round(game.average_trueskill, 2),
            inline=True,
        )
    if game.message_id and game.channel_id:
        game_channel = bot.get_channel(game.channel_id)
        try:
            if isinstance(game_channel, TextChannel):
                game_message = await game_channel.fetch_message(game.message_id)
                if game_message:
                    embed.add_field(
                        name="📺 Match Channel", value=f"{game_message.jump_url}"
                    )
        except Exception as e:
            _log.warning(f"Could not find game message {game.message_id} due to: {e}")
    embed.add_field(name="", value="", inline=False)  # newline
    newline = "\n"
    embed.add_field(
        name=f"⬅️ {game.team0_name} ({round(100 * game.win_probability)}%)",
        value="" if not team0_players else f"\n>>> {newline.join(team0_names)}",
        inline=True,
    )
    embed.add_field(
        name=f"➡️ {game.team1_name} ({round(100 * (1 - game.win_probability))}%)",
        value="" if not team1_players else f"\n>>> {newline.join(team1_names)}",
        inline=True,
    )
    return embed


def create_finished_game_embed(
    session: sqlalchemy.orm.Session,
    finished_game: FinishedGame,
    user_name: Optional[str] = None,
) -> Embed:
    # assumes that the FinishedGamePlayers have already been comitted
    embed = Embed(
        title=f"✅ Game '{finished_game.queue_name}' ({short_uuid(finished_game.game_id)}) Results",
        color=Colour.green(),
    )
    if user_name is not None:
        embed.set_footer(text=f"Finished by {user_name}")
    else:
        embed.set_footer(text="Finished")
    team0_fg_players: list[FinishedGamePlayer] = (
        session.query(FinishedGamePlayer)
        .filter(
            FinishedGamePlayer.finished_game_id == finished_game.id,
            FinishedGamePlayer.team == 0,
        )
        .all()
    )
    team1_fg_players: list[FinishedGamePlayer] = (
        session.query(FinishedGamePlayer)
        .filter(
            FinishedGamePlayer.finished_game_id == finished_game.id,
            FinishedGamePlayer.team == 1,
        )
        .all()
    )
    if finished_game.winning_team == 0:
        be_str = f"🥇 {finished_game.team0_name}"
        ds_str = f"🥈 {finished_game.team1_name}"
    elif finished_game.winning_team == 1:
        be_str = f"🥈 {finished_game.team0_name}"
        ds_str = f"🥇 {finished_game.team1_name}"
    else:
        be_str = f"🥈 {finished_game.team0_name}"
        ds_str = f"🥈 {finished_game.team1_name}"
    embed.add_field(
        name="📍 Map",
        value=f"{finished_game.map_full_name} ({finished_game.map_short_name})",
        inline=False,
    )
    duration: timedelta = finished_game.finished_at.replace(
        tzinfo=timezone.utc
    ) - finished_game.started_at.replace(tzinfo=timezone.utc)
    embed.add_field(
        name="⏱️ Duration",
        value=f"{duration.seconds // 60} minutes",
        inline=False,
    )
    if config.SHOW_TRUESKILL:
        embed.add_field(
            name=f"📊 Average {MU_LOWER_UNICODE}",
            value=round(finished_game.average_trueskill, 2),
            inline=False,
        )
    embed.add_field(name="", value="", inline=False)  # newline
    embed.add_field(
        name=f"{be_str} ({round(100 * finished_game.win_probability)}%)",
        value="\n".join([f"> <@{player.player_id}>" for player in team0_fg_players]),
        inline=True,
    )
    embed.add_field(
        name=f"{ds_str} ({round(100*(1 - finished_game.win_probability))}%)",
        value="\n".join([f"> <@{player.player_id}>" for player in team1_fg_players]),
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
    # TODO: implement mechanism to avoid being rate limited or some way to send DMs in bulk
    if not config.DISABLE_PRIVATE_MESSAGES:
        member: Member | None = guild.get_member(user_id)
        if member:
            try:
                await member.send(content=message_content, embed=embed)
            except Exception:
                _log.exception("[send_in_guild_message] exception:")


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
                    await last_message.edit(embed=Embed(description=output, colour=Colour.blue()))
                    return
            except Exception as e:
                _log.exception("[print_leaderboard] exception")
            await send_message(
                leaderboard_channel, embed_description=output, colour=Colour.blue()
            )
            return


def code_block(content: str, language: str = "autohotkey") -> str:
    return "\n".join(["```" + language, content, "```"])


@bot.command()
@commands.guild_only()
@commands.is_owner()
async def sync(
    ctx: Context,
    guilds: commands.Greedy[discord.Object],
    spec: Optional[Literal["~", "*", "^"]] = None,
) -> None:
    """
    https://about.abstractumbra.dev/discord.py/2023/01/29/sync-command-example.html
    """
    if not guilds:
        if spec == "~":
            synced = await ctx.bot.tree.sync(guild=ctx.guild)
        elif spec == "*":
            ctx.bot.tree.copy_global_to(guild=ctx.guild)
            synced = await ctx.bot.tree.sync(guild=ctx.guild)
        elif spec == "^":
            ctx.bot.tree.clear_commands(guild=ctx.guild)
            await ctx.bot.tree.sync(guild=ctx.guild)
            synced = []
        else:
            synced = await ctx.bot.tree.sync()

        await ctx.send(
            f"Synced {len(synced)} commands {'globally' if spec is None else 'to the current guild.'}"
        )
        return

    ret = 0
    for guild in guilds:
        try:
            await ctx.bot.tree.sync(guild=guild)
        except discord.HTTPException as e:
            _log.warn(f"Caught exception trying to sync for guild: ${guild}, e: {e}")
        else:
            ret += 1

    await ctx.send(f"Synced the tree to {ret}/{len(guilds)}.")
