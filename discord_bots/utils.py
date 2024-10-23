# Misc helper functions
import asyncio
import itertools
import logging
import math
import os
import statistics
from datetime import datetime, timedelta, timezone
from heapq import heappop, heappush
from itertools import combinations
from random import choices
from typing import List, Optional

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
    VoiceState,
)
from discord.ext.commands.context import Context
from discord.member import Member
from discord.utils import escape_markdown
from PIL import Image
from selenium import webdriver
from selenium.webdriver.firefox.options import Options as FirefoxOptions
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm.session import Session as SQLAlchemySession
from table2ascii import Alignment, Merge, PresetStyle, table2ascii
from trueskill import Rating, global_env

import discord_bots.config as config
from discord_bots.bot import bot
from discord_bots.models import (
    Category,
    CustomCommand,
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
    QueuePlayer,
    QueueWaitlistPlayer,
    Rotation,
    RotationMap,
    Session,
    SkipMapVote,
)

_log = logging.getLogger(__name__)


MU_LOWER_UNICODE = "\u03BC"
SIGMA_LOWER_UNICODE = "\u03C3"
DELTA_UPPER_UNICODE = "\u03B4"


def build_category_str(category: Category) -> str:
    output = ""
    output += f"**{category.name}**\n"
    output += f"- _Rated: {category.is_rated}_\n"
    output += "- _Sigma decay settings:_\n"
    output += f" - _Decay amount: {category.sigma_decay_amount}_\n"
    output += f" - _Grace days: {category.sigma_decay_grace_days}_\n"
    output += f" - _Max decay proportion: {category.sigma_decay_max_decay_proportion}_\n"
    output += f"- _Minimum games for leaderboard: {category.min_games_for_leaderboard}_\n"
    session: SQLAlchemySession
    with Session() as session:
        queue_names = [
            x[0]
            for x in (
                session.query(Queue.name)
                .filter(Queue.category_id == category.id)
                .order_by(Queue.ordinal.asc())
                .all()
            )
        ]
        if not queue_names:
            output += f"- _Queues: None_\n\n"
        else:
            output += f"- _Queues: {', '.join(queue_names)}_\n\n"
        return output


def get_n_best_finished_game_teams(
    fgps: list[FinishedGamePlayer], team_size: int, is_rated: bool, n: int
) -> list[tuple[list[FinishedGamePlayer], float]]:
    return get_n_finished_game_teams(fgps, team_size, is_rated, n, 1)


def get_n_worst_finished_game_teams(
    fgps: list[FinishedGamePlayer], team_size: int, is_rated: bool, n: int
) -> list[tuple[list[FinishedGamePlayer], float]]:
    return get_n_finished_game_teams(fgps, team_size, is_rated, n, -1)


# Return n of the most even or least even teams
# For best teams, use direction = 1, for worst teams use direction = -1
def get_n_finished_game_teams(
    fgps: list[FinishedGamePlayer],
    team_size: int,
    is_rated: bool,
    n: int,
    direction: int = 1,
) -> list[tuple[list[FinishedGamePlayer], float]]:
    teams: list[tuple[float, list[FinishedGamePlayer]]] = []

    all_combinations = list(combinations(fgps, team_size))
    for team0 in all_combinations:
        team1 = [p for p in fgps if p not in team0]
        team0_ratings = list(
            map(
                lambda x: Rating(
                    x.rated_trueskill_mu_before, x.rated_trueskill_sigma_before
                ),
                team0,
            )
        )
        team1_ratings = list(
            map(
                lambda x: Rating(
                    x.rated_trueskill_mu_before, x.rated_trueskill_sigma_before
                ),
                team1,
            )
        )
        win_prob = win_probability(team0_ratings, team1_ratings)
        current_team_evenness = abs(0.50 - win_prob)
        heappush(
            teams, (direction * current_team_evenness, list(team0[:]) + list(team1[:]))
        )

    teams_out = []
    for _ in range(n):
        teams_out.append(heappop(teams))

    return teams_out


def get_n_best_teams(
    players: list[Player], team_size: int, is_rated: bool, n: int
) -> list[tuple[list[Player], float]]:
    return get_n_teams(players, team_size, is_rated, n, 1)


def get_n_worst_teams(
    players: list[Player], team_size: int, is_rated: bool, n: int
) -> list[tuple[list[Player], float]]:
    return get_n_teams(players, team_size, is_rated, n, -1)


# Return n of the most even or least even teams
# For best teams, use direction = 1, for worst teams use direction = -1
def get_n_teams(
    players: list[Player],
    team_size: int,
    is_rated: bool,
    n: int,
    direction: int = 1,
) -> list[tuple[list[Player], float]]:
    teams: list[tuple[float, list[Player]]] = []

    all_combinations = list(combinations(players, team_size))
    for team0 in all_combinations:
        team1 = [p for p in players if p not in team0]
        team0_ratings = list(
            map(
                lambda x: Rating(x.rated_trueskill_mu, x.rated_trueskill_sigma),
                team0,
            )
        )
        team1_ratings = list(
            map(
                lambda x: Rating(x.rated_trueskill_mu, x.rated_trueskill_sigma),
                team1,
            )
        )
        win_prob = win_probability(team0_ratings, team1_ratings)
        current_team_evenness = abs(0.50 - win_prob)
        heappush(
            teams, (direction * current_team_evenness, list(team0[:]) + list(team1[:]))
        )

    teams_out = []
    for _ in range(n):
        teams_out.append(heappop(teams))

    return teams_out


