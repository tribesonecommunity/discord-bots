import logging

from discord import Colour, Embed, Interaction, TextChannel, app_commands
from discord.ext import commands

import discord_bots.config as env_config
from discord_bots.bot import bot
from discord_bots.checks import (
    is_admin_app_command,
    is_command_or_captain_channel,
    update_captain_channel_id_cache,
    update_ladder_channel_id_cache,
)
from discord_bots.models import Config, Session

_log = logging.getLogger(__name__)


class ConfigCommands(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    group = app_commands.Group(name="config", description="Config commands")

    @group.command(
        name="setdefaultmu", description="Set the default mu for new players"
    )
    @app_commands.check(is_admin_app_command)
    @app_commands.check(is_command_or_captain_channel)
    async def setdefaultmu(self, interaction: Interaction, value: float):
        """
        Set the default mu for new players
        """
        with Session() as session:
            config = session.query(Config).first()
            config.default_trueskill_mu = value
            session.commit()

        description = f"Default mu set to {value} by <@{interaction.user.id}>"
        embed = Embed(description=description, colour=Colour.green())
        await interaction.response.send_message(embed=embed)
        if env_config.ADMIN_LOG_CHANNEL:
            admin_log_channel = bot.get_channel(env_config.ADMIN_LOG_CHANNEL)
            if isinstance(admin_log_channel, TextChannel):
                await admin_log_channel.send(embed=embed)
        return embed

    @group.command(
        name="setdefaultsigma", description="Set the default sigma for new players"
    )
    @app_commands.check(is_admin_app_command)
    @app_commands.check(is_command_or_captain_channel)
    async def setdefaultsigma(self, interaction: Interaction, value: float):
        """
        Set the default sigma for new players
        """
        with Session() as session:
            config = session.query(Config).first()
            config.default_trueskill_sigma = value
            session.commit()

        description = f"Default sigma set to {value} by <@{interaction.user.id}>"
        embed = Embed(description=description, colour=Colour.green())
        await interaction.response.send_message(embed=embed)
        if env_config.ADMIN_LOG_CHANNEL:
            admin_log_channel = bot.get_channel(env_config.ADMIN_LOG_CHANNEL)
            if isinstance(admin_log_channel, TextChannel):
                await admin_log_channel.send(embed=embed)

    @group.command(
        name="setdefaulttau", description="Set the default tau for new players"
    )
    @app_commands.check(is_admin_app_command)
    @app_commands.check(is_command_or_captain_channel)
    async def setdefaulttau(self, interaction: Interaction, value: float):
        """
        Set the default tau for new players
        """
        with Session() as session:
            config = session.query(Config).first()
            config.default_trueskill_tau = value
            session.commit()

        description = f"Default tau set to {value} by <@{interaction.user.id}>"
        embed = Embed(description=description, colour=Colour.green())

        await interaction.response.send_message(embed=embed)
        if env_config.ADMIN_LOG_CHANNEL:
            admin_log_channel = bot.get_channel(env_config.ADMIN_LOG_CHANNEL)
            if isinstance(admin_log_channel, TextChannel):
                await admin_log_channel.send(embed=embed)

    @group.command(name="list", description="List current configuration values")
    @app_commands.check(is_admin_app_command)
    @app_commands.check(is_command_or_captain_channel)
    async def listconfig(self, interaction: Interaction):
        """
        List current configuration values
        """
        with Session() as session:
            config = session.query(Config).first()

            embed = Embed(
                title="Current Configuration",
                colour=Colour.blue(),
            )

            # Add fields for each config value
            embed.add_field(
                name="Default Mu", value=f"`{config.default_trueskill_mu}`", inline=True
            )
            embed.add_field(
                name="Default Sigma",
                value=f"`{config.default_trueskill_sigma}`",
                inline=True,
            )
            embed.add_field(
                name="Default Tau",
                value=f"`{config.default_trueskill_tau}`",
                inline=True,
            )
            embed.add_field(
                name="Position-based trueskill",
                value=f"`{config.enable_position_trueskill}`",
                inline=True,
            )
            captain_channel_value = (
                f"<#{config.captain_channel_id}>"
                if config.captain_channel_id
                else "`Not set`"
            )
            embed.add_field(
                name="Captain channel",
                value=captain_channel_value,
                inline=True,
            )
            ladder_channel_value = (
                f"<#{config.ladder_channel_id}>"
                if config.ladder_channel_id
                else "`Not set`"
            )
            embed.add_field(
                name="Ladder channel",
                value=ladder_channel_value,
                inline=True,
            )

            await interaction.response.send_message(embed=embed)

    @group.command(
        name="togglepositiontrueskill",
        description="Enable/disable position-based trueskill",
    )
    @app_commands.check(is_admin_app_command)
    @app_commands.check(is_command_or_captain_channel)
    @app_commands.describe(option="True/False")
    async def togglepositiontrueskill(self, interaction: Interaction, option: bool):
        """
        Toggle position-based trueskill
        """
        with Session() as session:
            config = session.query(Config).first()
            config.enable_position_trueskill = option
            session.commit()

            embed = Embed(
                description=f"Position-based trueskill set to {option} by <@{interaction.user.id}>",
                colour=Colour.blue(),
            )

            await interaction.response.send_message(embed=embed)

            if env_config.ADMIN_LOG_CHANNEL:
                admin_log_channel = bot.get_channel(env_config.ADMIN_LOG_CHANNEL)
                if isinstance(admin_log_channel, TextChannel):
                    await admin_log_channel.send(embed=embed)

    @group.command(
        name="togglemaptrueskill",
        description="Enable/disable map-based trueskill",
    )
    @app_commands.check(is_admin_app_command)
    @app_commands.check(is_command_or_captain_channel)
    @app_commands.describe(option="True/False")
    async def togglemaptrueskill(self, interaction: Interaction, option: bool):
        """
        Toggle map-based trueskill
        """
        with Session() as session:
            config = session.query(Config).first()
            config.enable_map_trueskill = option
            session.commit()

            embed = Embed(
                description=f"Map-based trueskill set to {option} by <@{interaction.user.id}>",
                colour=Colour.blue(),
            )

            await interaction.response.send_message(embed=embed)

            if env_config.ADMIN_LOG_CHANNEL:
                admin_log_channel = bot.get_channel(env_config.ADMIN_LOG_CHANNEL)
                if isinstance(admin_log_channel, TextChannel):
                    await admin_log_channel.send(embed=embed)

    @group.command(
        name="setcaptainchannel",
        description="Register a channel as the captain pick lobby",
    )
    @app_commands.check(is_admin_app_command)
    @app_commands.check(is_command_or_captain_channel)
    @app_commands.describe(channel="Channel to register as the captain pick lobby")
    async def setcaptainchannel(self, interaction: Interaction, channel: TextChannel):
        with Session() as session:
            config = session.query(Config).first()
            config.captain_channel_id = channel.id
            session.commit()
        update_captain_channel_id_cache(channel.id)

        embed = Embed(
            description=(
                f"Captain channel set to {channel.mention} by "
                f"<@{interaction.user.id}>. New queues created in that channel "
                f"will be captain-pick queues."
            ),
            colour=Colour.green(),
        )
        await interaction.response.send_message(embed=embed)
        if env_config.ADMIN_LOG_CHANNEL:
            admin_log_channel = bot.get_channel(env_config.ADMIN_LOG_CHANNEL)
            if isinstance(admin_log_channel, TextChannel):
                await admin_log_channel.send(embed=embed)

    @group.command(
        name="clearcaptainchannel",
        description="Unregister the captain pick lobby channel",
    )
    @app_commands.check(is_admin_app_command)
    @app_commands.check(is_command_or_captain_channel)
    async def clearcaptainchannel(self, interaction: Interaction):
        with Session() as session:
            config = session.query(Config).first()
            config.captain_channel_id = None
            session.commit()
        update_captain_channel_id_cache(None)

        embed = Embed(
            description=f"Captain channel cleared by <@{interaction.user.id}>",
            colour=Colour.green(),
        )
        await interaction.response.send_message(embed=embed)
        if env_config.ADMIN_LOG_CHANNEL:
            admin_log_channel = bot.get_channel(env_config.ADMIN_LOG_CHANNEL)
            if isinstance(admin_log_channel, TextChannel):
                await admin_log_channel.send(embed=embed)

    @group.command(
        name="setladderchannel",
        description="Register a channel as the ladder lobby",
    )
    @app_commands.check(is_admin_app_command)
    @app_commands.check(is_command_or_captain_channel)
    @app_commands.describe(channel="Channel to register as the ladder lobby")
    async def setladderchannel(self, interaction: Interaction, channel: TextChannel):
        with Session() as session:
            config = session.query(Config).first()
            config.ladder_channel_id = channel.id
            session.commit()
        update_ladder_channel_id_cache(channel.id)

        embed = Embed(
            description=(
                f"Ladder channel set to {channel.mention} by "
                f"<@{interaction.user.id}>. Ladder commands can now be used "
                f"there."
            ),
            colour=Colour.green(),
        )
        await interaction.response.send_message(embed=embed)
        if env_config.ADMIN_LOG_CHANNEL:
            admin_log_channel = bot.get_channel(env_config.ADMIN_LOG_CHANNEL)
            if isinstance(admin_log_channel, TextChannel):
                await admin_log_channel.send(embed=embed)

    @group.command(
        name="clearladderchannel",
        description="Unregister the ladder lobby channel",
    )
    @app_commands.check(is_admin_app_command)
    @app_commands.check(is_command_or_captain_channel)
    async def clearladderchannel(self, interaction: Interaction):
        with Session() as session:
            config = session.query(Config).first()
            config.ladder_channel_id = None
            session.commit()
        update_ladder_channel_id_cache(None)

        embed = Embed(
            description=f"Ladder channel cleared by <@{interaction.user.id}>",
            colour=Colour.green(),
        )
        await interaction.response.send_message(embed=embed)
        if env_config.ADMIN_LOG_CHANNEL:
            admin_log_channel = bot.get_channel(env_config.ADMIN_LOG_CHANNEL)
            if isinstance(admin_log_channel, TextChannel):
                await admin_log_channel.send(embed=embed)
