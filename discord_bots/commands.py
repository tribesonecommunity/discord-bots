import asyncio
import concurrent.futures
import heapq
import logging
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
import sqlalchemy
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
    VoiceState
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
from table2ascii import Alignment, PresetStyle, table2ascii
from trueskill import Rating

import discord_bots.config as config
from discord_bots.checks import is_admin
from discord_bots.utils import (
    MU_LOWER_UNICODE,
    SIGMA_LOWER_UNICODE,
    code_block,
    create_finished_game_embed,
    create_in_progress_game_embed,
    get_team_name_diff,
    mean,
    print_leaderboard,
    send_in_guild_message,
    send_message,
    short_uuid,
    update_next_map_to_map_after_next,
    upload_stats_screenshot_imgkit_channel,
    win_probability,
    move_game_players
)

from .bot import bot
from .cogs.economy import EconomyCommands
from .cogs.in_progress_game import InProgressGameCog, InProgressGameView
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
from .queues import AddPlayerQueueMessage, add_player_queue, waitlist_messages
from .twitch import twitch
from .utils import get_guild_partial_message

_log = logging.getLogger(__name__)


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
    session: sqlalchemy.orm.Session
    with Session() as session:
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

        _log.info(
            f"Found team evenness: {best_team_evenness_so_far} interations: {i}"
        )  # DEBUG, TRACE?
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
    channel_id,
    guild_id,
):
    guild = bot.get_guild(guild_id)
    if not guild:
        return
    channel = guild.get_channel(channel_id)
    if not channel:
        return
    session: sqlalchemy.orm.Session
    with Session() as session:
        queue: Queue | None = session.query(Queue).filter(Queue.id == queue_id).first()
        if not queue:
            _log.error(f"[create_game] could not find queue with id {queue_id}")
            return
        if len(player_ids) == 1:
            # Useful for debugging, no real world application
            players = session.query(Player).filter(Player.id == player_ids[0]).all()
            win_prob = 0
        else:
            """
            # run get_even_teams in a separate process, so that it doesn't block the event loop
            loop: asyncio.AbstractEventLoop = asyncio.get_running_loop()
            with concurrent.futures.ProcessPoolExecutor() as pool:
                result = await loop.run_in_executor(
                    pool,
                    get_even_teams,
                    player_ids,
                    len(player_ids) // 2,
                    queue.is_rated,
                    queue.category_id,
                )
                players = result[0]
                win_prob = result[1]
            """
            players, win_prob = get_even_teams(
                player_ids, len(player_ids) // 2, queue.is_rated, queue.category_id
            )
        category = (
            session.query(Category).filter(Category.id == queue.category_id).first()
        )
        player_category_trueskills = None
        if category:
            player_category_trueskills: list[PlayerCategoryTrueskill] = (
                session.query(PlayerCategoryTrueskill)
                .filter(
                    PlayerCategoryTrueskill.category_id == category.id,
                    PlayerCategoryTrueskill.player_id.in_(player_ids),
                )
                .all()
            )
        if player_category_trueskills:
            average_trueskill = mean(
                list(map(lambda x: x.mu, player_category_trueskills))
            )
        else:
            average_trueskill = mean(
                list(
                    map(
                        lambda x: x.rated_trueskill_mu,
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
        for player in team0_players:
            game_player = InProgressGamePlayer(
                in_progress_game_id=game.id,
                player_id=player.id,
                team=0,
            )
            session.add(game_player)
        for player in team1_players:
            game_player = InProgressGamePlayer(
                in_progress_game_id=game.id,
                player_id=player.id,
                team=1,
            )
            session.add(game_player)

        short_game_id = short_uuid(game.id)
        # embed = Embed(
        # title=title,
        # colour=Colour.blue(),
        # )
        embed: Embed = await create_in_progress_game_embed(session, game, guild)
        embed.title = f"⏳Game '{queue.name}' ({short_uuid(game.id)}) has begun!"

        category_channel: discord.abc.GuildChannel | None = guild.get_channel(
            config.TRIBES_VOICE_CATEGORY_CHANNEL_ID
        )
        if isinstance(category_channel, discord.CategoryChannel):
            match_channel = await guild.create_text_channel(
                f"{queue.name}-({short_game_id})", category=category_channel
            )
            be_voice_channel = await guild.create_voice_channel(
                f"{game.team0_name}", category=category_channel
            )
            ds_voice_channel = await guild.create_voice_channel(
                f"{game.team1_name}", category=category_channel
            )
            session.add(
                InProgressGameChannel(
                    in_progress_game_id=game.id, channel_id=match_channel.id
                )
            )
            session.add(
                InProgressGameChannel(
                    in_progress_game_id=game.id, channel_id=be_voice_channel.id
                )
            )
            session.add(
                InProgressGameChannel(
                    in_progress_game_id=game.id, channel_id=ds_voice_channel.id
                )
            )
        else:
            _log.warning(
                f"could not find tribes_voice_category with id {config.TRIBES_VOICE_CATEGORY_CHANNEL_ID} in guild"
            )
        if match_channel:
            # the embed won't have the Match Channel Field yet, so we add it ourselves
            embed.add_field(
                name="📺 Channel", value=match_channel.jump_url, inline=True
            )
            embed_fields_len = (
                len(embed.fields) - 3
            )  # subtract team0, team1, and "newline" fields
            if embed_fields_len >= 5 and embed_fields_len % 3 == 2:
                # embeds are allowed 3 "columns" per "row"
                # to line everything up nicely when there's >= 5 fields and only one "column" slot left, we add a blank
                embed.add_field(name="", value="", inline=True)
            game.channel_id = match_channel.id
        send_message_coroutines = []
        for player in team0_players:
            send_message_coroutines.append(
                send_in_guild_message(
                    guild,
                    player.id,
                    message_content=be_voice_channel.jump_url,
                    embed=embed,
                )
            )

        for player in team1_players:
            send_message_coroutines.append(
                send_in_guild_message(
                    guild,
                    player.id,
                    message_content=ds_voice_channel.jump_url,
                    embed=embed,
                )
            )
        await asyncio.gather(*send_message_coroutines)

        in_progress_game_cog = bot.get_cog("InProgressGameCog")
        if (
            in_progress_game_cog is not None
            and isinstance(in_progress_game_cog, InProgressGameCog)
            and match_channel
        ):
            message = await match_channel.send(
                embed=embed, view=InProgressGameView(game.id, in_progress_game_cog)
            )
            game.message_id = message.id
        else:
            _log.warning("Could not get InProgressGameCog")

        session.query(QueuePlayer).filter(QueuePlayer.player_id.in_(player_ids)).delete()  # type: ignore
        session.commit()

        if not rolled_random_map:
            await update_next_map_to_map_after_next(queue.rotation_id, False)

        if config.ECONOMY_ENABLED and match_channel:
            prediction_message_id: int | None = (
                await EconomyCommands.create_prediction_message(
                    None, game, match_channel
                )
            )
            if prediction_message_id:
                game.prediction_message_id = prediction_message_id
                session.commit()

        await channel.send(embed=embed)
        if (
            config.ENABLE_VOICE_MOVE
            and queue.move_enabled
            and be_voice_channel
            and ds_voice_channel
        ):
            await move_game_players(short_game_id, None, guild)
            await send_message(
                channel,
                embed_description=f"Players moved to voice channels for game {short_game_id}",
                colour=Colour.blue(),
            )


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
    session: sqlalchemy.orm.Session
    with Session() as session:
        queue_roles = (
            session.query(QueueRole).filter(QueueRole.queue_id == queue_id).all()
        )

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
        category = (
            session.query(Category).filter(Category.id == queue.category_id).first()
        )
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
            return False, False

        queue: Queue = session.query(Queue).filter(Queue.id == queue_id).first()
        queue_players: list[QueuePlayer] = (
            session.query(QueuePlayer).filter(QueuePlayer.queue_id == queue_id).all()
        )
        if len(queue_players) == queue.size and not queue.is_sweaty:  # Pop!
            player_ids: list[int] = list(map(lambda x: x.player_id, queue_players))
            await create_game(queue.id, player_ids, channel.id, guild.id)
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


# Commands start here


@bot.check
async def is_not_banned(ctx: Context):
    """
    Global check to ensure that banned users can't use commands

    https://discordpy.readthedocs.io/en/stable/ext/commands/commands.html#global-checks
    """
    session: sqlalchemy.orm.Session
    with Session() as session:
        is_banned = (
            session.query(Player)
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
            embed_description=f"<@{message.author.id}> you are already in a game",
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
            embed_description="No valid queues found",
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
                _log.error(f"integrity error {exc}")
                session.rollback()

        current_time: datetime = datetime.now(timezone.utc)
        # The assumption is the end timestamp is later than now, otherwise it
        # would have been processed
        difference: float = (
            vpw.end_waitlist_at.replace(tzinfo=timezone.utc) - current_time
        ).total_seconds()
        if difference < config.RE_ADD_DELAY:
            time_to_wait: int = floor(config.RE_ADD_DELAY - difference)
            timer = discord.utils.format_dt(
                discord.utils.utcnow() + timedelta(seconds=time_to_wait), style="R"
            )
            waitlist_message = (
                f"A vote just passed, you will be randomized into the queue {timer}"
            )
            await send_message(
                message.channel,
                # TODO: Populate this message with the queues the player was
                # eligible for
                content=f"{message.author.display_name} added to:",
                embed_description=waitlist_message,
                colour=Colour.yellow(),
                delete_after=time_to_wait,
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
            timer = discord.utils.format_dt(
                discord.utils.utcnow() + timedelta(seconds=time_to_wait), style="R"
            )
            is_waitlist = True

    if is_waitlist and most_recent_game:
        for queue in queues_to_add:
            # TODO: Check player eligibility here?
            queue_waitlist: QueueWaitlist | None = (
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

        queue_names = [queue.name for queue in queues_to_add]
        embed_description = f"<@{message.author.id}> your game has just finished, you will be randomized into **{', '.join(queue_names)}** {timer}"
        waitlist_message: Message | None = await send_message(
            message.channel,
            # TODO: Populate this message with the queues the player was
            # eligible for
            embed_description=embed_description,
            colour=Colour.yellow(),
        )
        if waitlist_message:
            waitlist_messages.append(waitlist_message)
            waitlist_messages.append(ctx.message)
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
    guild = ctx.guild
    assert message
    assert guild

    if config.ADMIN_AUTOSUB and not await is_admin(ctx):
            return

    player_in_game_id = member.id if member else message.author.id
    player_name = member.display_name if member else message.author.display_name
    ipg_player: InProgressGamePlayer | None = (
        session.query(InProgressGamePlayer)
        .filter(InProgressGamePlayer.player_id == player_in_game_id)
        .first()
    )
    if not ipg_player:
        # If target player isn't in a game, exit early
        await send_message(
            message.channel,
            embed_description=f"**{player_name}** must be in a game!",
            colour=Colour.red(),
        )
        return
    game: InProgressGame = (
        session.query(InProgressGame)
        .filter(InProgressGame.id == ipg_player.in_progress_game_id)
        .first()
    )
    results = (
        session.query(Player.id, Player.name)
        .join(InProgressGamePlayer)
        .filter(
            InProgressGamePlayer.in_progress_game_id == game.id,
            InProgressGamePlayer.team == 0,
        )
        .all()
    )
    # skip copying the player_ids
    team0_player_names_before: list[str] = (
        [res[1] for res in results if res] if results else []
    )
    results = (
        session.query(Player.id, Player.name)
        .join(InProgressGamePlayer)
        .filter(
            InProgressGamePlayer.in_progress_game_id == game.id,
            InProgressGamePlayer.team == 1,
        )
        .all()
    )
    # skip copying the player_ids
    team1_player_names_before: list[str] = (
        [res[1] for res in results if res] if results else []
    )
    players_in_queue: List[QueuePlayer] = (
        session.query(QueuePlayer).filter(QueuePlayer.queue_id == game.queue_id).all()
    )

    if len(players_in_queue) == 0:
        queue: Queue = session.query(Queue).filter(Queue.id == game.queue_id).first()
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
    subbed_out_player_name = (
        member.display_name if member else message.author.display_name
    )
    await send_message(
        message.channel,
        embed_description=f"Auto-substituted **{subbed_in_player.name}** in for **{subbed_out_player_name}**",
        colour=Colour.yellow(),
    )
    await _rebalance_game(game, session, message)
    embed: discord.Embed = await create_in_progress_game_embed(session, game, guild)
    short_game_id: str = short_uuid(game.id)
    embed.title = f"New Teams for Game {short_game_id})"
    embed.description = (
        f"Auto-subbed **{subbed_in_player.name}** in for **{subbed_out_player_name}**"
    )
    embed.color = discord.Color.yellow()
    results = (
        session.query(Player.id, Player.name)
        .join(InProgressGamePlayer)
        .filter(
            InProgressGamePlayer.in_progress_game_id == game.id,
            InProgressGamePlayer.team == 0,
        )
        .all()
    )
    team0_player_ids_after: list[int] = (
        [res[0] for res in results if res] if results else []
    )
    team0_player_names_after: list[str] = [res[1] for res in results] if results else []
    results = (
        session.query(Player.id, Player.name)
        .join(InProgressGamePlayer)
        .filter(
            InProgressGamePlayer.in_progress_game_id == game.id,
            InProgressGamePlayer.team == 1,
        )
        .all()
    )
    team1_player_ids_after: list[int] = [res[0] for res in results] if results else []
    team1_player_names_after: list[str] = [res[1] for res in results] if results else []
    team0_diff_vaules, team1_diff_values = get_team_name_diff(
        team0_player_names_before,
        team0_player_names_after,
        team1_player_names_before,
        team1_player_names_after,
    )
    for i, field in enumerate(embed.fields):
        # brute force iteration through the embed's fields until we find the original team0 and team1 embeds to update
        if field.name == f"⬅️ {game.team0_name} ({round(100 * game.win_probability)}%)":
            embed.set_field_at(
                i,
                name=f"⬅️ {game.team0_name} ({round(100 * game.win_probability)}%)",
                value=team0_diff_vaules,
                inline=True,
            )
        if (
            field.name
            == f"➡️ {game.team1_name} ({round(100 * (1 - game.win_probability))}%)"
        ):
            embed.set_field_at(
                i,
                name=f"➡️ {game.team1_name} ({round(100 * (1 - game.win_probability))}%)",
                value=team1_diff_values,
                inline=True,
            )
    be_voice_channel: discord.VoiceChannel | None = None
    ds_voice_channel: discord.VoiceChannel | None = None
    ipg_channels: list[InProgressGameChannel] | None = (
        session.query(InProgressGameChannel)
        .filter(InProgressGameChannel.in_progress_game_id == game.id)
        .all()
    )
    for ipg_channel in ipg_channels or []:
        discord_channel: discord.abc.GuildChannel | None = guild.get_channel(
            ipg_channel.channel_id
        )
        if isinstance(discord_channel, discord.VoiceChannel):
            # This is suboptimal solution but it's good enough for now. We should keep track of each team's VC in the database
            if discord_channel.name == game.team0_name:
                be_voice_channel = discord_channel
            elif discord_channel.name == game.team1_name:
                ds_voice_channel = discord_channel

    coroutines = []
    coroutines.append(message.channel.send(embed=embed))
    # update the embed in the game channel
    if game.message_id and game.channel_id:
        game_channel = bot.get_channel(game.channel_id)
        if isinstance(game_channel, TextChannel):
            game_message: discord.PartialMessage = game_channel.get_partial_message(
                game.message_id
            )
            coroutines.append(game_message.edit(embed=embed))

    # send the new discord.Embed to each player
    if be_voice_channel:
        for player_id in team0_player_ids_after:
            coroutines.append(
                send_in_guild_message(
                    guild,
                    player_id,
                    message_content=be_voice_channel.jump_url,
                    embed=embed,
                )
            )
    if ds_voice_channel:
        for player_id in team1_player_ids_after:
            coroutines.append(
                send_in_guild_message(
                    guild,
                    player_id,
                    message_content=ds_voice_channel.jump_url,
                    embed=embed,
                )
            )
    # use gather to run the sends and edit concurrently in the event loop
    try:
        await asyncio.gather(*coroutines)
    except:
        _log.exception("[autosub] Ignoring exception in asyncio.gather:")

    session.commit()


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
    session: sqlalchemy.orm.Session
    with Session() as session:
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
    date_string = datetime.now(timezone.utc).strftime("%Y-%m-%d")
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
    session: sqlalchemy.orm.Session = ctx.session
    embed = discord.Embed(color=discord.Color.green())
    queues_to_del_query = (
        session.query(Queue)
        .join(QueuePlayer)
        .filter(QueuePlayer.player_id == message.author.id)
        .order_by(Queue.ordinal.asc())
    )  # type: ignore

    # TODO: handle DataError here
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
        result = (
            session.query(Player.name)
            .join(QueuePlayer)
            .filter(QueuePlayer.queue_id == queue.id)
            .all()
        )
        player_names: list[str] = [name[0] for name in result] if result else []
        queue_title_str = (
            f"(**{queue.ordinal}**) {queue.name} [{len(player_names)}/{queue.size}]"
        )
        newline = "\n"
        embed.add_field(
            name=queue_title_str,
            value=(
                f">>> {newline.join(player_names)}"
                if player_names
                else "> \n** **"  # creates an empty quote
            ),
            inline=True,
        )

    # TODO: Check deleting by name / ordinal
    # session.query(QueueWaitlistPlayer).filter(
    #     QueueWaitlistPlayer.player_id == message.author.id
    # ).delete()

    if queues_to_del:
        embed_description = f"<@{message.author.id}> removed from **{', '.join([queue.name for queue in queues_to_del])}**"
        embed.color = discord.Color.green()
    else:
        embed_description = f"<@{message.author.id}> no valid queues specified"
        embed.color = discord.Color.red()
    embed.description = embed_description
    embed_fields_len = len(embed.fields)
    if embed_fields_len >= 5 and embed_fields_len % 3 == 2:
        # embeds are allowed 3 "columns" per "row"
        # to line everything up nicely when there's >= 5 fields and only one "column" slot left, we add a blank
        embed.add_field(name="", value="", inline=True)
    await message.channel.send(embed=embed)
    session.commit()
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


# @bot.command()
# @commands.check(is_admin)
# async def imagetest(ctx: Context):
#     await upload_stats_screenshot_selenium(ctx, False)


@bot.command()
@commands.check(is_admin)
async def imagetest2(ctx: Context):
    await upload_stats_screenshot_imgkit_channel(ctx.channel, False)


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
    session: sqlalchemy.orm.Session
    with Session() as session:
        for player in session.query(Player).filter(Player.is_banned == True):
            output += f"\n- {escape_markdown(player.name)}"
    await send_message(message.channel, embed_description=output, colour=Colour.blue())


@bot.command()
@commands.check(is_admin)
async def listchannels(ctx: Context):
    for channel in bot.get_all_channels():
        _log.info(channel.id, channel)  # DEBUG, TRACE?

    await send_message(
        ctx.message.channel,
        embed_description="Check the logs",
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


@bot.tree.command(
    name="movegameplayers",
    description="Moves players in an in progress game to their respective voice channels",
)
@commands.guild_only()
@commands.check(is_admin)
async def movegameplayers(interaction: Interaction, game_id: str):
    """
    Move players in a given in-progress game to the correct voice channels
    """
    assert interaction.guild

    if not config.ENABLE_VOICE_MOVE:
        await interaction.response.send_message(
            embed=Embed(
                description="Voice movement is disabled",
                colour=Colour.red(),
            ),
            ephemeral=True,
        )
        return
    else:
        try:
            await move_game_players(game_id, interaction)
        except Exception:
            await interaction.response.send_message(
                embed=Embed(
                    description=f"Failed to move players to voice channels for game {game_id}",
                    colour=Colour.red(),
                ),
            )
        else:
            await interaction.response.send_message(
                embed=Embed(
                    description=f"Players moved to voice channels for game {game_id}",
                    colour=Colour.blue(),
                ),
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


@bot.tree.command(
    name="gamehistory",
    description="Privately displays your game history",
)
@discord.app_commands.guild_only()
async def gamehistory(interaction: Interaction, count: int):
    assert interaction.guild
    if count > 10:
        await interaction.response.send_message(
            embed=Embed(
                description="Count cannot exceed 10",
                color=Colour.red(),
            ),
            ephemeral=True,
        )
        return
    elif count < 1:
        await interaction.response.send_message(
            embed=Embed(
                description="Count cannot be less than 1",
                color=Colour.red(),
            ),
            ephemeral=True,
        )
        return

    await interaction.response.defer(ephemeral=True, thinking=True)
    session: SQLAlchemySession
    with Session() as session:
        finished_games: list[FinishedGame]
        finished_games = (
            session.query(FinishedGame)
            .join(
                FinishedGamePlayer,
                FinishedGamePlayer.finished_game_id == FinishedGame.id,
            )
            .filter(FinishedGamePlayer.player_id == interaction.user.id)
            .order_by(FinishedGame.finished_at.desc())
            .limit(count)
            .all()
        )
        if not finished_games:
            await interaction.followup.send(
                embed=Embed(
                    description=f"{interaction.user.mention} has not played any games",
                ),
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        embeds = []
        finished_games.reverse()  # show most recent games last
        for finished_game in finished_games:
            # TODO: bold the callers name to make their name easier to see in the embed
            embed: discord.Embed = create_finished_game_embed(
                session, finished_game.id, interaction.guild.id
            )
            embed.timestamp = finished_game.finished_at
            embeds.append(embed)

        await interaction.followup.send(
            content=f"Last {count} games for {interaction.user.mention}",
            embeds=embeds,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
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


@bot.tree.command(
    name="resetleaderboardchannel", description="Resets & updates the leaderboards"
)
@commands.check(is_admin)
async def resetleaderboardchannel(interaction: Interaction):
    if not config.LEADERBOARD_CHANNEL:
        await interaction.response.send_message(
            "Leaderboard channel ID not configured", ephemeral=True
        )
        return
    channel: TextChannel = bot.get_channel(config.LEADERBOARD_CHANNEL)
    if not channel:
        await interaction.response.send_message(
            "Could not find leaderboard channel, check ID", ephemeral=True
        )
        return

    await interaction.response.defer()
    try:
        await channel.purge()
        await print_leaderboard()
    except:
        await interaction.followup.send(
            embed=Embed(
                description="Leaderboard failed to reset",
                colour=Colour.red(),
            )
        )
    else:
        await interaction.followup.send(
            embed=Embed(
                description="Leaderboard channel reset",
                colour=Colour.green(),
            )
        )


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
    assert interaction.guild
    session: sqlalchemy.orm.Session
    with Session() as session:
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

        if ipg.code == code:
            await interaction.response.send_message(
                embed=Embed(
                    description="This is already the current game code!",
                    colour=Colour.red(),
                ),
                ephemeral=True,
            )
            return
        ipg.code = code
        await interaction.response.defer(ephemeral=True)
        title: str = f"Lobby code for ({short_uuid(ipg.id)})"
        if ipg.channel_id and ipg.message_id:
            partial_message = get_guild_partial_message(
                interaction.guild, ipg.channel_id, ipg.message_id
            )
            channel = interaction.guild.get_channel(ipg.channel_id)
            if isinstance(channel, discord.TextChannel):
                try:
                    message: discord.Message = await channel.fetch_message(
                        ipg.message_id
                    )
                    if len(message.embeds) > 0:
                        embed: discord.Embed = message.embeds[0]
                        replaced_code = False
                        for i, field in enumerate(embed.fields):
                            if field.name == "🔢 Game Code":
                                field.value = f"`{code}`"
                                embed.set_field_at(
                                    i,
                                    name="🔢 Game Code",
                                    value=f"`{code}`",
                                    inline=True,
                                )
                                replaced_code = True
                                break
                        if not replaced_code:
                            last = embed.fields[-1]
                            if (
                                last.name == ""
                                and last.value == ""
                                and last.inline == True
                            ):
                                embed.remove_field(-1)
                            embed.add_field(
                                name="🔢 Game Code", value=f"`{code}`", inline=True
                            )
                            embed_fields_len = (
                                len(embed.fields) - 3
                            )  # subtract team0, team1, and "newline" fields
                            if embed_fields_len >= 5 and embed_fields_len % 3 == 2:
                                # embeds are allowed 3 "columns" per "row"
                                # to line everything up nicely when there's >= 5 fields and only one "column" slot left, we add a blank
                                embed.add_field(name="", value="", inline=True)
                        await message.edit(embed=embed)
                except:
                    _log.exception(
                        f"[setgamecode] Failed to get message with guild_id={interaction.guild_id}, channel_id={ipg.channel_id}, message_id={ipg.message_id}:"
                    )
            if partial_message:
                title = f"Lobby code for {partial_message.jump_url}"

        embed = Embed(
            title=title,
            description=f"`{code}`",
            colour=Colour.green(),
        )
        embed.set_footer(
            text=f"set by {interaction.user.display_name} ({interaction.user.name})"
        )
        coroutines = []
        result = (
            session.query(InProgressGamePlayer.player_id)
            .filter(
                InProgressGamePlayer.in_progress_game_id == ipg.id,
                InProgressGamePlayer.player_id
                != interaction.user.id,  # don't send the code to the one who wants to send it out
            )
            .all()
        )
        ipg_player_ids: list[int] = (
            [player_id[0] for player_id in result if player_id] if result else []
        )
        for player_id in ipg_player_ids:
            coroutines.append(
                send_in_guild_message(interaction.guild, player_id, embed=embed)
            )
        if ipg_player_ids:
            try:
                await asyncio.gather(*coroutines)
            except:
                _log.exception("[setgamecode] Ignoring exception in asyncio.gather:")
            else:
                await interaction.followup.send(
                    embed=Embed(
                        description="Lobby code sent to each player",
                        colour=Colour.blue(),
                    ),
                    ephemeral=True,
                )
        else:
            _log.warn("No in_progress_game_players to send a lobby code to")
            await interaction.followup.send(
                embed=Embed(
                    description="There are no in-game players to send this lobby code to!",
                    colour=Colour.red(),
                ),
                ephemeral=True,
            )
        session.commit()


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
@commands.is_owner()
async def showgamedebug(ctx: Context, game_id: str):
    player_id = ctx.message.author.id

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
    assert ctx.guild
    session: sqlalchemy.orm.Session
    with Session() as session:
        queue_indices: list[int] = []
        queue_names: list[str] = []
        all_rotations: list[Rotation] = []  # TODO: use sets
        if len(args) == 0:
            all_rotations = (
                session.query(Rotation).order_by(Rotation.created_at.asc()).all()
            )
        else:
            # get the rotation associated to the specified queue
            all_rotations = []
            for arg in args:
                # TODO: avoid looping so you only need one query
                try:
                    queue_index = int(arg)
                    arg_rotation = (
                        session.query(Rotation)
                        .join(Queue)
                        .filter(Queue.ordinal == queue_index)
                        .first()
                    )
                    if arg_rotation:
                        queue_indices.append(queue_index)
                        if arg_rotation not in all_rotations:
                            all_rotations.append(arg_rotation)
                except ValueError:
                    arg_rotation = (
                        session.query(Rotation)
                        .join(Queue)
                        .filter(Queue.name.ilike(arg))
                        .first()
                    )
                    if arg_rotation:
                        queue_names.append(arg)
                        if arg_rotation not in all_rotations:
                            all_rotations.append(arg_rotation)
                except IndexError:
                    pass

        if not all_rotations:
            await ctx.channel.send("No Rotations")
            return

        embed = Embed(title="Queues", color=Colour.blue())
        ipg_embeds: list[Embed] = []
        rotation_queues: list[Queue] | None
        for rotation in all_rotations:
            conditions = [Queue.rotation_id == rotation.id]
            if queue_indices:
                conditions.append(Queue.ordinal.in_(queue_indices))
            if queue_names:
                conditions.append(Queue.name.in_(queue_names))
            rotation_queues = (
                session.query(Queue)
                .filter(*conditions)
                .order_by(Queue.ordinal.asc())
                .all()
            )
            if not rotation_queues:
                continue

            games_by_queue: dict[str, list[InProgressGame]] = defaultdict(list)
            for game in session.query(InProgressGame).filter(
                InProgressGame.is_finished == False
            ):
                if game.queue_id:
                    games_by_queue[game.queue_id].append(game)

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
            next_map_str = f"{next_map.full_name} ({next_map.short_name})"
            if config.ENABLE_RAFFLE:
                has_raffle_reward = next_rotation_map.raffle_ticket_reward > 0
                raffle_reward = (
                    next_rotation_map.raffle_ticket_reward
                    if has_raffle_reward
                    else config.DEFAULT_RAFFLE_VALUE
                )
                next_map_str += f" ({raffle_reward} tickets)"
            embed.add_field(
                name=f"",
                # value="─"*10,
                value=f"```asciidoc\n* {rotation.name}```",
                inline=False,
            )
            embed.add_field(
                name=f"🗺️ Next Map",
                value=next_map_str,
                inline=False,
            )

            rotation_queues_len = len(rotation_queues)
            for i, queue in enumerate(rotation_queues):
                if queue.is_locked:
                    continue
                players_in_queue: list[Player] = (
                    session.query(Player)
                    .join(QueuePlayer)
                    .filter(QueuePlayer.queue_id == queue.id)
                    .all()
                )
                queue_title_str = f"(**{queue.ordinal}**) {queue.name} [{len(players_in_queue)}/{queue.size}]"
                player_display_names: list[str] = (
                    [player.name for player in players_in_queue]
                    if players_in_queue
                    else []
                )
                newline = "\n"  # Escape sequence (backslash) not allowed in expression portion of f-string prior to Python 3.12
                embed.add_field(
                    name=queue_title_str,
                    value=(
                        "> \n** **"  # weird hack to create an empty quote
                        if not player_display_names
                        else f">>> {newline.join(player_display_names)}"
                    ),
                    inline=True,
                )
                if i == rotation_queues_len - 1 and i >= 5 and i % 3 == 2:
                    # embeds are allowed 3 "columns" per "row"
                    # to line everything up nicely when there's >= 5 queues and only one "column" slot left, we add a blank
                    embed.add_field(name="", value="", inline=True)
                if queue.id in games_by_queue:
                    game: InProgressGame
                    for game in games_by_queue[queue.id]:
                        ipg_embed = await create_in_progress_game_embed(
                            session, game, ctx.guild
                        )
                        ipg_embeds.append(ipg_embed)
        await ctx.channel.send(
            embeds=[embed] + ipg_embeds,
        )


def win_rate(wins, losses, ties):
    denominator = max(wins + losses + ties, 1)
    return round(100 * (wins + 0.5 * ties) / denominator, 1)


@bot.tree.command(
    name="stats", description="Privately displays your TrueSkill statistics"
)
async def stats(interaction: Interaction, category_name: Optional[str] | None):
    """
    Replies to the user with their TrueSkill statistics. Can be used both inside and out of a Guild
    """
    session: sqlalchemy.orm.Session
    with Session() as session:
        player: Player | None = (
            session.query(Player).filter(Player.id == interaction.user.id).first()
        )
        if not player:
            # Edge case where user has no record in the Players table
            await interaction.response.send_message(
                "You have not played any games", ephemeral=True
            )
            return
        if not player.stats_enabled:
            await interaction.response.send_message(
                "You have disabled `/stats`",
                ephemeral=True,
            )
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
            return

        finished_game_ids: List[str] | None = [fgp.finished_game_id for fgp in fgps]
        fgs: List[FinishedGame] | None = (
            session.query(FinishedGame)
            .filter(FinishedGame.id.in_(finished_game_ids))
            .all()
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
                    if fg.finished_at.replace(tzinfo=timezone.utc)
                    > datetime.now(timezone.utc) - timedelta(days=n)
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
                winrate = round(win_rate(num_wins, num_losses, num_ties))
                col = [
                    "Total" if num_days == -1 else f"{num_days}D",
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
        conditions = []
        conditions.append(PlayerCategoryTrueskill.player_id == player.id)
        if category_name:
            conditions.append(Category.name == category_name)
        player_category_trueskills: list[PlayerCategoryTrueskill] | None = (
            session.query(PlayerCategoryTrueskill)
            .join(Category)
            .filter(*conditions)
            .order_by(Category.name)
            .all()
        )
        # assume that if a guild uses categories, they will use them exclusively, i.e., no mixing categorized and uncategorized queues
        if player_category_trueskills:
            num_pct = len(player_category_trueskills)
            for i, pct in enumerate(player_category_trueskills):
                category: Category | None = (
                    session.query(Category)
                    .filter(Category.id == pct.category_id)
                    .first()
                )
                if not category:
                    # should never happen
                    _log.error(
                        f"No Category found for player_category_trueskill with id {pct.id}"
                    )
                    await interaction.response.send_message(
                        embed=Embed(description="Could not find your stats")
                    )
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
                    header=["Last", "W", "L", "T", "Total", "WR"],
                    body=cols,
                    first_col_heading=True,
                    style=PresetStyle.plain,
                    alignments=[
                        Alignment.LEFT,
                        Alignment.DECIMAL,
                        Alignment.DECIMAL,
                        Alignment.DECIMAL,
                        Alignment.DECIMAL,
                        Alignment.RIGHT,
                    ],
                )
                description += code_block(table)
                embed = Embed(title=title, description=description)
                if i == (num_pct - 1):
                    description += f"\n{trueskill_url}"
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
                header=["Period", "Wins", "Losses", "Ties", "Total", "Win %"],
                body=cols,
                first_col_heading=True,
                style=PresetStyle.plain,
                alignments=[
                    Alignment.LEFT,
                    Alignment.DECIMAL,
                    Alignment.DECIMAL,
                    Alignment.DECIMAL,
                    Alignment.DECIMAL,
                    Alignment.DECIMAL,
                ],
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
        except Exception:
            _log.exception(f"Caught exception trying to send stats message")


@stats.autocomplete("category_name")
async def stats_autocomplete(interaction: discord.Interaction, current: str):
    choices = []
    session: sqlalchemy.orm.Session
    with Session() as session:
        result = (
            session.query(Category.name, PlayerCategoryTrueskill.player_id)
            .join(PlayerCategoryTrueskill)
            .filter(PlayerCategoryTrueskill.player_id == interaction.user.id)
            .order_by(Category.name)
            .limit(25)  # discord only supports up to 25 choices
            .all()
        )
        _log.info(result)
        category_names: list[str] = [r[0] for r in result] if result else []
        _log.info(category_names)
        for name in category_names:
            if current in name:
                choices.append(
                    discord.app_commands.Choice(
                        name=name,
                        value=name,
                    )
                )
    _log.info(choices)
    return choices


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
    assert message.guild
    assert message.channel
    queue: Queue = session.query(Queue).filter(Queue.id == game.queue_id).first()

    game_players = (
        session.query(InProgressGamePlayer)
        .filter(InProgressGamePlayer.in_progress_game_id == game.id)
        .all()
    )
    player_ids: list[int] = list(map(lambda x: x.player_id, game_players))
    """
    # run get_even_teams in a separate process, so that it doesn't block the event loop
    loop: asyncio.AbstractEventLoop = asyncio.get_running_loop()
    with concurrent.futures.ProcessPoolExecutor() as pool:
        result = await loop.run_in_executor(
            pool,
            get_even_teams,
            player_ids,
            len(player_ids) // 2,
            queue.is_rated,
            queue.category_id,
        )
        players = result[0]
        win_prob = result[1]
    """
    players, win_prob = get_even_teams(
        player_ids, len(player_ids) // 2, queue.is_rated, queue.category_id
    )
    for game_player in game_players:
        session.delete(game_player)
    game.win_probability = win_prob
    team0_players = players[: len(players) // 2]
    team1_players = players[len(players) // 2 :]
    for player in team0_players:
        game_player = InProgressGamePlayer(
            in_progress_game_id=game.id,
            player_id=player.id,
            team=0,
        )
        session.add(game_player)
    for player in team1_players:
        game_player = InProgressGamePlayer(
            in_progress_game_id=game.id,
            player_id=player.id,
            team=1,
        )
        session.add(game_player)

    session.commit()

    if config.ECONOMY_ENABLED:
        try:
            economy_cog = bot.get_cog("EconomyCommands")
            if economy_cog is not None and isinstance(economy_cog, EconomyCommands):
                await economy_cog.cancel_predictions(game.id)
            else:
                _log.warning("Could not get EconomyCommands cog")
        except ValueError as ve:
            # Raised if there are no predictions on this game
            await send_message(
                message.channel,
                content="",
                embed_description="No predictions to be refunded",
                colour=Colour.blue(),
            )
        except Exception:
            _log.exception("Caught Exception when canceling predicitons")
            await send_message(
                message.channel,
                content="",
                embed_description="Predictions failed to refund",
                colour=Colour.blue(),
            )
        else:
            await send_message(
                message.channel,
                content="",
                embed_description="Predictions refunded",
                colour=Colour.blue(),
            )

    short_game_id: str = short_uuid(game.id)
    if config.ENABLE_VOICE_MOVE:
        if queue.move_enabled:
            await move_game_players(short_game_id, None, message.guild)
            await send_message(
                message.channel,
                embed_description=f"Players moved to new team voice channels for game {short_game_id}",
                colour=Colour.green(),
            )


@bot.command()
async def sub(ctx: Context, member: Member):
    message = ctx.message
    """
    Substitute one player in a game for another
    """
    session = ctx.session
    guild = ctx.guild
    assert guild
    caller = message.author
    if message.author.id == member.id:
        await send_message(
            channel=message.channel,
            embed_description=f"You cannot sub yourself",
            colour=Colour.red(),
        )
        return
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

    game: InProgressGame | None = callee_game or caller_game
    if not game:
        return

    # get player_names per team before the swap
    results = (
        session.query(Player.id, Player.name)
        .join(InProgressGamePlayer)
        .filter(
            InProgressGamePlayer.in_progress_game_id == game.id,
            InProgressGamePlayer.team == 0,
        )
        .all()
    )
    # skip copying the player_ids
    team0_player_names_before: list[str] = (
        [res[1] for res in results if res] if results else []
    )
    results = (
        session.query(Player.id, Player.name)
        .join(InProgressGamePlayer)
        .filter(
            InProgressGamePlayer.in_progress_game_id == game.id,
            InProgressGamePlayer.team == 1,
        )
        .all()
    )
    # skip copying the player_ids
    team1_player_names_before: list[str] = (
        [res[1] for res in results if res] if results else []
    )

    await _rebalance_game(game, session, message)
    embed: discord.Embed = await create_in_progress_game_embed(session, game, guild)
    short_game_id: str = short_uuid(game.id)
    embed.title = f"New Teams for Game {short_game_id})"
    embed.description = (
        f"Substituted **{caller.display_name}** in for **{callee.display_name}**"
    )
    embed.color = discord.Color.yellow()
    results = (
        session.query(Player.id, Player.name)
        .join(InProgressGamePlayer)
        .filter(
            InProgressGamePlayer.in_progress_game_id == game.id,
            InProgressGamePlayer.team == 0,
        )
        .all()
    )
    team0_player_ids_after: list[int] = (
        [res[0] for res in results if res] if results else []
    )
    team0_player_names_after: list[str] = [res[1] for res in results] if results else []
    results = (
        session.query(Player.id, Player.name)
        .join(InProgressGamePlayer)
        .filter(
            InProgressGamePlayer.in_progress_game_id == game.id,
            InProgressGamePlayer.team == 1,
        )
        .all()
    )
    team1_player_ids_after: list[int] = [res[0] for res in results] if results else []
    team1_player_names_after: list[str] = [res[1] for res in results] if results else []
    team0_diff_vaules, team1_diff_values = get_team_name_diff(
        team0_player_names_before,
        team0_player_names_after,
        team1_player_names_before,
        team1_player_names_after,
    )
    for i, field in enumerate(embed.fields):
        # brute force iteration through the embed's fields until we find the original team0 and team1 embeds to update
        if field.name == f"⬅️ {game.team0_name} ({round(100 * game.win_probability)}%)":
            embed.set_field_at(
                i,
                name=f"⬅️ {game.team0_name} ({round(100 * game.win_probability)}%)",
                value=team0_diff_vaules,
                inline=True,
            )
        if (
            field.name
            == f"➡️ {game.team1_name} ({round(100 * (1 - game.win_probability))}%)"
        ):
            embed.set_field_at(
                i,
                name=f"➡️ {game.team1_name} ({round(100 * (1 - game.win_probability))}%)",
                value=team1_diff_values,
                inline=True,
            )
    be_voice_channel: discord.VoiceChannel | None = None
    ds_voice_channel: discord.VoiceChannel | None = None
    ipg_channels: list[InProgressGameChannel] | None = (
        session.query(InProgressGameChannel)
        .filter(InProgressGameChannel.in_progress_game_id == game.id)
        .all()
    )
    for ipg_channel in ipg_channels or []:
        discord_channel: discord.abc.GuildChannel | None = guild.get_channel(
            ipg_channel.channel_id
        )
        if isinstance(discord_channel, discord.VoiceChannel):
            # This is suboptimal solution but it's good enough for now. We should keep track of each team's VC in the database
            if discord_channel.name == game.team0_name:
                be_voice_channel = discord_channel
            elif discord_channel.name == game.team1_name:
                ds_voice_channel = discord_channel

    coroutines = []
    coroutines.append(message.channel.send(embed=embed))
    # update the embed in the game channel
    if game.message_id and game.channel_id:
        game_channel = bot.get_channel(game.channel_id)
        if isinstance(game_channel, TextChannel):
            game_message: discord.PartialMessage = game_channel.get_partial_message(
                game.message_id
            )
            coroutines.append(game_message.edit(embed=embed))

    # send the new discord.Embed to each player
    if be_voice_channel:
        for player_id in team0_player_ids_after:
            coroutines.append(
                send_in_guild_message(
                    guild,
                    player_id,
                    message_content=be_voice_channel.jump_url,
                    embed=embed,
                )
            )
    if ds_voice_channel:
        for player_id in team1_player_ids_after:
            coroutines.append(
                send_in_guild_message(
                    guild,
                    player_id,
                    message_content=ds_voice_channel.jump_url,
                    embed=embed,
                )
            )
    # use gather to run the sends and edit concurrently in the event loop
    try:
        await asyncio.gather(*coroutines)
    except:
        _log.exception("[autosub] Ignoring exception in asyncio.gather:")
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


@bot.command()
@commands.guild_only()
@commands.check(is_admin)
# @commands.is_owner() # TODO: In a multi-guild context, use this instead
async def sync(
    ctx: Context,
    guilds: commands.Greedy[discord.Object],
    spec: Optional[Literal["~", "*", "^"]] = None,
) -> None:
    """
    Do not use this command frequently, as it could get the bot rate limited
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
