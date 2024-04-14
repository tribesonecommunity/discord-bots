import asyncio
import logging
from datetime import datetime, timezone

import sqlalchemy
from discord import Colour, Embed, Interaction, Member, Message, Reaction
from discord.abc import User
from discord.app_commands import AppCommandError, errors
from discord.ext.commands import CommandError, Context, UserInputError

import discord_bots.config as config
from discord_bots.cogs.categories import CategoryCommands
from discord_bots.cogs.economy import EconomyCommands
from discord_bots.cogs.in_progress_game import InProgressGameCog
from discord_bots.cogs.map import MapCommands
from discord_bots.cogs.queue import QueueCommands
from discord_bots.cogs.raffle import RaffleCommands
from discord_bots.cogs.rotation import RotationCommands
from discord_bots.cogs.schedule import ScheduleCommands, ScheduleUtils
from discord_bots.cogs.vote import VoteCommands

from .bot import bot
from .models import (
    CustomCommand,
    Player,
    QueuePlayer,
    QueueWaitlistPlayer,
    Session,
    engine,
)
from .tasks import (
    add_player_task,
    afk_timer_task,
    leaderboard_task,
    map_rotation_task,
    prediction_task,
    queue_waitlist_task,
    schedule_task,
    vote_passed_waitlist_task,
)

_log = logging.getLogger(__name__)

async def create_seed_admins():
    with Session() as session:
        for seed_admin_id in config.SEED_ADMIN_IDS:
            player = session.query(Player).filter(Player.id == seed_admin_id).first()
            if player:
                player.is_admin = True
            else:
                session.add(
                    Player(
                        id=seed_admin_id,
                        is_admin=True,
                        name="AUTO_GENERATED_ADMIN",
                        last_activity_at=datetime.now(timezone.utc),
                        currency=config.STARTING_CURRENCY,
                    )
                )
        session.commit()


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
    if interaction.response.is_done():
        await interaction.followup.send(
            embed=Embed(description="Oops! Something went wrong ☹️", color=Colour.red())
        )
    else:
        # fallback case that responds to the interaction, since there always needs to be a response
        await interaction.response.send_message(
            embed=Embed(description="Oops! Something went wrong ☹️", color=Colour.red()),
            ephemeral=True,
        )

    if isinstance(error, errors.CheckFailure):
        return
    else:
        if interaction.command:
            _log.error(
                f"[on_app_command_error]: {error}, command: {interaction.command.name}"
            )
        else:
            _log.error(f"[on_app_command_error]: {error}")


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
    else:
        if ctx.command:
            _log.exception(
                f"[on_command_error]: Ignoring exception in command {ctx.command.name}: {error}"
            )
        else:
            _log.exception(f"[on_command_error]: Ignoring exception: {error}")


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
        session: sqlalchemy.orm.Session
        with Session() as session:
            player: Player | None = (
                session.query(Player).filter(Player.id == message.author.id).first()
            )
            if player:
                player.last_activity_at = datetime.now(timezone.utc)
                if player.name != message.author.display_name:
                    player.name = message.author.display_name
            else:
                session.add(
                    Player(
                        id=message.author.id,
                        name=message.author.display_name,
                        last_activity_at=datetime.now(timezone.utc),
                        currency=config.STARTING_CURRENCY,
                    )
                )
            session.commit()
        await bot.process_commands(message)

        # Custom commands below
        if not message.content.startswith(config.COMMAND_PREFIX):
            return

        bot_commands = {command.name for command in bot.commands}
        command_name = message.content.split(" ")[0][1:]
        with Session() as session:
            if command_name not in bot_commands:
                custom_command: CustomCommand | None = (
                    session.query(CustomCommand)
                    .filter(CustomCommand.name == command_name)
                    .first()
                )
                if custom_command:
                    await message.channel.send(content=custom_command.output)


@bot.event
async def on_reaction_add(reaction: Reaction, user: User | Member):
    session: sqlalchemy.orm.Session
    with Session() as session:
        player: Player | None = (
            session.query(Player).filter(Player.id == user.id).first()
        )
        if player:
            player.last_activity_at = datetime.now(timezone.utc)
            player.name = user.display_name
            session.commit()
        else:
            session.add(
                Player(
                    id=reaction.message.author.id,
                    name=reaction.message.author.display_name,
                    last_activity_at=datetime.now(timezone.utc),
                    currency=config.STARTING_CURRENCY,
                )
            )


@bot.event
async def on_join(member: Member):
    session: sqlalchemy.orm.Session
    with Session() as session:
        player = session.query(Player).filter(Player.id == member.id).first()
        if player:
            player.name = member.name
            session.commit()
        else:
            session.add(
                Player(
                    id=member.id,
                    name=member.display_name,
                    currency=config.STARTING_CURRENCY,
                )
            )
            session.commit()


@bot.event
async def on_leave(member: Member):
    session: sqlalchemy.orm.Session
    with Session() as session:
        session.query(QueuePlayer).filter(QueuePlayer.player_id == member.id).delete()
        session.query(QueueWaitlistPlayer).filter(
            QueueWaitlistPlayer.player_id == member.id
        ).delete()
        session.commit()


@bot.before_invoke
async def before_invoke(context: Context):
    session = Session()
    context.session = session


@bot.after_invoke
async def after_invoke(context: Context):
    context.session.close()


async def setup():
    await bot.add_cog(CategoryCommands(bot))
    await bot.add_cog(RaffleCommands(bot))
    await bot.add_cog(RotationCommands(bot))
    await bot.add_cog(MapCommands(bot))
    await bot.add_cog(QueueCommands(bot))
    await bot.add_cog(VoteCommands(bot))
    await bot.add_cog(EconomyCommands(bot))
    await bot.add_cog(InProgressGameCog(bot))
    await bot.add_cog(ScheduleCommands(bot))
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
