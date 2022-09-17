import logging
from typing import List

import dateparser
import interactions
from apscheduler.jobstores.base import JobLookupError
from interactions import CommandContext, Embed, Option, OptionType
from interactions.ext.paginator import Paginator
from interactions.ext.wait_for import setup

from discord_reminder_bot.countdown import calculate
from discord_reminder_bot.create_pages import create_pages
from discord_reminder_bot.settings import (
    bot_token,
    config_timezone,
    log_level,
    scheduler,
    sqlite_location,
)

bot = interactions.Client(token=bot_token)

# Initialize the wait_for extension.
setup(bot)


@bot.command(name="remind")
async def base_command(ctx: interactions.CommandContext):
    """This description isn't seen in the UI (yet?)

    This is the base command for the reminder bot."""
    pass


@bot.modal("edit_modal")
async def modal_response_edit(ctx: CommandContext, new_date: str, new_message: str):
    """Edit a reminder.

    Args:
        ctx: The context.
        new_date: The new date.
        new_message: The new message.

    Returns:
        A Discord message with changes.
    """
    await ctx.defer()

    job_id = ctx.message.embeds[0].title
    old_date = None
    old_message = None

    try:
        job = scheduler.get_job(job_id)
    except JobLookupError as e:
        return await ctx.send(
            f"Failed to get the job after the modal.\nJob ID: {job_id}\nError: {e}"
        )

    if job is None:
        return await ctx.send("Job not found.")

    message_embeds: List[Embed] = ctx.message.embeds
    for embeds in message_embeds:
        for field in embeds.fields:
            if field.name == "**Channel:**":
                continue
            elif field.name == "**Message:**":
                old_message = field.value
            elif field.name == "**Trigger:**":
                old_date = field.value
            else:
                return await ctx.send(
                    f"Unknown field name ({field.name}).", ephemeral=True
                )

    msg = f"Modified job {job_id}.\n"
    if new_date != old_date and old_date is not None:
        parsed_date = dateparser.parse(
            f"{new_date}",
            settings={
                "PREFER_DATES_FROM": "future",
                "TIMEZONE": f"{config_timezone}",
                "TO_TIMEZONE": f"{config_timezone}",
            },
        )

        if not parsed_date:
            return await ctx.send("Could not parse the date.", ephemeral=True)

        date_new = parsed_date.strftime("%Y-%m-%d %H:%M:%S")

        new_job = scheduler.reschedule_job(job.id, run_date=date_new)
        new_time = calculate(new_job)

        msg += f"**Old date**: {old_date}\n**New date**: {date_new} (in {new_time})"

    if new_message != old_message and old_message is not None:
        channel_id = job.kwargs.get("channel_id")
        job_author_id = job.kwargs.get("author_id")

        scheduler.modify_job(
            job.id,
            kwargs={
                "channel_id": channel_id,
                "message": f"{new_message}",
                "author_id": job_author_id,
            },
        )

        msg += f"**Old message**: {old_message}\n**New message**: {new_message}"

    return await ctx.send(msg)


@base_command.subcommand(
    name="list", description="List, pause, unpause, and remove reminders."
)
async def list_command(ctx: interactions.CommandContext):
    """List, pause, unpause, and remove reminders."""

    pages = create_pages(ctx)
    if not pages:
        await ctx.send("No reminders found.")
        return

    if len(pages) == 1:
        await ctx.send("a")
        return

    paginator: Paginator = Paginator(
        client=bot,
        ctx=ctx,
        pages=pages,
        remove_after_timeout=True,
        author_only=True,
        extended_buttons=False,
        use_buttons=False,
    )

    await paginator.run()


@base_command.subcommand(
    name="add",
    description="Set a reminder.",
    options=[
        Option(
            name="message_reason",
            description="The message to send.",
            type=OptionType.STRING,
            required=True,
        ),
        Option(
            name="message_date",
            description="The date to send the message.",
            type=OptionType.STRING,
            required=True,
        ),
        Option(
            name="different_channel",
            description="The channel to send the message to.",
            type=OptionType.CHANNEL,
            required=False,
        ),
    ],
)
async def command_add(
        ctx: interactions.CommandContext,
        message_reason: str,
        message_date: str,
        different_channel: interactions.Channel | None = None,
):
    """Add a new reminder. You can add a date and message.

    Args:
        ctx: Context of the slash command. Contains the guild, author and message and more.
        message_date: The parsed date and time when you want to get reminded.
        message_reason: The message the bot should write when the reminder is triggered.
        different_channel: The channel the reminder should be sent to.
    """
    await ctx.defer()

    parsed_date = dateparser.parse(
        f"{message_date}",
        settings={
            "PREFER_DATES_FROM": "future",
            "TO_TIMEZONE": f"{config_timezone}",
        },
    )

    if not parsed_date:
        await ctx.send("Could not parse the date.")
        return

    channel_id = int(ctx.channel_id)

    # If we should send the message to a different channel
    if different_channel:
        channel_id = int(different_channel.id)

    run_date = parsed_date.strftime("%Y-%m-%d %H:%M:%S")
    reminder = scheduler.add_job(
        send_to_discord,
        run_date=run_date,
        kwargs={
            "channel_id": channel_id,
            "message": message_reason,
            "author_id": ctx.member.id,
        },
    )

    message = (
        f"Hello {ctx.member.name},"
        f" I will notify you in <#{channel_id}> at:\n"
        f"**{run_date}** (in {calculate(reminder)})\n"
        f"With the message:\n**{message_reason}**."
    )

    await ctx.send(message)


