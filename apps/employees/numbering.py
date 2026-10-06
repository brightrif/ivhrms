from django.db import connection
from django.db.models import F

from .models import Employee, EmployeeNumberSequence


def format_number(sequence, number):
    return f"{sequence.prefix}{number:0{sequence.padding}d}"


def _defaults(company, worker_type):
    series = "L" if worker_type == Employee.WorkerType.LABOR else ""
    return {"prefix": f"{company.code}-{series}", "padding": 4, "next_number": 1}


def _taken(company, candidate):
    return Employee.objects.filter(company=company, employee_no=candidate).exists()


def next_number(company, worker_type):
    """Allocate the next free employee number. Must run inside the transaction that saves the employee."""
    if not connection.in_atomic_block:
        raise RuntimeError("next_number() must be called inside a transaction.")
    seq, _ = EmployeeNumberSequence.objects.get_or_create(
        company=company, worker_type=worker_type, defaults=_defaults(company, worker_type))
    while True:
        # UPDATE takes a row lock on PostgreSQL, so concurrent callers queue up and never get the same value
        EmployeeNumberSequence.objects.filter(pk=seq.pk).update(next_number=F("next_number") + 1)
        seq.refresh_from_db(fields=["prefix", "padding", "next_number"])
        candidate = format_number(seq, seq.next_number - 1)
        if not _taken(company, candidate):          # skip numbers already used by legacy or imported staff
            return candidate


def peek(company, worker_type):
    """The number the next employee will probably get. Read-only: reserves nothing, creates nothing."""
    seq = EmployeeNumberSequence.objects.filter(company=company, worker_type=worker_type).first()
    if seq is None:
        seq = EmployeeNumberSequence(company=company, worker_type=worker_type,
                                     **_defaults(company, worker_type))
    number = seq.next_number
    while _taken(company, format_number(seq, number)):
        number += 1
    return format_number(seq, number)