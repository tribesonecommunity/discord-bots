"""
TODO:
- persistence for queues, admins, banned players

- delay and randomization when adding after a game
- proper game id
- command prefix
- afk timer
- queue notifications
- decorator for admin commands
- generalize printing usage?
- random / rotating queue pop
- remove player from queue (admin)
- pick random team captain for each game
- use recognizable words for game ids rather than random strings
- docstrings?
- backup database each time it starts up
- migrations
- enable strict typing configuration
"""
from collections import defaultdict
from math import floor
from random import random
from typing import Awaitable, Callable, Dict, List, Optional, Set

from discord import Colour, DMChannel, Embed, GroupChannel, TextChannel, Member, Message
from sqlalchemy.exc import IntegrityError

from models import Game, GameChannel, GamePlayer, Player, Queue, QueuePlayer, Session

COMMAND_PREFIX: str = "!"
COMMAND_PREFIX: str = "$"


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
    await channel.send(content=content, embed=embed)


def require_admin(command_func: Callable[[Message, List[str]], Awaitable[None]]):
    """
    Decorator to wrap functions that require being called by an admin
    """

    async def wrapper(*args, **kwargs):
        session = Session()
        message: Message = args[0]
        caller = list(session.query(Player).filter(Player.id == message.author.id))
        if len(caller) > 0 and caller[0].is_admin:
            await command_func(*args, **kwargs)
        else:
            await send_message(
                message.channel,
                embed_description="You must be an admin to use that command",
                colour=Colour.red(),
            )

    return wrapper


TRIBES_VOICE_CATEGORY_CHANNEL_ID: str = "462824101753520138"
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


