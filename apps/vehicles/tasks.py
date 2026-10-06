from celery import shared_task


@shared_task(name="apps.vehicles.tasks.daily_service_scan")
def daily_service_scan():
    from . import maintenance
    return maintenance.run_service_scan()
