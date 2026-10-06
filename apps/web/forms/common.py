"""Field helpers shared by every form."""

from django import forms


def date_input(**rules):
    """A date field. Optional rules reach the date picker as data-* attributes (see static/web/datepickers.js)."""
    attrs = {"type": "date"}
    attrs.update({"data-" + key.replace("_", "-"): value for key, value in rules.items()})
    return forms.DateInput(format="%Y-%m-%d", attrs=attrs)


def time_input():
    return forms.TimeInput(format="%H:%M", attrs={"type": "time"})
