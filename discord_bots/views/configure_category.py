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

from discord_bots.models import Category
from discord_bots.views.base import BaseView
from discord_bots.views.confirmation import ConfirmationView

_log = logging.getLogger(__name__)


class CategoryConfigureView(BaseView):
    def __init__(self, interaction: Interaction, category: Category):
        super().__init__(timeout=300)
        self.value: bool = False
        self.category: Category = category
        self.interaction: Interaction = interaction
        self.embed: Embed
        self.add_item(CategoryRatedSelect(self))

    @button(label="Set Name", style=ButtonStyle.primary, row=0)
    async def setname(self, interaction: Interaction, button: Button):
        modal = CategoryNameModal(self)
        await interaction.response.send_modal(modal)
        return True

    @button(label="Leaderboard Settings", style=ButtonStyle.primary, row=0)
    async def leaderboard(self, interaction: Interaction, button: Button):
        modal = CategoryLeaderboardModal(self)
        await interaction.response.send_modal(modal)
        return True

    @button(label="Decay Settings", style=ButtonStyle.primary, row=0)
    async def decay(self, interaction: Interaction, button: Button):
        modal = CategoryDecayModal(self)
        await interaction.response.send_modal(modal)
        return True

    @button(label="Save", style=ButtonStyle.success, row=4)
    async def save(self, interaction: Interaction, button: Button):
        await interaction.response.defer()
        confirmation_buttons = ConfirmationView(interaction.user.id)
        confirmation_buttons.message = await interaction.followup.send(
            embed=Embed(
                description=f"⚠️ Are you sure you want to save configurations for category **{self.category.name}**?⚠️",
                colour=Colour.yellow(),
            ),
            view=confirmation_buttons,
            ephemeral=True,
        )
        await confirmation_buttons.wait()
        if not confirmation_buttons.value:
            return False
        else:
            self.value = True
            self.stop()
            return True

    @button(label="Cancel", style=ButtonStyle.danger, row=4)
    async def cancel(self, interaction: Interaction, button: Button):
        self.stop()
        return False


class CategoryDecayModal(Modal):
    def __init__(
        self,
        view: CategoryConfigureView,
    ):
        super().__init__(title="Decay Settings", timeout=30)
        self.view: CategoryConfigureView = view
        self.sigma_decay_amount: TextInput = TextInput(
            label="Sigma decay amount",
            style=TextStyle.short,
            required=False,
            placeholder=str(self.view.category.sigma_decay_amount),
        )
        self.sigma_decay_grace_days: TextInput = TextInput(
            label="Sigma decay grace days",
            style=TextStyle.short,
            required=False,
            placeholder=str(self.view.category.sigma_decay_grace_days),
        )
        self.sigma_decay_max_decay_proportion: TextInput = TextInput(
            label="Sigma decay max decay proportion",
            style=TextStyle.short,
            required=False,
            placeholder=str(self.view.category.sigma_decay_max_decay_proportion),
        )
        self.add_item(self.sigma_decay_amount)
        self.add_item(self.sigma_decay_grace_days)
        self.add_item(self.sigma_decay_max_decay_proportion)

    async def on_submit(self, interaction: Interaction[Client]) -> None:
        self.decay_amount: float | None = None
        self.decay_grace_days: int | None = None
        self.decay_max_decay_proportion: float | None = None

        try:
            if not str(self.sigma_decay_amount) == "":
                self.decay_amount = float(str(self.sigma_decay_amount))
            if not str(self.sigma_decay_max_decay_proportion) == "":
                self.decay_max_decay_proportion = float(
                    str(self.sigma_decay_max_decay_proportion)
                )
        except Exception:
            await interaction.response.send_message(
                embed=Embed(
                    description="Decay amount & proportion must be a decimal number",
                    colour=Colour.red(),
                ),
                ephemeral=True,
            )
            return

        try:
            if not str(self.sigma_decay_grace_days) == "":
                self.decay_grace_days = int(str(self.sigma_decay_grace_days))
        except Exception:
            await interaction.response.send_message(
                embed=Embed(
                    description="Decay grace days must be an integer",
                    colour=Colour.red(),
                ),
                ephemeral=True,
            )
            return

        # Check for zero's
        if (
            (self.decay_amount and self.decay_amount < 0)
            or (self.decay_grace_days and self.decay_grace_days < 0)
            or (self.decay_max_decay_proportion and self.decay_max_decay_proportion < 0)
        ):
            await interaction.response.send_message(
                embed=Embed(
                    description="Value must be greater than or equal to 0",
                    colour=Colour.red(),
                ),
                ephemeral=True,
            )
            return

        if self.decay_amount:
            self.view.category.sigma_decay_amount = self.decay_amount
            if self.view.embed.description:
                self.view.embed.description = (
                    self.view.embed.description
                    + f"\nSigma decay amount: **{self.view.category.sigma_decay_amount}**"
                )

        if self.decay_grace_days:
            self.view.category.sigma_decay_grace_days = self.decay_grace_days
            if self.view.embed.description:
                self.view.embed.description = (
                    self.view.embed.description
                    + f"\nSigma decay grace days: **{self.view.category.sigma_decay_grace_days}**"
                )

        if self.decay_max_decay_proportion:
            self.view.category.sigma_decay_max_decay_proportion = (
                self.decay_max_decay_proportion
            )
            if self.view.embed.description:
                self.view.embed.description = (
                    self.view.embed.description
                    + f"\nSigma max decay proportion: **{self.view.category.sigma_decay_max_decay_proportion}**"
                )

        await self.view.interaction.edit_original_response(embed=self.view.embed)

        # Interaction must be responded to, but is then deleted
        await interaction.response.send_message(
            embed=Embed(
                description=f"Set decay settings successful",
                colour=Colour.blue(),
            ),
            ephemeral=True,
        )
        await interaction.delete_original_response()
        return


