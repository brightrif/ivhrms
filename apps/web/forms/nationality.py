from django import forms
from django.core.exceptions import ValidationError
from django.utils.html import format_html, format_html_join
from django.utils.safestring import mark_safe

from apps.airtickets.nationalities import canonical, known_names
from apps.organization.models import Country


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


def _saved_as(country):
    """What is saved for a country: its nationality as Air Tickets knows it (India -> Indian), else its name."""
    return canonical(country.name) or country.name


class NationalityWidget(forms.Widget):
    """A box with the countries switched on in Settings > Organization listed under it as you type. The browser
    narrows the list (nationality.js). A saved nationality such as "Indian" shows as "India"."""

    def render(self, name, value, attrs=None, renderer=None):
        attrs = self.build_attrs(self.attrs, attrs)
        field_id = attrs.get("id") or f"id_{name}"
        bad = " is-invalid" if "is-invalid" in attrs.get("class", "") else ""
        countries = list(Country.objects.all())
        shown = value or ""
        for c in countries:
            if shown.casefold() == _saved_as(c).casefold():
                shown = c.name
                break
        rows = format_html_join(
            "", '<button type="button" class="list-group-item list-group-item-action py-1" data-nationality-pick '
                'data-name="{}" hidden><span class="d-flex align-items-center gap-2">'
                '<span class="fi fi-{} border"></span>{}</span></button>',
            ((c.name, c.iso or "xx", c.name) for c in sorted(countries, key=lambda c: c.name) if c.is_nationality))
        return format_html(
            '<div class="position-relative" data-nationality>'
            '<input type="text" name="{name}" id="{fid}" class="form-control{bad}" value="{value}" '
            'data-saved="{value}" autocomplete="off" placeholder="Type to search" role="combobox" '
            'aria-autocomplete="list" aria-expanded="false" data-nationality-text>'
            '<div class="list-group position-absolute w-100 shadow-sm" data-nationality-list hidden '
            'style="z-index:1050;max-height:260px;overflow-y:auto">{rows}'
            '<div class="list-group-item small text-body-secondary" data-nationality-none hidden>'
            'No match. Ask an administrator to switch the country on in Settings.</div></div></div>',
            name=name, fid=field_id, bad=bad, value=shown, rows=rows)


class NationalityField(forms.CharField):
    """Accepts only a country switched on for nationality and returns it as Air Tickets knows it (India -> Indian).
    A value already saved on the record stays valid (set `keep`), so editing other details never loses an older entry."""

    def __init__(self, **kwargs):
        kwargs.setdefault("required", False)
        kwargs.setdefault("max_length", 60)
        kwargs.setdefault("widget", NationalityWidget())
        super().__init__(**kwargs)
        self.keep = ""

    def to_python(self, value):
        text = super().to_python(value)
        if not text:
            return text
        for country in Country.objects.filter(is_nationality=True):
            if country.name.casefold() == text.casefold():
                return _saved_as(country)
        if self.keep:
            kept = {self.keep.casefold()}
            kept |= {c.name.casefold() for c in Country.objects.all() if _saved_as(c).casefold() == self.keep.casefold()}
            if text.casefold() in kept:
                return self.keep
        raise ValidationError("Choose a country from the list.")