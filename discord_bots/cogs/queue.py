import logging
from datetime import datetime, timedelta, timezone

import numpy
from discord import (
    app_commands,
    Colour,
    Embed,
    Interaction,
    Role,
    TextChannel,
)
from discord.ext.commands import Bot, Context, check, command
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import NoResultFound
from sqlalchemy.orm.session import Session as SQLAlchemySession

from discord_bots.checks import is_admin_app_command
from discord_bots.cogs.base import BaseCog
from discord_bots.config import (
    CURRENCY_AWARD,
    CURRENCY_NAME,
    ECONOMY_ENABLED,
    ENABLE_VOICE_MOVE,
)
from discord_bots.models import (
    Category,
    FinishedGame,
    FinishedGamePlayer,
    InProgressGame,
    Map,
    Player,
    Queue,
    QueuePlayer,
    QueueRole,
    Rotation,
    RotationMap,
    Session
)
from discord_bots.queues import AddPlayerQueueMessage, add_player_queue

_log = logging.getLogger(__name__)


class QueueCommands(BaseCog):
    def __init__(self, bot: Bot):
        super().__init__(bot)

    group = app_commands.Group(name="queue", description="Queue commands")
    
    @group.command(name="addrole", description="Associate a discord role with a queue")
    @app_commands.check(is_admin_app_command)
    @app_commands.describe(queue_name="Name of queue", role="Discord role")
    async def addqueuerole(self, interaction: Interaction, queue_name: str, role: Role):
        """
        Associate a discord role with a queue
        """
        if interaction.guild:
            if not role in interaction.guild.roles:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Could not find role: {role.name}",
                        colour=Colour.blue(),
                    ),
                    ephemeral=True,
                )
                return

        session: SQLAlchemySession
        with Session() as session:
            queue = session.query(Queue).filter(Queue.name.ilike(queue_name)).first()  # type: ignore
            if not queue:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Could not find queue: {queue_name}",
                        colour=Colour.red(),
                    ),
                    ephemeral=True
                )
                return
            else:
                session.add(QueueRole(queue.id, role.id))
                session.commit()
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Added role {role.name} to queue {queue.name}",
                        colour=Colour.green()
                    )
                )

    @group.command(name="clear", description="Clear all players out of a queue")
    @app_commands.check(is_admin_app_command)
    @app_commands.describe(queue_name="Name of queue")
    async def clearqueue(self, interaction: Interaction, queue_name: str):
        """
        Clear all players out of a queue
        """
        session: SQLAlchemySession
        with Session() as session:
            queue = session.query(Queue).filter(Queue.name.ilike(queue_name)).first()  # type: ignore

            if not queue:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Could not find queue: {queue_name}",
                        colour=Colour.red(),
                    ),
                    ephemeral=True
                )
                return
            
            session.query(QueuePlayer).filter(QueuePlayer.queue_id == queue.id).delete()
            session.commit()
            await interaction.response.send_message(
                embed=Embed(
                    description=f"Queue cleared: {queue.name}",
                    colour=Colour.green(),
                )
            )

    @group.command(name="clearrange", description="Clear the mu range for a queue")
    @app_commands.check(is_admin_app_command)
    @app_commands.describe(queue_name="Name of queue")
    async def clearqueuerange(self, interaction: Interaction, queue_name: str):
        """
        Clear the mu range for a queue
        """
        session: SQLAlchemySession
        with Session() as session:
            queue = session.query(Queue).filter(Queue.name.ilike(queue_name)).first()  # type: ignore
            if queue:
                queue.mu_min = None
                queue.mu_max = None
                session.commit()
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Queue {queue.name} range cleared",
                        colour=Colour.green()
                    )
                )
            else:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Queue not found: {queue_name}",
                        colour=Colour.red(),
                    ),
                    ephemeral=True
                )

    @group.command(name="create", description="Create a queue")
    @app_commands.check(is_admin_app_command)
    @app_commands.describe(queue_name="Name of queue", size="Size of queue")
    async def createqueue(self, interaction: Interaction, queue_name: str, size: int):
        """
        Create a queue
        """
        vote_threshold: int = round(float(size) * 2 / 3)
        queue = Queue(name=queue_name, size=size, vote_threshold=vote_threshold)
        
        session: SQLAlchemySession
        with Session() as session:
            try:
                session.add(queue)
                session.commit()
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Queue created: {queue.name}",
                        colour=Colour.green()
                    )
                )
            except IntegrityError:
                session.rollback()
                await interaction.response.send_message(
                    embed=Embed(
                        description="A queue already exists with that name",
                        colour=Colour.red(),
                    ),
                    ephemeral=True
                )

    @group.command(name="isolate", description="Isolate a queue (no auto-adds)")
    @app_commands.check(is_admin_app_command)
    @app_commands.describe(queue_name="Name of queue")
    async def isolatequeue(self, interaction: Interaction, queue_name: str):
        """
        Isolate a queue (no auto-adds)
        """
        session: SQLAlchemySession
        with Session() as session:
            queue: Queue = session.query(Queue).filter(Queue.name.ilike(queue_name)).first()  # type: ignore
            if queue:
                queue.is_isolated = True
                session.commit()
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Queue {queue.name} is now isolated (no auto-adds)",
                        colour=Colour.green(),
                    )
                )
            else:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Queue not found: {queue_name}",
                        colour=Colour.red(),
                    ),
                    ephemeral=True
                )

    @group.command(name="list", description="List all queues with their category and rotation")
    async def listqueues(self, interaction: Interaction):
        """
        List all queues with their category and rotation
        """
        session: SQLAlchemySession
        with Session() as session:
            queues: list[Queue] | None = session.query(Queue).all()
            if not queues:
                await interaction.response.send_message(
                    embed=Embed(
                        description="No queues found",
                        colour=Colour.red(),
                    ),
                    ephemeral=True
                )
                return

            output = ""
            for queue in queues:
                if queue.is_locked:
                    output += f"### {queue.name} [locked]\n"
                else:
                    output += f"### {queue.name}\n"

                output += "- Category: "
                category_name: str | None = (
                    session.query(Category.name)
                    .filter(Category.id == queue.category_id)
                    .scalar()
                )
                if category_name:
                    output += f"{category_name}\n"
                else:
                    output += "None\n"

                output += "- Rotation: "
                rotation_name: str | None = (
                    session.query(Rotation.name)
                    .filter(Rotation.id == queue.rotation_id)
                    .scalar()
                )
                if rotation_name:
                    output += f"{rotation_name}\n"
                else:
                    output += "None\n"

            await interaction.response.send_message(
                embed=Embed(
                    description=output,
                    colour=Colour.blue(),
                )
            )

    @group.command(name="listroles", description="List all queues and their associated discord roles")
    async def listqueueroles(self, interaction: Interaction):
        """
        List all queues and their associated discord roles
        """
        if not interaction.guild:
            return

        output = "Queues:\n"
        session: SQLAlchemySession
        with Session() as session:
            queue: Queue
            for i, queue in enumerate(session.query(Queue).all()):
                queue_role_names: list[str] = []
                queue_role: QueueRole
                for queue_role in (
                    session.query(QueueRole).filter(QueueRole.queue_id == queue.id).all()
                ):
                    role: Role = interaction.guild.get_role(queue_role.role_id)
                    if role:
                        queue_role_names.append(role.name)
                    else:
                        queue_role_names.append(str(queue_role.role_id))
                output += f"**{queue.name}**: {', '.join(queue_role_names)}\n"
            await self.send_info_message(output)

    @command()
    @check(is_admin)
    async def lockqueue(self, ctx: Context, queue_name: str):
        """
        Prevent players from adding to a queue
        """
        session = ctx.session
        queue: Queue | None = (
            session.query(Queue).filter(Queue.name.ilike(queue_name)).first()
        )
        if not queue:
            await self.send_error_message(f"Could not find queue: {queue_name}")
            return

        queue.is_locked = True
        session.commit()
        await self.send_success_message(f"Queue **{queue.name}** locked")

    @command()
    @check(is_admin)
    async def mockqueue(self, ctx: Context, queue_name: str, count: int):
        message = ctx.message
        """
        Helper test method for adding random players to queues

        This will send PMs to players, create voice channels, etc. so be careful
        """
        if message.author.id not in [
            115204465589616646,
            347125254050676738,
            508003755220926464,
            133700743201816577,
        ]:
            await self.send_error_message("Only special people can use this command")
            return

        session = ctx.session
        players_from_last_30_days = (
            session.query(Player)
            .join(FinishedGamePlayer, FinishedGamePlayer.player_id == Player.id)
            .join(FinishedGame, FinishedGame.id == FinishedGamePlayer.finished_game_id)
            .filter(
                FinishedGame.finished_at
                > datetime.now(timezone.utc) - timedelta(days=30),
            )
            .order_by(FinishedGame.finished_at.desc())  # type: ignore
            .all()
        )
        queue: Queue | None = session.query(Queue).filter(Queue.name.ilike(queue_name)).first()  # type: ignore
        # This throws an error if people haven't played in 30 days
        for player in numpy.random.choice(
            players_from_last_30_days, size=int(count), replace=False
        ):
            if isinstance(message.channel, TextChannel) and message.guild:
                add_player_queue.put(
                    AddPlayerQueueMessage(
                        player.id,
                        player.name,
                        [queue.id],
                        False,
                        message.channel,
                        message.guild,
                    )
                )
                player.last_activity_at = datetime.now(timezone.utc)
                session.add(player)
                session.commit()

        await self.send_success_message(
            f"Added **{count}** players to **{queue.name}**"
        )

    @command()
    @check(is_admin)
    async def removequeue(self, ctx: Context, queue_name: str):
        """
        Remove a queue
        """
        session = ctx.session

        queue = session.query(Queue).filter(Queue.name.ilike(queue_name)).first()  # type: ignore
        if queue:
            games_in_progress = (
                session.query(InProgressGame)
                .filter(InProgressGame.queue_id == queue.id)
                .all()
            )
            if len(games_in_progress) > 0:
                await self.send_error_message(
                    f"Cannot remove queue with game in progress: {queue.name}"
                )
                return
            else:
                session.delete(queue)
                session.commit()
                await self.send_success_message(f"Queue removed: {queue.name}")
        else:
            await self.send_error_message(f"Queue not found: {queue_name}")

    @command()
    @check(is_admin)
    async def removequeuerole(self, ctx: Context, queue_name: str, role_name: str):
        """
        Remove a discord role from a queue
        """
        message = ctx.message
        session = ctx.session
        queue: Queue | None = session.query(Queue).filter(Queue.name.ilike(queue_name)).first()  # type: ignore
        if not queue:
            await self.send_error_message(f"Could not find queue: {queue_name}")
            return
        if message.guild:
            role_name_to_role_id: dict[str, int] = {
                role.name.lower(): role.id for role in message.guild.roles
            }
            if role_name.lower() not in role_name_to_role_id:
                # In case a queue role was deleted from the server
                queue_role_by_role_id = session.query(QueueRole).filter(
                    QueueRole.queue_id == queue.id,
                    QueueRole.role_id == role_name,
                )
                if queue_role_by_role_id:
                    session.query(QueueRole).filter(
                        QueueRole.queue_id == queue.id,
                        QueueRole.role_id == role_name,
                    ).delete()
                    session.commit()
                    await self.send_success_message(
                        f"Removed role {role_name} from queue {queue.name}"
                    )
                    return

                await self.send_error_message(f"Could not find role: {role_name}")
                return
            session.query(QueueRole).filter(
                QueueRole.queue_id == queue.id,
                QueueRole.role_id == role_name_to_role_id[role_name.lower()],
            ).delete()
            await self.send_success_message(
                f"Removed role {role_name} from queue {queue.name}"
            )
            session.commit()

    @command()
    @check(is_admin)
    async def setqueuecurrencyaward(
        self, ctx: Context, queue_name: str, currency_award: int = CURRENCY_AWARD
    ):
        """
        Set how much currency is awarded for games in queue.\nSet to default if no value provided.
        """
        session: SQLAlchemySession = ctx.session

        if not ECONOMY_ENABLED:
            await self.send_error_message("Player economy is disabled")
            return

        try:
            queue: Queue = (
                session.query(Queue).filter(Queue.name.ilike(queue_name)).one()
            )
        except NoResultFound:
            await self.send_error_message(f"Could not find queue **{queue_name}**")
            return

        queue.currency_award = currency_award
        session.commit()
        await self.send_success_message(
            f"**{queue_name}** award set to {currency_award} {CURRENCY_NAME}"
        )

    @command()
    @check(is_admin)
    async def setqueuemoveenabled(
        self, ctx: Context, queue_name: str, enabled_option: bool
    ):
        """
        Enables automatic moving of people in game when queue pops
        """
        session: SQLAlchemySession = ctx.session

        if not ENABLE_VOICE_MOVE:
            await self.send_error_message("Voice movement is disabled")
            return

        try:
            queue = session.query(Queue).filter(Queue.name.ilike(queue_name)).one()
        except NoResultFound:
            await self.send_error_message(f"Could not find queue **{queue_name}**")
            return

        queue.move_enabled = enabled_option
        session.commit()
        if enabled_option:
            await self.send_success_message(
                f"Player moving enabled on queue **{queue_name}**"
            )
        else:
            await self.send_success_message(
                f"Player moving disabled on queue **{queue_name}**"
            )

    @command()
    @check(is_admin)
    async def setqueuename(
        self, ctx: Context, old_queue_name: str, new_queue_name: str
    ):
        """
        Set queue name
        """
        await self.setname(ctx, Queue, old_queue_name, new_queue_name)

    @command()
    @check(is_admin)
    async def setqueueordinal(self, ctx: Context, queue_name: str, ordinal: int):
        """
        Set queue ordinal
        """
        session = ctx.session
        queue: Queue = session.query(Queue).filter(Queue.name.ilike(queue_name)).first()  # type: ignore
        if queue:
            queue.ordinal = ordinal
            session.commit()
            await self.send_success_message(
                f"Queue {queue.name} ordinal set to {ordinal}"
            )
        else:
            await self.send_error_message(f"Queue not found: {queue_name}")

    @command()
    @check(is_admin)
    async def setqueuerange(
        self, ctx: Context, queue_name: str, min: float, max: float
    ):
        """
        Set the mu range for a queue
        """
        session = ctx.session
        queue: Queue = session.query(Queue).filter(Queue.name.ilike(queue_name)).first()  # type: ignore
        if queue:
            queue.mu_min = min
            queue.mu_max = max
            session.commit()
            await self.send_success_message(
                f"Queue {queue.name} range set to [{min}, {max}]"
            )
        else:
            await self.send_error_message(f"Queue not found: {queue_name}")

    @command()
    @check(is_admin)
    async def setqueuerotation(self, ctx: Context, queue_name: str, rotation_name: str):
        """
        Assign a map rotation to a queue
        """
        session = ctx.session

        try:
            queue = session.query(Queue).filter(Queue.name.ilike(queue_name)).one()
        except NoResultFound:
            await self.send_error_message(f"Could not find queue **{queue_name}**")
            return

        try:
            rotation = (
                session.query(Rotation).filter(Rotation.name.ilike(rotation_name)).one()
            )
        except NoResultFound:
            await self.send_error_message(
                f"Could not find rotation **{rotation_name}**"
            )
            return

        queue.rotation_id = rotation.id
        session.commit()
        await self.send_success_message(
            f"Rotation for **{queue.name}** set to **{rotation.name}**"
        )

    @command()
    @check(is_admin)
    async def setqueuesize(self, ctx: Context, queue_name: str, queue_size: int):
        """
        Set the number of players to pop a queue.  Also updates queue vote threshold.
        """
        session = ctx.session

        queue: Queue | None = (
            session.query(Queue).filter(Queue.name.ilike(queue_name)).first()
        )
        if not queue:
            await self.send_error_message(f"Could not find queue **{queue_name}**")
            return

        queue.size = queue_size
        queue.vote_threshold = round(float(queue_size) * 2 / 3)
        session.commit()
        await self.send_success_message(f"Queue size updated to **{queue.size}**")

    @command()
    @check(is_admin)
    async def setqueuesweaty(self, ctx: Context, queue_name: str):
        """
        Make a queue sweaty
        """
        session = ctx.session
        queue: Queue = session.query(Queue).filter(Queue.name.ilike(queue_name)).first()  # type: ignore
        if queue:
            queue.is_sweaty = True
            session.commit()
            await self.send_success_message(f"Queue {queue.name} is now sweaty")
        else:
            await self.send_error_message(f"Queue not found: {queue_name}")

    @command()
    @check(is_admin)
    async def setqueuevotethreshold(
        self, ctx: Context, queue_name: str, vote_threshold: int
    ):
        """
        Set the vote threshold for a queue
        """
        session = ctx.session
        queue: Queue | None = (
            session.query(Queue).filter(Queue.name.ilike(queue_name)).first()
        )
        if not queue:
            await self.send_error_message(f"Could not find queue: {queue_name}")
            return

        queue.vote_threshold = vote_threshold
        session.commit()
        await self.send_success_message(
            f"Vote threshold for **{queue.name}** set to **{queue.vote_threshold}**"
        )

    @command()
    async def showqueuerange(self, ctx: Context, queue_name: str):
        """
        Show the mu range for a queue
        """
        session = ctx.session
        queue = session.query(Queue).filter(Queue.name.ilike(queue_name)).first()  # type: ignore
        if not queue:
            await self.send_error_message(f"Could not find queue: {queue_name}")
            return
        await self.send_info_message(f"Queue range: [{queue.mu_min}, {queue.mu_max}]")

    @command()
    async def showqueuerotation(self, ctx: Context, queue_name: str):
        """
        Show the map rotation assigned to a queue
        """
        session = ctx.session

        queue: Queue | None = (
            session.query(Queue).filter(Queue.name.ilike(queue_name)).first()
        )
        if not queue:
            await self.send_error_message(f"Could not find queue **{queue_name}**")
            return

        rotation: Rotation | None = (
            session.query(Rotation).filter(Rotation.id == queue.rotation_id).first()
        )
        if not rotation:
            await self.send_error_message(
                f"Queue **{queue.name}** has not been assigned a rotation"
            )
            return

        map_names = [
            x[0]
            for x in (
                session.query(Map.short_name)
                .join(RotationMap, RotationMap.map_id == Map.id)
                .filter(RotationMap.rotation_id == rotation.id)
                .order_by(RotationMap.ordinal.asc())
                .all()
            )
        ]

        if not map_names:
            map_names = ["None"]

        output = f"**{queue.name}** is assigned to **{rotation.name}**\n"
        output += f"- _Maps: {', '.join(map_names)}_"
        await self.send_info_message(output)

    @command()
    @check(is_admin)
    async def unisolatequeue(self, ctx: Context, queue_name: str):
        """
        Unisolate a queue (rated, map rotation, auto-adds)
        """
        session = ctx.session
        queue: Queue = session.query(Queue).filter(Queue.name.ilike(queue_name)).first()  # type: ignore
        if queue:
            queue.is_isolated = False
            session.commit()
            await self.send_success_message(f"Queue {queue.name} is now unisolated")
        else:
            await self.send_error_message(f"Queue not found: {queue_name}")

    @command()
    @check(is_admin)
    async def unlockqueue(self, ctx: Context, queue_name: str):
        """
        Allow players to add to a queue
        """
        session = ctx.session
        queue: Queue | None = (
            session.query(Queue).filter(Queue.name.ilike(queue_name)).first()
        )
        if not queue:
            await self.send_error_message(f"Could not find queue: {queue_name}")
            return

        queue.is_locked = False
        session.commit()
        await self.send_success_message(f"Queue **{queue.name}** unlocked")

    @command()
    @check(is_admin)
    async def unsetqueuesweaty(self, ctx: Context, queue_name: str):
        """
        Make a queue not sweaty
        """
        session = ctx.session
        queue: Queue = session.query(Queue).filter(Queue.name.ilike(queue_name)).first()  # type: ignore
        if queue:
            queue.is_sweaty = False
            session.commit()
            await self.send_success_message(f"Queue {queue.name} is no longer sweaty")
        else:
            await self.send_error_message(f"Queue not found: {queue_name}")

    @addqueuerole.autocomplete("queue_name")
    @clearqueue.autocomplete("queue_name")
    @clearqueuerange.autocomplete("queue_name")
    @isolatequeue.autocomplete("queue_name")
    async def queue_autocomplete(self, interaction: Interaction, current: str):
        result = []
        session: SQLAlchemySession
        with Session() as session:
            queues: list[Queue] | None = session.query(Queue).limit(25).all()
            if queues:
                for queue in queues:
                    if current in queue.name:
                        result.append(
                            app_commands.Choice(name=queue.name, value=queue.name)
                        )
        return result