import logging
from typing import Optional

import discord


class ConfirmationView(discord.ui.View):
    """
    Generic View that can be added to messages to implement a confirmation popup.
    You must wait for the view to finish.
    """

    def __init__(self):
        super().__init__()
        self.value = None

    @discord.ui.button(label="Yes", style=discord.ButtonStyle.secondary, emoji="✅")
    async def yes(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.value = True
        for child in self.children:
            if type(child) == discord.ui.Button and not child.disabled:
                child.disabled = True
        await interaction.response.edit_message(view=self)
        self.stop()

    @discord.ui.button(label="No", style=discord.ButtonStyle.secondary, emoji="❌")
    async def no(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.value = False
        for child in self.children:
            if type(child) == discord.ui.Button and not child.disabled:
                child.disabled = True
        await interaction.response.edit_message(view=self)
        self.stop()
