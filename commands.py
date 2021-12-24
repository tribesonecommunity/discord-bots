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
from math import ceil, floor
from random import random
from time import clock_gettime
from typing import Dict, List, Set
from uuid import uuid4

from discord import Member, Message
from sqlalchemy.exc import IntegrityError

from models import Player, Queue, QueuePlayer, Session

COMMAND_PREFIX: str = "!"
DEBUG: bool = False


def debug(*args):
    if DEBUG:
        print(args)


# Models start here
# Some of these models are mocks of discord models exist so that the commands can be invoked from a script rather than needing to be hooked up to discord
@dataclass
class Game:
    queue_name: str
    team1: List[str]
    team2: List[str]
    id: str = str(uuid4()).split("-")[0]

    def contains_player(self, player_name):
        return player_name in self.team1 or player_name in self.team2


@dataclass
class Role:
    name: str
    id: str = str(uuid4()).split("-")[0]


@dataclass
class Guild:
    roles: List[Role] = field(default_factory=lambda: [Role("LTpug")])


@dataclass
class Member:
    guild: Guild = Guild()


@dataclass(frozen=True)
class Author:
    name: str
    id: int = floor(random() * 2 ** 32)


@dataclass
class Message:
    author: Member
    content: str


@dataclass
class QueuePlayers:
    queue: Queue
    players: List[str] = field(default_factory=list)


# Test models end here


# To be persisted
GAME_HISTORY = []

GAMES: Dict[str, List[Game]] = defaultdict(
    list
)  # Map queue names to players in the game
# QUEUES: Dict[Queue, List[str]] = {}  # TODO: Persist queues

# List of players to be randomized into a queue
QUEUE_WAITING_ROOM: Dict[Queue, List[str]] = defaultdict(list)

ADMINS: Set[str] = set()
BANNED_PLAYERS: Set[str] = set()
RE_ADD_DELAY: int = 1


def is_in_game(player_name) -> bool:
    return get_player_game(player_name) is not None


def get_player_game(player_name) -> Game:
    for games in GAMES.values():
        for game in games:
            if game.contains_player(player_name):
                return game
    return None


# Commands start here


