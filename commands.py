from collections import defaultdict
from datetime import datetime, timedelta, timezone
from math import floor
from random import choices, random, shuffle
from threading import Timer
from typing import Awaitable, Callable, Dict, List, Set, Tuple
import asyncio
import itertools
import math
import numpy

from discord import Colour, DMChannel, Embed, GroupChannel, TextChannel, Message
from discord.guild import Guild
from sqlalchemy.exc import IntegrityError
from trueskill import Rating, global_env, rate

from models import (
    GameFinished,
    GameFinishedPlayer,
    GameInProgress,
    GameChannel,
    GameInProgressPlayer,
    Player,
    Queue,
    QueuePlayer,
    QueueWaitlistPlayer,
    Session,
)
from queues import (
    SEND_MESSAGE_QUEUE,
    CREATE_VOICE_CHANNEL_QUEUE,
    MessageQueueMessage,
    VoiceChannelQueueMessage,
)

AFK_TIME_MINUTES: int = 45
COMMAND_PREFIX: str = "$"
RE_ADD_DELAY: int = 5


def win_probability(team0: List[Rating], team1: List[Rating]) -> float:
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


def async_wrapper(func, *args, **kwargs):
    """
    Wrapper to allow passing async functions to threads
    """
    asyncio.run(func(*args, **kwargs))


def get_even_teams(player_ids: List[int]) -> Tuple[List[Player], float]:
    """
    Try to figure out even teams, the first half of the returning list is
    the first team, the second half is the second team.

    :returns: List of players and win probability for the first team
    """
    players: List[Player] = (
        session.query(Player).filter(Player.id.in_(player_ids)).all()  # type: ignore
    )
    best_win_prob_so_far: float | None = None
    best_teams_so_far: List[Player] | None = None

    # Use a fixed number of shuffles over generating permutations then
    # shuffle for performance.  There are 3.6 million permutations!
    for _ in range(100):
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


