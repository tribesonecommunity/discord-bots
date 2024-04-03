import logging
import re
from datetime import date, datetime, time, timedelta

import discord
from discord import (
    ButtonStyle,
    Client,
    Colour,
    Embed,
    Guild,
    Interaction,
    Message,
    TextChannel,
    TextStyle,
    app_commands,
)
from discord.ext.commands import Bot
from discord.ui import Button, TextInput, View
from discord.utils import get
from sqlalchemy.exc import IntegrityError

from discord_bots.checks import is_admin_app_command
from discord_bots.cogs.base import BaseCog
from discord_bots.models import (
    DiscordChannel,
    Player,
    Schedule,
    SchedulePlayer,
    Session,
)

_log = logging.getLogger(__name__)
DAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]


class ScheduleCommands(BaseCog):
    def __init__(self, bot: Bot):
        super().__init__(bot)
        self.bot = bot
        self.views: list[ScheduleView] = []

    async def cog_load(self) -> None:
        if not ScheduleUtils.is_active():
            return

        with Session() as session:
            for day in DAYS:
                self.views.append(ScheduleView(day=day))
                message_id = (
                    session.query(Schedule.message_id)
                    .filter(Schedule.day == day)
                    .first()[0]
                )
                self.bot.add_view(ScheduleView(day=day), message_id=message_id)

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
                )
            )
        else:
            await interaction.response.send_modal(ScheduleModal())

    @app_commands.command(name="deleteschedule", description="Delete a schedule")
    @app_commands.check(is_admin_app_command)
    @app_commands.guild_only()
    async def deleteschedule(self, interaction: Interaction):
        """
        Delete schedule
        """
        with Session() as session:
            session.query(SchedulePlayer).delete()
            session.query(Schedule).delete()

            discord_channel: DiscordChannel = (
                session.query(DiscordChannel)
                .filter(DiscordChannel.name == "schedule")
                .first()
            )
            await interaction.guild.get_channel(discord_channel.channel_id).delete()
            session.delete(discord_channel)

            session.commit()

        await interaction.response.send_message(
            embed=Embed(description="Schedule deleted", colour=Colour.green())
        )


class ScheduleView(View):
    def __init__(self, day: str):
        super().__init__(timeout=None)
        self.day = day
        self.schedules_for_day: list[Schedule] = ScheduleUtils.get_schedules_for_day(
            self.day
        )
        self.create_buttons()

    def create_buttons(self):
        for i, schedule in enumerate(self.schedules_for_day, start=1):
            button_time = Button(
                label=f"\u2800\u2800\u2800\u2800Time {i}\u2800\u2800\u2800\u2800",
                style=ButtonStyle.primary,
                custom_id=f"{self.day}-{i}",
            )
            button_time.callback = (
                lambda interaction, schedule=schedule: self.button_time_callback(
                    interaction, schedule
                )
            )
            self.add_item(button_time)

        button_day = Button(
            label=f"\u2800\u2800\u2800\u2800Add All\u2800\u2800\u2800\u2800",
            style=ButtonStyle.primary,
            custom_id=f"{self.day}-all",
        )
        button_day.callback = lambda interaction: self.button_day_callback(interaction)
        self.add_item(button_day)

    async def button_time_callback(self, interaction: Interaction, schedule: Schedule):
        with Session() as session:
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

        await ScheduleUtils.rebuild_embed(interaction.guild, self.day)
        await interaction.response.defer()

    async def button_day_callback(self, interaction: Interaction):
        with Session() as session:
            player: Player = (
                session.query(Player).filter(Player.id == interaction.user.id).first()
            )
            schedule_players: list[SchedulePlayer] = (
                session.query(SchedulePlayer)
                .filter(SchedulePlayer.player_id == player.id)
                .all()
            )
            schedule_ids = [x.schedule_id for x in schedule_players]
            for schedule in self.schedules_for_day:
                if schedule.id not in schedule_ids:
                    session.add(
                        SchedulePlayer(schedule_id=schedule.id, player_id=player.id)
                    )
            session.commit()

        await ScheduleUtils.rebuild_embed(interaction.guild, self.day)
        await interaction.response.defer()


