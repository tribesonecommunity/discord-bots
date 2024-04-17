# The discord bot doesn't like to execute things off the main thread. Instead we
# use queues to be able to execute discord actions from child threads.
# https://stackoverflow.com/a/67996748

import asyncio
import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from random import shuffle

import discord
import sqlalchemy
from discord.channel import TextChannel
from discord.colour import Colour
from discord.ext import tasks
from discord.guild import Guild
from discord.member import Member
from discord.utils import escape_markdown

import discord_bots.config as config
from discord_bots.cogs.schedule import ScheduleUtils
from discord_bots.utils import (
    print_leaderboard,
    send_message,
    update_next_map_to_map_after_next,
    move_game_players_lobby
)

from .bot import bot
from .cogs.economy import EconomyCommands
from .commands import add_player_to_queue, create_game, is_in_game
from .models import (
    InProgressGame,
    InProgressGameChannel,
    MapVote,
    Player,
    PlayerCategoryTrueskill,
    Queue,
    QueuePlayer,
    QueueWaitlist,
    QueueWaitlistPlayer,
    Rotation,
    RotationMap,
    SchedulePlayer,
    ScopedSession,
    Session,
    SkipMapVote,
    VotePassedWaitlist,
    VotePassedWaitlistPlayer,
)
from .queues import AddPlayerQueueMessage, add_player_queue, waitlist_messages

_log = logging.getLogger(__name__)


async def add_players(session: sqlalchemy.orm.Session):
    """
    Handle adding players in a task that pulls messages off of a queue.

    This helps with concurrency issues since players can be added from multiple
    sources (waitlist vs normal add command)
    """
    if add_player_queue.empty():
        # check if the queue is empty up front to avoid emitting any SQL
        return
    queues: list[Queue] = session.query(Queue).order_by(Queue.ordinal.asc()).all()
    queue_by_id: dict[str, Queue] = {queue.id: queue for queue in queues}
    queues_added_to_by_player_id: dict[int, list[Queue]] = {}
    queues_added_to_by_id: dict[str, Queue] = {}
    player_name_by_id: dict[int, str] = {}
    message: AddPlayerQueueMessage | None = None
    embed = discord.Embed()
    while not add_player_queue.empty():
        queues_added_to: list[Queue] = []
        message: AddPlayerQueueMessage = add_player_queue.get()
        player_name_by_id[message.player_id] = message.player_name
        queue_popped = False
        for queue_id in message.queue_ids:
            queue: Queue = queue_by_id[queue_id]
            if queue.is_locked:
                continue

            added_to_queue, queue_popped = await add_player_to_queue(
                queue.id, message.player_id, message.channel, message.guild
            )
            if queue_popped:
                queues_added_to = []
                break
            if added_to_queue:
                queues_added_to.append(queue)
        for queue in queues_added_to:
            queues_added_to_by_id[queue.id] = queue
        if message.player_id not in queues_added_to_by_player_id:
            queues_added_to_by_player_id[message.player_id] = queues_added_to
        else:
            queues_added_to_by_player_id[message.player_id] += queues_added_to

    if not queue_popped:
        queue: Queue
        embed_description = ""
        for queue in queues_added_to_by_id.values():
            if queue.is_locked:
                continue
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

        for player_id in queues_added_to_by_player_id.keys():
            if is_in_game(player_id):
                continue
            player_name = player_name_by_id[player_id]
            queues_added_to = queues_added_to_by_player_id[player_id]
            queue_names = [queue.name for queue in queues_added_to]
            if not queues_added_to:
                embed_description += f"**{player_name}** not added to any queues" + "\n"
            else:
                embed_description += (
                    f"**{player_name}** added to **{', '.join(queue_names)}**" + "\n"
                )
            embed.color = discord.Color.green()

        embed.description = embed_description
        if not queues_added_to_by_id:
            embed.color = discord.Color.yellow()
        embed_fields_len = len(embed.fields)
        if embed_fields_len >= 5 and embed_fields_len % 3 == 2:
            # embeds are allowed 3 "columns" per "row"
            # to line everything up nicely when there's >= 5 fields and only one "column" slot left, we add a blank
            embed.add_field(name="", value="", inline=True)
        await message.channel.send(embed=embed)

    # No messages processed, so no way that sweaty queues popped
    if not message:
        return

    # Handle sweaty queues
    for queue in queues:
        if not queue.is_sweaty:
            continue
        queue_players: list[QueuePlayer] = (
            session.query(QueuePlayer).filter(QueuePlayer.queue_id == queue.id).all()
        )
        if len(queue_players) >= queue.size:
            player_ids: list[int] = list(map(lambda x: x.player_id, queue_players))
            if queue.category_id:
                pcts = session.query(PlayerCategoryTrueskill).filter(
                    PlayerCategoryTrueskill.player_id.in_(player_ids),
                    PlayerCategoryTrueskill.category_id == queue.category_id,
                )
                top_player_ids = [
                    prt.player_id
                    for prt in sorted(
                        pcts,
                        key=lambda prt: prt.mu,
                        reverse=True,
                    )[: queue.size]
                ]
            else:
                players: list[Player] = (
                    session.query(Player).filter(Player.id.in_(player_ids)).all()
                )
                top_player_ids = [
                    player.id
                    for player in sorted(
                        players,
                        key=lambda player: player.rated_trueskill_mu,
                        reverse=True,
                    )[: queue.size]
                ]
            await create_game(
                queue_id=queue.id,
                player_ids=top_player_ids,
                channel_id=message.channel.id,
                guild_id=message.guild.id,
            )


