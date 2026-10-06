from django import forms, template
from django.utils.html import format_html

register = template.Library()

# One place that decides the colour of every status badge in the app
STATUS_TONES = {
    "present": "success", "approved": "success", "paid_leave": "success", "active": "success",
    "late_entry": "warning", "early_exit": "warning", "half_day": "warning",
    "pending": "warning", "on_notice": "warning",
    "absent": "danger", "rejected": "danger", "unpaid_leave": "danger",
    "holiday": "secondary", "weekly_off": "secondary", "cancelled": "secondary", "separated": "secondary",
    # compliance
    "valid": "success", "completed": "success",
    "due": "warning",
    "expired": "danger",
    "open": "primary",
}


@register.filter
def bound(form, name):
    """{{ form|bound:"expiry_date" }} -> that field, or None. Lets one template lay out any form."""
    try:
        return form[name]
    except KeyError:
        return None


@register.filter
def bs(bound_field):
    """Render a form field's widget with the matching Bootstrap class."""
    widget = bound_field.field.widget
    if isinstance(widget, forms.CheckboxInput):
        css = "form-check-input"
    elif isinstance(widget, (forms.RadioSelect, forms.CheckboxSelectMultiple)):
        css = ""
    elif isinstance(widget, forms.Select):          # also covers SelectMultiple
        css = "form-select"
    else:
        css = "form-control"
    if bound_field.errors:
        css += " is-invalid"
    existing = widget.attrs.get("class", "")
    return bound_field.as_widget(attrs={"class": f"{existing} {css}".strip()})


@register.simple_tag
def status_badge(code, label=None):
    """{% status_badge obj.status obj.get_status_display %} -> a soft coloured badge."""
    tone = STATUS_TONES.get(code, "secondary")
    text = label if label is not None else str(code).replace("_", " ").capitalize()
    return format_html(
        '<span class="badge rounded-pill bg-{0}-subtle text-{0}-emphasis border border-{0}-subtle">{1}</span>',
        tone, text)
