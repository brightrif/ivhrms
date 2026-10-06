/*
 * Date pickers for every date field in the app.
 *
 * Progressive enhancement: the server still renders <input type="date">, and this script upgrades it.
 * Whatever the person sees on screen ("05 Oct 2026"), the form always submits ISO dates (yyyy-mm-dd),
 * so the server code and its validation are unchanged. If this script or the library fails to load,
 * the native date fields keep working.
 *
 * Optional rules, set from Django with date_input(...) as data-* attributes:
 *   data-min / data-max        "today" or an ISO date
 *   data-min-from              id of another date field; this one cannot be earlier than it
 *   data-view="years"          open on the year list (birth dates)
 *   data-range-end="#id"       turn this field and that one into a single start-end range picker (leave)
 *   data-off-days="5"          weekdays to shade as weekly off (0 = Sunday ... 6 = Saturday)
 *   data-holidays='{"2026-12-16": "National Day"}'   dates to mark as public holidays
 */
(function () {
  "use strict";
  if (typeof AirDatepicker === "undefined") { return; }

  var SHOW = "dd MMM yyyy";
  /* The library's own default language is Russian, so English is spelled out here. Sunday starts the week. */
  var LOCALE = {
    days: ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"],
    daysShort: ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"],
    daysMin: ["Su", "Mo", "Tu", "We", "Th", "Fr", "Sa"],
    months: ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
             "November", "December"],
    monthsShort: ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
    today: "Today", clear: "Clear", dateFormat: SHOW, timeFormat: "hh:mm aa", firstDay: 0
  };
  var MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"];
  var PHONE = window.matchMedia("(pointer: coarse) and (max-width: 768px)");

  /* ---------- dates (always local time: toISOString() would shift the day) ---------- */
  function pad(n) { return (n < 10 ? "0" : "") + n; }
  function iso(d) { return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate()); }
  function today() { var n = new Date(); return new Date(n.getFullYear(), n.getMonth(), n.getDate()); }
  function fromIso(s) {
    var m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(s || "");
    return m ? new Date(+m[1], +m[2] - 1, +m[3]) : null;
  }
  function limit(v) {
    if (!v) { return false; }
    return v === "today" ? today() : (fromIso(v) || false);
  }
  /* "12/11/2026", "12-11-2026", "12 Nov 2026", "12Nov2026" or "2026-11-12" -> Date, or null. Day first. */
  function parseTyped(text) {
    text = (text || "").trim().replace(/\s+/g, " ");
    var m, d, mo, y;
    if ((m = /^(\d{1,2})[\/\-. ](\d{1,2})[\/\-. ](\d{4})$/.exec(text))) { d = +m[1]; mo = +m[2]; y = +m[3]; }
    else if ((m = /^(\d{1,2})[\/\-. ]?([A-Za-z]{3,9})[\/\-. ,]*(\d{4})$/.exec(text))) {
      d = +m[1]; y = +m[3]; mo = MONTHS.indexOf(m[2].slice(0, 3).toLowerCase()) + 1;
      if (mo < 1) { return null; }
    }
    else if ((m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(text))) { y = +m[1]; mo = +m[2]; d = +m[3]; }
    else { return null; }
    var date = new Date(y, mo - 1, d);
    return (date.getFullYear() === y && date.getMonth() === mo - 1 && date.getDate() === d) ? date : null;
  }

  /* ---------- small helpers ---------- */
  function hiddenCopy(input) {
    var h = document.createElement("input");
    h.type = "hidden"; h.name = input.name; h.value = input.value;
    input.parentNode.insertBefore(h, input.nextSibling);
    return h;
  }
  function fire(el, name) { el.dispatchEvent(new Event(name, { bubbles: true })); }
  function showError(input, message) {
    input.classList.add("is-invalid");
    if (!input._dpMsg) {
      var box = document.createElement("div");
      box.className = "invalid-feedback d-block";
      input._dpMsg = box;
      input._dpAnchor.parentNode.insertBefore(box, input._dpAnchor.nextSibling);
    }
    input._dpMsg.textContent = message;
  }
  function clearError(input) {
    input.classList.remove("is-invalid");
    if (input._dpMsg) { input._dpMsg.remove(); input._dpMsg = null; }
  }
  function csv(v) { return v ? v.split(",").map(Number) : []; }
  function json(v) { try { return v ? JSON.parse(v) : {}; } catch (e) { return {}; } }

  /* weekly offs and holidays shown on the calendar */
  function cellMarker(input) {
    var off = csv(input.dataset.offDays), holidays = json(input.dataset.holidays);
    if (!off.length && !Object.keys(holidays).length) { return undefined; }
    return function (p) {
      if (p.cellType !== "day") { return undefined; }
      var key = iso(p.date);
      if (holidays[key]) { return { classes: "-holiday-", attrs: { title: holidays[key] } }; }
      if (off.indexOf(p.date.getDay()) !== -1) { return { classes: "-off-day-", attrs: { title: "Weekly off" } }; }
      return undefined;
    };
  }

  /* The library's own "Today" button only scrolls the calendar to this month. This one picks today, unless
     today is outside what this field allows (e.g. a renewal that must expire later), where it just shows the month. */
  var TODAY_BUTTON = {
    content: LOCALE.today,
    onClick: function (dp) {
      var t = today(), min = dp.opts.minDate, max = dp.opts.maxDate;
      if ((min && t < min) || (max && t > max)) { dp.setViewDate(t); return; }
      dp.selectDate(t);
      dp.setViewDate(t);
    }
  };

  /* Open below the box when there is room, otherwise above it; right-aligned near the screen edge. */
  function placeFor(getDp, input) {
    return function (animationDone) {
      if (animationDone) { return; }
      var dp = getDp(), box = input.getBoundingClientRect();
      var height = dp.$datepicker.offsetHeight + 12, width = dp.$datepicker.offsetWidth + 12;
      var below = window.innerHeight - box.bottom, above = box.top;
      var vertical = (below < height && above > below) ? "top" : "bottom";
      var horizontal = (box.left + width > window.innerWidth && box.right - width > 0) ? "right" : "left";
      dp.opts.position = vertical + " " + horizontal;
      dp.setPosition();
    };
  }

  function baseOptions(input) {
    var o = {
      locale: LOCALE, dateFormat: SHOW, autoClose: true, fixedHeight: true, toggleSelected: false,
      buttons: [TODAY_BUTTON, "clear"], weekends: [],
      minDate: limit(input.dataset.min), maxDate: limit(input.dataset.max),
      view: input.dataset.view || "days"
    };
    var marker = cellMarker(input);
    if (marker) { o.onRenderCell = marker; }
    return o;
  }

  /* ---------- one date ---------- */
  function enhanceSingle(input) {
    var hidden = hiddenCopy(input);                     // this one carries the name and the ISO value
    var start = fromIso(input.value);
    input.type = "text"; input.removeAttribute("name"); input.autocomplete = "off";
    input.placeholder = input.placeholder || "Select date";
    input.classList.add("has-datepicker");
    input._dpAnchor = hidden;
    var ready = false, dp;
    var getDp = function () { return dp; };

    function publish(date) {
      hidden.value = date ? iso(date) : "";
      input.dataset.iso = hidden.value;
      if (date) { clearError(input); }
      fire(input, "dp:change");
      fire(hidden, "change");
    }
    var opts = baseOptions(input);
    opts.selectedDates = start ? [start] : false;
    opts.onSelect = function (p) { if (ready) { publish(p.date || null); } };
    opts.onShow = placeFor(getDp, input);
    dp = new AirDatepicker(input, opts);
    input._dp = dp;                                     // a handle for tests and debugging
    input.dataset.iso = hidden.value;
    ready = true;

    /* the minimum can follow another field: an expiry date cannot be before its issue date */
    var follow = input.dataset.minFrom;
    if (follow) {
      document.addEventListener("dp:change", function (e) {
        if (e.target.id !== follow) { return; }
        var other = fromIso(e.target.dataset.iso), fixed = limit(input.dataset.min);
        var min = other && fixed ? (other > fixed ? other : fixed) : (other || fixed);
        dp.update({ minDate: min || false });
      });
      var seed = document.getElementById(follow);
      if (seed && seed.dataset.iso) {
        var o = fromIso(seed.dataset.iso), f = limit(input.dataset.min);
        dp.update({ minDate: (o && f ? (o > f ? o : f) : (o || f)) || false });
      }
    }

    /* typing: accept it only if it is a real date inside the allowed range, otherwise put the old value back.
       Only after the person really typed: the calendar writes its own text into the box a moment after a pick,
       and reading that stale text back would undo the pick (this is what broke the Today button). */
    var typed = false;
    input.addEventListener("input", function () { typed = true; });
    function commit() {
      if (!typed) { return; }
      typed = false;
      var text = input.value.trim();
      if (!text) {                                      // erased: update the stored value at once, not a moment later
        if (hidden.value) { publish(null); dp.clear({ silent: true }); }
        return;
      }
      var d = parseTyped(text), min = dp.opts.minDate, max = dp.opts.maxDate;
      var ok = d && !(min && d < min) && !(max && d > max);
      if (ok) {
        publish(d);
        dp.selectDate(d, { silent: true });
        input.value = dp.formatDate(d, SHOW);
      } else {
        var old = fromIso(hidden.value);
        input.value = old ? dp.formatDate(old, SHOW) : "";
      }
    }
    input.addEventListener("blur", commit);
    input.addEventListener("keydown", function (e) { if (e.key === "Enter") { commit(); } });
    input.addEventListener("click", function () { dp.show(); });
    return dp;
  }

  /* ---------- a start-end range in one picker ---------- */
  function enhanceRange(startInput, endInput) {
    var startHidden = hiddenCopy(startInput);
    endInput.type = "hidden";                            // keeps its own name and value
    var wrap = endInput.closest("[class*='col']");
    if (wrap) { wrap.style.display = "none"; }           // the neighbouring box now has the whole row
    var label = document.querySelector('label[for="' + startInput.id + '"]');
    if (label && label.firstChild && label.firstChild.nodeType === 3) {
      label.firstChild.nodeValue = startInput.dataset.rangeLabel || "Dates";
    }
    var a = fromIso(startHidden.value), b = fromIso(endInput.value);
    startInput.type = "text"; startInput.removeAttribute("name"); startInput.autocomplete = "off";
    startInput.readOnly = true;                          // ranges are picked, not typed
    startInput.placeholder = "Pick the first and last day";
    startInput.classList.add("has-datepicker");
    startInput._dpAnchor = startHidden;
    var ready = false;

    var opts = baseOptions(startInput);
    opts.range = true; opts.multipleDatesSeparator = " \u2013 "; opts.buttons = ["clear"];
    opts.selectedDates = a ? (b ? [a, b] : [a]) : false;
    opts.onSelect = function (p) {
      if (!ready) { return; }
      var ds = Array.isArray(p.date) ? p.date : (p.date ? [p.date] : []);
      var first = ds[0] || null, last = ds[1] || ds[0] || null;   // one click = a one-day request
      startHidden.value = first ? iso(first) : "";
      endInput.value = last ? iso(last) : "";
      startInput.dataset.iso = startHidden.value;
      if (first) { clearError(startInput); }
      fire(startInput, "dp:change");
      fire(endInput, "change");
    };
    var dp;
    opts.onShow = placeFor(function () { return dp; }, startInput);
    dp = new AirDatepicker(startInput, opts);
    startInput.dataset.iso = startHidden.value;
    ready = true;
    startInput.addEventListener("click", function () { dp.show(); });
    return dp;
  }

  /* ---------- wiring ---------- */
  function enhance(input) {
    if (input.dataset.dpReady || input.disabled || input.readOnly) { return; }
    if (PHONE.matches) { return; }                       // phones keep their own picker, which is better there
    input.dataset.dpReady = "1";
    var endSel = input.dataset.rangeEnd, end = endSel ? document.querySelector(endSel) : null;
    if (end && end.type === "date") { end.dataset.dpReady = "1"; enhanceRange(input, end); }
    else { enhanceSingle(input); }
  }
  function scan(root) {
    (root || document).querySelectorAll('input[type="date"]').forEach(enhance);
  }

  /* a required date left empty: say so next to the field (the picker is a text box, so the browser cannot) */
  document.addEventListener("submit", function (e) {
    var first = null;
    e.target.querySelectorAll('input[data-dp-ready][required]:not([type="hidden"])').forEach(function (input) {
      if (input.dataset.iso) { return; }
      showError(input, "Choose a date.");
      first = first || input;
    });
    if (first) { e.preventDefault(); e.stopPropagation(); first.focus(); }
  }, true);

  if (document.readyState === "loading") { document.addEventListener("DOMContentLoaded", function () { scan(); }); }
  else { scan(); }
  document.addEventListener("htmx:load", function (e) { scan(e.target); });   // forms that HTMX swaps in later
})();
