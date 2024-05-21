import logging

from discord import Colour, Embed, Interaction, app_commands
from discord.ext.commands import Bot
from sqlalchemy.orm.exc import NoResultFound
from sqlalchemy.orm.session import Session as SQLAlchemySession

from discord_bots.checks import is_admin_app_command, is_command_channel
from discord_bots.cogs.base import BaseCog
from discord_bots.models import Category, PlayerCategoryTrueskill, Queue, Session
from discord_bots.utils import default_sigma_decay_amount, build_category_str
from discord_bots.views.configure_category import CategoryConfigureView

_log = logging.getLogger(__name__)


class CategoryCommands(BaseCog):
    def __init__(self, bot: Bot):
        super().__init__(bot)

    group = app_commands.Group(name="category", description="Category commands")

    @group.command(name="configure", description="Create/Edit a category")
    @app_commands.check(is_admin_app_command)
    @app_commands.check(is_command_channel)
    @app_commands.describe(category_name="New or existing category")
    async def configure(self, interaction: Interaction, category_name: str):
        assert interaction.guild

        new_category: bool = False
        session: SQLAlchemySession
        with Session() as session:
            category: Category | None = (
                session.query(Category)
                .filter(Category.name.ilike(category_name))
                .first()
            )
            if not category:
                new_category = True
                category = Category(
                    name=category_name,
                    is_rated=True,
                    sigma_decay_amount=default_sigma_decay_amount(),
                )

            configure_view = CategoryConfigureView(interaction, category)
            configure_view.embed = Embed(
                description=f"**{category.name}** Category Configure\n-----",
                colour=Colour.blue(),
            )
            await interaction.response.send_message(
                embed=configure_view.embed,
                view=configure_view,
                ephemeral=True,
            )

            await configure_view.wait()
            if configure_view.value:
                if new_category:
                    session.add(category)
                await interaction.delete_original_response()
                await interaction.followup.send(
                    embed=Embed(
                        description=f"**{configure_view.category.name}** has been configured!",
                        colour=Colour.green(),
                    ),
                    ephemeral=True,
                )
                session.commit()
            else:
                await interaction.delete_original_response()
                await interaction.followup.send(
                    embed=Embed(
                        description=f"**{category_name}** configuration cancelled",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )

    @group.command(name="remove", description="Remove an existing category")
    @app_commands.check(is_admin_app_command)
    @app_commands.check(is_command_channel)
    @app_commands.describe(name="Category to be removed")
    @app_commands.rename(name="category")
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
            await interaction.response.send_message(
                embed=Embed(
                    description=f"Category **{category.name}** removed",
                    colour=Colour.green(),
                )
            )
            session.commit()

    @group.command(name="show", description="Show category details")
    @app_commands.check(is_command_channel)
    @app_commands.describe(category_name="Existing category")
    @app_commands.rename(category_name="category")
    async def showcategory(self, interaction: Interaction, category_name: str):
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

        await interaction.response.send_message(
            embed=Embed(
                description=build_category_str(category),
                colour=Colour.blue(),
            )
        )

    @configure.autocomplete("category_name")
    @removecategory.autocomplete("name")
    @showcategory.autocomplete("category_name")
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
