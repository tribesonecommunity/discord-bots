import logging
from typing import Optional

import discord

from discord_bots.cogs.base import BaseView


class ConfirmationView(BaseView):
    """
    Generic View that can be added to messages to implement a confirmation popup.
    You must wait for the view to finish.
    """

    def __init__(self, author_id, timeout=10):
        super().__init__(timeout=timeout)
        self.value = None
        self.message: Optional[discord.Message] = None
        self.author_id: int = author_id

    async def on_timeout(self) -> None:
        if self.message:
            await self.message.delete()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user and interaction.user.id == self.author_id:
            return True
        else:
            await interaction.response.send_message(
                "This confirmation dialog is not for you.", ephemeral=True
            )
            return False

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.secondary, emoji="✅")
    async def confirm(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        self.value = True
        await interaction.response.defer()
        await interaction.delete_original_response()
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, emoji="❌")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.value = False
        await interaction.response.defer()
        await interaction.delete_original_response()
        self.stop()