def mock_teams_str(
    team0_players: list[Player],
    team1_players: list[Player],
    is_rated: bool,
) -> str:
    """
    Helper method to debug print teams if these were the players
    """
    output = ""
    team0_rating = [
        Rating(p.rated_trueskill_mu, p.rated_trueskill_sigma) for p in team0_players
    ]
    team1_rating = [
        Rating(p.rated_trueskill_mu, p.rated_trueskill_sigma) for p in team1_players
    ]
    team0_names = ", ".join(
        sorted([escape_markdown(player.name) for player in team0_players])
    )
    team1_names = ", ".join(
        sorted([escape_markdown(player.name) for player in team1_players])
    )
    team0_win_prob = round(100 * win_probability(team0_rating, team1_rating), 1)
    team1_win_prob = round(100 - team0_win_prob, 1)
    team0_mu = round(mean([player.rated_trueskill_mu for player in team0_players]), 2)
    team1_mu = round(mean([player.rated_trueskill_mu for player in team1_players]), 2)
    team0_sigma = round(
        mean([player.rated_trueskill_sigma for player in team0_players]),
        2,
    )
    team1_sigma = round(
        mean([player.rated_trueskill_sigma for player in team1_players]),
        2,
    )
    output += f"\n**BE** (**{team0_win_prob}%**, mu: {team0_mu}, sigma: {team0_sigma}): {team0_names}"
    output += f"\n**DS** (**{team1_win_prob}%**, mu: {team1_mu}, sigma: {team1_sigma}): {team1_names}"
    return output


def in_progress_game_str(in_progress_game: InProgressGame, debug: bool = False) -> str:
    """
    Helper method to pretty print a finished game
    """
    output = ""
    session: sqlalchemy.orm.Session
    with Session() as session:
        short_game_id = short_uuid(in_progress_game.id)
        queue: Queue = (
            session.query(Queue).filter(Queue.id == in_progress_game.queue_id).first()
        )
        if debug:
            output += f"**{queue.name}** ({short_game_id}) (TS: {round(in_progress_game.average_trueskill, 2)})"
        else:
            output += f"**{queue.name}** ({short_game_id})"
        team0_igp_players: list[InProgressGamePlayer] = session.query(
            InProgressGamePlayer
        ).filter(
            InProgressGamePlayer.in_progress_game_id == in_progress_game.id,
            InProgressGamePlayer.team == 0,
        )
        team1_igp_players: list[InProgressGamePlayer] = session.query(
            InProgressGamePlayer
        ).filter(
            InProgressGamePlayer.in_progress_game_id == in_progress_game.id,
            InProgressGamePlayer.team == 1,
        )
        team0_player_ids = set(map(lambda x: x.player_id, team0_igp_players))
        team1_player_ids = set(map(lambda x: x.player_id, team1_igp_players))
        team0_fgp_by_id = {fgp.player_id: fgp for fgp in team0_igp_players}
        team1_fgp_by_id = {fgp.player_id: fgp for fgp in team1_igp_players}
        team0_players: list[Player] = session.query(Player).filter(Player.id.in_(team0_player_ids))  # type: ignore
        team1_players: list[Player] = session.query(Player).filter(Player.id.in_(team1_player_ids))  # type: ignore
        if debug and False:
            team0_names = ", ".join(
                sorted(
                    [
                        f"{escape_markdown(player.name)} ({round(team0_fgp_by_id[player.id].rated_trueskill_mu_before, 1)})"
                        for player in team0_players
                    ]
                )
            )
            team1_names = ", ".join(
                sorted(
                    [
                        f"{escape_markdown(player.name)} ({round(team1_fgp_by_id[player.id].rated_trueskill_mu_before, 1)})"
                        for player in team1_players
                    ]
                )
            )
        else:
            team0_names = ", ".join(
                sorted([escape_markdown(player.name) for player in team0_players])
            )
            team1_names = ", ".join(
                sorted([escape_markdown(player.name) for player in team1_players])
            )
        # TODO: Include win prob
        # team0_win_prob = round(100 * finished_game.win_probability, 1)
        # team1_win_prob = round(100 - team0_win_prob, 1)
        team0_tsr = round(
            mean([player.rated_trueskill_mu for player in team0_players]), 1
        )
        team1_tsr = round(
            mean([player.rated_trueskill_mu for player in team1_players]), 1
        )
        # TODO: Include win prob
        if debug:
            team0_str = f"{in_progress_game.team0_name} ({team0_tsr}): {team0_names}"
            team1_str = f"{in_progress_game.team1_name} ({team1_tsr}): {team1_names}"
        else:
            team0_str = f"{in_progress_game.team0_name} ({team0_names}"
            team1_str = f"{in_progress_game.team1_name} ({team1_names}"

        output += f"\n{team0_str}"
        output += f"\n{team1_str}"
        delta: timedelta = datetime.now(
            timezone.utc
        ) - in_progress_game.created_at.replace(tzinfo=timezone.utc)
        if delta.days > 0:
            output += f"\n@ {delta.days} days ago\n"
        elif delta.seconds > 3600:
            hours_ago = delta.seconds // 3600
            output += f"\n@ {hours_ago} hours ago\n"
        else:
            minutes_ago = delta.seconds // 60
            output += f"\n@ {minutes_ago} minutes ago\n"
        return output


