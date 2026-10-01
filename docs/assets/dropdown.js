/* Themed <select> popover - vanilla JS, no dependencies.
 *
 * Chrome draws a native dropdown popup in OS chrome, so it cannot be themed:
 * unstyled light-on-dark against this site's palette, and with long option
 * lists it flips upward and runs off the top of the viewport. This replaces
 * every <select class="pillar-select"> with a real popover that keeps the dark
 * theme, caps its height and scrolls, and flips up deliberately when needed.

 * Design notes:
 *  - the native <select> is NOT removed. It stays in the DOM (visually hidden),
 *    so every existing `.value` read/write and "change" listener keeps working
 *    unchanged. Picking an option writes to it and fires a real change event.
 *  - option rows reuse the .pillar-dd-* classes, so there is one popover style
 *    on the page and one set of rules to maintain.
 *  - options carrying a data-num attribute render in the numbered slot (pillars).

 * Public helper: window.SynhalEESDropdowns.refresh(select) after options change.
 */
(function () {
  "use strict";

  var SVGNS = "http://www.w3.org/2000/svg";

  // built as real SVG nodes, so there is no attribute-quoting to get wrong
  function chevron() {
    var svg = document.createElementNS(SVGNS, "svg");
    svg.setAttribute("class", "icon pillar-dd-chevron");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("fill", "none");
    svg.setAttribute("stroke", "currentColor");
    svg.setAttribute("stroke-width", "2.4");
    svg.setAttribute("stroke-linecap", "round");
    svg.setAttribute("stroke-linejoin", "round");
    svg.setAttribute("aria-hidden", "true");
    var pl = document.createElementNS(SVGNS, "polyline");
    pl.setAttribute("points", "6 9 12 15 18 9");
    svg.appendChild(pl);
    return svg;
  }

  function isOpen(dd) {
    var m = dd.querySelector(".pillar-dd-menu");
    return !!m && !m.hidden;
  }

  function closeAll(except) {
    all.forEach(function (dd) {
      if (dd !== except && isOpen(dd)) setOpen(dd, false);
    });
  }

  // Hide every open picker panel (.chart-picker: the leaderboard model filter,
  // the chart picker, the head-to-head compare picker) except `except`.
  // Each trigger stops propagation on its own click, so the other panels'
  // document-level outside-click handlers never fire - without this they would
  // happily sit open on top of each other (e.g. "Top 25" + "+ Select models").
  function closePickers(except) {
    Array.prototype.forEach.call(document.querySelectorAll(".chart-picker"), function (p) {
      if (p === except || p.hidden) return;
      p.hidden = true;
      p.classList.remove("open-up");
      var trig = p.id === "model-filter" ? document.getElementById("model-filter-btn") : null;
      if (trig) trig.setAttribute("aria-expanded", "false");
    });
  }

  function setOpen(dd, open) {
    var btn = dd.querySelector(".pillar-dd-btn");
    var menu = dd.querySelector(".pillar-dd-menu");
    if (!btn || !menu) return;
    if (open) {
      // Close any other open dropdowns first so menus do not overlap
      closeAll(dd);
      // a pillar popover opening must also dismiss any open picker panel
      closePickers();
      // Self-heal: the <select> is the source of truth, and its options can be
      // rewritten after this menu was built (chart.js populates the chart selects
      // in JS). Rebuilding here is a handful of DOM nodes and makes the popover
      // correct whatever order things ran in.
      if (dd.__sel) refresh(dd.__sel);
      menu.hidden = false;
      // lift the WRAPPER, not just the menu - see .pillar-dd in style.css
      dd.classList.add("is-open");
      btn.setAttribute("aria-expanded", "true");
      // flip up only when the capped list genuinely will not fit below
      menu.classList.remove("open-up");
      var need = Math.min(320, menu.scrollHeight);
      var below = window.innerHeight - menu.getBoundingClientRect().top;
      if (below < need + 16) menu.classList.add("open-up");
    } else {
      menu.hidden = true;
      menu.classList.remove("open-up");
      dd.classList.remove("is-open");
      btn.setAttribute("aria-expanded", "false");
    }
  }

  function labelFor(sel) {
    var o = sel.options[sel.selectedIndex];
    if (!o) return sel.getAttribute("aria-label") || "Select";
    return o.textContent;
  }

  function syncSelection(dd) {
    var sel = dd.__sel, menu = dd.querySelector(".pillar-dd-menu");
    if (!sel || !menu) return;
    Array.prototype.forEach.call(menu.querySelectorAll("[data-value]"), function (b) {
      b.setAttribute("aria-selected", b.getAttribute("data-value") === sel.value ? "true" : "false");
    });
  }

  // Rebuild the option rows from the live <select>. Cheap and idempotent, so it is
  // safe to call whenever the option list changes.

  function refresh(sel) {
    var dd = sel.__dd;
    if (!dd) return;
    var label = dd.querySelector(".pillar-dd-btn > span");
    if (label) label.textContent = labelFor(sel);
    var menu = dd.querySelector(".pillar-dd-menu");
    if (!menu) return;
    menu.innerHTML = "";
    for (var i = 0; i < sel.options.length; i++) {
      var o = sel.options[i];
      var b = document.createElement("button");
      b.type = "button";
      b.className = "pillar-dd-opt";
      b.setAttribute("role", "option");
      b.setAttribute("data-value", o.value);
      b.setAttribute("aria-selected", o.selected ? "true" : "false");
      if (o.dataset && o.dataset.num) {
        var n = document.createElement("span");
        n.className = "pillar-dd-num";
        n.textContent = o.dataset.num;
        b.appendChild(n);
      }
      var t = document.createElement("span");
      t.textContent = o.textContent;
      b.appendChild(t);
      menu.appendChild(b);
    }
  }

  // Wrap one <select>: hide the native control, add the themed trigger + menu.
  function enhance(sel) {
    if (sel.__dd) return sel.__dd;

    var dd = document.createElement("span");
    dd.className = "pillar-dd";

    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "pillar-dd-btn";
    btn.setAttribute("aria-haspopup", "listbox");
    btn.setAttribute("aria-expanded", "false");

    var lab = document.createElement("span");
    btn.appendChild(lab);
    btn.appendChild(chevron());

    var menu = document.createElement("div");
    menu.className = "pillar-dd-menu";
    menu.setAttribute("role", "listbox");
    menu.hidden = true;

    dd.appendChild(btn);
    dd.appendChild(menu);

    sel.parentNode.insertBefore(dd, sel);
    sel.classList.add("pillar-dd-hidden");

    sel.__dd = dd;
    dd.__sel = sel;

    refresh(sel);

    btn.addEventListener("click", function (e) {
      e.stopPropagation();
      setOpen(dd, !isOpen(dd));
    });

    menu.addEventListener("click", function (e) {
      var opt = e.target.closest("[data-value]");
      if (!opt) return;
      sel.value = opt.getAttribute("data-value");
      // fire a real change so every existing listener keeps working untouched
      sel.dispatchEvent(new Event("change", { bubbles: true }));
      refresh(sel);
      setOpen(dd, false);
      btn.focus();
    });

    // the keyboard contract a native <select> gave for free
    menu.addEventListener("keydown", function (e) {
      var opts = Array.prototype.slice.call(menu.querySelectorAll("[data-value]"));
      if (!opts.length) return;
      var cur = opts.indexOf(document.activeElement);
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        var next = e.key === "ArrowDown"
          ? (cur + 1) % opts.length
          : (cur <= 0 ? opts.length - 1 : cur - 1);
        opts[next].focus();
      } else if (e.key === "Home") {
        e.preventDefault();
        opts[0].focus();
      } else if (e.key === "End") {
        e.preventDefault();
        opts[opts.length - 1].focus();
      } else if (e.key === "Escape") {
        e.preventDefault();
        setOpen(dd, false);
        btn.focus();
      } else if (e.key === "Tab") {
        setOpen(dd, false);
      }
    });

    return dd;
  }

  /* ---------- boot ---------- */

  var SELECTOR = "select.pillar-select";

  var all = [];

  function syncLabels() {
    all.forEach(function (dd) {
      var sel = dd.__sel;
      if (!sel) return;
      var label = dd.querySelector(".pillar-dd-btn > span");
      if (!label) return;
      var want = labelFor(sel);
      if (label.textContent !== want) {
        label.textContent = want;
        syncSelection(dd);
      }
    });
  }

  // chart.js appends its options to the chart selects in JS, and it loads after
  // this file, so the first build of those menus happens against an empty
  // <select> and the popover opens with no rows. Watching the option list means
  // the menu is rebuilt the moment an option appears, whatever the load order.

  function watchOptions(sel) {
    if (!window.MutationObserver || !sel) return;
    var mo = new MutationObserver(function () {
      if (!sel.__dd) return;
      var wasOpen = isOpen(sel.__dd);
      refresh(sel);
      // keep an open menu open (and repositioned) when its rows appear
      if (wasOpen) setOpen(sel.__dd, true);
    });
    mo.observe(sel, { childList: true, subtree: true });
    sel.__mo = mo;
  }

  function init() {
    var nodes = document.querySelectorAll(SELECTOR);
    Array.prototype.forEach.call(nodes, function (sel) {
      all.push(enhance(sel));
      watchOptions(sel);
    });

    // keep every trigger label and selection marker in step with whatever the
    // <select> holds, including value writes made by app.js and chart.js
    document.addEventListener("change", function () { syncLabels(); }, true);

    document.addEventListener("click", function (e) {
      all.forEach(function (dd) {
        if (isOpen(dd) && !dd.contains(e.target)) setOpen(dd, false);
      });
    });

    document.addEventListener("keydown", function (e) {
      if (e.key !== "Escape") return;
      all.forEach(function (dd) { if (isOpen(dd)) setOpen(dd, false); });
    });

    // a resize can move the trigger, so drop any open menu rather than leave it
    // anchored to stale coordinates
    window.addEventListener("resize", function () {
      all.forEach(function (dd) { if (isOpen(dd)) setOpen(dd, false); });
    });
  }

  window.SynhalEESDropdowns = {
    refresh: function (sel) {
      if (sel) refresh(sel); else all.forEach(function (dd) { if (dd.__sel) refresh(dd.__sel); });
    },
    sync: syncLabels,
    closeAll: closeAll,
    closePickers: closePickers
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
