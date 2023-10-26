import heapq
import os
import sys

from bisect import bisect
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from glob import glob
from itertools import combinations
from math import floor
from os import remove
from random import choice, randint, random, shuffle, uniform
from shutil import copyfile
from tempfile import NamedTemporaryFile
from typing import List, Union

import discord
import imgkit
import numpy
from discord import Colour, DMChannel, Embed, GroupChannel, Message, TextChannel
from discord.ext import commands
from discord.ext.commands.context import Context
from discord.guild import Guild
from discord.member import Member
from discord.utils import escape_markdown
from dotenv import load_dotenv
from PIL import Image
from sqlalchemy.exc import IntegrityError
from trueskill import Rating, rate
from discord_bots.checks import is_admin
from discord_bots.config import LEADERBOARD_CHANNEL, SHOW_TRUESKILL
from discord_bots.utils import (
    update_current_map_to_next_map_in_rotation,
    send_message,
    upload_stats_screenshot_imgkit,
    mean,
    pretty_format_team,
    short_uuid,
    win_probability,
)

from .bot import COMMAND_PREFIX, bot
from .config import DISABLE_MAP_ROTATION
from .models import (
    DB_NAME,
    AdminRole,
    CurrentMap,
    CustomCommand,
    FinishedGame,
    FinishedGamePlayer,
    InProgressGame,
    InProgressGameChannel,
    InProgressGamePlayer,
    MapVote,
    Player,
    PlayerDecay,
    PlayerRegionTrueskill,
    Queue,
    QueueNotification,
    QueuePlayer,
    QueueRegion,
    QueueRole,
    QueueWaitlist,
    QueueWaitlistPlayer,
    RotationMap,
    Session,
    SkipMapVote,
    VoteableMap,
    VotePassedWaitlist,
    VotePassedWaitlistPlayer,
)
from .names import generate_be_name, generate_ds_name
from .queues import AddPlayerQueueMessage, add_player_queue
from .twitch import twitch

load_dotenv()

AFK_TIME_MINUTES: int = 45
DEFAULT_RAFFLE_VALUE = 5
try:
    DEFAULT_RAFFLE_VALUE = int(os.getenv("DEFAULT_RAFFLE_VALUE"))
except:
    pass
DEBUG: bool = bool(os.getenv("DEBUG")) or False
DISABLE_PRIVATE_MESSAGES = bool(os.getenv("DISABLE_PRIVATE_MESSAGES"))
MAP_ROTATION_MINUTES: int = 60
# The number of votes needed to succeed a map skip / replacement
MAP_VOTE_THRESHOLD: int = 7
RE_ADD_DELAY: int = 30


def debug_print(*args):
    global DEBUG
    if DEBUG:
        print(args)


def get_even_teams(
    player_ids: list[int], team_size: int, is_rated: bool, queue_region_id: str | None
) -> tuple[list[Player], float]:
    """
    This is the one used when a new game is created. The other methods are for the showgamedebug command
    TODO: Tests
    TODO: Re-use get_n_teams function

    Try to figure out even teams, the first half of the returning list is
    the first team, the second half is the second team.

    :returns: list of players and win probability for the first team
    """
    session = Session()
    players: list[Player] = (
        session.query(Player).filter(Player.id.in_(player_ids)).all()
    )
    shuffle(
        players
    )  # This is important! This ensures captains are randomly distributed!
    if queue_region_id:
        player_region_trueskills = session.query(PlayerRegionTrueskill).filter(
            PlayerRegionTrueskill.player_id.in_(player_ids),
            PlayerRegionTrueskill.queue_region_id == queue_region_id,
        )
        player_region_trueskills = {
            prt.player_id: prt for prt in player_region_trueskills
        }
    else:
        player_region_trueskills = {}
    best_win_prob_so_far: float = 0.0
    best_teams_so_far: list[Player] = []

    all_combinations = list(combinations(players, team_size))
    for team0 in all_combinations:
        team1 = [p for p in players if p not in team0]
        if is_rated:
            team0_ratings = []
            for player in team0:
                if queue_region_id and player.id in player_region_trueskills:
                    player_region_trueskill = player_region_trueskills[player.id]
                    team0_ratings.append(
                        Rating(
                            player_region_trueskill.rated_trueskill_mu,
                            player_region_trueskill.rated_trueskill_sigma,
                        )
                    )
                else:
                    team0_ratings.append(
                        Rating(player.rated_trueskill_mu, player.rated_trueskill_sigma)
                    )
            team1_ratings = []
            for player in team1:
                if queue_region_id and player.id in player_region_trueskills:
                    player_region_trueskill = player_region_trueskills[player.id]
                    team1_ratings.append(
                        Rating(
                            player_region_trueskill.rated_trueskill_mu,
                            player_region_trueskill.rated_trueskill_sigma,
                        )
                    )
                else:
                    team1_ratings.append(
                        Rating(player.rated_trueskill_mu, player.rated_trueskill_sigma)
                    )
        else:
            team0_ratings = []
            for player in team0:
                if queue_region_id and player.id in player_region_trueskills:
                    player_region_trueskill = player_region_trueskills[player.id]
                    team0_ratings.append(
                        Rating(
                            player_region_trueskill.unrated_trueskill_mu,
                            player_region_trueskill.unrated_trueskill_sigma,
                        )
                    )
                else:
                    team0_ratings.append(
                        Rating(
                            player.unrated_trueskill_mu, player.unrated_trueskill_sigma
                        )
                    )
            team1_ratings = []
            for player in team1:
                if queue_region_id and player.id in player_region_trueskills:
                    player_region_trueskill = player_region_trueskills[player.id]
                    team1_ratings.append(
                        Rating(
                            player_region_trueskill.unrated_trueskill_mu,
                            player_region_trueskill.unrated_trueskill_sigma,
                        )
                    )
                else:
                    team1_ratings.append(
                        Rating(
                            player.unrated_trueskill_mu, player.unrated_trueskill_sigma
                        )
                    )
        win_prob = win_probability(team0_ratings, team1_ratings)
        current_team_evenness = abs(0.50 - win_prob)
        best_team_evenness_so_far = abs(0.50 - best_win_prob_so_far)
        if current_team_evenness < best_team_evenness_so_far:
            best_win_prob_so_far = win_prob
            best_teams_so_far = list(team0[:]) + list(team1[:])

    return best_teams_so_far, best_win_prob_so_far


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
        if is_rated:
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
        else:
            team0_ratings = list(
                map(
                    lambda x: Rating(x.unrated_trueskill_mu, x.unrated_trueskill_sigma),
                    team0,
                )
            )
            team1_ratings = list(
                map(
                    lambda x: Rating(x.unrated_trueskill_mu, x.unrated_trueskill_sigma),
                    team1,
                )
            )

        win_prob = win_probability(team0_ratings, team1_ratings)
        current_team_evenness = abs(0.50 - win_prob)
        heapq.heappush(
            teams, (direction * current_team_evenness, list(team0[:]) + list(team1[:]))
        )

    teams_out = []
    for _ in range(n):
        teams_out.append(heapq.heappop(teams))

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
        if is_rated:
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
        else:
            team0_ratings = list(
                map(
                    lambda x: Rating(
                        x.unrated_trueskill_mu_before, x.unrated_trueskill_sigma_before
                    ),
                    team0,
                )
            )
            team1_ratings = list(
                map(
                    lambda x: Rating(
                        x.unrated_trueskill_mu_before, x.unrated_trueskill_sigma_before
                    ),
                    team1,
                )
            )

        win_prob = win_probability(team0_ratings, team1_ratings)
        current_team_evenness = abs(0.50 - win_prob)
        heapq.heappush(
            teams, (direction * current_team_evenness, list(team0[:]) + list(team1[:]))
        )

    teams_out = []
    for _ in range(n):
        teams_out.append(heapq.heappop(teams))

    return teams_out


def get_n_best_finished_game_teams(
    fgps: list[FinishedGamePlayer], team_size: int, is_rated: bool, n: int
) -> list[tuple[list[FinishedGamePlayer], float]]:
    return get_n_finished_game_teams(fgps, team_size, is_rated, n, 1)


def get_n_worst_finished_game_teams(
    fgps: list[FinishedGamePlayer], team_size: int, is_rated: bool, n: int
) -> list[tuple[list[FinishedGamePlayer], float]]:
    return get_n_finished_game_teams(fgps, team_size, is_rated, n, -1)


