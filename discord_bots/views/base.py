import logging
from typing import Any

from discord import Client, Colour, Embed, Interaction
from discord.ui import View
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

    async def disable_children(self, interaction: Interaction):
        for child in self.children:
            if not child.disabled:  # type: ignore
                try:
                    child.disabled = True  # type: ignore
                except Exception:
                    pass

        if interaction.response.is_done():
            await interaction.edit_original_response(view=self)
        else:
            if interaction.message is not None:
                await interaction.message.edit(view=self)

    async def enable_children(self, interaction: Interaction):
        for child in self.children:
            if child.disabled:  # type: ignore
                try:
                    child.disabled = False  # type: ignore
                except Exception:
                    pass

        if interaction.response.is_done():
            await interaction.edit_original_response(view=self)
        else:
            if interaction.message is not None:
                await interaction.message.edit(view=self)
