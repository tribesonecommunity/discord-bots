from datetime import datetime, timezone

from discord import Colour, Embed, Member, Message, Reaction
from discord.abc import User
from discord.ext.commands import CommandError, Context, UserInputError

from discord_bots.cogs.categories import CategoryCommands
from discord_bots.cogs.map import MapCommands
from discord_bots.cogs.queue import QueueCommands
from discord_bots.cogs.raffle import RaffleCommands
from discord_bots.cogs.rotation import RotationCommands
from discord_bots.cogs.vote import VoteCommands

import discord_bots.config as config
from .bot import bot
from .models import CustomCommand, Player, QueuePlayer, QueueWaitlistPlayer, Session
from .tasks import (
    add_player_task,
    afk_timer_task,
    leaderboard_task,
    map_rotation_task,
    queue_waitlist_task,
    vote_passed_waitlist_task,
)

add_player_task.start()
afk_timer_task.start()
leaderboard_task.start()
map_rotation_task.start()
queue_waitlist_task.start()
vote_passed_waitlist_task.start()


def create_seed_admins():
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
                        name='AUTO_GENERATED_ADMIN',
                        last_activity_at=datetime.now(timezone.utc),
                    )
                )
        session.commit()


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")


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
            print("[on_command_error]:", error, ", command:", ctx.command.name)
        else:
            print("[on_command_error]:", error)


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
            print(f"Your id: {message.author.id}")
            print(f"Channel id: {message.channel.id}")
            print(f"{[(c.id, c.name) for c in guild.categories]}")

            # await message.channel.send(content=f"Your id: {message.author.id}\nChannel id: {message.channel.id}")
            return

    if (config.CHANNEL_ID and message.channel.id == config.CHANNEL_ID) or (
        config.LEADERBOARD_CHANNEL and message.channel.id == config.LEADERBOARD_CHANNEL
    ):
        session = Session()
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
                )
            )
        session.commit()
        session.close()
        await bot.process_commands(message)

        # Custom commands below
        if not message.content.startswith(config.COMMAND_PREFIX):
            return

        bot_commands = {command.name for command in bot.commands}
        command_name = message.content.split(" ")[0][1:]
        session = Session()
        if command_name not in bot_commands:
            custom_command: CustomCommand | None = (
                session.query(CustomCommand)
                .filter(CustomCommand.name == command_name)
                .first()
            )
            if custom_command:
                await message.channel.send(content=custom_command.output)
        session.close()


@bot.event
async def on_reaction_add(reaction: Reaction, user: User | Member):
    session = Session()
    player: Player | None = session.query(Player).filter(Player.id == user.id).first()
    if player:
        player.last_activity_at = datetime.now(timezone.utc)
        session.commit()
    else:
        session.add(
            Player(
                id=reaction.message.author.id,
                name=reaction.message.author.display_name,
                last_activity_at=datetime.now(timezone.utc),
            )
        )
    session.close()


@bot.event
async def on_join(member: Member):
    session = Session()
    player = session.query(Player).filter(Player.id == member.id).first()
    if player:
        player.name = member.name
        session.commit()
    else:
        session.add(Player(id=member.id, name=member.name))
        session.commit()
    session.close()


@bot.event
async def on_leave(member: Member):
    session = Session()
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


def main():
    if not config.CONFIG_VALID:
        print("You must provide a valid config!")
        return

    create_seed_admins()

    bot.add_cog(CategoryCommands(bot))
    bot.add_cog(RaffleCommands(bot))
    bot.add_cog(RotationCommands(bot))
    bot.add_cog(MapCommands(bot))
    bot.add_cog(QueueCommands(bot))
    bot.add_cog(VoteCommands(bot))
    bot.run(config.API_KEY)


if __name__ == "__main__":
    main()
