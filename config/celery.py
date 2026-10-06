import os

from celery import Celery
from celery.schedules import crontab

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

app = Celery("ivhrms")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

# Runs by itself every morning once `celery -A config worker --beat` is running (see CELERY_TIMEZONE).
app.conf.beat_schedule = {
    "compliance-daily-scan": {
        "task": "apps.compliance.tasks.daily_scan",
        "schedule": crontab(hour=7, minute=0),
    },
}
