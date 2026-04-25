"""
DraftCommands cog. State machine for captain pick drafts:

- handle_first_pick_choice: captain B clicks Pick first/second; transition
  to DraftPickView for the chosen first picker.
- handle_pick: a captain selects a player from the dropdown; persist to
  DraftPick + InProgressGamePlayer.team, advance the view (or finalize).
- handle_pick_timeout: 2-minute pick timer expires; auto-pick a random
  remaining player.

cog_load re-attaches views for every drafting game on bot restart.
"""

from __future__ import annotations

import logging
import random

from discord import Colour, Embed, Interaction, Message, TextChannel
from discord.ext import commands
from discord.ext.commands import Bot
from sqlalchemy.orm.session import Session as SQLAlchemySession

from discord_bots.bot import bot
from discord_bots.captain_pick import (
    create_draft_embed,
    finalize_draft,
    get_current_picker,
    get_first_picker_team,
    picker_for_pick,
)
from discord_bots.models import (
    DraftPick,
    InProgressGame,
    InProgressGamePlayer,
    Player,
    Session,
)
from discord_bots.views.draft import DraftPickView, FirstPickChoiceView

_log = logging.getLogger(__name__)


class DraftCommands(commands.Cog):
    def __init__(self, bot: Bot):
        self.bot: Bot = bot

    async def cog_load(self) -> None:
        """
        Re-attach views for any in-progress drafts after a bot restart.

        - 0 picks made → re-post FirstPickChoiceView (captain B re-decides).
        - 1+ picks made → re-post DraftPickView for the current picker
          (snake formula). Pick timer resets to 2 minutes; per design
          this is acceptable.
        """
        session: SQLAlchemySession
        with Session() as session:
            drafting_games: list[InProgressGame] = (
                session.query(InProgressGame)
                .filter(InProgressGame.is_drafting == True)
                .all()
            )
            for game in drafting_games:
                if not game.message_id or not game.channel_id:
                    continue
                channel = self.bot.get_channel(game.channel_id)
                if not isinstance(channel, TextChannel):
                    continue
                try:
                    message = await channel.fetch_message(game.message_id)
                except Exception as e:
                    _log.warning(
                        f"[DraftCommands.cog_load] could not fetch "
                        f"message {game.message_id}: {e}"
                    )
                    continue

                first_picker_team = get_first_picker_team(session, game.id)
                if first_picker_team is None:
                    # No picks yet — re-prompt for first/second pick choice.
                    captain_b = (
                        session.query(InProgressGamePlayer)
                        .filter(
                            InProgressGamePlayer.in_progress_game_id == game.id,
                            InProgressGamePlayer.is_captain == True,
                            InProgressGamePlayer.team == 1,
                        )
                        .first()
                    )
                    if not captain_b:
                        continue
                    view = FirstPickChoiceView(game.id, captain_b.player_id, self)
                    self.bot.add_view(view, message_id=message.id)
                else:
                    # Picks in progress — repost a fresh DraftPickView.
                    await self._post_pick_view(session, game, message)

    async def _post_pick_view(
        self,
        session: SQLAlchemySession,
        game: InProgressGame,
        message: Message,
        first_picker_team_override: int | None = None,
    ) -> None:
        """Edit `message` to show a fresh DraftPickView for the current picker.

        first_picker_team_override is needed when this is called immediately
        after the FirstPickChoiceView click — at that moment no DraftPick rows
        exist yet, so the choice has to be passed in explicitly.
        """
        first_picker_team = first_picker_team_override
        if first_picker_team is None:
            first_picker_team = get_first_picker_team(session, game.id)
        if first_picker_team is None:
            return
        current_picker = get_current_picker(session, game.id, first_picker_team)
        if current_picker is None:
            # Draft is complete — finalize.
            await finalize_draft(game.id)
            return

        pool = (
            session.query(InProgressGamePlayer)
            .filter(
                InProgressGamePlayer.in_progress_game_id == game.id,
                InProgressGamePlayer.team == None,
            )
            .all()
        )
        pool_player_ids = [igp.player_id for igp in pool]
        pool_players = {
            p.id: p
            for p in session.query(Player).filter(Player.id.in_(pool_player_ids)).all()
        }
        remaining = [
            (igp.player_id, pool_players[igp.player_id].name)
            for igp in pool
            if igp.player_id in pool_players
        ]

        pick_count = (
            session.query(DraftPick)
            .filter(DraftPick.in_progress_game_id == game.id)
            .count()
        )
        view = DraftPickView(
            game_id=game.id,
            current_picker_id=current_picker.player_id,
            pick_number=pick_count + 1,
            remaining_players=remaining,
            cog=self,
        )
        embed = create_draft_embed(session, game)
        await message.edit(embed=embed, view=view)

    async def handle_first_pick_choice(
        self,
        interaction: Interaction,
        game_id: str,
        captain_b_picks_first: bool,
    ) -> None:
        """Captain B chose first/second. Transition to the pick view."""
        await interaction.response.defer()
        session: SQLAlchemySession
        with Session() as session:
            game = (
                session.query(InProgressGame)
                .filter(InProgressGame.id == game_id)
                .first()
            )
            if not game or not game.is_drafting:
                return
            # Captain B is team 1, captain A is team 0.
            # Captain B picks first => first_picker_team = 1
            # Captain B picks second => first_picker_team = 0 (i.e. captain A)
            first_picker_team = 1 if captain_b_picks_first else 0
            captain_a_or_b = (
                session.query(InProgressGamePlayer)
                .filter(
                    InProgressGamePlayer.in_progress_game_id == game.id,
                    InProgressGamePlayer.is_captain == True,
                    InProgressGamePlayer.team == first_picker_team,
                )
                .first()
            )
            if not captain_a_or_b or not interaction.message:
                return

            # The choice will be encoded into the first DraftPick row when
            # the captain selects a player. Until then, pass first_picker_team
            # explicitly so _post_pick_view can render the right captain's view.
            await self._post_pick_view(
                session,
                game,
                interaction.message,
                first_picker_team_override=first_picker_team,
            )

    async def handle_pick(
        self,
        interaction: Interaction,
        game_id: str,
        picked_player_id: int,
        expected_pick_number: int,
    ) -> None:
        """A captain selected a player. Persist + advance the view."""
        await interaction.response.defer()
        session: SQLAlchemySession
        with Session() as session:
            game = (
                session.query(InProgressGame)
                .filter(InProgressGame.id == game_id)
                .first()
            )
            if not game or not game.is_drafting:
                return

            pick_count = (
                session.query(DraftPick)
                .filter(DraftPick.in_progress_game_id == game.id)
                .count()
            )
            if pick_count + 1 != expected_pick_number:
                # Stale view; refresh and bail.
                if interaction.message:
                    await self._post_pick_view(session, game, interaction.message)
                return

            # Determine first_picker_team: if this is pick 1, the picker is the
            # team whose captain matches interaction.user.id; otherwise it's
            # already encoded in the first DraftPick row.
            first_picker_team = get_first_picker_team(session, game.id)
            if first_picker_team is None:
                first_picker_igp = (
                    session.query(InProgressGamePlayer)
                    .filter(
                        InProgressGamePlayer.in_progress_game_id == game.id,
                        InProgressGamePlayer.player_id == interaction.user.id,
                        InProgressGamePlayer.is_captain == True,
                    )
                    .first()
                )
                if not first_picker_igp:
                    return
                first_picker_team = first_picker_igp.team

            # Verify it's actually this captain's turn (snake formula).
            expected_team = picker_for_pick(expected_pick_number, first_picker_team)
            picker_igp = (
                session.query(InProgressGamePlayer)
                .filter(
                    InProgressGamePlayer.in_progress_game_id == game.id,
                    InProgressGamePlayer.is_captain == True,
                    InProgressGamePlayer.team == expected_team,
                )
                .first()
            )
            if not picker_igp or picker_igp.player_id != interaction.user.id:
                return

            await self._record_pick(
                session,
                game,
                captain_player_id=interaction.user.id,
                picked_player_id=picked_player_id,
                pick_number=expected_pick_number,
                pick_team=expected_team,
            )

            # Advance: either finalize, or post the next pick view.
            session.commit()
            if interaction.message:
                # Refresh from DB after commit.
                game = (
                    session.query(InProgressGame)
                    .filter(InProgressGame.id == game_id)
                    .first()
                )
                if game:
                    await self._advance_or_finalize(session, game, interaction.message)

    async def handle_pick_timeout(
        self, game_id: str, expected_pick_number: int
    ) -> None:
        """2-minute timer expired: auto-pick a random remaining player."""
        session: SQLAlchemySession
        with Session() as session:
            game = (
                session.query(InProgressGame)
                .filter(InProgressGame.id == game_id)
                .first()
            )
            if not game or not game.is_drafting:
                return

            pick_count = (
                session.query(DraftPick)
                .filter(DraftPick.in_progress_game_id == game.id)
                .count()
            )
            if pick_count + 1 != expected_pick_number:
                # Pick already happened (race condition); nothing to do.
                return

            first_picker_team = get_first_picker_team(session, game.id)
            if first_picker_team is None:
                # Shouldn't happen — pick view shouldn't be active without
                # a first-pick choice.
                return

            current_picker = get_current_picker(session, game.id, first_picker_team)
            if current_picker is None:
                return

            pool = (
                session.query(InProgressGamePlayer)
                .filter(
                    InProgressGamePlayer.in_progress_game_id == game.id,
                    InProgressGamePlayer.team == None,
                )
                .all()
            )
            if not pool:
                return
            chosen = random.choice(pool)

            await self._record_pick(
                session,
                game,
                captain_player_id=current_picker.player_id,
                picked_player_id=chosen.player_id,
                pick_number=expected_pick_number,
                pick_team=current_picker.team,
            )
            session.commit()

            game = (
                session.query(InProgressGame)
                .filter(InProgressGame.id == game_id)
                .first()
            )
            if not game or not game.channel_id or not game.message_id:
                return
            channel = self.bot.get_channel(game.channel_id)
            if not isinstance(channel, TextChannel):
                return
            try:
                message = await channel.fetch_message(game.message_id)
            except Exception:
                return
            await channel.send(
                embed=Embed(
                    description=(
                        f"⏰ <@{current_picker.player_id}>'s pick timer "
                        f"expired — auto-picked <@{chosen.player_id}>."
                    ),
                    colour=Colour.yellow(),
                )
            )
            await self._advance_or_finalize(session, game, message)

    async def _record_pick(
        self,
        session: SQLAlchemySession,
        game: InProgressGame,
        captain_player_id: int,
        picked_player_id: int,
        pick_number: int,
        pick_team: int,
    ) -> None:
        """Persist a DraftPick row and assign the picked player to the team."""
        session.add(
            DraftPick(
                in_progress_game_id=game.id,
                pick_number=pick_number,
                captain_player_id=captain_player_id,
                picked_player_id=picked_player_id,
            )
        )
        picked_igp = (
            session.query(InProgressGamePlayer)
            .filter(
                InProgressGamePlayer.in_progress_game_id == game.id,
                InProgressGamePlayer.player_id == picked_player_id,
            )
            .first()
        )
        if picked_igp:
            picked_igp.team = pick_team

    async def _advance_or_finalize(
        self,
        session: SQLAlchemySession,
        game: InProgressGame,
        message: Message,
    ) -> None:
        """If draft is complete, finalize. Otherwise post the next pick view."""
        remaining = (
            session.query(InProgressGamePlayer)
            .filter(
                InProgressGamePlayer.in_progress_game_id == game.id,
                InProgressGamePlayer.team == None,
            )
            .count()
        )
        if remaining == 0:
            await message.edit(embed=create_draft_embed(session, game), view=None)
            await finalize_draft(game.id)
        else:
            await self._post_pick_view(session, game, message)
