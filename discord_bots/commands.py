from collections import defaultdict
from datetime import datetime, timedelta, timezone
from glob import glob
from math import floor
from os import remove
from random import randint, random, shuffle
from shutil import copyfile
from statistics import mean
from typing import Awaitable, Callable

import numpy
from discord import Colour, DMChannel, Embed, GroupChannel, Message, TextChannel
from discord.guild import Guild
from sqlalchemy.exc import IntegrityError
from trueskill import Rating, rate

from discord_bots.utils import (
    pretty_format_team,
    short_uuid,
    update_current_map_to_next_map_in_rotation,
    win_probability,
)

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
    Queue,
    QueuePlayer,
    QueueRole,
    QueueWaitlist,
    QueueWaitlistPlayer,
    RotationMap,
    Session,
    SkipMapVote,
    VoteableMap,
)
from .names import generate_be_name, generate_ds_name

AFK_TIME_MINUTES: int = 45
COMMAND_PREFIX: str = "!"
# The number of votes needed to succeed a map skip / replacement
MAP_VOTE_THRESHOLD: int = 10
RE_ADD_DELAY: int = 45
TEAM_NAMES: bool = True


def get_even_teams(player_ids: list[int], is_rated: bool) -> tuple[list[Player], float]:
    """
    TODO: Tests

    Try to figure out even teams, the first half of the returning list is
    the first team, the second half is the second team.

    :returns: list of players and win probability for the first team
    """
    session = Session()
    players: list[Player] = (
        session.query(Player).filter(Player.id.in_(player_ids)).all()  # type: ignore
    )
    best_win_prob_so_far: float = 0.0
    best_teams_so_far: list[Player] = []

    # Use a fixed number of shuffles instead of generating permutations. There
    # are 3.6 million permutations!
    for _ in range(500):
        shuffle(players)
        if is_rated:
            team0_ratings = list(
                map(
                    lambda x: Rating(x.rated_trueskill_mu, x.rated_trueskill_sigma),
                    players[: len(players) // 2],
                )
            )
            team1_ratings = list(
                map(
                    lambda x: Rating(x.rated_trueskill_mu, x.rated_trueskill_sigma),
                    players[len(players) // 2 :],
                )
            )
        else:
            team0_ratings = list(
                map(
                    lambda x: Rating(x.unrated_trueskill_mu, x.unrated_trueskill_sigma),
                    players[: len(players) // 2],
                )
            )
            team1_ratings = list(
                map(
                    lambda x: Rating(x.unrated_trueskill_mu, x.unrated_trueskill_sigma),
                    players[len(players) // 2 :],
                )
            )

        win_prob = win_probability(team0_ratings, team1_ratings)
        current_team_evenness = abs(0.50 - win_prob)
        best_team_evenness_so_far = abs(0.50 - best_win_prob_so_far)
        if current_team_evenness < best_team_evenness_so_far:
            best_win_prob_so_far = win_prob
            best_teams_so_far = players[:]

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
        players, win_prob = get_even_teams(player_ids, is_rated=queue.is_rated)
        if queue.is_rated:
            average_trueskill = mean(list(map(lambda x: x.rated_trueskill_mu, players)))
        else:
            average_trueskill = mean(
                list(map(lambda x: x.unrated_trueskill_mu, players))
            )
        current_map: CurrentMap | None = session.query(CurrentMap).first()
        game = InProgressGame(
            average_trueskill=average_trueskill,
            map_full_name=current_map.full_name if current_map else "",
            map_short_name=current_map.short_name if current_map else "",
            queue_id=queue.id,
            team0_name=generate_be_name() if TEAM_NAMES else "Blood Eagle",
            team1_name=generate_ds_name() if TEAM_NAMES else "Diamond Sword",
            win_probability=win_prob,
        )
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
        # channel_message = f"Game '{queue.name}' ({short_game_id}) (TS: {round(game.average_trueskill, 2)}) has begun!"
        channel_message = f"Game '{queue.name}' ({short_game_id}) has begun!"
        channel_embed = ""
        channel_embed += f"**Map: {game.map_full_name} ({game.map_short_name})**\n"
        channel_embed += pretty_format_team(game.team0_name, win_prob, team0_players)
        channel_embed += pretty_format_team(
            game.team1_name, 1 - win_prob, team1_players
        )

        await send_message(
            channel,
            content=channel_message,
            embed_description=channel_embed,
            colour=Colour.blue(),
        )

        categories = {category.id: category for category in guild.categories}
        tribes_voice_category = categories[TRIBES_VOICE_CATEGORY_CHANNEL_ID]

        be_channel = await guild.create_voice_channel(
            f"{game.team0_name}",
            category=tribes_voice_category,
        )
        ds_channel = await guild.create_voice_channel(
            f"{game.team1_name}",
            category=tribes_voice_category,
        )
        session.add(
            InProgressGameChannel(in_progress_game_id=game.id, channel_id=be_channel.id)
        )
        session.add(
            InProgressGameChannel(in_progress_game_id=game.id, channel_id=ds_channel.id)
        )

        session.query(QueuePlayer).filter(QueuePlayer.queue_id == queue_id).delete()
        session.query(MapVote).delete()
        session.query(SkipMapVote).delete()
        session.commit()
        update_current_map_to_next_map_in_rotation()
        return True, True
    return True, False


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


def finished_game_str(finished_game: FinishedGame) -> str:
    """
    Helper method to pretty print a finished game
    """
    output = ""
    session = Session()
    short_game_id = short_uuid(finished_game.game_id)
    # output += f"**{finished_game.queue_name}** ({short_game_id}) (TS: {round(finished_game.average_trueskill, 2)})"
    output += f"**{finished_game.queue_name}** ({short_game_id})"
    team0_fg_players: list[FinishedGamePlayer] = session.query(
        FinishedGamePlayer
    ).filter(
        FinishedGamePlayer.finished_game_id == finished_game.id,
        FinishedGamePlayer.team == 0,
    )
    team1_fg_players: list[FinishedGamePlayer] = session.query(
        FinishedGamePlayer
    ).filter(
        FinishedGamePlayer.finished_game_id == finished_game.id,
        FinishedGamePlayer.team == 1,
    )
    team0_player_ids = set(map(lambda x: x.player_id, team0_fg_players))
    team1_player_ids = set(map(lambda x: x.player_id, team1_fg_players))
    team0_players = session.query(Player).filter(Player.id.in_(team0_player_ids))  # type: ignore
    team1_players = session.query(Player).filter(Player.id.in_(team1_player_ids))  # type: ignore
    team0_names = ", ".join(sorted([player.name for player in team0_players]))
    team1_names = ", ".join(sorted([player.name for player in team1_players]))
    team0_win_prob = round(100 * finished_game.win_probability, 1)
    team1_win_prob = 100 - team0_win_prob
    if finished_game.winning_team == 0:
        output += f"\n**{finished_game.team0_name} ({team0_win_prob}%): {team0_names}**"
        output += f"\n{finished_game.team1_name} ({team1_win_prob}%): {team1_names}"
    elif finished_game.winning_team == 1:
        output += f"\n{finished_game.team0_name} ({team0_win_prob}%): {team0_names}"
        output += f"\n**{finished_game.team1_name} ({team1_win_prob}%): {team1_names}**"
    else:
        output += f"\n{finished_game.team0_name} ({team0_win_prob}%): {team0_names}"
        output += f"\n{finished_game.team1_name} ({team1_win_prob}%): {team1_names}"
    minutes_ago = (
        datetime.now(timezone.utc)
        - finished_game.finished_at.replace(tzinfo=timezone.utc)
    ).seconds // 60
    if minutes_ago < 60:
        output += f"\n@ {minutes_ago} minutes ago\n"
    else:
        hours_ago = minutes_ago // 60
        output += f"\n@ {hours_ago} hours ago\n"

    return output


TRIBES_VOICE_CATEGORY_CHANNEL_ID: int = 462824101753520138


def is_in_game(player_id: int) -> bool:
    return get_player_game(player_id, Session()) is not None


def get_player_game(player_id: int, session=Session()) -> InProgressGame | None:
    """
    Find the game a player is currently in

    :session: Pass in a session if you want to do something with the game that
    gets returned
    """
    ipg_player = (
        session.query(InProgressGamePlayer)
        .join(InProgressGame)
        .filter(InProgressGamePlayer.player_id == player_id)
        .first()
    )
    if ipg_player:
        return (
            session.query(InProgressGame)
            .filter(InProgressGame.id == ipg_player.in_progress_game_id)
            .first()
        )
    else:
        return None


# Commands start here


async def add(message: Message, args: list[str]):
    """
    Players adds self to queue(s). If no args to all existing queues

    Players can also add to a queue by its index. The index starts at 1.
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
    all_queues = session.query(Queue).order_by(Queue.created_at.asc()).all()  # type: ignore
    if len(args) == 0:
        queues_to_add += all_queues
    else:
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

    queues_added_to = []
    for queue in queues_to_add:
        if queue.is_locked:
            continue

        if is_waitlist and most_recent_game:
            queue_waitlist = (
                session.query(QueueWaitlist)
                .filter(QueueWaitlist.finished_game_id == most_recent_game.id)
                .first()
            )
            if queue_waitlist:
                session.add(
                    QueueWaitlistPlayer(
                        queue_waitlist_id=queue_waitlist.id,
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
    for queue in all_queues:
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
async def add_rotation_map(message: Message, args: list[str]):
    if len(args) != 2:
        await send_message(
            message.channel,
            embed_description="Usage: !addrotationmap <map_short_name> <map_full_name>",
            colour=Colour.red(),
        )
        return

    session = Session()
    session.add(RotationMap(args[1], args[0]))
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        await send_message(
            message.channel,
            embed_description=f"Error adding map {args[1]} ({args[0]}) to rotation. Does it already exist?",
            colour=Colour.red(),
        )
        return

    await send_message(
        message.channel,
        embed_description=f"{args[1]} ({args[0]}) added to map rotation",
        colour=Colour.green(),
    )


@require_admin
async def add_voteable_map(message: Message, args: list[str]):
    if len(args) != 2:
        await send_message(
            message.channel,
            embed_description="Usage: !addvoteablemap <map_short_name> <map_full_name>",
            colour=Colour.red(),
        )
        return

    session = Session()
    session.add(VoteableMap(args[1], args[0]))
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        await send_message(
            message.channel,
            embed_description=f"Error adding voteable map {args[1]} ({args[0]}). Does it already exist?",
            colour=Colour.red(),
        )
        return

    await send_message(
        message.channel,
        embed_description=f"{args[1]} ({args[0]}) added to voteable map pool",
        colour=Colour.green(),
    )


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


async def create_command(message: Message, args: list[str]):
    if len(args) != 2:
        await send_message(
            message.channel,
            embed_description="Usage: !createcommand <name> <output>",
            colour=Colour.red(),
        )
        return
    name = args[0]
    output = args[1]

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


@require_admin
async def create_db_backup(message: Message, args: list[str]):
    date_string = datetime.now().strftime("%Y-%m-%d")
    copyfile(f"{DB_NAME}.db", f"{DB_NAME}_{date_string}.db")
    await send_message(
        message.channel,
        embed_description=f"Backup made to {DB_NAME}_{date_string}.db",
        colour=Colour.green(),
    )


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
    queue = session.query(Queue).filter(Queue.name.ilike(args[0])).first()  # type: ignore
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


@require_admin
async def decay_player(message: Message, args: list[str]):
    """
    Manually adjust a player's trueskill rating downward by a percentage
    """
    if len(args) != 2:
        await send_message(
            message.channel,
            embed_description="Usage: !decayplayer @<player> <decay_amount_percent>%",
            colour=Colour.red(),
        )
        return

    decay_amount = args[1]
    if not decay_amount.endswith("%"):
        await send_message(
            message.channel,
            embed_description="Decay amount must end with %",
            colour=Colour.red(),
        )
        return

    decay_amount = int(decay_amount[:-1])
    if decay_amount <= 0 or decay_amount > 10:
        await send_message(
            message.channel,
            embed_description="Decay amount must be between 0-10",
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
        embed_description=f"{message.mentions[0].name} decayed by {decay_amount}%",
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


async def del_(message: Message, args: list[str]):
    """
    Players deletes self from queue(s)

    If no args deletes from existing queues
    """
    session = Session()
    queues_to_del: list[Queue] = []
    all_queues: list(Queue) = session.query(Queue).order_by(Queue.created_at.asc()).all()  # type: ignore
    if len(args) == 0:
        queues_to_del += session.query(Queue).all()
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

    queue_statuses = []
    queue: Queue
    for queue in all_queues:  # type: ignore
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
    session.commit()


@require_admin
async def disable_team_names(message: Message, args: list[str]):
    global TEAM_NAMES
    TEAM_NAMES = False
    await send_message(
        message.channel,
        embed_description="Team names disabled",
        colour=Colour.blue(),
    )


@require_admin
async def edit_game_winner(message: Message, args: list[str]):
    if len(args) != 2:
        await send_message(
            message.channel,
            embed_description="Usage: !editgamewinner <game_id> <tie|be|ds>",
            colour=Colour.red(),
        )
        return

    session = Session()
    game: FinishedGame | None = (
        session.query(FinishedGame)
        .filter(FinishedGame.game_id.startswith(args[0]))
        .first()
    )
    if not game:
        await send_message(
            message.channel,
            embed_description=f"Could not find game: {args[0]}",
            colour=Colour.red(),
        )
        return
    outcome = args[1].lower()
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
        embed_description=f"Game {args[0]} outcome changed:\n\n"
        + finished_game_str(game),
        colour=Colour.green(),
    )


@require_admin
async def enable_team_names(message: Message, args: list[str]):
    global TEAM_NAMES
    TEAM_NAMES = True
    await send_message(
        message.channel,
        embed_description="Team names enabled",
        colour=Colour.blue(),
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
            team0_rated_ratings_before.append(
                Rating(player.rated_trueskill_mu, player.rated_trueskill_sigma)
            )
            team0_unrated_ratings_before.append(
                Rating(player.unrated_trueskill_mu, player.unrated_trueskill_sigma)
            )
        else:
            team1_players.append(in_progress_game_player)
            team1_rated_ratings_before.append(
                Rating(player.rated_trueskill_mu, player.rated_trueskill_sigma)
            )
            team1_unrated_ratings_before.append(
                Rating(player.unrated_trueskill_mu, player.unrated_trueskill_sigma)
            )

    finished_game = FinishedGame(
        average_trueskill=in_progress_game.average_trueskill,
        finished_at=datetime.now(timezone.utc),
        game_id=in_progress_game.id,
        is_rated=queue.is_rated,
        map_full_name=in_progress_game.map_full_name,
        map_short_name=in_progress_game.map_short_name,
        queue_name=queue.name,
        started_at=in_progress_game.created_at,
        team0_name=in_progress_game.team0_name,
        team1_name=in_progress_game.team1_name,
        win_probability=in_progress_game.win_probability,
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

    team0_rated_ratings_after: list[Rating]
    team1_rated_ratings_after: list[Rating]
    team0_unrated_ratings_after: list[Rating]
    team1_unrated_ratings_after: list[Rating]
    if queue.is_rated:
        team0_rated_ratings_after, team1_rated_ratings_after = rate(
            [team0_rated_ratings_before, team1_rated_ratings_before], outcome
        )
    else:
        # Don't modify rated ratings if the queue isn't rated
        team0_rated_ratings_after, team1_rated_ratings_after = (
            team0_rated_ratings_before,
            team1_rated_ratings_before,
        )

    team0_unrated_ratings_after, team1_unrated_ratings_after = rate(
        [team0_unrated_ratings_before, team1_unrated_ratings_before], outcome
    )

    for i, team0_gip in enumerate(team0_players):
        player = players_by_id[team0_gip.player_id]
        finished_game_player = FinishedGamePlayer(
            finished_game_id=finished_game.id,
            player_id=player.id,
            player_name=player.name,
            team=team0_gip.team,
            rated_trueskill_mu_before=player.rated_trueskill_mu,
            rated_trueskill_sigma_before=player.rated_trueskill_sigma,
            rated_trueskill_mu_after=team0_rated_ratings_after[i].mu,
            rated_trueskill_sigma_after=team0_rated_ratings_after[i].sigma,
            unrated_trueskill_mu_before=player.unrated_trueskill_mu,
            unrated_trueskill_sigma_before=player.unrated_trueskill_sigma,
            unrated_trueskill_mu_after=team0_unrated_ratings_after[i].mu,
            unrated_trueskill_sigma_after=team0_unrated_ratings_after[i].sigma,
        )
        player.rated_trueskill_mu = team0_rated_ratings_after[i].mu
        player.rated_trueskill_sigma = team0_rated_ratings_after[i].sigma
        player.unrated_trueskill_mu = team0_unrated_ratings_after[i].mu
        player.unrated_trueskill_sigma = team0_unrated_ratings_after[i].sigma
        session.add(finished_game_player)
    for i, team1_gip in enumerate(team1_players):
        player = players_by_id[team1_gip.player_id]
        finished_game_player = FinishedGamePlayer(
            finished_game_id=finished_game.id,
            player_id=player.id,
            player_name=player.name,
            team=team1_gip.team,
            rated_trueskill_mu_before=player.rated_trueskill_mu,
            rated_trueskill_sigma_before=player.rated_trueskill_sigma,
            rated_trueskill_mu_after=team1_rated_ratings_after[i].mu,
            rated_trueskill_sigma_after=team1_rated_ratings_after[i].sigma,
            unrated_trueskill_mu_before=player.rated_trueskill_mu,
            unrated_trueskill_sigma_before=player.rated_trueskill_sigma,
            unrated_trueskill_mu_after=team1_unrated_ratings_after[i].mu,
            unrated_trueskill_sigma_after=team1_unrated_ratings_after[i].sigma,
        )
        player.rated_trueskill_mu = team1_rated_ratings_after[i].mu
        player.rated_trueskill_sigma = team1_rated_ratings_after[i].sigma
        player.unrated_trueskill_mu = team1_unrated_ratings_after[i].mu
        player.unrated_trueskill_sigma = team1_unrated_ratings_after[i].sigma
        session.add(finished_game_player)

    session.query(InProgressGamePlayer).filter(
        InProgressGamePlayer.in_progress_game_id == in_progress_game.id
    ).delete()
    session.query(InProgressGame).filter(
        InProgressGame.id == in_progress_game.id
    ).delete()

    embed_description = ""
    if winning_team == 0:
        embed_description = f"**Winner:** {in_progress_game.team0_name}"
    elif winning_team == 1:
        embed_description = f"**Winner:** {in_progress_game.team1_name}"
    else:
        embed_description = "**Tie game**"

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
    session.commit()

    short_in_progress_game_id = in_progress_game.id.split("-")[0]
    await send_message(
        message.channel,
        # content=f"Game '{queue.name}' ({short_in_progress_game_id}) (TS: {round(finished_game.average_trueskill, 2)}) finished",
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


@require_admin
async def list_db_backups(message: Message, args: list[str]):
    output = "Backups:"
    for filename in glob("tribes_*.db"):
        output += f"\n- {filename}"

    await send_message(
        message.channel,
        embed_description=output,
        colour=Colour.blue(),
    )


async def list_commands(message: Message, args: list[str]):
    output = "Commands:"
    for command in Session().query(CustomCommand):
        output += f"\n- {command.name}"
    await send_message(message.channel, embed_description=output, colour=Colour.blue())


async def list_map_rotation(message: Message, args: list[str]):
    output = "Map rotation:"
    rotation_map: RotationMap
    for rotation_map in Session().query(RotationMap).order_by(RotationMap.created_at.asc()):  # type: ignore
        output += f"\n- {rotation_map.full_name} ({rotation_map.short_name})"
    await send_message(message.channel, embed_description=output, colour=Colour.blue())


async def list_player_decays(message: Message, args: list[str]):
    if len(args) != 1:
        await send_message(
            message.channel,
            embed_description="Usage: !listplayerdecays @<player>",
            colour=Colour.red(),
        )
        return

    session = Session()
    player = session.query(Player).filter(Player.id == message.mentions[0].id).first()
    player_decays: list[PlayerDecay] = session.query(PlayerDecay).filter(
        PlayerDecay.player_id == player.id
    )
    output = f"Decays for {player.name}:"
    for player_decay in player_decays:
        output += f"\n- {player_decay.decayed_at.strftime('%Y-%m-%d')} - Amount: {player_decay.decay_percentage}%"

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


async def list_voteable_maps(message: Message, args: list[str]):
    output = "Voteable map pool"
    voteable_map: VoteableMap
    for voteable_map in Session().query(VoteableMap).order_by(VoteableMap.full_name):
        output += f"\n- {voteable_map.full_name} ({voteable_map.short_name})"
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
    if message.author.id != 115204465589616646:
        await send_message(
            message.channel,
            embed_description="Only special people can use this command",
            colour=Colour.red(),
        )
        return

    if len(args) != 2:
        await send_message(
            message.channel,
            embed_description="Usage: !mockrandomqueue <queue_name> <count>",
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
    queue = session.query(Queue).filter(Queue.name.ilike(args[0])).first()  # type: ignore
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
async def game_history(message: Message, args: list[str]):
    """
    Display recent game history
    """
    if len(args) != 1:
        await send_message(
            message.channel,
            embed_description="Usage: !gamehistory <count>",
            colour=Colour.red(),
        )
        return
    count = int(args[0])
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


async def random_names(message: Message, args: list[str]):
    count = int(args[0])

    output = ""
    for _ in range(count):
        output += f"{generate_be_name()} vs {generate_ds_name()}\n"

    await send_message(
        message.channel,
        embed_description=output,
        colour=Colour.blue(),
    )


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


async def remove_command(message: Message, args: list[str]):
    if len(args) != 1:
        await send_message(
            message.channel,
            embed_description="Usage: !removecommand <name>",
            colour=Colour.red(),
        )
        return

    name = args[0]
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

    queue = session.query(Queue).filter(Queue.name.ilike(args[0])).first()  # type: ignore
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
async def remove_db_backup(message: Message, args: list[str]):
    if len(args) != 1:
        await send_message(
            message.channel,
            embed_description="Usage: !removedbbackup <db_filename>",
            colour=Colour.red(),
        )
        return

    db_filename = args[0]
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


@require_admin
async def remove_rotation_map(message: Message, args: list[str]):
    if len(args) != 1:
        await send_message(
            message.channel,
            embed_description="Usage: !removerotationmap <map_short_name>",
            colour=Colour.red(),
        )
        return

    session = Session()
    rotation_map = (
        session.query(RotationMap).filter(RotationMap.short_name.ilike(args[0])).first()  # type: ignore
    )
    if rotation_map:
        session.delete(rotation_map)
        session.commit()
        await send_message(
            message.channel,
            embed_description=f"{args[0]} removed from map rotation",
            colour=Colour.green(),
        )
    else:
        await send_message(
            message.channel,
            embed_description=f"Could not find rotation map: {args[0]}",
            colour=Colour.red(),
        )


@require_admin
async def remove_voteable_map(message: Message, args: list[str]):
    if len(args) != 1:
        await send_message(
            message.channel,
            embed_description="Usage: !removevoteablemap <map_short_name>",
            colour=Colour.red(),
        )
        return

    session = Session()
    voteable_map = (
        session.query(VoteableMap).filter(VoteableMap.short_name.ilike(args[0])).first()  # type: ignore
    )
    if voteable_map:
        session.query(MapVote).filter(
            MapVote.voteable_map_id == voteable_map.id
        ).delete()
        session.delete(voteable_map)
        await send_message(
            message.channel,
            embed_description=f"{args[0]} removed from voteable map pool",
            colour=Colour.green(),
        )
    else:
        await send_message(
            message.channel,
            embed_description=f"Could not find vote for map: {args[0]}",
            colour=Colour.red(),
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
        embed_description=f"Delay between games set to {RE_ADD_DELAY}",
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


@require_admin
async def set_queue_rated(message: Message, args: list[str]):
    if len(args) != 1:
        await send_message(
            message.channel,
            embed_description="Usage: !setqueuerated <queue_name>",
            colour=Colour.red(),
        )
        return

    session = Session()
    queue: Queue = session.query(Queue).filter(Queue.name.ilike(args[0])).first()  # type: ignore
    if queue:
        queue.is_rated = True
        await send_message(
            message.channel,
            embed_description=f"Queue {args[0]} is now rated",
            colour=Colour.blue(),
        )
    else:
        await send_message(
            message.channel,
            embed_description=f"Queue not found: {args[0]}",
            colour=Colour.red(),
        )
    session.commit()


@require_admin
async def set_queue_unrated(message: Message, args: list[str]):
    if len(args) != 1:
        await send_message(
            message.channel,
            embed_description="Usage: !setqueueunrated <queue_name>",
            colour=Colour.red(),
        )
        return

    session = Session()
    queue: Queue = session.query(Queue).filter(Queue.name.ilike(args[0])).first()  # type: ignore
    if queue:
        queue.is_rated = False
        await send_message(
            message.channel,
            embed_description=f"Queue {args[0]} is now unrated",
            colour=Colour.blue(),
        )
    else:
        await send_message(
            message.channel,
            embed_description=f"Queue not found: {args[0]}",
            colour=Colour.red(),
        )
    session.commit()


@require_admin
async def set_map_vote_threshold(message: Message, args: list[str]):
    if len(args) != 1:
        await send_message(
            message.channel,
            embed_description="Usage: !setmapvotethreshold <threshold>",
            colour=Colour.red(),
        )
        return

    global MAP_VOTE_THRESHOLD
    MAP_VOTE_THRESHOLD = int(args[0])

    await send_message(
        message.channel,
        embed_description=f"Map vote threshold set to {MAP_VOTE_THRESHOLD}",
        colour=Colour.green(),
    )


async def show_game(message: Message, args: list[str]):
    if len(args) != 1:
        await send_message(
            message.channel,
            embed_description="Usage: !showgame <game_id>",
            colour=Colour.red(),
        )
        return

    session = Session()
    finished_game = (
        session.query(FinishedGame)
        .filter(FinishedGame.game_id.startswith(args[0]))
        .first()
    )
    if not finished_game:
        await send_message(
            message.channel,
            embed_description=f"Could not find game: {args[0]}",
            colour=Colour.red(),
        )
        return

    await send_message(
        message.channel,
        embed_description=finished_game_str(finished_game),
        colour=Colour.blue(),
    )


async def status(message: Message, args: list[str]):
    session = Session()
    queues = session.query(Queue).order_by(Queue.created_at.asc()).all()  # type: ignore
    games_by_queue: dict[str, list[InProgressGame]] = defaultdict(list)
    for game in session.query(InProgressGame):
        if game.queue_id:
            games_by_queue[game.queue_id].append(game)

    output = ""
    current_map: CurrentMap | None = session.query(CurrentMap).first()
    if current_map:
        rotation_maps: list[RotationMap] = session.query(RotationMap).order_by(RotationMap.created_at.asc()).all()  # type: ignore
        next_rotation_map_index = (current_map.map_rotation_index + 1) % len(
            rotation_maps
        )
        next_map = rotation_maps[next_rotation_map_index]

        output += f"**Map: {current_map.full_name} ({current_map.short_name})**\n_Next: {next_map.full_name} ({next_map.short_name})_\n"
    skip_map_votes: list[SkipMapVote] = session.query(SkipMapVote).all()
    output += f"_Votes to skip: [{len(skip_map_votes)}/{MAP_VOTE_THRESHOLD}]_\n"

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
    output += f"_Votes to swap: {voted_maps_str}_\n\n"

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
                # output += f"**IN GAME** ({short_game_id}) (TS: {round(game.average_trueskill, 2)}):\n"
                output += f"**IN GAME - {game.map_short_name}** ({short_game_id}):\n"
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
    queue: Queue = session.query(Queue).filter(Queue.id == game.queue_id).first()

    game_players = (
        session.query(InProgressGamePlayer)
        .filter(InProgressGamePlayer.in_progress_game_id == game.id)
        .all()
    )
    player_ids: list[int] = list(map(lambda x: x.player_id, game_players))
    players, win_prob = get_even_teams(player_ids, is_rated=queue.is_rated)
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

    short_game_id = short_uuid(game.id)
    channel_message = f"New teams ({short_game_id}):"
    channel_embed = ""
    channel_embed += pretty_format_team(game.team0_name, win_prob, team0_players)
    channel_embed += pretty_format_team(game.team1_name, win_prob, team1_players)

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


# TODO: Unvote for many maps at once
async def unvote_swap_map(message: Message, args: list[str]):
    if len(args) != 1:
        await send_message(
            message.channel,
            embed_description="Usage: !unvoteswapmap <map_short_name>",
            colour=Colour.red(),
        )
        return

    session = Session()
    voteable_map: VoteableMap | None = session.query(VoteableMap).filter(VoteableMap.short_name.ilike(args[0])).first()  # type: ignore
    if not voteable_map:
        await send_message(
            message.channel,
            embed_description=f"Could not find voteable map: {args[0]}",
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
            embed_description=f"You don't have a vote for: {args[0]}",
            colour=Colour.red(),
        )
        return
    else:
        session.delete(map_vote)
    session.commit()
    await send_message(
        message.channel,
        embed_description=f"Your swap vote for {args[0]} was removed",
        colour=Colour.green(),
    )


async def unvote_skip_map(message: Message, args: list[str]):
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


# TODO: Vote for many maps at once
async def vote_swap_map(message: Message, args: list[str]):
    session = Session()
    if len(args) != 1:
        voteable_maps: list[VoteableMap] = session.query(VoteableMap).all()
        voteable_map_str = ", ".join(
            [voteable_map.short_name for voteable_map in voteable_maps]
        )

        await send_message(
            message.channel,
            embed_description=f"Usage: !voteswapmap <map_short_name>\nVoteable maps: {voteable_map_str}",
            colour=Colour.red(),
        )
        return

    voteable_map: VoteableMap | None = session.query(VoteableMap).filter(VoteableMap.short_name.ilike(args[0])).first()  # type: ignore
    if not voteable_map:
        voteable_maps: list[VoteableMap] = session.query(VoteableMap).all()
        voteable_map_str = ", ".join(
            [voteable_map.short_name for voteable_map in voteable_maps]
        )
        await send_message(
            message.channel,
            embed_description=f"Could not find voteable map: {args[0]}\nVoteable maps: {voteable_map_str}",
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
            embed_description=f"Vote to swap to {voteable_map.full_name} ({voteable_map.short_name}) passed!\n**New map: {voteable_map.full_name} ({voteable_map.short_name})**",
            colour=Colour.green(),
        )
        session.query(MapVote).delete()
        session.query(SkipMapVote).delete()
        # TODO: Show updated status
        # TODO: Randomly add votes into queue
        # TODO: What to do about players currently in queue?
        # TODO: Buffer for 20 seconds?
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
            embed_description=f"Added map vote to swap to {args[0]}.\nVotes to swap: {voted_maps_str}",
            colour=Colour.green(),
        )

    session.commit()


async def vote_skip_map(message: Message, args: list[str]):
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
        update_current_map_to_next_map_in_rotation()
        current_map: CurrentMap = Session().query(CurrentMap).first()
        await send_message(
            message.channel,
            embed_description=f"Vote to skip the current map passed!\n**New map: {current_map.full_name} ({current_map.short_name})**",
            colour=Colour.green(),
        )

        session.query(MapVote).delete()
        session.query(SkipMapVote).delete()
        session.commit()
    else:
        skip_map_votes: list[SkipMapVote] = session.query(SkipMapVote).all()
        await send_message(
            message.channel,
            embed_description=f"Added vote to skip the current map\nVotes to skip: [{len(skip_map_votes)}/{MAP_VOTE_THRESHOLD}]",
            colour=Colour.green(),
        )


# Commands end here


COMMANDS = {
    "add": add,
    "addadmin": add_admin,
    "addadminrole": add_admin_role,
    "addqueuerole": add_queue_role,
    "addrotationmap": add_rotation_map,
    "addvoteablemap": add_voteable_map,
    "ban": ban,
    "cancelgame": cancel_game,
    "coinflip": coinflip,
    "commands": commands,
    "createcommand": create_command,
    "createdbbackup": create_db_backup,
    "createqueue": create_queue,
    "clearqueue": clear_queue,
    # "decayplayer": decay_player,
    "del": del_,
    "disableteamnames": disable_team_names,
    "editgamewinner": edit_game_winner,
    "enableteamnames": enable_team_names,
    "finishgame": finish_game,
    # "forcemapswap": force_swap_map,
    # "forcemaprotation": force_map_rotation,
    "listadmins": list_admins,
    "listadminroles": list_admin_roles,
    "listbans": list_bans,
    "listdbbackups": list_db_backups,
    "listcommands": list_commands,
    "listmaprotation": list_map_rotation,
    # "listplayerdecays": list_player_decays,
    "listqueueroles": list_queue_roles,
    "listvoteablemaps": list_voteable_maps,
    "lockqueue": lock_queue,
    "gamehistory": game_history,
    "mockrandomqueue": mock_random_queue,
    "randomnames": random_names,
    "removeadmin": remove_admin,
    "removeadminrole": remove_admin_role,
    "removecommand": remove_command,
    "removedbbackup": remove_db_backup,
    "removequeuerole": remove_queue_role,
    "removequeue": remove_queue,
    "removerotationmap": remove_rotation_map,
    "removevoteablemap": remove_voteable_map,
    "roll": roll,
    "setadddelay": set_add_delay,
    "setcommandprefix": set_command_prefix,
    "setqueuerated": set_queue_rated,
    "setqueueunrated": set_queue_unrated,
    "setmapvotethreshold": set_map_vote_threshold,
    "showgame": show_game,
    "status": status,
    "sub": sub,
    "unban": unban,
    "unlockqueue": unlock_queue,
    "unvoteswapmap": unvote_swap_map,
    "unvoteskipmap": unvote_skip_map,
    "voteswapmap": vote_swap_map,
    "voteskipmap": vote_skip_map,
}


async def handle_message(message: Message):
    print("[handle_message] message:", message)
    command = message.content.split(" ")[0]

    if not command.startswith(COMMAND_PREFIX):
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

    command = command[1:]
    if command not in COMMANDS:
        custom_command: CustomCommand | None = (
            session.query(CustomCommand).filter(CustomCommand.name == command).first()
        )
        if custom_command:
            await send_message(
                message.channel, content=custom_command.output, embed_content=False
            )
            return

        print("[handle_message] exiting - command not found:", command)
        return

    print("[handle_message] executing command:", command)

    args = message.content.split(" ")
    await COMMANDS[command](message, args[1:])
