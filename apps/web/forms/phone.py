"""Phone and WhatsApp boxes: a country-code list next to the number.

The list comes from Settings > Organization (the Country table). A phone number is stored as "+973 33366235";
a WhatsApp number as digits only, "97333366235", exactly as before, so nothing that reads it has to change."""
import re

from django import forms
from django.core.exceptions import ValidationError
from django.utils.html import format_html, format_html_join
from django.utils.safestring import mark_safe

from apps.organization.models import Country

MIN_NATIONAL, MAX_NATIONAL, MAX_TOTAL = 6, 12, 15       # digits without the code; digits with it


def split_number(value, countries, bare_includes_code=False):
    """('973', '33366235') from '+973 33366235'. `bare_includes_code` says that digits without a '+' already start
    with the country code (WhatsApp numbers do). When the code cannot be told it is ''."""
    text = value.strip() if isinstance(value, str) else ""
    digits = re.sub(r"\D", "", text)
    if text.startswith("00"):
        digits = digits[2:]
    if not digits:
        return "", ""
    if text.startswith(("+", "00")) or bare_includes_code:
        for dial in sorted((c.dial_code for c in countries), key=len, reverse=True):
            if digits.startswith(dial):
                return dial, digits[len(dial):]
        return "", "+" + digits          # a country that is not in the list: shown whole, never cut or changed
    return "", digits


class PhoneWidget(forms.Widget):
    """A country-code list and a number box side by side. They post as <name>_code and <name>_number. A single
    value posted as <name> alone (an old form, a test) is passed on whole and split by the field."""

    def __init__(self, attrs=None, bare_includes_code=False):
        super().__init__(attrs)
        self.bare_includes_code = bare_includes_code

    def value_from_datadict(self, data, files, name):
        code, number = data.get(f"{name}_code"), data.get(f"{name}_number")
        if code is None and number is None:
            return data.get(name)
        return {"code": code or "", "number": number or ""}

    def value_omitted_from_data(self, data, files, name):
        return all(key not in data for key in (name, f"{name}_code", f"{name}_number"))

    def id_for_label(self, id_):
        return f"{id_}_number" if id_ else id_

    def _boxes(self, value, countries):
        if isinstance(value, dict):
            return re.sub(r"\D", "", value.get("code") or ""), value.get("number") or ""
        return split_number(value, countries, self.bare_includes_code)

    def render(self, name, value, attrs=None, renderer=None):
        attrs = self.build_attrs(self.attrs, attrs)
        field_id = attrs.get("id") or f"id_{name}"
        bad = " is-invalid" if "is-invalid" in attrs.get("class", "") else ""
        countries = list(Country.objects.all())
        code, number = self._boxes(value, countries)
        offered = [c for c in countries if c.is_active or c.dial_code == code]   # a saved code stays visible
        if not code and offered:
            code = offered[0].dial_code
        shown = next((c for c in offered if c.dial_code == code), None)
        items = format_html_join(
            "", '<button type="button" class="dropdown-item d-flex align-items-center gap-2" data-phone-pick '
                'data-code="{}" data-iso="{}" title="{}" aria-label="{}"><span class="fi fi-{} border"></span>+{}</button>',
            ((c.dial_code, c.iso or "xx", c.name + ("" if c.is_active else " (off)"), f"{c.name} +{c.dial_code}",
              c.iso or "xx", c.dial_code) for c in offered))
        describedby = (format_html(' aria-describedby="{}"', attrs["aria-describedby"])
                       if attrs.get("aria-describedby") else "")
        return format_html(
            '<div class="input-group" data-phone>'
            '<button type="button" class="btn btn-outline-secondary dropdown-toggle d-flex align-items-center gap-2" '
            'data-bs-toggle="dropdown" aria-expanded="false" aria-label="Country code">'
            '<span class="fi fi-{iso} border" data-phone-flag></span><span data-phone-dial>+{code}</span></button>'
            '<div class="dropdown-menu" style="max-height:18rem;overflow-y:auto">'
            '<input type="hidden" name="{name}_code" id="{fid}_code" value="{code}" data-phone-code>{items}</div>'
            '<input type="tel" name="{name}_number" id="{fid}_number" class="form-control{bad}" value="{number}" '
            'inputmode="tel" autocomplete="off" placeholder="Number"{required}{describedby}>'
            '</div>',
            name=name, fid=field_id, bad=bad, code=code, iso=(shown.iso if shown and shown.iso else "xx"),
            items=items, number=number,
            required=mark_safe(" required") if attrs.get("required") else "", describedby=describedby)

class PhoneField(forms.CharField):
    """`style="spaced"` stores +973 33366235 (an ordinary phone); `style="digits"` stores 97333366235 (WhatsApp)."""

    def __init__(self, *, style="spaced", **kwargs):
        self.style = style
        kwargs.setdefault("widget", PhoneWidget(bare_includes_code=(style == "digits")))
        super().__init__(**kwargs)

    def to_python(self, value):
        if isinstance(value, dict):
            return self._from_boxes(value.get("code"), value.get("number"))
        return self._from_one_value(value)

    def _from_one_value(self, value):
        """A single value (an old form, a test, an import): the rules that applied before the country list."""
        text = value.strip() if isinstance(value, str) else ""
        if not text:
            return self.empty_value
        if self.style == "digits":
            digits = re.sub(r"\D", "", text)
            if not 8 <= len(digits) <= 15:
                raise ValidationError("Enter the full number with country code, e.g. 97333123456.")
            return digits
        return text

    def _from_boxes(self, code, number):
        countries = list(Country.objects.all())
        text = (number or "").strip()
        digits = re.sub(r"\D", "", text)
        if not digits:
            return self.empty_value
        if text.startswith(("+", "00")):                      # a full international number was typed
            if text.startswith("00"):
                digits = digits[2:]
            offered = sorted((c.dial_code for c in countries if c.is_active), key=len, reverse=True)
            dial = next((d for d in offered if digits.startswith(d)), None)
            if dial is None:
                raise ValidationError("That country code is not in the list. Choose the country from the list, "
                                      "or ask an administrator to switch it on.")
            national = digits[len(dial):]
        else:
            dial = re.sub(r"\D", "", code or "")
            if dial not in {c.dial_code for c in countries}:
                raise ValidationError("Choose the country code.")
            national = digits
            if len(national) > 10 and national.startswith(dial) and len(national) - len(dial) >= MIN_NATIONAL:
                national = national[len(dial):]               # the code was typed again in the number box
        national = national.lstrip("0")                       # 0 before the number is not part of it abroad
        if not MIN_NATIONAL <= len(national) <= MAX_NATIONAL or len(dial) + len(national) > MAX_TOTAL:
            raise ValidationError("Check the number. Enter it without the country code, e.g. 3336 6235.")
        return f"{dial}{national}" if self.style == "digits" else f"+{dial} {national}"


def phone_field(**kwargs):
    return PhoneField(style="spaced", required=False, label="Phone", **kwargs)


def whatsapp_field(**kwargs):
    return PhoneField(style="digits", required=False, empty_value=None, label="WhatsApp number",
                      help_text="Optional. Used for WhatsApp messages.", **kwargs)