import discord

import discord_bots.utils


class InProgressGameView(discord.ui.View):
    def __init__(self, game_id: str):
        super().__init__(timeout=None)
        self.game_id: str = ""
        self.is_game_finished: bool = False
        self.cancel_votes: int = 0

    async def interaction_check(self, interaction: discord.Interaction[discord.Client]):
        return not self.is_game_finished

    @discord.ui.button(
        label="Win", style=discord.ButtonStyle.green, custom_id="persistent_view:win"
    )
    async def win_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        self.is_game_finished = await discord_bots.utils.finish_in_progress_game(
            interaction, "win"
        )
        if self.is_game_finished:
            await self.disable_buttons(interaction)
            self.stop()

    @discord.ui.button(
        label="Loss", style=discord.ButtonStyle.red, custom_id="persistent_view:loss"
    )
    async def loss_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        self.is_game_finished = await discord_bots.utils.finish_in_progress_game(
            interaction, "loss"
        )
        if self.is_game_finished:
            await self.disable_buttons(interaction)
            self.stop()

    @discord.ui.button(
        label="Tie", style=discord.ButtonStyle.blurple, custom_id="persistent_view:tie"
    )
    async def tie_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        self.is_game_finished = await discord_bots.utils.finish_in_progress_game(
            interaction, "tie"
        )
        if self.is_game_finished:
            await self.disable_buttons(interaction)
            self.stop()

    async def disable_buttons(self, interaction: discord.Interaction):
        for child in self.children:
            if type(child) == discord.ui.Button:
                child.disabled = True
                await interaction.edit_original_response(
                    embed=interaction.message.embeds[0], view=self
                )