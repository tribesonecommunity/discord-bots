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
- docstrings?
"""
from collections import defaultdict
from dataclasses import dataclass, field
from math import floor
from random import random
from time import clock_gettime
from typing import Dict, List, Set

from discord import TextChannel, Member, Message
from sqlalchemy.exc import IntegrityError

from models import Game, GamePlayer, Player, Queue, QueuePlayer, Session

COMMAND_PREFIX: str = "$"
DEBUG: bool = False


def debug(*args):
    if DEBUG:
        print(args)


# To be persisted
GAME_HISTORY = []

# List of players to be randomized into a queue
QUEUE_WAITING_ROOM: Dict[Queue, List[str]] = defaultdict(list)

ADMINS: Set[str] = set()
BANNED_PLAYERS: Set[str] = set()
RE_ADD_DELAY: int = 1


async def embed_message(channel: TextChannel, content: str):
    """
    TODO: Figure out how to use https://discordpy.readthedocs.io/en/stable/api.html#discord.Embed
    """
    await channel.send("`" + content + "`")


def is_in_game(player_id: int) -> bool:
    return get_player_game(player_id) is not None


def get_player_game(player_id: int) -> Game:
    """
    Find the game a player is currently in, excluding finished games
    """
    session = Session()
    game_players = [
        gp
        for gp in session.query(GamePlayer)
        .join(Game)
        .filter(Game.winning_team == None, GamePlayer.player_id == player_id)
    ]
    if len(game_players) > 0:
        return session.query(Game).filter(Game.id == GamePlayer.game_id)[0]
    else:
        return None


# Commands start here


async def add(message: Message, author: Member, args: List[str]):
    """
        Players adds self to queue(s)

        If no args to all existing queues

        TODO:
        - Player must be eligible for queue
        - Player queued if just finished game?

        opsayo added to: LTgold, LTsilver, LTpug, LTunrated
    LTgold [3/10] LTsilver [3/10] LTpug [3/10] LTunrated [3/10] LTirregular [0/10]
    """
    if is_in_game(author.id):
        await embed_message(message.channel, f"{author} you are already in a game")
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

    queues_added_to = []
    session = Session()
    player = Player(id=author.id, name=author.name)
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
            queue = session.query(Queue).filter(Queue.name == queue_name)
            if len(queue) > 0:
                queues_to_add.append(queue[0])
            else:
                await embed_message(
                    message.channel, f"could not find queue: {queue_name}"
                )

    for queue in queues_to_add:
        session.add(QueuePlayer(queue_id=queue.id, player_id=player.id))
        try:
            session.commit()
            queues_added_to.append(queue.name)
        except IntegrityError:
            session.rollback()
        else:
            queue_players: List[QueuePlayer] = [
                qp
                for qp in session.query(QueuePlayer).filter(
                    QueuePlayer.queue_id == queue.id
                )
            ]
            if len(queue_players) == queue.size:  # Pop!
                # TODO: Create voice channels
                game = Game(queue_id=queue.id)
                session.add(game)

                # TODO: Import even teams using trueskill
                team0 = queue_players[: len(queue_players) // 2]
                team1 = queue_players[len(queue_players) // 2 :]

                for queue_player in team0:
                    game_player = GamePlayer(
                        game_id=game.id, player_id=queue_player.player_id, team=0
                    )
                    session.add(game_player)

                for queue_player in team1:
                    game_player = GamePlayer(
                        game_id=game.id, player_id=queue_player.player_id, team=1
                    )
                    session.add(game_player)

                await embed_message(
                    message.channel, f"{queue.name} popped! BE: {team0}, DS:, {team1}"
                )

                session.query(QueuePlayer).delete()
                session.commit()
                return

    await embed_message(
        message.channel, f"{player.name} added to queues {queues_added_to}"
    )


async def add_admin(message: Message, author: Member, args: List[str]):
    """
    TODO:
    - Decorator for admin permissions
    """
    await embed_message(message.channel, "not implemented")
    if len(args) != 1:
        await embed_message(message.channel, "usage: !addadmin <player_name>")
    elif author not in ADMINS:
        await embed_message(message.channel, "you must be an admin to use this command")
    elif args[0] in ADMINS:
        await embed_message(message.channel, f"{args[0]} is already an admin")
    else:
        ADMINS.add(args[0])
        await embed_message(message.channel, f"{args[0]} added to admins")


async def ban(message: Message, author: Member, args: List[str]):
    """TODO: remove player from queues"""
    await embed_message(message.channel, "not implemented")
    if len(args) != 1:
        await embed_message(message.channel, f"usage: !ban <player_name>")
    elif author not in ADMINS:
        await embed_message(message.channel, "you must be an admin to use this command")
    elif args[0] in BANNED_PLAYERS:
        await embed_message(message.channel, f"{args[0]} is already banned")
    else:
        BANNED_PLAYERS.add(args[0])
        await embed_message(message.channel, f"{args[0]} added to ban list")


async def cancel_game(message: Message, author: Member, args: List[str]):
    await embed_message(message.channel, "not implemented")
    if len(args) == 1:
        if author not in ADMINS:
            await embed_message(
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


async def coinflip(message: Message, author: Member, args: List[str]):
    result = "`HEADS`" if floor(random() * 2) == 0 else "`TAILS`"
    await embed_message(message.channel, result)


async def commands(message: Message, author: Member, args: List[str]):
    output = "commands:"
    for command in COMMANDS:
        output += f"\n{COMMAND_PREFIX}{command}"

    await embed_message(message.channel, output)


async def create_queue(message: Message, author: Member, args: List[str]):
    if len(args) != 2:
        await embed_message(
            message.channel, "usage: !createqueue <queue_name> <queue_size>"
        )
        return
    queue_size = int(args[1])
    if queue_size % 2 != 0:
        await embed_message(message.channel, f"queue size must be even: {queue_size}")
        return

    queue = Queue(name=args[0], size=int(args[1]))
    session = Session()

    try:
        session.add(queue)
        session.commit()
        await embed_message(message.channel, f"queue created: {queue}")
    except IntegrityError:
        session.rollback()
        await embed_message(message.channel, "queue already exists")


async def del_(message: Message, author: Member, args: List[str]):
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

    queues_removed_from = []
    for queue in queues_to_del:
        session.query(QueuePlayer).filter(
            QueuePlayer.queue_id == queue.id, QueuePlayer.player_id == author.id
        ).delete()
        session.commit()
        queues_removed_from.append(queue.name)
    await embed_message(message.channel, f"removed from queues: {queues_removed_from}")


async def finish_game(message: Message, author: Member, args: List[str]):
    """
    TODO
    - Player must be a captain?
    """
    if len(args) == 0:
        await embed_message(message.channel, "usage: !finishgame <win|loss|tie>")
        return

    session = Session()
    game_players = [
        gp for gp in session.query(GamePlayer).filter(GamePlayer.player_id == author.id)
    ]
    if len(game_players) == 0:
        await embed_message(
            message.channel, "you must be in a game to use that command"
        )
        return

    game = session.query(Game).filter(Game.id == game_players[0].game_id)[0]
    if args[0] == "win":
        game.winning_team = game_players[0].team
    elif args[0] == "loss":
        game.winning_team = (game_players[0].team + 1) % 2
    elif args[0] == "draw":
        game.winning_team = -1
    else:
        await embed_message(message.channel, "usage: !finishgame <win|loss|tie>")

    session.add(game)
    session.commit()
    await embed_message(message.channel, f"game {game.id} finished")


async def list_bans(message: Message, author: Member, args: List[str]):
    await embed_message(message.channel, "not implemented")
    print("[list_bans]", BANNED_PLAYERS)


async def remove_admin(message: Message, author: Member, args: List[str]):
    await embed_message(message.channel, "not implemented")
    if len(args) != 1:
        await embed_message(
            message.channel, "[remove_admin] usage: !removeadmin <player_name>"
        )
    elif author not in ADMINS:
        await embed_message(
            message.channel, "[remove_admin] you must be an admin to use this command"
        )
        return
    elif args[0] not in ADMINS:
        await embed_message(
            message.channel, "[remove_admin]", args[0], "is not an admin"
        )
    elif args[0] == author:
        await embed_message(
            message.channel, "[remove_admin] you cannot remove yourself as an admin"
        )
    else:
        ADMINS.remove(args[0])
        await embed_message(
            message.channel, "[remove_admin]", args[0], "removed from admins"
        )


async def remove_queue(message: Message, author: Member, args: List[str]):
    if len(args) == 0:
        await embed_message(message.channel, "usage: !remove_queue <queue_name>")
        return
    session = Session()
    session.query(Queue).filter(Queue.name == args[0]).delete()
    session.commit()
    await embed_message(message.channel, f"queue removed: {args[0]}")


async def set_add_delay(message: Message, author: Member, args: List[str]):
    await embed_message(message.channel, "not implemented")
    if len(args) != 1:
        print("[set_add_delay] usage: !setadddelay <delay_seconds>")
        return
    global RE_ADD_DELAY
    RE_ADD_DELAY = int(args[0])
    print("[set_add_delay] timer to re-add to games set to", RE_ADD_DELAY)


async def set_command_prefix(message: Message, author: Member, args: List[str]):
    if len(args) != 1:
        await embed_message(message.channel, "usage: !setcommandprefix <prefix>")
        return
    global COMMAND_PREFIX
    COMMAND_PREFIX = args[0]
    await embed_message(message.channel, f"command prefix set to {COMMAND_PREFIX}")


async def status(message: Message, author: Member, args: List[str]):
    session = Session()
    queues = [q for q in session.query(Queue)]
    games = [g for g in session.query(Game).filter(Game.winning_team == None)]
    games_by_queue = dict([(g.queue_id, g) for g in games])
    output = ""
    for queue in queues:
        players_in_queue = [
            p
            for p in session.query(Player)
            .join(QueuePlayer)
            .filter(QueuePlayer.queue_id == queue.id)
        ]
        output += f"**{queue.name}** [{len(players_in_queue)} / {queue.size}]"

        if len(players_in_queue) > 0:
            output += f"\n**IN QUEUE:** "
            output += ", ".join([player.name for player in players_in_queue])
            output += "\n"

        if queue.id in games_by_queue:
            for game in games_by_queue[queue.id]:
                game_players = [
                    p
                    for p in session.query(Player)
                    .join(GamePlayer)
                    .filter(GamePlayer.game_id == game.id)
                ]
                output += f"**IN GAME:** "
                team0 = filter(lambda x: x.team == 0, game_players)
                team1 = filter(lambda x: x.team == 1, game_players)
                output += f", ".join([player.name for player in team0])
                output += f", ".join([player.name for player in team1])
                output += "\n"

    await embed_message(message.channel, output)


async def sub(message: Message, author: Member, args: List[str]):
    """
    Substitute one player in a game for another
    """
    await embed_message(message.channel, "not implemented")
    if len(args) != 1:
        print("[sub] usage: !sub <player_name>")
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


async def unban(message: Message, author: Member, args: List[str]):
    await embed_message(message.channel, "not implemented")
    if len(args) != 1:
        print("[unban] usage: !unban <player_name>")
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
    await COMMANDS[command](message, message.author, message.content.split(" ")[1:])
