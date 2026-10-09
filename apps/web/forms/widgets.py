from django import forms
from django.utils.html import format_html


class TypeaheadWidget(forms.Widget):
    """A text box that lists matches from `url` while the person types. The chosen row's id goes in a hidden
    input, so the ModelChoiceField underneath still validates it. The text box is named <field>_text."""

    def __init__(self, url, placeholder="", attrs=None):
        super().__init__(attrs)
        self.url = url
        self.placeholder = placeholder
        self.typed = ""                    # what was typed, when the form is sent back with an error
        self.choices = []                  # ModelChoiceField fills this in

    def render(self, name, value, attrs=None, renderer=None):
        attrs = self.build_attrs(self.attrs, attrs)
        field_id = attrs.get("id", f"id_{name}")
        label = ""
        if value not in (None, ""):
            label = next((str(text) for key, text in self.choices if str(key) == str(value)), "")
        return format_html(
            '<div class="position-relative" data-typeahead data-typeahead-url="{url}">'
            '<input type="hidden" name="{name}" id="{id}_value" value="{value}" data-typeahead-value>'
            '<input type="text" name="{name}_text" id="{id}" class="form-control" autocomplete="off" '
            'placeholder="{placeholder}" value="{text}" data-typeahead-text role="combobox" '
            'aria-autocomplete="list" aria-expanded="false">'
            '<div class="list-group position-absolute w-100 shadow-sm" data-typeahead-list hidden '
            'style="z-index:1050;max-height:260px;overflow-y:auto"></div></div>',
            url=self.url, name=name, id=field_id, value=value or "",
            placeholder=self.placeholder, text=label or self.typed)