@tasks.loop(seconds=1)
async def add_player_task():
    session: sqlalchemy.orm.Session
    with Session() as session:
        await add_players(session)


@tasks.loop(minutes=1)
async def afk_timer_task():
    session: sqlalchemy.orm.Session
    with Session() as session:
        timeout: datetime = datetime.now(timezone.utc) - timedelta(
            minutes=config.AFK_TIME_MINUTES
        )

        player: Player
        for player in (
            session.query(Player)
            .join(QueuePlayer)
            .filter(
                Player.last_activity_at < timeout, QueuePlayer.player_id == Player.id
            )
        ):
            queue_player = (
                session.query(QueuePlayer)
                .filter(QueuePlayer.player_id == player.id)
                .first()
            )
            if queue_player:
                channel = bot.get_channel(queue_player.channel_id)
                if channel and isinstance(channel, TextChannel):
                    member: Member | None = channel.guild.get_member(player.id)
                    if member:
                        await send_message(
                            channel,
                            content=member.mention,
                            embed_content=False,
                            embed_description=f"{escape_markdown(player.name)} was removed from all queues for being inactive for {config.AFK_TIME_MINUTES} minutes",
                            colour=Colour.red(),
                        )
                session.query(QueuePlayer).filter(
                    QueuePlayer.player_id == player.id
                ).delete()
                session.commit()

        votes_removed_sent = False
        for player in (
            session.query(Player)
            .join(MapVote)
            .filter(Player.last_activity_at < timeout, MapVote.player_id == Player.id)
        ):
            map_votes: list[MapVote] = (
                session.query(MapVote).filter(MapVote.player_id == player.id).all()
            )
            if len(map_votes) > 0:
                channel = bot.get_channel(map_votes[0].channel_id)
                if channel and isinstance(channel, TextChannel):
                    member: Member | None = channel.guild.get_member(player.id)
                    if member:
                        await send_message(
                            channel,
                            content=member.mention,
                            embed_content=False,
                            embed_description=f"{escape_markdown(player.name)}'s votes removed for being inactive for {config.AFK_TIME_MINUTES} minutes",
                            colour=Colour.red(),
                        )
                        votes_removed_sent = True
                session.query(MapVote).filter(MapVote.player_id == player.id).delete()
                session.commit()

        for player in (
            session.query(Player)
            .join(SkipMapVote)
            .filter(
                Player.last_activity_at < timeout, SkipMapVote.player_id == Player.id
            )
        ):
            skip_map_votes: list[SkipMapVote] = (
                session.query(SkipMapVote)
                .filter(SkipMapVote.player_id == player.id)
                .all()
            )
            if len(skip_map_votes) > 0:
                # So we don't send this message twice
                if not votes_removed_sent:
                    channel = bot.get_channel(skip_map_votes[0].channel_id)
                    if channel and isinstance(channel, TextChannel):
                        member: Member | None = channel.guild.get_member(player.id)
                        if member:
                            await send_message(
                                channel,
                                content=member.mention,
                                embed_content=False,
                                embed_description=f"{escape_markdown(player.name)}'s votes removed for being inactive for {config.AFK_TIME_MINUTES} minutes",
                                colour=Colour.red(),
                            )
                session.query(SkipMapVote).filter(
                    SkipMapVote.player_id == player.id
                ).delete()
                session.commit()


