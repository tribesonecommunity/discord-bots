import logging
from typing import Any

from discord import (
    Client,
    Colour, 
    Embed,
    Interaction
)
from discord.ui import Button, View
from discord.ui.item import Item

_log = logging.getLogger(__name__)


class BaseView(View):
    async def on_error(
        self,
        interaction: Interaction[Client],
        error: Exception,
        item: Item[Any],
    ) -> None:
        if interaction.response.is_done():
            await interaction.followup.send(
                embed=Embed(
                    description="Oops! Something went wrong ☹️",
                    colour=Colour.red(),
                ),
                ephemeral=True,
            )
        else:
            # fallback case that responds to the interaction, since there always needs to be a response
            await interaction.response.send_message(
                embed=Embed(
                    description="Oops! Something went wrong ☹️",
                    colour=Colour.red(),
                ),
                ephemeral=True,
            )
        await super().on_error(interaction, error, item)
        return

    async def disable_buttons(self, interaction: Interaction):
        for child in self.children:
            if type(child) == Button and not child.disabled:
                child.disabled = True
        if interaction.message is not None:
            await interaction.message.edit(view=self)