def get_player_game(player_id: int) -> Optional[Game]:
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
        Players adds self to queue(s)

        If no args to all existing queues

        TODO:
        - Player must be eligible for queue
        - Player queued if just finished game?

        opsayo added to: LTgold, LTsilver, LTpug, LTunrated
    LTgold [3/10] LTsilver [3/10] LTpug [3/10] LTunrated [3/10] LTirregular [0/10]
    """
    if is_in_game(message.author.id):
        await send_message(
            message.channel,
            embed_description=f"{message.author} you are already in a game",
            colour=Colour.red(),
        )
        return

    # if len(GAME_HISTORY) > 0:
    #     for finish_time, game in reversed(GAME_HISTORY):
    #         current_time = clock_gettime(0)
    #         time_difference = current_time - finish_time
    #         if game.contains_player(author) and time_difference < RE_ADD_DELAY:
    #             print(
    #                 "[add]",
    #                 author,
    #                 "your game has just finished, you will be randomized into the queue in",
    #                 ceil(RE_ADD_DELAY - time_difference),
    #                 "seconds",
    #             )
    #             for queue_name in QUEUES.keys():
    #                 QUEUE_WAITING_ROOM[queue_name].append(author)
    #             return

    session = Session()
    player = Player(id=message.author.id, name=message.author.name)
    session.add(player)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()

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
        return

    for queue in queues_to_add:
        session.add(QueuePlayer(queue_id=queue.id, player_id=player.id))
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
        else:
            queue_players: List[QueuePlayer] = list(
                session.query(QueuePlayer).filter(QueuePlayer.queue_id == queue.id)
            )
            if len(queue_players) == queue.size:  # Pop!
                # TODO: Create voice channels
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

                await send_message(
                    message.channel,
                    content=f"Game '{queue.name}' ({short_game_id}) has begun!",
                    embed_description=f"**Blood Eagle:** {', '.join(team0_players)}\n**Diamond Sword**: {', '.join(team1_players)}",
                    colour=Colour.blue(),
                )

                if message.guild:
                    categories = {
                        category.id: category for category in message.guild.categories
                    }
                    tribes_voice_category = categories[TRIBES_VOICE_CATEGORY_CHANNEL_ID]
                    be_channel = await message.guild.create_voice_channel(
                        f"Blood Eagle ({short_game_id})",
                        category=tribes_voice_category,
                    )
                    ds_channel = await message.guild.create_voice_channel(
                        f"Diamond Sword ({short_game_id})",
                        category=tribes_voice_category,
                    )
                    session.add(GameChannel(game_id=game.id, channel_id=be_channel.id))
                    session.add(GameChannel(game_id=game.id, channel_id=ds_channel.id))
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

    await send_message(
        message.channel,
        content=f"{player.name} added to: {', '.join([queue.name for queue in queues_to_add])}",
        embed_description=" ".join(queue_statuses),
        colour=Colour.green(),
    )


@require_admin
async def add_admin(message: Message, args: List[str]):
    """
    TODO:
    - Decorator for admin permissions
    """
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

    elif author not in ADMINS:
        await send_message(message.channel, "you must be an admin to use this command")
    elif args[0] in BANNED_PLAYERS:
        await send_message(message.channel, f"{args[0]} is already banned")
    else:
        BANNED_PLAYERS.add(args[0])
        await send_message(message.channel, f"{args[0]} added to ban list")


@require_admin
async def cancel_game(message: Message, args: List[str]):
    await send_message(message.channel, "not implemented")
    if len(args) == 1:
        if author not in ADMINS:
            await send_message(
                message.channel, "you must be an admin to cancel a game by its id"
            )
            return
        for games in GAMES.values():
            for game in games:
                if game.id == args[0]:
                    games.remove(game)
                    print(
                        "[cancelgame]",
                        game.queue_name,
                        "game",
                        args[0],
                        "cancelled by",
                        author,
                    )
                    return
    else:
        author_game = get_player_game(author.id)
        if not author_game:
            print("[cancelgame] you are not in a game")
            return
        else:
            GAMES[author_game.queue_name].remove(author_game)
            print(
                "[cancelgame]",
                author_game.queue_name,
                "game",
                author_game.id,
                "cancelled by",
                author,
            )


async def coinflip(message: Message, args: List[str]):
    result = "`HEADS`" if floor(random() * 2) == 0 else "`TAILS`"
    await send_message(message.channel, result)


async def commands(message: Message, args: List[str]):
    output = "commands:"
    for command in COMMANDS:
        output += f"\n{COMMAND_PREFIX}{command}"

    await send_message(message.channel, output)


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
    await send_message(message.channel, "not implemented")
    print("[list_bans]", BANNED_PLAYERS)


@require_admin
async def remove_admin(message: Message, args: List[str]):
    if len(args) != 1 or len(message.mentions) == 0:
        await send_message(
            message.channel,
            embed_description="Usage: !removeadmin @<player_name>",
            colour=Colour.red(),
        )
        return

    # if message.mentions[0].id == message.author.id:
    #     await send_message(
    #         message.channel,
    #         embed_description="You cannot remove yourself as an admin",
    #         colour=Colour.red(),
    #     )
    #     return

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
    await send_message(message.channel, "not implemented")
    if len(args) != 1:
        print("[set_add_delay] Usage: !setadddelay <delay_seconds>")
        return
    global RE_ADD_DELAY
    RE_ADD_DELAY = int(args[0])
    print("[set_add_delay] timer to re-add to games set to", RE_ADD_DELAY)


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
    await send_message(message.channel, f"command prefix set to {COMMAND_PREFIX}")


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
            output += ", ".join([player.name for player in players_in_queue])
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
                output += f"**IN GAME:**"
                output += f", ".join([player.name for player in team0_players])
                output += "\n"
                output += f", ".join([player.name for player in team1_players])
                output += "\n"

    if len(output) == 0:
        output = "No queues or games"

    await send_message(message.channel, embed_description=output, colour=Colour.green())


async def sub(message: Message, args: List[str]):
    """
    Substitute one player in a game for another
    """
    await send_message(message.channel, "not implemented")
    if len(args) != 1:
        print("[sub] Usage: !sub <player_name>")
        return

    callee = args[0]
    if is_in_game(author.id):
        print("[sub]", author, "is already in a game")
        return

    callee_game = get_player_game(callee)
    if not callee_game:
        print("[sub]", callee, "is not in a game")
        return

    if callee in callee_game.team1:
        callee_game.team1.remove(callee)
        callee_game.team1.append(author)
    else:
        callee_game.team1.remove(callee)
        callee_game.team1.append(author)

    print("[sub] swapped", callee, "for", author, "in", callee_game.queue_name)


@require_admin
async def unban(message: Message, args: List[str]):
    await send_message(message.channel, "not implemented")
    if len(args) != 1:
        print("[unban] Usage: !unban <player_name>")
        return
    elif author not in ADMINS:
        print("[unban] you must be an admin to use this command")
        return
    elif args[0] not in BANNED_PLAYERS:
        print("[unban]", args[0], "is not banned")
        return
    else:
        BANNED_PLAYERS.remove(args[0])
        print("[ban]", args[0], "removed from ban list")
        return


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
        print("[handle_message] not command", command)
        return

    command = command[1:]
    if command not in COMMANDS:
        print("[handle_message] exiting - command not found:", command)
        return

    print("[handle_message] command:", command)
    await COMMANDS[command](message, message.content.split(" ")[1:])
