from discord.ext.commands import Bot, Context, check, command
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import NoResultFound

from discord_bots.checks import is_admin
from discord_bots.cogs.base import BaseCog
from discord_bots.models import Map, Queue, Rotation, RotationMap


class Queue(BaseCog):
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

        queue_data = (
            session.query(
                Queue.name, Rotation.name, RotationMap.ordinal, Map.short_name
            )
            .filter(Queue.name.ilike(queue_name))
            .outerjoin(Rotation, Rotation.id == Queue.rotation_id)
            .outerjoin(RotationMap, RotationMap.rotation_id == Rotation.id)
            .outerjoin(Map, RotationMap.map_id == Map.id)
            .order_by(RotationMap.ordinal.asc())
            .all()
        )

        if not queue_data:
            await self.send_error_message(f"Could not find queue **{queue_name}**")
            return

        if not queue_data[0][1]:
            await self.send_error_message(
                f"Queue **{queue_data[0][0]}** has not been assigned a rotation"
            )
            return

        maps = []
        if not queue_data[0][3]:
            maps = ["None"]
        else:
            for row in queue_data:
                maps.append(row[3])

        output = f"**{queue_data[0][0]}** is assigned to **{queue_data[0][1]}**\n"
        output += f"- _Maps: {', '.join(maps)}_"
        await self.send_info_message(output)
