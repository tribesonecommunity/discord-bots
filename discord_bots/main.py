import asyncio
import logging
import time
from datetime import datetime, timezone

import discord
from discord import Colour, Embed, Interaction, Member, Message, Reaction
from discord.abc import User
from discord.app_commands import AppCommandError, errors
from discord.ext.commands import CommandError, CommandNotFound, Context, UserInputError
from trueskill import setup as trueskill_setup

import discord_bots.config as config
from discord_bots.async_db_utils import (
    async_delete_by_id,
    async_query_first,
    async_session,
)
from discord_bots.cogs.admin import AdminCommands
from discord_bots.cogs.category import CategoryCommands
from discord_bots.cogs.common import CommonCommands
from discord_bots.cogs.config import ConfigCommands
from discord_bots.cogs.economy import EconomyCommands
from discord_bots.cogs.in_progress_game import InProgressGameCommands
from discord_bots.cogs.list import ListCommands
from discord_bots.cogs.map import MapCommands
from discord_bots.cogs.notification import NotificationCommands
from discord_bots.cogs.player import PlayerCommands
from discord_bots.cogs.position import PositionCommands
from discord_bots.cogs.queue import QueueCommands
from discord_bots.cogs.queue_position import QueuePositionCommands
from discord_bots.cogs.raffle import RaffleCommands
from discord_bots.cogs.random import RandomCommands
from discord_bots.cogs.rotation import RotationCommands
from discord_bots.cogs.schedule import ScheduleCommands, ScheduleUtils
from discord_bots.cogs.trueskill import TrueskillCommands
from discord_bots.cogs.vote import VoteCommands
from discord_bots.utils import utc_now_naive

from .bot import bot
from .models import (
    AsyncSessionLocal,
    Config,
    CustomCommand,
    Player,
    QueuePlayer,
    QueueWaitlistPlayer,
    Session,
)
from .tasks import (
    add_player_task,
    afk_timer_task,
    leaderboard_task,
    map_rotation_task,
    prediction_task,
    queue_waitlist_task,
    schedule_task,
    sigma_decay_task,
    vote_passed_waitlist_task,
)

_log = logging.getLogger(__name__)


async def create_seed_admins():
    async with async_session() as session:
        for seed_admin_id in config.SEED_ADMIN_IDS:
            player = await async_query_first(
                session, Player, Player.id == seed_admin_id
            )
            if player:
                player.is_admin = True
            else:
                session.add(
                    Player(
                        id=seed_admin_id,
                        is_admin=True,
                        name="AUTO_GENERATED_ADMIN",
                        last_activity_at=utc_now_naive(),
                        currency=config.STARTING_CURRENCY,
                    )
                )
        await session.commit()


@bot.event
async def on_ready():
    """
    https://discordpy.readthedocs.io/en/stable/api.html#discord.on_ready
    This function is not guaranteed to be the first event called. Likewise, this function is not guaranteed to only be called once.
    Do not setup anything in here
    """
    _log.info(f"Logged in as {bot.user} (ID: {bot.user.id})")


@bot.tree.error
async def on_app_command_error(
    interaction: Interaction, error: AppCommandError
) -> None:
    # TODO: provide more context about the error to the user
    if isinstance(error, errors.CheckFailure):
        return
    else:
        if interaction.command:
            _log.exception(
                f"[on_app_command_error]: {error}, command: {interaction.command.name}"
            )
        else:
            _log.exception(f"[on_app_command_error]: {error}")

    if interaction.response.is_done():
        await interaction.followup.send(
            embed=Embed(description="Oops! Something went wrong ☹️", color=Colour.red())
        )
    else:
        # fallback case that responds to the interaction, since there always needs to be a response
        await interaction.response.send_message(
            embed=Embed(
                description="Oops! Something went wrong ☹️", color=Colour.red()
            ),
            ephemeral=True,
        )


@bot.event
async def on_command_error(ctx: Context, error: CommandError):
    if isinstance(error, UserInputError):
        if ctx.command.usage:
            await ctx.channel.send(
                embed=Embed(
                    description=f"Usage: {config.COMMAND_PREFIX}{ctx.command.name} {ctx.command.usage}",
                    colour=Colour.red(),
                )
            )
        else:
            await ctx.channel.send(
                embed=Embed(
                    description=f"Usage: {config.COMMAND_PREFIX}{ctx.command.name} {ctx.command.signature}",
                    colour=Colour.red(),
                )
            )
    elif isinstance(error, CommandNotFound):
        # Handle custom commands when built-in command is not found
        command_name = ctx.message.content.split(" ")[0][1:]  # Remove prefix
        try:
            async with async_session() as session:
                custom_command: CustomCommand | None = await async_query_first(
                    session, CustomCommand, CustomCommand.name == command_name
                )
                if custom_command:
                    await ctx.channel.send(content=custom_command.output)
        except Exception as e:
            _log.error(
                f"[on_command_error]: Error checking custom command '{command_name}': {e}"
            )
    else:
        if ctx.command:
            _log.warning(
                f"[on_command_error]: Ignoring exception in command {ctx.command.name}: {error}"
            )
        else:
            _log.warning(f"[on_command_error]: Ignoring exception: {error}")


