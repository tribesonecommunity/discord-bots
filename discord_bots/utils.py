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
    InProgressGameChannel,
    InProgressGamePlayer,
    Map,
    MapVote,
    Player,
    PlayerCategoryTrueskill,
    Queue,
    QueueWaitlist,
    Rotation,
    RotationMap,
    Session,
    SkipMapVote,
)


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
    session = Session()

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
                session.query(Queue.name).filter(Queue.rotation_id == rotation_id).all()
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
    session.query(SkipMapVote).filter(SkipMapVote.rotation_id == rotation_id).delete()
    session.commit()
    session.close()


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
            except Exception as e:
                print(f"Caught exception sending message: {e}")


async def send_message(
    channel: DMChannel | GroupChannel | TextChannel,
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


async def print_leaderboard(channel=None):
    output = "**Leaderboard**"
    session = Session()
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
                output += f"\n{i}. {round(pct.rank, 1)} - {player.name} _(mu: {round(pct.mu, 1)}, sigma: {round(pct.sigma, 1)})_"
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

    if config.LEADERBOARD_CHANNEL:
        leaderboard_channel = bot.get_channel(config.LEADERBOARD_CHANNEL)
        if leaderboard_channel:
            try:
                last_message = await leaderboard_channel.fetch_message(
                    leaderboard_channel.last_message_id
                )
                if last_message:
                    await last_message.edit(embed=Embed(description=output))
                    return
            except Exception as e:
                print("caught exception fetching channel last message:", e)
            await send_message(
                leaderboard_channel, embed_description=output, colour=Colour.blue()
            )
            return
        else:
            if channel:
                await send_message(
                    channel, embed_description=output, colour=Colour.blue()
                )


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
            logging.warn(f"Caught exception trying to sync for guild: ${guild}, e: {e}")
        else:
            ret += 1

    await ctx.send(f"Synced the tree to {ret}/{len(guilds)}.")


async def cancel_in_progress_game(interaction: Interaction, game_id: str):
    session: SQLAlchemySession = Session()
    game = (
        session.query(InProgressGame)
        .filter(InProgressGame.id.startswith(game_id))
        .first()
    )
    if not game:
        await interaction.response.send_message(
            embed=Embed(
                description=f"Could not find game: {game_id}",
                colour=Colour.red(),
            )
        )
        return

    session.query(InProgressGamePlayer).filter(
        InProgressGamePlayer.in_progress_game_id == game.id
    ).delete()
    for channel in session.query(InProgressGameChannel).filter(
        InProgressGameChannel.in_progress_game_id == game.id
    ):
        if interaction.guild:
            guild_channel = interaction.guild.get_channel(channel.channel_id)
            if guild_channel:
                await guild_channel.delete()
        session.delete(channel)
    session.commit()

    session.query(InProgressGame).filter(InProgressGame.id == game.id).delete()
    session.commit()
    session.close()
    await interaction.response.send_message(
        embed=Embed(
            description=f"Game {game_id} cancelled",
            colour=Colour.blue(),
        )
    )
    return


async def finish_in_progress_game(
    interaction: Interaction,
    outcome: Literal["win", "loss", "tie"],
    game_id: Optional[str] = None,
) -> bool:
    session: sqlalchemy.orm.session.Session = Session()
    game_player: InProgressGamePlayer | None = (
        session.query(InProgressGamePlayer)
        .filter(InProgressGamePlayer.player_id == interaction.user.id)
        .first()
    )
    if not game_player:
        await interaction.followup.send(
            embed=discord.Embed(
                description="You are not in a game!",
                color=discord.Colour.red(),
            ),
            ephemeral=True,
        )
        session.close()
        return False

    in_progress_game: InProgressGame | None
    if game_id:
        in_progress_game = (
            session.query(InProgressGame)
            .filter(InProgressGame.id == game_player.in_progress_game_id)
            .filter(InProgressGame.id == game_id)
            .first()
        )
        if not in_progress_game:
            await interaction.followup.send(
                embed=discord.Embed(
                    description="You are not in this game!", color=discord.Colour.red()
                ),
                ephemeral=True,
            )
            session.close()
            return False
    else:
        in_progress_game = (
            session.query(InProgressGame)
            .filter(InProgressGame.id == game_player.in_progress_game_id)
            .first()
        )
        if not in_progress_game:
            logging.warn(
                f"No in_progress_game found with id={game_player.in_progress_game_id} for game_player with id={game_player.id}"
            )
            await interaction.followup.send(
                embed=discord.Embed(
                    description="You are not in a game!", color=discord.Colour.red()
                ),
                ephemeral=True,
            )
            session.close()
            return False

    queue: Queue | None = (
        session.query(Queue).filter(Queue.id == in_progress_game.queue_id).first()
    )
    if not queue:
        # should never happen
        logging.error(
            f"Could not find queue with id {in_progress_game.queue_id} for in_progress_game with id {in_progress_game.id}"
        )
        await interaction.followup.send(
            embed=discord.Embed(
                description="Something went wrong, please contact the server owner",
                color=discord.Colour.red(),
            ),
            ephemeral=True,
        )
        session.close()
        return False

    winning_team = -1
    if outcome == "win":
        winning_team = game_player.team
    elif outcome == "loss":
        winning_team = (game_player.team + 1) % 2
    else:
        # tie
        winning_team = -1

    players = (
        session.query(Player)
        .join(InProgressGamePlayer)
        .filter(
            InProgressGamePlayer.player_id == Player.id,
            InProgressGamePlayer.in_progress_game_id == in_progress_game.id,
        )
    ).all()
    player_ids: list[str] = [player.id for player in players]
    players_by_id: dict[int, Player] = {player.id: player for player in players}
    player_category_trueskills_by_id: dict[int, PlayerCategoryTrueskill] = {}
    if queue.category_id:
        player_category_trueskills: list[PlayerCategoryTrueskill] = (
            session.query(PlayerCategoryTrueskill)
            .filter(
                PlayerCategoryTrueskill.player_id.in_(player_ids),
                PlayerCategoryTrueskill.category_id == queue.category_id,
            )
            .all()
        )
        player_category_trueskills_by_id = {
            pct.player_id: pct for pct in player_category_trueskills
        }
    in_progress_game_players = (
        session.query(InProgressGamePlayer)
        .filter(InProgressGamePlayer.in_progress_game_id == in_progress_game.id)
        .all()
    )
    team0_rated_ratings_before = []
    team1_rated_ratings_before = []
    team0_players: list[InProgressGamePlayer] = []
    team1_players: list[InProgressGamePlayer] = []
    for in_progress_game_player in in_progress_game_players:
        player = players_by_id[in_progress_game_player.player_id]
        if in_progress_game_player.team == 0:
            team0_players.append(in_progress_game_player)
            if player.id in player_category_trueskills_by_id:
                pct = player_category_trueskills_by_id[player.id]
                team0_rated_ratings_before.append(Rating(pct.mu, pct.sigma))
            else:
                team0_rated_ratings_before.append(
                    Rating(player.rated_trueskill_mu, player.rated_trueskill_sigma)
                )
        else:
            team1_players.append(in_progress_game_player)
            if player.id in player_category_trueskills_by_id:
                pct = player_category_trueskills_by_id[player.id]
                team1_rated_ratings_before.append(Rating(pct.mu, pct.sigma))
            else:
                team1_rated_ratings_before.append(
                    Rating(player.rated_trueskill_mu, player.rated_trueskill_sigma)
                )

    if queue.category_id:
        category: Category | None = (
            session.query(Category).filter(Category.id == queue.category_id).first()
        )
        if category:
            category_name = category.name
        else:
            # should never happen
            logging.error(
                f"Could not find category with id {queue.category_id} for queue with id {queue.id}"
            )
            await interaction.followup.send(
                embed=discord.Embed(
                    description="Something went wrong, please contact the server owner",
                    color=discord.Colour.red(),
                ),
                ephemeral=True,
            )
            session.close()
            return False
    else:
        category_name = None

    finished_game = FinishedGame(
        average_trueskill=in_progress_game.average_trueskill,
        finished_at=datetime.now(timezone.utc),
        game_id=in_progress_game.id,
        is_rated=queue.is_rated,
        map_full_name=in_progress_game.map_full_name,
        map_short_name=in_progress_game.map_short_name,
        queue_name=queue.name,
        category_name=category_name,
        started_at=in_progress_game.created_at,
        team0_name=in_progress_game.team0_name,
        team1_name=in_progress_game.team1_name,
        win_probability=in_progress_game.win_probability,
        winning_team=winning_team,
    )
    session.add(finished_game)

    result = None
    if winning_team == -1:
        result = [0, 0]
    elif winning_team == 0:
        result = [0, 1]
    elif winning_team == 1:
        result = [1, 0]

    team0_rated_ratings_after: list[Rating]
    team1_rated_ratings_after: list[Rating]
    if len(players) > 1:
        team0_rated_ratings_after, team1_rated_ratings_after = rate(
            [team0_rated_ratings_before, team1_rated_ratings_before], result
        )
    else:
        # Mostly useful for creating solo queues for testing, no real world
        # application
        team0_rated_ratings_after, team1_rated_ratings_after = (
            team0_rated_ratings_before,
            team1_rated_ratings_before,
        )

    for i, team0_gip in enumerate(team0_players):
        player = players_by_id[team0_gip.player_id]
        finished_game_player = FinishedGamePlayer(
            finished_game_id=finished_game.id,
            player_id=player.id,
            player_name=player.name,
            team=team0_gip.team,
            rated_trueskill_mu_before=team0_rated_ratings_before[i].mu,
            rated_trueskill_sigma_before=team0_rated_ratings_before[i].sigma,
            rated_trueskill_mu_after=team0_rated_ratings_after[i].mu,
            rated_trueskill_sigma_after=team0_rated_ratings_after[i].sigma,
        )
        trueskill_rating = team0_rated_ratings_after[i]
        # Regardless of category, always update the master trueskill. That way
        # when we create new categories off of it the data isn't completely
        # stale
        player.rated_trueskill_mu = trueskill_rating.mu
        player.rated_trueskill_sigma = trueskill_rating.sigma
        if player.id in player_category_trueskills_by_id:
            pct = player_category_trueskills_by_id[player.id]
            pct.mu = trueskill_rating.mu
            pct.sigma = trueskill_rating.sigma
            pct.rank = trueskill_rating.mu - 3 * trueskill_rating.sigma
        else:
            session.add(
                PlayerCategoryTrueskill(
                    player_id=player.id,
                    category_id=queue.category_id,
                    mu=trueskill_rating.mu,
                    sigma=trueskill_rating.sigma,
                    rank=trueskill_rating.mu - 3 * trueskill_rating.sigma,
                )
            )
        session.add(finished_game_player)
    for i, team1_gip in enumerate(team1_players):
        player = players_by_id[team1_gip.player_id]
        finished_game_player = FinishedGamePlayer(
            finished_game_id=finished_game.id,
            player_id=player.id,
            player_name=player.name,
            team=team1_gip.team,
            rated_trueskill_mu_before=team1_rated_ratings_before[i].mu,
            rated_trueskill_sigma_before=team1_rated_ratings_before[i].sigma,
            rated_trueskill_mu_after=team1_rated_ratings_after[i].mu,
            rated_trueskill_sigma_after=team1_rated_ratings_after[i].sigma,
        )
        trueskill_rating = team1_rated_ratings_after[i]
        # Regardless of category, always update the master trueskill. That way
        # when we create new categories off of it the data isn't completely
        # stale
        player.rated_trueskill_mu = trueskill_rating.mu
        player.rated_trueskill_sigma = trueskill_rating.sigma
        if player.id in player_category_trueskills_by_id:
            pct = player_category_trueskills_by_id[player.id]
            pct.mu = trueskill_rating.mu
            pct.sigma = trueskill_rating.sigma
            pct.rank = trueskill_rating.mu - 3 * trueskill_rating.sigma
        else:
            session.add(
                PlayerCategoryTrueskill(
                    player_id=player.id,
                    category_id=queue.category_id,
                    mu=trueskill_rating.mu,
                    sigma=trueskill_rating.sigma,
                    rank=trueskill_rating.mu - 3 * trueskill_rating.sigma,
                )
            )
        session.add(finished_game_player)

    session.query(InProgressGamePlayer).filter(
        InProgressGamePlayer.in_progress_game_id == in_progress_game.id
    ).delete()
    in_progress_game.is_finished = True
    embed_description = ""
    duration: timedelta = finished_game.finished_at.replace(
        tzinfo=timezone.utc
    ) - in_progress_game.created_at.replace(tzinfo=timezone.utc)
    if winning_team == 0:
        embed_description = f"**Winner:** {in_progress_game.team0_name}\n**Duration:** {duration.seconds // 60} minutes"
    elif winning_team == 1:
        embed_description = f"**Winner:** {in_progress_game.team1_name}\n**Duration:** {duration.seconds // 60} minutes"
    else:
        embed_description = (
            f"**Tie game**\n**Duration:** {duration.seconds // 60} minutes"
        )
    session.add(
        QueueWaitlist(
            channel_id=interaction.channel_id,  # not sure about this column and what it's used for
            finished_game_id=finished_game.id,
            guild_id=interaction.guild_id,
            in_progress_game_id=in_progress_game.id,
            queue_id=queue.id,
            end_waitlist_at=datetime.now(timezone.utc)
            + timedelta(seconds=config.RE_ADD_DELAY),
        )
    )

    # Reward raffle tickets
    reward = (
        session.query(RotationMap.raffle_ticket_reward)
        .join(Map, Map.id == RotationMap.map_id)
        .join(Rotation, Rotation.id == RotationMap.rotation_id)
        .join(Queue, Queue.rotation_id == Rotation.id)
        .filter(Map.short_name == in_progress_game.map_short_name)
        .filter(Queue.id == in_progress_game.queue_id)
        .scalar()
    )
    if reward == 0:
        reward = config.DEFAULT_RAFFLE_VALUE

    for player in players:
        player.raffle_tickets += reward
        session.add(player)

    queue_name = queue.name
    short_in_progress_game_id = in_progress_game.id.split("-")[0]
    await interaction.followup.send(
        embed=discord.Embed(
            title=f"Game '{queue_name}' ({short_in_progress_game_id}) finished",
            description=embed_description,
            colour=discord.Colour.green(),
        )
    )
    if config.CHANNEL_ID and config.CHANNEL_ID != interaction.channel_id:
        channel: discord.channel.TextChannel | None = discord.utils.get(
            interaction.guild.text_channels, id=config.CHANNEL_ID
        )
        if channel:
            await channel.send(
                embed=discord.Embed(
                    title=f"Game '{queue_name}' ({short_in_progress_game_id}) finished",
                    description=embed_description,
                    colour=discord.Colour.green(),
                )
            )
            await upload_stats_screenshot_imgkit_channel(channel)
    else:
        await upload_stats_screenshot_imgkit_interaction(interaction)

    session.commit()
    session.close()
    return True
