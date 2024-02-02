from discord.ext.commands import Bot, Context, check, command
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import NoResultFound

from discord_bots.checks import is_admin
from discord_bots.cogs.base import BaseCog
from discord_bots.models import Map, Queue, Rotation, RotationMap


class QueueCommands(BaseCog):
    def __init__(self, bot: Bot):
        super().__init__(bot)

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
    async def showqueuerotation(self, ctx: Context, queue_name: str):
        """
        Shows the map rotation assigned to a queue
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
    async def setqueuename(
        self, ctx: Context, old_queue_name: str, new_queue_name: str
    ):
        session = ctx.session

        queue: Queue | None = (
            session.query(Queue).filter(Queue.name.ilike(old_queue_name)).first()
        )
        if not queue:
            await self.send_error_message(f"Could not find queue **{old_queue_name}**")

        queue.name = new_queue_name
        session.commit()
        await self.send_success_message(
            f"Queue name updated from **{old_queue_name}** to **{new_queue_name}**"
        )
