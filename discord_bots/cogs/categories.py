import logging
from sqlalchemy.orm.exc import NoResultFound
from sqlalchemy.orm.session import Session as SQLAlchemySession

from discord import app_commands, Colour, Embed, Interaction
from discord.ext.commands import Bot

from discord_bots.checks import is_admin_app_command
from discord_bots.cogs.base import BaseCog
from discord_bots.models import (
    Category,
    PlayerCategoryTrueskill,
    Queue,
    Session,
)

_log = logging.getLogger(__name__)


class CategoryCommands(BaseCog):
    def __init__(self, bot: Bot):
        super().__init__(bot)

    group = app_commands.Group(name="category", description="Category related commands")

    @group.command(name="clearqueue", description="Remove category from queue")
    @app_commands.check(is_admin_app_command)
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

    @group.command(name="create", description="Create a new category")
    @app_commands.check(is_admin_app_command)
    @app_commands.describe(name="Name of new category")
    async def createcategory(self, interaction: Interaction, name: str):
        session: SQLAlchemySession
        with Session() as session:
            session.add(Category(name=name, is_rated=True))
            session.commit()
            await interaction.response.send_message(
                embed=Embed(
                    description=f"Category **{name}** added",
                    colour=Colour.green(),
                ),
                ephemeral=True,
            )

    @group.command(name="list", description="List categories")
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

            output = ""
            for category in categories:
                output += f"- **{category.name}**\n"
                queue_names = [
                    x[0]
                    for x in (
                        session.query(Queue.name)
                        .filter(Queue.category_id == category.id)
                        .order_by(Queue.ordinal.asc())
                        .all()
                    )
                ]
                if not queue_names:
                    output += f" - _Queues: None_\n\n"
                else:
                    output += f" - _Queues: {', '.join(queue_names)}_\n\n"

            await interaction.response.send_message(
                embed=Embed(description=output, colour=Colour.blue())
            )

    @group.command(name="remove", description="Remove an existing category")
    @app_commands.check(is_admin_app_command)
    @app_commands.describe(name="Category to be removed")
    async def removecategory(self, interaction: Interaction, name: str):
        session: SQLAlchemySession
        with Session() as session:
            try:
                category: Category = (
                    session.query(Category).filter(Category.name.ilike(name)).one()
                )
            except NoResultFound:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Could not find category **{name}**",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )
                return
            session.query(PlayerCategoryTrueskill).filter(
                category.id == PlayerCategoryTrueskill.category_id
            ).delete()
            session.delete(category)
            session.commit()
            await interaction.response.send_message(
                embed=Embed(
                    description=f"Category **{category.name}** removed",
                    colour=Colour.green(),
                )
            )

    @group.command(name="setname", description="Set category name")
    @app_commands.check(is_admin_app_command)
    @app_commands.describe(
        old_category_name="Existing category", new_category_name="New category name"
    )
    async def setcategoryname(
        self, interaction: Interaction, old_category_name: str, new_category_name: str
    ):
        """
        Set category name
        """
        await self.setname(interaction, Category, old_category_name, new_category_name)

    @group.command(name="setrated", description="Set category rated")
    @app_commands.check(is_admin_app_command)
    @app_commands.describe(category_name="Existing category")
    async def setcategoryrated(self, interaction: Interaction, category_name: str):
        session: SQLAlchemySession
        with Session() as session:
            try:
                category: Category = (
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
            else:
                category.is_rated = True
                session.commit()
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Category **{category.name}** changed to **rated**",
                        colour=Colour.green(),
                    )
                )

    @group.command(name="setunrated", description="Set category unrated")
    @app_commands.check(is_admin_app_command)
    @app_commands.describe(category_name="Existing category")
    async def setcategoryunrated(self, interaction: Interaction, category_name: str):
        session: SQLAlchemySession
        with Session() as session:
            try:
                category: Category = (
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
            else:
                category.is_rated = False
                session.commit()
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Category **{category.name}** changed to **unrated**",
                        colour=Colour.green(),
                    )
                )

    @group.command(name="setqueue", description="Set category on queue")
    @app_commands.check(is_admin_app_command)
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
