from django import forms
from django.utils.html import format_html
from django.utils.safestring import mark_safe

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

_STAFF_SEARCH_JS = """
<script>
(function () {
  document.querySelectorAll("[data-typeahead]:not([data-typeahead-ready])").forEach(function (box) {
    box.setAttribute("data-typeahead-ready", "1");
    var text = box.querySelector("[data-typeahead-text]"),
        value = box.querySelector("[data-typeahead-value]"),
        list = box.querySelector("[data-typeahead-list]"),
        follows = box.dataset.typeaheadExtra ? document.getElementById(box.dataset.typeaheadExtra) : null,
        emptyText = box.dataset.typeaheadEmpty || "No staff member matches",
        chosen = value.value ? text.value : "", timer, items = [], active = -1;

    function changed() { value.dispatchEvent(new Event("change", { bubbles: true })); }
    function close() {
      list.hidden = true; list.innerHTML = ""; items = []; active = -1;
      text.setAttribute("aria-expanded", "false");
    }
    function choose(item) {
      text.value = item.name; chosen = item.name; value.value = item.id; close(); changed();
    }
    function mark() {
      Array.prototype.forEach.call(list.children, function (el, i) { el.classList.toggle("active", i === active); });
    }
    function show(results) {
      list.innerHTML = ""; items = results; active = -1;
      if (!results.length) {
        var none = document.createElement("div");
        none.className = "list-group-item small text-body-secondary";
        none.textContent = emptyText;
        list.appendChild(none);
      }
      results.forEach(function (item) {
        var row = document.createElement("button");
        row.type = "button"; row.className = "list-group-item list-group-item-action py-2";
        row.textContent = item.name;
        if (item.code) {
          var code = document.createElement("small");
          code.className = "text-body-secondary ms-2"; code.textContent = item.code;
          row.appendChild(code);
        }
        row.addEventListener("mousedown", function (e) { e.preventDefault(); choose(item); });
        list.appendChild(row);
      });
      list.hidden = false; text.setAttribute("aria-expanded", "true");
    }
    function search() {
      var q = text.value.trim();
      if (value.value && q === chosen) q = "";               // just opened on the chosen one: list from the top
      var url = box.dataset.typeaheadUrl + "?q=" + encodeURIComponent(q);
      if (follows && follows.value) url += "&type=" + encodeURIComponent(follows.value);
      fetch(url)
        .then(function (r) { return r.ok ? r.json() : { results: [] }; })
        .then(function (data) { if (document.activeElement === text) show(data.results); });
    }
    if (follows) {                                          // a different document type means a different list
      follows.addEventListener("change", function () {
        if (value.value) { value.value = ""; chosen = ""; text.value = ""; changed(); }
      });
    }
    text.addEventListener("focus", function () { text.select(); search(); });
    text.addEventListener("input", function () {
      if (value.value) { value.value = ""; chosen = ""; changed(); }
      clearTimeout(timer); timer = setTimeout(search, 150);
    });
    text.addEventListener("keydown", function (e) {
      if (list.hidden) return;
      if (e.key === "ArrowDown") { e.preventDefault(); active = Math.min(active + 1, items.length - 1); mark(); }
      else if (e.key === "ArrowUp") { e.preventDefault(); active = Math.max(active - 1, 0); mark(); }
      else if (e.key === "Enter" && items.length) { e.preventDefault(); choose(items[active >= 0 ? active : 0]); }
      else if (e.key === "Escape") { close(); }
    });
    text.addEventListener("blur", function () {
      setTimeout(function () { close(); if (!value.value) text.value = ""; }, 150);   // what shows is what is saved
    });
  });
})();
</script>
"""


class StaffSearchWidget(TypeaheadWidget):
    """The search-as-you-type box for choosing a staff member. Opening it lists the first ten; typing narrows them."""

    def render(self, name, value, attrs=None, renderer=None):
        return mark_safe(str(super().render(name, value, attrs, renderer)) + _STAFF_SEARCH_JS)

class EmployeeSearchWidget(TypeaheadWidget):
    """Search box for choosing an employee. `depends_on` is the id of another box (the document type) whose value
    narrows the list."""

    def __init__(self, url, placeholder="", depends_on="", empty="Nobody matches", attrs=None):
        super().__init__(url, placeholder, attrs)
        self.depends_on, self.empty = depends_on, empty

    def render(self, name, value, attrs=None, renderer=None):
        html = str(super().render(name, value, attrs, renderer))
        extra = format_html('data-typeahead data-typeahead-extra="{}" data-typeahead-empty="{}" data-typeahead-url=',
                            self.depends_on, self.empty)
        return mark_safe(html.replace("data-typeahead data-typeahead-url=", extra, 1) + _STAFF_SEARCH_JS)