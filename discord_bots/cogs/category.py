import logging

from discord import Colour, Embed, Interaction, app_commands
from discord.ext.commands import Bot
from sqlalchemy.orm.exc import NoResultFound
from sqlalchemy.orm.session import Session as SQLAlchemySession

from discord_bots.checks import is_admin_app_command, is_command_channel
from discord_bots.cogs.base import BaseCog
from discord_bots.models import Category, PlayerCategoryTrueskill, Queue, Session
from discord_bots.utils import default_sigma_decay_amount, build_category_str

_log = logging.getLogger(__name__)


class CategoryCommands(BaseCog):
    def __init__(self, bot: Bot):
        super().__init__(bot)

    group = app_commands.Group(name="category", description="Category commands")

    @group.command(name="create", description="Create a new category")
    @app_commands.check(is_admin_app_command)
    @app_commands.check(is_command_channel)
    @app_commands.describe(name="Name of new category")
    async def createcategory(self, interaction: Interaction, name: str):
        session: SQLAlchemySession
        with Session() as session:
            session.add(Category(name=name, is_rated=True, sigma_decay_amount=default_sigma_decay_amount()))
            session.commit()
            await interaction.response.send_message(
                embed=Embed(
                    description=f"Category **{name}** added",
                    colour=Colour.green(),
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
            session.commit()
            await interaction.response.send_message(
                embed=Embed(
                    description=f"Category **{category.name}** removed",
                    colour=Colour.green(),
                )
            )

    @group.command(name="setname", description="Set category name")
    @app_commands.check(is_admin_app_command)
    @app_commands.check(is_command_channel)
    @app_commands.describe(
        old_category_name="Existing category", new_category_name="New category name"
    )
    @app_commands.rename(old_category_name="category")
    async def setcategoryname(
        self, interaction: Interaction, old_category_name: str, new_category_name: str
    ):
        """
        Set category name
        """
        await self.setname(interaction, Category, old_category_name, new_category_name)

    @group.command(name="setrated", description="Set category rated")
    @app_commands.check(is_admin_app_command)
    @app_commands.check(is_command_channel)
    @app_commands.describe(category_name="Existing category")
    @app_commands.rename(category_name="category")
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

    @group.command(name="setsigmadecay", description="Set the amount of sigma decay per day for a given category")
    @app_commands.check(is_admin_app_command)
    @app_commands.check(is_command_channel)
    @app_commands.describe(category_name="Existing category")
    @app_commands.rename(category_name="category")
    async def setcategorysigmadecay(
        self,
        interaction: Interaction,
        category_name: str,
        sigma_decay_amount: float,
        sigma_decay_grace_days: int,
        sigma_decay_max_decay_proportion: float,
    ) -> None:
        """
        Set the amount of sigma decay per day for a given category
        """
        session: SQLAlchemySession
        with Session() as session:
            try:
                category: Category = (
                    session.query(Category).filter(Category.name.ilike(category_name)).one()
                )
            except NoResultFound:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Could not find category **{category_name}**",
                        colour=Colour.red(),
                    ),
                    ephemeral=True
                )
                return
            category.sigma_decay_amount = sigma_decay_amount
            category.sigma_decay_grace_days = sigma_decay_grace_days
            category.sigma_decay_max_decay_proportion = sigma_decay_max_decay_proportion
            session.commit()

        output = f"Sigma decay settings updated for **{category.name}**:\n"
        output += f"- Decay amount: {sigma_decay_amount}\n"
        output += f"- Grace days: {sigma_decay_grace_days}\n"
        output += f"- Max decay proportion: {sigma_decay_max_decay_proportion}\n"
        await interaction.response.send_message(
            embed=Embed(
                description=output,
                colour=Colour.green(),
            )
        )

    @group.command(name="setunrated", description="Set category unrated")
    @app_commands.check(is_admin_app_command)
    @app_commands.check(is_command_channel)
    @app_commands.describe(category_name="Existing category")
    @app_commands.rename(category_name="category")
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

    @group.command(
        name="setminleaderboardgames",
        description="Set minimum number of games to be on the category leaderboard",
    )
    @app_commands.check(is_admin_app_command)
    @app_commands.check(is_command_channel)
    @app_commands.describe(
        category_name="Existing category",
        min_num_games="Games required to show on the leaderboard",
    )
    @app_commands.rename(category_name="category")
    async def setmingamesforleaderboard(
        self,
        interaction: Interaction,
        category_name: str,
        min_num_games: int,
    ):
        if min_num_games < 0:
            await interaction.response.send_message(
                embed=Embed(
                    description=f"The minimum number of games must be non-negative",
                    colour=Colour.red(),
                ),
                ephemeral=True,
            )
            return

        session: SQLAlchemySession
        with Session() as session:
            category: Category | None = (
                session.query(Category).filter(Category.name.ilike(category_name)).one()
            )
            if not category:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Could not find category **{category_name}**",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )
                return
            category.min_games_for_leaderboard = min_num_games
            session.commit()
        await interaction.response.send_message(
            embed=Embed(
                description=f"The minimum number of games required to appear on the leaderboard for category **{category.name}** is now **{min_num_games}**",
                colour=Colour.green(),
            )
        )

    @group.command(name="show", description="Show category details")
    @app_commands.check(is_command_channel)
    @app_commands.describe(category_name="Existing category")
    @app_commands.rename(category_name="category")
    async def showcategory(self, interaction: Interaction, category_name: str):
        session: SQLAlchemySession
        with Session() as session:
            try:
                category: Category = (
                    session.query(Category).filter(Category.name.ilike(category_name)).one()
                )
            except NoResultFound:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Could not find category **{category_name}**",
                        colour=Colour.red(),
                    ),
                    ephemeral=True
                )
                return

        await interaction.response.send_message(
            embed=Embed(
                description=build_category_str(category),
                colour=Colour.blue(),
            )
        )

    @createcategory.autocomplete("name")
    @removecategory.autocomplete("name")
    @setcategoryname.autocomplete("old_category_name")
    @setcategoryrated.autocomplete("category_name")
    @setcategoryunrated.autocomplete("category_name")
    @setmingamesforleaderboard.autocomplete("category_name")
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