@base_command.subcommand(
    name="cron",
    description="Triggers when current time matches all specified time constraints, similarly to the UNIX cron.",
    options=[
        Option(
            name="message_reason",
            description="The message I'm going to send you.",
            type=OptionType.STRING,
            required=True,
        ),
        Option(
            name="year",
            description="4-digit year. (Example: 2042)",
            type=OptionType.STRING,
            required=False,
        ),
        Option(
            name="month",
            description="Month (1-12)",
            type=OptionType.STRING,
            required=False,
        ),
        Option(
            name="day",
            description="Day of month (1-31)",
            type=OptionType.STRING,
            required=False,
        ),
        Option(
            name="week",
            description="ISO week (1-53)",
            type=OptionType.STRING,
            required=False,
        ),
        Option(
            name="day_of_week",
            description="Number or name of weekday (0-6 or mon,tue,wed,thu,fri,sat,sun). The first weekday is monday.",
            type=OptionType.STRING,
            required=False,
        ),
        Option(
            name="hour",
            description="Hour (0-23)",
            type=OptionType.STRING,
            required=False,
        ),
        Option(
            name="minute",
            description="Minute (0-59)",
            type=OptionType.STRING,
            required=False,
        ),
        Option(
            name="second",
            description="Second (0-59)",
            type=OptionType.STRING,
            required=False,
        ),
        Option(
            name="start_date",
            description="Earliest possible time to trigger on, in the ISO 8601 format. (Example: 2010-10-10 09:30:00)",
            type=OptionType.STRING,
            required=False,
        ),
        Option(
            name="end_date",
            description="Latest possible time to trigger on, in the ISO 8601 format. (Example: 2010-10-10 09:30:00)",
            type=OptionType.STRING,
            required=False,
        ),
        Option(
            name="timezone",
            description="Time zone to use for the date/time calculations (defaults to scheduler timezone)",
            type=OptionType.STRING,
            required=False,
        ),
        Option(
            name="jitter",
            description="Delay the job execution by x seconds at most. Adds a random component to the execution time.",
            type=OptionType.INTEGER,
            required=False,
        ),
        Option(
            name="different_channel",
            description="Send the messages to a different channel.",
            type=OptionType.CHANNEL,
            required=False,
        ),
    ],
)
async def remind_cron(
        ctx: interactions.CommandContext,
        message_reason: str,
        year: int | None = None,
        month: int | None = None,
        day: int | None = None,
        week: int | None = None,
        day_of_week: str | None = None,
        hour: int | None = None,
        minute: int | None = None,
        second: int | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        timezone: str | None = None,
        jitter: int | None = None,
        different_channel: interactions.Channel | None = None,
):
    """Create new cron job. Works like UNIX cron.

    https://en.wikipedia.org/wiki/Cron
    Args that are None will be defaulted to *.

    Args:
        ctx: Context of the slash command. Contains the guild, author and message and more.
        message_reason: The message the bot should send every time cron job triggers.
        year: 4-digit year.
        month: Month (1-12).
        day: Day of month (1-31).
        week: ISO week (1-53).
        day_of_week: Number or name of weekday (0-6 or mon,tue,wed,thu,fri,sat,sun).
        hour: Hour (0-23).
        minute: Minute (0-59).
        second: Second (0-59).
        start_date: Earliest possible date/time to trigger on (inclusive).
        end_date: Latest possible date/time to trigger on (inclusive).
        timezone: Time zone to use for the date/time calculations Defaults to scheduler timezone.
        jitter: Delay the job execution by jitter seconds at most.
        different_channel: Send the messages to a different channel.
    """
    await ctx.defer()

    channel_id = int(ctx.channel_id)

    # If we should send the message to a different channel
    if different_channel:
        channel_id = int(different_channel.id)

    job = scheduler.add_job(
        send_to_discord,
        "cron",
        year=year,
        month=month,
        day=day,
        week=week,
        day_of_week=day_of_week,
        hour=hour,
        minute=minute,
        second=second,
        start_date=start_date,
        end_date=end_date,
        timezone=timezone,
        jitter=jitter,
        kwargs={
            "channel_id": channel_id,
            "message": message_reason,
            "author_id": ctx.member.id,
        },
    )

    # TODO: Add what arguments we used in the job to the message
    message = (
        f"Hello {ctx.member.name},"
        f" I will send messages to <#{channel_id}>.\n"
        f"First run in {calculate(job)} with the message:\n"
        f"**{message_reason}**."
    )
    await ctx.send(message)


