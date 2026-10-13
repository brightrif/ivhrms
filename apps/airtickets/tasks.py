from celery import shared_task


@shared_task(name="apps.airtickets.tasks.daily_sync")
def daily_sync():
    """Bring every employee's "next ticket" up to date (new joiners, grade or contract changes, policy edits)."""
    from . import services
    return services.sync_all()
