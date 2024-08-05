import logging
from typing import TYPE_CHECKING

import interactions
from apscheduler import events
from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_MISSED, JobExecutionEvent
from apscheduler.jobstores.base import JobLookupError
from apscheduler.triggers.date import DateTrigger
from discord_webhook import DiscordWebhook
from interactions import (
    Channel,
    Client,
    CommandContext,
    Embed,
    Member,
    Message,
    OptionType,
    autodefer,
)
from interactions.ext.paginator import Page, Paginator

from discord_reminder_bot import settings
from discord_reminder_bot.countdown import calculate
from discord_reminder_bot.create_pages import create_pages
from discord_reminder_bot.parse import ParsedTime, parse_time
from discord_reminder_bot.settings import (
    bot_token,
    config_timezone,
    log_level,
    scheduler,
    sqlite_location,
    webhook_url,
)

if TYPE_CHECKING:
    from datetime import datetime

    from apscheduler.job import Job

bot: Client = interactions.Client(token=bot_token)


def send_webhook(
    url: str = webhook_url,
    message: str = "discord-reminder-bot: Empty message.",
) -> None:
    """Send a webhook to Discord.

    Args:
        url: Our webhook url, defaults to the one from settings.
        message: The message that will be sent to Discord.
    """
    if not url:
        msg = "ERROR: Tried to send a webhook but you have no webhook url configured."
        logging.error(msg)
        webhook: DiscordWebhook = DiscordWebhook(url=settings.webhook_url, content=msg, rate_limit_retry=True)
        webhook.execute()
        return

    webhook: DiscordWebhook = DiscordWebhook(url=url, content=message, rate_limit_retry=True)
    webhook.execute()


@bot.command(name="remind")
async def base_command(ctx: interactions.CommandContext) -> None:  # noqa: ARG001
    """This is the base command for the reminder bot."""


@bot.modal("edit_modal")
async def modal_response_edit(ctx: CommandContext, *response: str) -> Message:  # noqa: C901, PLR0912, PLR0911
    """This is what gets triggered when the user clicks the Edit button in /reminder list.

    Args:
        ctx: Context of the slash command. Contains the guild, author and message and more.
        response: The response from the modal.

    Returns:
        A Discord message with changes.
    """
    if not ctx.message:
        return await ctx.send(
            "Сообщение которое триггернуло этот модаль. Или произошло нечто другое...",
            ephemeral=True,
        )

    job_id: str | None = ctx.message.embeds[0].title
    old_date: str | None = None
    old_message: str | None = None

    try:
        job: Job | None = scheduler.get_job(job_id)
    except JobLookupError as e:
        return await ctx.send(
            f"Неудалось получить задачу после модаля.\nJob ID: {job_id}\nОшибка: {e}",
            ephemeral=True,
        )

    if job is None:
        return await ctx.send("Задача не найдена.", ephemeral=True)

    if not response:
        return await ctx.send("Не найдено изменений.", ephemeral=True)

    if type(job.trigger) is DateTrigger:
        new_message: str | None = response[0]
        new_date: str | None = response[1]
    else:
        new_message = response[0]
        new_date = None

    message_embeds: list[Embed] = ctx.message.embeds
    for embeds in message_embeds:
        if embeds.fields is None:
            return await ctx.send("Не найдено полей в embed'е.", ephemeral=True)

        for field in embeds.fields:
            if field.name == "**Channel:**":
                continue
            if field.name == "**Message:**":
                old_message = field.value
            if field.name == "**Trigger:**":
                old_date = field.value
            else:
                return await ctx.send(
                    f"Неизвестное имя поля ({field.name}).",
                    ephemeral=True,
                )

    msg: str = f"Измененная задача {job_id}.\n"
    if old_date is not None and new_date:
        # Parse the time/date we got from the command.
        parsed: ParsedTime = parse_time(date_to_parse=new_date)
        if parsed.err:
            return await ctx.send(parsed.err_msg)
        parsed_date: datetime | None = parsed.parsed_time

        if parsed_date is None:
            return await ctx.send(f"Неудалось спарсить дату. ({new_date})")

        date_new: str = parsed_date.strftime("%Y-%m-%d %H:%M:%S")

        new_job: Job = scheduler.reschedule_job(job.id, run_date=date_new)
        new_time: str = calculate(new_job)

        # TODO: old_date and date_new has different precision.
        # Old date: 2032-09-18 00:07
        # New date: 2032-09-18 00:07:13
        msg += f"**Предыдущая дата**: {old_date}\n**Новая дата**: {date_new} (в {new_time})\n"

    if old_message is not None:
        channel_id: int = job.kwargs.get("channel_id")
        job_author_id: int = job.kwargs.get("author_id")
        try:
            scheduler.modify_job(
                job.id,
                kwargs={
                    "channel_id": channel_id,
                    "message": f"{new_message}",
                    "author_id": job_author_id,
                },
            )
        except JobLookupError as e:
            return await ctx.send(
                f"Не удалось изменить задачу.\nJob ID: {job_id}\nError: {e}",
                ephemeral=True,
            )
        msg += f"**Старое сообщение**: {old_message}\n**Новое сообщение**: {new_message}\n"

    return await ctx.send(msg)


