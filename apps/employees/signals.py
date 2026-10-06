from django.dispatch import Signal

# Sent inside the creating transaction. kwargs: employee, shift (optional)
employee_onboarded = Signal()