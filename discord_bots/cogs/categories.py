import sqlalchemy
from discord.ext.commands import Bot, Context, check, command
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import NoResultFound

from discord_bots.checks import is_admin
from discord_bots.cogs.base import BaseCog
from discord_bots.models import (
    Category,
    Map,
    PlayerCategoryTrueskill,
    Queue,
    Rotation,
    RotationMap,
)
from discord_bots.utils import update_next_map_to_map_after_next


class CategoryCommands(BaseCog):
    def __init__(self, bot: Bot):
        super().__init__(bot)

    @command()
    @check(is_admin)
    async def clearqueuecategory(self, ctx: Context, queue_name: str):
        session = ctx.session

        try:
            queue = session.query(Queue).filter(Queue.name.ilike(queue_name)).one()
        except NoResultFound:
            await self.send_error_message(f"Could not find queue **{queue_name}**")
            return

        queue.category_id = None
        session.commit()
        await self.send_success_message(f"Queue **{queue.name}** category cleared")

    @command()
    @check(is_admin)
    async def createcategory(self, ctx: Context, name: str):
        session = ctx.session
        session.add(Category(name=name, is_rated=True))
        session.commit()
        await self.send_success_message(f"Category **{name}** added")

    @command()
    async def listcategories(self, ctx: Context):
        session = ctx.session
        categories: list[Category] | None = (
            session.query(Category).order_by(Category.created_at.asc()).all()
        )
        if not categories:
            await self.send_info_message("_-- No categories-- _")
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

        await self.send_info_message(output)

    @command()
    @check(is_admin)
    async def removecategory(self, ctx: Context, name: str):
        session: sqlalchemy.orm.Session = ctx.session
        try:
            category: Category = (
                session.query(Category).filter(Category.name.ilike(name)).one()
            )
        except NoResultFound:
            await self.send_error_message(f"Could not find category **{name}**")
            return
        session.query(PlayerCategoryTrueskill).filter(
            category.id == PlayerCategoryTrueskill.category_id
        ).delete()
        session.delete(category)
        session.commit()
        await self.send_success_message(f"Category **{category.name}** removed")

    @command()
    @check(is_admin)
    async def setcategoryname(
        self, ctx: Context, old_category_name: str, new_category_name: str
    ):
        """
        Set category name
        """
        await self.setname(ctx, Category, old_category_name, new_category_name)

    @command()
    @check(is_admin)
    async def setcategoryrated(self, ctx: Context, category_name: str):
        session = ctx.session

        try:
            category: Category | None = (
                session.query(Category).filter(Category.name.ilike(category_name)).one()
            )
        except NoResultFound:
            await self.send_error_message(
                f"Could not find category **{category_name}**"
            )
            return

        category.is_rated = True
        session.commit()
        await self.send_success_message(
            f"Category **{category.name}** changed to **rated**"
        )

    @command()
    @check(is_admin)
    async def setcategoryunrated(self, ctx: Context, category_name: str):
        session = ctx.session

        try:
            category: Category | None = (
                session.query(Category).filter(Category.name.ilike(category_name)).one()
            )
        except NoResultFound:
            await self.send_error_message(
                f"Could not find category **{category_name}**"
            )
            return

        category.is_rated = False
        session.commit()
        await self.send_success_message(
            f"Category **{category.name}** changed to **unrated**"
        )

    @command()
    @check(is_admin)
    async def setqueuecategory(self, ctx: Context, queue_name: str, category_name: str):
        session = ctx.session

        try:
            queue = session.query(Queue).filter(Queue.name.ilike(queue_name)).one()
        except NoResultFound:
            await self.send_error_message(f"Could not find queue **{queue_name}**")
            return

        try:
            category = (
                session.query(Category).filter(Category.name.ilike(category_name)).one()
            )
        except NoResultFound:
            await self.send_error_message(
                f"Could not find category **{category_name}**"
            )
            return

        queue.category_id = category.id
        session.commit()
        await self.send_success_message(
            f"Queue **{queue.name}** set to category **{category.name}**"
        )

    @command()
    @check(is_admin)
    async def setmingamesforleaderboard(
        self, ctx: Context, min_num_games: int, category_name: str
    ):
        if min_num_games < 0:
            await self.send_error_message(
                f"The minimum number of games must be non-negative"
            )
            return

        session = ctx.session
        category: Category | None = (
            session.query(Category).filter(Category.name.ilike(category_name)).one()
        )
        if not category:
            await self.send_error_message(
                f"Could not find category **{category_name}**"
            )
            return
        category.min_games_for_leaderboard = min_num_games
        session.commit()
        await self.send_success_message(
            f"The minimum number of games required to appear on the leaderboard for category **{category.name}** is now **{min_num_games}**"
        )