@autodefer()
@bot.command(name="parse", description="Спарсить время из строки")  # type: ignore  # noqa: PGH003
@interactions.option(
    name="time_to_parse",
    description="Строка которую хочешь распарсить.",
    type=OptionType.STRING,
    required=True,
)
@interactions.option(
    name="optional_timezone",
    description="Опционально: часовой пояс, например Europe/Moscow",
    type=OptionType.STRING,
    required=False,
)
async def parse_command(
    ctx: interactions.CommandContext,
    time_to_parse: str,
    optional_timezone: str | None = None,
) -> Message:
    """Find the date and time from a string.

    Args:
        ctx: Context of the slash command. Contains the guild, author and message and more.
        time_to_parse: The string you want to parse.
        optional_timezone: Optional time zone, for example Europe/Stockholm.
    """
    if optional_timezone:
        parsed: ParsedTime = parse_time(date_to_parse=time_to_parse, timezone=optional_timezone)
    else:
        parsed = parse_time(date_to_parse=time_to_parse)
    if parsed.err:
        return await ctx.send(parsed.err_msg)
    parsed_date: datetime | None = parsed.parsed_time

    if parsed_date is None:
        return await ctx.send(f"Не удалось распарсить дату. ({time_to_parse})")

    # Locale`s appropriate date and time representation.
    locale_time: str = parsed_date.strftime("%c")
    run_date: str = parsed_date.strftime("%Y-%m-%d %H:%M:%S")
    return await ctx.send(
        f"**Строка**: {time_to_parse}\n"
        f"**Спаршенная дата**: {parsed_date}\n"
        f"**Форматированная**: {run_date}\n"
        f"**Время**: {locale_time}\n",
    )


@autodefer()
@base_command.subcommand(
    name="list",
    description="Показать, заморозить, разморозить, и удалить напоминалки.",
)
async def list_command(ctx: interactions.CommandContext) -> Message | None:
    """List, pause, unpause, and remove reminders.

    Args:
        ctx: Context of the slash command. Contains the guild, author and message and more.
    """
    pages: list[Page] = await create_pages(ctx)
    if not pages:
        return await ctx.send("Не нашел напоминалок.", ephemeral=True)

    if len(pages) == 1:
        for page in pages:
            return await ctx.send(
                content="Я не сделал поддержку кнопок если присутствует только одна напоминалка, "
                "поэтому добавь еще одну или измени/удали эту 🙃",
                embeds=page.embeds,
            )

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
    return None


