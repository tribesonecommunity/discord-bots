"""
Discord UI views for the captain pick draft flow.

Two views:
- FirstPickChoiceView — captain B's "Pick first / Pick second" buttons.
  Persistent (timeout=None) so it survives bot restarts via add_view.
- DraftPickView — Select dropdown of remaining players, gated to the current
  picker. Non-persistent with a 2-minute timeout; on timeout, the cog auto-
  picks a random remaining player. On bot restart the cog re-posts a fresh
  view with a new timer (acceptable per design).

Both views delegate state mutations to DraftCommands.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from discord import ButtonStyle, Colour, Embed, Interaction, SelectOption
from discord.ui import Button, Select, button

from discord_bots.views.base import BaseView

if TYPE_CHECKING:
    from discord_bots.cogs.draft import DraftCommands

_log = logging.getLogger(__name__)


class FirstPickChoiceView(BaseView):
    """Captain B chooses whether to pick first or second."""

    def __init__(self, game_id: str, captain_b_id: int, cog: "DraftCommands | None"):
        super().__init__(timeout=None)
        self.game_id = game_id
        self.captain_b_id = captain_b_id
        self.cog = cog

    async def interaction_check(self, interaction: Interaction) -> bool:
        if interaction.user.id != self.captain_b_id:
            await interaction.response.send_message(
                embed=Embed(
                    description="Only the lower-rated captain can choose pick order.",
                    colour=Colour.red(),
                ),
                ephemeral=True,
            )
            return False
        return True

    @button(
        label="Pick first",
        style=ButtonStyle.primary,
        custom_id="captain_pick_first_pick_choice:first",
        emoji="1️⃣",
    )
    async def first_button(self, interaction: Interaction, button: Button):
        if self.cog is None:
            self.cog = interaction.client.get_cog("DraftCommands")
        if self.cog is None:
            return
        await self.cog.handle_first_pick_choice(
            interaction, self.game_id, captain_b_picks_first=True
        )
        self.stop()

    @button(
        label="Pick second",
        style=ButtonStyle.secondary,
        custom_id="captain_pick_first_pick_choice:second",
        emoji="2️⃣",
    )
    async def second_button(self, interaction: Interaction, button: Button):
        if self.cog is None:
            self.cog = interaction.client.get_cog("DraftCommands")
        if self.cog is None:
            return
        await self.cog.handle_first_pick_choice(
            interaction, self.game_id, captain_b_picks_first=False
        )
        self.stop()


class DraftPickView(BaseView):
    """Current picker selects a player from a dropdown of the remaining pool."""

    # 2 minute timeout per pick. On timeout, the cog auto-picks a random
    # remaining player. See cogs/draft.py:on_pick_timeout.
    PICK_TIMEOUT_SECONDS = 120

    def __init__(
        self,
        game_id: str,
        current_picker_id: int,
        pick_number: int,
        remaining_players: list[tuple[int, str]],
        cog: "DraftCommands",
    ):
        super().__init__(timeout=self.PICK_TIMEOUT_SECONDS)
        self.game_id = game_id
        self.current_picker_id = current_picker_id
        self.pick_number = pick_number
        self.cog = cog

        options = [
            SelectOption(label=name[:100], value=str(player_id))
            for player_id, name in remaining_players[:25]
        ]
        select = Select(
            placeholder=f"Pick {pick_number}: choose a player",
            options=options,
            min_values=1,
            max_values=1,
        )

        async def select_callback(interaction: Interaction):
            picked_player_id = int(select.values[0])
            await self.cog.handle_pick(
                interaction,
                self.game_id,
                picked_player_id=picked_player_id,
                expected_pick_number=self.pick_number,
            )
            self.stop()

        select.callback = select_callback
        self.add_item(select)

    async def interaction_check(self, interaction: Interaction) -> bool:
        if interaction.user.id != self.current_picker_id:
            await interaction.response.send_message(
                embed=Embed(
                    description="It's not your turn to pick.",
                    colour=Colour.red(),
                ),
                ephemeral=True,
            )
            return False
        return True

    async def on_timeout(self) -> None:
        await self.cog.handle_pick_timeout(self.game_id, self.pick_number)
