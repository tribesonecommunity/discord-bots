from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import Colour, TextChannel
from discord.ext.commands import Cog, Context
from discord.ui.item import Item

from discord_bots.checks import HasName
from discord_bots.utils import send_message

if TYPE_CHECKING:
    from typing import Any, Type


class BaseCog(Cog):
    def __init__(self, bot):
        self.bot = bot

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
        self, ctx: Context, class_: Type[HasName], old_name: str, new_name: str
    ):
        session = ctx.session

        entry: class_ | None = (
            session.query(class_).filter(class_.name.ilike(old_name)).first()
        )
        if not entry:
            await self.send_error_message(
                f"Could not find {class_.__name__.lower()} **{old_name}**"
            )
            return

        old_name = entry.name
        entry.name = new_name
        session.commit()
        await self.send_success_message(
            f"{class_.__name__} name updated from **{old_name}** to **{new_name}**"
        )


class BaseView(discord.ui.View):
    async def on_error(
        self,
        interaction: discord.Interaction[discord.Client],
        error: Exception,
        item: Item[Any],
    ) -> None:
        if interaction.response.is_done():
            await interaction.followup.send(
                embed=discord.Embed(
                    description="Oops! Something went wrong ☹️",
                    color=discord.Color.red(),
                ),
                ephemeral=True,
            )
        else:
            # fallback case that responds to the interaction, since there always needs to be a response
            await interaction.response.send_message(
                embed=discord.Embed(
                    description="Oops! Something went wrong ☹️",
                    color=discord.Color.red(),
                ),
                ephemeral=True,
            )
        await super().on_error(interaction, error, item)
        return

    async def disable_buttons(self, interaction: discord.Interaction):
        for child in self.children:
            if type(child) == discord.ui.Button and not child.disabled:
                child.disabled = True
        if interaction.message is not None:
            await interaction.message.edit(view=self)
