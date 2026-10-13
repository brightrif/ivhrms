from django import forms
from django.utils.html import format_html, format_html_join
from django.utils.safestring import mark_safe

from apps.airtickets.nationalities import canonical, known_names


class NationalityInput(forms.TextInput):
    """A text box that suggests the known nationalities as you type. Anything can still be typed."""
    list_id = "nationality-options"

    def __init__(self, attrs=None):
        super().__init__({"list": self.list_id, "autocomplete": "off", **(attrs or {})})

    def render(self, name, value, attrs=None, renderer=None):
        options = format_html_join("", '<option value="{}">', ((n,) for n in known_names()))
        return mark_safe(super().render(name, value, attrs, renderer)
                         + format_html('<datalist id="{}">{}</datalist>', self.list_id, options))


class NationalityMixin:
    """Put first among a form's bases. 'india ' and 'India' are saved as 'Indian'; something not in the list is kept
    as typed (it shows as 'not recognised' on the Air Tickets page)."""

    def clean_nationality(self):
        raw = " ".join((self.cleaned_data.get("nationality") or "").split())
        return canonical(raw) or raw
