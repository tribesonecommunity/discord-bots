import logging
import numpy
from datetime import datetime, timedelta, timezone
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import NoResultFound
from sqlalchemy.orm.session import Session as SQLAlchemySession

from discord import (
    app_commands,
    Colour,
    Embed,
    Interaction,
    Role,
    TextChannel,
)
from discord.ext.commands import Bot

from discord_bots.checks import is_admin_app_command, is_command_channel, is_mock_user_app_command
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
    Session,
)
from discord_bots.queues import AddPlayerQueueMessage, add_player_queue

_log = logging.getLogger(__name__)


class QueueCommands(BaseCog):
    def __init__(self, bot: Bot):
        super().__init__(bot)

    group = app_commands.Group(name="queue", description="Queue commands")

    @group.command(name="addrole", description="Associate a discord role with a queue")
    @app_commands.check(is_admin_app_command)
    @app_commands.check(is_command_channel)
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
                    ephemeral=True,
                )
                return
            else:
                session.add(QueueRole(queue.id, role.id))
                session.commit()
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Added role {role.name} to queue {queue.name}",
                        colour=Colour.green(),
                    )
                )

    @group.command(name="clearcategory", description="Remove category from queue")
    @app_commands.check(is_admin_app_command)
    @app_commands.check(is_command_channel)
    @app_commands.describe(queue_name="Name of existing queue")
    async def clearqueuecategory(self, interaction: Interaction, queue_name: str):
        session: SQLAlchemySession
        with Session() as session:
            try:
                queue: Queue = (
                    session.query(Queue).filter(Queue.name.ilike(queue_name)).one()
                )
            except NoResultFound:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Could not find queue **{queue_name}**",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )
            else:
                queue.category_id = None
                session.commit()
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Queue **{queue.name}** category cleared",
                        colour=Colour.green(),
                    ),
                    ephemeral=True,
                )

    @group.command(name="clear", description="Clear all players out of a queue")
    @app_commands.check(is_admin_app_command)
    @app_commands.check(is_command_channel)
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
                    ephemeral=True,
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
    @app_commands.check(is_command_channel)
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
                        colour=Colour.green(),
                    )
                )
            else:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Queue not found: {queue_name}",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )

    @group.command(name="create", description="Create a queue")
    @app_commands.check(is_admin_app_command)
    @app_commands.check(is_command_channel)
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
                        colour=Colour.green(),
                    )
                )
            except IntegrityError:
                session.rollback()
                await interaction.response.send_message(
                    embed=Embed(
                        description="A queue already exists with that name",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )

    @group.command(name="isolate", description="Isolate a queue (no auto-adds)")
    @app_commands.check(is_admin_app_command)
    @app_commands.check(is_command_channel)
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
                    ephemeral=True,
                )

    @group.command(name="lock", description="Prevent players from adding to a queue")
    @app_commands.check(is_admin_app_command)
    @app_commands.check(is_command_channel)
    @app_commands.describe(queue_name="Name of queue")
    async def lockqueue(self, interaction: Interaction, queue_name: str):
        """
        Prevent players from adding to a queue
        """
        session: SQLAlchemySession
        with Session() as session:
            queue: Queue | None = (
                session.query(Queue).filter(Queue.name.ilike(queue_name)).first()
            )
            if not queue:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Could not find queue: {queue_name}",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )
                return

            queue.is_locked = True
            session.commit()
            await interaction.response.send_message(
                embed=Embed(
                    description=f"Queue **{queue.name}** locked",
                    colour=Colour.green(),
                )
            )

    @group.command(
        name="mock",
        description="Helper test method for adding random players to queues",
    )
    @app_commands.check(is_mock_user_app_command)
    @app_commands.check(is_command_channel)
    @app_commands.describe(queue_name="Name of queue", count="Number of people to add")
    async def mockqueue(self, interaction: Interaction, queue_name: str, count: int):
        """
        Helper test method for adding random players to queues

        This will send PMs to players, create voice channels, etc. so be careful
        """
        session: SQLAlchemySession
        with Session() as session:
            players_from_last_30_days = (
                session.query(Player)
                .join(FinishedGamePlayer, FinishedGamePlayer.player_id == Player.id)
                .join(
                    FinishedGame, FinishedGame.id == FinishedGamePlayer.finished_game_id
                )
                .filter(
                    FinishedGame.finished_at
                    > datetime.now(timezone.utc) - timedelta(days=30),
                )
                .order_by(FinishedGame.finished_at.desc())  # type: ignore
                .all()
            )
            queue: Queue | None = session.query(Queue).filter(Queue.name.ilike(queue_name)).first()  # type: ignore
            if not queue:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Queue {queue_name} not found",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )
                return
            # This throws an error if people haven't played in 30 days
            for player in numpy.random.choice(
                players_from_last_30_days, size=int(count), replace=False
            ):
                if isinstance(interaction.channel, TextChannel) and interaction.guild:
                    add_player_queue.put(
                        AddPlayerQueueMessage(
                            player.id,
                            player.name,
                            [queue.id],
                            False,
                            interaction.channel,
                            interaction.guild,
                        )
                    )
                    player.last_activity_at = datetime.now(timezone.utc)
                    session.add(player)
                    session.commit()

            await interaction.response.send_message(
                embed=Embed(
                    description=f"Added **{count}** players to **{queue.name}**",
                    colour=Colour.green(),
                )
            )

    @group.command(name="remove", description="Remove a queue")
    @app_commands.check(is_admin_app_command)
    @app_commands.check(is_command_channel)
    @app_commands.describe(queue_name="Name of queue")
    async def removequeue(self, interaction: Interaction, queue_name: str):
        """
        Remove a queue
        """
        session: SQLAlchemySession
        with Session() as session:
            queue: Queue = session.query(Queue).filter(Queue.name.ilike(queue_name)).first()  # type: ignore
            if queue:
                games_in_progress: list[InProgressGame] = (
                    session.query(InProgressGame)
                    .filter(InProgressGame.queue_id == queue.id)
                    .all()
                )
                if len(games_in_progress) > 0:
                    await interaction.response.send_message(
                        embed=Embed(
                            description=f"Cannot remove queue with game in progress: {queue.name}",
                            colour=Colour.red(),
                        ),
                        ephemeral=True,
                    )
                    return
                else:
                    session.delete(queue)
                    session.commit()
                    await interaction.response.send_message(
                        embed=Embed(
                            description=f"Queue removed: {queue.name}",
                            colour=Colour.green(),
                        )
                    )
            else:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Queue not found: {queue_name}",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )

    @group.command(name="removerole", description="Remove a discord role from a queue")
    @app_commands.check(is_admin_app_command)
    @app_commands.check(is_command_channel)
    @app_commands.describe(queue_name="Name of queue", role="Discord role")
    async def removequeuerole(
        self, interaction: Interaction, queue_name: str, role: Role
    ):
        """
        Remove a discord role from a queue
        """
        session: SQLAlchemySession
        with Session() as session:
            queue: Queue | None = session.query(Queue).filter(Queue.name.ilike(queue_name)).first()  # type: ignore
            if not queue:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Could not find queue: {queue_name}",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )
                return
            if interaction.guild:
                if not role in interaction.guild.roles:
                    # In case a queue role was deleted from the server
                    queue_role_by_role_id = session.query(QueueRole).filter(
                        QueueRole.queue_id == queue.id,
                        QueueRole.role_id == role.name,
                    )
                    if queue_role_by_role_id:
                        session.query(QueueRole).filter(
                            QueueRole.queue_id == queue.id,
                            QueueRole.role_id == role.name,
                        ).delete()
                        session.commit()
                        await interaction.response.send_message(
                            embed=Embed(
                                description=f"Removed role {role.name} from queue {queue.name}",
                                colour=Colour.green(),
                            )
                        )
                        return
                    await interaction.response.send_message(
                        embed=Embed(
                            description=f"Could not find role: {role.name}",
                            colour=Colour.red(),
                        ),
                        ephemeral=True,
                    )
                    return
                session.query(QueueRole).filter(
                    QueueRole.queue_id == queue.id,
                    QueueRole.role_id == role.id,
                ).delete()
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Removed role {role.name} from queue {queue.name}",
                        colour=Colour.green(),
                    )
                )
                session.commit()

    @group.command(name="setcategory", description="Set category on queue")
    @app_commands.check(is_admin_app_command)
    @app_commands.check(is_command_channel)
    @app_commands.describe(
        queue_name="Existing queue", category_name="Existing category"
    )
    async def setqueuecategory(
        self, interaction: Interaction, queue_name: str, category_name: str
    ):
        session: SQLAlchemySession
        with Session() as session:
            try:
                queue = session.query(Queue).filter(Queue.name.ilike(queue_name)).one()
            except NoResultFound:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Could not find queue **{queue_name}**",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )
                return

            try:
                category = (
                    session.query(Category)
                    .filter(Category.name.ilike(category_name))
                    .one()
                )
            except NoResultFound:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Could not find category **{category_name}**",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )
                return

            queue.category_id = category.id
            session.commit()
            await interaction.response.send_message(
                embed=Embed(
                    description=f"Queue **{queue.name}** set to category **{category.name}**",
                    colour=Colour.green(),
                )
            )

    @group.command(
        name="currencyaward",
        description="Set how much currency is awarded for games in queue",
    )
    @app_commands.check(is_admin_app_command)
    @app_commands.check(is_command_channel)
    @app_commands.describe(
        queue_name="Name of queue", award="Currency value to be awarded"
    )
    async def setqueuecurrencyaward(
        self, interaction: Interaction, queue_name: str, award: int = CURRENCY_AWARD
    ):
        """
        Set how much currency is awarded for games in queue.\nSet to default if no value provided.
        """
        if not ECONOMY_ENABLED:
            await interaction.response.send_message(
                embed=Embed(
                    description="Player economy is disabled",
                    colour=Colour.red(),
                ),
                ephemeral=True,
            )
            return

        session: SQLAlchemySession
        with Session() as session:
            try:
                queue: Queue = (
                    session.query(Queue).filter(Queue.name.ilike(queue_name)).one()
                )
            except NoResultFound:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Could not find queue **{queue_name}**",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )
                return

            queue.currency_award = award
            session.commit()
            await interaction.response.send_message(
                embed=Embed(
                    description=f"**{queue_name}** award set to {award} {CURRENCY_NAME}",
                    colour=Colour.green(),
                )
            )

    @group.command(
        name="moveenabled",
        description="Enables automatic moving of people in game when queue pops",
    )
    @app_commands.check(is_admin_app_command)
    @app_commands.check(is_command_channel)
    @app_commands.describe(
        queue_name="Name of queue", enabled_option="Is queue move enabled"
    )
    async def setqueuemoveenabled(
        self, interaction: Interaction, queue_name: str, enabled_option: bool
    ):
        """
        Enables automatic moving of people in game when queue pops
        """
        if not ENABLE_VOICE_MOVE:
            await interaction.response.send_message(
                embed=Embed(
                    description="Voice movement is disabled",
                    colour=Colour.red(),
                ),
                ephemeral=True,
            )
            return

        session: SQLAlchemySession
        with Session() as session:
            try:
                queue = session.query(Queue).filter(Queue.name.ilike(queue_name)).one()
            except NoResultFound:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Could not find queue **{queue_name}**",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )
                return

            queue.move_enabled = enabled_option
            session.commit()
            if enabled_option:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Player moving enabled on queue **{queue_name}**",
                        colour=Colour.green(),
                    )
                )
            else:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Player moving disabled on queue **{queue_name}**",
                        colour=Colour.green(),
                    )
                )

    @group.command(name="setname", description="Set queue name")
    @app_commands.check(is_admin_app_command)
    @app_commands.check(is_command_channel)
    @app_commands.describe(
        old_queue_name="Name of existing queue", new_queue_name="New name of queue"
    )
    async def setqueuename(
        self, interaction: Interaction, old_queue_name: str, new_queue_name: str
    ):
        """
        Set queue name
        """
        await self.setname(interaction, Queue, old_queue_name, new_queue_name)

    @group.command(name="setordinal", description="Set queue ordinal")
    @app_commands.check(is_admin_app_command)
    @app_commands.check(is_command_channel)
    @app_commands.describe(queue_name="Name of queue", ordinal="Queue ordinal")
    async def setqueueordinal(
        self, interaction: Interaction, queue_name: str, ordinal: int
    ):
        """
        Set queue ordinal
        """
        session: SQLAlchemySession
        with Session() as session:
            queue: Queue = session.query(Queue).filter(Queue.name.ilike(queue_name)).first()  # type: ignore
            if queue:
                queue.ordinal = ordinal
                session.commit()
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Queue {queue.name} ordinal set to {ordinal}",
                        colour=Colour.green(),
                    )
                )
            else:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Queue not found: {queue_name}",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )

    @group.command(name="setrange", description="Set the mu range for a queue")
    @app_commands.check(is_admin_app_command)
    @app_commands.check(is_command_channel)
    @app_commands.describe(
        queue_name="Name of queue", min="Minimum mu", max="Maximum mu"
    )
    async def setqueuerange(
        self, interaction: Interaction, queue_name: str, min: float, max: float
    ):
        """
        Set the mu range for a queue
        """
        session: SQLAlchemySession
        with Session() as session:
            queue: Queue = session.query(Queue).filter(Queue.name.ilike(queue_name)).first()  # type: ignore
            if queue:
                queue.mu_min = min
                queue.mu_max = max
                session.commit()
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Queue {queue.name} range set to [{min}, {max}]",
                        colour=Colour.green(),
                    )
                )
            else:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Queue not found: {queue_name}",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )

    @group.command(name="setrotation", description="Assign a map rotation to a queue")
    @app_commands.check(is_admin_app_command)
    @app_commands.check(is_command_channel)
    @app_commands.describe(queue_name="Name of queue", rotation_name="Name of rotation")
    async def setqueuerotation(
        self, interaction: Interaction, queue_name: str, rotation_name: str
    ):
        """
        Assign a map rotation to a queue
        """
        session: SQLAlchemySession
        with Session() as session:
            try:
                queue = session.query(Queue).filter(Queue.name.ilike(queue_name)).one()
            except NoResultFound:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Could not find queue **{queue_name}**",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )
                return

            try:
                rotation = (
                    session.query(Rotation)
                    .filter(Rotation.name.ilike(rotation_name))
                    .one()
                )
            except NoResultFound:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Could not find rotation **{rotation_name}**",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )
                return

            queue.rotation_id = rotation.id
            session.commit()
            await interaction.response.send_message(
                embed=Embed(
                    description=f"Rotation for **{queue.name}** set to **{rotation.name}**",
                    colour=Colour.green(),
                )
            )

    @group.command(
        name="setsize",
        description="Set the number of players to pop a queue.  Also updates queue vote threshold.",
    )
    @app_commands.check(is_admin_app_command)
    @app_commands.check(is_command_channel)
    @app_commands.describe(queue_name="Name of queue", queue_size="Queue size")
    async def setqueuesize(
        self, interaction: Interaction, queue_name: str, queue_size: int
    ):
        """
        Set the number of players to pop a queue.  Also updates queue vote threshold.
        """
        session: SQLAlchemySession
        with Session() as session:
            queue: Queue | None = (
                session.query(Queue).filter(Queue.name.ilike(queue_name)).first()
            )
            if not queue:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Could not find queue **{queue_name}**",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )
                return

            queue.size = queue_size
            queue.vote_threshold = round(float(queue_size) * 2 / 3)
            session.commit()
            await interaction.response.send_message(
                embed=Embed(
                    description=f"Queue size updated to **{queue.size}**",
                    colour=Colour.green(),
                )
            )

    @group.command(name="setsweaty", description="Make a queue sweaty")
    @app_commands.check(is_admin_app_command)
    @app_commands.check(is_command_channel)
    @app_commands.describe(queue_name="Name of queue")
    async def setqueuesweaty(self, interaction: Interaction, queue_name: str):
        """
        Make a queue sweaty
        """
        session: SQLAlchemySession
        with Session() as session:
            queue: Queue = session.query(Queue).filter(Queue.name.ilike(queue_name)).first()  # type: ignore
            if queue:
                queue.is_sweaty = True
                session.commit()
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Queue {queue.name} is now sweaty",
                        colour=Colour.green(),
                    )
                )
            else:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Queue not found: {queue_name}",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )

    @group.command(
        name="setvotethreshold", description="Set the vote threshold for a queue"
    )
    @app_commands.check(is_admin_app_command)
    @app_commands.check(is_command_channel)
    @app_commands.describe(queue_name="Name of queue", vote_threshold="Vote threshold")
    async def setqueuevotethreshold(
        self, interaction: Interaction, queue_name: str, vote_threshold: int
    ):
        """
        Set the vote threshold for a queue
        """
        session: SQLAlchemySession
        with Session() as session:
            queue: Queue | None = (
                session.query(Queue).filter(Queue.name.ilike(queue_name)).first()
            )
            if not queue:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Could not find queue: {queue_name}",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )
                return

            queue.vote_threshold = vote_threshold
            session.commit()
            await interaction.response.send_message(
                embed=Embed(
                    description=f"Vote threshold for **{queue.name}** set to **{queue.vote_threshold}**",
                    colour=Colour.green(),
                )
            )

    @group.command(name="showrange", description="Show the mu range for a queue")
    @app_commands.check(is_command_channel)
    @app_commands.describe(queue_name="Name of queue")
    async def showqueuerange(self, interaction: Interaction, queue_name: str):
        """
        Show the mu range for a queue
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
                    ephemeral=True,
                )
                return
            await interaction.response.send_message(
                embed=Embed(
                    description=f"Queue range: [{queue.mu_min}, {queue.mu_max}]",
                    colour=Colour.blue(),
                )
            )

    @group.command(
        name="showrotation", description="Show the map rotation assigned to a queue"
    )
    @app_commands.check(is_command_channel)
    @app_commands.describe(queue_name="Name of queue")
    async def showqueuerotation(self, interaction: Interaction, queue_name: str):
        """
        Show the map rotation assigned to a queue
        """
        session: SQLAlchemySession
        with Session() as session:
            queue: Queue | None = (
                session.query(Queue).filter(Queue.name.ilike(queue_name)).first()
            )
            if not queue:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Could not find queue **{queue_name}**",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )
                return

            rotation: Rotation | None = (
                session.query(Rotation).filter(Rotation.id == queue.rotation_id).first()
            )
            if not rotation:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Queue **{queue.name}** has not been assigned a rotation",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )
                return

            query_ordering = (
                [RotationMap.random_weight.desc()]
                if rotation.is_random
                else [RotationMap.ordinal.asc()]
            )
            map_infos: list[tuple[Map, bool, float, bool]] | None = (
                session.query(Map, RotationMap.is_next, RotationMap.random_weight, RotationMap.stop_rotation)
                .join(RotationMap, RotationMap.map_id == Map.id)
                .filter(RotationMap.rotation_id == rotation.id)
                .order_by(*query_ordering)
                .all()
            )
            embed: Embed = Embed(
                title=f"Queue '{queue.name}'",
                colour=Colour.blue(),
            )
            footer = "The next map is bold. Rotation stops on maps with ⏯️"
            if map_infos:
                if rotation.is_random:
                    footer += f"\nMaps before requeuing: {rotation.min_maps_before_requeue}\n"
                    footer += f"Weight increase every time the map is not selected: {rotation.weight_increase}"
                    map_names = ["`[Weight] Map Name`"]
                    for map, is_next, random_weight, stop_rotation in map_infos:
                        if is_next:
                            map_names.append(
                                f"[{random_weight}] **{map.full_name} ({map.short_name}){' ⏯️' if stop_rotation else ''}**"
                            )
                            embed.set_thumbnail(url=map.image_url)
                        else:
                            map_names.append(
                                f"[{random_weight}] {map.full_name} ({map.short_name}){' ⏯️' if stop_rotation else ''}"
                            )
                else:
                    map_names = []
                    for i, r in enumerate(map_infos, start=1):
                        map, is_next, random_weight, stop_rotation = r
                        if is_next:
                            map_names.append(
                                f"{i}. **{map.full_name} ({map.short_name}){' ⏯️' if stop_rotation else ''}**"
                            )
                            embed.set_thumbnail(url=map.image_url)
                        else:
                            map_names.append(f"{i}. {map.full_name} ({map.short_name}){' ⏯️' if stop_rotation else ''}")

            embed.set_footer(text=footer)
            newline = "\n"
            embed.add_field(
                name=(
                    f"Rotation: {rotation.name} (random)"
                    if rotation.is_random
                    else f"Rotation: {rotation.name} (sequential)"
                ),
                value=(f">>> {newline.join(map_names)}" if map_names else "*None*"),
                inline=True,
            )
            await interaction.response.send_message(
                embed=embed,
                ephemeral=False,  # leaving as False for now, since it may be useful for other users to see
            )

    @group.command(
        name="unisolate",
        description="Unisolate a queue (rated, map rotation, auto-adds)",
    )
    @app_commands.check(is_admin_app_command)
    @app_commands.check(is_command_channel)
    @app_commands.describe(queue_name="Name of queue")
    async def unisolatequeue(self, interaction: Interaction, queue_name: str):
        """
        Unisolate a queue (rated, map rotation, auto-adds)
        """
        session: SQLAlchemySession
        with Session() as session:
            queue: Queue = session.query(Queue).filter(Queue.name.ilike(queue_name)).first()  # type: ignore
            if queue:
                queue.is_isolated = False
                session.commit()
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Queue {queue.name} is now unisolated",
                        colour=Colour.green(),
                    )
                )
            else:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Queue not found: {queue_name}",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )

    @group.command(name="unlock", description="Allow players to add to a queue")
    @app_commands.check(is_admin_app_command)
    @app_commands.check(is_command_channel)
    @app_commands.describe(queue_name="Name of queue")
    async def unlockqueue(self, interaction: Interaction, queue_name: str):
        """
        Allow players to add to a queue
        """
        session: SQLAlchemySession
        with Session() as session:
            queue: Queue | None = (
                session.query(Queue).filter(Queue.name.ilike(queue_name)).first()
            )
            if not queue:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Could not find queue: {queue_name}",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )
                return

            queue.is_locked = False
            session.commit()
            await interaction.response.send_message(
                embed=Embed(
                    description=f"Queue **{queue.name}** unlocked",
                    colour=Colour.green(),
                )
            )

    @group.command(name="unsetsweaty", description="Make a queue not sweaty")
    @app_commands.check(is_admin_app_command)
    @app_commands.check(is_command_channel)
    @app_commands.describe(queue_name="Name of queue")
    async def unsetqueuesweaty(self, interaction: Interaction, queue_name: str):
        """
        Make a queue not sweaty
        """
        session: SQLAlchemySession
        with Session() as session:
            queue: Queue = session.query(Queue).filter(Queue.name.ilike(queue_name)).first()  # type: ignore
            if queue:
                queue.is_sweaty = False
                session.commit()
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Queue {queue.name} is no longer sweaty",
                        colour=Colour.green(),
                    )
                )
            else:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Queue not found: {queue_name}",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )

    @setqueuecategory.autocomplete("category_name")
    async def category_autocomplete(self, interaction: Interaction, current: str):
        result = []
        session: SQLAlchemySession
        with Session() as session:
            categories: list[Category] | None = (
                session.query(Category).order_by(Category.name).limit(25).all()
            )  # discord only supports up to 25 choices
            if categories:
                for category in categories:
                    if current in category.name:
                        result.append(
                            app_commands.Choice(name=category.name, value=category.name)
                        )
        return result

    @addqueuerole.autocomplete("queue_name")
    @clearqueuecategory.autocomplete("queue_name")
    @clearqueue.autocomplete("queue_name")
    @clearqueuerange.autocomplete("queue_name")
    @isolatequeue.autocomplete("queue_name")
    @lockqueue.autocomplete("queue_name")
    @mockqueue.autocomplete("queue_name")
    @removequeue.autocomplete("queue_name")
    @removequeuerole.autocomplete("queue_name")
    @setqueuecategory.autocomplete("queue_name")
    @setqueuecurrencyaward.autocomplete("queue_name")
    @setqueuemoveenabled.autocomplete("queue_name")
    @setqueuename.autocomplete("old_queue_name")
    @setqueueordinal.autocomplete("queue_name")
    @setqueuerange.autocomplete("queue_name")
    @setqueuerotation.autocomplete("queue_name")
    @setqueuesize.autocomplete("queue_name")
    @setqueuesweaty.autocomplete("queue_name")
    @setqueuevotethreshold.autocomplete("queue_name")
    @showqueuerange.autocomplete("queue_name")
    @showqueuerotation.autocomplete("queue_name")
    @unisolatequeue.autocomplete("queue_name")
    @unlockqueue.autocomplete("queue_name")
    @unsetqueuesweaty.autocomplete("queue_name")
    async def queue_autocomplete(self, interaction: Interaction, current: str):
        result = []
        session: SQLAlchemySession
        with Session() as session:
            queues: list[Queue] | None = (
                session.query(Queue).order_by(Queue.name).limit(25).all()
            )
            if queues:
                for queue in queues:
                    if current in queue.name:
                        result.append(
                            app_commands.Choice(name=queue.name, value=queue.name)
                        )
        return result

    @setqueuerotation.autocomplete("rotation_name")
    async def rotation_autocomplete(self, interaction: Interaction, current: str):
        result = []
        session: SQLAlchemySession
        with Session() as session:
            rotations: list[Rotation] | None = (
                session.query(Rotation).order_by(Rotation.name).limit(25).all()
            )
            if rotations:
                for rotation in rotations:
                    if current in rotation.name:
                        result.append(
                            app_commands.Choice(name=rotation.name, value=rotation.name)
                        )
        return result
