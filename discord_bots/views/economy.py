from discord import app_commands, Interaction, Embed, Colour, ButtonStyle, ActionRow
from discord.ui import View, Button, button
from discord_bots.config import PREDICTION_TIMEOUT
from discord_bots.models import Session, InProgressGame, InProgressGamePlayer


class EconomyPredictionView(View):
    def __init__(
            self,
            # game_id: str
            #  in_progress_game: InProgressGame
        ):
        super().__init__(timeout=None)
        self.value = None
        # self.is_persistent = True
        # self.in_progress_game: InProgressGame = in_progress_game

    # async def interaction_check(self, interaction: Interaction) -> bool:
    #     session = Session()
    #     game: InProgressGame = (
    #         session.query(InProgressGame)
    #         .filter(InProgressGame.id == self.in_progress_game.id)
    #         .first()
    #     )
    #     igp_players: list[InProgressGamePlayer] = (
    #         session.query(InProgressGamePlayer)
    #         .filter(InProgressGamePlayer.in_progress_game_id == game.id)
    #         .all()
    #     )
    #     team0_players = igp_players[: len(igp_players) // 2]
    #     team1_players = igp_players[len(igp_players) // 2 :]

    #     if button.custom_id == "0":
    #         if interaction.user.id in map(
    #             filter(lambda x: x == 0, team0_players.player_id)
    #         ):
    #             interaction.response.send_message(
    #                 "You cannot predict against yourself", ephemeral=True
    #             )
    #             return False
    #         else:
    #             return True
    #     elif button.custom_id == "1":
    #         if interaction.user.id in map(
    #             filter(lambda x: x == 0, team1_players.player_id)
    #         ):
    #             interaction.response.send_message(
    #                 "You cannot predict against yourself", ephemeral=True
    #             )
    #             return False
    #         else:
    #             return True
    #     else:
    #         return False

    @button(label="Team 0", style=ButtonStyle.green, custom_id="0")
    async def Team0(self, interaction: Interaction, button: Button):
        await interaction.response.send_message("Predicted team 0", ephemeral=False)
        self.value = True
        # self.stop()

    @button(label="Team 1", style=ButtonStyle.green, custom_id="1")
    async def Team1(self, interaction: Interaction, button: Button):
        await interaction.message.edit(view=self)
        await interaction.response.send_message("Predicted team 1", ephemeral=False)
        self.value = True
        # self.stop()

    # button_row = ActionRow(Team0, Team1)
