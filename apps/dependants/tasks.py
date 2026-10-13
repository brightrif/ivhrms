from celery import shared_task


@shared_task(name="apps.dependants.tasks.daily_scan")
def daily_scan():
    """The one daily family job: tell HR about family visas whose sponsor is on notice or has left. (Visa, CPR and
    passport expiries are already alerted by the compliance scan.)"""
    from . import leavers
    return {"leavers": leavers.run_leaver_scan()}
