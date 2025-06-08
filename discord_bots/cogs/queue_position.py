import logging

from discord import Colour, Embed, Interaction, app_commands
from discord.ext.commands import Bot
from sqlalchemy.orm.session import Session as SQLAlchemySession

from discord_bots.checks import is_admin_app_command, is_command_channel
from discord_bots.cogs.base import BaseCog
from discord_bots.cogs.position import PositionCommands
from discord_bots.cogs.queue import QueueCommands
from discord_bots.models import Position, Queue, QueuePosition, Session

_log = logging.getLogger(__name__)


class QueuePositionCommands(BaseCog):
    def __init__(self, bot: Bot):
        super().__init__(bot)

    group = app_commands.Group(
        name="queueposition", description="Queue position commands"
    )

    @group.command(name="add", description="Add a position to a queue")
    @app_commands.check(is_admin_app_command)
    @app_commands.check(is_command_channel)
    @app_commands.describe(
        queue_name="Name of queue",
        position_name="Name of position",
        count="Number of players needed for this position",
    )
    @app_commands.autocomplete(queue_name=QueueCommands.queue_autocomplete)
    @app_commands.autocomplete(position_name=PositionCommands.position_autocomplete)
    async def addqueueposition(
        self, interaction: Interaction, queue_name: str, position_name: str, count: int
    ):
        """
        Add a position to a queue
        """
        session: SQLAlchemySession
        with Session() as session:
            queue = session.query(Queue).filter(Queue.name.ilike(queue_name)).first()  # type: ignore
            if not queue:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Could not find queue: **{queue_name}**",
                        colour=Colour.blue(),
                    ),
                    ephemeral=True,
                )
                return

            position = session.query(Position).filter(Position.name.ilike(position_name)).first()  # type: ignore
            if not position:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Could not find position: **{position_name}**",
                        colour=Colour.blue(),
                    ),
                    ephemeral=True,
                )
                return

            existing_position = (
                session.query(QueuePosition)
                .filter(
                    QueuePosition.queue_id == queue.id,
                    QueuePosition.position_id == position.id,
                )
                .first()
            )
            if existing_position:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Position **{position_name}** already exists in queue **{queue_name}**",
                        colour=Colour.blue(),
                    ),
                    ephemeral=True,
                )
                return

            queue_position = QueuePosition(
                queue_id=queue.id,
                position_id=position.id,
                count=count,
            )
            session.add(queue_position)
            session.commit()

            await interaction.response.send_message(
                embed=Embed(
                    description=f"Added position **{position_name}** to queue **{queue_name}** with count **{count}**",
                    colour=Colour.blue(),
                ),
                ephemeral=True,
            )

    @group.command(name="update", description="Update a position's count in a queue")
    @app_commands.check(is_admin_app_command)
    @app_commands.check(is_command_channel)
    @app_commands.describe(
        queue_name="Name of queue",
        position_name="Name of position",
        count="New number of players needed for this position",
    )
    @app_commands.autocomplete(queue_name=QueueCommands.queue_autocomplete)
    @app_commands.autocomplete(position_name=PositionCommands.position_autocomplete)
    async def updatequeueposition(
        self, interaction: Interaction, queue_name: str, position_name: str, count: int
    ):
        """
        Update a position's count in a queue
        """
        session: SQLAlchemySession
        with Session() as session:
            queue = session.query(Queue).filter(Queue.name.ilike(queue_name)).first()  # type: ignore
            if not queue:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Could not find queue: **{queue_name}**",
                        colour=Colour.blue(),
                    ),
                    ephemeral=True,
                )
                return

            position = session.query(Position).filter(Position.name.ilike(position_name)).first()  # type: ignore
            if not position:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Could not find position: **{position_name}**",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )
                return

            queue_position = (
                session.query(QueuePosition)
                .filter(
                    QueuePosition.queue_id == queue.id,
                    QueuePosition.position_id == position.id,
                )
                .first()
            )
            if not queue_position:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Position **{position_name}** does not exist in queue **{queue_name}**",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )
                return

            queue_position.count = count
            session.commit()

            await interaction.response.send_message(
                embed=Embed(
                    description=f"Updated position **{position_name}** in queue **{queue_name}** to count **{count}**",
                    colour=Colour.green(),
                ),
                ephemeral=True,
            )

    @group.command(name="remove", description="Remove a position from a queue")
    @app_commands.check(is_admin_app_command)
    @app_commands.check(is_command_channel)
    @app_commands.describe(queue_name="Name of queue", position_name="Name of position")
    @app_commands.autocomplete(queue_name=QueueCommands.queue_autocomplete)
    @app_commands.autocomplete(position_name=PositionCommands.position_autocomplete)
    async def removequeueposition(
        self, interaction: Interaction, queue_name: str, position_name: str
    ):
        """
        Remove a position from a queue
        """
        session: SQLAlchemySession
        with Session() as session:
            queue = session.query(Queue).filter(Queue.name.ilike(queue_name)).first()  # type: ignore
            if not queue:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Could not find queue: **{queue_name}**",
                        colour=Colour.blue(),
                    ),
                    ephemeral=True,
                )
                return

            position = session.query(Position).filter(Position.name.ilike(position_name)).first()  # type: ignore
            if not position:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Could not find position: **{position_name}**",
                        colour=Colour.blue(),
                    ),
                    ephemeral=True,
                )
                return

            queue_position = (
                session.query(QueuePosition)
                .filter(
                    QueuePosition.queue_id == queue.id,
                    QueuePosition.position_id == position.id,
                )
                .first()
            )
            if not queue_position:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Position **{position_name}** does not exist in queue **{queue_name}**",
                        colour=Colour.blue(),
                    ),
                    ephemeral=True,
                )
                return

            session.delete(queue_position)
            session.commit()

            await interaction.response.send_message(
                embed=Embed(
                    description=f"Removed position **{position_name}** from queue **{queue_name}**",
                    colour=Colour.blue(),
                ),
                ephemeral=True,
            )

    @group.command(name="list", description="List positions for a queue")
    @app_commands.check(is_command_channel)
    @app_commands.describe(queue_name="Name of queue")
    @app_commands.autocomplete(queue_name=QueueCommands.queue_autocomplete)
    async def listqueuepositions(self, interaction: Interaction, queue_name: str):
        """
        List positions for a queue
        """
        session: SQLAlchemySession
        with Session() as session:
            queue = session.query(Queue).filter(Queue.name.ilike(queue_name)).first()  # type: ignore
            if not queue:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Could not find queue: **{queue_name}**",
                        colour=Colour.blue(),
                    ),
                    ephemeral=True,
                )
                return

            # Get all positions for this queue
            queue_positions = (
                session.query(QueuePosition)
                .join(Position)
                .filter(QueuePosition.queue_id == queue.id)
                .order_by(Position.name)
                .all()
            )

            if not queue_positions:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"No positions defined for queue: **{queue_name}**",
                        colour=Colour.blue(),
                    ),
                    ephemeral=True,
                )
                return

            # Calculate total position count
            total_position_count = sum(qp.count for qp in queue_positions)
            expected_count = queue.size // 2  # Each team needs half the players

            # Build position list
            position_list = []
            for qp in queue_positions:
                position = (
                    session.query(Position)
                    .filter(Position.id == qp.position_id)
                    .first()
                )
                position_list.append(f"**{position.name}**: {qp.count}")

            # Create embed
            embed = Embed(
                title=f"Positions for queue: {queue_name}",
                description="\n".join(position_list),
                colour=Colour.blue(),
            )

            # Add warning if position counts don't match
            if total_position_count != expected_count:
                embed.add_field(
                    name="⚠️ Warning",
                    value=f"Total position count ({total_position_count}) does not match expected count ({expected_count}) for queue size {queue.size}",
                    inline=False,
                )
                embed.colour = Colour.red()

            await interaction.response.send_message(embed=embed)