async def create_game(
    queue_id: str,
    player_ids: list[int],
    channel: TextChannel | DMChannel | GroupChannel,
    guild: Guild,
):
    session = Session()
    queue: Queue = session.query(Queue).filter(Queue.id == queue_id).first()
    if len(player_ids) == 1:
        # Useful for debugging, no real world application
        players = session.query(Player).filter(Player.id == player_ids[0]).all()
        win_prob = 0
    else:
        players, win_prob = get_even_teams(
            player_ids,
            len(player_ids) // 2,
            is_rated=queue.is_rated,
            queue_region_id=queue.queue_region_id,
        )
    if queue.is_rated:
        average_trueskill = mean(list(map(lambda x: x.rated_trueskill_mu, players)))
    else:
        average_trueskill = mean(list(map(lambda x: x.unrated_trueskill_mu, players)))
    current_map: CurrentMap | None = session.query(CurrentMap).first()

    if not current_map:
        raise Exception("No current map!")

    current_map_full_name = current_map.full_name
    current_map_short_name = current_map.short_name

    rolled_random_map = False
    if current_map.is_random:
        # Roll for random map
        voteable_maps: List[VoteableMap] = session.query(VoteableMap).all()
        roll = uniform(0, 1)
        if roll < current_map.random_probability:
            rolled_random_map = True
            random_map = choice(voteable_maps)
            current_map_full_name = random_map.full_name
            current_map_short_name = random_map.short_name

    game = InProgressGame(
        average_trueskill=average_trueskill,
        map_full_name=current_map_full_name,
        map_short_name=current_map_short_name,
        queue_id=queue.id,
        team0_name=generate_be_name(),
        team1_name=generate_ds_name(),
        win_probability=win_prob,
    )
    session.add(game)

    team0_players = players[: len(players) // 2]
    team1_players = players[len(players) // 2 :]

    short_game_id = short_uuid(game.id)
    message_content = f"Game '{queue.name}' ({short_game_id}) has begun!"
    message_embed = f"**Map: {game.map_full_name} ({game.map_short_name})**\n"
    message_embed += pretty_format_team(game.team0_name, win_prob, team0_players)
    message_embed += pretty_format_team(game.team1_name, 1 - win_prob, team1_players)

    for player in team0_players:
        if not DISABLE_PRIVATE_MESSAGES:
            member: Member | None = guild.get_member(player.id)
            if member:
                try:
                    await member.send(
                        content=message_content,
                        embed=Embed(
                            description=f"{message_embed}",
                            colour=Colour.blue(),
                        ),
                    )
                except Exception:
                    pass

        game_player = InProgressGamePlayer(
            in_progress_game_id=game.id,
            player_id=player.id,
            team=0,
        )
        session.add(game_player)

    for player in team1_players:
        if not DISABLE_PRIVATE_MESSAGES:
            member: Member | None = guild.get_member(player.id)
            if member:
                try:
                    await member.send(
                        content=message_content,
                        embed=Embed(
                            description=f"{message_embed}",
                            colour=Colour.blue(),
                        ),
                    )
                except Exception:
                    pass

        game_player = InProgressGamePlayer(
            in_progress_game_id=game.id,
            player_id=player.id,
            team=1,
        )
        session.add(game_player)

    await send_message(
        channel,
        content=message_content,
        embed_description=message_embed,
        colour=Colour.blue(),
    )

    categories = {category.id: category for category in guild.categories}
    tribes_voice_category = categories[TRIBES_VOICE_CATEGORY_CHANNEL_ID]

    be_channel = await guild.create_voice_channel(
        f"{game.team0_name}", category=tribes_voice_category, bitrate=128000
    )
    ds_channel = await guild.create_voice_channel(
        f"{game.team1_name}", category=tribes_voice_category, bitrate=128000
    )
    session.add(
        InProgressGameChannel(in_progress_game_id=game.id, channel_id=be_channel.id)
    )
    session.add(
        InProgressGameChannel(in_progress_game_id=game.id, channel_id=ds_channel.id)
    )

    session.query(QueuePlayer).filter(
        QueuePlayer.player_id.in_(player_ids)  # type: ignore
    ).delete()
    session.query(MapVote).delete()
    session.query(SkipMapVote).delete()
    session.commit()
    if not queue.is_isolated:
        await update_current_map_to_next_map_in_rotation(rolled_random_map)


async def add_player_to_queue(
    queue_id: str,
    player_id: int,
    channel: TextChannel | DMChannel | GroupChannel,
    guild: Guild,
) -> tuple[bool, bool]:
    """
    Helper function to add player to a queue and pop if needed.

    TODO: Remove this function, it's only called in one place so just inline it

    :returns: A tuple of booleans - the first represents whether the player was
    added to the queue, the second represents whether the queue popped as a
    result.
    """
    session = Session()
    queue_roles = session.query(QueueRole).filter(QueueRole.queue_id == queue_id).all()

    # Zero queue roles means no role restrictions
    if len(queue_roles) > 0:
        member = guild.get_member(player_id)
        if not member:
            return False, False
        queue_role_ids = set(map(lambda x: x.role_id, queue_roles))
        player_role_ids = set(map(lambda x: x.id, member.roles))
        has_role = len(queue_role_ids.intersection(player_role_ids)) > 0
        if not has_role:
            return False, False

    if is_in_game(player_id):
        return False, False

    session.add(
        QueuePlayer(
            queue_id=queue_id,
            player_id=player_id,
            channel_id=channel.id,
        )
    )
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        session.close()
        return False, False

    queue: Queue = session.query(Queue).filter(Queue.id == queue_id).first()
    queue_players: list[QueuePlayer] = (
        session.query(QueuePlayer).filter(QueuePlayer.queue_id == queue_id).all()
    )
    if len(queue_players) == queue.size and not queue.is_sweaty:  # Pop!
        player_ids: list[int] = list(map(lambda x: x.player_id, queue_players))
        session.close()
        await create_game(queue.id, player_ids, channel, guild)
        return True, True

    queue_notifications: list[QueueNotification] = (
        session.query(QueueNotification)
        .filter(
            QueueNotification.queue_id == queue_id,
            QueueNotification.size == len(queue_players),
        )
        .all()
    )
    for queue_notification in queue_notifications:
        member: Member | None = guild.get_member(queue_notification.player_id)
        if member:
            try:
                await member.send(
                    embed=Embed(
                        description=f"'{queue.name}' is at {queue_notification.size} players!",
                        colour=Colour.blue(),
                    )
                )
            except Exception:
                pass
        session.delete(queue_notification)
    session.commit()
    session.close()

    return True, False


def mock_teams_str(
    team0_players: list[Player],
    team1_players: list[Player],
    is_rated: bool,
) -> str:
    """
    Helper method to debug print teams if these were the players
    """
    output = ""
    if is_rated:
        team0_rating = [
            Rating(p.rated_trueskill_mu, p.rated_trueskill_sigma) for p in team0_players
        ]
        team1_rating = [
            Rating(p.rated_trueskill_mu, p.rated_trueskill_sigma) for p in team1_players
        ]
    else:
        team0_rating = [
            Rating(p.unrated_trueskill_mu, p.unrated_trueskill_sigma)
            for p in team0_players
        ]
        team1_rating = [
            Rating(p.unrated_trueskill_mu, p.unrated_trueskill_sigma)
            for p in team1_players
        ]

    team0_names = ", ".join(
        sorted([escape_markdown(player.name) for player in team0_players])
    )
    team1_names = ", ".join(
        sorted([escape_markdown(player.name) for player in team1_players])
    )
    team0_win_prob = round(100 * win_probability(team0_rating, team1_rating), 1)
    team1_win_prob = round(100 - team0_win_prob, 1)
    if is_rated:
        team0_mu = round(
            mean([player.rated_trueskill_mu for player in team0_players]), 2
        )
        team1_mu = round(
            mean([player.rated_trueskill_mu for player in team1_players]), 2
        )
        team0_sigma = round(
            mean([player.rated_trueskill_sigma for player in team0_players]),
            2,
        )
        team1_sigma = round(
            mean([player.rated_trueskill_sigma for player in team1_players]),
            2,
        )
    else:
        team0_mu = round(
            mean([player.unrated_trueskill_mu for player in team0_players]), 2
        )
        team1_mu = round(
            mean([player.unrated_trueskill_mu for player in team1_players]), 2
        )
        team0_sigma = round(
            mean([player.unrated_trueskill_sigma for player in team0_players]),
            2,
        )
        team1_sigma = round(
            mean([player.unrated_trueskill_sigma for player in team1_players]),
            2,
        )
    output += f"\n**BE** (**{team0_win_prob}%**, mu: {team0_mu}, sigma: {team0_sigma}): {team0_names}"
    output += f"\n**DS** (**{team1_win_prob}%**, mu: {team1_mu}, sigma: {team1_sigma}): {team1_names}"
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
    session = Session()
    if is_rated:
        team0_rating = [
            Rating(fgp.rated_trueskill_mu_before, fgp.rated_trueskill_sigma_before)
            for fgp in team0_fg_players
        ]
        team1_rating = [
            Rating(fgp.rated_trueskill_mu_before, fgp.rated_trueskill_sigma_before)
            for fgp in team1_fg_players
        ]
    else:
        team0_rating = [
            Rating(fgp.unrated_trueskill_mu_before, fgp.unrated_trueskill_sigma_before)
            for fgp in team0_fg_players
        ]
        team1_rating = [
            Rating(fgp.unrated_trueskill_mu_before, fgp.unrated_trueskill_sigma_before)
            for fgp in team1_fg_players
        ]

    team0_player_ids = set(map(lambda x: x.player_id, team0_fg_players))
    team1_player_ids = set(map(lambda x: x.player_id, team1_fg_players))
    team0_players: list[Player] = session.query(Player).filter(
        Player.id.in_(team0_player_ids)
    )
    team1_players: list[Player] = session.query(Player).filter(
        Player.id.in_(team1_player_ids)
    )
    team0_names = ", ".join(
        sorted([escape_markdown(player.name) for player in team0_players])
    )
    team1_names = ", ".join(
        sorted([escape_markdown(player.name) for player in team1_players])
    )
    team0_win_prob = round(100 * win_probability(team0_rating, team1_rating), 1)
    team1_win_prob = round(100 - team0_win_prob, 1)
    if is_rated:
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
    else:
        team0_mu = round(
            mean([player.unrated_trueskill_mu_before for player in team0_fg_players]), 2
        )
        team1_mu = round(
            mean([player.unrated_trueskill_mu_before for player in team1_fg_players]), 2
        )
        team0_sigma = round(
            mean(
                [player.unrated_trueskill_sigma_before for player in team0_fg_players]
            ),
            2,
        )
        team1_sigma = round(
            mean(
                [player.unrated_trueskill_sigma_before for player in team1_fg_players]
            ),
            2,
        )
    output += f"\n**BE** (**{team0_win_prob}%**, mu: {team0_mu}, sigma: {team0_sigma}): {team0_names}"
    output += f"\n**DS** (**{team1_win_prob}%**, mu: {team1_mu}, sigma: {team1_sigma}): {team1_names}"
    return output


def finished_game_str(finished_game: FinishedGame, debug: bool = False) -> str:
    """
    Helper method to pretty print a finished game
    """
    output = ""
    session = Session()
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

    if finished_game.is_rated:
        average_mu = mean(
            [
                fgp.rated_trueskill_mu_before
                for fgp in team0_fg_players + team1_fg_players
            ]
        )
        average_sigma = mean(
            [
                fgp.rated_trueskill_sigma_before
                for fgp in team0_fg_players + team1_fg_players
            ]
        )
    else:
        average_mu = mean(
            [
                fgp.unrated_trueskill_mu_before
                for fgp in team0_fg_players + team1_fg_players
            ]
        )
        average_sigma = mean(
            [
                fgp.unrated_trueskill_sigma_before
                for fgp in team0_fg_players + team1_fg_players
            ]
        )

    if debug:
        output += f"**{finished_game.queue_name}** - **{finished_game.map_short_name}** ({short_game_id}) (mu: {round(average_mu, 2)}, sigma: {round(average_sigma, 2)})"
    else:
        output += f"**{finished_game.queue_name}** - **{finished_game.map_short_name}** ({short_game_id})"

    team0_player_ids = set(map(lambda x: x.player_id, team0_fg_players))
    team1_player_ids = set(map(lambda x: x.player_id, team1_fg_players))
    team0_fgp_by_id = {fgp.player_id: fgp for fgp in team0_fg_players}
    team1_fgp_by_id = {fgp.player_id: fgp for fgp in team1_fg_players}
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
    team0_win_prob = round(100 * finished_game.win_probability, 1)
    team1_win_prob = round(100 - team0_win_prob, 1)
    if finished_game.is_rated:
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
    else:
        team0_mu = round(
            mean([player.unrated_trueskill_mu_before for player in team0_fg_players]), 2
        )
        team1_mu = round(
            mean([player.unrated_trueskill_mu_before for player in team1_fg_players]), 2
        )
        team0_sigma = round(
            mean(
                [player.unrated_trueskill_sigma_before for player in team0_fg_players]
            ),
            2,
        )
        team1_sigma = round(
            mean(
                [player.unrated_trueskill_sigma_before for player in team1_fg_players]
            ),
            2,
        )
    if debug:
        team0_str = f"{finished_game.team0_name} ({team0_win_prob}%, mu: {team0_mu}, sigma: {team0_sigma}): {team0_names}"
        team1_str = f"{finished_game.team1_name} ({team1_win_prob}%, mu: {team1_mu}, sigma: {team1_sigma}): {team1_names}"
    else:
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
    delta: timedelta = datetime.now(timezone.utc) - finished_game.finished_at.replace(
        tzinfo=timezone.utc
    )
    if delta.days > 0:
        output += f"\n@ {delta.days} days ago\n"
    elif delta.seconds > 3600:
        hours_ago = delta.seconds // 3600
        output += f"\n@ {hours_ago} hours ago\n"
    else:
        minutes_ago = delta.seconds // 60
        output += f"\n@ {minutes_ago} minutes ago\n"
    session.close()
    return output


def in_progress_game_str(in_progress_game: InProgressGame, debug: bool = False) -> str:
    """
    Helper method to pretty print a finished game
    """
    output = ""
    session = Session()
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
    if queue.is_rated:
        team0_tsr = round(
            mean([player.rated_trueskill_mu for player in team0_players]), 1
        )
        team1_tsr = round(
            mean([player.rated_trueskill_mu for player in team1_players]), 1
        )
    else:
        team0_tsr = round(
            mean([player.unrated_trueskill_mu for player in team0_players]), 1
        )
        team1_tsr = round(
            mean([player.unrated_trueskill_mu for player in team1_players]), 1
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
    delta: timedelta = datetime.now(timezone.utc) - in_progress_game.created_at.replace(
        tzinfo=timezone.utc
    )
    if delta.days > 0:
        output += f"\n@ {delta.days} days ago\n"
    elif delta.seconds > 3600:
        hours_ago = delta.seconds // 3600
        output += f"\n@ {hours_ago} hours ago\n"
    else:
        minutes_ago = delta.seconds // 60
        output += f"\n@ {minutes_ago} minutes ago\n"
    session.close()
    return output


TRIBES_VOICE_CATEGORY_CHANNEL_ID: int = int(
    os.getenv("TRIBES_VOICE_CATEGORY_CHANNEL_ID") or ""
)


def is_in_game(player_id: int) -> bool:
    return get_player_game(player_id, Session()) is not None


def get_player_game(player_id: int, session=None) -> InProgressGame | None:
    """
    Find the game a player is currently in

    :session: Pass in a session if you want to do something with the game that
    gets returned
    """
    should_close = False
    if not session:
        should_close = True
        session = Session()
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


# Commands start here


@bot.check
async def is_not_banned(ctx: Context):
    """
    Global check to ensure that banned users can't use commands

    https://discordpy.readthedocs.io/en/stable/ext/commands/commands.html#global-checks
    """
    is_banned = (
        Session()
        .query(Player)
        .filter(Player.id == ctx.message.author.id, Player.is_banned)
        .first()
    )
    return not is_banned


@bot.command()
async def add(ctx: Context, *args):
    """
    Players adds self to queue(s). If no args to all existing queues

    Players can also add to a queue by its index. The index starts at 1.
    """
    message = ctx.message
    if is_in_game(message.author.id):
        await send_message(
            message.channel,
            embed_description=f"{message.author} you are already in a game",
            colour=Colour.red(),
        )
        return

    session = Session()
    most_recent_game: FinishedGame | None = (
        session.query(FinishedGame)
        .join(FinishedGamePlayer)
        .filter(
            FinishedGamePlayer.player_id == message.author.id,
        )
        .order_by(FinishedGame.finished_at.desc())  # type: ignore
        .first()
    )

    queues_to_add: list[Queue] = []
    if len(args) == 0:
        # Don't auto-add to isolated queues
        queues_to_add += session.query(Queue).filter(Queue.is_isolated == False).order_by(Queue.created_at.asc()).all()  # type: ignore
    else:
        all_queues = session.query(Queue).order_by(Queue.created_at.asc()).all()  # type: ignore
        for arg in args:
            # Try adding by integer index first, then try string name
            try:
                queue_index = int(arg) - 1
                queues_to_add.append(all_queues[queue_index])
            except ValueError:
                queue: Queue | None = session.query(Queue).filter(Queue.name.ilike(arg)).first()  # type: ignore
                if queue:
                    queues_to_add.append(queue)
            except IndexError:
                continue

    if len(queues_to_add) == 0:
        await send_message(
            message.channel,
            content="No valid queues found",
            colour=Colour.red(),
        )
        return

    vpw: VotePassedWaitlist | None = session.query(VotePassedWaitlist).first()
    if vpw:
        for queue in queues_to_add:
            session.add(
                VotePassedWaitlistPlayer(
                    vote_passed_waitlist_id=vpw.id,
                    player_id=message.author.id,
                    queue_id=queue.id,
                )
            )
            try:
                session.commit()
            except IntegrityError as exc:
                print("integrity error?", exc)
                session.rollback()

        current_time: datetime = datetime.now(timezone.utc)
        # The assumption is the end timestamp is later than now, otherwise it
        # would have been processed
        difference: float = (
            vpw.end_waitlist_at.replace(tzinfo=timezone.utc) - current_time
        ).total_seconds()
        if difference < RE_ADD_DELAY:
            waitlist_message = f"A vote just passed, you will be randomized into the queue in {floor(difference)} seconds"
            await send_message(
                message.channel,
                # TODO: Populate this message with the queues the player was
                # eligible for
                content=f"{message.author.display_name} added to:",
                embed_description=waitlist_message,
                colour=Colour.green(),
            )
        return

    is_waitlist: bool = False
    waitlist_message: str | None = None
    if most_recent_game and most_recent_game.finished_at:
        # The timezone info seems to get lost in the round trip to the database
        finish_time: datetime = most_recent_game.finished_at.replace(
            tzinfo=timezone.utc
        )
        current_time: datetime = datetime.now(timezone.utc)
        difference: float = (current_time - finish_time).total_seconds()
        if difference < RE_ADD_DELAY:
            time_to_wait: int = floor(RE_ADD_DELAY - difference)
            waitlist_message = f"Your game has just finished, you will be randomized into the queue in {time_to_wait} seconds"
            is_waitlist = True

    if is_waitlist and most_recent_game:
        for queue in queues_to_add:
            # TODO: Check player eligibility here?
            queue_waitlist = (
                session.query(QueueWaitlist)
                .filter(QueueWaitlist.finished_game_id == most_recent_game.id)
                .first()
            )
            if queue_waitlist:
                session.add(
                    QueueWaitlistPlayer(
                        queue_id=queue.id,
                        queue_waitlist_id=queue_waitlist.id,
                        player_id=message.author.id,
                    )
                )
                try:
                    session.commit()
                except IntegrityError:
                    session.rollback()

        await send_message(
            message.channel,
            # TODO: Populate this message with the queues the player was
            # eligible for
            content=f"{escape_markdown(message.author.display_name)} added to:",
            embed_description=waitlist_message,
            colour=Colour.green(),
        )
        return

    if isinstance(message.channel, TextChannel) and message.guild:
        add_player_queue.put(
            AddPlayerQueueMessage(
                message.author.id,
                message.author.display_name,
                [q.id for q in queues_to_add],
                True,
                message.channel,
                message.guild,
            )
        )


@bot.command()
@commands.check(is_admin)
async def addadmin(ctx: Context, member: Member):
    message = ctx.message
    session = Session()
    player: Player | None = session.query(Player).filter(Player.id == member.id).first()
    if not player:
        session.add(
            Player(
                id=member.id,
                name=member.name,
                is_admin=True,
            )
        )
        await send_message(
            message.channel,
            embed_description=f"{escape_markdown(member.name)} added to admins",
            colour=Colour.green(),
        )
        session.commit()
    else:
        if player.is_admin:
            await send_message(
                message.channel,
                embed_description=f"{escape_markdown(player.name)} is already an admin",
                colour=Colour.red(),
            )
        else:
            player.is_admin = True
            session.commit()
            await send_message(
                message.channel,
                embed_description=f"{escape_markdown(player.name)} added to admins",
                colour=Colour.green(),
            )


@bot.command()
@commands.check(is_admin)
async def addadminrole(ctx: Context, role_name: str):
    message = ctx.message
    if message.guild:
        session = Session()
        role_name_to_role_id: dict[str, int] = {
            role.name.lower(): role.id for role in message.guild.roles
        }
        if role_name.lower() not in role_name_to_role_id:
            await send_message(
                message.channel,
                embed_description=f"Could not find role: {role_name}",
                colour=Colour.red(),
            )
            return
        session.add(AdminRole(role_name_to_role_id[role_name.lower()]))
        await send_message(
            message.channel,
            embed_description=f"Added admin role: {role_name}",
            colour=Colour.green(),
        )
        session.commit()


@bot.command()
@commands.check(is_admin)
async def addqueueregion(ctx: Context, region_name: str):
    session = Session()
    exists = (
        session.query(QueueRegion).filter(QueueRegion.name.ilike(region_name)).first()
    )
    if exists:
        session.close()
        await send_message(
            ctx.message.channel,
            embed_description=f"Region already exists: **{region_name}**",
            colour=Colour.red(),
        )
        return

    session.add(QueueRegion(region_name))
    session.commit()
    session.close()
    await send_message(
        ctx.message.channel,
        embed_description=f"Region added: **{region_name}**",
        colour=Colour.green(),
    )


@bot.command()
@commands.check(is_admin)
async def addqueuerole(ctx: Context, queue_name: str, role_name: str):
    message = ctx.message
    session = Session()
    queue = session.query(Queue).filter(Queue.name.ilike(queue_name)).first()  # type: ignore
    if not queue:
        await send_message(
            message.channel,
            embed_description=f"Could not find queue: {queue_name}",
            colour=Colour.red(),
        )
        return
    if message.guild:
        role_name_to_role_id: dict[str, int] = {
            role.name.lower(): role.id for role in message.guild.roles
        }
        if role_name.lower() not in role_name_to_role_id:
            await send_message(
                message.channel,
                embed_description=f"Could not find role: {role_name}",
                colour=Colour.red(),
            )
            return
        session.add(QueueRole(queue.id, role_name_to_role_id[role_name.lower()]))
        await send_message(
            message.channel,
            embed_description=f"Added role {role_name} to queue {queue_name}",
            colour=Colour.green(),
        )
        session.commit()


@bot.command(usage="<map_full_name> <map_short_name> <random_probability>")
@commands.check(is_admin)
async def addrandomrotationmap(
    ctx: Context, map_full_name: str, map_short_name: str, random_probability: float
):
    """
    Adds a special map to the rotation that is random each time it comes up
    """
    message = ctx.message
    if random_probability < 0 or random_probability > 1:
        await send_message(
            message.channel,
            embed_description=f"Random map probability must be between 0 and 1!",
            colour=Colour.red(),
        )
        return

    session = Session()
    session.add(
        RotationMap(
            f"{map_full_name} (R)",
            f"{map_short_name}R",
            is_random=True,
            random_probability=random_probability,
        )
    )
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        await send_message(
            message.channel,
            embed_description=f"Error adding random map {map_full_name} ({map_short_name}) to rotation. Does it already exist?",
            colour=Colour.red(),
        )
        return

    await send_message(
        message.channel,
        embed_description=f"{map_full_name} (R) ({map_short_name}R) added to map rotation",
        colour=Colour.green(),
    )


@bot.command()
@commands.check(is_admin)
async def addrotationmap(ctx: Context, map_full_name: str, map_short_name: str):
    message = ctx.message
    session = Session()
    session.add(RotationMap(map_full_name, map_short_name))
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        await send_message(
            message.channel,
            embed_description=f"Error adding map {map_full_name} ({map_short_name}) to rotation. Does it already exist?",
            colour=Colour.red(),
        )
        return

    await send_message(
        message.channel,
        embed_description=f"{map_full_name} ({map_short_name}) added to map rotation",
        colour=Colour.green(),
    )


@bot.command()
@commands.check(is_admin)
async def addmap(ctx: Context, map_short_name: str, map_full_name: str):
    message = ctx.message
    session = Session()
    session.add(VoteableMap(map_full_name, map_short_name))
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        await send_message(
            message.channel,
            embed_description=f"Error adding map {map_full_name} ({map_short_name}). Does it already exist?",
            colour=Colour.red(),
        )
        return

    await send_message(
        message.channel,
        embed_description=f"{map_full_name} ({map_short_name}) added to map pool",
        colour=Colour.green(),
    )


@bot.command()
@commands.check(is_admin)
async def ban(ctx: Context, member: Member):
    """TODO: remove player from queues"""
    message = ctx.message
    session = Session()
    players = session.query(Player).filter(Player.id == member.id).all()
    if len(players) == 0:
        session.add(
            Player(
                id=member.id,
                name=member.name,
                is_banned=True,
            )
        )
        await send_message(
            message.channel,
            embed_description=f"{escape_markdown(member.name)} banned",
            colour=Colour.green(),
        )
        session.commit()
    else:
        player = players[0]
        if player.is_banned:
            await send_message(
                message.channel,
                embed_description=f"{escape_markdown(player.name)} is already banned",
                colour=Colour.red(),
            )
        else:
            player.is_banned = True
            session.commit()
            await send_message(
                message.channel,
                embed_description=f"{escape_markdown(player.name)} banned",
                colour=Colour.green(),
            )


@bot.command()
async def coinflip(ctx: Context):
    message = ctx.message
    result = "HEADS" if floor(random() * 2) == 0 else "TAILS"
    await send_message(message.channel, embed_description=result, colour=Colour.blue())


@bot.command()
@commands.check(is_admin)
async def cancelgame(ctx: Context, game_id: str):
    message = ctx.message
    session = Session()
    game = (
        session.query(InProgressGame)
        .filter(InProgressGame.id.startswith(game_id))
        .first()
    )
    if not game:
        await send_message(
            message.channel,
            embed_description=f"Could not find game: {game_id}",
            colour=Colour.red(),
        )
        return

    session.query(InProgressGamePlayer).filter(
        InProgressGamePlayer.in_progress_game_id == game.id
    ).delete()
    for channel in session.query(InProgressGameChannel).filter(
        InProgressGameChannel.in_progress_game_id == game.id
    ):
        if message.guild:
            guild_channel = message.guild.get_channel(channel.channel_id)
            if guild_channel:
                await guild_channel.delete()
        session.delete(channel)

    session.delete(game)
    session.commit()
    await send_message(
        message.channel,
        embed_description=f"Game {game_id} cancelled",
        colour=Colour.blue(),
    )


@bot.command()
async def changegamemap(ctx: Context, game_id: str, map_short_name: str):
    message = ctx.message
    """
    TODO: tests
    """
    session = Session()
    ipg: InProgressGame | None = (
        session.query(InProgressGame)
        .filter(InProgressGame.id.startswith(game_id))
        .first()
    )
    if not ipg:
        await send_message(
            message.channel,
            embed_description=f"Could not find game: {game_id}",
            colour=Colour.red(),
        )
        return

    rotation_map: RotationMap | None = (
        session.query(RotationMap).filter(RotationMap.short_name.ilike(map_short_name)).first()  # type: ignore
    )
    if rotation_map:
        ipg.map_full_name = rotation_map.full_name
        ipg.map_short_name = rotation_map.short_name
        session.commit()
    else:
        voteable_map: VoteableMap | None = (
            session.query(VoteableMap)
            .filter(VoteableMap.short_name.ilike(map_short_name))  # type: ignore
            .first()
        )
        if voteable_map:
            ipg.map_full_name = voteable_map.full_name
            ipg.map_short_name = voteable_map.short_name
            session.commit()
        else:
            await send_message(
                message.channel,
                embed_description=f"Could not find map: {map_short_name}. Add to rotation or map pool first.",
                colour=Colour.red(),
            )
            return

    session.commit()
    await send_message(
        message.channel,
        embed_description=f"Map for game {game_id} changed to {map_short_name}",
        colour=Colour.green(),
    )


@bot.command()
@commands.check(is_admin)
async def changequeuemap(ctx: Context, map_short_name: str):
    message = ctx.message
    """
    TODO: tests
    """
    session = Session()
    current_map: CurrentMap = session.query(CurrentMap).first()
    rotation_map: RotationMap | None = (
        session.query(RotationMap).filter(RotationMap.short_name.ilike(map_short_name)).first()  # type: ignore
    )
    if rotation_map:
        rotation_maps: list[RotationMap] = (
            session.query(RotationMap).order_by(RotationMap.created_at.asc()).all()  # type: ignore
        )
        rotation_map_index = rotation_maps.index(rotation_map)
        current_map.full_name = rotation_map.full_name
        current_map.short_name = rotation_map.short_name
        current_map.map_rotation_index = rotation_map_index
        current_map.updated_at = datetime.now(timezone.utc)
        session.commit()
    else:
        voteable_map: VoteableMap | None = (
            session.query(VoteableMap)
            .filter(VoteableMap.short_name.ilike(map_short_name))  # type: ignore
            .first()
        )
        if voteable_map:
            current_map.full_name = voteable_map.full_name
            current_map.short_name = voteable_map.short_name
            current_map.updated_at = datetime.now(timezone.utc)
            session.commit()
        else:
            await send_message(
                message.channel,
                embed_description=f"Could not find map: {map_short_name}. Add to rotation or map pool first.",
                colour=Colour.red(),
            )
            return
    session.commit()
    await send_message(
        message.channel,
        embed_description=f"Queue map changed to {map_short_name}",
        colour=Colour.green(),
    )


@bot.command()
@commands.check(is_admin)
async def createcommand(ctx: Context, name: str, *, output: str):
    message = ctx.message
    session = Session()
    exists = session.query(CustomCommand).filter(CustomCommand.name == name).first()
    if exists is not None:
        await send_message(
            message.channel,
            embed_description="A command with that name already exists",
            colour=Colour.red(),
        )
        return

    session.add(CustomCommand(name, output))
    session.commit()

    await send_message(
        message.channel,
        embed_description=f"Command `{name}` added",
        colour=Colour.green(),
    )


@bot.command()
@commands.check(is_admin)
async def createdbbackup(ctx: Context):
    message = ctx.message
    date_string = datetime.now().strftime("%Y-%m-%d")
    copyfile(f"{DB_NAME}.db", f"{DB_NAME}_{date_string}.db")
    await send_message(
        message.channel,
        embed_description=f"Backup made to {DB_NAME}_{date_string}.db",
        colour=Colour.green(),
    )


@bot.command()
@commands.check(is_admin)
async def restart(ctx):
    await ctx.send("Restarting bot... ")
    os.execv(sys.executable, ["python", "-m", "discord_bots.main"])


@bot.command()
@commands.check(is_admin)
async def clearqueue(ctx: Context, queue_name: str):
    message = ctx.message
    session = Session()
    queue = session.query(Queue).filter(Queue.name.ilike(queue_name)).first()  # type: ignore
    if not queue:
        await send_message(
            message.channel,
            embed_description=f"Could not find queue: {queue_name}",
            colour=Colour.red(),
        )
        return
    session.query(QueuePlayer).filter(QueuePlayer.queue_id == queue.id).delete()
    session.commit()

    await send_message(
        message.channel,
        embed_description=f"Queue cleared: {queue_name}",
        colour=Colour.green(),
    )


@bot.command()
@commands.check(is_admin)
async def createqueue(ctx: Context, queue_name: str, queue_size: int):
    message = ctx.message
    queue = Queue(name=queue_name, size=queue_size)
    session = Session()

    try:
        session.add(queue)
        session.commit()
        await send_message(
            message.channel,
            embed_description=f"Queue created: {queue.name}",
            colour=Colour.green(),
        )
    except IntegrityError:
        session.rollback()
        await send_message(
            message.channel,
            embed_description="A queue already exists with that name",
            colour=Colour.red(),
        )


@bot.command()
@commands.check(is_admin)
async def decayplayer(ctx: Context, member: Member, decay_amount_percent: str):
    message = ctx.message
    """
    Manually adjust a player's trueskill rating downward by a percentage
    """
    if not decay_amount_percent.endswith("%"):
        await send_message(
            message.channel,
            embed_description="Decay amount must end with %",
            colour=Colour.red(),
        )
        return

    decay_amount = int(decay_amount_percent[:-1])
    if decay_amount < 1 or decay_amount > 100:
        await send_message(
            message.channel,
            embed_description="Decay amount must be between 1-100",
            colour=Colour.red(),
        )
        return

    session = Session()
    player: Player = (
        session.query(Player).filter(Player.id == message.mentions[0].id).first()
    )
    rated_trueskill_mu_before = player.rated_trueskill_mu
    rated_trueskill_mu_after = player.rated_trueskill_mu * (100 - decay_amount) / 100
    unrated_trueskill_mu_before = player.unrated_trueskill_mu
    unrated_trueskill_mu_after = (
        player.unrated_trueskill_mu * (100 - decay_amount) / 100
    )
    player.rated_trueskill_mu = rated_trueskill_mu_after
    player.unrated_trueskill_mu = unrated_trueskill_mu_after
    await send_message(
        message.channel,
        embed_description=f"{escape_markdown(member.name)} decayed by {decay_amount}%",
        colour=Colour.green(),
    )
    session.add(
        PlayerDecay(
            player.id,
            decay_amount,
            rated_trueskill_mu_before=rated_trueskill_mu_before,
            rated_trueskill_mu_after=rated_trueskill_mu_after,
            unrated_trueskill_mu_before=unrated_trueskill_mu_before,
            unrated_trueskill_mu_after=unrated_trueskill_mu_after,
        )
    )
    session.commit()


@bot.command(name="del")
async def del_(ctx: Context, *args):
    """
    Players deletes self from queue(s)

    If no args deletes from existing queues
    """
    message = ctx.message
    session = Session()
    queues_to_del: list[Queue] = []
    all_queues: list(Queue) = session.query(Queue).join(QueuePlayer).filter(QueuePlayer.player_id == message.author.id).order_by(Queue.created_at.asc()).all()  # type: ignore
    if len(args) == 0:
        queues_to_del = all_queues
    else:
        for arg in args:
            # Try remove by integer index first, then try string name
            try:
                queue_index = int(arg) - 1
                queues_to_del.append(all_queues[queue_index])
            except ValueError:
                queue: Queue | None = session.query(Queue).filter(Queue.name.ilike(arg)).first()  # type: ignore
                if queue:
                    queues_to_del.append(queue)
            except IndexError:
                continue

    for queue in queues_to_del:
        session.query(QueuePlayer).filter(
            QueuePlayer.queue_id == queue.id, QueuePlayer.player_id == message.author.id
        ).delete()
        # TODO: Test this part
        queue_waitlist: QueueWaitlist | None = (
            session.query(QueueWaitlist)
            .filter(
                QueueWaitlist.queue_id == queue.id,
            )
            .first()
        )
        if queue_waitlist:
            session.query(QueueWaitlistPlayer).filter(
                QueueWaitlistPlayer.player_id == message.author.id,
                QueueWaitlistPlayer.queue_waitlist_id == queue_waitlist.id,
            ).delete()

    queue_statuses = []
    queue: Queue
    for queue in session.query(Queue).order_by(Queue.created_at.asc()).all():  # type: ignore
        queue_players = (
            session.query(QueuePlayer).filter(QueuePlayer.queue_id == queue.id).all()
        )
        queue_statuses.append(f"{queue.name} [{len(queue_players)}/{queue.size}]")

    await send_message(
        message.channel,
        content=f"{escape_markdown(message.author.display_name)} removed from: {', '.join([queue.name for queue in queues_to_del])}",
        embed_description=" ".join(queue_statuses),
        colour=Colour.green(),
    )
    session.commit()


@bot.command(usage="<player>")
@commands.check(is_admin)
async def delplayer(ctx: Context, member: Member, *args):
    """
    Admin command to delete player from all queues
    """
    message = ctx.message
    session = Session()
    queues: list(Queue) = session.query(Queue).join(QueuePlayer).filter(QueuePlayer.player_id == member.id).order_by(Queue.created_at.asc()).all()  # type: ignore
    for queue in queues:
        session.query(QueuePlayer).filter(
            QueuePlayer.queue_id == queue.id, QueuePlayer.player_id == member.id
        ).delete()
        # TODO: Test this part
        queue_waitlist: QueueWaitlist | None = (
            session.query(QueueWaitlist)
            .filter(
                QueueWaitlist.queue_id == queue.id,
            )
            .first()
        )
        if queue_waitlist:
            session.query(QueueWaitlistPlayer).filter(
                QueueWaitlistPlayer.player_id == member.id,
                QueueWaitlistPlayer.queue_waitlist_id == queue_waitlist.id,
            ).delete()

    queue_statuses = []
    queue: Queue
    for queue in session.query(Queue).order_by(Queue.created_at.asc()).all():  # type: ignore
        queue_players = (
            session.query(QueuePlayer).filter(QueuePlayer.queue_id == queue.id).all()
        )
        queue_statuses.append(f"{queue.name} [{len(queue_players)}/{queue.size}]")

    await send_message(
        message.channel,
        content=f"{escape_markdown(member.name)} removed from: {', '.join([queue.name for queue in queues])}",
        embed_description=" ".join(queue_statuses),
        colour=Colour.green(),
    )
    session.commit()


@bot.command()
async def disableleaderboard(ctx: Context):
    session = Session()
    player = (
        session.query(Player)
        .filter(Player.player_id == ctx.message.author.id)
        .first()
    )
    player.enable_leaderboard = False
    session.commit()
    await send_message(
        ctx.message.channel,
        embed_description="You are no longer visible on the leaderboard",
        colour=Colour.blue(),
    )


@bot.command(usage="<command_name> <output>")
async def editcommand(ctx: Context, name: str, *, output: str):
    message = ctx.message
    session = Session()
    exists = session.query(CustomCommand).filter(CustomCommand.name == name).first()
    if exists is None:
        await send_message(
            message.channel,
            embed_description="Could not find a command with that name",
            colour=Colour.red(),
        )
        return

    exists.output = output
    session.commit()

    await send_message(
        message.channel,
        embed_description=f"Command `{name}` updated",
        colour=Colour.green(),
    )


@bot.command(usage="<game_id> <tie|be|ds>")
@commands.check(is_admin)
async def editgamewinner(ctx: Context, game_id: str, outcome: str):
    message = ctx.message
    session = Session()
    game: FinishedGame | None = (
        session.query(FinishedGame)
        .filter(FinishedGame.game_id.startswith(game_id))
        .first()
    )
    if not game:
        await send_message(
            message.channel,
            embed_description=f"Could not find game: {game_id}",
            colour=Colour.red(),
        )
        return
    outcome = outcome.lower()
    if outcome == "tie":
        game.winning_team = -1
    elif outcome == "be":
        game.winning_team = 0
    elif outcome == "ds":
        game.winning_team = 1
    else:
        await send_message(
            message.channel,
            embed_description="Outcome must be tie, be, or ds",
            colour=Colour.red(),
        )
        return

    session.add(game)
    session.commit()
    await send_message(
        message.channel,
        embed_description=f"Game {game_id} outcome changed:\n\n"
        + finished_game_str(game),
        colour=Colour.green(),
    )


@bot.command()
async def enableleaderboard(ctx: Context):
    session = Session()
    player = (
        session.query(Player)
        .filter(Player.player_id == ctx.message.author.id)
        .first()
    )
    player.enable_leaderboard = True
    session.commit()
    await send_message(
        ctx.message.channel,
        embed_description="You are visible on the leaderboard",
        colour=Colour.blue(),
    )



@bot.command(usage="<win|loss|tie>")
async def finishgame(ctx: Context, outcome: str):
    message = ctx.message
    session = Session()
    game_player = (
        session.query(InProgressGamePlayer)
        .filter(InProgressGamePlayer.player_id == message.author.id)
        .first()
    )
    if not game_player:
        await send_message(
            message.channel,
            embed_description="You must be in a game to use that command",
            colour=Colour.red(),
        )
        return

    in_progress_game: InProgressGame = (
        session.query(InProgressGame)
        .filter(InProgressGame.id == game_player.in_progress_game_id)
        .first()
    )
    queue: Queue = (
        session.query(Queue).filter(Queue.id == in_progress_game.queue_id).first()
    )
    winning_team = -1
    if outcome.lower() == "win":
        winning_team = game_player.team
    elif outcome.lower() == "loss":
        winning_team = (game_player.team + 1) % 2
    elif outcome.lower() == "tie":
        winning_team = -1
    else:
        await send_message(
            message.channel,
            embed_description="Usage: !finishgame <win|loss|tie>",
            colour=Colour.red(),
        )
        return

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
    player_region_trueskills_by_id: dict[str, PlayerRegionTrueskill] = {}
    if queue.queue_region_id:
        player_region_trueskills: list[PlayerRegionTrueskill] = (
            session.query(PlayerRegionTrueskill)
            .filter(
                PlayerRegionTrueskill.player_id.in_(player_ids),
                PlayerRegionTrueskill.queue_region_id == queue.queue_region_id,
            )
            .all()
        )
        player_region_trueskills_by_id = {
            prt.player_id: prt for prt in player_region_trueskills
        }
    in_progress_game_players = (
        session.query(InProgressGamePlayer)
        .filter(InProgressGamePlayer.in_progress_game_id == in_progress_game.id)
        .all()
    )
    team0_rated_ratings_before = []
    team1_rated_ratings_before = []
    team0_unrated_ratings_before = []
    team1_unrated_ratings_before = []
    team0_players: list[InProgressGamePlayer] = []
    team1_players: list[InProgressGamePlayer] = []
    for in_progress_game_player in in_progress_game_players:
        player = players_by_id[in_progress_game_player.player_id]
        if in_progress_game_player.team == 0:
            team0_players.append(in_progress_game_player)
            if queue.queue_region_id and player.id in player_region_trueskills_by_id:
                prt = player_region_trueskills_by_id[player.id]
                team0_rated_ratings_before.append(
                    Rating(prt.rated_trueskill_mu, prt.rated_trueskill_sigma)
                )
                team0_unrated_ratings_before.append(
                    Rating(prt.unrated_trueskill_mu, prt.unrated_trueskill_sigma)
                )
            else:
                team0_rated_ratings_before.append(
                    Rating(player.rated_trueskill_mu, player.rated_trueskill_sigma)
                )
                team0_unrated_ratings_before.append(
                    Rating(player.unrated_trueskill_mu, player.unrated_trueskill_sigma)
                )
        else:
            team1_players.append(in_progress_game_player)
            if queue.queue_region_id and player.id in player_region_trueskills_by_id:
                prt = player_region_trueskills_by_id[player.id]
                team1_rated_ratings_before.append(
                    Rating(prt.rated_trueskill_mu, prt.rated_trueskill_sigma)
                )
                team1_unrated_ratings_before.append(
                    Rating(prt.unrated_trueskill_mu, prt.unrated_trueskill_sigma)
                )
            else:
                team1_rated_ratings_before.append(
                    Rating(player.rated_trueskill_mu, player.rated_trueskill_sigma)
                )
                team1_unrated_ratings_before.append(
                    Rating(player.unrated_trueskill_mu, player.unrated_trueskill_sigma)
                )

    if queue.queue_region_id:
        queue_region_name = (
            session.query(QueueRegion)
            .filter(QueueRegion.id == queue.queue_region_id)
            .first()
            .name
        )
    else:
        queue_region_name = None

    finished_game = FinishedGame(
        average_trueskill=in_progress_game.average_trueskill,
        finished_at=datetime.now(timezone.utc),
        game_id=in_progress_game.id,
        is_rated=queue.is_rated,
        map_full_name=in_progress_game.map_full_name,
        map_short_name=in_progress_game.map_short_name,
        queue_name=queue.name,
        queue_region_name=queue_region_name,
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
    team0_unrated_ratings_after: list[Rating]
    team1_unrated_ratings_after: list[Rating]
    if queue.is_rated and not queue.is_isolated:
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
    else:
        # Don't modify rated ratings if the queue isn't rated
        team0_rated_ratings_after, team1_rated_ratings_after = (
            team0_rated_ratings_before,
            team1_rated_ratings_before,
        )

    if len(players) > 1 and not queue.is_isolated:
        team0_unrated_ratings_after, team1_unrated_ratings_after = rate(
            [team0_unrated_ratings_before, team1_unrated_ratings_before], result
        )
    else:
        team0_unrated_ratings_after, team1_unrated_ratings_after = (
            team0_unrated_ratings_before,
            team1_unrated_ratings_before,
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
            unrated_trueskill_mu_before=team0_unrated_ratings_before[i].mu,
            unrated_trueskill_sigma_before=team0_unrated_ratings_before[i].sigma,
            unrated_trueskill_mu_after=team0_unrated_ratings_after[i].mu,
            unrated_trueskill_sigma_after=team0_unrated_ratings_after[i].sigma,
        )
        if not queue.queue_region_id:
            player.rated_trueskill_mu = team0_rated_ratings_after[i].mu
            player.rated_trueskill_sigma = team0_rated_ratings_after[i].sigma
            player.unrated_trueskill_mu = team0_unrated_ratings_after[i].mu
            player.unrated_trueskill_sigma = team0_unrated_ratings_after[i].sigma
        else:
            if player.id in player_region_trueskills_by_id:
                prt = player_region_trueskills_by_id[player.id]
                prt.rated_trueskill_mu = team0_rated_ratings_after[i].mu
                prt.rated_trueskill_sigma = team0_rated_ratings_after[i].sigma
                prt.unrated_trueskill_mu = team0_unrated_ratings_after[i].mu
                prt.unrated_trueskill_sigma = team0_unrated_ratings_after[i].sigma
            else:
                session.add(
                    PlayerRegionTrueskill(
                        player_id=player.id,
                        queue_region_id=queue.queue_region_id,
                        rated_trueskill_mu=team0_rated_ratings_after[i].mu,
                        rated_trueskill_sigma=team0_rated_ratings_after[i].sigma,
                        unrated_trueskill_mu=team0_unrated_ratings_after[i].mu,
                        unrated_trueskill_sigma=team0_unrated_ratings_after[i].sigma,
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
            unrated_trueskill_mu_before=team1_unrated_ratings_before[i].mu,
            unrated_trueskill_sigma_before=team1_unrated_ratings_before[i].sigma,
            unrated_trueskill_mu_after=team1_unrated_ratings_after[i].mu,
            unrated_trueskill_sigma_after=team1_unrated_ratings_after[i].sigma,
        )
        if not queue.queue_region_id:
            player.rated_trueskill_mu = team1_rated_ratings_after[i].mu
            player.rated_trueskill_sigma = team1_rated_ratings_after[i].sigma
            player.unrated_trueskill_mu = team1_unrated_ratings_after[i].mu
            player.unrated_trueskill_sigma = team1_unrated_ratings_after[i].sigma
        else:
            if player.id in player_region_trueskills_by_id:
                prt = player_region_trueskills_by_id[player.id]
                prt.rated_trueskill_mu = team1_rated_ratings_after[i].mu
                prt.rated_trueskill_sigma = team1_rated_ratings_after[i].sigma
                prt.unrated_trueskill_mu = team1_unrated_ratings_after[i].mu
                prt.unrated_trueskill_sigma = team1_unrated_ratings_after[i].sigma
            else:
                session.add(
                    PlayerRegionTrueskill(
                        player_id=player.id,
                        queue_region_id=queue.queue_region_id,
                        rated_trueskill_mu=team1_rated_ratings_after[i].mu,
                        rated_trueskill_sigma=team1_rated_ratings_after[i].sigma,
                        unrated_trueskill_mu=team1_unrated_ratings_after[i].mu,
                        unrated_trueskill_sigma=team1_unrated_ratings_after[i].sigma,
                    )
                )
        session.add(finished_game_player)

    session.query(InProgressGamePlayer).filter(
        InProgressGamePlayer.in_progress_game_id == in_progress_game.id
    ).delete()
    session.query(InProgressGame).filter(
        InProgressGame.id == in_progress_game.id
    ).delete()

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

    queue = session.query(Queue).filter(Queue.id == in_progress_game.queue_id).first()
    if message.guild:
        session.add(
            QueueWaitlist(
                channel_id=message.channel.id,
                finished_game_id=finished_game.id,
                guild_id=message.guild.id,
                in_progress_game_id=in_progress_game.id,
                queue_id=queue.id,
                end_waitlist_at=datetime.now(timezone.utc)
                + timedelta(seconds=RE_ADD_DELAY),
            )
        )

    # Reward raffle tickets
    rotation_map: RotationMap | None = (
        session.query(RotationMap)
        .filter(RotationMap.short_name.ilike(in_progress_game.map_short_name))
        .first()
    )
    reward = DEFAULT_RAFFLE_VALUE
    if rotation_map:
        reward = DEFAULT_RAFFLE_VALUE
        if rotation_map.raffle_ticket_reward > 0:
            reward = rotation_map.raffle_ticket_reward
    for player in players:
        player.raffle_tickets += reward
        session.add(player)

    session.commit()
    queue_name = queue.name
    session.close()
    short_in_progress_game_id = in_progress_game.id.split("-")[0]
    await send_message(
        message.channel,
        content=f"Game '{queue_name}' ({short_in_progress_game_id}) finished",
        embed_description=embed_description,
        colour=Colour.green(),
    )
    await upload_stats_screenshot_imgkit(ctx)


# @bot.command()
# @commands.check(is_admin)
# async def imagetest(ctx: Context):
#     await upload_stats_screenshot_selenium(ctx, False)


# @bot.command()
# @commands.check(is_admin)
# async def imagetest2(ctx: Context):
#     await upload_stats_screenshot_imgkit(ctx, False)


@bot.command()
@commands.check(is_admin)
async def isolatequeue(ctx: Context, queue_name: str):
    message = ctx.message
    session = Session()
    queue: Queue = session.query(Queue).filter(Queue.name.ilike(queue_name)).first()  # type: ignore
    if queue:
        queue.is_isolated = True
        await send_message(
            message.channel,
            embed_description=f"Queue {queue_name} is now isolated (unrated, no map rotation, no auto-adds)",
            colour=Colour.blue(),
        )
    else:
        await send_message(
            message.channel,
            embed_description=f"Queue not found: {queue_name}",
            colour=Colour.red(),
        )
    session.commit()


@bot.command()
async def leaderboard(ctx: Context):
    if not SHOW_TRUESKILL:
        await send_message(
            ctx.message.channel,
            embed_description="Command disabled",
            colour=Colour.red(),
        )
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
        top_20_players: list[Player] = (
            session.query(Player)
                .filter(Player.rated_trueskill_sigma != 5.0)
                .filter(Player.leaderboard_enabled = True)
            # .order_by(Player.leaderboard_trueskill.desc())
            # .limit(20)
        )
        players_adjusted = sorted(
            [
                (player.rated_trueskill_mu - 3 * player.rated_trueskill_sigma, player)
                for player in top_20_players
            ],
            reverse=True,
        )[0:15]
        for i, (_, player) in enumerate(players_adjusted, 1):
            output += f"\n{i}. {round(player.leaderboard_trueskill, 1)} - {player.name} _(mu: {round(player.rated_trueskill_mu, 1)}, sigma: {round(player.rated_trueskill_sigma, 1)})_"
    session.close()
    output += "\n(Ranks calculated using the formula: _mu - 3*sigma_)"

    if LEADERBOARD_CHANNEL:
        channel = bot.get_channel(LEADERBOARD_CHANNEL)
        await send_message(
            channel, embed_description=output, colour=Colour.blue()
        )
        await send_message(
            ctx.message.channel, embed_description=f"Check {channel.mention}!", colour=Colour.blue()
        )
    elif ctx.message.guild:
        player_id = ctx.message.author.id
        member_: Member | None = ctx.message.guild.get_member(player_id)
        if member_:
            try:
                await member_.send(
                    embed=Embed(
                        description=f"{output}",
                        colour=Colour.blue(),
                    ),
                )
            except Exception:
                pass


@bot.command()
async def listadmins(ctx: Context):
    message = ctx.message
    output = "Admins:"
    player: Player
    for player in Session().query(Player).filter(Player.is_admin == True).all():
        output += f"\n- {escape_markdown(player.name)}"

    await send_message(message.channel, embed_description=output, colour=Colour.blue())


@bot.command()
async def listadminroles(ctx: Context):
    message = ctx.message
    output = "Admin roles:"
    if not message.guild:
        return

    admin_role_ids = list(map(lambda x: x.role_id, Session().query(AdminRole).all()))
    admin_role_names: list[str] = []

    role_id_to_role_name: dict[int, str] = {
        role.id: role.name for role in message.guild.roles
    }

    for admin_role_id in admin_role_ids:
        if admin_role_id in role_id_to_role_name:
            admin_role_names.append(role_id_to_role_name[admin_role_id])
    output += f"\n{', '.join(admin_role_names)}"

    await send_message(message.channel, embed_description=output, colour=Colour.blue())


@bot.command()
async def listbans(ctx: Context):
    message = ctx.message
    output = "Bans:"
    for player in Session().query(Player).filter(Player.is_banned == True):
        output += f"\n- {escape_markdown(player.name)}"
    await send_message(message.channel, embed_description=output, colour=Colour.blue())


@bot.command()
@commands.check(is_admin)
async def listchannels(ctx: Context):
    for channel in bot.get_all_channels():
        print(channel.id, channel)

    await send_message(
        ctx.message.channel,
        embed_description="Check stdout",
        colour=Colour.blue(),
    )


@bot.command()
@commands.check(is_admin)
async def listdbbackups(ctx: Context):
    message = ctx.message
    output = "Backups:"
    for filename in glob("tribes_*.db"):
        output += f"\n- {filename}"

    await send_message(
        message.channel,
        embed_description=output,
        colour=Colour.blue(),
    )


@bot.command()
async def listmaprotation(ctx: Context):
    message = ctx.message
    output = "Map rotation:"
    rotation_map: RotationMap
    for rotation_map in Session().query(RotationMap).order_by(RotationMap.created_at.asc()):  # type: ignore
        output += f"\n- {rotation_map.full_name} ({rotation_map.short_name})"
    await send_message(message.channel, embed_description=output, colour=Colour.blue())


@bot.command()
async def listnotifications(ctx: Context):
    message = ctx.message
    session = Session()
    queue_notifications: list[QueueNotification] = session.query(
        QueueNotification
    ).filter(QueueNotification.player_id == ctx.author.id)
    output = "Queue notifications:"
    for queue_notification in queue_notifications:
        queue = (
            session.query(Queue).filter(Queue.id == queue_notification.queue_id).first()
        )
        output += f"\n- {queue.name} {queue_notification.size}"
    await send_message(message.channel, embed_description=output, colour=Colour.blue())


@bot.command()
@commands.check(is_admin)
async def listplayerdecays(ctx: Context, member: Member):
    message = ctx.message
    session = Session()
    player = session.query(Player).filter(Player.id == member.id).first()
    player_decays: list[PlayerDecay] = session.query(PlayerDecay).filter(
        PlayerDecay.player_id == player.id
    )
    output = f"Decays for {escape_markdown(player.name)}:"
    for player_decay in player_decays:
        output += f"\n- {player_decay.decayed_at.strftime('%Y-%m-%d')} - Amount: {player_decay.decay_percentage}%"

    await send_message(message.channel, embed_description=output, colour=Colour.blue())


@bot.command()
async def listqueueregions(ctx: Context):
    output = "Regions:"
    session = Session()
    queue_region: QueueRegion
    for queue_region in session.query(QueueRegion).all():
        output += f"\n- {queue_region.name}"
    session.close()
    await send_message(
        ctx.message.channel, embed_description=output, colour=Colour.blue()
    )


@bot.command()
async def listqueueroles(ctx: Context):
    message = ctx.message
    if not message.guild:
        return

    output = "Queues:\n"
    session = Session()
    queue: Queue
    for i, queue in enumerate(session.query(Queue).all()):
        queue_role_names: list[str] = []
        queue_role: QueueRole
        for queue_role in (
            session.query(QueueRole).filter(QueueRole.queue_id == queue.id).all()
        ):
            role = message.guild.get_role(queue_role.role_id)
            if role:
                queue_role_names.append(role.name)
        output += f"**{queue.name}**: {', '.join(queue_role_names)}\n"
    await send_message(message.channel, embed_description=output, colour=Colour.blue())


@bot.command()
async def listmaps(ctx: Context):
    message = ctx.message
    output = "Voteable map pool"
    voteable_map: VoteableMap
    for voteable_map in Session().query(VoteableMap).order_by(VoteableMap.full_name):
        output += f"\n- {voteable_map.full_name} ({voteable_map.short_name})"
    await send_message(message.channel, embed_description=output, colour=Colour.blue())


@bot.command()
@commands.check(is_admin)
async def lockqueue(ctx: Context, queue_name: str):
    message = ctx.message
    session = Session()
    queue: Queue | None = session.query(Queue).filter(Queue.name == queue_name).first()
    if not queue:
        await send_message(
            message.channel,
            embed_description=f"Could not find queue: {queue_name}",
            colour=Colour.red(),
        )
        return

    queue.is_locked = True
    session.commit()

    await send_message(
        message.channel,
        embed_description=f"Queue {queue_name} locked",
        colour=Colour.green(),
    )


@bot.command()
async def lt(ctx: Context):
    query_url = "http://tribesquery.toocrooked.com/hostQuery.php?server=207.148.13.132:28006&port=28006"
    await ctx.message.channel.send(query_url)

    ntf = NamedTemporaryFile(delete=True, suffix=".png")
    imgkit.from_url(query_url, ntf.name)
    image = Image.open(ntf.name)
    cropped = image.crop((0, 0, 450, 650))
    cropped.save(ntf.name)
    await ctx.message.channel.send(file=discord.File(ntf.name))
    ntf.close()


@bot.command()
async def trueskill(ctx: Context):
    description = ""
    description += "**mu (μ)**: The average skill of the gamer"
    description += "\n**sigma (σ)**: The degree of uncertainty in the gamer's skill"
    description += "\n**Reference**: https://www.microsoft.com/en-us/research/project/trueskill-ranking-system"
    description += "\n**Implementation**: https://trueskill.org/"
    embed_thumbnail = "https://www.microsoft.com/en-us/research/uploads/prod/2016/02/trueskill-skilldia.jpg"
    await send_message(
        channel=ctx.message.channel,
        embed_description=description,
        embed_thumbnail=embed_thumbnail,
        embed_title="Trueskill",
        colour=Colour.blue(),
    )


@bot.command(name="map")
async def map_(ctx: Context):
    # TODO: This is duplicated
    session = Session()
    output = ""
    current_map: CurrentMap | None = session.query(CurrentMap).first()
    if current_map:
        rotation_maps: list[RotationMap] = session.query(RotationMap).order_by(RotationMap.created_at.asc()).all()  # type: ignore
        next_rotation_map_index = (current_map.map_rotation_index + 1) % len(
            rotation_maps
        )
        next_map = rotation_maps[next_rotation_map_index]

        time_since_update: timedelta = datetime.now(
            timezone.utc
        ) - current_map.updated_at.replace(tzinfo=timezone.utc)
        time_until_rotation = MAP_ROTATION_MINUTES - (time_since_update.seconds // 60)
        if current_map.map_rotation_index == 0:
            output += f"**Next map: {current_map.full_name} ({current_map.short_name})**\n_Map after next: {next_map.full_name} ({next_map.short_name})_\n"
        else:
            output += f"**Next map: {current_map.full_name} ({current_map.short_name})**\n_Map after next (auto-rotates in {time_until_rotation} minutes): {next_map.full_name} ({next_map.short_name})_\n"
    skip_map_votes: list[SkipMapVote] = session.query(SkipMapVote).all()
    output += (
        f"_Votes to skip (voteskip): [{len(skip_map_votes)}/{MAP_VOTE_THRESHOLD}]_\n"
    )

    # TODO: This is duplicated
    map_votes: list[MapVote] = session.query(MapVote).all()
    voted_map_ids: list[str] = [map_vote.voteable_map_id for map_vote in map_votes]
    voted_maps: list[VoteableMap] = (
        session.query(VoteableMap).filter(VoteableMap.id.in_(voted_map_ids)).all()  # type: ignore
    )
    voted_maps_str = ", ".join(
        [
            f"{voted_map.short_name} [{voted_map_ids.count(voted_map.id)}/{MAP_VOTE_THRESHOLD}]"
            for voted_map in voted_maps
        ]
    )
    output += f"_Votes to change map (votemap): {voted_maps_str}_\n\n"
    session.close()
    await ctx.send(embed=Embed(description=output, colour=Colour.blue()))


@bot.command(usage="<queue_name> <count>")
@commands.check(is_admin)
async def mockqueue(ctx: Context, queue_name: str, count: int):
    message = ctx.message
    """
    Helper test method for adding random players to queues

    This will send PMs to players, create voice channels, etc. so be careful
    """
    if message.author.id not in [115204465589616646, 347125254050676738]:
        await send_message(
            message.channel,
            embed_description="Only special people can use this command",
            colour=Colour.red(),
        )
        return

    session = Session()
    players_from_last_30_days = (
        session.query(Player)
        .join(FinishedGamePlayer, FinishedGamePlayer.player_id == Player.id)
        .join(FinishedGame, FinishedGame.id == FinishedGamePlayer.finished_game_id)
        .filter(
            FinishedGame.finished_at > datetime.now(timezone.utc) - timedelta(days=30),
        )
        .order_by(FinishedGame.finished_at.desc())  # type: ignore
        .all()
    )
    queue = session.query(Queue).filter(Queue.name.ilike(queue_name)).first()  # type: ignore
    # This throws an error if people haven't played in 30 days
    for player in numpy.random.choice(
        players_from_last_30_days, size=int(count), replace=False
    ):
        if isinstance(message.channel, TextChannel) and message.guild:
            add_player_queue.put(
                AddPlayerQueueMessage(
                    player.id,
                    player.name,
                    [queue.id],
                    False,
                    message.channel,
                    message.guild,
                )
            )
            player.last_activity_at = datetime.now(timezone.utc)
            session.add(player)
            session.commit()


@bot.command()
async def notify(ctx: Context, queue_name_or_index: Union[int, str], size: int):
    message = ctx.message
    if size <= 0:
        await send_message(
            message.channel,
            embed_description="size must be greater than 0",
            colour=Colour.red(),
        )
        return

    session = Session()
    if isinstance(queue_name_or_index, str):
        queue: Queue | None = (
            session.query(Queue).filter(Queue.name.ilike(queue_name_or_index)).first()
        )
        if not queue:
            await send_message(
                message.channel,
                embed_description=f"Could not find queue: {queue_name_or_index}",
                colour=Colour.red(),
            )
            return
        session.add(
            QueueNotification(queue_id=queue.id, player_id=ctx.author.id, size=size)
        )
        await send_message(
            message.channel,
            embed_description=f"Notification added for {queue.name} at {size} players.",
            colour=Colour.green(),
        )
    else:
        all_queues: list[Queue] = (
            session.query(Queue).order_by(Queue.created_at.asc()).all()
        )
        if queue_name_or_index < 1 or queue_name_or_index >= len(all_queues):
            await send_message(
                message.channel,
                embed_description=f"Invalid queue index",
                colour=Colour.red(),
            )
            return
        queue = all_queues[queue_name_or_index - 1]
        session.add(
            QueueNotification(queue_id=queue.id, player_id=ctx.author.id, size=size)
        )
        await send_message(
            message.channel,
            embed_description=f"Notification added for {queue.name} at {size} players.",
            colour=Colour.green(),
        )
    session.commit()


@bot.command()
async def gamehistory(ctx: Context, count: int):
    message = ctx.message
    """
    Display recent game history
    """
    if count > 10:
        await send_message(
            message.channel,
            embed_description="Count cannot exceed 10",
            colour=Colour.red(),
        )
        return

    session = Session()
    finished_games: list[FinishedGame] = (
        session.query(FinishedGame).order_by(FinishedGame.finished_at.desc()).limit(count)  # type: ignore
    )

    output = ""
    for i, finished_game in enumerate(finished_games):
        if i > 0:
            output += "\n"
        output += finished_game_str(finished_game)

    await send_message(
        message.channel,
        embed_description=output,
        colour=Colour.blue(),
    )


@bot.command()
async def pug(ctx: Context):
    query_url = "http://tribesquery.toocrooked.com/hostQuery.php?server=216.128.150.208port=28001"
    await ctx.message.channel.send(query_url)
    ntf = NamedTemporaryFile(delete=True, suffix=".png")
    imgkit.from_url(query_url, ntf.name)
    image = Image.open(ntf.name)
    cropped = image.crop((0, 0, 450, 650))
    cropped.save(ntf.name)
    await ctx.message.channel.send(file=discord.File(ntf.name))
    ntf.close()


@bot.command()
async def randommap(ctx: Context):
    session = Session()
    voteable_maps: list[VoteableMap] = session.query(VoteableMap).all()
    voteable_map = choice(voteable_maps)
    await send_message(
        ctx.message.channel,
        embed_description=f"Random map selected: **{voteable_map.full_name} ({voteable_map.short_name})**",
        colour=Colour.blue(),
    )


@bot.command()
@commands.check(is_admin)
async def removeadmin(ctx: Context, member: Member):
    message = ctx.message
    session = Session()
    players = session.query(Player).filter(Player.id == member.id).all()
    if len(players) == 0 or not players[0].is_admin:
        await send_message(
            message.channel,
            embed_description=f"{escape_markdown(member.name)} is not an admin",
            colour=Colour.red(),
        )
        return

    players[0].is_admin = False
    session.commit()
    await send_message(
        message.channel,
        embed_description=f"{escape_markdown(member.name)} removed from admins",
        colour=Colour.green(),
    )


@bot.command()
@commands.check(is_admin)
async def removeadminrole(ctx: Context, role_name: str):
    message = ctx.message
    if message.guild:
        session = Session()
        role_name_to_role_id: dict[str, int] = {
            role.name.lower(): role.id for role in message.guild.roles
        }
        if role_name.lower() not in role_name_to_role_id:
            await send_message(
                message.channel,
                embed_description=f"Could not find role: {role_name}",
                colour=Colour.red(),
            )
            return
        admin_role = (
            session.query(AdminRole)
            .filter(AdminRole.role_id == role_name_to_role_id[role_name.lower()])
            .first()
        )
        if admin_role:
            session.delete(admin_role)
            await send_message(
                message.channel,
                embed_description=f"Removed admin role: {role_name}",
                colour=Colour.green(),
            )
            session.commit()
        else:
            await send_message(
                message.channel,
                embed_description=f"Could not find admin role: {role_name}",
                colour=Colour.red(),
            )
            return


@bot.command()
@commands.check(is_admin)
async def removecommand(ctx: Context, name: str):
    message = ctx.message
    session = Session()
    exists = session.query(CustomCommand).filter(CustomCommand.name == name).first()
    if not exists:
        await send_message(
            message.channel,
            embed_description="Could not find command with that name",
            colour=Colour.red(),
        )
        return

    session.delete(exists)
    session.commit()

    await send_message(
        message.channel,
        embed_description=f"Command `{name}` removed",
        colour=Colour.green(),
    )


@bot.command()
@commands.check(is_admin)
async def removenotifications(ctx: Context):
    message = ctx.message
    session = Session()
    session.query(QueueNotification).filter(
        QueueNotification.player_id == ctx.author.id
    ).delete()
    session.commit()
    await send_message(
        message.channel,
        embed_description=f"All queue notifications removed",
        colour=Colour.green(),
    )


@bot.command()
@commands.check(is_admin)
async def removequeue(ctx: Context, queue_name: str):
    message = ctx.message
    session = Session()

    queue = session.query(Queue).filter(Queue.name.ilike(queue_name)).first()  # type: ignore
    if queue:
        games_in_progress = (
            session.query(InProgressGame)
            .filter(InProgressGame.queue_id == queue.id)
            .all()
        )
        if len(games_in_progress) > 0:
            await send_message(
                message.channel,
                embed_description=f"Cannot remove queue with game in progress: {queue_name}",
                colour=Colour.red(),
            )
            return
        else:
            session.delete(queue)
            session.commit()
            await send_message(
                message.channel,
                embed_description=f"Queue removed: {queue.name}",
                colour=Colour.blue(),
            )
    else:
        await send_message(
            message.channel,
            embed_description=f"Queue not found: {queue.name}",
            colour=Colour.red(),
        )


@bot.command()
@commands.check(is_admin)
async def removedbbackup(ctx: Context, db_filename: str):
    message = ctx.message
    if not db_filename.startswith("tribes") or not db_filename.endswith(".db"):
        await send_message(
            message.channel,
            embed_description="Filename must be of the format tribes_{date}.db",
            colour=Colour.red(),
        )
        return

    remove(db_filename)
    await send_message(
        message.channel,
        embed_description=f"DB backup {db_filename} removed",
        colour=Colour.green(),
    )


@bot.command()
@commands.check(is_admin)
async def removequeueregion(ctx: Context, region_name: str):
    session = Session()
    region = (
        session.query(QueueRegion).filter(QueueRegion.name.ilike(region_name)).first()
    )
    if region:
        session.delete(region)
        session.commit()
        session.close()
        await send_message(
            ctx.message.channel,
            embed_description=f"Region removed: **{region_name}**",
            colour=Colour.green(),
        )
        return

    session.close()
    await send_message(
        ctx.message.channel,
        embed_description=f"Could not find region: **{region_name}**",
        colour=Colour.red(),
    )


@bot.command()
@commands.check(is_admin)
async def removequeuerole(ctx: Context, queue_name: str, role_name: str):
    message = ctx.message
    session = Session()
    queue = session.query(Queue).filter(Queue.name.ilike(args[0])).first()  # type: ignore
    if not queue:
        await send_message(
            message.channel,
            embed_description=f"Could not find queue: {queue_name}",
            colour=Colour.red(),
        )
        return
    if message.guild:
        role_name_to_role_id: dict[str, int] = {
            role.name.lower(): role.id for role in message.guild.roles
        }
        if role_name.lower() not in role_name_to_role_id:
            await send_message(
                message.channel,
                embed_description=f"Could not find role: {role_name}",
                colour=Colour.red(),
            )
            return
        session.query(QueueRole).filter(
            QueueRole.queue_id == queue.id,
            QueueRole.role_id == role_name_to_role_id[role_name.lower()],
        ).delete()
        await send_message(
            message.channel,
            embed_description=f"Removed role {role_name} from queue {queue_name}",
            colour=Colour.green(),
        )
        session.commit()


@bot.command()
@commands.check(is_admin)
async def removerotationmap(ctx: Context, map_short_name: str):
    message = ctx.message
    session = Session()
    rotation_map = (
        session.query(RotationMap).filter(RotationMap.short_name.ilike(map_short_name)).first()  # type: ignore
    )
    if rotation_map:
        session.delete(rotation_map)
        session.commit()
        await send_message(
            message.channel,
            embed_description=f"{map_short_name} removed from map rotation",
            colour=Colour.green(),
        )
    else:
        await send_message(
            message.channel,
            embed_description=f"Could not find rotation map: {map_short_name}",
            colour=Colour.red(),
        )


@bot.command()
@commands.check(is_admin)
async def removemap(ctx: Context, map_short_name: str):
    message = ctx.message
    session = Session()
    voteable_map = (
        session.query(VoteableMap).filter(VoteableMap.short_name.ilike(map_short_name)).first()  # type: ignore
    )
    if voteable_map:
        session.query(MapVote).filter(
            MapVote.voteable_map_id == voteable_map.id
        ).delete()
        session.delete(voteable_map)
        await send_message(
            message.channel,
            embed_description=f"{map_short_name} removed from map pool",
            colour=Colour.green(),
        )
    else:
        await send_message(
            message.channel,
            embed_description=f"Could not find vote for map: {map_short_name}",
            colour=Colour.red(),
        )

    session.commit()


@bot.command()
async def roll(ctx: Context, low_range: int, high_range: int):
    message = ctx.message
    await send_message(
        message.channel,
        embed_description=f"You rolled: {randint(low_range, high_range)}",
        colour=Colour.blue(),
    )


@bot.command()
@commands.check(is_admin)
async def resetplayertrueskill(ctx: Context, member: Member):
    message = ctx.message
    session = Session()
    player: Player = session.query(Player).filter(Player.id == member.id).first()
    default_rating = Rating(12.5)
    player.rated_trueskill_mu = default_rating.mu
    player.rated_trueskill_sigma = default_rating.sigma
    player.unrated_trueskill_mu = default_rating.mu
    player.unrated_trueskill_sigma = default_rating.sigma
    session.commit()
    session.close()
    await send_message(
        message.channel,
        embed_description=f"{escape_markdown(member.name)} trueskill reset.",
        colour=Colour.green(),
    )


@bot.command()
@commands.check(is_admin)
async def setadddelay(ctx: Context, delay_seconds: int):
    message = ctx.message
    global RE_ADD_DELAY
    RE_ADD_DELAY = delay_seconds
    await send_message(
        message.channel,
        embed_description=f"Delay between games set to {RE_ADD_DELAY}",
        colour=Colour.green(),
    )


@bot.command()
@commands.check(is_admin)
async def setbias(ctx: Context, member: Member, amount: float):
    if amount < -100 or amount > 100:
        await send_message(
            ctx.message.channel,
            embed_description=f"Amount must be between -100 and 100",
            colour=Colour.red(),
        )
        return
    await send_message(
        ctx.message.channel,
        embed_description=f"Team bias for {member.name} set to `{amount}%`",
        colour=Colour.green(),
    )


@bot.command()
@commands.check(is_admin)
async def setcaptainbias(ctx: Context, member: Member, amount: float):
    if amount < -100 or amount > 100:
        await send_message(
            ctx.message.channel,
            embed_description=f"Amount must be between -100 and 100",
            colour=Colour.red(),
        )
        return
    await send_message(
        ctx.message.channel,
        embed_description=f"Captain bias for {member.name} set to `{amount}%`",
        colour=Colour.green(),
    )


@bot.command()
@commands.check(is_admin)
async def setcommandprefix(ctx: Context, prefix: str):
    message = ctx.message
    global COMMAND_PREFIX
    COMMAND_PREFIX = prefix
    await send_message(
        message.channel,
        embed_description=f"Command prefix set to {COMMAND_PREFIX}",
        colour=Colour.green(),
    )


@bot.command()
@commands.check(is_admin)
async def setqueuerated(ctx: Context, queue_name: str):
    message = ctx.message
    session = Session()
    queue: Queue = session.query(Queue).filter(Queue.name.ilike(queue_name)).first()  # type: ignore
    if queue:
        queue.is_rated = True
        await send_message(
            message.channel,
            embed_description=f"Queue {queue_name} is now rated",
            colour=Colour.blue(),
        )
    else:
        await send_message(
            message.channel,
            embed_description=f"Queue not found: {queue_name}",
            colour=Colour.red(),
        )
    session.commit()


@bot.command()
@commands.check(is_admin)
async def setqueueregion(ctx: Context, queue_name: str, region_name: str):
    message = ctx.message
    session = Session()
    queue: Queue = session.query(Queue).filter(Queue.name.ilike(queue_name)).first()  # type: ignore
    if not queue:
        session.close()
        await send_message(
            message.channel,
            embed_description=f"Could not find queue: {queue_name}",
            colour=Colour.red(),
        )
        return
    queue_region: QueueRegion = (
        session.query(QueueRegion).filter(QueueRegion.name.ilike(region_name)).first()
    )
    if not queue_region:
        session.close()
        await send_message(
            message.channel,
            embed_description=f"Could not find region: {region_name}",
            colour=Colour.red(),
        )
        return
    queue.queue_region_id = queue_region.id
    session.commit()
    await send_message(
        message.channel,
        embed_description=f"Queue {queue.name} assigned to region {queue_region.name}",
        colour=Colour.blue(),
    )
    session.commit()


@bot.command()
@commands.check(is_admin)
async def setqueueunrated(ctx: Context, queue_name: str):
    message = ctx.message
    session = Session()
    queue: Queue = session.query(Queue).filter(Queue.name.ilike(queue_name)).first()  # type: ignore
    if queue:
        queue.is_rated = False
        await send_message(
            message.channel,
            embed_description=f"Queue {queue_name} is now unrated",
            colour=Colour.blue(),
        )
    else:
        await send_message(
            message.channel,
            embed_description=f"Queue not found: {queue_name}",
            colour=Colour.red(),
        )
    session.commit()


@bot.command()
@commands.check(is_admin)
async def setqueuesweaty(ctx: Context, queue_name: str):
    message = ctx.message
    session = Session()
    queue: Queue = session.query(Queue).filter(Queue.name.ilike(queue_name)).first()  # type: ignore
    if queue:
        queue.is_sweaty = True
        await send_message(
            message.channel,
            embed_description=f"Queue {queue_name} is now sweaty",
            colour=Colour.blue(),
        )
    else:
        await send_message(
            message.channel,
            embed_description=f"Queue not found: {queue_name}",
            colour=Colour.red(),
        )
    session.commit()


@bot.command()
@commands.check(is_admin)
async def setmapvotethreshold(ctx: Context, threshold: int):
    message = ctx.message
    global MAP_VOTE_THRESHOLD
    MAP_VOTE_THRESHOLD = threshold

    await send_message(
        message.channel,
        embed_description=f"Map vote threshold set to {MAP_VOTE_THRESHOLD}",
        colour=Colour.green(),
    )


@bot.command()
@commands.check(is_admin)
async def setsigma(ctx: Context, member: Member, sigma: float):
    if sigma < 1 or sigma > 8.33:
        await send_message(
            ctx.message.channel,
            embed_description=f"Amount must be between 1 and 8.33",
            colour=Colour.red(),
        )
        return

    session = Session()
    player: Player = session.query(Player).filter(Player.id == member.id).first()
    sigma_before = player.rated_trueskill_sigma
    player.rated_trueskill_sigma = sigma
    session.commit()
    session.close()

    await send_message(
        ctx.message.channel,
        embed_description=f"Sigma for **{member.name}** changed from **{round(sigma_before, 4)}** to **{sigma}**",
        colour=Colour.blue(),
    )


@bot.command()
async def showgame(ctx: Context, game_id: str):
    message = ctx.message
    session = Session()
    finished_game = (
        session.query(FinishedGame)
        .filter(FinishedGame.game_id.startswith(game_id))
        .first()
    )
    if not finished_game:
        await send_message(
            message.channel,
            embed_description=f"Could not find game: {game_id}",
            colour=Colour.red(),
        )
        return

    game_str = finished_game_str(finished_game)
    await send_message(
        message.channel,
        embed_description=game_str,
        colour=Colour.blue(),
    )


@bot.command()
async def showgamedebug(ctx: Context, game_id: str):
    player_id = ctx.message.author.id
    if player_id != 115204465589616646:
        await send_message(
            ctx.message.channel,
            embed_description="Ice cream machine under maintenance",
            colour=Colour.red(),
        )
        return

    message = ctx.message
    session = Session()
    finished_game = (
        session.query(FinishedGame)
        .filter(FinishedGame.game_id.startswith(game_id))
        .first()
    )
    if finished_game:
        game_str = finished_game_str(finished_game, debug=True)
        fgps: list[FinishedGamePlayer] = (
            session.query(FinishedGamePlayer)
            .filter(FinishedGamePlayer.finished_game_id == finished_game.id)
            .all()
        )
        player_ids: list[int] = [fgp.player_id for fgp in fgps]
        best_teams = get_n_best_finished_game_teams(
            fgps, (len(fgps) + 1) // 2, finished_game.is_rated, 5
        )
        worst_teams = get_n_worst_finished_game_teams(
            fgps, (len(fgps) + 1) // 2, finished_game.is_rated, 1
        )
        game_str += "\n**Most even team combinations:**"
        for _, best_team in best_teams:
            team0_players = best_team[: len(best_team) // 2]
            team1_players = best_team[len(best_team) // 2 :]
            game_str += f"\n{mock_finished_game_teams_str(team0_players, team1_players, finished_game.is_rated)}"
        game_str += "\n\n**Least even team combination:**"
        for _, worst_team in worst_teams:
            team0_players = worst_team[: len(worst_team) // 2]
            team1_players = worst_team[len(worst_team) // 2 :]
            game_str += f"\n{mock_finished_game_teams_str(team0_players, team1_players, finished_game.is_rated)}"
        # await send_message(
        #     message.channel,
        #     embed_description=game_str,
        #     colour=Colour.blue(),
        # )
        if ctx.message.guild:
            player_id = ctx.message.author.id
            member_: Member | None = ctx.message.guild.get_member(player_id)
            await send_message(
                ctx.message.channel,
                embed_description="Stats sent to DM",
                colour=Colour.blue(),
            )
            if member_:
                try:
                    await member_.send(
                        embed=Embed(
                            description=game_str,
                            colour=Colour.blue(),
                        ),
                    )
                except Exception:
                    pass

    else:
        in_progress_game: InProgressGame | None = (
            session.query(InProgressGame)
            .filter(InProgressGame.id.startswith(game_id))
            .first()
        )
        if not in_progress_game:
            await send_message(
                message.channel,
                embed_description=f"Could not find game: {game_id}",
                colour=Colour.red(),
            )
            return
        else:
            game_str = in_progress_game_str(in_progress_game, debug=True)
            queue: Queue = (
                session.query(Queue)
                .filter(Queue.id == in_progress_game.queue_id)
                .first()
            )
            igps: list[InProgressGamePlayer] = (
                session.query(InProgressGamePlayer)
                .filter(InProgressGamePlayer.in_progress_game_id == in_progress_game.id)
                .all()
            )
            player_ids: list[int] = [igp.player_id for igp in igps]
            players: list[Player] = (
                session.query(Player).filter(Player.id.in_(player_ids)).all()
            )
            best_teams = get_n_best_teams(
                players, (len(players) + 1) // 2, queue.is_rated, 5
            )
            worst_teams = get_n_worst_teams(
                players, (len(players) + 1) // 2, queue.is_rated, 1
            )
            game_str += "\n**Most even team combinations:**"
            for _, best_team in best_teams:
                team0_players = best_team[: len(best_team) // 2]
                team1_players = best_team[len(best_team) // 2 :]
                game_str += (
                    f"\n{mock_teams_str(team0_players, team1_players, queue.is_rated)}"
                )
            game_str += "\n\n**Least even team combination:**"
            for _, worst_team in worst_teams:
                team0_players = worst_team[: len(worst_team) // 2]
                team1_players = worst_team[len(worst_team) // 2 :]
                game_str += (
                    f"\n{mock_teams_str(team0_players, team1_players, queue.is_rated)}"
                )
            if ctx.message.guild:
                player_id = ctx.message.author.id
                member_: Member | None = ctx.message.guild.get_member(player_id)
                await send_message(
                    ctx.message.channel,
                    embed_description="Stats sent to DM",
                    colour=Colour.blue(),
                )
                if member_:
                    try:
                        await member_.send(
                            embed=Embed(
                                description=game_str,
                                colour=Colour.blue(),
                            ),
                        )
                    except Exception:
                        pass

            # await send_message(
            #     message.channel,
            #     embed_description=game_str,
            #     colour=Colour.blue(),
            # )


@bot.command()
@commands.check(is_admin)
async def showsigma(ctx: Context, member: Member):
    """
    Returns the player's base sigma. Doesn't consider regions
    """
    session = Session()
    player: Player = session.query(Player).filter(Player.id == member.id).first()
    output = (
        embed_title
    ) = f"**{member.name}'s** sigma: **{round(player.rated_trueskill_sigma, 4)}**"
    await send_message(
        channel=ctx.message.channel,
        embed_description=output,
        # embed_title=f"{member.name} sigma:",
        colour=Colour.blue(),
    )


@bot.command()
async def status(ctx: Context, *args):
    session = Session()
    queues: list[Queue] = []
    all_queues = session.query(Queue).order_by(Queue.created_at.asc()).all()  # type: ignore
    if len(args) == 0:
        queues: list[Queue] = all_queues
    else:
        for arg in args:
            # Try adding by integer index first, then try string name
            try:
                queue_index = int(arg) - 1
                queues.append(all_queues[queue_index])
            except ValueError:
                queue: Queue | None = session.query(Queue).filter(Queue.name.ilike(arg)).first()  # type: ignore
                if queue:
                    queues.append(queue)
            except IndexError:
                continue

    games_by_queue: dict[str, list[InProgressGame]] = defaultdict(list)
    for game in session.query(InProgressGame):
        if game.queue_id:
            games_by_queue[game.queue_id].append(game)

    output = ""
    # Only show map if they didn't request a specific queue
    if len(args) == 0:
        current_map: CurrentMap | None = session.query(CurrentMap).first()
        if current_map:
            rotation_maps: list[RotationMap] = session.query(RotationMap).order_by(RotationMap.created_at.asc()).all()  # type: ignore
            upcoming_map = rotation_maps[current_map.map_rotation_index]
            next_rotation_map_index = (current_map.map_rotation_index + 1) % len(
                rotation_maps
            )
            next_map = rotation_maps[next_rotation_map_index]

            time_since_update: timedelta = datetime.now(
                timezone.utc
            ) - current_map.updated_at.replace(tzinfo=timezone.utc)
            time_until_rotation = MAP_ROTATION_MINUTES - (
                time_since_update.seconds // 60
            )
            has_raffle_reward = upcoming_map.raffle_ticket_reward > 0
            upcoming_map_str = f"**Next map: {upcoming_map.full_name} ({upcoming_map.short_name})** _({DEFAULT_RAFFLE_VALUE} tickets)_\n"
            if has_raffle_reward:
                upcoming_map_str = f"**Next map: {upcoming_map.full_name} ({upcoming_map.short_name})** _({upcoming_map.raffle_ticket_reward} tickets)_\n"
            if DISABLE_MAP_ROTATION:
                output += f"{upcoming_map_str}\n_Map after next: {next_map.full_name} ({next_map.short_name})_\n"
            else:
                if current_map.map_rotation_index == 0:
                    output += f"{upcoming_map_str}\n_Map after next: {next_map.full_name} ({next_map.short_name})_\n"
                else:
                    output += f"{upcoming_map_str}\n_Map after next (auto-rotates in {time_until_rotation} minutes): {next_map.full_name} ({next_map.short_name})_\n"
        skip_map_votes: list[SkipMapVote] = session.query(SkipMapVote).all()
        output += f"_Votes to skip (voteskip): [{len(skip_map_votes)}/{MAP_VOTE_THRESHOLD}]_\n"

        # TODO: This is duplicated
        map_votes: list[MapVote] = session.query(MapVote).all()
        voted_map_ids: list[str] = [map_vote.voteable_map_id for map_vote in map_votes]
        voted_maps: list[VoteableMap] = (
            session.query(VoteableMap).filter(VoteableMap.id.in_(voted_map_ids)).all()  # type: ignore
        )
        voted_maps_str = ", ".join(
            [
                f"{voted_map.short_name} [{voted_map_ids.count(voted_map.id)}/{MAP_VOTE_THRESHOLD}]"
                for voted_map in voted_maps
            ]
        )
        output += f"_Votes to change map (votemap): {voted_maps_str}_\n\n"

    for i, queue in enumerate(queues):
        if i > 0:
            output += "\n"
        players_in_queue = (
            session.query(Player)
            .join(QueuePlayer)
            .filter(QueuePlayer.queue_id == queue.id)
            .all()
        )
        queue_region: QueueRegion | None = None
        if queue.queue_region_id:
            queue_region = (
                session.query(QueueRegion)
                .filter(QueueRegion.id == queue.queue_region_id)
                .first()
            )
        if queue.is_locked:
            output += (
                f"*{queue.name} (locked)* [{len(players_in_queue)} / {queue.size}]\n"
            )
        else:
            output += f"**{queue.name}** [{len(players_in_queue)} / {queue.size}]\n"

        if len(players_in_queue) > 0:
            output += f"**IN QUEUE:** "
            output += ", ".join(
                sorted([escape_markdown(player.name) for player in players_in_queue])
            )
            output += "\n"

        if queue.id in games_by_queue:
            game: InProgressGame
            for i, game in enumerate(games_by_queue[queue.id]):
                team0_players = (
                    session.query(Player)
                    .join(InProgressGamePlayer)
                    .filter(
                        InProgressGamePlayer.in_progress_game_id == game.id,
                        InProgressGamePlayer.team == 0,
                    )
                    .all()
                )

                team1_players = (
                    session.query(Player)
                    .join(InProgressGamePlayer)
                    .filter(
                        InProgressGamePlayer.in_progress_game_id == game.id,
                        InProgressGamePlayer.team == 1,
                    )
                    .all()
                )

                short_game_id = short_uuid(game.id)
                if i > 0:
                    output += "\n"
                output += f"**Map: {game.map_full_name}** ({short_game_id}):\n"
                output += pretty_format_team(
                    game.team0_name, game.win_probability, team0_players
                )
                output += pretty_format_team(
                    game.team1_name, 1 - game.win_probability, team1_players
                )
                minutes_ago = (
                    datetime.now(timezone.utc)
                    - game.created_at.replace(tzinfo=timezone.utc)
                ).seconds // 60
                output += f"@ {minutes_ago} minutes ago\n"

    if len(output) == 0:
        output = "No queues or games"

    await send_message(
        ctx.message.channel, embed_description=output, colour=Colour.blue()
    )


def win_rate(wins, losses, ties):
    denominator = max(wins + losses + ties, 1)
    return round(100 * (wins + 0.5 * ties) / denominator, 1)


@bot.command()
async def stats(ctx: Context):
    player_id = ctx.message.author.id
    session = Session()
    fgps = (
        session.query(FinishedGamePlayer)
        .filter(FinishedGamePlayer.player_id == player_id)
        .all()
    )
    finished_game_ids = [fgp.finished_game_id for fgp in fgps]
    fgs = (
        session.query(FinishedGame).filter(FinishedGame.id.in_(finished_game_ids)).all()
    )
    fgps_by_finished_game_id: dict[str, FinishedGamePlayer] = {
        fgp.finished_game_id: fgp for fgp in fgps
    }

    player: Player = session.query(Player).filter(Player.id == player_id).first()
    players: list[Player] = session.query(Player).all()

    default_rating = Rating()
    DEFAULT_TRUESKILL_MU = float(os.getenv("DEFAULT_TRUESKILL_MU") or default_rating.mu)

    # Filter players that haven't played a game
    players = list(
        filter(
            lambda x: x.rated_trueskill_mu != default_rating.mu
            and x.rated_trueskill_mu != DEFAULT_TRUESKILL_MU,
            players,
        )
    )
    trueskills = list(
        sorted(
            [
                round(p.rated_trueskill_mu - 3 * p.rated_trueskill_sigma, 2)
                for p in players
            ]
        )
    )
    trueskill_index = bisect(
        trueskills,
        round(player.rated_trueskill_mu - 3 * player.rated_trueskill_sigma, 2),
    )
    trueskill_ratio = (len(trueskills) - trueskill_index) / len(trueskills)
    if trueskill_ratio <= 0.05:
        trueskill_pct = "Top 5%"
    elif trueskill_ratio <= 0.10:
        trueskill_pct = "Top 10%"
    elif trueskill_ratio <= 0.25:
        trueskill_pct = "Top 25%"
    elif trueskill_ratio <= 0.50:
        trueskill_pct = "Top 50%"
    elif trueskill_ratio <= 0.75:
        trueskill_pct = "Top 75%"
    else:
        trueskill_pct = "Top 100%"

    def is_win(finished_game: FinishedGame) -> bool:
        if (
            fgps_by_finished_game_id[finished_game.id].team
            == finished_game.winning_team
        ):
            return True
        return False

    def is_loss(finished_game: FinishedGame) -> bool:
        if (
            fgps_by_finished_game_id[finished_game.id].team
            != finished_game.winning_team
            and finished_game.winning_team != -1
        ):
            return True
        return False

    def is_tie(finished_game: FinishedGame) -> bool:
        return finished_game.winning_team == -1

    wins = list(filter(is_win, fgs))
    losses = list(filter(is_loss, fgs))
    ties = list(filter(is_tie, fgs))
    winrate = win_rate(len(wins), len(losses), len(ties))
    total_games = len(fgs)

    def last_month(finished_game: FinishedGame) -> bool:
        return finished_game.finished_at > datetime.now() - timedelta(days=30)

    def last_three_months(finished_game: FinishedGame) -> bool:
        return finished_game.finished_at > datetime.now() - timedelta(days=90)

    def last_six_months(finished_game: FinishedGame) -> bool:
        return finished_game.finished_at > datetime.now() - timedelta(days=180)

    def last_year(finished_game: FinishedGame) -> bool:
        return finished_game.finished_at > datetime.now() - timedelta(days=365)

    games_last_month = list(filter(last_month, fgs))
    games_last_three_months = list(filter(last_three_months, fgs))
    games_last_six_months = list(filter(last_six_months, fgs))
    games_last_year = list(filter(last_year, fgs))
    wins_last_month = len(list(filter(is_win, games_last_month)))
    losses_last_month = len(list(filter(is_loss, games_last_month)))
    ties_last_month = len(list(filter(is_tie, games_last_month)))
    winrate_last_month = win_rate(wins_last_month, losses_last_month, ties_last_month)
    wins_last_three_months = len(list(filter(is_win, games_last_three_months)))
    losses_last_three_months = len(list(filter(is_loss, games_last_three_months)))
    ties_last_three_months = len(list(filter(is_tie, games_last_three_months)))
    winrate_last_three_months = win_rate(
        wins_last_three_months, losses_last_three_months, ties_last_three_months
    )
    wins_last_six_months = len(list(filter(is_win, games_last_six_months)))
    losses_last_six_months = len(list(filter(is_loss, games_last_six_months)))
    ties_last_six_months = len(list(filter(is_tie, games_last_six_months)))
    winrate_last_six_months = win_rate(
        wins_last_six_months, losses_last_six_months, ties_last_six_months
    )
    wins_last_year = len(list(filter(is_win, games_last_year)))
    losses_last_year = len(list(filter(is_loss, games_last_year)))
    ties_last_year = len(list(filter(is_tie, games_last_year)))
    winrate_last_year = win_rate(wins_last_year, losses_last_year, ties_last_year)

    output = ""
    if SHOW_TRUESKILL:
        player_region_trueskills: list[PlayerRegionTrueskill] = (
            session.query(PlayerRegionTrueskill)
            .filter(PlayerRegionTrueskill.player_id == player_id)
            .all()
        )
        if player_region_trueskills:
            output += f"**Trueskill:**"
            for prt in player_region_trueskills:
                queue_region: QueueRegion = (
                    session.query(QueueRegion)
                    .filter(QueueRegion.id == prt.queue_region_id)
                    .first()
                )
                output += f"\n**{queue_region.name}**: {round(prt.rated_trueskill_mu - 3 * prt.rated_trueskill_sigma, 1)} _(mu: {round(prt.rated_trueskill_mu, 1)}, sigma: {round(prt.rated_trueskill_sigma, 1)})_"

        # This assumes that if a community uses regions then they'll use regions exclusively
        else:
            output += f"**Trueskill:** {trueskill_pct}"
            output += f"\n{round(player.rated_trueskill_mu - 3 * player.rated_trueskill_sigma, 2)} _(mu: {round(player.rated_trueskill_mu, 2)}, sigma: {round(player.rated_trueskill_sigma, 2)})_"
    else:
        output += f"**Trueskill:** {trueskill_pct}"
    output += f"\n\n**Wins / Losses / Ties / Total:**"
    output += f"\n**Lifetime:** {len(wins)} / {len(losses)} / {len(ties)} / {total_games} _({winrate}%)_"
    output += f"\n**Last month:** {wins_last_month} / {losses_last_month} / {ties_last_month} / {len(games_last_month)} _({winrate_last_month}%)_"
    output += f"\n**Last three months:** {wins_last_three_months} / {losses_last_three_months} / {ties_last_three_months} / {len(games_last_three_months)} _({winrate_last_three_months}%)_"
    output += f"\n**Last six months:** {wins_last_six_months} / {losses_last_six_months} / {ties_last_six_months} / {len(games_last_six_months)} _({winrate_last_six_months}%)_"
    output += f"\n**Last year:** {wins_last_year} / {losses_last_year} / {ties_last_year} / {len(games_last_year)} _({winrate_last_year}%)_"

    if ctx.message.guild:
        member_: Member | None = ctx.message.guild.get_member(player.id)
        await send_message(
            ctx.message.channel,
            embed_description="Stats sent to DM",
            colour=Colour.blue(),
        )
        if member_:
            try:
                await member_.send(
                    embed=Embed(
                        description=f"{output}",
                        colour=Colour.blue(),
                    ),
                )
            except Exception:
                pass


TWITCH_GAME_NAME: str | None = os.getenv("TWITCH_GAME_NAME")


@bot.command()
async def streams(ctx: Context):
    if not TWITCH_GAME_NAME:
        await send_message(
            channel=ctx.message.channel,
            embed_description=f"TWITCH_GAME_NAME not set!",
            colour=Colour.red(),
        )
        return

    if not twitch:
        await send_message(
            channel=ctx.message.channel,
            embed_description=f"TWITCH_CLIENT_ID or TWITCH_CLIENT_SECRET not set!",
            colour=Colour.red(),
        )
        return

    games_data = twitch.get_games(names=[TWITCH_GAME_NAME])
    game_id = games_data["data"][0]["id"]
    game_name = games_data["data"][0]["name"]
    game_box_art_url = (
        games_data["data"][0]["box_art_url"]
        .replace("{width}", "40")
        .replace("{height}", "40")
    )

    streams_data = twitch.get_streams(game_id=game_id)
    output = ""
    for stream_data in streams_data["data"]:
        output += f"\n**{stream_data['user_name']}** ([link](https://www.twitch.tv/{stream_data['user_name']})): {stream_data['title']}"

    await send_message(
        channel=ctx.message.channel,
        embed_description=output,
        embed_thumbnail=game_box_art_url,
        embed_title=f"Players streaming {game_name}",
        colour=Colour.blue(),
    )


@bot.command()
async def sub(ctx: Context, member: Member):
    message = ctx.message
    """
    Substitute one player in a game for another
    """
    session = Session()
    caller = message.author
    caller_game = get_player_game(caller.id, session)
    callee = member
    callee_game = get_player_game(callee.id, session)

    if caller_game and callee_game:
        await send_message(
            channel=message.channel,
            embed_description=f"{escape_markdown(caller.name)} and {escape_markdown(callee.name)} are both already in a game",
            colour=Colour.red(),
        )
        return
    elif not caller_game and not callee_game:
        await send_message(
            channel=message.channel,
            embed_description=f"{escape_markdown(caller.name)} and {escape_markdown(callee.name)} are not in a game",
            colour=Colour.red(),
        )
        return

    # The callee may not be recorded in the database
    if not session.query(Player).filter(Player.id == callee.id).first():
        session.add(Player(id=callee.id, name=callee.name))

    if caller_game:
        caller_game_player = (
            session.query(InProgressGamePlayer)
            .filter(
                InProgressGamePlayer.in_progress_game_id == caller_game.id,
                InProgressGamePlayer.player_id == caller.id,
            )
            .first()
        )
        session.add(
            InProgressGamePlayer(
                in_progress_game_id=caller_game.id,
                player_id=callee.id,
                team=caller_game_player.team,
            )
        )
        session.delete(caller_game_player)

        # Remove the person subbed in from queues
        session.query(QueuePlayer).filter(QueuePlayer.player_id == callee.id).delete()
        session.commit()
    elif callee_game:
        callee_game_player = (
            session.query(InProgressGamePlayer)
            .filter(
                InProgressGamePlayer.in_progress_game_id == callee_game.id,
                InProgressGamePlayer.player_id == callee.id,
            )
            .first()
        )
        session.add(
            InProgressGamePlayer(
                in_progress_game_id=callee_game.id,
                player_id=caller.id,
                team=callee_game_player.team,
            )
        )
        session.delete(callee_game_player)

        # Remove the person subbing in from queues
        session.query(QueuePlayer).filter(QueuePlayer.player_id == caller.id).delete()
        session.commit()

    await send_message(
        channel=message.channel,
        embed_description=f"{escape_markdown(callee.name)} has been substituted with {escape_markdown(caller.name)}",
        colour=Colour.green(),
    )

    game: InProgressGame | None = callee_game or caller_game
    if not game:
        return
    queue: Queue = session.query(Queue).filter(Queue.id == game.queue_id).first()

    game_players = (
        session.query(InProgressGamePlayer)
        .filter(InProgressGamePlayer.in_progress_game_id == game.id)
        .all()
    )
    player_ids: list[int] = list(map(lambda x: x.player_id, game_players))
    players, win_prob = get_even_teams(
        player_ids,
        len(player_ids) // 2,
        is_rated=queue.is_rated,
        queue_region_id=queue.queue_region_id,
    )
    for game_player in game_players:
        session.delete(game_player)
    game.win_probability = win_prob
    team0_players = players[: len(players) // 2]
    team1_players = players[len(players) // 2 :]

    short_game_id = short_uuid(game.id)
    channel_message = f"New teams ({short_game_id}):"
    channel_embed = ""
    channel_embed += pretty_format_team(game.team0_name, win_prob, team0_players)
    channel_embed += pretty_format_team(game.team1_name, 1 - win_prob, team1_players)

    for player in team0_players:
        # TODO: This block is duplicated
        if not DISABLE_PRIVATE_MESSAGES:
            if message.guild:
                member_: Member | None = message.guild.get_member(player.id)
                if member_:
                    try:
                        await member_.send(
                            content=channel_message,
                            embed=Embed(
                                description=f"{channel_embed}",
                                colour=Colour.blue(),
                            ),
                        )
                    except Exception:
                        pass

        game_player = InProgressGamePlayer(
            in_progress_game_id=game.id,
            player_id=player.id,
            team=0,
        )
        session.add(game_player)

    for player in team1_players:
        # TODO: This block is duplicated
        if not DISABLE_PRIVATE_MESSAGES:
            if message.guild:
                member_: Member | None = message.guild.get_member(player.id)
                if member_:
                    try:
                        await member_.send(
                            content=channel_message,
                            embed=Embed(
                                description=f"{channel_embed}",
                                colour=Colour.blue(),
                            ),
                        )
                    except Exception:
                        pass

        game_player = InProgressGamePlayer(
            in_progress_game_id=game.id,
            player_id=player.id,
            team=1,
        )
        session.add(game_player)

    await send_message(
        message.channel,
        content=channel_message,
        embed_description=channel_embed,
        colour=Colour.blue(),
    )
    session.commit()


@bot.command()
@commands.check(is_admin)
async def unban(ctx: Context, member: Member):
    message = ctx.message
    session = Session()
    players = session.query(Player).filter(Player.id == member.id).all()
    if len(players) == 0 or not players[0].is_banned:
        await send_message(
            message.channel,
            embed_description=f"{escape_markdown(member.name)} is not banned",
            colour=Colour.red(),
        )
        return

    players[0].is_banned = False
    session.commit()
    await send_message(
        message.channel,
        embed_description=f"{escape_markdown(member.name)} unbanned",
        colour=Colour.green(),
    )


@bot.command()
@commands.check(is_admin)
async def unisolatequeue(ctx: Context, queue_name: str):
    message = ctx.message
    session = Session()
    queue: Queue = session.query(Queue).filter(Queue.name.ilike(queue_name)).first()  # type: ignore
    if queue:
        queue.is_isolated = False
        await send_message(
            message.channel,
            embed_description=f"Queue {queue_name} is now unisolated",
            colour=Colour.blue(),
        )
    else:
        await send_message(
            message.channel,
            embed_description=f"Queue not found: {queue_name}",
            colour=Colour.red(),
        )
    session.commit()


@bot.command()
@commands.check(is_admin)
async def unlockqueue(ctx: Context, queue_name: str):
    message = ctx.message
    session = Session()
    queue: Queue | None = session.query(Queue).filter(Queue.name == queue_name).first()
    if not queue:
        await send_message(
            message.channel,
            embed_description=f"Could not find queue: {queue_name}",
            colour=Colour.red(),
        )
        return

    queue.is_locked = False
    session.commit()

    await send_message(
        message.channel,
        embed_description=f"Queue {queue_name} unlocked",
        colour=Colour.green(),
    )


@bot.command()
@commands.check(is_admin)
async def unsetqueueregion(ctx: Context, queue_name: str):
    message = ctx.message
    session = Session()
    queue: Queue = session.query(Queue).filter(Queue.name.ilike(queue_name)).first()  # type: ignore
    if not queue:
        session.close()
        await send_message(
            message.channel,
            embed_description=f"Could not find queue: {queue_name}",
            colour=Colour.red(),
        )
        return
    queue.queue_region_id = None
    session.commit()
    await send_message(
        message.channel,
        embed_description=f"Region removed from queue {queue.name}",
        colour=Colour.blue(),
    )
    session.commit()


@bot.command()
@commands.check(is_admin)
async def unsetqueuesweaty(ctx: Context, queue_name: str):
    message = ctx.message
    session = Session()
    queue: Queue = session.query(Queue).filter(Queue.name.ilike(queue_name)).first()  # type: ignore
    if queue:
        queue.is_sweaty = False
        await send_message(
            message.channel,
            embed_description=f"Queue {queue_name} is no longer sweaty",
            colour=Colour.blue(),
        )
    else:
        await send_message(
            message.channel,
            embed_description=f"Queue not found: {queue_name}",
            colour=Colour.red(),
        )
    session.commit()


@bot.command()
async def unvote(ctx: Context):
    message = ctx.message
    """
    Remove all of a player's votes
    """
    session = Session()
    session.query(MapVote).filter(MapVote.player_id == message.author.id).delete()
    session.query(SkipMapVote).filter(
        SkipMapVote.player_id == message.author.id
    ).delete()
    session.commit()
    await send_message(
        message.channel,
        embed_description="All map votes deleted",
        colour=Colour.green(),
    )


# TODO: Unvote for many maps at once
@bot.command()
async def unvotemap(ctx: Context, map_short_name: str):
    message = ctx.message
    session = Session()
    voteable_map: VoteableMap | None = session.query(VoteableMap).filter(VoteableMap.short_name.ilike(args[0])).first()  # type: ignore
    if not voteable_map:
        await send_message(
            message.channel,
            embed_description=f"Could not find voteable map: {map_short_name}",
            colour=Colour.red(),
        )
        return
    map_vote: MapVote | None = (
        session.query(MapVote)
        .filter(
            MapVote.player_id == message.author.id,
            MapVote.voteable_map_id == voteable_map.id,
        )
        .first()
    )
    if not map_vote:
        await send_message(
            message.channel,
            embed_description=f"You don't have a vote for: {map_short_name}",
            colour=Colour.red(),
        )
        return
    else:
        session.delete(map_vote)
    session.commit()
    await send_message(
        message.channel,
        embed_description=f"Your vote for {map_short_name} was removed",
        colour=Colour.green(),
    )


@bot.command()
async def unvoteskip(ctx: Context):
    message = ctx.message
    """
    A player votes to go to the next map in rotation
    """
    session = Session()
    skip_map_vote: SkipMapVote | None = (
        session.query(SkipMapVote)
        .filter(SkipMapVote.player_id == message.author.id)
        .first()
    )
    if skip_map_vote:
        session.delete(skip_map_vote)
        session.commit()
        await send_message(
            message.channel,
            embed_description="Your vote to skip the current map was removed.",
            colour=Colour.green(),
        )
    else:
        await send_message(
            message.channel,
            embed_description="You don't have a vote to skip the current map.",
            colour=Colour.green(),
        )


def get_voteable_maps_str():
    voteable_maps: list[VoteableMap] = Session().query(VoteableMap).all()
    return ", ".join([voteable_map.short_name for voteable_map in voteable_maps])


# TODO: Vote for many maps at once
@bot.command(usage=f"<map_short_name>\nMaps:{get_voteable_maps_str()}")
async def votemap(ctx: Context, map_short_name: str):
    message = ctx.message
    session = Session()
    voteable_map: VoteableMap | None = session.query(VoteableMap).filter(VoteableMap.short_name.ilike(map_short_name)).first()  # type: ignore
    if not voteable_map:
        await send_message(
            message.channel,
            embed_description=f"Could not find voteable map: {map_short_name}\nMaps: {get_voteable_maps_str()}",
            colour=Colour.red(),
        )
        return

    session.add(
        MapVote(message.channel.id, message.author.id, voteable_map_id=voteable_map.id)
    )
    try:
        session.commit()
    except IntegrityError:
        session.rollback()

    map_votes: list[MapVote] = (
        Session()
        .query(MapVote)
        .filter(MapVote.voteable_map_id == voteable_map.id)
        .all()
    )
    if len(map_votes) == MAP_VOTE_THRESHOLD:
        current_map: CurrentMap | None = session.query(CurrentMap).first()
        if current_map:
            current_map.full_name = voteable_map.full_name
            current_map.short_name = voteable_map.short_name
            current_map.updated_at = datetime.now(timezone.utc)
        else:
            session.add(
                CurrentMap(
                    full_name=voteable_map.full_name,
                    map_rotation_index=0,
                    short_name=voteable_map.short_name,
                )
            )
            try:
                session.commit()
            except IntegrityError:
                session.rollback()

        await send_message(
            message.channel,
            embed_description=f"Vote for {voteable_map.full_name} ({voteable_map.short_name}) passed!\n**New map: {voteable_map.full_name} ({voteable_map.short_name})**",
            colour=Colour.green(),
        )
        session.query(MapVote).delete()
        session.query(SkipMapVote).delete()
        if message.guild:
            # TODO: Check if another vote already exists
            session.add(
                VotePassedWaitlist(
                    channel_id=message.channel.id,
                    guild_id=message.guild.id,
                    end_waitlist_at=datetime.now(timezone.utc)
                    + timedelta(seconds=RE_ADD_DELAY),
                )
            )
        session.commit()
    else:
        map_votes: list[MapVote] = session.query(MapVote).all()
        voted_map_ids: list[str] = [map_vote.voteable_map_id for map_vote in map_votes]
        voted_maps: list[VoteableMap] = (
            session.query(VoteableMap).filter(VoteableMap.id.in_(voted_map_ids)).all()  # type: ignore
        )
        voted_maps_str = ", ".join(
            [
                f"{voted_map.short_name} [{voted_map_ids.count(voted_map.id)}/{MAP_VOTE_THRESHOLD}]"
                for voted_map in voted_maps
            ]
        )
        await send_message(
            message.channel,
            embed_description=f"Added map vote for {map_short_name}.\n!unvotemap to remove your vote.\nVotes: {voted_maps_str}",
            colour=Colour.green(),
        )

    session.commit()


@bot.command()
async def voteskip(ctx: Context):
    message = ctx.message
    """
    A player votes to go to the next map in rotation
    """
    session = Session()
    session.add(SkipMapVote(message.channel.id, message.author.id))
    try:
        session.commit()
    except IntegrityError:
        session.rollback()

    skip_map_votes: list[SkipMapVote] = Session().query(SkipMapVote).all()
    if len(skip_map_votes) >= MAP_VOTE_THRESHOLD:
        await update_current_map_to_next_map_in_rotation(False)
        current_map: CurrentMap = Session().query(CurrentMap).first()
        await send_message(
            message.channel,
            embed_description=f"Vote to skip the current map passed!\n**New map: {current_map.full_name} ({current_map.short_name})**",
            colour=Colour.green(),
        )

        session.query(MapVote).delete()
        session.query(SkipMapVote).delete()
        if message.guild:
            # TODO: Might be bugs if two votes pass one after the other
            vpw: VotePassedWaitlist | None = session.query(VotePassedWaitlist).first()
            if not vpw:
                session.add(
                    VotePassedWaitlist(
                        channel_id=message.channel.id,
                        guild_id=message.guild.id,
                        end_waitlist_at=datetime.now(timezone.utc)
                        + timedelta(seconds=RE_ADD_DELAY),
                    )
                )
        session.commit()
    else:
        skip_map_votes: list[SkipMapVote] = session.query(SkipMapVote).all()
        await send_message(
            message.channel,
            embed_description=f"Added vote to skip the current map.\n!unvoteskip to remove vote.\nVotes to skip: [{len(skip_map_votes)}/{MAP_VOTE_THRESHOLD}]",
            colour=Colour.green(),
        )
