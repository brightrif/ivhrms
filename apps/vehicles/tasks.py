from celery import shared_task


@shared_task(name="apps.vehicles.tasks.daily_service_scan")
def daily_service_scan():
    """The one daily vehicle job: service alerts, loan installment alerts and the notices about vehicles held by people
    who are leaving. (Kept under its phase 3 name so the Celery schedule does not change.)"""
    from . import financing, handovers, maintenance
    return {"service": maintenance.run_service_scan(), "loans": financing.run_loan_scan(),
            "leavers": handovers.run_leaver_scan()}
