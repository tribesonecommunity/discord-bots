from __future__ import annotations

from sqlalchemy.orm.session import Session as SQLAlchemySession
from typing import TYPE_CHECKING

from discord import Colour, Embed, Interaction, TextChannel
from discord.ext.commands import Cog, Context

from discord_bots.checks import HasName
from discord_bots.models import Session
from discord_bots.utils import send_message

if TYPE_CHECKING:
    from typing import Type
    from discord.ext.commands import Bot


class BaseCog(Cog):
    def __init__(self, bot):
        self.bot: Bot = bot

    async def cog_before_invoke(self, ctx: Context):
        self.message = ctx.message

    async def send_success_message(self, success_message):
        if isinstance(self.message.channel, TextChannel):
            await send_message(
                self.message.channel,
                embed_description=success_message,
                colour=Colour.green(),
            )

    async def send_info_message(self, info_message):
        if isinstance(self.message.channel, TextChannel):
            await send_message(
                self.message.channel,
                embed_description=info_message,
                colour=Colour.blue(),
            )

    async def send_error_message(self, error_message):
        if isinstance(self.message.channel, TextChannel):
            await send_message(
                self.message.channel,
                embed_description=error_message,
                colour=Colour.red(),
            )

    async def setname(
        self,
        interaction: Interaction,
        class_: Type[HasName],
        old_name: str,
        new_name: str,
    ):
        session: SQLAlchemySession
        with Session() as session:
            entry: class_ | None = (
                session.query(class_).filter(class_.name.ilike(old_name)).first()
            )
            if not entry:
                await interaction.response.send_message(
                    embed=Embed(
                        description=f"Could not find {class_.__name__.lower()} **{old_name}**",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )
                return

            old_name = entry.name
            entry.name = new_name
            session.commit()
            await interaction.response.send_message(
                embed=Embed(
                    description=f"{class_.__name__} name updated from **{old_name}** to **{new_name}**",
                    colour=Colour.green(),
                )
            )
