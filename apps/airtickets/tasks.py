from celery import shared_task


@shared_task(name="apps.airtickets.tasks.daily_sync")
def daily_sync():
    """The one daily air ticket job: bring every employee's "next ticket" up to date, then send the reminders."""
    from . import reminders, services
    return {"sync": services.sync_all(), "reminders": reminders.run_reminders()}
