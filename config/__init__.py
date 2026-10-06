# Load the Celery app when Django starts so @shared_task functions are registered.
try:
    from .celery import app as celery_app
except ModuleNotFoundError as exc:      # Celery is not installed: the scan still works via the management command
    if exc.name != "celery":
        raise
    celery_app = None

__all__ = ("celery_app",)