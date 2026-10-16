/* Nationality box: type to narrow the list of countries, then pick one. Only listed countries are accepted. */
(function () {
  function parts(box) {
    return {
      text: box.querySelector("[data-nationality-text]"),
      list: box.querySelector("[data-nationality-list]"),
      none: box.querySelector("[data-nationality-none]"),
      rows: Array.prototype.slice.call(
        box.querySelectorAll("[data-nationality-pick]"),
      ),
    };
  }
  function nameOf(row) {
    return row.dataset.name;
  }
  function byName(a, b) {
    return nameOf(a).localeCompare(nameOf(b));
  }
  function shown(p) {
    return p.rows.filter(function (r) {
      return !r.hidden;
    });
  }
  function setActive(p, index) {
    shown(p).forEach(function (r, i) {
      r.classList.toggle("active", i === index);
    });
  }
  function activeIndex(p) {
    return shown(p).findIndex(function (r) {
      return r.classList.contains("active");
    });
  }

  // Names that start with what was typed come first, then names where a later word starts with it.
  function filter(box) {
    var p = parts(box),
      q = p.text.value.trim().toLowerCase(),
      starts = [],
      inside = [];
    p.rows.forEach(function (r) {
      var name = nameOf(r).toLowerCase();
      r.hidden = true;
      r.classList.remove("active");
      if (!q || name.indexOf(q) === 0) {
        starts.push(r);
      } else if (
        name.split(/[\s-]+/).some(function (w) {
          return w.indexOf(q) === 0;
        })
      ) {
        inside.push(r);
      }
    });
    starts
      .sort(byName)
      .concat(inside.sort(byName))
      .forEach(function (r) {
        r.hidden = false;
        p.list.insertBefore(r, p.none);
      });
    p.none.hidden = starts.length + inside.length > 0;
    p.list.hidden = false;
    p.text.setAttribute("aria-expanded", "true");
  }
  function close(box) {
    var p = parts(box);
    p.list.hidden = true;
    p.text.setAttribute("aria-expanded", "false");
    setActive(p, -1);
  }
  function pick(box, row) {
    var p = parts(box);
    p.text.value = nameOf(row);
    p.text.classList.remove("is-invalid");
    close(box);
    p.text.dispatchEvent(new Event("change", { bubbles: true }));
  }
  // Leaving the box: an exact name, or the only name that starts with what was typed, is taken.
  // Anything else is cleared, unless it is the value the record already had.
  function settle(box) {
    var p = parts(box),
      text = p.text.value.trim().toLowerCase();
    if (text) {
      var exact = p.rows.filter(function (r) {
        return nameOf(r).toLowerCase() === text;
      })[0];
      var starts = p.rows.filter(function (r) {
        return nameOf(r).toLowerCase().indexOf(text) === 0;
      });
      if (exact) {
        p.text.value = nameOf(exact);
      } else if (starts.length === 1) {
        p.text.value = nameOf(starts[0]);
      } else if (text !== (p.text.dataset.saved || "").toLowerCase()) {
        p.text.value = "";
      }
    }
    close(box);
  }
  function boxOfText(e) {
    var t = e.target;
    return t.matches && t.matches("[data-nationality-text]")
      ? t.closest("[data-nationality]")
      : null;
  }

  document.addEventListener("focusin", function (e) {
    var box = boxOfText(e);
    if (box) {
      filter(box);
    }
  });
  document.addEventListener("click", function (e) {
    var box = boxOfText(e);
    if (box) {
      filter(box);
    }
  });
  document.addEventListener("input", function (e) {
    var box = boxOfText(e);
    if (box) {
      filter(box);
    }
  });
  document.addEventListener("focusout", function (e) {
    var box = boxOfText(e);
    if (box) {
      settle(box);
    }
  });

  document.addEventListener("mousedown", function (e) {
    var row = e.target.closest
      ? e.target.closest("[data-nationality-pick]")
      : null;
    if (row) {
      e.preventDefault();
      pick(row.closest("[data-nationality]"), row);
    }
  });

  document.addEventListener("keydown", function (e) {
    var box = boxOfText(e);
    if (!box) {
      return;
    }
    var p = parts(box),
      rows = shown(p),
      i;
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      if (p.list.hidden) {
        filter(box);
        rows = shown(p);
      }
      i = activeIndex(p);
      i =
        e.key === "ArrowDown"
          ? Math.min(i + 1, rows.length - 1)
          : Math.max(i - 1, 0);
      setActive(p, i);
      if (rows[i]) {
        rows[i].scrollIntoView({ block: "nearest" });
      }
    } else if (e.key === "Enter" && !p.list.hidden) {
      e.preventDefault(); // Enter picks, it does not save the form
      if (rows.length) {
        pick(box, rows[Math.max(activeIndex(p), 0)]);
      }
    } else if (e.key === "Escape" && !p.list.hidden) {
      e.preventDefault();
      close(box);
    }
  });
})();
