from collections import defaultdict
from datetime import datetime, timezone
from math import floor
from random import random, shuffle
from threading import Timer
from typing import Awaitable, Callable, List, Set
import asyncio

from discord import Colour, DMChannel, Embed, GroupChannel, TextChannel, Message
from discord.guild import Guild
from sqlalchemy.exc import IntegrityError

from models import (
    Game,
    GameChannel,
    GamePlayer,
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
COMMAND_PREFIX: str = "!"
RE_ADD_DELAY: int = 5


def async_wrapper(func, *args, **kwargs):
    """
    Wrapper to allow passing async functions to threads
    """
    asyncio.run(func(*args, **kwargs))


async def queue_waitlist(
    channel: TextChannel | DMChannel | GroupChannel,
    guild: Guild | None,
    game_id: str,
) -> None:
    """
    Move players in the waitlist into the queues. Pop queues if needed.
    """
    session = Session()

    queue_waitlist_players: List[QueueWaitlistPlayer]
    queue_waitlist_players = list(
        session.query(QueueWaitlistPlayer).filter(
            QueueWaitlistPlayer.game_id == game_id
        )
    )
    shuffle(queue_waitlist_players)

    players_in_game: Set[int] = set()
    for queue_waitlist_player in queue_waitlist_players:
        if queue_waitlist_player.player_id in players_in_game:
            continue

        session.add(
            QueuePlayer(
                queue_id=queue_waitlist_player.queue_id,
                player_id=queue_waitlist_player.player_id,
                channel_id=channel.id,
            )
        )
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
        else:
            queue: Queue = (
                session.query(Queue)
                .filter(Queue.id == queue_waitlist_player.queue_id)
                .first()
            )
            queue_players: List[QueuePlayer] = list(
                session.query(QueuePlayer).filter(
                    QueuePlayer.queue_id == queue_waitlist_player.queue_id
                )
            )
            if len(queue_players) == queue.size:  # Pop!
                game = Game(queue_id=queue.id)
                session.add(game)

                # TODO: Import even teams using trueskill
                team0 = queue_players[: len(queue_players) // 2]
                team1 = queue_players[len(queue_players) // 2 :]

                team0_players = []
                for queue_player in team0:
                    game_player = GamePlayer(
                        game_id=game.id, player_id=queue_player.player_id, team=0
                    )
                    session.add(game_player)
                    team0_players.append(
                        session.query(Player)
                        .filter(Player.id == queue_player.player_id)[0]
                        .name
                    )

                team1_players = []
                for queue_player in team1:
                    game_player = GamePlayer(
                        game_id=game.id, player_id=queue_player.player_id, team=1
                    )
                    session.add(game_player)
                    team1_players.append(
                        session.query(Player)
                        .filter(Player.id == queue_player.player_id)[0]
                        .name
                    )

                short_game_id = game.id.split("-")[0]

                # Send the message on the main thread using queues
                SEND_MESSAGE_QUEUE.put(
                    MessageQueueMessage(
                        channel,
                        content=f"Game '{queue.name}' ({short_game_id}) has begun!",
                        embed_description=f"**Blood Eagle:** {', '.join(team0_players)}\n**Diamond Sword**: {', '.join(team1_players)}",
                        colour=Colour.blue(),
                    )
                )

                if guild:
                    categories = {
                        category.id: category for category in guild.categories
                    }
                    tribes_voice_category = categories[TRIBES_VOICE_CATEGORY_CHANNEL_ID]

                    # Create voice channels on the main thread
                    CREATE_VOICE_CHANNEL_QUEUE.put(
                        VoiceChannelQueueMessage(
                            guild,
                            f"Blood Eagle ({short_game_id})",
                            game_id=game.id,
                            category=tribes_voice_category,
                        )
                    )
                    CREATE_VOICE_CHANNEL_QUEUE.put(
                        VoiceChannelQueueMessage(
                            guild,
                            f"Diamond Sword ({short_game_id})",
                            game_id=game.id,
                            category=tribes_voice_category,
                        )
                    )
                    session.query(QueuePlayer).delete()
                else:
                    SEND_MESSAGE_QUEUE.put(
                        MessageQueueMessage(
                            channel,
                            embed_description="No guild for message!",
                            colour=Colour.red(),
                        )
                    )

    session.query(QueueWaitlistPlayer).filter(
        QueueWaitlistPlayer.game_id == game_id
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
    return get_player_game(player_id) is not None


def get_player_game(player_id: int) -> Game | None:
    """
    Find the game a player is currently in, excluding finished games
    """
    session = Session()
    game_players = list(
        session.query(GamePlayer)
        .join(Game)
        .filter(Game.winning_team == None, GamePlayer.player_id == player_id)
    )
    if len(game_players) > 0:
        return session.query(Game).filter(Game.id == GamePlayer.game_id)[0]
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
    if is_in_game(message.author.id):
        await send_message(
            message.channel,
            embed_description=f"{message.author} you are already in a game",
            colour=Colour.red(),
        )
        return

    session = Session()

    most_recent_game: Game | None = (
        session.query(Game)
        .join(GamePlayer)
        .filter(Game.finished_at != None, GamePlayer.player_id == message.author.id)
        .order_by(Game.finished_at.desc())  # type: ignore
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
                    game_id=most_recent_game.id,
                    queue_id=queue.id,
                    player_id=message.author.id,
                )
            )
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
        else:
            session.add(
                QueuePlayer(
                    queue_id=queue.id,
                    player_id=message.author.id,
                    channel_id=message.channel.id,
                )
            )
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
            else:
                queue_players: List[QueuePlayer] = list(
                    session.query(QueuePlayer).filter(QueuePlayer.queue_id == queue.id)
                )
                if len(queue_players) == queue.size:  # Pop!
                    game = Game(queue_id=queue.id)
                    session.add(game)

                    # TODO: Import even teams using trueskill
                    team0 = queue_players[: len(queue_players) // 2]
                    team1 = queue_players[len(queue_players) // 2 :]

                    team0_players = []
                    for queue_player in team0:
                        game_player = GamePlayer(
                            game_id=game.id, player_id=queue_player.player_id, team=0
                        )
                        session.add(game_player)
                        team0_players.append(
                            session.query(Player)
                            .filter(Player.id == queue_player.player_id)
                            .first()
                            .name
                        )

                    team1_players = []
                    for queue_player in team1:
                        game_player = GamePlayer(
                            game_id=game.id, player_id=queue_player.player_id, team=1
                        )
                        session.add(game_player)
                        team1_players.append(
                            session.query(Player)
                            .filter(Player.id == queue_player.player_id)
                            .first()
                            .name
                        )

                    short_game_id = game.id.split("-")[0]

                    await send_message(
                        message.channel,
                        content=f"Game '{queue.name}' ({short_game_id}) has begun!",
                        embed_description=f"**Blood Eagle:** {', '.join(team0_players)}\n**Diamond Sword**: {', '.join(team1_players)}",
                        colour=Colour.blue(),
                    )

                    if message.guild:
                        categories = {
                            category.id: category
                            for category in message.guild.categories
                        }
                        tribes_voice_category = categories[
                            TRIBES_VOICE_CATEGORY_CHANNEL_ID
                        ]
                        be_channel = await message.guild.create_voice_channel(
                            f"Blood Eagle ({short_game_id})",
                            category=tribes_voice_category,
                        )
                        ds_channel = await message.guild.create_voice_channel(
                            f"Diamond Sword ({short_game_id})",
                            category=tribes_voice_category,
                        )
                        session.add(
                            GameChannel(game_id=game.id, channel_id=be_channel.id)
                        )
                        session.add(
                            GameChannel(game_id=game.id, channel_id=ds_channel.id)
                        )
                        session.query(QueuePlayer).delete()
                        session.commit()
                    else:
                        await send_message(
                            message.channel,
                            embed_description="No guild for message!",
                            colour=Colour.red(),
                        )
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


@require_admin
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


async def commands(message: Message, args: List[str]):
    output = "Commands:"
    for command in COMMANDS:
        output += f"\n- {COMMAND_PREFIX}{command}"

    await send_message(message.channel, embed_description=output, colour=Colour.blue())


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
        await send_message(message.channel, f"Queue created: {queue.name}")
    except IntegrityError:
        session.rollback()
        await send_message(message.channel, "A queue already exists with that name")


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
    game_players = list(
        session.query(GamePlayer)
        .join(Game)
        .filter(GamePlayer.player_id == message.author.id, Game.winning_team == None)
    )
    if len(game_players) == 0:
        await send_message(message.channel, "you must be in a game to use that command")
        return

    game = session.query(Game).filter(Game.id == game_players[0].game_id)[0]
    if args[0] == "win":
        game.winning_team = game_players[0].team
    elif args[0] == "loss":
        game.winning_team = (game_players[0].team + 1) % 2
    elif args[0] == "tie":
        game.winning_team = -1
    else:
        await send_message(
            message.channel,
            embed_description="Usage: !finishgame <win|loss|tie>",
            colour=Colour.red(),
        )

    # TODO: Delete channels after the between game wait time, it's nice for
    # players to stick around and chat
    for channel in session.query(GameChannel).filter(GameChannel.game_id == game.id):
        if message.guild:
            guild_channel = message.guild.get_channel(channel.channel_id)
            if guild_channel:
                await guild_channel.delete()
        session.delete(channel)

    game.finished_at = datetime.now(timezone.utc)
    session.add(game)
    session.commit()
    short_game_id = game.id.split("-")[0]
    queue = session.query(Queue).filter(Queue.id == Game.queue_id)[0]

    embed_description = ""
    if game.winning_team == 0:
        embed_description = "**Winner:** Blood Eagle"
    elif game.winning_team == 1:
        embed_description = "**Winner:** Diamond Sword"
    else:
        embed_description = "**Tie game**"

    # Players in this game who try to re-add too soon are added to a waitlist.
    # This schedules a thread to put those players in the waitlist into queues.
    timer = Timer(
        RE_ADD_DELAY,
        async_wrapper,
        [queue_waitlist, message.channel, message.guild, game.id],
    )
    timer.start()

    await send_message(
        message.channel,
        content=f"Game '{queue.name}' ({short_game_id}) finished",
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
    for game in session.query(Game).filter(Game.winning_team == None):
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
                    .join(GamePlayer)
                    .filter(GamePlayer.game_id == game.id, GamePlayer.team == 0)
                )
                team1_players = list(
                    session.query(Player)
                    .join(GamePlayer)
                    .filter(GamePlayer.game_id == game.id, GamePlayer.team == 1)
                )

                # TODO: Sort names
                output += f"**IN GAME: **"
                output += f", ".join(sorted([player.name for player in team0_players]))
                output += "\n"
                output += f", ".join(sorted([player.name for player in team1_players]))
                output += "\n"

    if len(output) == 0:
        output = "No queues or games"

    await send_message(message.channel, embed_description=output, colour=Colour.green())


async def sub(message: Message, args: List[str]):
    """
    Substitute one player in a game for another
    """
    if len(args) != 1:
        print("[sub] Usage: !sub <player_name>")
        return

    caller = message.author
    caller_game = get_player_game(caller.id)
    callee = message.mentions[0]
    callee_game = get_player_game(callee.id)

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

    session = Session()

    # The callee may not be recorded in the database
    if not session.query(Player).filter(Player.id == callee.id).first():
        session.add(Player(id=callee.id, name=callee.name))

    if caller_game:
        caller_game_player = (
            session.query(GamePlayer)
            .filter(
                GamePlayer.game_id == caller_game.id, GamePlayer.player_id == caller.id
            )
            .first()
        )
        session.add(
            GamePlayer(
                game_id=caller_game.id,
                player_id=callee.id,
                team=caller_game_player.team,
            )
        )
        session.delete(caller_game_player)
        session.commit()
    elif callee_game:
        callee_game_player = (
            session.query(GamePlayer)
            .filter(
                GamePlayer.game_id == callee_game.id, GamePlayer.player_id == callee.id
            )
            .first()
        )
        session.add(
            GamePlayer(
                game_id=callee_game.id,
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
    return


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
    "coinflip": coinflip,
    "commands": commands,
    "createqueue": create_queue,
    "del": del_,
    "finishgame": finish_game,
    "listadmins": list_admins,
    "listbans": list_bans,
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
