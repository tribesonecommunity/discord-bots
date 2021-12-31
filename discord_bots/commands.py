from collections import defaultdict
from datetime import datetime, timedelta, timezone
from math import floor
from random import randint, random, shuffle
from threading import Timer
from typing import Awaitable, Callable
import itertools
import math
import numpy

from discord import Colour, DMChannel, Embed, GroupChannel, TextChannel, Message
from discord.guild import Guild
from sqlalchemy.exc import IntegrityError
from trueskill import Rating, global_env, rate

from .models import (
    AdminRole,
    FinishedGame,
    FinishedGamePlayer,
    InProgressGame,
    InProgressGameChannel,
    InProgressGamePlayer,
    Player,
    Queue,
    QueuePlayer,
    QueueRole,
    QueueWaitlistPlayer,
    Session,
)
from .queues import (
    QUEUE_WAITLIST,
    QueueWaitlistQueueMessage,
)

AFK_TIME_MINUTES: int = 45
COMMAND_PREFIX: str = "$"
RE_ADD_DELAY: int = 5


def win_probability(team0: list[Rating], team1: list[Rating]) -> float:
    """
    Calculate the probability that team0 beats team1
    Taken from https://trueskill.org/#win-probability
    """
    BETA = 4.1666
    delta_mu = sum(r.mu for r in team0) - sum(r.mu for r in team1)
    sum_sigma = sum(r.sigma ** 2 for r in itertools.chain(team0, team1))
    size = len(team0) + len(team1)
    denom = math.sqrt(size * (BETA * BETA) + sum_sigma)
    trueskill = global_env()
    return round(trueskill.cdf(delta_mu / denom), 2)