class CategoryLeaderboardModal(Modal):
    def __init__(
        self,
        view: CategoryConfigureView,
    ):
        super().__init__(title="Leaderboard Settings", timeout=30)
        self.view: CategoryConfigureView = view
        self.input: TextInput = TextInput(
            label="Min games for leaderboard",
            style=TextStyle.short,
            required=True,
            placeholder=str(self.view.category.min_games_for_leaderboard),
        )
        self.add_item(self.input)

    async def on_submit(self, interaction: Interaction[Client]) -> None:
        try:
            self.value = int(str(self.input))
        except Exception as e:
            await interaction.response.send_message(
                embed=Embed(
                    description="Value must be an integer",
                    colour=Colour.red(),
                ),
                ephemeral=True,
            )
            return

        if self.value < 0:
            await interaction.response.send_message(
                embed=Embed(
                    description="Value must be greater than or equal to 0",
                    colour=Colour.red(),
                ),
                ephemeral=True,
            )
            return

        self.view.category.min_games_for_leaderboard = self.value
        if self.view.embed.description:
            self.view.embed.description = (
                self.view.embed.description
                + f"\nMin games for leaderboard: **{self.view.category.min_games_for_leaderboard}**"
            )
        await self.view.interaction.edit_original_response(embed=self.view.embed)

        # Interaction must be responded to, but is then deleted
        await interaction.response.send_message(
            embed=Embed(
                description=f"Set min games for leaderboard **{self.view.category.min_games_for_leaderboard}** successful",
                colour=Colour.blue(),
            ),
            ephemeral=True,
        )
        await interaction.delete_original_response()
        return


class CategoryNameModal(Modal):
    def __init__(
        self,
        view: CategoryConfigureView,
    ):
        super().__init__(title="Set Name", timeout=30)
        self.view: CategoryConfigureView = view
        self.input: TextInput = TextInput(
            label="Category Name",
            style=TextStyle.short,
            required=True,
            placeholder=self.view.category.name,
        )
        self.add_item(self.input)

    async def on_submit(self, interaction: Interaction[Client]) -> None:
        self.view.category.name = self.input.value
        if self.view.embed.description:
            self.view.embed.description = (
                self.view.embed.description
                + f"\nCategory name: **{self.view.category.name}**"
            )
        await self.view.interaction.edit_original_response(embed=self.view.embed)

        # Interaction must be responded to, but is then deleted
        await interaction.response.send_message(
            embed=Embed(
                description=f"Set name **{self.view.category.name}** successful",
                colour=Colour.blue(),
            ),
            ephemeral=True,
        )
        await interaction.delete_original_response()
        return


class CategoryRatedSelect(Select):
    def __init__(self, view: CategoryConfigureView):
        super().__init__(
            placeholder=f"Is_Rated: {str(view.category.is_rated)}",
            row=1,
            options=[
                SelectOption(label="True", value="True"),
                SelectOption(label="False", value="False"),
            ],
        )
        self.view: CategoryConfigureView

    async def callback(self, interaction: Interaction[Client]):
        self.view.category.is_rated = self.values[0] == "True"
        if self.view.embed.description:
            self.view.embed.description = (
                self.view.embed.description
                + f"\nIs_Rated set: **{self.view.category.is_rated}**"
            )
        await self.view.interaction.edit_original_response(embed=self.view.embed)

        await interaction.response.send_message(
            embed=Embed(
                description=f"Set is_rated **{self.view.category.is_rated}** successful",
                colour=Colour.blue(),
            ),
            ephemeral=True,
        )
        await interaction.delete_original_response()
        return
