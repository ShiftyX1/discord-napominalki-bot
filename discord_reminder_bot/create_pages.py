"""This module creates the pages for the paginator."""
from typing import List

import interactions
from apscheduler.job import Job
from apscheduler.triggers.date import DateTrigger
from interactions import ActionRow, ComponentContext
from interactions.ext.paginator import Page, Paginator, RowPosition

from discord_reminder_bot.countdown import calculate
from discord_reminder_bot.settings import scheduler


def create_pages(ctx) -> list[Page]:
    """Create pages for the paginator.

    Args:
        ctx: The context of the command.

    Returns:
        list[Page]: A list of pages.
    """
    pages = []

    jobs: List[Job] = scheduler.get_jobs()
    for job in jobs:
        channel_id = job.kwargs.get("channel_id")
        # Only add reminders from channels in the server we run "/reminder list" in
        # Check if channel is in the Discord server, if not, skip it.
        for channel in ctx.guild.channels:
            if int(channel.id) == channel_id:
                if type(job.trigger) is DateTrigger:
                    # Get trigger time for normal reminders
                    trigger_time = job.trigger.run_date
                else:
                    # Get trigger time for cron and interval jobs
                    trigger_time = job.next_run_time

                # Paused reminders returns None
                if trigger_time is None:
                    trigger_value = None
                    trigger_text = "Paused"
                else:
                    trigger_value = f'{trigger_time.strftime("%Y-%m-%d %H:%M")} (in {calculate(job)})'
                    trigger_text = trigger_value

                message = job.kwargs.get("message")
                message = (message[:98] + '..') if len(message) > 98 else message

                edit_button = interactions.Button(
                    label="Edit",
                    style=interactions.ButtonStyle.PRIMARY,
                    custom_id="edit",
                )
                pause_button = interactions.Button(
                    label="Pause",
                    style=interactions.ButtonStyle.PRIMARY,
                    custom_id="pause",
                )
                unpause_button = interactions.Button(
                    label="Unpause",
                    style=interactions.ButtonStyle.PRIMARY,
                    custom_id="unpause",
                )
                remove_button = interactions.Button(
                    label="Remove",
                    style=interactions.ButtonStyle.DANGER,
                    custom_id="remove",
                )

                embed = interactions.Embed(
                    title=f"{job.id}",
                    fields=[
                        interactions.EmbedField(
                            name=f"**Channel:**",
                            value=f"#{channel.name}",
                        ),
                        interactions.EmbedField(
                            name=f"**Message:**",
                            value=f"{message}",
                        ),
                    ],
                )

                if trigger_value is not None:
                    embed.add_field(
                        name=f"**Trigger:**",
                        value=f"{trigger_text}",
                    )
                else:
                    embed.add_field(
                        name=f"**Trigger:**",
                        value=f"_Paused_",
                    )

                components = [
                    edit_button,
                    remove_button,
                ]

                if type(job.trigger) is not DateTrigger:
                    # Get trigger time for cron and interval jobs
                    trigger_time = job.next_run_time
                    if trigger_time is None:
                        pause_or_unpause_button = unpause_button
                    else:
                        pause_or_unpause_button = pause_button
                    components.insert(1, pause_or_unpause_button)

                # Add a page to pages list
                pages.append(
                    Page(
                        embeds=embed,
                        title=message,
                        components=ActionRow(components=components),  # type: ignore
                        callback=callback,
                        position=RowPosition.BOTTOM,
                    )
                )

    return pages


async def callback(self: Paginator, ctx: ComponentContext):
    """Callback for the paginator."""
    job_id = self.component_ctx.message.embeds[0].title  # type: ignore
    job = scheduler.get_job(job_id)

    if job is None:
        return await ctx.send("Job not found.", ephemeral=True)

    channel_id = job.kwargs.get("channel_id")
    old_message = job.kwargs.get("message")

    components = [
        interactions.TextInput(  # type: ignore
            style=interactions.TextStyleType.PARAGRAPH,
            placeholder=old_message,
            label="New message",
            custom_id="new_message",
            value=old_message,
            required=False,
        ),
    ]

    if type(job.trigger) is DateTrigger:
        # Get trigger time for normal reminders
        trigger_time = job.trigger.run_date
        job_type = "normal"
        components.append(
            interactions.TextInput(  # type: ignore
                style=interactions.TextStyleType.SHORT,
                placeholder=str(trigger_time),
                label="New date, Can be human readable or ISO8601",
                custom_id="new_date",
                value=str(trigger_time),
                required=False,
            ),
        )

    else:
        # Get trigger time for cron and interval jobs
        trigger_time = job.next_run_time
        job_type = "cron/interval"

    if ctx.custom_id == "edit":
        await self.end_paginator()
        modal = interactions.Modal(
            title=f"Edit {job_type} reminder.",
            custom_id="edit_modal",
            components=components,  # type: ignore
        )
        await ctx.popup(modal)

    elif ctx.custom_id == "pause":
        await self.end_paginator()
        # TODO: Add unpause button if user paused the wrong job
        scheduler.pause_job(job_id)
        await ctx.send(f"Job {job_id} paused.")

    elif ctx.custom_id == "unpause":
        await self.end_paginator()
        # TODO: Add pause button if user unpauses the wrong job
        scheduler.resume_job(job_id)
        await ctx.send(f"Job {job_id} unpaused.")

    elif ctx.custom_id == "remove":
        await self.end_paginator()
        # TODO: Add recreate button if user removed the wrong job
        scheduler.remove_job(job_id)
        await ctx.send(
            f"Job {job_id} removed.\n"
            f"**Message:** {old_message}\n"
            f"**Channel:** {channel_id}\n"
            f"**Time:** {trigger_time}"
        )