@bot.event
async def on_message(message: Message):
    # only respond to users
    if message.author.bot:
        return
    # Use this to get the channel id
    if config.ENABLE_DEBUG:
        if (
            message.content.startswith(config.COMMAND_PREFIX)
            and "configurebot" in message.content
        ):
            guild = message.guild
            content = []
            content.append(f"Your id: {message.author.id}")
            content.append(f"Channel id: {message.channel.id}, {message.channel.type}")
            content.append(f"Guild id: {message.guild.id}, {message.guild.name}")
            content.append(f"{[(c.id, c.name) for c in guild.categories]}")

            await message.channel.send(content="\n".join(content))
            return

    if (config.CHANNEL_ID and message.channel.id == config.CHANNEL_ID) or (
        config.LEADERBOARD_CHANNEL and message.channel.id == config.LEADERBOARD_CHANNEL
    ):
        async with async_session() as session:
            player: Player | None = await async_query_first(
                session, Player, Player.id == message.author.id
            )
            if player:
                player.last_activity_at = utc_now_naive()
                if player.name != message.author.display_name:
                    player.name = message.author.display_name
            else:
                session.add(
                    Player(
                        id=message.author.id,
                        name=message.author.display_name,
                        last_activity_at=utc_now_naive(),
                        currency=config.STARTING_CURRENCY,
                    )
                )
            await session.commit()
        try:
            await bot.process_commands(message)
        except Exception as e:
            _log.error(f"[on_message] Error processing command: {e}")

        # Custom commands are now handled in on_command_error() for CommandNotFound


@bot.event
async def on_reaction_add(reaction: Reaction, user: User | Member):
    async with async_session() as session:
        player: Player | None = await async_query_first(
            session, Player, Player.id == user.id
        )
        if player:
            player.last_activity_at = utc_now_naive()
            player.name = user.display_name
            await session.commit()
        else:
            session.add(
                Player(
                    id=user.id,
                    name=user.display_name,
                    last_activity_at=utc_now_naive(),
                    currency=config.STARTING_CURRENCY,
                )
            )
            await session.commit()

@bot.event
async def on_member_join(member: Member):
    async with async_session() as session:
        player = await async_query_first(session, Player, Player.id == member.id)
        if player:
            player.name = member.name
            await session.commit()
        else:
            session.add(
                Player(
                    id=member.id,
                    name=member.display_name,
                    currency=config.STARTING_CURRENCY,
                )
            )
            await session.commit()


@bot.event
async def on_member_remove(member: Member):
    async with async_session() as session:
        await async_delete_by_id(session, QueuePlayer, member.id)
        await async_delete_by_id(session, QueueWaitlistPlayer, member.id)
        await session.commit()


@bot.before_invoke
async def before_invoke(context: Context):
    session = Session()
    context.session = session
    if AsyncSessionLocal:
        context.asyncSession = AsyncSessionLocal()


@bot.after_invoke
async def after_invoke(context: Context):
    context.session.close()
    if context.asyncSession:
        await context.asyncSession.close()


async def init_config():
    async with async_session() as session:
        config = await async_query_first(session, Config)
        if config:
            return

        config = Config()
        session.add(config)
        await session.commit()


async def setup():
    await bot.add_cog(AdminCommands(bot))
    await bot.add_cog(CategoryCommands(bot))
    await bot.add_cog(CommonCommands(bot))
    await bot.add_cog(EconomyCommands(bot))
    await bot.add_cog(InProgressGameCommands(bot))
    await bot.add_cog(ListCommands(bot))
    await bot.add_cog(MapCommands(bot))
    await bot.add_cog(PlayerCommands(bot))
    await bot.add_cog(PositionCommands(bot))
    await bot.add_cog(QueueCommands(bot))
    await bot.add_cog(QueuePositionCommands(bot))
    await bot.add_cog(RaffleCommands(bot))
    await bot.add_cog(RandomCommands(bot))
    await bot.add_cog(RotationCommands(bot))
    await bot.add_cog(ScheduleCommands(bot))
    await bot.add_cog(TrueskillCommands(bot))
    await bot.add_cog(VoteCommands(bot))
    await bot.add_cog(NotificationCommands(bot))
    await bot.add_cog(ConfigCommands(bot))
    add_player_task.start()
    afk_timer_task.start()
    leaderboard_task.start()
    map_rotation_task.start()
    queue_waitlist_task.start()
    if ScheduleUtils.is_active():
        schedule_task.start()
    vote_passed_waitlist_task.start()
    if config.ECONOMY_ENABLED:
        prediction_task.start()
    sigma_decay_task.start()
    await init_config()
    async with async_session() as session:
        db_config = await async_query_first(session, Config)
        if db_config:
            trueskill_setup(
                mu=db_config.default_trueskill_mu,
                sigma=db_config.default_trueskill_sigma,
                tau=db_config.default_trueskill_tau,
            )


async def main():
    await create_seed_admins()
    await setup()
    await bot.start(config.API_KEY)


if __name__ == "__main__":
    try:
        with config.setup_logging(config.LOG_LEVEL):
            asyncio.run(main())
    except KeyboardInterrupt:
        print("KeyboardInterrupt")
