import asyncio
import logging
import re
from datetime import date, datetime, time, timedelta

import discord
import pytz
from discord import (
    ButtonStyle,
    Client,
    Colour,
    Embed,
    Guild,
    Interaction,
    Message,
    SelectOption,
    TextChannel,
    TextStyle,
    app_commands,
)
from discord.ext.commands import Bot
from discord.ui import Button, Select, TextInput, View
from discord.utils import get
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

import discord_bots.config as config
from discord_bots.checks import is_admin_app_command
from discord_bots.cogs.base import BaseCog
from discord_bots.models import (
    DiscordChannel,
    Player,
    Schedule,
    SchedulePlayer,
    ScopedSession,
)

_log = logging.getLogger(__name__)


class ScheduleCommands(BaseCog):
    def __init__(self, bot: Bot):
        super().__init__(bot)
        self.bot = bot
        self.views: list[ScheduleView] = []

    async def cog_load(self) -> None:
        if not ScheduleUtils.is_active():
            return

        for nth_embed in range(7):
            self.views.append(ScheduleView(nth_embed))
            first_schedule = ScheduleUtils.get_schedules_for_nth_embed(nth_embed)[0]
            self.bot.add_view(
                ScheduleView(nth_embed), message_id=first_schedule.message_id
            )

        ScopedSession.remove()

    async def cog_unload(self) -> None:
        for view in self.views:
            view.stop()

    @app_commands.command(
        name="createschedule",
        description="Create a daily schedule for up to three times",
    )
    @app_commands.check(is_admin_app_command)
    @app_commands.guild_only()
    async def createschedule(self, interaction: Interaction):
        """
        Still in beta - only one schedule at a time supported
        """
        if ScheduleUtils.is_active():
            await interaction.response.send_message(
                embed=Embed(
                    description="Only one schedule allowed at a time",
                    colour=Colour.red(),
                ),
                ephemeral=True,
            )
        else:
            timezones = ["US/Pacific", "US/Mountain", "US/Central", "US/Eastern"]

            options = [SelectOption(label=tz, value=tz) for tz in timezones]
            select_menu = Select(placeholder="Select an option", options=options)
            select_menu.callback = lambda interaction: asyncio.create_task(
                interaction.response.send_modal(ScheduleModal(select_menu.values[0]))
            )

            view = View()
            view.add_item(select_menu)
            await interaction.response.send_message(
                view=view, ephemeral=True, delete_after=10
            )

    @app_commands.command(name="deleteschedule", description="Delete the schedule")
    @app_commands.check(is_admin_app_command)
    @app_commands.guild_only()
    async def deleteschedule(self, interaction: Interaction):
        """
        Delete schedule
        """
        with ScopedSession() as session:
            session.query(SchedulePlayer).delete()
            session.query(Schedule).delete()

            discord_channel: DiscordChannel = (
                session.query(DiscordChannel)
                .filter(DiscordChannel.name == "schedule")
                .first()
            )
            if not discord_channel:
                await interaction.response.send_message(
                    embed=Embed(
                        description="Could not find schedule channel in database",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )
                return
            session.delete(discord_channel)
            session.commit()

        schedule_channel = interaction.guild.get_channel(discord_channel.channel_id)
        if not schedule_channel:
            await interaction.response.send_message(
                embed=Embed(
                    description="Could not find schedule channel in Discord",
                    colour=Colour.red(),
                ),
                ephemeral=True,
            )
            return
        await schedule_channel.delete()

        await interaction.response.send_message(
            embed=Embed(description="Schedule deleted", colour=Colour.green()),
            ephemeral=True,
        )

        from discord_bots.tasks import schedule_task

        schedule_task.cancel()


class ScheduleView(View):
    def __init__(self, nth_embed: str):
        super().__init__(timeout=None)
        # represents the nth embed in the schedule channel, where n=0 is today, listed first.
        self.nth_embed = nth_embed
        self.create_buttons()

    def create_buttons(self):
        for i, schedule in enumerate(
            ScheduleUtils.get_schedules_for_nth_embed(self.nth_embed), start=1
        ):
            if i == 1:
                label = "First Time"
            elif i == 2:
                label = "Second Time"
            elif i == 3:
                label = "Third Time"
            button_time = Button(
                label=label,
                style=ButtonStyle.primary,
                custom_id=f"{self.nth_embed}-{i}",
                emoji="⏱️",
            )
            button_time.callback = (
                lambda interaction, schedule=schedule: self.button_time_callback(
                    interaction, schedule
                )
            )
            self.add_item(button_time)

        button_day = Button(
            label="Add All",
            style=ButtonStyle.primary,
            custom_id=f"{self.nth_embed}-all",
            emoji="⏱️",
        )
        button_day.callback = lambda interaction: self.button_day_callback(interaction)
        self.add_item(button_day)

        ScopedSession.remove()

    async def button_time_callback(self, interaction: Interaction, schedule: Schedule):
        with ScopedSession() as session:
            player: Player = (
                session.query(Player).filter(Player.id == interaction.user.id).first()
            )
            schedule_player: SchedulePlayer = (
                session.query(SchedulePlayer)
                .filter(
                    SchedulePlayer.schedule_id == schedule.id,
                    SchedulePlayer.player_id == player.id,
                )
                .first()
            )

            if schedule_player:
                session.delete(schedule_player)
            else:
                session.add(
                    SchedulePlayer(schedule_id=schedule.id, player_id=player.id)
                )
            session.commit()

        await ScheduleUtils.rebuild_embed(interaction.guild, self.nth_embed)
        await interaction.response.defer()

    async def button_day_callback(self, interaction: Interaction):
        with ScopedSession() as session:
            player: Player = (
                session.query(Player).filter(Player.id == interaction.user.id).first()
            )
            schedule_players: list[SchedulePlayer] = (
                session.query(SchedulePlayer)
                .filter(SchedulePlayer.player_id == player.id)
                .all()
            )
            schedule_ids = [x.schedule_id for x in schedule_players]
            for schedule in ScheduleUtils.get_schedules_for_nth_embed(self.nth_embed):
                if schedule.id not in schedule_ids:
                    session.add(
                        SchedulePlayer(schedule_id=schedule.id, player_id=player.id)
                    )
            session.commit()

        await ScheduleUtils.rebuild_embed(interaction.guild, self.nth_embed)
        await interaction.response.defer()


class ScheduleModal(discord.ui.Modal, title="Enter up to three schedule times."):
    def __init__(self, timezone_input: str):
        super().__init__(timeout=None)
        self.timezone_input = timezone_input
        self.input_one: TextInput = TextInput(
            label="First Time",
            style=TextStyle.short,
            required=True,
            placeholder="Valid formats: 7:00am, 2:00pm, 11:00PM",
        )
        self.input_two: TextInput = TextInput(
            label="Second Time", style=TextStyle.short, required=False
        )
        self.input_three: TextInput = TextInput(
            label="Third Time", style=TextStyle.short, required=False
        )
        self.add_item(self.input_one)
        self.add_item(self.input_two)
        self.add_item(self.input_three)

    async def on_submit(self, interaction: Interaction[Client]) -> None:
        await interaction.response.defer(thinking=True, ephemeral=True)
        inputs = [self.input_one.value, self.input_two.value, self.input_three.value]
        inputs = [x for x in inputs if x != ""]  # clean up empty inputs
        pattern = r"^\d{1,2}:\d{2}[APap][Mm]$"  # regex for 7:00pm or 7:00PM

        for input in inputs:
            if not re.match(pattern, input):
                await interaction.followup.send(
                    embed=Embed(
                        description="Invalid time format.  Valid formats:\n- 7:00am\n- 2:00pm\n- 11:00AM",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )
                return

        category_channel: discord.abc.GuildChannel | None = (
            interaction.guild.get_channel(config.TRIBES_VOICE_CATEGORY_CHANNEL_ID)
        )
        if not isinstance(category_channel, discord.CategoryChannel):
            await interaction.followup.send(
                embed=Embed(
                    description="Could not find voice channel category",
                    colour=Colour.red(),
                ),
                ephemeral=True,
            )
            return

        schedule_channel: TextChannel = await interaction.guild.create_text_channel(
            "schedule",
            topic="Welcome to the scheduling channel!  Click the Time buttons to toggle your availability for a specific day and time.  Click the Add All button to add to all times for that day.",
            category=category_channel,
        )

        with ScopedSession() as session:
            session.add(DiscordChannel(name="schedule", channel_id=schedule_channel.id))

            user_timezone = pytz.timezone(self.timezone_input)
            for nth_embed in range(7):
                embed = Embed(
                    title=ScheduleUtils.get_embed_title(nth_embed), colour=Colour.blue()
                )
                message = await schedule_channel.send(embed=embed)
                date_to_add: date = date.today() + timedelta(days=nth_embed)

                for input in inputs:
                    time_to_add: time = datetime.strptime(input, "%I:%M%p").time()
                    datetime_to_add: datetime = datetime.combine(
                        date_to_add, time_to_add
                    )
                    datetime_to_add = user_timezone.localize(
                        datetime_to_add
                    )  # add user's timezone
                    datetime_to_add = datetime_to_add.astimezone(
                        pytz.utc
                    )  # convert to utc for database
                    session.add(
                        Schedule(datetime=datetime_to_add, message_id=message.id)
                    )

                try:
                    session.commit()
                except IntegrityError as exc:
                    _log.error(f"integrity error {exc}")
                    session.rollback()

            # run two separate loops because my method for grouping schedules together in embeds relies on them all being added to the database first
            coroutines = []
            for nth_embed in range(7):
                coroutines.append(
                    ScheduleUtils.rebuild_embed(interaction.guild, nth_embed)
                )
            try:
                await asyncio.gather(*coroutines)
            except Exception as exc:
                _log.exception(f"exception {exc}")

        await interaction.followup.send(
            embed=Embed(description="Schedule created", colour=Colour.green()),
            ephemeral=True,
        )
        from discord_bots.tasks import schedule_task

        schedule_task.start()

class ScheduleUtils:
    @classmethod
    async def rebuild_embed(self, guild: Guild, nth_embed: int):
        with ScopedSession() as session:
            schedule_channel_id = (
                session.query(DiscordChannel.channel_id)
                .filter(DiscordChannel.name == "schedule")
                .scalar()
            )
            schedule_channel = get(guild.text_channels, id=schedule_channel_id)

            schedules_for_nth_embed = ScheduleUtils.get_schedules_for_nth_embed(
                nth_embed
            )
            message: Message = schedule_channel.get_partial_message(
                schedules_for_nth_embed[0].message_id
            )

            embed = Embed(
                title=ScheduleUtils.get_embed_title(nth_embed), colour=Colour.blue()
            )

            utc_tz = pytz.utc
            for schedule in schedules_for_nth_embed:
                utc_datetime = utc_tz.localize(schedule.datetime)
                timestamp = discord.utils.format_dt(utc_datetime)
                players_scheduled = (
                    session.query(Player)
                    .join(SchedulePlayer, SchedulePlayer.player_id == Player.id)
                    .filter(SchedulePlayer.schedule_id == schedule.id)
                    .all()
                )
                if not players_scheduled:
                    value = "> \n** **"  # create column indentation for empty schedules
                else:
                    value = "\n".join(
                        [f"> <@{player.id}>" for player in players_scheduled]
                    )

                embed.add_field(
                    name=timestamp,
                    value=value,
                    inline=True,
                )
            await message.edit(embed=embed, view=ScheduleView(nth_embed))

    @classmethod
    def get_embed_title(cls, nth_embed: int) -> str:
        if nth_embed == 0:
            return "Today"
        elif nth_embed == 1:
            return "Tomorrow"
        else:
            return f"{nth_embed} Days From Now"

    @classmethod
    def get_schedules_for_nth_embed(cls, nth_embed: int) -> list[Schedule]:
        session = (
            ScopedSession()
        )  # this needs to be left open for use in the schedule task
        num_schedules: int = session.query(func.count(Schedule.id)).scalar()
        schedules_per_day = num_schedules / 7
        offset = nth_embed * schedules_per_day
        return (
            session.query(Schedule)
            .order_by(Schedule.datetime.asc())
            .slice(offset, offset + schedules_per_day)
            .all()
        )

    @classmethod
    def is_active(cls) -> bool:
        with ScopedSession() as session:
            schedule = session.query(Schedule).first()
            return bool(schedule)