@base_command.subcommand(
    name="interval",
    description="Schedules messages to be run periodically, on selected intervals.",
    options=[
        Option(
            name="message_reason",
            description="The message I'm going to send you.",
            type=OptionType.STRING,
            required=True,
        ),
        Option(
            name="weeks",
            description="Number of weeks to wait",
            type=OptionType.INTEGER,
            required=False,
        ),
        Option(
            name="days",
            description="Number of days to wait",
            type=OptionType.INTEGER,
            required=False,
        ),
        Option(
            name="hours",
            description="Number of hours to wait",
            type=OptionType.INTEGER,
            required=False,
        ),
        Option(
            name="minutes",
            description="Number of minutes to wait",
            type=OptionType.INTEGER,
            required=False,
        ),
        Option(
            name="seconds",
            description="Number of seconds to wait.",
            type=OptionType.INTEGER,
            required=False,
        ),
        Option(
            name="start_date",
            description="When to start, in the ISO 8601 format. (Example: 2010-10-10 09:30:00)",
            type=OptionType.STRING,
            required=False,
        ),
        Option(
            name="end_date",
            description="When to stop, in the ISO 8601 format. (Example: 2014-06-15 11:00:00)",
            type=OptionType.STRING,
            required=False,
        ),
        Option(
            name="timezone",
            description="Time zone to use for the date/time calculations",
            type=OptionType.STRING,
            required=False,
        ),
        Option(
            name="jitter",
            description="Delay the job execution by x seconds at most. Adds a random component to the execution time.",
            type=OptionType.INTEGER,
            required=False,
        ),
        Option(
            name="different_channel",
            description="Send the messages to a different channel.",
            type=OptionType.CHANNEL,
            required=False,
        ),
    ],
)
async def remind_interval(
        ctx: interactions.CommandContext,
        message_reason: str,
        weeks: int = 0,
        days: int = 0,
        hours: int = 0,
        minutes: int = 0,
        seconds: int = 0,
        start_date: str | None = None,
        end_date: str | None = None,
        timezone: str | None = None,
        jitter: int | None = None,
        different_channel: interactions.Channel | None = None,
):
    """Create a new reminder that triggers based on an interval.

    Args:
        ctx: Context of the slash command. Contains the guild, author and message and more.
        message_reason: The message we should write when triggered.
        weeks: Amount weeks to wait.
        days: Amount days to wait.
        hours: Amount hours to wait.
        minutes: Amount minutes to wait.
        seconds: Amount seconds to wait.
        start_date: Starting point for the interval calculation.
        end_date: Latest possible date/time to trigger on.
        timezone: Time zone to use for the date/time calculations.
        jitter: Delay the job execution by jitter seconds at most.
        different_channel: Send the messages to a different channel.
    """
    await ctx.defer()

    channel_id = int(ctx.channel_id)

    # If we should send the message to a different channel
    if different_channel:
        channel_id = int(different_channel.id)

    job = scheduler.add_job(
        send_to_discord,
        "interval",
        weeks=weeks,
        days=days,
        hours=hours,
        minutes=minutes,
        seconds=seconds,
        start_date=start_date,
        end_date=end_date,
        timezone=timezone,
        jitter=jitter,
        kwargs={
            "channel_id": channel_id,
            "message": message_reason,
            "author_id": ctx.member.id,
        },
    )

    # TODO: Add what arguments we used in the job to the message
    message = (
        f"Hello {ctx.member.name}, I will send messages to <#{channel_id}>.\n"
        f"First run in {calculate(job)} with the message:\n"
        f"**{message_reason}**."
    )

    await ctx.send(message)


async def send_to_discord(channel_id: int, message: str, author_id: int):
    """Send a message to Discord.

    Args:
        channel_id: The Discord channel ID.
        message: The message.
        author_id: User we should ping.
    """
    # TODO: Check if channel exists.
    # TODO: Send message to webhook if channel is not found.
    channel = await interactions.get(  # type: ignore
        bot,
        interactions.Channel,
        object_id=int(channel_id),
        force=interactions.Force.HTTP,  # type: ignore
    )

    await channel.send(f"<@{author_id}>\n{message}")


def start():
    """Start scheduler and log in to Discord."""
    # TODO: Add how many reminders are scheduled.
    # TODO: Make backup of jobs.sqlite before running the bot.
    logging.basicConfig(level=logging.getLevelName(log_level))
    logging.info(
        f"\nsqlite_location = {sqlite_location}\n"
        f"config_timezone = {config_timezone}\n"
        f"bot_token = {bot_token}\n"
        f"log_level = {log_level}"
    )

    scheduler.start()
    bot.start()


if __name__ == "__main__":
    start()
