import logging

from discord import (
    ButtonStyle,
    Client,
    Colour,
    Embed,
    Interaction,
    SelectOption,
    TextStyle,
)
from discord.ui import button, Button, Modal, Select, TextInput

from discord_bots.models import Map
from discord_bots.views.base import BaseView
from discord_bots.views.confirmation import ConfirmationView

_log = logging.getLogger(__name__)


class MapConfigureView(BaseView):
    def __init__(self, interaction: Interaction, map: Map):
        super().__init__(timeout=300)
        self.value: bool = False
        self.map: Map = map
        self.interaction: Interaction = interaction
        self.embed: Embed

    @button(label="Set Name", style=ButtonStyle.primary, row=0)
    async def setname(self, interaction: Interaction, button: Button):
        modal = MapNameModal(self)
        await interaction.response.send_modal(modal)
        return True

    @button(label="Save", style=ButtonStyle.success, row=4)
    async def save(self, interaction: Interaction, button: Button):
        if self.map.short_name == "" or self.map.full_name == "":
            await interaction.response.send_message(
                embed=Embed(
                    description="Map full name & short name must not be empty",
                    colour=Colour.red(),
                ),
                ephemeral=True,
            )
            return

        await interaction.response.defer()
        await self.disable_children(interaction)

        confirmation_buttons = ConfirmationView(interaction.user.id)
        confirmation_buttons.message = await interaction.followup.send(
            embed=Embed(
                description=f"⚠️ Are you sure you want to save configurations for category **{self.map.full_name}**?⚠️",
                colour=Colour.yellow(),
            ),
            view=confirmation_buttons,
            ephemeral=True,
        )
        await confirmation_buttons.wait()
        if not confirmation_buttons.value:
            await self.enable_children(interaction)
            return False
        else:
            self.value = True
            self.stop()
            return True

    @button(label="Cancel", style=ButtonStyle.danger, row=4)
    async def cancel(self, interaction: Interaction, button: Button):
        self.stop()
        return False


class MapNameModal(Modal):
    def __init__(
        self,
        view: MapConfigureView,
    ):
        super().__init__(title="Set Name", timeout=30)
        self.view: MapConfigureView = view
        self.short_name: TextInput = TextInput(
            label="Short Name",
            style=TextStyle.short,
            required=False,
            placeholder=self.view.map.short_name,
        )
        self.full_name: TextInput = TextInput(
            label="Full Name",
            style=TextStyle.short,
            required=False,
            placeholder=self.view.map.full_name,
        )
        self.add_item(self.short_name)
        self.add_item(self.full_name)

    async def on_submit(self, interaction: Interaction[Client]) -> None:
        if not self.short_name.value == "":
            self.view.map.short_name = self.short_name.value
        if not self.full_name.value == "":
            self.view.map.full_name = self.full_name.value

        if self.view.embed.description:
            self.view.embed.description = (
                self.view.embed.description
                + f"\nMap name: **{self.view.map.full_name} ({self.view.map.short_name})**"
            )
        await self.view.interaction.edit_original_response(embed=self.view.embed)

        # Interaction must be responded to, but is then deleted
        await interaction.response.send_message(
            embed=Embed(
                description=f"Set name **{self.view.map.full_name}** (**{self.view.map.short_name}**) successful",
                colour=Colour.blue(),
            ),
            ephemeral=True,
        )
        await interaction.delete_original_response()
        return