def mock_finished_game_teams_str(
    team0_fg_players: list[FinishedGamePlayer],
    team1_fg_players: list[FinishedGamePlayer],
    is_rated: bool,
) -> str:
    """
    Helper method to debug print teams if these were the players
    """
    output = ""
    session: sqlalchemy.orm.Session
    with Session() as session:
        team0_rating = [
            Rating(fgp.rated_trueskill_mu_before, fgp.rated_trueskill_sigma_before)
            for fgp in team0_fg_players
        ]
        team1_rating = [
            Rating(fgp.rated_trueskill_mu_before, fgp.rated_trueskill_sigma_before)
            for fgp in team1_fg_players
        ]
        team0_player_ids_map = {x.player_id: x for x in team0_fg_players}
        team1_player_ids_map = {x.player_id: x for x in team1_fg_players}
        team0_player_ids = set(map(lambda x: x.player_id, team0_fg_players))
        team1_player_ids = set(map(lambda x: x.player_id, team1_fg_players))
        team0_players: list[Player] = session.query(Player).filter(
            Player.id.in_(team0_player_ids)
        )
        team1_players: list[Player] = session.query(Player).filter(
            Player.id.in_(team1_player_ids)
        )
        team0_names = ", ".join(
            sorted(
                [
                    f"{escape_markdown(player.name)} ({round(team0_player_ids_map.get(player.id).rated_trueskill_mu_before, 1)})"
                    for player in team0_players
                ]
            )
        )
        team1_names = ", ".join(
            sorted(
                [
                    f"{escape_markdown(player.name)} ({round(team1_player_ids_map.get(player.id).rated_trueskill_mu_before, 1)})"
                    for player in team1_players
                ]
            )
        )
        team0_win_prob = round(100 * win_probability(team0_rating, team1_rating), 1)
        team1_win_prob = round(100 - team0_win_prob, 1)
        team0_mu = round(
            mean([player.rated_trueskill_mu_before for player in team0_fg_players]), 2
        )
        team1_mu = round(
            mean([player.rated_trueskill_mu_before for player in team1_fg_players]), 2
        )
        team0_sigma = round(
            mean([player.rated_trueskill_sigma_before for player in team0_fg_players]),
            2,
        )
        team1_sigma = round(
            mean([player.rated_trueskill_sigma_before for player in team1_fg_players]),
            2,
        )
        output += f"\n**BE** (**{team0_win_prob}%**, mu: {team0_mu}, sigma: {team0_sigma}): {team0_names}"
        output += f"\n**DS** (**{team1_win_prob}%**, mu: {team1_mu}, sigma: {team1_sigma}): {team1_names}"
        return output


def is_in_game(player_id: int) -> bool:
    session: sqlalchemy.orm.Session
    with Session() as session:
        return get_player_game(player_id, session) is not None


def get_player_game(player_id: int, session=None) -> InProgressGame | None:
    """
    Find the game a player is currently in

    :session: Pass in a session if you want to do something with the game that
    gets returned
    """
    should_close = False
    if not session:
        should_close = True
        session = (
            Session()
        )  # TODO: this session has the potential to not be closed, replace with context manager
    ipg_player = (
        session.query(InProgressGamePlayer)
        .join(InProgressGame)
        .filter(InProgressGamePlayer.player_id == player_id)
        .first()
    )
    if ipg_player:
        if should_close:
            session.close()
        return (
            session.query(InProgressGame)
            .filter(InProgressGame.id == ipg_player.in_progress_game_id)
            .first()
        )
    else:
        if should_close:
            session.close()
        return None


