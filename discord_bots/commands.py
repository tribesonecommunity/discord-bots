import heapq
import logging  # TODO: need to change to module logging, since doing this will always display "root.INFO,WARN,..."
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
from typing import List, Literal, Optional, Union

import discord
import imgkit
from discord import (
    CategoryChannel,
    Colour,
    DMChannel,
    Embed,
    GroupChannel,
    Interaction,
    Message,
    TextChannel,
    VoiceChannel,
)
from discord.ext import commands
from discord.ext.commands.context import Context
from discord.guild import Guild
from discord.member import Member
from discord.utils import escape_markdown
from numpy import std
from PIL import Image
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.session import Session as SQLAlchemySession
from sqlalchemy.sql import select
from table2ascii import PresetStyle, table2ascii
from trueskill import Rating, rate

import discord_bots.config as config
from discord_bots.checks import is_admin
from discord_bots.utils import (
    MU_LOWER_UNICODE,
    SIGMA_LOWER_UNICODE,
    cancel_in_progress_game,
    code_block,
    finish_in_progress_game,
    mean,
    pretty_format_team,
    pretty_format_team_no_format,
    print_leaderboard,
    send_in_guild_message,
    send_message,
    short_uuid,
    update_next_map_to_map_after_next,
    upload_stats_screenshot_imgkit,
    win_probability,
)

from .bot import bot
from .models import (
    AdminRole,
    Category,
    Commend,
    CustomCommand,
    DiscordGuild,
    FinishedGame,
    FinishedGamePlayer,
    InProgressGame,
    InProgressGameChannel,
    InProgressGamePlayer,
    Map,
    MapVote,
    Player,
    PlayerCategoryTrueskill,
    PlayerDecay,
    Queue,
    QueueNotification,
    QueuePlayer,
    QueueRole,
    QueueWaitlist,
    QueueWaitlistPlayer,
    Rotation,
    RotationMap,
    Session,
    SkipMapVote,
    VotePassedWaitlist,
    VotePassedWaitlistPlayer,
)
from .names import generate_be_name, generate_ds_name
from .queues import AddPlayerQueueMessage, add_player_queue
from .twitch import twitch
from .views.in_progress_game import InProgressGameView
from .cogs.economy import EconomyCommands