@autodefer()
@base_command.subcommand(name="add", description="Поставить напоминалку.")
@interactions.option(
    name="message_reason",
    description="Сообщение которое я отправлю тебе.",
    type=OptionType.STRING,
    required=True,
)
@interactions.option(
    name="message_date",
    description="Дата в которую отправить сообщение.",
    type=OptionType.STRING,
    required=True,
)
@interactions.option(
    name="different_channel",
    description="Канал в который нужно отправить сообщение.",
    type=OptionType.CHANNEL,
    required=False,
)
@interactions.option(
    name="send_dm_to_user",
    description="Отправить сообщение пользаку в личку вместо канала. Укажи both_dm_and_channel чтобы отправить и туда и туда.",
    type=OptionType.USER,
    required=False,
)
@interactions.option(
    name="both_dm_and_channel",
    description="Отправка и личного сообщения и сообщения в канал, необходимо указать send_dm_to_user если хочешь отправить и туда и туда.",
    type=OptionType.BOOLEAN,
    required=False,
)
async def command_add(  # noqa: PLR0913
    ctx: interactions.CommandContext,
    message_reason: str,
    message_date: str,
    different_channel: interactions.Channel | None = None,
    send_dm_to_user: interactions.User | None = None,
    both_dm_and_channel: bool | None = None,
) -> Message | None:
    """Add a new reminder. You can add a date and message.

    Args:
        ctx: Context of the slash command. Contains the guild, author and message and more.
        message_date: The parsed date and time when you want to get reminded.
        message_reason: The message the bot should write when the reminder is triggered.
        different_channel: The channel the reminder should be sent to.
        send_dm_to_user: Send the message to the user via DM instead of the channel.
        both_dm_and_channel: If we should send both a DM and a message to the channel. Works with different_channel.
    """
    # Parse the time/date we got from the command.
    parsed: ParsedTime = parse_time(date_to_parse=message_date)
    if parsed.err:
        return await ctx.send(parsed.err_msg)
    parsed_date: datetime | None = parsed.parsed_time

    if parsed_date is None:
        return await ctx.send(f"Не удалось распарсить дату. ({message_date})")

    run_date: str = parsed_date.strftime("%Y-%m-%d %H:%M:%S")

    # If we should send the message to a different channel
    channel_id = int(ctx.channel_id)
    if different_channel:
        channel_id = int(different_channel.id)

    dm_message: str = ""
    where_and_when = "Ты бля ваще не должен видеть это сообщение, это баг. Если увидел, то Лучше звони Shifty. :-)"
    should_send_channel_reminder = True
    try:
        if send_dm_to_user:
            dm_reminder: Job = scheduler.add_job(
                send_to_user,
                run_date=run_date,
                kwargs={
                    "user_id": int(send_dm_to_user.id),
                    "guild_id": ctx.guild_id,
                    "message": message_reason,
                },
            )
            dm_message = f"and a DM to {send_dm_to_user.username} "
            if not both_dm_and_channel:
                # If we should send the message to the channel too instead of just a DM.
                should_send_channel_reminder = False
                where_and_when: str = (
                    f"Я отправлю сообщение в личку {send_dm_to_user.username}:\n"
                    f"**{run_date}** (в {calculate(dm_reminder)})\n"
                )
        if ctx.member is None:
            return await ctx.send("Что-то пошло не так при получении участника, проверь 'guild' в дискорде.", ephemeral=True)

        if should_send_channel_reminder:
            reminder: Job = scheduler.add_job(
                send_to_discord,
                run_date=run_date,
                kwargs={
                    "channel_id": channel_id,
                    "message": message_reason,
                    "author_id": ctx.member.id,
                },
            )
            where_and_when = (
                f"Я напомню тебе в <#{channel_id}> {dm_message}:\n**{run_date}** (в {calculate(reminder)})\n"
            )

    except ValueError as e:
        await ctx.send(str(e), ephemeral=True)
        return None

    message: str = f"Привет {ctx.member.name}, {where_and_when}С сообщением:\n**{message_reason}**."
    await ctx.send(message)
    return None


async def send_to_user(user_id: int, guild_id: int, message: str) -> None:
    """Send a message to a user via DM.

    Args:
        user_id: The user ID to send the message to.
        guild_id: The guild ID to get the user from.
        message: The message to send.
    """
    member: Member = await interactions.get(
        bot,
        interactions.Member,
        parent_id=guild_id,
        object_id=user_id,
        force="http",
    )
    await member.send(message)


