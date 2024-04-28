import logging
from glob import glob

from discord import Colour, Embed, Interaction, app_commands
from discord.ext.commands import Bot
from discord.utils import escape_markdown
from sqlalchemy.orm.session import Session as SQLAlchemySession

from discord_bots.bot import bot
from discord_bots.checks import is_admin_app_command, is_command_channel
from discord_bots.cogs.base import BaseCog
from discord_bots.config import DB_NAME
from discord_bots.models import (
    AdminRole,
    Category,
    Map,
    Player,
    Queue,
    QueueNotification,
    QueueRole,
    Rotation,
    RotationMap,
    Session,
)
from discord_bots.utils import buildCategoryOutput

_log = logging.getLogger(__name__)


class ListCommands(BaseCog):
    def __init__(self, bot: Bot):
        super().__init__(bot)

    group = app_commands.Group(name="list", description="List commands")

    @group.command(name="admin", description="List admin users")
    @app_commands.check(is_command_channel)
    async def listadmins(self, interaction: Interaction):
        output = "Admins:"
        session: SQLAlchemySession
        with Session() as session:
            player: Player
            for player in session.query(Player).filter(Player.is_admin == True).all():
                output += f"\n- {escape_markdown(player.name)}"

        await interaction.response.send_message(
            embed=Embed(description=output, colour=Colour.blue())
        )

    @group.command(name="adminrole", description="List admin roles")
    @app_commands.check(is_command_channel)
    async def listadminroles(self, interaction: Interaction):
        output = "Admin roles:"
        session: SQLAlchemySession
        if not interaction.guild:
            return

        with Session() as session:
            admin_role_ids = list(
                map(lambda x: x.role_id, session.query(AdminRole).all())
            )
        admin_role_names: list[str] = []

        role_id_to_role_name: dict[int, str] = {
            role.id: role.name for role in interaction.guild.roles
        }

        for admin_role_id in admin_role_ids:
            if admin_role_id in role_id_to_role_name:
                admin_role_names.append(role_id_to_role_name[admin_role_id])
        output += f"\n{', '.join(admin_role_names)}"

        await interaction.response.send_message(
            embed=Embed(description=output, colour=Colour.blue())
        )

    @group.command(name="ban", description="List banned players")
    @app_commands.check(is_command_channel)
    async def listbans(self, interaction: Interaction):
        output = "Bans:"
        session: SQLAlchemySession
        with Session() as session:
            for player in session.query(Player).filter(Player.is_banned == True):
                output += f"\n- {escape_markdown(player.name)}"
        await interaction.response.send_message(
            embed=Embed(
                description=output,
                colour=Colour.blue(),
            )
        )

    @group.command(name="category", description="List categories")
    @app_commands.check(is_command_channel)
    async def listcategories(self, interaction: Interaction):
        session: SQLAlchemySession
        with Session() as session:
            categories: list[Category] | None = (
                session.query(Category).order_by(Category.created_at.asc()).all()
            )
            if not categories:
                await interaction.response.send_message(
                    embed=Embed(
                        description="_-- No categories-- _",
                        colour=Colour.blue(),
                    ),
                    ephemeral=True,
                )
                return

            output = "\n".join(
                buildCategoryOutput(category) 
                for category 
                in categories
            )

            await interaction.response.send_message(
                embed=Embed(description=output, colour=Colour.blue())
            )

    @group.command(name="channel", description="List bot channels")
    @app_commands.check(is_admin_app_command)
    @app_commands.check(is_command_channel)
    async def listchannels(self, interaction: Interaction):
        for channel in bot.get_all_channels():
            _log.info(channel.id, channel)  # DEBUG, TRACE?

        await interaction.response.send_message(
            embed=Embed(
                description="Check the logs",
                colour=Colour.blue(),
            ),
            ephemeral=True,
        )

    @group.command(name="dbbackup", description="List database backups")
    @app_commands.check(is_admin_app_command)
    @app_commands.check(is_command_channel)
    async def listdbbackups(self, interaction: Interaction):
        output = "Backups:"
        for filename in glob(f"{DB_NAME}_*.db"):
            output += f"\n- {filename}"

        await interaction.response.send_message(
            embed=Embed(
                description=output,
                colour=Colour.blue(),
            ),
            ephemeral=True,
        )

    @group.command(name="map", description="List all maps in the map pool")
    @app_commands.check(is_command_channel)
    async def listmaps(self, interaction: Interaction):
        """
        List all maps in the map pool
        """
        session: SQLAlchemySession
        with Session() as session:
            maps = session.query(Map).order_by(Map.created_at.asc()).all()

            if not maps:
                output = "_-- No Maps --_"
            else:
                output = ""
                for map in maps:
                    output += f"- {map.full_name} ({map.short_name})\n"

            await interaction.response.send_message(
                embed=Embed(
                    description=output,
                    colour=Colour.blue(),
                )
            )

    @group.command(name="notification", description="List your notifications")
    @app_commands.check(is_command_channel)
    async def listnotifications(self, interaction: Interaction):
        output = "Queue notifications:"
        session: SQLAlchemySession
        with Session() as session:
            queue_notifications: list[QueueNotification] = (
                session.query(QueueNotification)
                .filter(QueueNotification.player_id == interaction.user.id)
                .all()
            )
            for queue_notification in queue_notifications:
                queue: Queue | None = (
                    session.query(Queue)
                    .filter(Queue.id == queue_notification.queue_id)
                    .first()
                )
                if queue:
                    output += f"\n- {queue.name} {queue_notification.size}"
                else:
                    output += f"\n- *Unknown* {queue_notification.size}"
        await interaction.response.send_message(
            embed=Embed(
                description=output,
                colour=Colour.blue(),
            ),
            ephemeral=True,
        )

    @group.command(
        name="queue", description="List all queues with their category and rotation"
    )
    @app_commands.check(is_command_channel)
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
                    ephemeral=True,
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

    @group.command(
        name="queuerole",
        description="List all queues and their associated discord roles",
    )
    @app_commands.check(is_command_channel)
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
                    session.query(QueueRole)
                    .filter(QueueRole.queue_id == queue.id)
                    .all()
                ):
                    role = interaction.guild.get_role(queue_role.role_id)
                    if role:
                        queue_role_names.append(role.name)
                    else:
                        queue_role_names.append(str(queue_role.role_id))
                output += f"**{queue.name}**: {', '.join(queue_role_names)}\n"
            await interaction.response.send_message(
                embed=Embed(description=output, colour=Colour.blue())
            )

    @group.command(
        name="rotation", description="List all rotations in the rotation pool"
    )
    @app_commands.check(is_command_channel)
    async def listrotations(self, interaction: Interaction):
        """
        List all rotations in the rotation pool
        """
        session: SQLAlchemySession
        with Session() as session:
            rotations: list[Rotation] | None = (
                session.query(Rotation).order_by(Rotation.created_at.asc()).all()
            )
            if not rotations:
                await interaction.response.send_message(
                    embed=Embed(
                        description="_-- No Rotations-- _", colour=Colour.blue()
                    )
                )
                return

            output = ""

            for rotation in rotations:
                output += f"### {rotation.name}\n"

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
                    output += f" - Maps:  None\n"
                else:
                    output += f" - Maps:  {', '.join(map_names)}\n"

                queue_names = [
                    x[0]
                    for x in (
                        session.query(Queue.name)
                        .filter(Queue.rotation_id == rotation.id)
                        .order_by(Queue.ordinal.asc())
                        .all()
                    )
                ]
                if not queue_names:
                    output += f" - Queues:  None\n"
                else:
                    output += f" - Queues:  {', '.join(queue_names)}\n"

            await interaction.response.send_message(
                embed=Embed(description=output, colour=Colour.blue())
            )
