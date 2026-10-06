from celery import shared_task


@shared_task(name="apps.compliance.tasks.daily_scan")
def daily_scan():
    from . import services
    return services.run_daily_scan()