def get_even_teams(
    player_ids: list[int], team_size: int, is_rated: bool, queue_category_id: str | None
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
    # This is important! This ensures captains are randomly distributed!
    shuffle(players)
    if queue_category_id:
        player_category_trueskills = session.query(PlayerCategoryTrueskill).filter(
            PlayerCategoryTrueskill.player_id.in_(player_ids),
            PlayerCategoryTrueskill.category_id == queue_category_id,
        )
        player_category_trueskills = {
            prt.player_id: prt for prt in player_category_trueskills
        }
    else:
        player_category_trueskills = {}
    best_win_prob_so_far: float = 0.0
    best_teams_so_far: list[Player] = []

    all_combinations = list(combinations(players, team_size))
    if config.MAXIMUM_TEAM_COMBINATIONS:
        all_combinations = all_combinations[: config.MAXIMUM_TEAM_COMBINATIONS]
    for i, team0 in enumerate(all_combinations):
        team1 = [p for p in players if p not in team0]
        team0_ratings = []
        for player in team0:
            if queue_category_id and player.id in player_category_trueskills:
                player_category_trueskill: PlayerCategoryTrueskill = (
                    player_category_trueskills[player.id]
                )
                team0_ratings.append(
                    Rating(
                        player_category_trueskill.mu,
                        player_category_trueskill.sigma,
                    )
                )
            else:
                team0_ratings.append(
                    Rating(player.rated_trueskill_mu, player.rated_trueskill_sigma)
                )
        team1_ratings = []
        for player in team1:
            if queue_category_id and player.id in player_category_trueskills:
                player_category_trueskill: PlayerCategoryTrueskill = (
                    player_category_trueskills[player.id]
                )
                team1_ratings.append(
                    Rating(
                        player_category_trueskill.mu,
                        player_category_trueskill.sigma,
                    )
                )
            else:
                team1_ratings.append(
                    Rating(player.rated_trueskill_mu, player.rated_trueskill_sigma)
                )
        win_prob = win_probability(team0_ratings, team1_ratings)
        current_team_evenness = abs(0.50 - win_prob)
        best_team_evenness_so_far = abs(0.50 - best_win_prob_so_far)
        if current_team_evenness < best_team_evenness_so_far:
            best_win_prob_so_far = win_prob
            best_teams_so_far = list(team0[:]) + list(team1[:])
        if best_team_evenness_so_far < 0.001:
            break

    print("Found team evenness:", best_team_evenness_so_far, "iterations:", i)
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
            queue_category_id=queue.category_id,
        )
    category = session.query(Category).filter(Category.id == queue.category_id).first()
    player_category_trueskills = None
    if category:
        player_category_trueskills = session.query(PlayerCategoryTrueskill).filter(
            PlayerCategoryTrueskill.category_id == category.id
        )
    if player_category_trueskills:
        average_trueskill = mean(
            list(map(lambda x: x.rank, player_category_trueskills))
        )
    else:
        average_trueskill = mean(
            list(
                map(
                    lambda x: x.rated_trueskill_mu - 3 * x.rated_trueskill_sigma,
                    players,
                )
            )
        )

    next_rotation_map: RotationMap | None = (
        session.query(RotationMap)
        .join(Rotation, Rotation.id == RotationMap.rotation_id)
        .join(Queue, Queue.rotation_id == Rotation.id)
        .filter(Queue.id == queue.id)
        .filter(RotationMap.is_next == True)
        .first()
    )
    if not next_rotation_map:
        raise Exception("No next map!")

    rolled_random_map = False
    if next_rotation_map.is_random:
        # Roll for random map
        rolled_random_map = uniform(0, 1) < next_rotation_map.random_probability

    if rolled_random_map:
        maps: List[Map] = session.query(Map).all()
        random_map = choice(maps)
        next_map_full_name = random_map.full_name
        next_map_short_name = random_map.short_name
    else:
        next_map: Map | None = (
            session.query(Map).filter(Map.id == next_rotation_map.map_id).first()
        )
        next_map_full_name = next_map.full_name
        next_map_short_name = next_map.short_name

    game = InProgressGame(
        average_trueskill=average_trueskill,
        map_full_name=next_map_full_name,
        map_short_name=next_map_short_name,
        queue_id=queue.id,
        team0_name=generate_be_name(),
        team1_name=generate_ds_name(),
        win_probability=win_prob,
    )
    if config.ECONOMY_ENABLED:
        game.prediction_open = True
    session.add(game)

    team0_players = players[: len(players) // 2]
    team1_players = players[len(players) // 2 :]

    short_game_id = short_uuid(game.id)
    title = f"\nGame '{queue.name}' ({short_game_id}) has begun!\n"
    embed = Embed(
        title=title,
        colour=Colour.blue(),
    )

    be_channel, ds_channel = None, None
    categories = {category.id: category for category in guild.categories}
    voice_category = categories[config.TRIBES_VOICE_CATEGORY_CHANNEL_ID]
    if voice_category:
        be_channel, ds_channel = await create_team_voice_channels(
            session, guild, game, voice_category
        )
    else:
        print(
            f"could not find tribes_voice_category with id {config.TRIBES_VOICE_CATEGORY_CHANNEL_ID} in guild"
        )
    match_channel: discord.TextChannel = await guild.create_text_channel(
        f"{queue.name}-({short_game_id})", category=voice_category
    )
    session.add(
        InProgressGameChannel(in_progress_game_id=game.id, channel_id=match_channel.id)
    )
    embed.add_field(
        name="Map", value=f"{game.map_full_name} ({game.map_short_name})", inline=False
    )
    embed.add_field(
        name=f"{game.team0_name} ({round(100*win_prob)}%)",
        value="\n".join([f"<@{player.id}>" for player in team0_players]),
        inline=True,
    )
    embed.add_field(
        name=f"{game.team1_name} ({round(100*(1- win_prob))}%)",
        value="\n".join([f"<@{player.id}>" for player in team1_players]),
        inline=True,
    )
    if match_channel:
        embed.add_field(
            name="Match Channel", value=match_channel.jump_url, inline=False
        )
    if config.SHOW_TRUESKILL:
        embed.add_field(
            name="Average Rating", value=round(average_trueskill, 2), inline=False
        )
    embed.add_field(
        name="Match Commands", value="\n".join(["`/setgamecode`"]), inline=True
    )
    for player in team0_players:
        if be_channel:
            await send_in_guild_message(
                guild, player.id, message_content=be_channel.jump_url, embed=embed
            )
        game_player = InProgressGamePlayer(
            in_progress_game_id=game.id,
            player_id=player.id,
            team=0,
        )
        session.add(game_player)

    for player in team1_players:
        if ds_channel:
            await send_in_guild_message(
                guild, player.id, message_content=ds_channel.jump_url, embed=embed
            )
        game_player = InProgressGamePlayer(
            in_progress_game_id=game.id,
            player_id=player.id,
            team=1,
        )
        session.add(game_player)

    await match_channel.send(embed=embed, view=InProgressGameView(game.id))

    session.query(QueuePlayer).filter(QueuePlayer.player_id.in_(player_ids)).delete()  # type: ignore
    session.commit()

    if not rolled_random_map:
        await update_next_map_to_map_after_next(queue.rotation_id, False)

    if config.ECONOMY_ENABLED:
        prediction_message_id: int = await EconomyCommands.create_prediction_message(game, match_channel)
        if prediction_message_id:
            game.prediction_message_id = prediction_message_id
            session.commit()

    if config.ENABLE_VOICE_MOVE and queue.move_enabled and be_channel and ds_channel:
        await _movegameplayers(short_game_id, None, guild)
        await send_message(
            channel,
            embed_description=f"Players moved to voice channels for game {short_game_id}",
            colour=Colour.blue(),
        )

    session.close()


async def create_team_voice_channels(
    session: SQLAlchemySession,
    guild: Guild,
    game: InProgressGame,
    category: CategoryChannel,
) -> tuple[discord.VoiceChannel, discord.VoiceChannel]:
    be_channel = await guild.create_voice_channel(
        f"{game.team0_name}", category=category
    )
    ds_channel = await guild.create_voice_channel(
        f"{game.team1_name}", category=category
    )
    session.add(
        InProgressGameChannel(in_progress_game_id=game.id, channel_id=be_channel.id)
    )
    session.add(
        InProgressGameChannel(in_progress_game_id=game.id, channel_id=ds_channel.id)
    )
    return be_channel, ds_channel


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

    player: Player = session.query(Player).filter(Player.id == player_id).first()
    queue: Queue = session.query(Queue).filter(Queue.id == queue_id).first()
    category = session.query(Category).filter(Category.id == queue.category_id).first()
    player_category_trueskill: PlayerCategoryTrueskill | None = None
    if category:
        player_category_trueskill = (
            session.query(PlayerCategoryTrueskill)
            .filter(
                PlayerCategoryTrueskill.player_id == player_id,
                PlayerCategoryTrueskill.category_id == category.id,
            )
            .first()
        )
    player_mu = player.rated_trueskill_mu
    if player_category_trueskill:
        player_mu = player_category_trueskill.mu
    if queue.mu_max is not None:
        if player_mu > queue.mu_max:
            return False, False
    if queue.mu_min is not None:
        if player_mu < queue.mu_min:
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
    team0_tsr = round(mean([player.rated_trueskill_mu for player in team0_players]), 1)
    team1_tsr = round(mean([player.rated_trueskill_mu for player in team1_players]), 1)
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

    session = ctx.session
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
        if config.REQUIRE_ADD_TARGET:
            await send_message(
                message.channel,
                embed_description=f"Usage: !add [queue]",
                colour=Colour.red(),
            )
            session.close()
            return
        # Don't auto-add to isolated queues
        queues_to_add += (
            session.query(Queue)
            .filter(Queue.is_isolated == False, Queue.is_locked == False)
            .order_by(Queue.ordinal.asc())
            .all()
        )  # type: ignore
    else:
        all_queues = (
            session.query(Queue)
            .filter(Queue.is_locked == False)
            .order_by(Queue.ordinal.asc())
            .all()
        )  # type: ignore
        for arg in args:
            # Try adding by integer index first, then try string name
            try:
                queue_ordinal = int(arg)
                queues_with_ordinal = list(
                    filter(lambda x: x.ordinal == queue_ordinal, all_queues)
                )
                for queue_to_add in queues_with_ordinal:
                    queues_to_add.append(queue_to_add)
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
        if difference < config.RE_ADD_DELAY:
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
        if difference < config.RE_ADD_DELAY:
            time_to_wait: int = floor(config.RE_ADD_DELAY - difference)
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
    session = ctx.session
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
        session = ctx.session
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
async def autosub(ctx: Context, member: Member = None):
    """
    Picks a person to sub at random

    :member: If provided, this is the player in the game being subbed out. If
    not provided, then the player running the command must be in the game
    """
    message = ctx.message
    session = ctx.session

    player_in_game_id = member.id if member else message.author.id
    # If target player isn't a game, exit early
    ipg_player = (
        session.query(InProgressGamePlayer)
        .filter(InProgressGamePlayer.player_id == player_in_game_id)
        .first()
    )
    if not ipg_player:
        player_name = member.name if member else message.author.name
        await send_message(
            message.channel,
            embed_description=f"**{player_name}** must be in a game!",
            colour=Colour.red(),
        )
        return

    in_progress_game: InProgressGame = (
        session.query(InProgressGame)
        .filter(InProgressGame.id == ipg_player.in_progress_game_id)
        .first()
    )
    players_in_queue: List[QueuePlayer] = (
        session.query(QueuePlayer)
        .filter(QueuePlayer.queue_id == in_progress_game.queue_id)
        .all()
    )

    if len(players_in_queue) == 0:
        queue: Queue = (
            session.query(Queue).filter(Queue.id == in_progress_game.queue_id).first()
        )
        await send_message(
            message.channel,
            embed_description=f"No players in queue **{queue.name}**",
            colour=Colour.red(),
        )
        return

    # Do the sub - swap the in progress game players and delete the subbed in player from the queue
    player_to_sub: QueuePlayer = choice(players_in_queue)
    session.add(
        InProgressGamePlayer(
            in_progress_game_id=ipg_player.in_progress_game_id,
            player_id=player_to_sub.player_id,
            team=ipg_player.team,
        )
    )
    session.delete(ipg_player)
    queue_players_to_delete = (
        session.query(QueuePlayer)
        .filter(QueuePlayer.player_id == player_to_sub.player_id)
        .all()
    )
    for qp in queue_players_to_delete:
        session.delete(qp)
    session.commit()

    subbed_in_player: Player = (
        session.query(Player).filter(Player.id == player_to_sub.player_id).first()
    )
    subbed_out_player_name = member.name if member else message.author.name
    await send_message(
        message.channel,
        embed_description=f"Auto-subbed **{subbed_in_player.name}** in for **{subbed_out_player_name}**",
        colour=Colour.blue(),
    )

    await _rebalance_game(in_progress_game, session, message)
    session.commit()
    session.close()


@bot.command()
@commands.check(is_admin)
async def ban(ctx: Context, member: Member):
    """TODO: remove player from queues"""
    message = ctx.message
    session = ctx.session
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


@bot.tree.command(name="cancelgame", description="Given a game ID, cancels that game")
@commands.check(is_admin)
async def cancelgame(interaction: Interaction, game_id: str):
    if config.ECONOMY_ENABLED:
        try:
            await EconomyCommands.cancel_predictions(interaction, game_id)
        except ValueError as ve:
            #Raised if there are no predictions on this game
            await interaction.channel.send(
                embed=Embed(
                    description="No predictions to be refunded",
                    colour=Colour.blue()
                )           
            )
        except Exception as e:
            await interaction.channel.send(
                embed=Embed(
                    description="Predictions failed to refund",
                    colour=Colour.red()
                )           
            )
        else:
            await interaction.channel.send(
                embed=Embed(
                    description="Predictions refunded",
                    colour=Colour.blue()
                )           
            )
    
    await cancel_in_progress_game(interaction, game_id)
    


@bot.command()
async def coinflip(ctx: Context):
    message = ctx.message
    result = "HEADS" if floor(random() * 2) == 0 else "TAILS"
    await send_message(message.channel, embed_description=result, colour=Colour.blue())


@bot.command()
async def commend(ctx: Context, member: Member):
    session = ctx.session
    commender: Player | None = (
        session.query(Player).filter(Player.id == ctx.message.author.id).first()
    )
    commendee: Player | None = (
        session.query(Player).filter(Player.id == member.id).first()
    )
    emoji = choice(["🔨", "💥", "🤕", "🤌"])
    if ctx.message.author.id == member.id:
        await send_message(
            ctx.message.channel,
            embed_description=f"{emoji}  **BONK**  {emoji}",
            colour=Colour.red(),
        )
        return

    if not commendee:
        await send_message(
            ctx.message.channel,
            embed_description=f"Could not find {escape_markdown(member.name)}",
            colour=Colour.red(),
        )
        return

    last_finished_game: FinishedGame | None = (
        session.query(FinishedGame)
        .join(FinishedGamePlayer)
        .filter(FinishedGamePlayer.player_id == commender.id)
        .order_by(FinishedGame.finished_at.desc())
        .first()
    )
    if not last_finished_game:
        await send_message(
            ctx.message.channel,
            embed_description=f"Could not find last game played for {escape_markdown(member.name)}",
            colour=Colour.red(),
        )
        return

    has_commend = (
        session.query(Commend)
        .filter(
            Commend.finished_game_id == last_finished_game.id,
            Commend.commender_id == commender.id,
        )
        .first()
    )
    if has_commend is not None:
        await send_message(
            ctx.message.channel,
            embed_description=f"You already commended someone for this game",
            colour=Colour.red(),
        )
        return

    players_in_last_game = (
        session.query(FinishedGamePlayer)
        .filter(FinishedGamePlayer.finished_game_id == last_finished_game.id)
        .all()
    )
    player_ids = set(map(lambda x: x.player_id, players_in_last_game))
    if commendee.id not in player_ids:
        await send_message(
            ctx.message.channel,
            embed_description=f"{escape_markdown(commendee.name)} was not in your last game",
            colour=Colour.red(),
        )
        return

    session.add(
        Commend(
            last_finished_game.id,
            commender.id,
            commender.name,
            commendee.id,
            commendee.name,
        )
    )
    commender.raffle_tickets += 1
    session.add(commender)
    session.commit()
    await send_message(
        ctx.message.channel,
        embed_description=f"⭐ {escape_markdown(commendee.name)} received a commend! ⭐",
        colour=Colour.green(),
    )
    session.close()


@bot.command()
async def commendstats(ctx: Context):
    session = ctx.session
    most_commends_given_statement = (
        select(Player, func.count(Commend.commender_id).label("commend_count"))
        .join(Commend, Commend.commender_id == Player.id)
        .group_by(Player.id)
        .having(func.count(Commend.commender_id) > 0)
        .order_by(func.count(Commend.commender_id).desc())
    )
    most_commends_received_statement = (
        select(Player, func.count(Commend.commendee_id).label("commend_count"))
        .join(Commend, Commend.commendee_id == Player.id)
        .group_by(Player.id)
        .having(func.count(Commend.commendee_id) > 0)
        .order_by(func.count(Commend.commendee_id).desc())
    )

    most_commends_given: List[Player] = session.execute(
        most_commends_given_statement
    ).fetchall()
    most_commends_received: List[Player] = session.execute(
        most_commends_received_statement
    ).fetchall()
    session.close()

    output = "**Most commends given**"
    for i, row in enumerate(most_commends_given, 1):
        player = row[Player]
        count = row["commend_count"]
        output += f"\n{i}. {count} - {player.name}"
    output += "\n**Most commends received**"
    for i, row in enumerate(most_commends_received, 1):
        player = row[Player]
        count = row["commend_count"]
        output += f"\n{i}. {count} - {player.name}"

    if config.LEADERBOARD_CHANNEL:
        channel = bot.get_channel(config.LEADERBOARD_CHANNEL)
        await send_message(channel, embed_description=output, colour=Colour.blue())
        await send_message(
            ctx.message.channel,
            embed_description=f"Check {channel.mention}!",
            colour=Colour.blue(),
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


@bot.tree.command(description="Initially configure the bot for this server")
async def configure(interaction: Interaction):
    session = Session()
    guild = (
        session.query(DiscordGuild)
        .filter(DiscordGuild.discord_id == interaction.guild_id)
        .first()
    )
    if guild:
        await interaction.response.send_message(
            embed=Embed(
                description="Server already configured",
                colour=Colour.red(),
            ),
            ephemeral=True,
        )
    else:
        guild = DiscordGuild(interaction.guild_id, interaction.guild.name)
        session.add(guild)
        session.commit()
        await interaction.response.send_message(
            embed=Embed(
                description="Server configured successfully!",
                colour=Colour.green(),
            ),
            ephemeral=True,
        )
    session.close()


@bot.command()
@commands.check(is_admin)
async def createcommand(ctx: Context, name: str, *, output: str):
    message = ctx.message
    session: str = ctx.session
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
    copyfile(f"{config.DB_NAME}.db", f"{config.DB_NAME}_{date_string}.db")
    await send_message(
        message.channel,
        embed_description=f"Backup made to {config.DB_NAME}_{date_string}.db",
        colour=Colour.green(),
    )


@bot.command()
@commands.check(is_admin)
async def restart(ctx):
    await ctx.send("Restarting bot... ")
    os.execv(sys.executable, ["python", "-m", "discord_bots.main"])


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

    session = ctx.session
    player: Player = (
        session.query(Player).filter(Player.id == message.mentions[0].id).first()
    )
    rated_trueskill_mu_before = player.rated_trueskill_mu
    rated_trueskill_mu_after = player.rated_trueskill_mu * (100 - decay_amount) / 100
    player.rated_trueskill_mu = rated_trueskill_mu_after
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
    session = ctx.session
    queues_to_del_query = (
        session.query(Queue)
        .join(QueuePlayer)
        .filter(QueuePlayer.player_id == message.author.id)
        .order_by(Queue.ordinal.asc())
    )  # type: ignore

    if len(args) > 0:
        queues_to_del_query = queues_to_del_query.filter(
            or_(
                Queue.ordinal.in_(args),
                func.lower(Queue.name).in_([x.lower() for x in args]),
            )
        )

    queues_to_del = queues_to_del_query.all()

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
    for queue in (
        session.query(Queue)
        .filter(Queue.is_locked == False)
        .order_by(Queue.ordinal.asc())
        .all()
    ):  # type: ignore
        queue_players = (
            session.query(QueuePlayer).filter(QueuePlayer.queue_id == queue.id).all()
        )
        queue_statuses.append(f"{queue.name} [{len(queue_players)}/{queue.size}]\n")

    # TODO: Check deleting by name / ordinal
    # session.query(QueueWaitlistPlayer).filter(
    #     QueueWaitlistPlayer.player_id == message.author.id
    # ).delete()

    session.commit()

    content = f"{escape_markdown(message.author.display_name)} removed from: {', '.join([queue.name for queue in queues_to_del])}\n\n"
    content += "".join(queue_statuses)
    await message.channel.send(code_block(content))
    session.close()


@bot.command(usage="<player>")
@commands.check(is_admin)
async def delplayer(ctx: Context, member: Member, *args):
    """
    Admin command to delete player from all queues
    """
    message = ctx.message
    session = ctx.session
    queues: List[Queue] = (
        session.query(Queue)
        .join(QueuePlayer)
        .filter(QueuePlayer.player_id == member.id)
        .order_by(Queue.created_at.asc())
        .all()
    )  # type: ignore
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
@commands.check(is_admin)
async def deletegame(ctx: Context, game_id: str):
    message = ctx.message
    session = ctx.session
    finished_game: FinishedGame | None = (
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
    session.query(FinishedGamePlayer).filter(
        FinishedGamePlayer.finished_game_id == finished_game.id
    ).delete()
    session.delete(finished_game)
    session.commit()
    await send_message(
        message.channel,
        embed_description=f"Game: **{finished_game.game_id}** deleted",
        colour=Colour.green(),
    )


@bot.command()
async def testleaderboard(ctx: Context):
    await print_leaderboard(ctx.channel)


@bot.command()
async def disableleaderboard(ctx: Context):
    session = ctx.session
    player = session.query(Player).filter(Player.id == ctx.message.author.id).first()
    player.leaderboard_enabled = False
    session.commit()
    await send_message(
        ctx.message.channel,
        embed_description="You are no longer visible on the leaderboard",
        colour=Colour.blue(),
    )


@bot.command()
async def disablestats(ctx: Context):
    session = ctx.session
    player = session.query(Player).filter(Player.id == ctx.message.author.id).first()
    player.stats_enabled = False
    session.commit()
    await send_message(
        ctx.message.channel, embed_description="!stats disabled", colour=Colour.blue()
    )


@bot.command(usage="<command_name> <output>")
async def editcommand(ctx: Context, name: str, *, output: str):
    message = ctx.message
    session = ctx.session
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
    session = ctx.session
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
    session = ctx.session
    player = session.query(Player).filter(Player.id == ctx.message.author.id).first()
    player.leaderboard_enabled = True
    session.commit()
    await send_message(
        ctx.message.channel,
        embed_description="You are visible on the leaderboard",
        colour=Colour.blue(),
    )


@bot.command()
async def enablestats(ctx: Context):
    session = ctx.session
    player = session.query(Player).filter(Player.id == ctx.message.author.id).first()
    player.stats_enabled = True
    session.commit()
    await send_message(
        ctx.message.channel, embed_description="!stats enabled", colour=Colour.blue()
    )


@bot.tree.command(name="finishgame", description="Ends the current game you are in")
@discord.app_commands.describe(outcome="win, loss, or tie")
@discord.app_commands.guild_only()
async def finishgame(
    interaction: Interaction,
    outcome: Literal["win", "loss", "tie"],
    game_id: Optional[str],
):
    if config.ECONOMY_ENABLED:
        await EconomyCommands.resolve_predictions(interaction, outcome, game_id)
    await finish_in_progress_game(interaction, outcome, game_id)


# @bot.command()
# @commands.check(is_admin)
# async def imagetest(ctx: Context):
#     await upload_stats_screenshot_selenium(ctx, False)


# @bot.command()
# @commands.check(is_admin)
# async def imagetest2(ctx: Context):
#     await upload_stats_screenshot_imgkit(ctx, False)


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
    for filename in glob(f"{config.DB_NAME}_*.db"):
        output += f"\n- {filename}"

    await send_message(
        message.channel,
        embed_description=output,
        colour=Colour.blue(),
    )


@bot.command()
async def listnotifications(ctx: Context):
    message = ctx.message
    session = ctx.session
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
    session = ctx.session
    player = session.query(Player).filter(Player.id == member.id).first()
    player_decays: list[PlayerDecay] = session.query(PlayerDecay).filter(
        PlayerDecay.player_id == player.id
    )
    output = f"Decays for {escape_markdown(player.name)}:"
    for player_decay in player_decays:
        output += f"\n- {player_decay.decayed_at.strftime('%Y-%m-%d')} - Amount: {player_decay.decay_percentage}%"

    await send_message(message.channel, embed_description=output, colour=Colour.blue())


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


async def _movegameplayers(game_id: str, ctx: Context = None, guild: Guild = None):
    message: Message | None = None
    if ctx:
        message = ctx.message
        session = ctx.session
        guild = ctx.guild
    elif guild:
        message = None
        session = Session()
    else:
        raise Exception("No Context or Guild on _movegameplayers")

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

    in_progress_game_channels: list[InProgressGameChannel] = session.query(
        InProgressGameChannel
    ).filter(InProgressGameChannel.in_progress_game_id == in_progress_game.id)

    for player in team0_players:
        if player.move_enabled:
            member: Member | None = guild.get_member(player.id)
            if member:
                channel: VoiceChannel | None = guild.get_channel(
                    in_progress_game_channels[0].channel_id
                )
                if channel:
                    try:
                        await member.move_to(channel, reason=game_id)
                    except Exception as e:
                        print(f"Caught exception sending message: {e}")

    for player in team1_players:
        if player.move_enabled:
            member: Member | None = guild.get_member(player.id)
            if member:
                channel: VoiceChannel | None = guild.get_channel(
                    in_progress_game_channels[1].channel_id
                )
                if channel:
                    try:
                        await member.move_to(channel, reason=game_id)
                    except Exception as e:
                        print(f"Caught exception sending message: {e}")


@bot.command(usage="<game_id>")
@commands.check(is_admin)
async def movegameplayers(ctx: Context, game_id: str):
    """
    Move players in a given in-progress game to the correct voice channels
    """
    message = ctx.message

    if not config.ENABLE_VOICE_MOVE:
        await send_message(
            message.channel,
            embed_description="Voice movement is disabled",
            colour=Colour.red(),
        )
        return
    else:
        await _movegameplayers(game_id, ctx)
        await send_message(
            message.channel,
            embed_description=f"Players moved to voice channels for game {game_id}",
            colour=Colour.green(),
        )


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

    session = ctx.session
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

    session = ctx.session
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
@commands.check(is_admin)
async def removeadmin(ctx: Context, member: Member):
    message = ctx.message
    session = ctx.session
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
        session = ctx.session
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
    session = ctx.session
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
    session = ctx.session
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
async def removedbbackup(ctx: Context, db_filename: str):
    message = ctx.message
    if not db_filename.startswith(config.DB_NAME) or not db_filename.endswith(".db"):
        await send_message(
            message.channel,
            embed_description=f"Filename must be of the format {config.DB_NAME}_{{date}}.db",
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
async def roll(ctx: Context, low_range: int, high_range: int):
    message = ctx.message
    await send_message(
        message.channel,
        embed_description=f"You rolled: {randint(low_range, high_range)}",
        colour=Colour.blue(),
    )


@bot.command()
@commands.check(is_admin)
async def resetleaderboardchannel(ctx: Context):
    if not config.LEADERBOARD_CHANNEL:
        await send_message(
            ctx.message.channel,
            embed_description=f"Leaderboard channel ID not configured",
            colour=Colour.red(),
        )
        return
    channel = bot.get_channel(config.LEADERBOARD_CHANNEL)
    if not channel:
        await send_message(
            ctx.message.channel,
            embed_description=f"Could not find leaderboard channel, check ID",
            colour=Colour.red(),
        )
        return

    await channel.purge()
    await print_leaderboard(ctx.channel)
    await send_message(
        ctx.message.channel,
        embed_description=f"Leaderboard channel reset",
        colour=Colour.green(),
    )
    return


@bot.command()
@commands.check(is_admin)
async def resetplayertrueskill(ctx: Context, member: Member):
    message = ctx.message
    session = ctx.session
    player: Player = session.query(Player).filter(Player.id == member.id).first()
    player.rated_trueskill_mu = config.DEFAULT_TRUESKILL_MU
    player.rated_trueskill_sigma = config.DEFAULT_TRUESKILL_SIGMA
    session.commit()
    session.close()
    await send_message(
        message.channel,
        embed_description=f"{escape_markdown(member.name)} trueskill reset.",
        colour=Colour.green(),
    )


# TODO: Re-enable when configs are stored in the db
# @bot.command()
# @commands.check(is_admin)
# async def setadddelay(ctx: Context, delay_seconds: int):
#     message = ctx.message
#     global RE_ADD_DELAY
#     RE_ADD_DELAY = delay_seconds
#     await send_message(
#         message.channel,
#         embed_description=f"Delay between games set to {RE_ADD_DELAY}",
#         colour=Colour.green(),
#     )


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
    # TODO move to db-config
    message = ctx.message
    global COMMAND_PREFIX
    COMMAND_PREFIX = prefix
    await send_message(
        message.channel,
        embed_description=f"Command prefix set to {COMMAND_PREFIX}",
        colour=Colour.green(),
    )


@bot.tree.command(
    name="setgamecode", description="Sets lobby code for your current game"
)
@discord.app_commands.guild_only()
async def setgamecode(interaction: Interaction, code: str):
    session = Session()
    ipgp = (
        session.query(InProgressGamePlayer)
        .filter(InProgressGamePlayer.player_id == interaction.user.id)
        .first()
    )
    if not ipgp:
        await interaction.response.send_message(
            embed=Embed(
                description="You must be in game to set the game code!",
                colour=Colour.red(),
            ),
            ephemeral=True,
        )
        return

    ipg = (
        session.query(InProgressGame)
        .filter(InProgressGame.id == ipgp.in_progress_game_id)
        .first()
    )
    if not ipg:
        await interaction.response.send_message(
            embed=Embed(
                description="You must be in game to set the game code!",
                colour=Colour.red(),
            ),
            ephemeral=True,
        )
        return

    ipg.code = code
    ipg_players: list[InProgressGamePlayer] = (
        session.query(InProgressGamePlayer)
        .filter(
            InProgressGamePlayer.in_progress_game_id == ipg.id,
            InProgressGamePlayer.player_id
            != interaction.user.id,  # don't send the code to the one who wants to send it out
        )
        .all()
    )
    session.commit()

    for ipg_player in ipg_players:
        embed = Embed(
            title=f"Lobby code for ({short_uuid(ipg.id)})",
            description=f"`{code}`",
            colour=Colour.blue(),
        )
        embed.set_footer(text=f"set by {interaction.user}")
        if interaction.guild:
            await send_in_guild_message(
                interaction.guild, ipg_player.player_id, embed=embed
            )
    if ipg_players:
        await interaction.response.send_message(
            embed=Embed(
                description="Lobby code sent to each player", colour=Colour.blue()
            ),
            ephemeral=True,
        )
    else:
        logging.warn("No in_progress_game_players to send a lobby code to")
        await interaction.response.send_message(
            embed=Embed(
                description="There are no in-game players to send this lobby code to!",
                colour=Colour.red(),
            ),
            ephemeral=True,
        )
    session.close()


@bot.command(usage="<true|false>")
async def setmoveenabled(ctx: Context, enabled_option: bool = True):
    session = ctx.session

    if not config.ENABLE_VOICE_MOVE:
        await send_message(
            ctx.message.channel,
            embed_description="Voice movement is disabled",
            colour=Colour.red(),
        )
        return

    player = session.query(Player).filter(Player.id == ctx.message.author.id).first()
    player.move_enabled = enabled_option
    session.commit()

    if enabled_option:
        await send_message(
            ctx.message.channel,
            embed_description="Player moving enabled",
            colour=Colour.blue(),
        )
    else:
        await send_message(
            ctx.message.channel,
            embed_description="Player moving disabled",
            colour=Colour.blue(),
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

    session = ctx.session
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
    session = ctx.session
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
    session = ctx.session
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
            fgps, (len(fgps) + 1) // 2, finished_game.is_rated, 7
        )
        worst_teams = get_n_worst_finished_game_teams(
            fgps, (len(fgps) + 1) // 2, finished_game.is_rated, 1
        )
        game_str += "\n**Most even team combinations:**"
        for i, (_, best_team) in enumerate(best_teams):
            # Every two pairings is the same
            if i % 2 == 0:
                continue
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
    Returns the player's base sigma
    """
    session = ctx.session
    player: Player = session.query(Player).filter(Player.id == member.id).first()
    output = embed_title = (
        f"**{member.name}'s** sigma: **{round(player.rated_trueskill_sigma, 4)}**"
    )
    await send_message(
        channel=ctx.message.channel,
        embed_description=output,
        # embed_title=f"{member.name} sigma:",
        colour=Colour.blue(),
    )


@bot.command()
@commands.check(is_admin)
async def showtrueskillnormdist(ctx: Context, queue_name: str):
    """
    Print the normal distribution of the trueskill in a given queue.

    Useful for setting queue mu ranges
    """
    session = ctx.session
    queue = session.query(Queue).filter(Queue.name.ilike(queue_name)).first()  # type: ignore
    if not queue:
        await send_message(
            channel=ctx.message.channel,
            embed_description=f"Could not find queue: **{queue_name}**",
            colour=Colour.red(),
        )
        return

    trueskill_mus = []
    if queue.category_id:
        player_category_trueskills: List[PlayerCategoryTrueskill] = (
            session.query(PlayerCategoryTrueskill)
            .filter(
                PlayerCategoryTrueskill.category_id == queue.category_id,
            )
            .all()
        )
        trueskill_mus = [pct.mu for pct in player_category_trueskills]
    else:
        players = session.query(Player).filter(Player.finished_game_players.any()).all()
        trueskill_mus = [p.rated_trueskill_mu for p in players]

    std_dev = std(trueskill_mus)
    average = mean(trueskill_mus)
    output = []
    output.append(f"**Data points**: {len(trueskill_mus)}")
    output.append(f"**Mean**: {round(average, 2)}")
    output.append(f"**Stddev**: {round(std_dev, 2)}\n")
    output.append(f"**2%** (+2σ): {round(average + 2 * std_dev, 2)}")
    output.append(f"**7%** (+1.5σ): {round(average + 1.5 * std_dev, 2)}")
    output.append(f"**16%** (+1σ): {round(average + 1 * std_dev, 2)}")
    output.append(f"**31%** (+0.5σ): {round(average + 0.5 * std_dev, 2)}")
    output.append(f"**50%** (0σ): {round(average, 2)}")
    output.append(f"**69%** (-0.5σ): {round(average - 0.5 * std_dev, 2)}")
    output.append(f"**84%** (-1σ): {round(average - 1 * std_dev, 2)}")
    output.append(f"**93%** (-1.5σ): {round(average - 1.5 * std_dev, 2)}")
    output.append(f"**98%** (+2σ): {round(average - 2 * std_dev, 2)}")

    await send_message(
        channel=ctx.message.channel,
        embed_description="\n".join(output),
        colour=Colour.blue(),
    )


@bot.command()
async def status(ctx: Context, *args):
    session = ctx.session

    all_rotations: list[Rotation] | None = (
        session.query(Rotation).order_by(Rotation.created_at.asc()).all()
    )

    output = "```autohotkey\n"

    for rotation in all_rotations:
        output += f"--- {rotation.name} ---\n\n"
        queues: list[Queue] = []
        rotation_queues: list[Queue] | None = (
            session.query(Queue)
            .filter(Queue.rotation_id == rotation.id)
            .order_by(Queue.ordinal.asc())
            .all()
        )
        if not rotation_queues:
            continue

        if len(args) == 0:
            queues: list[Queue] = rotation_queues
        else:
            for arg in args:
                # Try adding by integer index first, then try string name
                try:
                    queue_index = int(arg) - 1
                    queues.append(rotation_queues[queue_index])
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

        # Only show map if they didn't request a specific queue
        if len(args) == 0:
            next_rotation_map: RotationMap | None = (
                session.query(RotationMap)
                .filter(RotationMap.rotation_id == rotation.id)
                .filter(RotationMap.is_next == True)
                .first()
            )
            if not next_rotation_map:
                continue
            next_map: Map | None = (
                session.query(Map)
                .join(RotationMap, RotationMap.map_id == Map.id)
                .filter(next_rotation_map.map_id == Map.id)
                .first()
            )
            rotation_map_after_next = (
                session.query(RotationMap)
                .filter(RotationMap.rotation_id == rotation.id)
                .filter(RotationMap.ordinal == next_rotation_map.ordinal + 1)
                .first()
            )
            if not rotation_map_after_next:
                rotation_map_after_next = (
                    session.query(RotationMap)
                    .filter(RotationMap.rotation_id == rotation.id)
                    .filter(RotationMap.ordinal == 1)
                    .first()
                )
            map_after_next: Map | None = (
                session.query(Map)
                .join(RotationMap, RotationMap.map_id == Map.id)
                .filter(rotation_map_after_next.map_id == Map.id)
                .first()
            )

            next_map_str = f"Next map: {next_map.full_name} ({next_map.short_name})"
            if config.ENABLE_RAFFLE:
                has_raffle_reward = next_rotation_map.raffle_ticket_reward > 0
                raffle_reward = (
                    next_rotation_map.raffle_ticket_reward
                    if has_raffle_reward
                    else config.DEFAULT_RAFFLE_VALUE
                )
                next_map_str += f" ({raffle_reward} tickets)"
            next_map_str += "\n"

            if config.DISABLE_MAP_ROTATION or next_rotation_map.ordinal == 1:
                # output += f"{next_map_str}\nMap after next: "
                output += f"{next_map_str}\n"
            else:
                time_since_update: timedelta = datetime.now(
                    timezone.utc
                ) - next_rotation_map.updated_at.replace(tzinfo=timezone.utc)

                time_until_rotation = config.MAP_ROTATION_MINUTES - (
                    time_since_update.seconds // 60
                )
                # output += f"{next_map_str}\nMap after next (auto-rotates in {time_until_rotation} minutes): "
                output += f"{next_map_str}\n"

            # output += f"{map_after_next.full_name} ({map_after_next.short_name})\n"

            skip_map_votes: list[SkipMapVote] = (
                session.query(SkipMapVote)
                .filter(SkipMapVote.rotation_id == rotation.id)
                .all()
            )
            # output += f"Votes to skip (voteskip): [{len(skip_map_votes)}/{config.MAP_VOTE_THRESHOLD}]\n"

            # TODO: This is duplicated
            map_vote_names = (
                session.query(Map.short_name)
                .join(RotationMap, RotationMap.map_id == Map.id)
                .join(MapVote, MapVote.rotation_map_id == RotationMap.id)
                .filter(RotationMap.rotation_id == rotation.id)
                .all()
            )

            vote_counts = {}
            for map_vote in map_vote_names:
                map_name = map_vote[0]
                vote_counts[map_name] = vote_counts.get(map_name, 0) + 1

            voted_maps_str = ""
            for map, count in vote_counts.items():
                voted_maps_str += f"{map} [{count}/{config.MAP_VOTE_THRESHOLD}], "
            voted_maps_str = voted_maps_str[:-2]

            # output += f"Votes to change map (votemap): {voted_maps_str}\n\n"

        for i, queue in enumerate(queues):
            if i > 0:
                output += "\n"
            players_in_queue = (
                session.query(Player)
                .join(QueuePlayer)
                .filter(QueuePlayer.queue_id == queue.id)
                .all()
            )
            if queue.is_locked:
                continue
            else:
                output += f"({queue.ordinal}) {queue.name} [{len(players_in_queue)} / {queue.size}]\n"

            if len(players_in_queue) > 0:
                output += f"IN QUEUE: "
                output += ", ".join(
                    sorted(
                        [escape_markdown(player.name) for player in players_in_queue]
                    )
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
                    if config.SHOW_TRUESKILL:
                        output += f"Map: {game.map_full_name} ({short_game_id}) (mu: {round(game.average_trueskill, 2)}):\n"
                        if game.code:
                            output += f"Game code: {game.code}\n"
                    else:
                        output += f"Map: {game.map_full_name} ({short_game_id}):\n"
                        if game.code:
                            output += f"Game code: {game.code}\n"

                    skip_map_votes = (
                        session.query(SkipMapVote)
                        .join(
                            InProgressGamePlayer,
                            InProgressGamePlayer.player_id == SkipMapVote.player_id,
                        )
                        .filter(InProgressGamePlayer.in_progress_game_id == game.id)
                        .all()
                    )
                    if skip_map_votes:
                        queue_vote_threshold = (
                            session.query(Queue.vote_threshold)
                            .join(InProgressGame, InProgressGame.queue_id == Queue.id)
                            .filter(InProgressGame.id == game.id)
                            .scalar()
                        )
                        output += f"Votes to skip: [{len(skip_map_votes)} / {queue_vote_threshold}]\n"

                    if config.SHOW_LEFT_RIGHT_TEAM:
                        output += "(L) "
                    output += pretty_format_team_no_format(
                        game.team0_name, game.win_probability, team0_players
                    )
                    if config.SHOW_LEFT_RIGHT_TEAM:
                        output += "(R) "
                    output += pretty_format_team_no_format(
                        game.team1_name, 1 - game.win_probability, team1_players
                    )
                    minutes_ago = (
                        datetime.now(timezone.utc)
                        - game.created_at.replace(tzinfo=timezone.utc)
                    ).seconds // 60
                    output += f"@ {minutes_ago} minutes ago\n"

        output += "\n"

    if len(output) == 0:
        output = "No queues or games"

    output += "\n```"

    await ctx.message.channel.send(content=output)


def win_rate(wins, losses, ties):
    denominator = max(wins + losses + ties, 1)
    return round(100 * (wins + 0.5 * ties) / denominator, 1)


@bot.tree.command(
    name="stats", description="Privately displays your TrueSkill statistics"
)
async def stats(interaction: Interaction):
    """
    Replies to the user with their TrueSkill statistics. Can be used both inside and out of a Guild
    """
    session: SQLAlchemySession = Session()
    player: Player | None = (
        session.query(Player).filter(Player.id == interaction.user.id).first()
    )
    if not player:
        # Edge case where user has no record in the Players table
        await interaction.response.send_message(
            "You have not played any games", ephemeral=True
        )
        session.close()
        return
    if not player.stats_enabled:
        await interaction.response.send_message(
            "You have disabled `/stats`",
            ephemeral=True,
        )
        session.close()
        return

    fgps: List[FinishedGamePlayer] | None = (
        session.query(FinishedGamePlayer)
        .filter(FinishedGamePlayer.player_id == player.id)
        .all()
    )
    if not fgps:
        await interaction.response.send_message(
            "You have not played any games",
            ephemeral=True,
        )
        session.close()
        return

    finished_game_ids: List[str] | None = [fgp.finished_game_id for fgp in fgps]
    fgs: List[FinishedGame] | None = (
        session.query(FinishedGame).filter(FinishedGame.id.in_(finished_game_ids)).all()
    )
    if not fgs:
        await interaction.response.send_message(
            "You have not played any games",
            ephemeral=True,
        )
        session.close()
        return

    fgps_by_finished_game_id: dict[str, FinishedGamePlayer] = {
        fgp.finished_game_id: fgp for fgp in fgps
    }

    players: list[Player] = session.query(Player).all()

    default_rating = Rating()
    # Filter players that haven't played a game
    players = list(
        filter(
            lambda x: (
                x.rated_trueskill_mu != default_rating.mu
                and x.rated_trueskill_sigma != default_rating.sigma
            )
            and (
                x.rated_trueskill_mu != config.DEFAULT_TRUESKILL_MU
                and x.rated_trueskill_sigma != config.DEFAULT_TRUESKILL_SIGMA
            ),
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
    trueskill_ratio = (len(trueskills) - trueskill_index) / (len(trueskills) or 1)
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

    # all of this below can probably be done more gracefull with a pandas dataframe
    def wins_losses_ties_last_ndays(
        finished_games: List[FinishedGame], n: int = -1
    ) -> tuple[list[FinishedGame], list[FinishedGame], list[FinishedGame]]:
        if n == -1:
            # all finished games
            last_nfgs = finished_games
        else:
            # last n
            last_nfgs = [
                fg
                for fg in finished_games
                if fg.finished_at > datetime.now() - timedelta(days=n)
            ]
        wins = [
            fg
            for fg in last_nfgs
            if fg.winning_team == fgps_by_finished_game_id[fg.id].team
        ]
        losses = [
            fg
            for fg in last_nfgs
            if fg.winning_team != fgps_by_finished_game_id[fg.id].team
            and fg.winning_team != -1
        ]
        ties = [fg for fg in last_nfgs if fg.winning_team == -1]
        return wins, losses, ties

    def get_table_col(games: List[FinishedGame]):
        cols = []
        for num_days in [7, 30, 90, 365, -1]:
            wins, losses, ties = wins_losses_ties_last_ndays(games, num_days)
            num_wins, num_losses, num_ties = len(wins), len(losses), len(ties)
            winrate = win_rate(num_wins, num_losses, num_ties)
            col = [
                "Overall" if num_days == -1 else f"Last {num_days} days",
                len(wins),
                len(losses),
                len(ties),
                num_wins + num_losses + num_ties,
                f"{winrate}%",
            ]
            cols.append(col)
        return cols

    embeds: list[Embed] = []
    trueskill_url = (
        "https://www.microsoft.com/en-us/research/project/trueskill-ranking-system/"
    )
    footer_text = "{}\n{}\n{}".format(
        f"Rating = {MU_LOWER_UNICODE} - 3*{SIGMA_LOWER_UNICODE}",
        f"{MU_LOWER_UNICODE} (mu) = your average Rating",
        f"{SIGMA_LOWER_UNICODE} (sigma) = the uncertainity of your Rating",
    )
    cols = []
    player_category_trueskills: list[PlayerCategoryTrueskill] | None = (
        session.query(PlayerCategoryTrueskill)
        .filter(PlayerCategoryTrueskill.player_id == player.id)
        .all()
    )
    # assume that if a guild uses categories, they will use them exclusively, i.e., no mixing categorized and uncategorized queues
    if player_category_trueskills:
        for pct in player_category_trueskills:
            category: Category | None = (
                session.query(Category).filter(Category.id == pct.category_id).first()
            )
            if not category:
                # should never happen
                logging.error(
                    f"No Category found for player_category_trueskill with id {pct.id}"
                )
                await interaction.response.send_message(
                    embed=Embed(description="Could not find your stats")
                )
                session.close()
                return
            title = f"Stats for {category.name}"
            description = ""
            if category.is_rated and config.SHOW_TRUESKILL:
                description = f"Rating: {round(pct.rank, 1)}"
                description += f"\n{MU_LOWER_UNICODE}: {round(pct.mu, 1)}"
                description += f"\n{SIGMA_LOWER_UNICODE}: {round(pct.sigma, 1)}"
            else:
                description = f"Rating: {trueskill_pct}"

            category_games = [
                game
                for game in fgs
                if game.category_name and category.name == game.category_name
            ]
            cols = get_table_col(category_games)
            table = table2ascii(
                header=["", f"Wins", "Losses", "Ties", "Total", "Winrate"],
                body=cols,
                first_col_heading=True,
                style=PresetStyle.minimalist,
            )
            description += code_block(table)
            description += f"\n{trueskill_url}"
            embed = Embed(title=title, description=description)
            embed.set_footer(text=footer_text)
            embeds.append(embed)
    if not player_category_trueskills:
        # no categories defined, display their global trueskill stats
        description = ""
        if config.SHOW_TRUESKILL:
            description = f"Rating: {round(player.rated_trueskill_mu - 3 * player.rated_trueskill_sigma, 2)}"
            description += (
                f"\n{MU_LOWER_UNICODE}: {round(player.rated_trueskill_mu, 1)}"
            )
            description += (
                f"\n{SIGMA_LOWER_UNICODE}: {round(player.rated_trueskill_sigma, 1)}"
            )
        else:
            description = f"Rating: {trueskill_pct}"
        cols = get_table_col(fgs)
        table = table2ascii(
            header=["", f"Wins", "Losses", "Ties", "Total", "Winrate"],
            body=cols,
            first_col_heading=True,
            style=PresetStyle.minimalist,
        )
        description += code_block(table)
        embed = Embed(
            title="Overall Stats",
            description=description,
        )
        embed.set_footer(text=footer_text)
        embeds.append(embed)
    try:
        await interaction.response.send_message(embeds=embeds, ephemeral=True)
    except Exception as e:
        logging.warn(f"Caught exception {e} trying to send stats message")
    session.close()


@bot.command()
async def streams(ctx: Context):
    if not twitch:
        await send_message(
            channel=ctx.message.channel,
            embed_description=f"TWITCH_GAME_NAME, TWITCH_CLIENT_ID or TWITCH_CLIENT_SECRET not set!",
            colour=Colour.red(),
        )
        return

    games_data = twitch.get_games(names=[config.TWITCH_GAME_NAME])
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


async def _rebalance_game(
    game: InProgressGame, session: SQLAlchemySession, message: Message
):
    """
    Recreate the players on each team - use this after subbing a player
    """
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
        queue_category_id=queue.category_id,
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
        if not config.DISABLE_PRIVATE_MESSAGES:
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
        if not config.DISABLE_PRIVATE_MESSAGES:
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

    if config.ENABLE_VOICE_MOVE:
        if queue.move_enabled:
            await _movegameplayers(short_game_id, None, message.guild)
            await send_message(
                message.channel,
                embed_description=f"Players moved to new team voice channels for game {short_game_id}",
                colour=Colour.green(),
            )

    pass


@bot.command()
async def sub(ctx: Context, member: Member):
    message = ctx.message
    """
    Substitute one player in a game for another
    """
    session = ctx.session
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

    await _rebalance_game(game, session, message)
    session.commit()


@bot.command()
@commands.check(is_admin)
async def unban(ctx: Context, member: Member):
    message = ctx.message
    session = ctx.session
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
