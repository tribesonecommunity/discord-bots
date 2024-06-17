import logging
from re import A

import discord
import sqlalchemy
from discord import (
    ButtonStyle,
    Client,
    Colour,
    Embed,
    Interaction,
    SelectOption,
    TextStyle,
)
from discord.ui import Button, Modal, Select, TextInput, button

from discord_bots.models import Map, Session
from discord_bots.views.base import BaseView
from discord_bots.views.confirmation import ConfirmationView

_log = logging.getLogger(__name__)


class MapConfigureView(BaseView):
    def __init__(
        self,
        full_name: str,
        short_name: str,
        image_url: str | None = None,
        map_id: str | None = None,
    ):
        super().__init__(timeout=None)
        self.full_name: str = full_name
        self.short_name: str = short_name
        self.image_url: str | None = image_url
        self.map_id: str | None = map_id
        self.embed: Embed = self.create_embed()

    @button(label="Name", style=ButtonStyle.secondary, row=0)
    async def set_name(self, interaction: Interaction, button: Button):
        modal = MapNameModal(self)
        await interaction.response.send_modal(modal)

    @button(label="Image", style=ButtonStyle.secondary, row=0)
    async def set_image_url(self, interaction: Interaction, button: Button):
        modal = MapImageModal(self)
        await interaction.response.send_modal(modal)

    @button(label="Save", style=ButtonStyle.primary, row=1)
    async def save(self, interaction: Interaction, button: Button):
        session: sqlalchemy.orm.Session
        with Session() as session:
            map: Map | None = session.query(Map).filter(Map.id == self.map_id).first()
            if not map:
                session.add(Map(self.full_name, self.short_name, self.image_url))
            else:
                map.full_name = self.full_name
                map.short_name = self.short_name
                map.image_url = self.image_url
            try:
                session.commit()
            except:
                _log.exception(
                    f"[MapConfigureView.save] Exception caught when committing map {self.full_name} ({self.short_name})"
                )
                await interaction.response.send_message(
                    embed=Embed(
                        description="Oops! Something went wrong ☹️",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )
            else:
                if not map:
                    self.embed.set_footer(text="New Map created")
                    """
                    await interaction.response.send_message(
                        embed=Embed(
                            description=f'New Map "**{self.full_name}**" (**{self.short_name}**) created',
                            color=Colour.green(),
                        ),
                        ephemeral=True,
                    )
                    """
                else:
                    self.embed.set_footer(text="Your changes have been saved")
                    """
                    await interaction.response.send_message(
                        embed=Embed(
                            description=f'Your changes to "**{self.full_name}**" (**{self.short_name}**) have been saved',
                            color=Colour.green(),
                        ),
                        ephemeral=True,
                    )
                    """
                await interaction.response.edit_message(embed=self.embed)

    def create_embed(self) -> Embed:
        embed = Embed(
            title=(
                f'Creating New Map "{self.full_name}" ({self.short_name})'
                if not self.map_id
                else f'Editing Map "{self.full_name}" ({self.short_name})'
            ),
            color=discord.Color.dark_embed(),
        )
        embed.add_field(
            name="Name", value=self.full_name if self.full_name else "*None*"
        )
        embed.add_field(
            name="Short Name", value=self.short_name if self.short_name else "*None*"
        )
        embed.add_field(
            name="Image",
            value=f"[URL]({self.image_url})" if self.image_url else "*None*",
        )
        embed.set_image(url=self.image_url)
        return embed


class MapNameModal(Modal):
    def __init__(
        self,
        view: MapConfigureView,
    ):
        super().__init__(title="Set Name", timeout=None)
        self.view: MapConfigureView = view
        self.full_name: TextInput = TextInput(
            label="Name",
            style=TextStyle.short,
            required=True,
            placeholder='E.g. "Dangerous Crossing"',
            default=self.view.full_name,
        )
        self.short_name: TextInput = TextInput(
            label="Shorthand Name",
            style=TextStyle.short,
            required=True,
            placeholder='E.g. "DX"',
            default=self.view.short_name,
        )
        self.add_item(self.full_name)
        self.add_item(self.short_name)

    async def on_submit(self, interaction: Interaction[Client]) -> None:
        self.view.full_name = self.full_name.value
        self.view.short_name = self.short_name.value
        self.view.embed.set_footer(text="You have unsaved changes")
        for i, field in enumerate(self.view.embed.fields):
            if field.name == "Name":
                self.view.embed.set_field_at(
                    i, name=field.name, value=self.full_name.value
                )
            elif field.name == "Short Name":
                self.view.embed.set_field_at(
                    i, name=field.name, value=self.short_name.value
                )
        await interaction.response.edit_message(embed=self.view.embed)

    async def on_error(
        self,
        interaction: Interaction,
        error: Exception,
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
        await super().on_error(interaction, error)


class MapImageModal(Modal):
    def __init__(
        self,
        view: MapConfigureView,
    ):
        super().__init__(title="Set Image URL", timeout=None)
        self.view: MapConfigureView = view
        self.image_url: TextInput = TextInput(
            label="Image URL",
            style=TextStyle.long,
            required=True,
            placeholder="Enter the URL of the image here...",
            default=self.view.image_url,
        )
        self.add_item(self.image_url)

    async def on_submit(self, interaction: Interaction[Client]) -> None:
        try:
            self.view.embed.set_image(url=self.image_url.value)
        except:
            _log.exception(
                f"[MapImageModal.on_submit] Error attaching image with url {self.image_url.value} to embed"
            )
            await interaction.response.send_message(
                embed=Embed(
                    description="Oops! That image URL isn't valid, please try a different one ☹️",
                    colour=Colour.red(),
                ),
                ephemeral=True,
            )
        else:
            self.view.embed.set_footer(text="You have unsaved changes!")
            self.view.image_url = self.image_url.value
            for i, field in enumerate(self.view.embed.fields):
                if field.name == "Image":
                    self.view.embed.set_field_at(
                        i, name=field.name, value=f"[URL]({self.image_url.value})"
                    )
            await interaction.response.edit_message(embed=self.view.embed)

    async def on_error(
        self,
        interaction: Interaction,
        error: Exception,
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
        await super().on_error(interaction, error)