# TODO: Add locking to this method? IDK what happens if lots of people add at the same time
async def add_player_to_queue(
    queue_id: str,
    player_id: int,
    channel: TextChannel | DMChannel | GroupChannel,
    guild: Guild,
    is_multithread: bool = False,
) -> bool:
    """
    Helper function to add player to a queue and pop if needed.

    :is_multithread: Discord client only executes certain commands on the main
    thread. Use this flag to handle cases where this function is being called
    in a multi-threaded state
    """
    session = Session()
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
        return False

    queue: Queue = session.query(Queue).filter(Queue.id == queue_id).first()
    queue_players: List[QueuePlayer] = (
        session.query(QueuePlayer).filter(QueuePlayer.queue_id == queue_id).all()
    )
    if len(queue_players) == queue.size:  # Pop!
        player_ids: List[int] = list(map(lambda x: x.player_id, queue_players))
        players, win_prob = get_even_teams(player_ids)
        # players: List[Player] = (
        #     session.query(Player).filter(Player.id.in_(player_ids)).all()  # type: ignore
        # )
        # best_win_prob_so_far: float | None = None
        # best_teams_so_far: List[Player] | None = None

        # # Use a fixed number of shuffles over generating permutations then
        # # shuffle for performance.  There are 3.6 million permutations!
        # for _ in range(100):
        #     shuffle(players)
        #     team0_ratings = list(
        #         map(
        #             lambda x: Rating(x.trueskill_mu, x.trueskill_sigma),
        #             players[: len(players) // 2],
        #         )
        #     )
        #     team1_ratings = list(
        #         map(
        #             lambda x: Rating(x.trueskill_mu, x.trueskill_sigma),
        #             players[len(players) // 2 :],
        #         )
        #     )
        #     win_prob = win_probability(team0_ratings, team1_ratings)
        #     if not best_win_prob_so_far:
        #         best_win_prob_so_far = win_prob
        #         best_teams_so_far = players[:]
        #     else:
        #         current_team_evenness = abs(0.50 - win_prob)
        #         best_team_evenness_so_far = abs(0.50 - best_win_prob_so_far)
        #         if current_team_evenness < best_team_evenness_so_far:
        #             best_win_prob_so_far = win_prob
        #             best_teams_so_far = players[:]

        #         if int(win_prob * 100) == 50:
        #             # Can't do better than this
        #             break

        # if not best_win_prob_so_far or not best_teams_so_far:
        #     # Can't really happen, so mostly just to appease the linter
        #     return False

        game = GameInProgress(queue_id=queue.id, win_probability=win_prob)
        session.add(game)

        team0_players = players[: len(players) // 2]
        team1_players = players[len(players) // 2 :]

        for player in team0_players:
            game_player = GameInProgressPlayer(
                game_in_progress_id=game.id,
                player_id=player.id,
                team=0,
            )
            session.add(game_player)

        for player in team1_players:
            game_player = GameInProgressPlayer(
                game_in_progress_id=game.id,
                player_id=player.id,
                team=1,
            )
            session.add(game_player)

        short_game_id = game.id.split("-")[0]
        team0_names = list(map(lambda x: x.name, team0_players))
        team1_names = list(map(lambda x: x.name, team1_players))
        channel_message = f"Game '{queue.name}' ({short_game_id}) has begun!"
        channel_embed = f"**Blood Eagle** ({int(100 * win_prob)}%): {', '.join(team0_names)}\n**Diamond Sword** ({int(100 * (1 - win_prob))}%): {', '.join(team1_names)}"

        if is_multithread:
            # Send the message on the main thread using queues
            SEND_MESSAGE_QUEUE.put(
                MessageQueueMessage(
                    channel,
                    content=channel_message,
                    embed_description=channel_embed,
                    colour=Colour.blue(),
                )
            )
        else:
            await send_message(
                channel,
                content=channel_message,
                embed_description=channel_embed,
                colour=Colour.blue(),
            )

        categories = {category.id: category for category in guild.categories}
        tribes_voice_category = categories[TRIBES_VOICE_CATEGORY_CHANNEL_ID]

        if is_multithread:
            # Create voice channels on the main thread
            CREATE_VOICE_CHANNEL_QUEUE.put(
                VoiceChannelQueueMessage(
                    guild,
                    f"Blood Eagle ({short_game_id})",
                    game_in_progress_id=game.id,
                    category=tribes_voice_category,
                )
            )
            CREATE_VOICE_CHANNEL_QUEUE.put(
                VoiceChannelQueueMessage(
                    guild,
                    f"Diamond Sword ({short_game_id})",
                    game_in_progress_id=game.id,
                    category=tribes_voice_category,
                )
            )
        else:
            be_channel = await guild.create_voice_channel(
                f"Blood Eagle ({short_game_id})",
                category=tribes_voice_category,
            )
            ds_channel = await guild.create_voice_channel(
                f"Diamond Sword ({short_game_id})",
                category=tribes_voice_category,
            )
            session.add(
                GameChannel(game_in_progress_id=game.id, channel_id=be_channel.id)
            )
            session.add(
                GameChannel(game_in_progress_id=game.id, channel_id=ds_channel.id)
            )

        session.query(QueuePlayer).filter(QueuePlayer.queue_id == queue_id).delete()
        session.commit()
        return True
    return False


async def queue_waitlist(
    channel: TextChannel | DMChannel | GroupChannel,
    guild: Guild,
    game_finished_id: str,
) -> None:
    """
    Move players in the waitlist into the queues. Pop queues if needed.
    """
    session = Session()

    queue_waitlist_players: List[QueueWaitlistPlayer]
    queue_waitlist_players = list(
        session.query(QueueWaitlistPlayer).filter(
            QueueWaitlistPlayer.game_finished_id == game_finished_id
        )
    )
    shuffle(queue_waitlist_players)

    for queue_waitlist_player in queue_waitlist_players:
        if is_in_game(queue_waitlist_player.player_id):
            session.delete(queue_waitlist_player)
            continue

        await add_player_to_queue(
            queue_waitlist_player.queue_id,
            queue_waitlist_player.player_id,
            channel,
            guild,
            True,
        )

    session.query(QueueWaitlistPlayer).filter(
        QueueWaitlistPlayer.game_finished_id == game_finished_id
    ).delete()
    session.commit()


async def send_message(
    channel: (DMChannel | GroupChannel | TextChannel),
    content: str = None,
    embed_description: str = None,
    colour: Colour = None,
):
    if content:
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


def require_admin(command_func: Callable[[Message, List[str]], Awaitable[None]]):
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
        else:
            await send_message(
                message.channel,
                embed_description="You must be an admin to use that command",
                colour=Colour.red(),
            )

    return wrapper


TRIBES_VOICE_CATEGORY_CHANNEL_ID: int = 462824101753520138
OPSAYO_MEMBER_ID = 115204465589616646

session = Session()
try:
    # There always has to be at least one initial admin to add others!
    session.add(Player(id=OPSAYO_MEMBER_ID, name="opsayo", is_admin=True))
    session.commit()
except IntegrityError:
    session.rollback()


def is_in_game(player_id: int) -> bool:
    return get_player_game(player_id, Session()) is not None


def get_player_game(player_id: int, session=Session()) -> GameInProgress | None:
    """
    Find the game a player is currently in

    :session: Pass in a session if you want to do something with the game that
    gets returned
    """
    game_players = list(
        session.query(GameInProgressPlayer)
        .join(GameInProgress)
        .filter(GameInProgressPlayer.player_id == player_id)
    )
    if len(game_players) > 0:
        return (
            session.query(GameInProgress)
            .filter(GameInProgress.id == GameInProgressPlayer.game_in_progress_id)
            .first()
        )
    else:
        return None


# Commands start here


async def add(message: Message, args: List[str]):
    """
    Players adds self to queue(s). If no args to all existing queues

    TODO:
    - Queue eligibility
    - Player queued if just finished game?
    """
    session = Session()
    if is_in_game(message.author.id):
        await send_message(
            message.channel,
            embed_description=f"{message.author} you are already in a game",
            colour=Colour.red(),
        )
        return


    most_recent_game: GameFinished | None = (
        session.query(GameFinished)
        .join(GameFinishedPlayer)
        .filter(
            GameFinishedPlayer.player_id == message.author.id,
        )
        .order_by(GameFinished.finished_at.desc())  # type: ignore
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

    queues_to_add = []
    if len(args) == 0:
        for queue in session.query(Queue):
            queues_to_add.append(queue)
    else:
        for queue_name in args:
            queues = list(session.query(Queue).filter(Queue.name == queue_name))
            if len(queues) > 0:
                queues_to_add.append(queues[0])
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

    for queue in queues_to_add:
        if is_waitlist and most_recent_game:
            session.add(
                QueueWaitlistPlayer(
                    game_finished_id=most_recent_game.id,
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
                if await add_player_to_queue(
                    queue.id, message.author.id, message.channel, message.guild, False
                ):
                    return

    queue_statuses = []
    for queue in session.query(Queue):
        queue_players = list(
            session.query(QueuePlayer).filter(QueuePlayer.queue_id == queue.id)
        )
        queue_statuses.append(f"{queue.name} [{len(queue_players)}/{queue.size}]")

    if is_waitlist and waitlist_message:
        await send_message(
            message.channel,
            content=f"{message.author.name} added to: {', '.join([queue.name for queue in queues_to_add])}",
            embed_description=waitlist_message,
            colour=Colour.green(),
        )
    else:
        await send_message(
            message.channel,
            content=f"{message.author.name} added to: {', '.join([queue.name for queue in queues_to_add])}",
            embed_description=" ".join(queue_statuses),
            colour=Colour.green(),
        )
    session.commit()


# @require_admin
async def add_admin(message: Message, args: List[str]):
    if len(args) != 1 or len(message.mentions) == 0:
        await send_message(
            message.channel,
            embed_description="Usage: !addadmin <player_name>",
            colour=Colour.red(),
        )
        return

    session = Session()
    players = list(session.query(Player).filter(Player.id == message.mentions[0].id))
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
async def ban(message: Message, args: List[str]):
    """TODO: remove player from queues"""
    if len(args) != 1:
        await send_message(
            message.channel,
            embed_description=f"Usage: !ban @<player_name>",
            colour=Colour.red(),
        )
        return

    session = Session()
    players = list(session.query(Player).filter(Player.id == message.mentions[0].id))
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


async def coinflip(message: Message, args: List[str]):
    result = "`HEADS`" if floor(random() * 2) == 0 else "`TAILS`"
    await send_message(message.channel, result)


async def cancel_game(message: Message, args: List[str]):
    if len(args) != 1:
        await send_message(
            message.channel,
            embed_description="Usage: !cancelgame <game_id>",
            colour=Colour.red(),
        )
        return

    session = Session()
    game = (
        session.query(GameInProgress)
        .filter(GameInProgress.id.startswith(args[0]))
        .first()
    )
    if not game:
        await send_message(
            message.channel,
            embed_description=f"Could not find game: {args[0]}",
            colour=Colour.red(),
        )
        return

    session.query(GameInProgressPlayer).filter(
        GameInProgressPlayer.game_in_progress_id == game.id
    ).delete()
    for channel in session.query(GameChannel).filter(
        GameChannel.game_in_progress_id == game.id
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


async def commands(message: Message, args: List[str]):
    output = "Commands:"
    for command in COMMANDS:
        output += f"\n- {COMMAND_PREFIX}{command}"

    await send_message(message.channel, embed_description=output, colour=Colour.blue())


@require_admin
async def clear_queue(message: Message, args: List[str]):
    if len(args) != 1:
        await send_message(
            message.channel,
            embed_description="Usage: !clearqueue <queue_name>",
            colour=Colour.red(),
        )
        return

    session = Session()
    queue = session.query(Queue).filter(Queue.name == args[0]).first()
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
async def create_queue(message: Message, args: List[str]):
    if len(args) != 2:
        await send_message(
            message.channel,
            embed_description="Usage: !createqueue <queue_name> <queue_size>",
            colour=Colour.red(),
        )
        return

    queue_size = int(args[1])
    # TODO: Re-add later, but commented out is useful for solo testing
    # if queue_size % 2 != 0:
    #     await send_message(message.channel, f"queue size must be even: {queue_size}")
    #     return

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


async def del_(message: Message, args: List[str]):
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
            queue = session.query(Queue).filter(Queue.name == queue_name)[0]
            queues_to_del.append(queue)

    for queue in queues_to_del:
        session.query(QueuePlayer).filter(
            QueuePlayer.queue_id == queue.id, QueuePlayer.player_id == message.author.id
        ).delete()
        session.commit()

    queue_statuses = []
    for queue in session.query(Queue):
        queue_players = list(
            session.query(QueuePlayer).filter(QueuePlayer.queue_id == queue.id)
        )
        queue_statuses.append(f"{queue.name} [{len(queue_players)}/{queue.size}]")

    await send_message(
        message.channel,
        content=f"{message.author.name} removed from: {', '.join([queue.name for queue in queues_to_del])}",
        embed_description=" ".join(queue_statuses),
        colour=Colour.green(),
    )


async def finish_game(message: Message, args: List[str]):
    """
    TODO
    - Player must be a captain?
    """
    if len(args) == 0:
        await send_message(
            message.channel,
            embed_description="Usage: !finishgame <win|loss|tie>",
            colour=Colour.red(),
        )
        return

    session = Session()
    game_player = (
        session.query(GameInProgressPlayer)
        .filter(GameInProgressPlayer.player_id == message.author.id)
        .first()
    )
    if not game_player:
        await send_message(
            message.channel,
            embed_description="You must be in a game to use that command",
            colour=Colour.red(),
        )
        return

    game_in_progress: GameInProgress = (
        session.query(GameInProgress)
        .filter(GameInProgress.id == game_player.game_in_progress_id)
        .first()
    )
    queue: Queue = (
        session.query(Queue).filter(Queue.id == game_in_progress.queue_id).first()
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
        .join(GameInProgressPlayer)
        .filter(
            GameInProgressPlayer.player_id == Player.id,
            GameInProgressPlayer.game_in_progress_id == game_in_progress.id,
        )
    )
    players_by_id: Dict[int, Player] = {player.id: player for player in players}
    game_in_progress_players = (
        session.query(GameInProgressPlayer)
        .filter(GameInProgressPlayer.game_in_progress_id == game_in_progress.id)
        .all()
    )
    team0_ratings_before = []
    team1_ratings_before = []
    team0_players: List[GameInProgressPlayer] = []
    team1_players: List[GameInProgressPlayer] = []
    for game_in_progress_player in game_in_progress_players:
        player = players_by_id[game_in_progress_player.player_id]
        if game_in_progress_player.team == 0:
            team0_players.append(game_in_progress_player)
            team0_ratings_before.append(
                Rating(player.trueskill_mu, player.trueskill_sigma)
            )
        else:
            team1_players.append(game_in_progress_player)
            team1_ratings_before.append(
                Rating(player.trueskill_mu, player.trueskill_sigma)
            )
    game_finished = GameFinished(
        finished_at=datetime.now(timezone.utc),
        queue_name=queue.name,
        started_at=game_in_progress.created_at,
        win_probability=win_probability(team0_ratings_before, team1_ratings_before),
        winning_team=winning_team,
    )
    session.add(game_finished)

    outcome = None
    if winning_team == -1:
        outcome = [0, 0]
    elif winning_team == 0:
        outcome = [0, 1]
    elif winning_team == 1:
        outcome = [1, 0]

    team0_ratings_after: List[Rating]
    team1_ratings_after: List[Rating]
    team0_ratings_after, team1_ratings_after = rate(
        [team0_ratings_before, team1_ratings_before], outcome
    )

    for i, team0_gip in enumerate(team0_players):
        player = players_by_id[team0_gip.player_id]
        game_finished_player = GameFinishedPlayer(
            game_finished_id=game_finished.id,
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
        session.add(game_finished_player)
        session.add(player)
    for i, team1_gip in enumerate(team1_players):
        player = players_by_id[team1_gip.player_id]
        game_finished_player = GameFinishedPlayer(
            game_finished_id=game_finished.id,
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
        session.add(player)
        session.add(game_finished_player)

    session.query(GameInProgressPlayer).filter(
        GameInProgressPlayer.game_in_progress_id == game_in_progress.id
    ).delete()
    session.query(GameInProgress).filter(
        GameInProgress.id == game_in_progress.id
    ).delete()

    # TODO: Delete channels after the between game wait time, it's nice for
    # players to stick around and chat
    for channel in session.query(GameChannel).filter(
        GameChannel.game_in_progress_id == game_in_progress.id
    ):
        if message.guild:
            guild_channel = message.guild.get_channel(channel.channel_id)
            if guild_channel:
                await guild_channel.delete()
        session.delete(channel)

    short_game_in_progress_id = game_in_progress.id.split("-")[0]

    # TODO: This might behave weird if the queue is deleted mid game?
    queue = session.query(Queue).filter(Queue.id == game_in_progress.queue_id).first()

    embed_description = ""
    if winning_team == 0:
        embed_description = "**Winner:** Blood Eagle"
    elif winning_team == 1:
        embed_description = "**Winner:** Diamond Sword"
    else:
        embed_description = "**Tie game**"

    # Players in this game who try to re-add too soon are added to a waitlist.
    # This schedules a thread to put those players in the waitlist into queues.
    timer = Timer(
        RE_ADD_DELAY,
        async_wrapper,
        [queue_waitlist, message.channel, message.guild, game_in_progress.id],
    )
    timer.start()

    session.commit()
    await send_message(
        message.channel,
        content=f"Game '{queue.name}' ({short_game_in_progress_id}) finished",
        embed_description=embed_description,
        colour=Colour.green(),
    )


async def list_admins(message: Message, args: List[str]):
    output = "Admins:"
    player: Player
    for player in list(Session().query(Player).filter(Player.is_admin == True)):
        output += f"\n- {player.name}"

    await send_message(message.channel, embed_description=output, colour=Colour.green())


async def list_bans(message: Message, args: List[str]):
    output = "Bans:"
    for player in Session().query(Player).filter(Player.is_banned == True):
        output += f"\n- {player.name}"
    await send_message(message.channel, embed_description=output, colour=Colour.green())


@require_admin
async def mock_random_queue(message: Message, args: List[str]):
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
        .join(GameFinishedPlayer, GameFinishedPlayer.player_id == Player.id)
        .join(GameFinished, GameFinished.id == GameFinishedPlayer.game_finished_id)
        .filter(
            GameFinished.finished_at > datetime.now(timezone.utc) - timedelta(days=30),
        )
        .order_by(GameFinished.finished_at.desc())  # type: ignore
        .all()
    )
    queue = session.query(Queue).filter(Queue.name == args[0]).first()
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
async def remove_admin(message: Message, args: List[str]):
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
    players = list(session.query(Player).filter(Player.id == message.mentions[0].id))
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
async def remove_queue(message: Message, args: List[str]):
    """
    TODO:
    - Don't allow removing a queue if a game is currently in progress
    """
    if len(args) == 0:
        await send_message(
            message.channel,
            embed_description="Usage: !remove_queue <queue_name>",
            colour=Colour.red(),
        )
        return
    session = Session()
    queues = list(session.query(Queue).filter(Queue.name == args[0]))
    if len(queues) > 0:
        session.delete(queues[0])
        session.commit()
        await send_message(message.channel, f"Queue removed: {args[0]}")
    else:
        await send_message(message.channel, f"Queue not found: {args[0]}")


@require_admin
async def set_add_delay(message: Message, args: List[str]):
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
async def set_command_prefix(message: Message, args: List[str]):
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


async def status(message: Message, args: List[str]):
    session = Session()
    queues = list(session.query(Queue))
    games_by_queue = defaultdict(list)
    for game in session.query(GameInProgress):
        games_by_queue[game.queue_id].append(game)

    output = ""
    for i, queue in enumerate(queues):
        if i > 0:
            output += "\n"
        players_in_queue = list(
            session.query(Player)
            .join(QueuePlayer)
            .filter(QueuePlayer.queue_id == queue.id)
        )
        output += f"**{queue.name}** [{len(players_in_queue)} / {queue.size}]\n"

        if len(players_in_queue) > 0:
            output += f"**IN QUEUE:** "
            output += ", ".join(sorted([player.name for player in players_in_queue]))
            output += "\n"

        if queue.id in games_by_queue:
            for game in games_by_queue[queue.id]:
                team0_players = list(
                    session.query(Player)
                    .join(GameInProgressPlayer)
                    .filter(
                        GameInProgressPlayer.game_in_progress_id == game.id,
                        GameInProgressPlayer.team == 0,
                    )
                )
                team1_players = list(
                    session.query(Player)
                    .join(GameInProgressPlayer)
                    .filter(
                        GameInProgressPlayer.game_in_progress_id == game.id,
                        GameInProgressPlayer.team == 1,
                    )
                )

                short_game_id = game.id.split("-")[0]
                # TODO: Sort names
                output += f"**IN GAME** ({short_game_id}):"
                output += f", ".join(sorted([player.name for player in team0_players]))
                output += "\n"
                output += f", ".join(sorted([player.name for player in team1_players]))
                output += "\n"
                minutes_ago = (
                    datetime.now(timezone.utc)
                    - game.created_at.replace(tzinfo=timezone.utc)
                ).seconds // 60
                output += f"**@ {minutes_ago} minutes ago**\n"

    if len(output) == 0:
        output = "No queues or games"

    await send_message(message.channel, embed_description=output, colour=Colour.green())


# TODO: Repick teams on substitute
async def sub(message: Message, args: List[str]):
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
            session.query(GameInProgressPlayer)
            .filter(
                GameInProgressPlayer.game_in_progress_id == caller_game.id,
                GameInProgressPlayer.player_id == caller.id,
            )
            .first()
        )
        session.add(
            GameInProgressPlayer(
                game_in_progress_id=caller_game.id,
                player_id=callee.id,
                team=caller_game_player.team,
            )
        )
        session.delete(caller_game_player)
        session.commit()
    elif callee_game:
        callee_game_player = (
            session.query(GameInProgressPlayer)
            .filter(
                GameInProgressPlayer.game_in_progress_id == callee_game.id,
                GameInProgressPlayer.player_id == callee.id,
            )
            .first()
        )
        session.add(
            GameInProgressPlayer(
                game_in_progress_id=callee_game.id,
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

    game: GameInProgress | None = callee_game or caller_game
    if not game:
        return

    game_players = (
        session.query(GameInProgressPlayer)
        .filter(GameInProgressPlayer.game_in_progress_id == game.id)
        .all()
    )
    player_ids: List[int] = list(map(lambda x: x.player_id, game_players))
    players, win_prob = get_even_teams(player_ids)
    for game_player in game_players:
        session.delete(game_player)
    game.win_probability = win_prob
    session.add(game)
    team0_players = players[: len(players) // 2]
    team1_players = players[len(players) // 2 :]

    for player in team0_players:
        game_player = GameInProgressPlayer(
            game_in_progress_id=game.id,
            player_id=player.id,
            team=0,
        )
        session.add(game_player)

    for player in team1_players:
        game_player = GameInProgressPlayer(
            game_in_progress_id=game.id,
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
async def unban(message: Message, args: List[str]):
    if len(args) != 1 or len(message.mentions) == 0:
        await send_message(
            message.channel,
            embed_description="Usage: !iban @<player_name>",
            colour=Colour.red(),
        )
        return

    session = Session()
    players = list(session.query(Player).filter(Player.id == message.mentions[0].id))
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


# Commands end here


"""
TODO
- Ability to redact / fix results if mis-reported
"""
COMMANDS = {
    "add": add,
    "addadmin": add_admin,
    "ban": ban,
    "cancelgame": cancel_game,
    "coinflip": coinflip,
    "commands": commands,
    "createqueue": create_queue,
    "clearqueue": clear_queue,
    "del": del_,
    "finishgame": finish_game,
    "listadmins": list_admins,
    "listbans": list_bans,
    "mockrandomqueue": mock_random_queue,
    "removeadmin": remove_admin,
    "removequeue": remove_queue,
    "setadddelay": set_add_delay,
    "setcommandprefix": set_command_prefix,
    "status": status,
    "sub": sub,
    "unban": unban,
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
        session.commit()
    elif player:
        if player.is_banned:
            print("[handle_message] message author banned:", command)
            return
        else:
            player.last_activity_at = datetime.now(timezone.utc)
            session.add(player)
            session.commit()

    print("[handle_message] executing command:", command)

    await COMMANDS[command](message, message.content.split(" ")[1:])
