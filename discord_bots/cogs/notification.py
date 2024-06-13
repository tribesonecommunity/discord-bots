import logging

from discord import Colour, Embed, Interaction, app_commands
from discord.ext.commands import Bot
from sqlalchemy.orm.session import Session as SQLAlchemySession

from discord_bots.checks import is_command_channel
from discord_bots.cogs.base import BaseCog
from discord_bots.models import Queue, QueueNotification, Session
from discord_bots.utils import queue_autocomplete

_log = logging.getLogger(__name__)


class NotificationCommands(BaseCog):
    def __init__(self, bot: Bot):
        super().__init__(bot)

    group = app_commands.Group(name="notify", description="Notification commands")

    @group.command(
        name="queue",
        description="Get DM when a queue reaches the specified number of players. You will only be notified once.",
    )
    @app_commands.check(is_command_channel)
    @app_commands.describe(
        queue_name="Queue name",
        size="A one-time notification will be sent when the queue reaches this number of players",
    )
    @app_commands.autocomplete(queue_name=queue_autocomplete)
    @app_commands.rename(queue_name="queue")
    async def notify(self, interaction: Interaction, queue_name: str, size: int):
        if size <= 0:
            await interaction.response.send_message(
                embed=Embed(
                    description="size must be greater than 0",
                    colour=Colour.red(),
                ),
                ephemeral=True,
            )
            return

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
            session.add(
                QueueNotification(
                    queue_id=queue.id, player_id=interaction.user.id, size=size
                )
            )
            await interaction.response.send_message(
                embed=Embed(
                    description=f"Notification added for **{queue.name}** at `{size}` players.",
                    colour=Colour.green(),
                ),
                ephemeral=True,
            )
            session.commit()

    @group.command(name="remove", description="Remove all your notifications")
    @app_commands.check(is_command_channel)
    async def removenotifications(self, interaction: Interaction):
        session: SQLAlchemySession
        with Session() as session:
            session.query(QueueNotification).filter(
                QueueNotification.player_id == interaction.user.id
            ).delete()
            session.commit()
        await interaction.response.send_message(
            embed=Embed(
                description=f"All queue notifications removed",
                colour=Colour.green(),
            ),
            ephemeral=True,
        )