def add(author: Member, args: List[str]):
    """
        Players adds self to queue(s)

        If no args to all existing queues

        TODO:
        - Player must be eligible for queue
        - Player queued if just finished game?

        opsayo added to: LTgold, LTsilver, LTpug, LTunrated
    LTgold [3/10] LTsilver [3/10] LTpug [3/10] LTunrated [3/10] LTirregular [0/10]
    """
    if is_in_game(author):
        print("[add]", author, "you are already in a game")
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
            queue = session.query(Queue).filter(Queue.name == queue_name)[0]
            queues_to_add.append(queue)

    for queue in queues_to_add:
        session.add(QueuePlayer(queue_id=queue.id, player_id=player.id))
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
        else:
            queue_players = [
                qp
                for qp in session.query(QueuePlayer).filter(
                    QueuePlayer.queue_id == queue.id
                )
            ]
            print(queue_players)
            print("[add]", author, "added to", queue.name)
            if len(queue_players) == queue.size:
                # TODO: Import even teams using trueskill
                # TODO: Create voice channels
                game = Game(
                    queue_name,
                    queue.players[0 : queue.size // 2],
                    queue.players[queue.size // 2 :],
                )

                GAMES[queue_name].append(game)
                print(
                    "[add]",
                    queue_name,
                    "game popped, BE:",
                    game.team1,
                    "DS:",
                    game.team2,
                )

                queue.players.clear()
                break


def add_admin(author: Member, args: List[str]):
    """
    TODO:
    - Decorator for admin permissions
    """
    if len(args) != 1:
        print("[add_admin] usage: !addadmin <player_name>")
    elif author not in ADMINS:
        print("[add_admin] you must be an admin to use this command")
        return
    elif args[0] in ADMINS:
        print("[add_admin]", args[0], "is already an admin")
    else:
        ADMINS.add(args[0])
        print("[add_admin]", args[0], "added to admins")


def ban(author: Member, args: List[str]):
    """TODO: remove player from queues"""
    if len(args) != 1:
        print("[ban] usage: !ban <player_name>")
        return
    elif author not in ADMINS:
        print("[ban] you must be an admin to use this command")
        return
    elif args[0] in BANNED_PLAYERS:
        print("[ban]", args[0], "is already banned")
        return
    else:
        BANNED_PLAYERS.add(args[0])
        print("[ban]", args[0], "added to ban list")
        return


def cancel_game(author: Member, args: List[str]):
    if len(args) == 1:
        if author not in ADMINS:
            print("[cancel_game] you must be an admin to cancel a game by its id")
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
        author_game = get_player_game(author)
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


def coinflip(author: Member, args: List[str]):
    print("[coinflip]", floor(random() * 2))


def commands(author: Member, args: List[str]):
    for command in COMMANDS:
        print(command)


def create_queue(author: Member, args: List[str]):
    if len(args) < 1 or len(args) > 2:
        print(
            "[create_queue] usage: !createqueue <queue_name> <queue_size (optional)> "
        )
        return
    debug("[create_queue]", args)
    queue_size = int(args[1])
    if queue_size % 2 != 0:
        print("[create_queue] queue size must be even:", queue_size)
        return

    queue = Queue(name=args[0], size=int(args[1]))
    session = Session()

    try:
        session.add(queue)
        session.commit()
        print("[create_queue] queue created:", queue)
    except IntegrityError:
        session.rollback()
        print("[create_queue] queue already exists")


def del_(author: Member, args: List[str]):
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
            QueuePlayer.queue_id == queue.id, QueuePlayer.player_id == author.id
        ).delete()
        session.commit()


def finish_game(author: Member, args: List[str]):
    """
    TODO
    - Player must be a captain?
    """
    if len(args) == 0:
        print("[finish_game] usage: !finishgame <win|loss|tie>")
        return

    for queue_name, games in GAMES.items():
        for game in games:
            if author in game.team1 or author in game.team2:
                # TODO: Record game outcome
                # TODO: Remove voice channels
                print("[finish_game]", queue_name, "game finished as", args[0])
                GAME_HISTORY.append((clock_gettime(0), game))
                GAMES[queue_name].remove(game)
                return

    print("[finish_game] you must be in a game to use that command")


def list_bans(author: Member, args: List[str]):
    print("[list_bans]", BANNED_PLAYERS)


def list_queues(author: Member, args: List[str]):
    print("[list_queues]:")
    for queue in Session().query(Queue):
        print(queue)


def remove_admin(author: Member, args: List[str]):
    if len(args) != 1:
        print("[remove_admin] usage: !removeadmin <player_name>")
    elif author not in ADMINS:
        print("[remove_admin] you must be an admin to use this command")
        return
    elif args[0] not in ADMINS:
        print("[remove_admin]", args[0], "is not an admin")
    elif args[0] == author:
        print("[remove_admin] you cannot remove yourself as an admin")
    else:
        ADMINS.remove(args[0])
        print("[remove_admin]", args[0], "removed from admins")


def remove_queue(author: Member, args: List[str]):
    if len(args) == 0:
        print("[remove_queue] usage: !remove_queue <queue_name>")
        return
    debug("[remove_queue]", args)
    session = Session()
    session.query(Queue).filter(Queue.name == args[0]).delete()
    session.commit()
    print("[remove_queue] queue removed:", args[0])


def set_add_delay(author: Member, args: List[str]):
    if len(args) != 1:
        print("[set_add_delay] usage: !setadddelay <delay_seconds>")
        return
    global RE_ADD_DELAY
    RE_ADD_DELAY = int(args[0])
    print("[set_add_delay] timer to re-add to games set to", RE_ADD_DELAY)


def set_command_prefix(author: Member, args: List[str]):
    if len(args) != 1:
        print("[set_command_prefix] usage: !setcommandprefix prefix")
        return
    global COMMAND_PREFIX
    COMMAND_PREFIX = args[0]
    print("[set_command_prefix] command prefix set to", COMMAND_PREFIX)


def status(author: Member, args: List[str]):
    print("[status] queues", QUEUES)
    print("[status] games", GAMES)


def sub(author: Member, args: List[str]):
    """
    Substitute one player in a game for another
    """
    if len(args) != 1:
        print("[sub] usage: !sub <player_name>")
        return

    callee = args[0]
    if is_in_game(author):
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
        callee_game.team2.remove(callee)
        callee_game.team2.append(author)

    print("[sub] swapped", callee, "for", author, "in", callee_game.queue_name)


def unban(author: Member, args: List[str]):
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
    "listqueues": list_queues,
    "removeadmin": remove_admin,
    "removequeue": remove_queue,
    "setadddelay": set_add_delay,
    "setcommandprefix": set_command_prefix,
    "status": status,
    "sub": sub,
    "unban": unban,
}


def on_message(message: Message):
    print()
    print("[on_message] message:", message)
    command = message.content.split(" ")[0]

    if not command.startswith(COMMAND_PREFIX):
        debug("[on_message] not command", command)
        return

    command = command[1:]
    if command not in COMMANDS:
        print("[on_message] exiting - command not found:", command)
        return

    debug("[on_message] command:", command)
    COMMANDS[command](message.author, message.content.split(" ")[1:])