def finished_game_str(finished_game: FinishedGame, debug: bool = False) -> str:
    """
    Helper method to pretty print a finished game
    """
    output = ""
    session: sqlalchemy.orm.Session
    with Session() as session:
        short_game_id = short_uuid(finished_game.game_id)
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

        if config.SHOW_TRUESKILL:
            output += f"**{finished_game.queue_name}** - **{finished_game.map_short_name}** ({short_game_id}) (mu: {round(finished_game.average_trueskill, 2)})"
        else:
            output += f"**{finished_game.queue_name}** - **{finished_game.map_short_name}** ({short_game_id})"

        team0_player_ids = set(map(lambda x: x.player_id, team0_fg_players))
        team1_player_ids = set(map(lambda x: x.player_id, team1_fg_players))
        team0_fgp_by_id = {fgp.player_id: fgp for fgp in team0_fg_players}
        team1_fgp_by_id = {fgp.player_id: fgp for fgp in team1_fg_players}
        team0_players: list[Player] = session.query(Player).filter(Player.id.in_(team0_player_ids))  # type: ignore
        team1_players: list[Player] = session.query(Player).filter(Player.id.in_(team1_player_ids))  # type: ignore
        if debug:
            team0_names = ", ".join(
                sorted(
                    [
                        f"{escape_markdown(player.name)} ({round(team0_fgp_by_id[player.id].rated_trueskill_mu_before, 1)})"
                        for player in team0_players
                    ]
                )
            )
            team1_names = ", ".join(
                sorted(
                    [
                        f"{escape_markdown(player.name)} ({round(team1_fgp_by_id[player.id].rated_trueskill_mu_before, 1)})"
                        for player in team1_players
                    ]
                )
            )
        else:
            team0_names = ", ".join(
                sorted([escape_markdown(player.name) for player in team0_players])
            )
            team1_names = ", ".join(
                sorted([escape_markdown(player.name) for player in team1_players])
            )
        team0_win_prob = round(100 * finished_game.win_probability, 1)
        team1_win_prob = round(100 - team0_win_prob, 1)
        team0_str = f"{finished_game.team0_name} ({team0_win_prob}%): {team0_names}"
        team1_str = f"{finished_game.team1_name} ({team1_win_prob}%): {team1_names}"

        if finished_game.winning_team == 0:
            output += f"\n**{team0_str}**"
            output += f"\n{team1_str}"
        elif finished_game.winning_team == 1:
            output += f"\n{team0_str}"
            output += f"\n**{team1_str}**"
        else:
            output += f"\n{team0_str}"
            output += f"\n{team1_str}"
        delta: timedelta = datetime.now(
            timezone.utc
        ) - finished_game.finished_at.replace(tzinfo=timezone.utc)
        if delta.days > 0:
            output += f"\n@ {delta.days} days ago\n"
        elif delta.seconds > 3600:
            hours_ago = delta.seconds // 3600
            output += f"\n@ {hours_ago} hours ago\n"
        else:
            minutes_ago = delta.seconds // 60
            output += f"\n@ {minutes_ago} minutes ago\n"
        return output


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
    show_map_image: bool = True,
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
        name=f"🔴 {game.team0_name} ({round(100 * game.win_probability, 1)}%)",
        value=(
            f"\n>>> {newline.join(team0_player_names)}"
            if team0_player_names
            else "> \n** **"
        ),
        inline=True,
    )
    embed.add_field(
        name=f"🔵 {game.team1_name} ({round(100 * (1 - game.win_probability), 1)}%)",
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
    """ TODO: find a way to add this while keeping the embed compact
    if game.channel_id:
        embed.add_field(name="📺 Channel", value=f"<#{game.channel_id}>", inline=True)
    """
    if game.code:
        embed.add_field(
            name="🔢 Game Code",
            value=code_block(game.code, language="yaml"),
            inline=True,
        )
    add_empty_field(embed, offset=3)
    if show_map_image:
        map: Map | None = (
            session.query(Map)
            .filter(
                or_(
                    Map.full_name == game.map_full_name,
                    Map.short_name == game.map_short_name,
                )
            )
            .first()
        )
        if map and map.image_url:
            embed.set_image(url=map.image_url)
    return embed


async def create_condensed_in_progress_game_embed(
    session: sqlalchemy.orm.Session,
    game: InProgressGame,
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
    team0_player_names = [f"**{name[0]}**" for name in result if name] if result else []
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
        [f"**{name[0]}**" for name in result if name] if result else []
    )
    if config.SHOW_CAPTAINS:
        if team0_player_names:
            team0_player_names[0] = "(C) " + team0_player_names[0]
        if team1_player_names:
            team1_player_names[0] = "(C) " + team1_player_names[0]
    # sort the names alphabetically and caselessly to make them easier to read
    team0_player_names.sort(key=str.casefold)
    team1_player_names.sort(key=str.casefold)
    content = ""
    content += f"🗺️ Map: **{game.map_full_name} ({game.map_short_name})**"
    content += f"\n🔴 {game.team0_name} ({round(100 * game.win_probability, 1)}%):"
    content += (
        f'\n> {", ".join(team0_player_names)}' if team0_player_names else "\n> ** **"
    )
    content += (
        f"\n🔵 {game.team1_name} ({round(100 * (1 - game.win_probability), 1)}%):"
    )
    content += (
        f'\n> {", ".join(team1_player_names)}' if team1_player_names else "\n> ** **"
    )
    content += f"\n*{timestamp}*"
    embed.description = content
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
    newline = "\n"
    team0_embed_value: str = ""
    team1_embed_value: str = ""
    if finished_game.winning_team == 0:
        be_str = f"🥇 {finished_game.team0_name}"
        ds_str = f"🥈 {finished_game.team1_name}"
        if team0_player_names:
            team0_embed_value = f"\n>>> {'**{0}**'.format(newline.join(team0_player_names))}"  # bold winning names
        if team1_player_names:
            team1_embed_value = f"\n>>> {newline.join(team1_player_names)}"
    elif finished_game.winning_team == 1:
        be_str = f"🥈 {finished_game.team0_name}"
        ds_str = f"🥇 {finished_game.team1_name}"
        if team0_player_names:
            team0_embed_value = f"\n>>> {newline.join(team0_player_names)}"
        if team1_player_names:
            team1_embed_value = f"\n>>> {'**{0}**'.format(newline.join(team1_player_names))}"  # bold winning names
    else:
        be_str = f"🤝 {finished_game.team0_name}"
        ds_str = f"🤝 {finished_game.team1_name}"
        if team0_player_names:
            team0_embed_value = f"\n>>> {newline.join(team0_player_names)}"
        if team1_player_names:
            team1_embed_value = f"\n>>> {newline.join(team1_player_names)}"
    embed.add_field(
        name=f"{be_str} ({round(100 * finished_game.win_probability, 1)}%)",
        value=team0_embed_value,
        inline=True,
    )
    embed.add_field(
        name=f"{ds_str} ({round(100*(1 - finished_game.win_probability), 1)}%)",
        value=team1_embed_value,
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
    map: Map | None = (
        session.query(Map)
        .filter(
            or_(
                Map.full_name == finished_game.map_full_name,
                Map.short_name == finished_game.map_short_name,
            )
        )
        .first()
    )
    if map and map.image_url:
        embed.set_image(url=map.image_url)
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
        name=f"{in_progress_game.team0_name} ({round(100 * in_progress_game.win_probability, 1)}%)",
        value="\n".join([f"> {ipgp.player.name}" for ipgp in team0_fg_players]),
        inline=True,
    )
    embed.add_field(
        name=f"{in_progress_game.team1_name} ({round(100*(1 - in_progress_game.win_probability), 1)}%)",
        value="\n".join([f"> {ipgp.player.name}" for ipgp in team1_fg_players]),
        inline=True,
    )
    embed.add_field(name="", value="", inline=False)  # newline
    embed.add_field(
        name="🗺️️ Map",
        value=f"{in_progress_game.map_full_name} ({in_progress_game.map_short_name})",
        inline=True,
    )
    # probably don't need duration for cancelled games, but might as well add it
    duration: timedelta = discord.utils.utcnow() - in_progress_game.created_at.replace(
        tzinfo=timezone.utc
    )
    embed.add_field(
        name="⏱️ Duration",
        value=f"{duration.seconds // 60} minutes",
        inline=True,
    )
    if config.SHOW_TRUESKILL:
        embed.add_field(
            name=f"📊 Average {MU_LOWER_UNICODE}",
            value=round(in_progress_game.average_trueskill, 2),
            inline=True,
        )
    map: Map | None = (
        session.query(Map)
        .filter(
            or_(
                Map.full_name == in_progress_game.map_full_name,
                Map.short_name == in_progress_game.map_short_name,
            )
        )
        .first()
    )
    if map and map.image_url:
        embed.set_image(url=map.image_url)
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


def win_probability_matchmaking(team0: list[Rating], team1: list[Rating]) -> float:
    """
    See win_probability_matchmaking.
    May only be used for matchmaking! This way the rating calculations are unaffected.

    Alterations: To help with new players not leading to absolutely stacked games (as new players generally don't come
        in with a skill level similar to the average player) we subtract a sigma-based amount from players mu.
        New players join with high sima and are impacted more heavily than settled players with low sigmas.
    Theorized improvements: instead of using a static multiplier to alter the mu we could use a (linear?) function
        that returns 2 for 0 played games and 0.4 for high game counts or win counts. This more closely resembles the `v0` variable
        described in the TS whitepaper to help model new players. `v0` sadly is not available in the ts-python-lib.
    """
    mmu = lambda p: p.mu - config.MM_SIGMA_MULT * p.sigma
    delta_mu = sum(mmu(r) for r in team0) - sum(mmu(r) for r in team1)
    sum_sigma = sum(r.sigma ** 2 for r in itertools.chain(team0, team1))
    size = len(team0) + len(team1)
    denom = math.sqrt(size * config.DEFAULT_TRUESKILL_BETA**2 + sum_sigma)
    trueskill = global_env()

    return trueskill.cdf(delta_mu / denom)


def win_probability(team0: list[Rating], team1: list[Rating]) -> float:
    """
    Calculate the probability that team0 beats team1
    Taken from https://trueskill.org/#win-probability
    """
    delta_mu = sum(r.mu for r in team0) - sum(r.mu for r in team1)
    sum_sigma = sum(r.sigma ** 2 for r in itertools.chain(team0, team1))
    size = len(team0) + len(team1)
    denom = math.sqrt(size * config.DEFAULT_TRUESKILL_BETA**2 + sum_sigma)
    trueskill = global_env()

    return trueskill.cdf(delta_mu / denom)


async def execute_map_rotation(rotation_id: str, is_verbose: bool):
    """
    :is_verbose: specifies if we want to see queues affected in the bot response.
        currently passing in False for when game pops, True for everything else.
    """
    session: sqlalchemy.orm.Session
    with Session() as session:
        rotation: Rotation | None = (
            session.query(Rotation).filter(Rotation.id == rotation_id).first()
        )
        if not rotation:
            _log.warning(
                f"[execute_map_rotation] Could not find rotation {rotation_id}, skipping update"
            )
            return

        if rotation.is_random:
            # Follow-Up:
            #  - introduce backlog of previous maps per rotation -> RotationMapHistory
            #     update whenever a new map is chosen regardless of reason
            #     -> vote
            #     -> skip
            #     -> admin force
            #     -> game start
            #     -> autorotation
            #  - introduce Rotation.min_maps_before_requeue (int)
            #       -> not eligible if maps are available that haven't been queue recently
            #  - introduce Rotation.weight_increase (float)
            #       -> weight increases to weight + Math.floor(value*x) x = times not selected since eligible
            #  - refactoring: introduce  "update_next_map_to" function parallel to this that is called rather than the "manual" updates we have now scattered all over tha place
            eligible_maps: list[RotationMap] = (
                session.query(RotationMap)
                .filter(RotationMap.rotation_id == rotation_id)
                .filter(RotationMap.is_next == False)
                .all()
            )
            if not eligible_maps:
                _log.error(
                    f"[execute_map_rotation] Could not find a random rotation_map for rotation {rotation.id}. There are likely no rotation_maps to pick from"
                )
                return
            following_map = choices(eligible_maps, weights=[x.random_weight for x in eligible_maps])[0]
        else:
            current_rotation_map: RotationMap | None = (
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
            following_ordinal = current_rotation_map.ordinal + 1 if current_rotation_map else 1
            if following_ordinal > rotation_map_length:
                following_ordinal = 1

            following_map: RotationMap | None = (
                session.query(RotationMap)
                .filter(RotationMap.rotation_id == rotation_id)
                .filter(RotationMap.ordinal == following_ordinal)
                .first()
            )
            if not following_map:
                _log.error(
                    f"[execute_map_rotation] Could not find a rotation_map after next with ordinal {following_ordinal} for rotation {rotation.id}"
                )
                return

    await update_next_map(rotation.id, following_map.id, is_verbose)


async def update_next_map(rotation_id: str, new_rotation_map_id: str, is_verbose: bool = True):
    """
    Central function to update next rotation.
    Removes all votes

    :rotation_id: The id of the rotation. Must be verified beforehand
    :rotation_map_id: The id of the rotation map. Must be verified beforehand
    :is_verbose: specifies if we want to see queues affected in the bot response.
        currently passing in False for when game pops, True for everything else.
    """
    session: sqlalchemy.orm.Session
    with Session() as session:
        next_rotation_map: RotationMap = (
            session.query(RotationMap)
            .filter(RotationMap.id == new_rotation_map_id)
            .one()
        )
        session.query(RotationMap) \
            .filter(RotationMap.rotation_id == rotation_id) \
            .filter(RotationMap.is_next == True) \
            .update({"is_next": False})
        next_rotation_map.is_next = True

        map_votes: list[MapVote] = (
            session.query(MapVote)
            .join(RotationMap, RotationMap.id == MapVote.rotation_map_id)
            .filter(RotationMap.rotation_id == rotation_id)
            .all()
        )
        for map_vote in map_votes:
            session.delete(map_vote)
        session.query(SkipMapVote) \
            .filter(SkipMapVote.rotation_id == rotation_id) \
            .delete()
        session.commit()

        channel = bot.get_channel(config.CHANNEL_ID)
        if isinstance(channel, discord.TextChannel):
            if is_verbose:
                next_map: Map = (
                    session.query(Map)
                    .filter(Map.id == next_rotation_map.map_id)
                    .one()
                )
                affected_queues: list[Queue] = (
                    session.query(Queue.name)
                    .filter(Queue.rotation_id == rotation_id)
                    .all()
                )
                affected_queue_names = (
                    [q.name for q in affected_queues]
                    if affected_queues
                    else []
                )
                await send_message(
                    channel,
                    embed_title=f"Next Map rotated to {next_map.full_name}",
                    embed_description=f"Queues affected: **{', '.join(affected_queue_names)}**",
                    embed_footer="All votes removed",
                    image_url=next_map.image_url,
                    colour=Colour.blue(),
                )


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
    image_url: str | None = None,
    embed_footer: str | None = None,
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
    if embed_footer:
        embed.set_footer(text=embed_footer)
    if colour:
        embed.colour = colour
    if image_url:
        embed.set_image(url=image_url)
    try:
        message = await channel.send(
            content=content, embed=embed, delete_after=delete_after
        )
    except Exception:
        _log.exception("[send_message] Ignoring exception:")
    return message


async def print_leaderboard():
    message_content = ""
    session: SQLAlchemySession
    with Session() as session:
        categories: list[Category] = (
            session.query(Category)
            .filter(Category.is_rated == True)
            .order_by("name")
            .all()
        )
        if len(categories) > 0:
            for i, category in enumerate(categories):
                subquery = (
                    session.query(FinishedGamePlayer.player_id)
                    .join(
                        FinishedGame,
                        FinishedGame.id == FinishedGamePlayer.finished_game_id,
                    )
                    .filter(
                        FinishedGame.started_at
                        > (datetime.now(timezone.utc) - timedelta(days=30))
                    )
                    .filter(FinishedGame.category_name == category.name)
                    .group_by(FinishedGamePlayer.player_id)
                    .having(func.count() >= category.min_games_for_leaderboard)
                    .subquery()
                )
                top_10_pcts: list[PlayerCategoryTrueskill] | None = (
                    session.query(PlayerCategoryTrueskill)
                    .join(Player, Player.id == PlayerCategoryTrueskill.player_id)
                    .filter(PlayerCategoryTrueskill.player_id.in_(select(subquery)))
                    .filter(PlayerCategoryTrueskill.category_id == category.id)
                    .filter(Player.leaderboard_enabled == True)
                    .order_by(PlayerCategoryTrueskill.rank.desc())
                    .limit(10)
                    .all()
                )
                if top_10_pcts:
                    message_content += f"**{category.name} Leaderboard**"
                    cols = []
                    for i, pct in enumerate(top_10_pcts, 1):
                        # TODO: merge this with the pct query
                        player: Player | None = (
                            session.query(Player)
                            .filter(Player.id == pct.player_id)
                            .first()
                        )
                        if player:
                            if i == 1:
                                player_name = f"{player.name}🥇"
                            elif i == 2:
                                player_name = f"{player.name}🥈"
                            elif i == 3:
                                player_name = f"{player.name}🥉"
                            else:
                                player_name = player.name
                            col = [
                                i,
                                round(pct.rank, 1),
                                round(pct.mu, 1),
                                round(pct.sigma, 1),
                                player_name,
                            ]
                            cols.append(col)
                    if category.min_games_for_leaderboard > 0:
                        footer = [
                            f"Min. {category.min_games_for_leaderboard} {'games' if category.min_games_for_leaderboard > 1 else 'game'} played in the last 30 days",
                            Merge.LEFT,
                            Merge.LEFT,
                            Merge.LEFT,
                            Merge.LEFT,
                        ]
                    else:
                        footer = None
                    table = table2ascii(
                        header=[
                            Merge.LEFT,
                            "Rank",
                            MU_LOWER_UNICODE,
                            SIGMA_LOWER_UNICODE,
                            "Name",
                        ],
                        body=cols,
                        style=PresetStyle.plain,
                        alignments=[
                            Alignment.DECIMAL,
                            Alignment.DECIMAL,
                            Alignment.DECIMAL,
                            Alignment.DECIMAL,
                            Alignment.LEFT,
                        ],
                        footer=footer,
                    )
                    message_content += f"{code_block(table)}"
        if config.ECONOMY_ENABLED:
            # TODO: merge with new leaderboard style
            message_content += f"\n**{config.CURRENCY_NAME}**"
            top_10_player_currency: list[Player] = (
                session.query(Player).order_by(Player.currency.desc()).limit(10)
            )
            for i, player_currency in enumerate(top_10_player_currency, 1):
                message_content += (
                    f"\n{i}. {player_currency.currency} - <@{player_currency.id}>"
                )

    message_content += (
        f"Last updated: {discord.utils.format_dt(discord.utils.utcnow(), 'R')}"
    )
    message_content += (
        "\n*`/player toggleleaderboard` to show/hide yourself from the leaderboard*"
    )

    if config.LEADERBOARD_CHANNEL:
        leaderboard_channel = bot.get_channel(config.LEADERBOARD_CHANNEL)
        if leaderboard_channel and isinstance(leaderboard_channel, TextChannel):
            try:
                if leaderboard_channel.last_message_id:
                    last_message: Message = await leaderboard_channel.fetch_message(
                        leaderboard_channel.last_message_id
                    )
                if last_message:
                    await last_message.edit(content=message_content)
                    return
            except Exception as e:
                pass
            if len(message_content) > 2000:
                _log.warning(
                    "[print_leaderboard] The leaderboard is > 2000 characters. Try reducing its length by removing categories"
                )
            await leaderboard_channel.send(
                content=message_content[:2000]
            )  # TODO: paginate this instead


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
        be_voice_channel, ds_voice_channel = get_team_voice_channels(
            session, in_progress_game, guild
        )

        # TODO: combine for loops into one for all players
        coroutines = []
        for player in team0_players:
            if player.move_enabled and be_voice_channel:
                member: Member | None = guild.get_member(player.id)
                if member:
                    member_voice: VoiceState | None = member.voice
                    if member_voice and member_voice.channel:
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
                    if member_voice and member_voice.channel:
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


async def move_game_players_lobby(game_id: str, guild: Guild):
    session: sqlalchemy.orm.Session
    with Session() as session:
        in_progress_game: InProgressGame | None = (
            session.query(InProgressGame).filter(InProgressGame.id == game_id).first()
        )
        if not in_progress_game:
            return

        queue: Queue | None = (
            session.query(Queue).filter(Queue.id == in_progress_game.queue_id).first()
        )
        if not queue or not queue.move_enabled:
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
                if members:
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


def win_rate(wins, losses, ties):
    denominator = max(wins + losses + ties, 1)
    return round(100 * (wins + 0.5 * ties) / denominator, 1)


def default_sigma_decay_amount() -> float:
    """
    The default sigma decay applied to new categories
    Which causes decay from 0 sigma to default over a year
    """
    return config.DEFAULT_TRUESKILL_SIGMA / 365


def add_empty_field(embed: discord.Embed, *, offset: int = 0):
    """
    :offset Amount to deduct from the length of the embed's fields. Useful if you want to ignore some non-inlined fields beforehand.
    embeds are allowed 3 "columns" per "row" for fields.
    To line everything up nicely when there are >= 5 embed fields and only one "column" slot left, we add a blank.
    """
    if not embed.fields:
        return embed
    num_fields = len(embed.fields) - offset
    if num_fields >= 5 and num_fields % 3 == 2:
        embed.add_field(name="", value="", inline=True)
    return embed


async def map_short_name_autocomplete(interaction: Interaction, current: str):
    result = []
    session: SQLAlchemySession
    with Session() as session:
        maps: list[Map] | None = (
            session.query(Map).order_by(Map.full_name).limit(25).all()
        )
        if maps:
            for map in maps:
                current_casefold = current.casefold()
                if (
                    current_casefold in map.short_name.casefold()
                    or current_casefold in map.full_name.casefold()
                ):
                    result.append(
                        discord.app_commands.Choice(
                            name=map.full_name, value=map.short_name
                        )
                    )
    return result


async def map_full_name_autocomplete(interaction: Interaction, current: str):
    result = []
    session: SQLAlchemySession
    with Session() as session:
        maps: list[Map] | None = (
            session.query(Map).order_by(Map.full_name).limit(25).all()
        )
        if maps:
            for map in maps:
                current_casefold = current.casefold()
                if (
                    current_casefold in map.short_name.casefold()
                    or current_casefold in map.full_name.casefold()
                ):
                    result.append(
                        discord.app_commands.Choice(
                            name=map.full_name, value=map.full_name
                        )
                    )
    return result


async def queue_autocomplete(interaction: Interaction, current: str):
    result = []
    session: SQLAlchemySession
    with Session() as session:
        queues: list[Queue] | None = (
            session.query(Queue).order_by(Queue.ordinal).limit(25).all()
        )
        if queues:
            current_casefold = current.casefold()
            for queue in queues:
                if current_casefold in queue.name.casefold():
                    result.append(
                        discord.app_commands.Choice(name=queue.name, value=queue.name)
                    )
    return result


async def in_progress_game_autocomplete(interaction: Interaction, current: str):
    result = []
    session: SQLAlchemySession
    with Session() as session:
        in_progress_games: list[InProgressGame] | None = (
            session.query(InProgressGame).limit(25).all()
        )  # discord only supports up to 25 choices
        if in_progress_games:
            for ipg in in_progress_games:
                short_game_id = short_uuid(ipg.id)
                if current in short_game_id:
                    result.append(
                        discord.app_commands.Choice(
                            name=short_game_id, value=short_game_id
                        )
                    )
    return result


async def rotation_autocomplete(interaction: Interaction, current: str):
    result = []
    session: SQLAlchemySession
    with Session() as session:
        rotations: list[Rotation] | None = (
            session.query(Rotation).order_by(Rotation.name).limit(25).all()
        )
        if rotations:
            current_casefold = current.casefold()
            for rotation in rotations:
                if current_casefold in rotation.name.casefold():
                    result.append(
                        discord.app_commands.Choice(
                            name=rotation.name, value=rotation.name
                        )
                    )
    return result


async def category_autocomplete_with_user_id(interaction: Interaction, current: str):
    # useful for when you want to filter the categories based on the ones the author has games played in
    choices = []
    session: SQLAlchemySession
    with Session() as session:
        result = (
            session.query(Category.name, PlayerCategoryTrueskill.player_id)
            .join(PlayerCategoryTrueskill)
            .filter(PlayerCategoryTrueskill.player_id == interaction.user.id)
            .order_by(Category.name)
            .limit(25)  # discord only supports up to 25 choices
            .all()
        )
        category_names: list[str] = [r[0] for r in result] if result else []
        current_casefold = current.casefold()
        for name in category_names:
            if current_casefold in name.casefold():
                choices.append(
                    discord.app_commands.Choice(
                        name=name,
                        value=name,
                    )
                )
    return choices


async def category_name_autocomplete_without_user_id(
    interaction: Interaction, current: str
):
    # useful for when you want all of the categories, regardless of whether the user has played games in them
    choices = []
    session: SQLAlchemySession
    with Session() as session:
        categories: list[Category] | None = (
            session.query(Category)
            .order_by(Category.name)
            .limit(25)  # discord only supports up to 25 choices
            .all()
        )
        if not categories:
            return []
        current_casefold = current.casefold()
        for category in categories:
            if current_casefold in category.name.casefold():
                choices.append(
                    discord.app_commands.Choice(
                        name=category.name,
                        value=category.name,
                    )
                )
    return choices


async def command_autocomplete(interaction: Interaction, current: str):
    result = []
    session: SQLAlchemySession
    with Session() as session:
        commands: list[CustomCommand] | None = (
            session.query(CustomCommand).order_by(CustomCommand.name).limit(25).all()
        )  # discord only supports up to 25 choices
        if commands:
            for command in commands:
                if current in command.name:
                    result.append(
                        discord.app_commands.Choice(
                            name=command.name, value=command.name
                        )
                    )
    return result


def del_player_from_queues_and_waitlists(
    session: sqlalchemy.orm.Session, player_id: int, *args: str
) -> list[Queue]:
    queues_del_from_by_id: dict[str, Queue] = {}
    if args:
        # can be a mix of queue ordinals or names
        conditions = [
            or_(
                Queue.ordinal.in_(
                    [arg for arg in args if all(char in "0123456789" for char in arg)]
                ),
                func.lower(Queue.name).in_([x.casefold() for x in args]),
            )
        ]
    else:
        conditions = []
    queues: List[Queue] = (
        session.query(Queue)
        .join(
            QueuePlayer,
            and_(
                QueuePlayer.player_id == player_id,
                QueuePlayer.queue_id == Queue.id,
            ),
        )
        .filter(*conditions)
        .order_by(Queue.ordinal.asc())
        .all()
    )
    queues_by_queue_waitlist_player: List[Queue] = (
        session.query(Queue)
        .join(
            QueueWaitlistPlayer,
            and_(
                QueueWaitlistPlayer.player_id == player_id,
                QueueWaitlistPlayer.queue_id == Queue.id,
            ),
        )
        .filter(*conditions)
        .order_by(Queue.ordinal.asc())
        .all()
    )
    for queue in queues:
        session.query(QueuePlayer).filter(
            QueuePlayer.queue_id == queue.id, QueuePlayer.player_id == player_id
        ).delete()
        queues_del_from_by_id[queue.id] = queue

    for queue in queues_by_queue_waitlist_player:
        session.query(QueueWaitlistPlayer).filter(
            QueueWaitlistPlayer.queue_id == queue.id,
            QueueWaitlistPlayer.player_id == player_id,
        ).delete()
        queues_del_from_by_id[queue.id] = queue

    return list(queues_del_from_by_id.values())


def get_team_voice_channels(
    session: SQLAlchemySession, in_progress_game: InProgressGame, guild: Guild
):
    team0_vc: VoiceChannel | None = None
    team1_vc: VoiceChannel | None = None
    ipg_channels: list[InProgressGameChannel] | None = (
        session.query(InProgressGameChannel)
        .filter(InProgressGameChannel.in_progress_game_id == in_progress_game.id)
        .all()
    )
    for ipg_channel in ipg_channels or []:
        discord_channel: GuildChannel | None = guild.get_channel(ipg_channel.channel_id)
        if isinstance(discord_channel, VoiceChannel):
            # This is suboptimal and fragile solution but it's good enough for now. We should keep track of each team's VC in the database
            if in_progress_game.team0_name in discord_channel.name:
                team0_vc = discord_channel
            elif in_progress_game.team1_name in discord_channel.name:
                team1_vc = discord_channel
    return team0_vc, team1_vc