class ScheduleModal(discord.ui.Modal, title="Enter up to three schedule times."):
    def __init__(self):
        super().__init__(timeout=None)
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
        await interaction.response.defer(thinking=True)
        inputs = [self.input_one.value, self.input_two.value, self.input_three.value]
        inputs = [x for x in inputs if x != ""]  # clean up empty inputs
        pattern = r"^\d{1,2}:\d{2}[APap][Mm]$"

        for input in inputs:
            if not re.match(pattern, input):
                await interaction.response.send_message(
                    embed=Embed(
                        description="Invalid time format.  Valid formats:\n- 7:00am\n- 2:00pm\n- 11:00AM",
                        colour=Colour.red(),
                    ),
                    ephemeral=True,
                )
                return

        schedule_channel: TextChannel = await interaction.guild.create_text_channel(
            "schedule",
            topic="Welcome to the scheduling channel!  Click the Time buttons to toggle your availability for a specific day and time.  Click the Add All button to add to all times for that day.",
        )

        with Session() as session:
            session.add(DiscordChannel(name="schedule", channel_id=schedule_channel.id))

            for day in DAYS:
                embed = Embed(title=day, colour=Colour.blue())
                message = await schedule_channel.send(embed=embed)
                timestamp_date: date = ScheduleUtils.get_next_date(day)

                for input in inputs:
                    time_to_add: time = datetime.strptime(input, "%I:%M%p").time()
                    timestamp_date_time: datetime = datetime.combine(
                        timestamp_date, time_to_add
                    )
                    timestamp = discord.utils.format_dt(timestamp_date_time, style="t")
                    embed.add_field(
                        name=f"|            {timestamp}            |",
                        value="",
                        inline=True,
                    )
                    session.add(
                        Schedule(day=day, time=time_to_add, message_id=message.id)
                    )

                try:
                    session.commit()
                except IntegrityError as exc:
                    _log.error(f"integrity error {exc}")
                    session.rollback()

                await message.edit(embed=embed, view=ScheduleView(day))

        await interaction.followup.send(
            embed=Embed(description="Schedule created", colour=Colour.green())
        )


class ScheduleUtils:
    @classmethod
    async def rebuild_embed(self, guild: Guild, day: str):
        with Session() as session:
            schedule_channel_id = (
                session.query(DiscordChannel.channel_id)
                .filter(DiscordChannel.name == "schedule")
                .scalar()
            )
            schedule_channel = get(guild.text_channels, id=schedule_channel_id)

            schedules_for_day = ScheduleUtils.get_schedules_for_day(day)
            message: Message = await schedule_channel.fetch_message(
                schedules_for_day[0].message_id
            )

            embed = Embed(title=day, colour=Colour.blue())

            for schedule in schedules_for_day:
                timestamp_date: date = ScheduleUtils.get_next_date(day)
                timestamp_datetime: datetime = datetime.combine(
                    timestamp_date, schedule.time
                )
                timestamp = discord.utils.format_dt(timestamp_datetime, style="t")
                players_scheduled = (
                    session.query(Player)
                    .join(SchedulePlayer, SchedulePlayer.player_id == Player.id)
                    .filter(SchedulePlayer.schedule_id == schedule.id)
                    .all()
                )
                embed.add_field(
                    name=f"|            {timestamp}            |",
                    value="\n".join(
                        [f"> <@{player.id}>" for player in players_scheduled]
                    ),
                    inline=True,
                )
            await message.edit(embed=embed, view=ScheduleView(day))

    @classmethod
    def get_next_date(cls, target_day: str) -> date:
        """
        Returns the upcoming date for a given day of the week.
        Returns today if the day of the week matches.
        """
        target_day_num: int = cls.convert_day_to_datetime_num(target_day)
        today: date = date.today()
        today_num: int = today.weekday()
        if target_day_num == today_num:
            days_ahead = 0
        elif target_day_num > today_num:
            days_ahead = target_day_num - today_num
        elif target_day_num < today_num:
            days_ahead = 7 - today_num + target_day_num
        return today + timedelta(days=days_ahead)

    @classmethod
    def get_schedules_for_day(cls, day: str) -> list[Schedule]:
        with Session() as session:
            return session.query(Schedule).filter(Schedule.day == day).all()

    @classmethod
    def convert_day_to_datetime_num(cls, day: str) -> int:
        """
        Number representation of a day in the datetime module, where Monday = 0.
        """
        match day:
            case "Monday":
                return 0
            case "Tuesday":
                return 1
            case "Wednesday":
                return 2
            case "Thursday":
                return 3
            case "Friday":
                return 4
            case "Saturday":
                return 5
            case "Sunday":
                return 6

    @classmethod
    def is_active(cls) -> bool:
        with Session() as session:
            schedule = session.query(Schedule).first()
            return bool(schedule)
