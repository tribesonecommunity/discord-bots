import asyncio
import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from itertools import combinations
from math import floor
from random import choice, shuffle, uniform
from tempfile import NamedTemporaryFile
from typing import List, Literal, Optional

import discord
import imgkit
import sqlalchemy
from discord import (
    CategoryChannel,
    Colour,
    DMChannel,
    Embed,
    GroupChannel,
    Message,
    TextChannel,
)
from discord.ext import commands
from discord.ext.commands.context import Context
from discord.guild import Guild
from discord.member import Member
from discord.utils import escape_markdown
from PIL import Image
from sqlalchemy import and_, func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.session import Session as SQLAlchemySession
from trueskill import Rating

import discord_bots.config as config
from discord_bots.checks import is_admin
from discord_bots.utils import (
    add_empty_field,
    create_condensed_in_progress_game_embed,
    create_in_progress_game_embed,
    del_player_from_queues_and_waitlists,
    get_player_game,
    get_team_name_diff,
    get_team_voice_channels,
    is_in_game,
    mean,
    move_game_players,
    send_in_guild_message,
    send_message,
    short_uuid,
    update_next_map_to_map_after_next,
    upload_stats_screenshot_imgkit_channel,
    win_probability_matchmaking,
)

from .bot import bot
from .cogs.economy import EconomyCommands
from .cogs.in_progress_game import InProgressGameCommands, InProgressGameView
from .models import (
    Category,
    FinishedGame,
    FinishedGamePlayer,
    InProgressGame,
    InProgressGameChannel,
    InProgressGamePlayer,
    Map,
    Player,
    PlayerCategoryTrueskill,
    Queue,
    QueueNotification,
    QueuePlayer,
    QueueRole,
    QueueWaitlist,
    QueueWaitlistPlayer,
    Rotation,
    RotationMap,
    Session,
    VotePassedWaitlist,
    VotePassedWaitlistPlayer,
)
from .names import generate_be_name, generate_ds_name
from .queues import AddPlayerQueueMessage, add_player_queue, waitlist_messages
from .twitch import twitch

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
        # Shuffling is important! This ensures captains are randomly distributed!
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
            win_prob = win_probability_matchmaking(team0_ratings, team1_ratings)
            current_team_evenness = abs(0.50 - win_prob)
            best_team_evenness_so_far = abs(0.50 - best_win_prob_so_far)
            if current_team_evenness < best_team_evenness_so_far:
                best_win_prob_so_far = win_prob
                best_teams_so_far = list(team0[:]) + list(team1[:])
            if best_team_evenness_so_far < 0.001:
                break

        _log.debug(
            f"Found team evenness: {best_team_evenness_so_far} interations: {i}"
        )
        return best_teams_so_far, best_win_prob_so_far


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
            win_prob = 0.0
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
                f"🔴 {game.team0_name}", category=category_channel
            )
            ds_voice_channel = await guild.create_voice_channel(
                f"🔵 {game.team1_name}", category=category_channel
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
            """TODO: find a way to add this while keeping the embed compact
            embed.add_field(
                name="📺 Channel", value=match_channel.jump_url, inline=True
            )
            add_empty_field(embed, offset=3)
            """
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

        in_progress_game_cog = bot.get_cog("InProgressGameCommands")
        if (
            in_progress_game_cog is not None
            and isinstance(in_progress_game_cog, InProgressGameCommands)
            and match_channel
        ):
            message = await match_channel.send(
                embed=embed, view=InProgressGameView(game.id, in_progress_game_cog)
            )
            game.message_id = message.id
        else:
            _log.warning("Could not get InProgressGameCommands")

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
                added_at=discord.utils.utcnow(),
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
    embed: discord.Embed = await create_in_progress_game_embed(
        session, game, guild, False
    )
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
        if field.name and game.team0_name in field.name:
            embed.set_field_at(
                i,
                name=f"🔴 {game.team0_name} ({round(100 * game.win_probability, 1)}%)",
                value=team0_diff_vaules,
                inline=True,
            )
        if field.name and game.team1_name in field.name:
            embed.set_field_at(
                i,
                name=f"🔵 {game.team1_name} ({round(100 * (1 - game.win_probability), 1)}%)",
                value=team1_diff_values,
                inline=True,
            )
    be_voice_channel: discord.VoiceChannel | None
    ds_voice_channel: discord.VoiceChannel | None
    be_voice_channel, ds_voice_channel = get_team_voice_channels(session, game, guild)

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


@bot.command(name="del")
async def del_(ctx: Context, *args):
    """
    Players deletes self from queue(s)

    If no args deletes from existing queues
    """
    message = ctx.message
    embed = discord.Embed()
    session: SQLAlchemySession
    with Session() as session:
        queues_del_from = del_player_from_queues_and_waitlists(
            session, ctx.author.id, *args
        )
        for queue in queues_del_from:
            # TODO: generify this into queue status util
            players_in_queue = (
                session.query(Player)
                .join(
                    QueuePlayer,
                    and_(
                        QueuePlayer.queue_id == queue.id,
                        QueuePlayer.player_id == Player.id,
                    ),
                )
                .order_by(QueuePlayer.added_at.asc())
                .all()
            )
            player_names: list[str] = [player.name for player in players_in_queue]
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
        if queues_del_from:
            embed.description = f"**{ctx.author.display_name}** removed from **{', '.join([queue.name for queue in queues_del_from])}**"
            embed.color = discord.Color.green()
            add_empty_field(embed)
            await message.channel.send(embed=embed)
            session.commit()



# @bot.command()
# @commands.check(is_admin)
# async def imagetest(ctx: Context):
#     await upload_stats_screenshot_selenium(ctx, False)


@bot.command()
@commands.check(is_admin)
async def imagetest2(ctx: Context):
    await upload_stats_screenshot_imgkit_channel(ctx.channel, False)


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
            conditions = [Queue.rotation_id == rotation.id, Queue.is_locked == False]
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
            rotation_queues_len = len(rotation_queues)
            if rotation_queues_len:
                embed.add_field(
                    name=f"",
                    value=f"```asciidoc\n* {rotation.name}```",
                    inline=False,
                )
                embed.add_field(
                    name=f"🗺️ Next Map",
                    value=next_map_str,
                    inline=False,
                )

            for i, queue in enumerate(rotation_queues):
                if queue.is_locked:
                    continue
                players_in_queue: list[Player] = (
                    session.query(Player)
                    .join(QueuePlayer)
                    .filter(QueuePlayer.queue_id == queue.id)
                    .order_by(QueuePlayer.added_at.asc())
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
                if (i + 1) == rotation_queues_len and (i + 1) >= 5 and (i + 1) % 3 == 2:
                    # we have to do this "inline", since there can be multiple sets of queues per rotation in a single embed
                    # embeds are allowed 3 "columns" per "row"
                    # to line everything up nicely when there's >= 5 queues and only one "column" slot left, we add a blank
                    embed.add_field(name="", value="", inline=True)
                if queue.id in games_by_queue:
                    game: InProgressGame
                    for game in games_by_queue[queue.id]:
                        ipg_embed = await create_condensed_in_progress_game_embed(
                            session,
                            game,
                        )
                        ipg_embeds.append(ipg_embed)
        await ctx.channel.send(
            embeds=[embed] + ipg_embeds,
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
    embed: discord.Embed = await create_in_progress_game_embed(
        session, game, guild, False
    )
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
        if field.name and game.team0_name in field.name:
            embed.set_field_at(
                i,
                name=f"🔴 {game.team0_name} ({round(100 * game.win_probability, 1)}%)",
                value=team0_diff_vaules,
                inline=True,
            )
        if field.name and game.team1_name in field.name:
            embed.set_field_at(
                i,
                name=f"🔵 {game.team1_name} ({round(100 * (1 - game.win_probability), 1)}%)",
                value=team1_diff_values,
                inline=True,
            )
    be_voice_channel: discord.VoiceChannel | None
    ds_voice_channel: discord.VoiceChannel | None
    be_voice_channel, ds_voice_channel = get_team_voice_channels(session, game, guild)

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