@autodefer()
@base_command.subcommand(
    name="cron",
    description="Triggers when current time matches all specified time constraints, similarly to the UNIX cron.",
)
@interactions.option(
    name="message_reason",
    description="Сообщение которое я тебе отправлю.",
    type=OptionType.STRING,
    required=True,
)
@interactions.option(
    name="year",
    description="4-значное указание года. (Пример: 2042)",
    type=OptionType.STRING,
    required=False,
)
@interactions.option(
    name="month",
    description="Месяц. (от 1 до 12)",
    type=OptionType.STRING,
    required=False,
)
@interactions.option(
    name="day",
    description="Число месяца (от 1 до 31)",
    type=OptionType.STRING,
    required=False,
)
@interactions.option(
    name="week",
    description="ISO неделя (от 1 до 53)",
    type=OptionType.STRING,
    required=False,
)
@interactions.option(
    name="day_of_week",
    description="Номер или название дня недели (от 0 до 6 или mon,tue,wed,thu,fri,sat,sun).",
    type=OptionType.STRING,
    required=False,
)
@interactions.option(
    name="hour",
    description="Час (от 0 до 23)",
    type=OptionType.STRING,
    required=False,
)
@interactions.option(
    name="minute",
    description="Минуты (от 0 до 59)",
    type=OptionType.STRING,
    required=False,
)
@interactions.option(
    name="second",
    description="Секунды (от 0 до 59)",
    type=OptionType.STRING,
    required=False,
)
@interactions.option(
    name="start_date",
    description="Самое раннее возможное время чтобы начать напоминалку, в формате ISO 8601. (Пример: 2010-10-10 09:30:00)",
    type=OptionType.STRING,
    required=False,
)
@interactions.option(
    name="end_date",
    description="Самое последнее время когда включить напоминалку, в формате ISO 8601. (Пример: 2010-10-10 09:30:00)",
    type=OptionType.STRING,
    required=False,
)
@interactions.option(
    name="timezone",
    description="Часовой пояс который нужно использовать для вычислений даты/времени (по умолчанию scheduler_timezone)",
    type=OptionType.STRING,
    required=False,
)
@interactions.option(
    name="jitter",
    description="Задержка до выполнения задачи на x секунд в качестве наиболее высокой задержки. Добавляет компонент рандома в выполнение задачи.",
    type=OptionType.INTEGER,
    required=False,
)
@interactions.option(
    name="different_channel",
    description="Отправить сообщение в другой канал.",
    type=OptionType.CHANNEL,
    required=False,
)
@interactions.option(
    name="send_dm_to_user",
    description="Отправить сообщение пользователю в личку вместо канала. Устанавливай both_dm_and_channel чтобы отправить и туда и туда.",
    type=OptionType.USER,
    required=False,
)
@interactions.option(
    name="both_dm_and_channel",
    description="Отправляет и в личку и в канал, нужно установить send_dm_to_user если хочешь отправить и туда и туда.",
    type=OptionType.BOOLEAN,
    required=False,
)
async def remind_cron(  # noqa: PLR0913
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
    send_dm_to_user: interactions.User | None = None,
    both_dm_and_channel: bool | None = None,
) -> None:
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
        send_dm_to_user: Send the message to the user via DM instead of the channel.
        both_dm_and_channel: If we should send both a DM and a message to the channel.
    """
    # If we should send the message to a different channel
    channel_id = int(ctx.channel_id)
    if different_channel:
        channel_id = int(different_channel.id)

    dm_message: str = ""
    where_and_when = "Ты бля ваще не должен видеть это сообщение, это баг. Если увидел, то Лучше звони Shifty. :-)"
    should_send_channel_reminder = True
    try:
        if send_dm_to_user:
            dm_reminder: Job = scheduler.add_job(
                send_to_user,
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
                    "user_id": int(send_dm_to_user.id),
                    "guild_id": ctx.guild_id,
                    "message": message_reason,
                },
            )
            dm_message = f" and a DM to {send_dm_to_user.username}"
            if not both_dm_and_channel:
                # If we should send the message to the channel too instead of just a DM.
                should_send_channel_reminder = False
                where_and_when: str = (
                    f"I will send a DM to {send_dm_to_user.username} at:\n"
                    f"First run in {calculate(dm_reminder)} with the message:\n"
                )
        if ctx.member is None:
            await ctx.send("Ошибка при получении участника из контекста сообщения. Ты уверен что ты на сервере?", ephemeral=True)
            return
        if should_send_channel_reminder:
            job: Job = scheduler.add_job(
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
            where_and_when = (
                f" Я отправлю сообщения в <#{channel_id}>{dm_message}.\n"
                f"Первый запуск {calculate(job)} с сообщением:\n"
            )

    except ValueError as e:
        await ctx.send(str(e), ephemeral=True)
        return

    # TODO: Add what arguments we used in the job to the message
    message: str = f"Привет {ctx.member.name}, {where_and_when} **{message_reason}**."
    await ctx.send(message)


@base_command.subcommand(
    name="interval",
    description="Schedules messages to be run periodically, on selected intervals.",
)
@interactions.option(
    name="message_reason",
    description="The message I'm going to send you.",
    type=OptionType.STRING,
    required=True,
)
@interactions.option(
    name="weeks",
    description="Number of weeks to wait",
    type=OptionType.INTEGER,
    required=False,
)
@interactions.option(
    name="days",
    description="Number of days to wait",
    type=OptionType.INTEGER,
    required=False,
)
@interactions.option(
    name="hours",
    description="Number of hours to wait",
    type=OptionType.INTEGER,
    required=False,
)
@interactions.option(
    name="minutes",
    description="Number of minutes to wait",
    type=OptionType.INTEGER,
    required=False,
)
@interactions.option(
    name="seconds",
    description="Number of seconds to wait",
    type=OptionType.INTEGER,
    required=False,
)
@interactions.option(
    name="start_date",
    description="When to start, in the ISO 8601 format. (Example: 2010-10-10 09:30:00)",
    type=OptionType.STRING,
    required=False,
)
@interactions.option(
    name="end_date",
    description="When to stop, in the ISO 8601 format. (Example: 2014-06-15 11:00:00)",
    type=OptionType.STRING,
    required=False,
)
@interactions.option(
    name="timezone",
    description="Time zone to use for the date/time calculations",
    type=OptionType.STRING,
    required=False,
)
@interactions.option(
    name="jitter",
    description="Delay the job execution by x seconds at most. Adds a random component to the execution time.",
    type=OptionType.INTEGER,
    required=False,
)
@interactions.option(
    name="different_channel",
    description="Send the messages to a different channel.",
    type=OptionType.CHANNEL,
    required=False,
)
@interactions.option(
    name="send_dm_to_user",
    description="Send message to a user via DM instead of a channel. Set both_dm_and_channel to send both.",
    type=OptionType.USER,
    required=False,
)
@interactions.option(
    name="both_dm_and_channel",
    description="Send both DM and message to the channel, needs send_dm_to_user to be set if you want both.",
    type=OptionType.BOOLEAN,
    required=False,
)
async def remind_interval(  # noqa: PLR0913
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
    send_dm_to_user: interactions.User | None = None,
    both_dm_and_channel: bool | None = None,
) -> None:
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
        send_dm_to_user: Send the message to the user via DM instead of the channel.
        both_dm_and_channel: If we should send both a DM and a message to the channel.
    """
    # If we should send the message to a different channel
    channel_id = int(ctx.channel_id)
    if different_channel:
        channel_id = int(different_channel.id)

    dm_message: str = ""
    where_and_when = "Ты бля ваще не должен видеть это сообщение, это баг. Если увидел, то Лучше звони Shifty. :-)"
    should_send_channel_reminder = True
    try:
        if send_dm_to_user:
            dm_reminder: Job = scheduler.add_job(
                send_to_user,
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
                    "user_id": int(send_dm_to_user.id),
                    "guild_id": ctx.guild_id,
                    "message": message_reason,
                },
            )
            dm_message = f"and a DM to {send_dm_to_user.username} "
            if not both_dm_and_channel:
                # If we should send the message to the channel too instead of just a DM.
                should_send_channel_reminder = False
                where_and_when: str = (
                    f"Я отправлю сообщение в личку {send_dm_to_user.username}:\n"
                    f"Начну в {calculate(dm_reminder)} с сообщением:\n"
                )
        if ctx.member is None:
            await ctx.send("Не удалось получить участника который отправил команду.", ephemeral=True)
            return

        if should_send_channel_reminder:
            job: Job = scheduler.add_job(
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
            where_and_when = (
                f" Я буду отправлять сообщения в <#{channel_id}>{dm_message}.\n"
                f"Первая отправка {calculate(job)} с сообщением:"
            )

    except ValueError as e:
        await ctx.send(str(e), ephemeral=True)
        return

    # TODO: Add what arguments we used in the job to the message
    message: str = f"Привет {ctx.member.name}\n{where_and_when}\n**{message_reason}**."

    await ctx.send(message)


def my_listener(event: JobExecutionEvent) -> None:
    """This gets called when something in APScheduler happens."""
    if event.code == events.EVENT_JOB_MISSED:
        # TODO: Is it possible to get the message?
        scheduled_time: str = event.scheduled_run_time.strftime("%Y-%m-%d %H:%M:%S")
        msg: str = f"Job {event.job_id} was missed! Was scheduled at {scheduled_time}"
        send_webhook(message=msg)

    if event.exception:
        send_webhook(
            f"discord-reminder-bot failed to send message to Discord\n{event}",
        )


async def send_to_discord(channel_id: int, message: str, author_id: int) -> None:
    """Send a message to Discord.

    Args:
        channel_id: The Discord channel ID.
        message: The message.
        author_id: User we should ping.
    """
    channel: Channel = await interactions.get(
        bot,
        interactions.Channel,
        object_id=channel_id,
        force=interactions.Force.HTTP,
    )

    await channel.send(f"<@{author_id}>\n{message}")


def start() -> None:
    """Start scheduler and log in to Discord."""
    # TODO: Add how many reminders are scheduled.
    # TODO: Make backup of jobs.sqlite before running the bot.
    logging.basicConfig(level=logging.getLevelName(log_level))
    logging.info(
        "\nsqlite_location = %s\nconfig_timezone = %s\nlog_level = %s" % (sqlite_location, config_timezone, log_level),
    )
    scheduler.start()
    scheduler.add_listener(my_listener, EVENT_JOB_MISSED | EVENT_JOB_ERROR)
    bot.start()


if __name__ == "__main__":
    start()