def get_even_teams(player_ids: list[int]) -> tuple[list[Player], float]:
    """
    Try to figure out even teams, the first half of the returning list is
    the first team, the second half is the second team.

    :returns: list of players and win probability for the first team
    """
    session = Session()
    players: list[Player] = (
        session.query(Player).filter(Player.id.in_(player_ids)).all()  # type: ignore
    )
    best_win_prob_so_far: float | None = None
    best_teams_so_far: list[Player] | None = None

    # Use a fixed number of shuffles instead of generating permutations. There
    # are 3.6 million permutations!
    for _ in range(300):
        shuffle(players)
        team0_ratings = list(
            map(
                lambda x: Rating(x.trueskill_mu, x.trueskill_sigma),
                players[: len(players) // 2],
            )
        )
        team1_ratings = list(
            map(
                lambda x: Rating(x.trueskill_mu, x.trueskill_sigma),
                players[len(players) // 2 :],
            )
        )
        win_prob = win_probability(team0_ratings, team1_ratings)
        if not best_win_prob_so_far:
            best_win_prob_so_far = win_prob
            best_teams_so_far = players[:]
        else:
            current_team_evenness = abs(0.50 - win_prob)
            best_team_evenness_so_far = abs(0.50 - best_win_prob_so_far)
            if current_team_evenness < best_team_evenness_so_far:
                best_win_prob_so_far = win_prob
                best_teams_so_far = players[:]

            if int(win_prob * 100) == 50:
                # Can't do better than this
                break

    if not best_win_prob_so_far or not best_teams_so_far:
        # Can't really happen, so mostly just to appease the linter
        return [], 0.0

    return best_teams_so_far, best_win_prob_so_far


async def add_player_to_queue(
    queue_id: str,
    player_id: int,
    channel: TextChannel | DMChannel | GroupChannel,
    guild: Guild,
) -> tuple[bool, bool]:
    """
    Helper function to add player to a queue and pop if needed.

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
    if len(queue_players) == queue.size:  # Pop!
        player_ids: list[int] = list(map(lambda x: x.player_id, queue_players))
        players, win_prob = get_even_teams(player_ids)
        game = InProgressGame(queue_id=queue.id, win_probability=win_prob)
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

        short_game_id = game.id.split("-")[0]
        team0_names = sorted(map(lambda x: x.name, team0_players))
        team1_names = sorted(map(lambda x: x.name, team1_players))
        channel_message = f"Game '{queue.name}' ({short_game_id}) has begun!"
        channel_embed = f"**Blood Eagle** ({int(100 * win_prob)}%): {', '.join(team0_names)}\n**Diamond Sword** ({int(100 * (1 - win_prob))}%): {', '.join(team1_names)}"

        await send_message(
            channel,
            content=channel_message,
            embed_description=channel_embed,
            colour=Colour.blue(),
        )

        categories = {category.id: category for category in guild.categories}
        tribes_voice_category = categories[TRIBES_VOICE_CATEGORY_CHANNEL_ID]

        be_channel = await guild.create_voice_channel(
            f"Blood Eagle ({short_game_id})",
            category=tribes_voice_category,
        )
        ds_channel = await guild.create_voice_channel(
            f"Diamond Sword ({short_game_id})",
            category=tribes_voice_category,
        )
        session.add(
            InProgressGameChannel(in_progress_game_id=game.id, channel_id=be_channel.id)
        )
        session.add(
            InProgressGameChannel(in_progress_game_id=game.id, channel_id=ds_channel.id)
        )

        session.query(QueuePlayer).filter(QueuePlayer.queue_id == queue_id).delete()
        session.commit()
        return True, True
    return True, False


def add_queue_waitlist_message(
    channel: TextChannel | DMChannel | GroupChannel,
    guild: Guild,
    finished_game_id: str,
) -> None:
    """
    Put a message onto a queue to handle the game queue waitlist.

    We use a queue here so that it happens on the main thread. Sqlite doesn't
    handle concurrency well and by default blocks actions from separate threads.
    """
    QUEUE_WAITLIST.put(QueueWaitlistQueueMessage(channel, guild, finished_game_id))


async def send_message(
    channel: (DMChannel | GroupChannel | TextChannel),
    content: str = None,
    embed_description: str = None,
    colour: Colour = None,
    embed_content: bool = True,
):
    """
    :colour: red = fail, green = success, blue = informational
    """
    if content:
        if embed_content:
            content = f"`{content}`"
    embed = None
    if embed_description:
        embed = Embed(description=embed_description)
        if colour:
            embed.colour = colour
    try:
        await channel.send(content=content, embed=embed)
    except Exception as e:
        print("[send_message] exception:", e)


def require_admin(command_func: Callable[[Message, list[str]], Awaitable[None]]):
    """
    Decorator to wrap functions that require being called by an admin
    """

    async def wrapper(*args, **kwargs):
        session = Session()
        message: Message = args[0]
        caller = (
            session.query(Player)
            .filter(Player.id == message.author.id, Player.is_admin == True)
            .first()
        )
        if caller:
            await command_func(*args, **kwargs)
            return

        if not message.guild:
            print("No message guild?")
            return

        member = message.guild.get_member(message.author.id)
        if not member:
            return

        admin_roles = session.query(AdminRole).all()
        admin_role_ids = map(lambda x: x.role_id, admin_roles)
        member_role_ids = map(lambda x: x.id, member.roles)
        is_admin: bool = len(set(admin_role_ids).intersection(set(member_role_ids))) > 0
        if is_admin:
            await command_func(*args, **kwargs)
        else:
            await send_message(
                message.channel,
                embed_description="You must be an admin to use that command",
                colour=Colour.red(),
            )

    return wrapper


TRIBES_VOICE_CATEGORY_CHANNEL_ID: int = 462824101753520138


def is_in_game(player_id: int) -> bool:
    return get_player_game(player_id, Session()) is not None


def get_player_game(player_id: int, session=Session()) -> InProgressGame | None:
    """
    Find the game a player is currently in

    :session: Pass in a session if you want to do something with the game that
    gets returned
    """
    game_players = (
        session.query(InProgressGamePlayer)
        .join(InProgressGame)
        .filter(InProgressGamePlayer.player_id == player_id)
        .all()
    )
    if len(game_players) > 0:
        return (
            session.query(InProgressGame)
            .filter(InProgressGame.id == InProgressGamePlayer.in_progress_game_id)
            .first()
        )
    else:
        return None


# Commands start here


async def add(message: Message, args: list[str]):
    """
    Players adds self to queue(s). If no args to all existing queues

    TODO:
    - Queue eligibility
    """
    session = Session()
    if is_in_game(message.author.id):
        await send_message(
            message.channel,
            embed_description=f"{message.author} you are already in a game",
            colour=Colour.red(),
        )
        return

    most_recent_game: FinishedGame | None = (
        session.query(FinishedGame)
        .join(FinishedGamePlayer)
        .filter(
            FinishedGamePlayer.player_id == message.author.id,
        )
        .order_by(FinishedGame.finished_at.desc())  # type: ignore
        .first()
    )

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

    queues_to_add: list[Queue] = []
    if len(args) == 0:
        for queue in session.query(Queue):
            if queue and not queue.is_locked:
                queues_to_add.append(queue)
    else:
        for queue_name in args:
            queue: Queue | None = session.query(Queue).filter(Queue.name.ilike(queue_name)).first()  # type: ignore
            if queue:
                if not queue.is_locked:
                    queues_to_add.append(queue)
            else:
                await send_message(
                    message.channel,
                    embed_description=f"Could not find queue: {queue_name}",
                    colour=Colour.red(),
                )

    if len(queues_to_add) == 0:
        await send_message(
            message.channel,
            content="No valid queues found",
            colour=Colour.red(),
        )
        return

    queues_added_to = []
    for queue in queues_to_add:
        if is_waitlist and most_recent_game:
            session.add(
                QueueWaitlistPlayer(
                    finished_game_id=most_recent_game.id,
                    queue_id=queue.id,
                    player_id=message.author.id,
                )
            )
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
        else:
            if message.guild:
                added_to_queue, queue_popped = await add_player_to_queue(
                    queue.id, message.author.id, message.channel, message.guild
                )
                if queue_popped:
                    return
                if added_to_queue:
                    queues_added_to.append(queue)

    queue_statuses = []
    queue: Queue
    for queue in session.query(Queue):
        queue_players = (
            session.query(QueuePlayer).filter(QueuePlayer.queue_id == queue.id).all()
        )

        queue_statuses.append(f"{queue.name} [{len(queue_players)}/{queue.size}]")

    if is_waitlist and waitlist_message:
        await send_message(
            message.channel,
            content=f"{message.author.name} added to: {', '.join([queue.name for queue in queues_added_to])}",
            embed_description=waitlist_message,
            colour=Colour.green(),
        )
    else:
        await send_message(
            message.channel,
            content=f"{message.author.name} added to: {', '.join([queue.name for queue in queues_added_to])}",
            embed_description=" ".join(queue_statuses),
            colour=Colour.green(),
        )
    session.commit()


@require_admin
async def add_admin(message: Message, args: list[str]):
    if len(args) != 1 or len(message.mentions) == 0:
        await send_message(
            message.channel,
            embed_description="Usage: !addadmin <player_name>",
            colour=Colour.red(),
        )
        return

    session = Session()
    players = session.query(Player).filter(Player.id == message.mentions[0].id).all()
    if len(players) == 0:
        session.add(
            Player(
                id=message.mentions[0].id, name=message.mentions[0].name, is_admin=True
            )
        )
        await send_message(
            message.channel,
            embed_description=f"{message.mentions[0].name} added to admins",
            colour=Colour.green(),
        )
        session.commit()
    else:
        player = players[0]
        if player.is_admin:
            await send_message(
                message.channel,
                embed_description=f"{player.name} is already an admin",
                colour=Colour.red(),
            )
        else:
            player.is_admin = True
            session.commit()
            await send_message(
                message.channel,
                embed_description=f"{player.name} added to admins",
                colour=Colour.green(),
            )


@require_admin
async def add_admin_role(message: Message, args: list[str]):
    if len(args) != 1:
        await send_message(
            message.channel,
            embed_description="Usage: !addadminrole <role_name>",
            colour=Colour.red(),
        )
        return

    role_name = args[0]
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


@require_admin
async def add_queue_role(message: Message, args: list[str]):
    if len(args) != 2:
        await send_message(
            message.channel,
            embed_description="Usage: !addqueuerole <queue_name> <role_name>",
            colour=Colour.red(),
        )
        return

    queue_name = args[0]
    role_name = args[1]

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
        session.add(QueueRole(queue.id, role_name_to_role_id[role_name.lower()]))
        await send_message(
            message.channel,
            embed_description=f"Added role {role_name} to queue {queue_name}",
            colour=Colour.green(),
        )
        session.commit()


@require_admin
async def ban(message: Message, args: list[str]):
    """TODO: remove player from queues"""
    if len(args) != 1:
        await send_message(
            message.channel,
            embed_description=f"Usage: !ban @<player_name>",
            colour=Colour.red(),
        )
        return

    session = Session()
    players = session.query(Player).filter(Player.id == message.mentions[0].id).all()
    if len(players) == 0:
        session.add(
            Player(
                id=message.mentions[0].id, name=message.mentions[0].name, is_banned=True
            )
        )
        await send_message(
            message.channel,
            embed_description=f"{message.mentions[0].name} banned",
            colour=Colour.green(),
        )
        session.commit()
    else:
        player = players[0]
        if player.is_banned:
            await send_message(
                message.channel,
                embed_description=f"{player.name} is already banned",
                colour=Colour.red(),
            )
        else:
            player.is_banned = True
            session.commit()
            await send_message(
                message.channel,
                embed_description=f"{player.name} banned",
                colour=Colour.green(),
            )


async def coinflip(message: Message, args: list[str]):
    result = "HEADS" if floor(random() * 2) == 0 else "TAILS"
    await send_message(message.channel, embed_description=result, colour=Colour.blue())


async def cancel_game(message: Message, args: list[str]):
    if len(args) != 1:
        await send_message(
            message.channel,
            embed_description="Usage: !cancelgame <game_id>",
            colour=Colour.red(),
        )
        return

    session = Session()
    game = (
        session.query(InProgressGame)
        .filter(InProgressGame.id.startswith(args[0]))
        .first()
    )
    if not game:
        await send_message(
            message.channel,
            embed_description=f"Could not find game: {args[0]}",
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
        embed_description=f"Game {args[0]} cancelled",
        colour=Colour.blue(),
    )


async def commands(message: Message, args: list[str]):
    output = "Commands:"
    for command in COMMANDS:
        output += f"\n- {COMMAND_PREFIX}{command}"

    await send_message(message.channel, embed_description=output, colour=Colour.blue())


@require_admin
async def clear_queue(message: Message, args: list[str]):
    if len(args) != 1:
        await send_message(
            message.channel,
            embed_description="Usage: !clearqueue <queue_name>",
            colour=Colour.red(),
        )
        return

    session = Session()
    queue = session.query(Queue).filter(Queue.name.ilike(args[0])).first()
    if not queue:
        await send_message(
            message.channel,
            embed_description=f"Could not find queue: {args[0]}",
            colour=Colour.red(),
        )
        return
    session.query(QueuePlayer).filter(QueuePlayer.queue_id == queue.id).delete()
    session.commit()

    await send_message(
        message.channel,
        embed_description=f"Queue cleared: {args[0]}",
        colour=Colour.green(),
    )


@require_admin
async def create_queue(message: Message, args: list[str]):
    if len(args) != 2:
        await send_message(
            message.channel,
            embed_description="Usage: !createqueue <queue_name> <queue_size>",
            colour=Colour.red(),
        )
        return

    queue_size = int(args[1])
    if queue_size % 2 != 0:
        await send_message(message.channel, f"queue size must be even: {queue_size}")
        return

    queue = Queue(name=args[0], size=queue_size)
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


async def del_(message: Message, args: list[str]):
    """
    Players deletes self from queue(s)

    If no args deletes from existing queues
    """
    session = Session()
    queues_to_del = []
    if len(args) == 0:
        for queue in session.query(Queue):
            queues_to_del.append(queue)
    else:
        for queue_name in args:
            queue = session.query(Queue).filter(Queue.name.ilike(queue_name))[0]
            queues_to_del.append(queue)

    for queue in queues_to_del:
        session.query(QueuePlayer).filter(
            QueuePlayer.queue_id == queue.id, QueuePlayer.player_id == message.author.id
        ).delete()
        session.commit()

    queue_statuses = []
    for queue in session.query(Queue):
        queue_players = (
            session.query(QueuePlayer).filter(QueuePlayer.queue_id == queue.id).all()
        )
        queue_statuses.append(f"{queue.name} [{len(queue_players)}/{queue.size}]")

    await send_message(
        message.channel,
        content=f"{message.author.name} removed from: {', '.join([queue.name for queue in queues_to_del])}",
        embed_description=" ".join(queue_statuses),
        colour=Colour.green(),
    )


async def finish_game(message: Message, args: list[str]):
    if len(args) == 0:
        await send_message(
            message.channel,
            embed_description="Usage: !finishgame <win|loss|tie>",
            colour=Colour.red(),
        )
        return

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
    if args[0] == "win":
        winning_team = game_player.team
    elif args[0] == "loss":
        winning_team = (game_player.team + 1) % 2
    elif args[0] == "tie":
        winning_team = -1
    else:
        await send_message(
            message.channel,
            embed_description="Usage: !finishgame <win|loss|tie>",
            colour=Colour.red(),
        )

    players = (
        session.query(Player)
        .join(InProgressGamePlayer)
        .filter(
            InProgressGamePlayer.player_id == Player.id,
            InProgressGamePlayer.in_progress_game_id == in_progress_game.id,
        )
    )
    players_by_id: dict[int, Player] = {player.id: player for player in players}
    in_progress_game_players = (
        session.query(InProgressGamePlayer)
        .filter(InProgressGamePlayer.in_progress_game_id == in_progress_game.id)
        .all()
    )
    team0_ratings_before = []
    team1_ratings_before = []
    team0_players: list[InProgressGamePlayer] = []
    team1_players: list[InProgressGamePlayer] = []
    for in_progress_game_player in in_progress_game_players:
        player = players_by_id[in_progress_game_player.player_id]
        if in_progress_game_player.team == 0:
            team0_players.append(in_progress_game_player)
            team0_ratings_before.append(
                Rating(player.trueskill_mu, player.trueskill_sigma)
            )
        else:
            team1_players.append(in_progress_game_player)
            team1_ratings_before.append(
                Rating(player.trueskill_mu, player.trueskill_sigma)
            )
    finished_game = FinishedGame(
        finished_at=datetime.now(timezone.utc),
        queue_name=queue.name,
        started_at=in_progress_game.created_at,
        win_probability=win_probability(team0_ratings_before, team1_ratings_before),
        winning_team=winning_team,
    )
    session.add(finished_game)

    outcome = None
    if winning_team == -1:
        outcome = [0, 0]
    elif winning_team == 0:
        outcome = [0, 1]
    elif winning_team == 1:
        outcome = [1, 0]

    team0_ratings_after: list[Rating]
    team1_ratings_after: list[Rating]
    team0_ratings_after, team1_ratings_after = rate(
        [team0_ratings_before, team1_ratings_before], outcome
    )

    for i, team0_gip in enumerate(team0_players):
        player = players_by_id[team0_gip.player_id]
        finished_game_player = FinishedGamePlayer(
            finished_game_id=finished_game.id,
            player_id=player.id,
            player_name=player.name,
            team=team0_gip.team,
            trueskill_mu_before=player.trueskill_mu,
            trueskill_sigma_before=player.trueskill_sigma,
            trueskill_mu_after=team0_ratings_after[i].mu,
            trueskill_sigma_after=team0_ratings_after[i].sigma,
        )
        player.trueskill_mu = team0_ratings_after[i].mu
        player.trueskill_sigma = team0_ratings_after[i].sigma
        session.add(finished_game_player)
    for i, team1_gip in enumerate(team1_players):
        player = players_by_id[team1_gip.player_id]
        finished_game_player = FinishedGamePlayer(
            finished_game_id=finished_game.id,
            player_id=player.id,
            player_name=player.name,
            team=team1_gip.team,
            trueskill_mu_before=player.trueskill_mu,
            trueskill_sigma_before=player.trueskill_sigma,
            trueskill_mu_after=team1_ratings_after[i].mu,
            trueskill_sigma_after=team1_ratings_after[i].sigma,
        )
        player.trueskill_mu = team1_ratings_after[i].mu
        player.trueskill_sigma = team1_ratings_after[i].sigma
        session.add(finished_game_player)

    session.query(InProgressGamePlayer).filter(
        InProgressGamePlayer.in_progress_game_id == in_progress_game.id
    ).delete()
    session.query(InProgressGame).filter(
        InProgressGame.id == in_progress_game.id
    ).delete()

    # TODO: Delete channels after the between game wait time, it's nice for
    # players to stick around and chat
    for channel in session.query(InProgressGameChannel).filter(
        InProgressGameChannel.in_progress_game_id == in_progress_game.id
    ):
        if message.guild:
            guild_channel = message.guild.get_channel(channel.channel_id)
            if guild_channel:
                await guild_channel.delete()
        session.delete(channel)

    short_in_progress_game_id = in_progress_game.id.split("-")[0]

    queue = session.query(Queue).filter(Queue.id == in_progress_game.queue_id).first()

    embed_description = ""
    if winning_team == 0:
        embed_description = "**Winner:** Blood Eagle"
    elif winning_team == 1:
        embed_description = "**Winner:** Diamond Sword"
    else:
        embed_description = "**Tie game**"

    # Use a timer to delay processing the waitlist
    timer = Timer(
        RE_ADD_DELAY,
        add_queue_waitlist_message,
        [message.channel, message.guild, finished_game.id],
    )
    timer.start()

    session.commit()
    await send_message(
        message.channel,
        content=f"Game '{queue.name}' ({short_in_progress_game_id}) finished",
        embed_description=embed_description,
        colour=Colour.green(),
    )


async def list_admins(message: Message, args: list[str]):
    output = "Admins:"
    player: Player
    for player in Session().query(Player).filter(Player.is_admin == True).all():
        output += f"\n- {player.name}"

    await send_message(message.channel, embed_description=output, colour=Colour.blue())


async def list_admin_roles(message: Message, args: list[str]):
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


async def list_bans(message: Message, args: list[str]):
    output = "Bans:"
    for player in Session().query(Player).filter(Player.is_banned == True):
        output += f"\n- {player.name}"
    await send_message(message.channel, embed_description=output, colour=Colour.blue())


async def list_queue_roles(message: Message, args: list[str]):
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


@require_admin
async def lock_queue(message: Message, args: list[str]):
    if len(args) != 1:
        await send_message(
            message.channel,
            embed_description="Usage: !lock_queue <queue_name>",
        )
        return

    session = Session()
    queue: Queue | None = session.query(Queue).filter(Queue.name == args[0]).first()
    if not queue:
        await send_message(
            message.channel,
            embed_description=f"Could not find queue: {args[0]}",
            colour=Colour.red(),
        )
        return

    queue.is_locked = True
    session.commit()

    await send_message(
        message.channel,
        embed_description=f"Queue {args[0]} locked",
        colour=Colour.green(),
    )


@require_admin
async def mock_random_queue(message: Message, args: list[str]):
    """
    Helper test method for adding random players to queues
    """
    if len(args) != 2:
        await send_message(
            message.channel,
            embed_description="Usage: !mockrandomqueue <queue_name> <count>",
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
    queue = session.query(Queue).filter(Queue.name.ilike(args[0])).first()
    for player in numpy.random.choice(
        players_from_last_30_days, size=int(args[1]), replace=False
    ):
        if message.guild:
            await add_player_to_queue(
                queue.id, player.id, message.channel, message.guild
            )
        player.last_activity_at = datetime.now(timezone.utc)
        session.add(player)
    session.commit()
    if int(args[1]) != queue.size:
        await status(message, args)


@require_admin
async def remove_admin(message: Message, args: list[str]):
    if len(args) != 1 or len(message.mentions) == 0:
        await send_message(
            message.channel,
            embed_description="Usage: !removeadmin @<player_name>",
            colour=Colour.red(),
        )
        return

    if message.mentions[0].id == message.author.id:
        await send_message(
            message.channel,
            embed_description="You cannot remove yourself as an admin",
            colour=Colour.red(),
        )
        return

    session = Session()
    players = session.query(Player).filter(Player.id == message.mentions[0].id).all()
    if len(players) == 0 or not players[0].is_admin:
        await send_message(
            message.channel,
            embed_description=f"{message.mentions[0].name} is not an admin",
            colour=Colour.red(),
        )
        return

    players[0].is_admin = False
    session.commit()
    await send_message(
        message.channel,
        embed_description=f"{message.mentions[0].name} removed from admins",
        colour=Colour.green(),
    )


@require_admin
async def remove_admin_role(message: Message, args: list[str]):
    if len(args) != 1:
        await send_message(
            message.channel,
            embed_description="Usage: !removeadminrole <role_name>",
            colour=Colour.red(),
        )
        return

    role_name = args[0]
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


@require_admin
async def remove_queue(message: Message, args: list[str]):
    if len(args) == 0:
        await send_message(
            message.channel,
            embed_description="Usage: !remove_queue <queue_name>",
            colour=Colour.red(),
        )
        return

    session = Session()

    queue = session.query(Queue).filter(Queue.name.ilike(args[0])).first()
    if queue:
        games_in_progress = (
            session.query(InProgressGame)
            .filter(InProgressGame.queue_id == queue.id)
            .all()
        )
        if len(games_in_progress) > 0:
            await send_message(
                message.channel,
                embed_description=f"Cannot remove queue with game in progress: {args[0]}",
                colour=Colour.red(),
            )
            return
        else:
            session.delete(queue)
            session.commit()
            await send_message(
                message.channel,
                embed_description=f"Queue removed: {args[0]}",
                colour=Colour.blue(),
            )
    else:
        await send_message(
            message.channel,
            embed_description=f"Queue not found: {args[0]}",
            colour=Colour.red(),
        )


@require_admin
async def remove_queue_role(message: Message, args: list[str]):
    if len(args) != 2:
        await send_message(
            message.channel,
            embed_description="Usage: !removequeuerole <queue_name> <role_name>",
            colour=Colour.red(),
        )
        return

    queue_name = args[0]
    role_name = args[1]

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


async def roll(message: Message, args: list[str]):
    if len(args) != 2:
        await send_message(
            message.channel,
            embed_description="Usage: !roll <low_range> <high_range>",
            colour=Colour.red(),
        )
        return

    await send_message(
        message.channel,
        embed_description=f"You rolled: {randint(int(args[0]), int(args[1]))}",
        colour=Colour.blue(),
    )


@require_admin
async def set_add_delay(message: Message, args: list[str]):
    if len(args) != 1:
        await send_message(
            message.channel,
            embed_description="Usage: !setadddelay <delay_seconds>",
            colour=Colour.red(),
        )
        return

    global RE_ADD_DELAY
    RE_ADD_DELAY = int(args[0])
    await send_message(
        message.channel,
        embed_description=f"Timer to re-add to games set to {RE_ADD_DELAY}",
        colour=Colour.green(),
    )


@require_admin
async def set_command_prefix(message: Message, args: list[str]):
    if len(args) != 1:
        await send_message(
            message.channel,
            embed_description="Usage: !setcommandprefix <prefix>",
            colour=Colour.red(),
        )
        return
    global COMMAND_PREFIX
    COMMAND_PREFIX = args[0]
    await send_message(
        message.channel,
        embed_description=f"Command prefix set to {COMMAND_PREFIX}",
        colour=Colour.green(),
    )


async def status(message: Message, args: list[str]):
    session = Session()
    queues = session.query(Queue).all()
    games_by_queue = defaultdict(list)
    for game in session.query(InProgressGame):
        games_by_queue[game.queue_id].append(game)

    output = ""
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
            output += (
                f"*{queue.name} (locked)* [{len(players_in_queue)} / {queue.size}]\n"
            )
        else:
            output += f"**{queue.name}** [{len(players_in_queue)} / {queue.size}]\n"

        if len(players_in_queue) > 0:
            output += f"**IN QUEUE:** "
            output += ", ".join(sorted([player.name for player in players_in_queue]))
            output += "\n"

        if queue.id in games_by_queue:
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

                short_game_id = game.id.split("-")[0]
                team0_names = ", ".join(
                    sorted([player.name for player in team0_players])
                )
                team1_names = ", ".join(
                    sorted([player.name for player in team1_players])
                )
                win_prob = game.win_probability
                if i > 0:
                    output += "\n"
                output += f"**IN GAME** ({short_game_id}):\n"
                output += f"**Blood Eagle** ({int(100 * win_prob)}%): {team0_names}\n"
                output += (
                    f"**Diamond Sword** ({int(100 * (1 - win_prob))}%): {team1_names}\n"
                )
                minutes_ago = (
                    datetime.now(timezone.utc)
                    - game.created_at.replace(tzinfo=timezone.utc)
                ).seconds // 60
                output += f"**@ {minutes_ago} minutes ago**\n"

    if len(output) == 0:
        output = "No queues or games"

    await send_message(message.channel, embed_description=output, colour=Colour.green())


async def sub(message: Message, args: list[str]):
    """
    Substitute one player in a game for another
    """
    session = Session()
    if len(args) != 1:
        await send_message(
            channel=message.channel,
            embed_description="Usage: !sub @<player_name>",
            colour=Colour.red(),
        )
        return

    caller = message.author
    caller_game = get_player_game(caller.id, session)
    callee = message.mentions[0]
    callee_game = get_player_game(callee.id, session)

    if caller_game and callee_game:
        await send_message(
            channel=message.channel,
            embed_description=f"{caller.name} and {callee.name} are both already in a game",
            colour=Colour.red(),
        )
        return
    elif not caller_game and not callee_game:
        await send_message(
            channel=message.channel,
            embed_description=f"{caller.name} and {callee.name} are not in a game",
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
        session.commit()

    await send_message(
        channel=message.channel,
        embed_description=f"{callee.name} has been substituted with {caller.name}",
        colour=Colour.green(),
    )

    game: InProgressGame | None = callee_game or caller_game
    if not game:
        return

    game_players = (
        session.query(InProgressGamePlayer)
        .filter(InProgressGamePlayer.in_progress_game_id == game.id)
        .all()
    )
    player_ids: list[int] = list(map(lambda x: x.player_id, game_players))
    players, win_prob = get_even_teams(player_ids)
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

    short_game_id = game.id.split("-")[0]
    team0_names = list(map(lambda x: x.name, team0_players))
    team1_names = list(map(lambda x: x.name, team1_players))
    channel_message = f"New teams ({short_game_id}):"
    channel_embed = f"**Blood Eagle** ({int(100 * win_prob)}%): {', '.join(team0_names)}\n**Diamond Sword** ({int(100 * (1 - win_prob))}%): {', '.join(team1_names)}"

    await send_message(
        message.channel,
        content=channel_message,
        embed_description=channel_embed,
        colour=Colour.blue(),
    )
    session.commit()


@require_admin
async def unban(message: Message, args: list[str]):
    if len(args) != 1 or len(message.mentions) == 0:
        await send_message(
            message.channel,
            embed_description="Usage: !iban @<player_name>",
            colour=Colour.red(),
        )
        return

    session = Session()
    players = session.query(Player).filter(Player.id == message.mentions[0].id).all()
    if len(players) == 0 or not players[0].is_banned:
        await send_message(
            message.channel,
            embed_description=f"{message.mentions[0].name} is not banned",
            colour=Colour.red(),
        )
        return

    players[0].is_banned = False
    session.commit()
    await send_message(
        message.channel,
        embed_description=f"{message.mentions[0].name} unbanned",
        colour=Colour.green(),
    )


@require_admin
async def unlock_queue(message: Message, args: list[str]):
    if len(args) != 1:
        await send_message(
            message.channel,
            embed_description="Usage: !unlock_queue <queue_name>",
        )
        return

    session = Session()
    queue: Queue | None = session.query(Queue).filter(Queue.name == args[0]).first()
    if not queue:
        await send_message(
            message.channel,
            embed_description=f"Could not find queue: {args[0]}",
            colour=Colour.red(),
        )
        return

    queue.is_locked = False
    session.commit()

    await send_message(
        message.channel,
        embed_description=f"Queue {args[0]} unlocked",
        colour=Colour.green(),
    )


# Commands end here


COMMANDS = {
    "add": add,
    "addadmin": add_admin,
    "addadminrole": add_admin_role,
    "addqueuerole": add_queue_role,
    "ban": ban,
    "cancelgame": cancel_game,
    "coinflip": coinflip,
    "commands": commands,
    # "createcustomcommand": create_custom_command,
    "createqueue": create_queue,
    "clearqueue": clear_queue,
    "del": del_,
    # "editmatch": edit_match,
    # "deletecustomcommand": delete_custom_command,
    "finishgame": finish_game,
    "listadmins": list_admins,
    "listadminroles": list_admin_roles,
    "listbans": list_bans,
    # "listcustomcommands": list_custom_commands,
    "listqueueroles": list_queue_roles,
    "lockqueue": lock_queue,
    # "matchhistory": match_history,
    "mockrandomqueue": mock_random_queue,
    "removeadmin": remove_admin,
    "removeadminrole": remove_admin_role,
    "removequeuerole": remove_queue_role,
    "removequeue": remove_queue,
    "roll": roll,
    "setadddelay": set_add_delay,
    "setcommandprefix": set_command_prefix,
    "status": status,
    "sub": sub,
    "unban": unban,
    "unlockqueue": unlock_queue,
}


async def handle_message(message: Message):
    print("[handle_message] message:", message)
    command = message.content.split(" ")[0]

    if not command.startswith(COMMAND_PREFIX):
        return

    command = command[1:]
    if command not in COMMANDS:
        print("[handle_message] exiting - command not found:", command)
        return

    session = Session()

    player = session.query(Player).filter(Player.id == message.author.id).first()
    if not player:
        # Create player for the first time
        session.add(
            Player(
                id=message.author.id,
                name=message.author.name,
                last_activity_at=datetime.now(timezone.utc),
            )
        )
    elif player:
        if player.is_banned:
            print("[handle_message] message author banned:", command)
            return
        else:
            player.last_activity_at = datetime.now(timezone.utc)

    session.commit()
    print("[handle_message] executing command:", command)

    await COMMANDS[command](message, message.content.split(" ")[1:])