@tasks.loop(seconds=1800)
async def leaderboard_task():
    """
    Periodically print the leaderboard
    """
    await print_leaderboard()


@tasks.loop(minutes=1)
async def map_rotation_task():
    """Rotate the map automatically, stopping on the 1st map
    TODO: tests
    """
    if config.DISABLE_MAP_ROTATION:
        return

    session: sqlalchemy.orm.Session
    with Session() as session:
        rotations: list[Rotation] | None = session.query(Rotation).all()
        if not rotations:
            return

        for rotation in rotations:
            next_rotation_map: RotationMap | None = (
                session.query(RotationMap)
                .filter(RotationMap.rotation_id == rotation.id)
                .filter(RotationMap.is_next == True)
                .first()
            )
            if next_rotation_map and next_rotation_map.ordinal != 1:
                time_since_update: timedelta = datetime.now(
                    timezone.utc
                ) - next_rotation_map.updated_at.replace(tzinfo=timezone.utc)
                if (time_since_update.seconds // 60) > config.MAP_ROTATION_MINUTES:
                    await update_next_map_to_map_after_next(rotation.id, True)


@tasks.loop(seconds=5)
async def prediction_task():
    """
    Updates prediction embeds.
    Closes prediction after submission period
    """
    session = Session()
    in_progress_games: list[InProgressGame] | None = (
        session.query(InProgressGame)
        .filter(InProgressGame.prediction_open == True)
        .all()
    )
    try:
        await EconomyCommands.update_embeds(None, in_progress_games)
    except Exception as e:
        # Task fails if attemping to update an embed that hasn't been posted yet
        # Occurs during game channel creation depending on when task runs.
        # Cleanly cancel & restart task to resolve
        _log.warning(e)
        _log.info("prediction_task restarting...")
        session.close()
        prediction_task.cancel()
        prediction_task.restart()

    await EconomyCommands.close_predictions(None, in_progress_games=in_progress_games)
    session.close()


@tasks.loop(seconds=1)
async def queue_waitlist_task():
    """
    Move players in the waitlist into the queues. Pop queues if needed.

    This exists as a task so that it happens on the main thread. Sqlite doesn't
    like to do writes on a second thread.

    TODO: Tests for this method
    """
    session: sqlalchemy.orm.Session
    with Session() as session:
        queues: list[Queue] = session.query(Queue).order_by(Queue.ordinal.asc())  # type: ignore
        queue_waitlist: QueueWaitlist
        channel: (
            discord.abc.GuildChannel
            | discord.Thread
            | discord.abc.PrivateChannel
            | None
        ) = None
        guild: Guild | None = None
        for queue_waitlist in session.query(QueueWaitlist).filter(
            QueueWaitlist.end_waitlist_at < datetime.now(timezone.utc)
        ):
            if not channel:
                channel = bot.get_channel(queue_waitlist.channel_id)
                if isinstance(channel, TextChannel) and waitlist_messages:
                    # TODO: delete_messages can only delete a max of 100 messages
                    # so add logic to chunk waitlist_messages
                    try:
                        await channel.delete_messages(waitlist_messages)
                    except:
                        _log.exception(
                            f"[queue_waitlist_task] Ignoring exception in delete_messages"
                        )
                    finally:
                        waitlist_messages.clear()
            if not guild:
                guild = bot.get_guild(queue_waitlist.guild_id)

            queue_waitlist_players: list[QueueWaitlistPlayer]
            queue_waitlist_players = (
                session.query(QueueWaitlistPlayer)
                .filter(QueueWaitlistPlayer.queue_waitlist_id == queue_waitlist.id)
                .all()
            )
            qwp_by_queue_id: dict[str, list[QueueWaitlistPlayer]] = defaultdict(list)
            for qwp in queue_waitlist_players:
                if qwp.queue_id:
                    qwp_by_queue_id[qwp.queue_id].append(qwp)

            # Ensure that we process the queues in the order the queues were
            # created. TODO: Make the last queue that popped the lowest priority
            for queue in queues:
                qwps_for_queue = qwp_by_queue_id[queue.id]
                shuffle(qwps_for_queue)
                for queue_waitlist_player in qwps_for_queue:
                    if is_in_game(queue_waitlist_player.player_id):
                        session.delete(queue_waitlist_player)
                        continue

                    if isinstance(channel, TextChannel) and guild:
                        player = (
                            session.query(Player)
                            .filter(Player.id == queue_waitlist_player.player_id)
                            .first()
                        )

                        add_player_queue.put(
                            AddPlayerQueueMessage(
                                queue_waitlist_player.player_id,
                                player.name,
                                # TODO: This is sucky to do it one at a time
                                [queue.id],
                                True,
                                channel,
                                guild,
                            )
                        )
            ipg_channels: list[InProgressGameChannel] = (
                session.query(InProgressGameChannel)
                .filter(
                    InProgressGameChannel.in_progress_game_id
                    == queue_waitlist.in_progress_game_id
                )
                .all()
            )
            if guild:
                ipg_discord_channels: list[discord.abc.GuildChannel] = [
                    channel
                    for ipg_channel in ipg_channels
                    if (channel := guild.get_channel(ipg_channel.channel_id))
                    is not None
                ]
                channel_delete_coroutines = [
                    channel.delete() for channel in ipg_discord_channels
                ]
                try:
                    if config.ENABLE_VOICE_MOVE and config.VOICE_MOVE_LOBBY:
                        await move_game_players_lobby(queue_waitlist.in_progress_game_id, guild)
                    await asyncio.gather(*channel_delete_coroutines)
                except:
                    _log.exception(
                        f"[queue_waitlist_task] Failed to delete in_progress_game channels {ipg_discord_channels} from guild {guild.id}"
                    )
            # TODO: deleting channels from the guild and from the DB isn't atomic
            session.query(InProgressGameChannel).filter(
                InProgressGameChannel.in_progress_game_id
                == queue_waitlist.in_progress_game_id
            ).delete()
            session.query(QueueWaitlistPlayer).filter(
                QueueWaitlistPlayer.queue_waitlist_id == queue_waitlist.id
            ).delete()
            session.delete(queue_waitlist)
            session.query(InProgressGame).filter(
                InProgressGame.id == queue_waitlist.in_progress_game_id
            ).delete()
        session.commit()


@tasks.loop(hours=24)
async def schedule_task():
    """
    An hour after schedules end, roll over to the next day.
    Clear yesterday's schedule players, add 1 week.

    """
    with ScopedSession() as session:
        # cycle message ids for n = 1 through 6
        previous_message_id = ScheduleUtils.get_schedules_for_nth_embed(0)[
            0
        ].message_id  # store first message_id here initially
        current_message_id: int
        for n in range(1, 7):
            schedules = ScheduleUtils.get_schedules_for_nth_embed(n)
            current_message_id = schedules[0].message_id
            for schedule in schedules:
                schedule.message_id = previous_message_id
            previous_message_id = current_message_id

        # handle n = 0 (today)
        today_schedules = ScheduleUtils.get_schedules_for_nth_embed(0)
        for schedule in today_schedules:
            schedule.datetime = schedule.datetime + timedelta(days=7)
            session.query(SchedulePlayer).filter(
                SchedulePlayer.schedule_id == schedule.id
            ).delete()
            schedule.message_id = previous_message_id

        session.commit()

    # assumes we are only running this bot/database on one guild
    for n in range(7):
        await ScheduleUtils.rebuild_embed(bot.guilds[0], n)


@schedule_task.before_loop
async def delay_schedule_task():
    """
    Delay start of schedule task until an hour after today's last schedule
    """
    await bot.wait_until_ready()

    last_schedule_today = ScheduleUtils.get_schedules_for_nth_embed(0)[-1]
    ScopedSession.remove()

    # can't get timedelta if only one operand has tzinfo, so we convert datetime.now to utc and remove tzinfo
    time_until_target = (
        last_schedule_today.datetime
        - datetime.now(timezone.utc).replace(tzinfo=None)
        + timedelta(hours=1)
    )
    seconds_until_target = time_until_target.total_seconds()
    if (
        seconds_until_target <= 0
    ):  # go ahead and rotate to the next day if today's schedules have already passed
        await schedule_task()
        seconds_until_target += 86400  # add 24 hours
    await asyncio.sleep(seconds_until_target)


@tasks.loop(seconds=1)
async def vote_passed_waitlist_task():
    """
    Move players in the waitlist into the queues. Pop queues if needed.

    This exists as a task so that it happens on the main thread. Sqlite doesn't
    like to do writes on a second thread.

    TODO: Tests for this method
    """
    session: sqlalchemy.orm.Session
    with Session() as session:
        vpw: VotePassedWaitlist | None = (
            session.query(VotePassedWaitlist)
            .filter(VotePassedWaitlist.end_waitlist_at < datetime.now(timezone.utc))
            .first()
        )
        if not vpw:
            return

        channel = bot.get_channel(vpw.channel_id)
        guild: Guild | None = bot.get_guild(vpw.guild_id)
        queues: list[Queue] = session.query(Queue).order_by(Queue.created_at.asc())  # type: ignore

        # TODO: Do we actually need to filter by id?
        vote_passed_waitlist_players: list[VotePassedWaitlistPlayer] = (
            session.query(VotePassedWaitlistPlayer)
            .filter(VotePassedWaitlistPlayer.vote_passed_waitlist_id == vpw.id)
            .all()
        )
        vpwp_by_queue_id: dict[str, list[VotePassedWaitlistPlayer]] = defaultdict(list)
        for vote_passed_waitlist_player in vote_passed_waitlist_players:
            vpwp_by_queue_id[vote_passed_waitlist_player.queue_id].append(
                vote_passed_waitlist_player
            )

        # Ensure that we process the queues in the order the queues were created
        for queue in queues:
            vpwps_for_queue = vpwp_by_queue_id[queue.id]
            shuffle(vpwps_for_queue)
            for vote_passed_waitlist_player in vpwps_for_queue:
                if is_in_game(vote_passed_waitlist_player.player_id):
                    session.delete(vote_passed_waitlist_player)
                    continue

                if isinstance(channel, TextChannel) and guild:
                    player = (
                        session.query(Player)
                        .filter(Player.id == vote_passed_waitlist_player.player_id)
                        .first()
                    )

                    add_player_queue.put(
                        AddPlayerQueueMessage(
                            vote_passed_waitlist_player.player_id,
                            player.name,
                            # TODO: This is sucky to do it one at a time
                            [queue.id],
                            False,
                            channel,
                            guild,
                        )
                    )

        session.query(VotePassedWaitlistPlayer).filter(
            VotePassedWaitlistPlayer.vote_passed_waitlist_id == vpw.id
        ).delete()
        session.delete(vpw)
        session.commit()


@tasks.loop(time=config.TRUESKILL_SIGMA_DECAY_JOB_SCHEDULED_TIME)
async def apply_sigma_decay():
    if not config.ENABLE_TRUESKILL_SIGMA_DECAY:
        return
    sigma_decay_cutoff = datetime.now(timezone.utc) - timedelta(
        days=config.TRUESKILL_SIGMA_DECAY_GRACE_DAYS
    )
    session: sqlalchemy.orm.Session
    with Session() as session:
        # Find all player trueskill values with a last played game older than the decay grace period
        player_category_trueskills = (
            session.query(PlayerCategoryTrueskill)
            .filter(
                sqlalchemy.and_(
                    PlayerCategoryTrueskill.last_game_finished_at.is_not(None),
                    PlayerCategoryTrueskill.last_game_finished_at < sigma_decay_cutoff,
                )
            )
            .all()
        )
        for pct in player_category_trueskills:
            pct.sigma = min(
                pct.sigma + config.TRUESKILL_SIGMA_DECAY_DELTA,
                config.DEFAULT_TRUESKILL_SIGMA,
            )
        session.commit()
