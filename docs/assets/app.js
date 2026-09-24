/* SynhalEES Leaderboard — vanilla JS, no build step, no dependencies. */
(function () {
  "use strict";

  var state = {
    radarModel: null,   // model shown in the modal (for radar hover)
    radarHover: -1,     // hovered radar vertex index (-1 = none)
    pillars: [],
    models: [],
    demo: false,
    updated: "",
    mode: "overall",          // overall | text | vision | audio
    pillar: "",               // "" = overall, else pillar slug
    compare: [],              // rival model names in the comparison table (2-5)
    featured: "",             // pinned first column — always gets crown styling (UI-only emphasis)
    sortKey: "score",
    sortDir: -1,
    search: "",
    hidden: {},             // model names unchecked in the Models filter dropdown
    bootAnim: true          // first-load entrance animations
  };

  function $(sel) { return document.querySelector(sel); }

  /* ---------- inline SVG icons (Lucide-style, no emoji) ---------- */

  var SVG_OPEN = "<svg class='icon' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2' stroke-linecap='round' stroke-linejoin='round' aria-hidden='true'>";
  var ICONS = {
    "01_buddhist_culture": "<path d='M12 4v2'/><path d='M9.5 6h5'/><path d='M6 21v-2a6 6 0 0 1 12 0v2'/><path d='M4 21h16'/>",
    "02_pali_gatha": "<path d='M19 17V5a2 2 0 0 0-2-2H4'/><path d='M8 21h12a2 2 0 0 0 2-2v-1a1 1 0 0 0-1-1H11a1 1 0 0 0-1 1v3a2 2 0 1 1-4 0V5a2 2 0 1 0-4 0v3'/>",
    "03_classical_literature": "<path d='M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z'/><path d='M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z'/>",
    "04_kavi_sindu": "<path d='M9 18V5l12-2v13'/><circle cx='6' cy='18' r='3'/><circle cx='18' cy='16' r='3'/>",
    "05_sinhala_grammar": "<path d='M12 20h9'/><path d='M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z'/>",
    "06_daily_spoken": "<path d='M7.9 20A9 9 0 1 0 4 16.1L2 22Z'/>",
    "07_figurative_sinhala": "<path d='M3 21c3 0 7-1 7-8V5c0-1.25-.756-2.017-2-2H4c-1.25 0-2 .75-2 1.972V11c0 1.25.75 2 2 2 1 0 1 0 1 1v1c0 1-1 2-2 2s-1 .008-1 1.031V20c0 1 0 1 1 1z'/><path d='M15 21c3 0 7-1 7-8V5c0-1.25-.757-2.017-2-2h-4c-1.25 0-2 .75-2 1.972V11c0 1.25.75 2 2 2h.75c0 2.25.25 4-2.75 4v3c0 1 0 1 1 1z'/>",
    "08_profanity_nuance": "<path d='m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z'/><path d='M12 9v4'/><path d='M12 17h.01'/>",
    "09_singlish_sms": "<rect width='14' height='20' x='5' y='2' rx='2' ry='2'/><path d='M12 18h.01'/>",
    "10_regional_dialects": "<path d='M14.106 5.553a2 2 0 0 0 1.788 0l3.659-1.83A1 1 0 0 1 21 4.619v12.764a1 1 0 0 1-.553.894l-4.553 2.277a2 2 0 0 1-1.788 0l-4.212-2.106a2 2 0 0 0-1.788 0l-3.659 1.83A1 1 0 0 1 3 19.381V6.618a1 1 0 0 1 .553-.894l4.553-2.277a2 2 0 0 1 1.788 0z'/><path d='M15 5.764v15'/><path d='M9 3.236v15'/>",
    "11_astrology_beliefs": "<path d='M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z'/><path d='M19 3v4'/><path d='M21 5h-4'/>",
    "12_general_knowledge": "<path d='M4 15s1-1 4-1 5 2 8 2 4-1 4-1V3s-1 1-4 1-5-2-8-2-4 1-4 1z'/><line x1='4' x2='4' y1='22' y2='15'/>",
    "13_sri_lanka_law": "<path d='m16 16 3-8 3 8c-.87.65-1.92 1-3 1s-2.13-.35-3-1Z'/><path d='m2 16 3-8 3 8c-.87.65-1.92 1-3 1s-2.13-.35-3-1Z'/><path d='M7 21h10'/><path d='M12 3v18'/><path d='M3 7h2c2 0 5-1 7-2 2 1 5 2 7 2h2'/>",
    "14_culinary_kitchen": "<path d='M2 12h20'/><path d='M20 12v8a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2v-8'/><path d='m4 8 16-4'/><path d='m8.86 6.78-.45-1.81a2 2 0 0 1 1.45-2.43l1.94-.48a2 2 0 0 1 2.43 1.46l.45 1.8'/>",
    "15_numbers_maths": "<path d='M18 7V5a1 1 0 0 0-1-1H6.5a.5.5 0 0 0-.4.8l4.5 6a2 2 0 0 1 0 2.4l-4.5 6a.5.5 0 0 0 .4.8H17a1 1 0 0 0 1-1v-2'/>",
    "crown": "<path d='M11.562 3.266a.5.5 0 0 1 .876 0L15.39 8.87a1 1 0 0 0 1.516.294L21.183 5.5a.5.5 0 0 1 .798.519l-2.834 10.246a1 1 0 0 1-.956.735H5.81a1 1 0 0 1-.957-.735L2.02 6.02a.5.5 0 0 1 .798-.519l4.276 3.664a1 1 0 0 0 1.516-.294z'/><path d='M5 21h14'/>",
    "sun": "<circle cx='12' cy='12' r='4'/><path d='M12 2v2'/><path d='M12 20v2'/><path d='m4.93 4.93 1.41 1.41'/><path d='m17.66 17.66 1.41 1.41'/><path d='M2 12h2'/><path d='M20 12h2'/><path d='m6.34 17.66-1.41 1.41'/><path d='m19.07 4.93-1.41 1.41'/>",
    "moon": "<path d='M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z'/>",
  };

  function icon(name) {
    return SVG_OPEN + (ICONS[name] || "") + "</svg>";
  }
  /* ---------- provider logos (brand SVGs in assets/logos/) ---------- */

  // provider name (normalized to lowercase alnum) -> svg file in assets/logos/.
  // To support a new organization: drop its <slug>.svg into assets/logos/
  // and add one line here. Unmapped providers get a red initial chip.
  var PROVIDER_LOGOS = {
    google: "google",
    openai: "openai",
    anthropic: "anthropic",
    meta: "meta", facebook: "meta", llama: "meta",
    mistralai: "mistralai", mistral: "mistralai",
    deepseek: "deepseek",
    alibaba: "alibabacloud", alibabacloud: "alibabacloud", qwen: "alibabacloud",
    amazon: "amazonwebservices", aws: "amazonwebservices", amazonwebservices: "amazonwebservices",
    microsoft: "microsoft", azure: "microsoft",
    ibm: "ibm", watsonx: "ibm",
    perplexity: "perplexity",
    xiaomi: "xiaomi",
    synhalaai: "synhalaAI"
  };

  function providerLogoFile(name) {
    // full name first ("Ollama / Meta" -> "ollamameta"), then parts right-to-left
    // so the brand org ("meta") wins over a runner/host prefix ("ollama")
    var key = String(name).toLowerCase().replace(/[^a-z0-9]/g, "");
    var file = PROVIDER_LOGOS[key];
    if (!file) {
      var parts = String(name).toLowerCase().split(/[^a-z0-9]+/).filter(Boolean);
      for (var i = parts.length - 1; i >= 0; i--) {
        file = PROVIDER_LOGOS[parts[i]];
        if (file) break;
      }
    }
    return file || null;
  }

  function providerLogo(name) {
    var file = providerLogoFile(name);
    if (file) {
      return "<img class='prov-logo' src='assets/logos/" + file + ".svg' alt='' loading='lazy' onerror='this.remove()'>";
    }
    return "<span class='prov-chip' aria-hidden='true'>" + esc(String(name).charAt(0).toUpperCase()) + "</span>";
  }

  function setThemeIcon(theme) {
    $("#theme-toggle").innerHTML = icon(theme === "light" ? "moon" : "sun");
  }


  function fetchJSON(url) {
    return fetch(url).then(function (r) {
      if (!r.ok) throw new Error("Failed to load " + url);
      return r.json();
    });
  }

  function scoreOf(m) {
    if (state.pillar) return m.pillars[state.pillar] != null ? m.pillars[state.pillar] : null;
    if (state.mode === "overall") return m.overall;
    return m.modalities[state.mode];
  }

  function fmt(v) { return v == null ? "—" : v.toFixed(1); }

  function medal(rank) {
    return rank <= 3
      ? "<span class='rank-badge r" + rank + "'>" + rank + "</span>"
      : String(rank);
  }

  function tableRows() {
    var rows = state.models.filter(function (m) {
      return !state.hidden[m.name] && m.name.toLowerCase().indexOf(state.search) !== -1 && scoreOf(m) != null;
    });
    rows.sort(function (a, b) {
      var k = state.sortKey, va, vb;
      if (k === "name" || k === "provider" || k === "date") {
        va = a[k]; vb = b[k];
        return va < vb ? -state.sortDir : va > vb ? state.sortDir : 0;
      }
      va = k === "score" ? scoreOf(a) : (k === "rank" ? 0 : a.modalities[k]);
      vb = k === "score" ? scoreOf(b) : (k === "rank" ? 0 : b.modalities[k]);
      if (va == null) return 1;
      if (vb == null) return -1;
      return (va - vb) * state.sortDir;
    });
    return rows;
  }

  function scoreLabel() {
    return state.pillar
      ? pillarTitle(state.pillar)
      : state.mode.charAt(0).toUpperCase() + state.mode.slice(1) + " Score";
  }

  /* ---------- rendering ---------- */

  function renderTable() {
    var body = $("#lb-body");
    body.innerHTML = "";

    var rows = tableRows();

    $("#empty-state").hidden = rows.length > 0;
    // data exists but every row is filtered out (search box / Models dropdown)
    if (!rows.length && state.models.length) {
      $("#empty-state").textContent = "No models match the current search / model filter.";
    }

    var best = rows.length ? Math.max.apply(null, rows.map(scoreOf)) : -1;

    rows.forEach(function (m, i) {
      var s = scoreOf(m);
      var tr = document.createElement("tr");
      tr.innerHTML =
        "<td class=\"num medal\">" + medal(i + 1) + "</td>" +
        "<td><span class=\"model-name\">" + esc(m.name) + "</span>" +
        "<div class=\"scorebar\"><span style=\"width:" + s + "%\"></span></div></td>" +
        "<td class=\"provider\">" + providerLogo(m.provider) + esc(m.provider) + "</td>" +
        "<td class=\"num\"><span class=\"score-pill" + (s === best ? " top" : "") + "\">" + fmt(s) + "</span></td>" +
        "<td class=\"num\">" + fmt(m.modalities.text) + "</td>" +
        "<td class=\"num\">" + fmt(m.modalities.vision) + "</td>" +
        "<td class=\"num\">" + fmt(m.modalities.audio) + "</td>" +
        "<td class=\"num na\">" + esc(m.date) + "</td>";
      tr.addEventListener("click", function () { openModal(m); });
      body.appendChild(tr);
    });

    // first-load cascade (rows animate in once on page open)
    if (state.bootAnim) {
      body.classList.add("rows-boot");
      Array.prototype.forEach.call(body.children, function (tr, i) {
        tr.style.animationDelay = (i * 45) + "ms";
      });
    }

    var label = scoreLabel();
    // keep the sort-arrow svg: update only the label span inside the th
    var sc = $("#score-col"), scLabel = sc.querySelector(".th-label");
    if (scLabel) scLabel.textContent = label; else sc.textContent = label;
  }

  function esc(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function pillarTitle(slug) {
    for (var i = 0; i < state.pillars.length; i++) {
      if (state.pillars[i].slug === slug) return state.pillars[i].title_en;
    }
    return slug;
  }
  function renderPillarGrid() {
    var grid = $("#pillar-grid");
    grid.innerHTML = "";
    state.pillars.forEach(function (p) {
      var best = null;
      state.models.forEach(function (m) {
        var v = m.pillars[p.slug];
        if (v != null && (!best || v > best.v)) best = { v: v, name: m.name, provider: m.provider };
      });
      var card = document.createElement("button");
      card.className = "pillar-card";
      card.type = "button";
      card.innerHTML =
        "<span class=\"pc-top\">" +
          "<span class=\"pc-icon\">" + icon(p.slug) + "</span>" +
          "<span class=\"pc-titles\">" +
            "<span class=\"pc-title\">" + esc(p.title_en) + "</span>" +
            "<span class=\"pc-si\">" + esc(p.title_si) + "</span>" +
          "</span>" +
          "<span class=\"pc-num\" aria-hidden=\"true\">" + esc(p.slug.slice(0, 2)) + "</span>" +
        "</span>" +
        (best
          ? "<span class=\"pc-champ\"><span class=\"who\">" + providerLogo(best.provider) + icon("crown") +
            "<span class=\"who-name\">" + esc(best.name) + "</span></span>" +
            "<span class=\"pc-val\">" + best.v.toFixed(1) + "</span></span>" +
            "<span class=\"pc-bar\"><span style=\"width:" + Math.max(0, Math.min(100, best.v)).toFixed(1) + "%\"></span></span>"
          : "<span class=\"pc-champ\"><span class=\"who na\">No data yet</span></span>");
      card.addEventListener("click", function () {
        state.pillar = p.slug;
        $("#pillar-select").value = p.slug;
        renderTable();
        window.scrollTo({ top: 0, behavior: "smooth" });
      });
      grid.appendChild(card);
    });

    // first-load cascade
    if (state.bootAnim) {
      grid.classList.add("grid-boot");
      Array.prototype.forEach.call(grid.children, function (card, i) {
        card.style.animationDelay = (i * 35) + "ms";
      });
    }
  }

  // Progressive draw: vertices grow out ONE BY ONE, lowest score first,
  // highest last -- so strong pillars travel further and finish the reveal.
  function animateRadar(canvas, pillars, model) {
    var reduce = window.matchMedia &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduce || !window.requestAnimationFrame) { drawRadar(canvas, pillars, model, null); return; }

    // start order: ascending score
    var order = [];
    for (var i = 0; i < pillars.length; i++) {
      var v = model.pillars[pillars[i].slug];
      order.push([v == null ? -1 : v, i]);
    }
    order.sort(function (a, b) { return a[0] - b[0]; });
    var startAt = [];
    for (var j = 0; j < order.length; j++) startAt[order[j][1]] = j;

    var STEP = 90, GROW = 550;                 // ms: stagger per vertex / grow time
    var total = STEP * (pillars.length - 1) + GROW;
    var t0 = null;
    function step(ts) {
      var modal = $("#modal");
      if (!modal || modal.hidden) return; // stop pulsing once the modal closes
      if (t0 === null) t0 = ts;
      var e = ts - t0;
      if (e >= total) {
        // fully grown: keep looping so top-pillar dots keep beating
        drawRadar(canvas, pillars, model, null, state.radarHover, ts);
      } else {
        var vp = [];
        for (var k = 0; k < pillars.length; k++) {
          var pe = e - startAt[k] * STEP;
          var p = pe <= 0 ? 0 : Math.min(1, pe / GROW);
          vp.push(1 - Math.pow(1 - p, 3)); // easeOutCubic
        }
        drawRadar(canvas, pillars, model, vp, -1, null);
      }
      requestAnimationFrame(step);
    }
    requestAnimationFrame(step);
  }

  /* ---------- head-to-head comparison table ---------- */

  var CMP_MIN = 3, CMP_MAX = 6;

  function compareRows() {
    var rows = [
      { label: "Overall", sub: "Mean of available modalities", hero: true,
        get: function (m) { return m.overall; } },
      { group: "Modalities" },
      { label: "Text", sub: "All 15 pillars, text-only",
        get: function (m) { return m.modalities.text; } },
      { label: "Vision", sub: "Image + Sinhala prompt",
        get: function (m) { return m.modalities.vision; } },
      { label: "Audio", sub: "Spoken Sinhala prompts",
        get: function (m) { return m.modalities.audio; } },
      { group: "Pillars \u2014 15" }
    ];
    state.pillars.forEach(function (p) {
      rows.push({
        label: p.slug.slice(0, 2) + " \u00b7 " + p.title_en,
        sub: p.title_si,
        icon: p.slug,
        get: function (m) { return m.pillars[p.slug]; }
      });
    });
    return rows;
  }

  function renderCompare() {
    var tbl = $("#compare-table");
    if (!tbl) return;
    // featured model always leads the table; rivals follow in picker order
    var models = [];
    var featModel = null;
    state.models.forEach(function (m) { if (m.name === state.featured) featModel = m; });
    if (featModel) models.push(featModel);
    state.compare.forEach(function (n) {
      if (n === state.featured) return;
      state.models.forEach(function (m) { if (m.name === n) models.push(m); });
    });
    tbl.classList.toggle("has-featured", !!featModel);
    var rows = compareRows();
    var wins = {}; // honest row-win counts — never altered

    models.forEach(function (m) { wins[m.name] = 0; });
    rows.forEach(function (r) {
      if (r.group) return;
      var best = -Infinity, n = 0;
      models.forEach(function (m) {
        var v = r.get(m);
        if (v != null) { n++; if (v > best) best = v; }
      });
      if (n < 2) return; // need at least two scores to crown a row winner
      models.forEach(function (m) {
        if (r.get(m) === best) wins[m.name]++;
      });
    });

    var metricCount = 0;
    rows.forEach(function (r) { if (!r.group) metricCount++; });

    var html = "<thead><tr><th class='cmp-rowhead' scope='col'><span class='cmp-metriccount'>" +
      metricCount + " metrics</span></th>";
    models.forEach(function (m) {
      var feat = m.name === state.featured;
      var logo = providerLogo(m.provider);
      html += "<th scope='col' class='cmp-model" + (feat ? " cmp-featured" : "") + "'>" +
        "<span class='cmp-model-box'>" +
        (logo ? logo : "") +
        "<span class='cmp-name'>" + m.name + "</span>" +
        "<span class='cmp-provider'>" + m.provider + "</span>" +
        "<span class='cmp-wins" + (feat ? " top" : "") + "'>" +
        SVG_OPEN + ICONS.crown + "</svg>" + wins[m.name] + " wins</span>" +
        "</span></th>";
    });
    html += "</tr></thead><tbody>";

    rows.forEach(function (r) {
      if (r.group) {
        html += "<tr class='cmp-group'><td colspan='" + (models.length + 1) + "'>" + r.group + "</td></tr>";
        return;
      }
      var best = -Infinity, n = 0;
      models.forEach(function (m) {
        var v = r.get(m);
        if (v != null) { n++; if (v > best) best = v; }
      });
      var hasBest = n >= 2;
      html += "<tr" + (r.hero ? " class='cmp-hero'" : "") + "><th scope='row' class='cmp-rowhead'>" +
        (r.icon ? "<span class='cmp-ico'>" + SVG_OPEN + ICONS[r.icon] + "</svg></span>" : "") +
        "<span class='cmp-rtext'><span class='cmp-rlabel' title='" + esc(r.label) + "'>" + esc(r.label) + "</span>" +
        (r.sub ? "<span class='cmp-rsub'>" + r.sub + "</span>" : "") +
        "</span></th>";
      models.forEach(function (m) {
        var v = r.get(m);
        var feat = m.name === state.featured;
        var cls = "";
        if (hasBest && v === best) cls += " cmp-best";
        if (v == null) cls += " cmp-null";
        if (feat) cls += " cmp-featured";
        html += "<td class='" + cls.trim() + "'>" + (v == null ? "\u2014" : fmt(v)) + "</td>";
      });
      html += "</tr>";
    });
    html += "</tbody>";
    tbl.innerHTML = html;
  }

  function updateComparePickerState() {
    var list = $("#compare-picker-list");
    if (!list) return;
    var cbs = list.querySelectorAll("input[type=checkbox]");
    var atMax = state.compare.length >= CMP_MAX - 1; // -1: featured model fills the first slot
    var atMin = state.compare.length <= CMP_MIN - 1;
    for (var i = 0; i < cbs.length; i++) {
      cbs[i].disabled = (atMax && !cbs[i].checked) || (atMin && cbs[i].checked);
    }
    var cnt = $("#compare-count");
    if (cnt) cnt.textContent = "1 featured \u00b7 " + state.compare.length + " of " + (CMP_MAX - 1) + " rivals";
  }

  function flashCompareLimit() {
    var cnt = $("#compare-count");
    if (!cnt) return;
    cnt.classList.remove("flash");
    void cnt.offsetWidth; // restart the animation
    cnt.classList.add("flash");
  }

  /* ---------- PNG export (canvas-rendered, dependency-free, safe on file://) ---------- */

  var CROWN_D = [
    "M11.562 3.266a.5.5 0 0 1 .876 0L15.39 8.87a1 1 0 0 0 1.516.294L21.183 5.5a.5.5 0 0 1 .798.519l-2.834 10.246a1 1 0 0 1-.956.735H5.81a1 1 0 0 1-.957-.735L2.02 6.02a.5.5 0 0 1 .798-.519l4.276 3.664a1 1 0 0 0 1.516-.294z",
    "M5 21h14"
  ];

  function exportComparePNG() {
    // same ordering as the on-screen table: featured model first, then rivals
    var models = [];
    state.models.forEach(function (m) { if (m.name === state.featured) models.push(m); });
    state.compare.forEach(function (n) {
      if (n === state.featured) return;
      state.models.forEach(function (m) { if (m.name === n) models.push(m); });
    });
    if (models.length < 2) return;
    var rows = compareRows();

    // honest row-win counts (identical logic to renderCompare)
    var wins = {};
    models.forEach(function (m) { wins[m.name] = 0; });
    rows.forEach(function (r) {
      if (r.group) return;
      var best = -Infinity, n = 0;
      models.forEach(function (m) { var v = r.get(m); if (v != null) { n++; if (v > best) best = v; } });
      if (n < 2) return;
      models.forEach(function (m) { if (r.get(m) === best) wins[m.name]++; });
    });

    var cs = getComputedStyle(document.documentElement);
    function cv(n, fb) { var v = cs.getPropertyValue(n).trim(); return v || fb; }
    var C = {
      bg: cv("--navy-deep", "#1d2230"), card: cv("--navy-card", "#262b3c"),
      alt: cv("--row-alt", "#232838"), text: cv("--text", "#eef0f6"),
      muted: cv("--muted", "#9aa1b5"), border: cv("--border", "#3a4157"),
      red: cv("--red-bright", "#e53935"), gold: cv("--gold", "#f5b301")
    };
    var FONT = "'Inter', 'Noto Sans Sinhala', system-ui, sans-serif";
    var PAD = 36, SCALE = 2; // 2x for high-DPI / social-media quality
    var HEAD_H = 106, ROW_H = 44, HERO_H = 52, GROUP_H = 26, TITLE_H = 70, FOOT_H = 42;

    var canvas = document.createElement("canvas");
    var ctx = canvas.getContext("2d");

    function fitText(t, maxW) {
      if (ctx.measureText(t).width <= maxW) return t;
      while (t.length > 1 && ctx.measureText(t + "\u2026").width > maxW) t = t.slice(0, -1);
      return t + "\u2026";
    }
    function rrect(x, y, w, h, r) {
      ctx.beginPath();
      ctx.moveTo(x + r, y);
      ctx.arcTo(x + w, y, x + w, y + h, r);
      ctx.arcTo(x + w, y + h, x, y + h, r);
      ctx.arcTo(x, y + h, x, y, r);
      ctx.arcTo(x, y, x + w, y, r);
      ctx.closePath();
    }
    function drawCrown(x, y, color) {
      var s = 11 / 24;
      ctx.save();
      ctx.translate(x, y);
      ctx.scale(s, s);
      ctx.strokeStyle = color;
      ctx.lineWidth = 2;
      ctx.lineCap = "round";
      ctx.lineJoin = "round";
      for (var i = 0; i < CROWN_D.length; i++) ctx.stroke(new Path2D(CROWN_D[i]));
      ctx.restore();
    }

    // column widths measured from real text
    ctx.font = "700 12.5px " + FONT;
    var labelW = 0;
    rows.forEach(function (r) { if (!r.group) labelW = Math.max(labelW, ctx.measureText(r.label).width); });
    labelW = Math.min(Math.ceil(labelW) + 26, 250);
    ctx.font = "800 15px " + FONT;
    var nameW = 0;
    models.forEach(function (m) { nameW = Math.max(nameW, ctx.measureText(m.name).width); });
    var colW = Math.max(116, Math.ceil(nameW) + 32);

    var tableW = labelW + models.length * colW;
    var bodyH = 0;
    rows.forEach(function (r) { bodyH += r.group ? GROUP_H : (r.hero ? HERO_H : ROW_H); });
    var W = PAD * 2 + tableW;
    var H = PAD + TITLE_H + HEAD_H + bodyH + FOOT_H + 14;

    canvas.width = W * SCALE;
    canvas.height = H * SCALE;
    ctx.scale(SCALE, SCALE);
    ctx.textBaseline = "alphabetic";

    // page background + title block
    ctx.fillStyle = C.bg;
    ctx.fillRect(0, 0, W, H);
    var x0 = PAD, y = PAD;
    ctx.textAlign = "left";
    ctx.font = "800 22px " + FONT;
    ctx.fillStyle = C.red;
    ctx.fillText("SynhalEES", x0, y + 24);
    var tw = ctx.measureText("SynhalEES").width;
    ctx.fillStyle = C.text;
    ctx.fillText(" Benchmark", x0 + tw, y + 24);
    ctx.font = "600 12px " + FONT;
    ctx.fillStyle = C.muted;
    ctx.fillText("Head-to-Head Comparison \u00b7 Sinhala LLM evaluation across 15 pillars", x0, y + 46);
    var stamp = state.updated || "";
    if (state.demo) stamp = (stamp ? stamp + " \u00b7 " : "") + "DEMO DATA";
    if (stamp) {
      ctx.textAlign = "right";
      ctx.fillText(stamp, x0 + tableW, y + 46);
      ctx.textAlign = "left";
    }
    y += TITLE_H;

    // table card
    ctx.fillStyle = C.card;
    rrect(x0, y, tableW, HEAD_H + bodyH, 12);
    ctx.fill();

    var hx = x0 + labelW, ty = y;

    // ---- header: provider chip, name, provider, wins pill ----
    models.forEach(function (m, ci) {
      var cx = hx + ci * colW, midX = cx + colW / 2;
      var feat = m.name === state.featured;
      ctx.fillStyle = C.alt;
      rrect(midX - 13, ty + 12, 26, 26, 7);
      ctx.fill();
      ctx.textAlign = "center";
      ctx.fillStyle = C.muted;
      ctx.font = "800 13px " + FONT;
      ctx.fillText(m.provider.charAt(0).toUpperCase(), midX, ty + 30);
      ctx.fillStyle = C.text;
      ctx.font = "800 15px " + FONT;
      ctx.fillText(fitText(m.name, colW - 14), midX, ty + 58);
      ctx.fillStyle = C.muted;
      ctx.font = "400 11px " + FONT;
      ctx.fillText(fitText(m.provider, colW - 14), midX, ty + 75);
      // wins pill — gold for the featured model (UI-only emphasis; counts stay honest)
      ctx.font = "700 10.5px " + FONT;
      var pillTxt = wins[m.name] + " wins";
      var pw = ctx.measureText(pillTxt).width + 36;
      var px = midX - pw / 2, py = ty + 82;
      if (feat) { ctx.fillStyle = C.gold; rrect(px, py, pw, 18, 9); ctx.fill(); }
      else { ctx.strokeStyle = C.border; ctx.lineWidth = 1; rrect(px, py, pw, 18, 9); ctx.stroke(); }
      drawCrown(px + 9, py + 3.5, feat ? "#1d2230" : C.muted);
      ctx.fillStyle = feat ? "#1d2230" : C.muted;
      ctx.textAlign = "left";
      ctx.fillText(pillTxt, px + 24, py + 13);
    });

    ty += HEAD_H;
    ctx.fillStyle = C.border;
    ctx.fillRect(x0, ty - 1, tableW, 1);

    // ---- body rows (group headers + metric rows) ----
    rows.forEach(function (r) {
      if (r.group) {
        ctx.fillStyle = C.alt;
        ctx.fillRect(x0, ty, tableW, GROUP_H);
        ctx.fillStyle = C.muted;
        ctx.font = "800 10px " + FONT;
        ctx.textAlign = "left";
        ctx.fillText(r.group.toUpperCase(), x0 + 14, ty + 17);
        ty += GROUP_H;
        return;
      }
      var rh = r.hero ? HERO_H : ROW_H;
      var cy = ty + rh / 2;
      var sub = r.hero ? null : r.sub;
      ctx.textAlign = "left";
      ctx.fillStyle = C.text;
      ctx.font = (r.hero ? "800 15px " : "700 12.5px ") + FONT;
      ctx.fillText(fitText(r.label, labelW - 20), x0 + 14, sub ? cy - 2 : cy + 4.5);
      if (sub) {
        ctx.fillStyle = C.muted;
        ctx.font = "400 10.5px " + FONT;
        ctx.fillText(fitText(sub, labelW - 20), x0 + 14, cy + 13);
      }
      var best = -Infinity, n = 0;
      models.forEach(function (m) { var v = r.get(m); if (v != null) { n++; if (v > best) best = v; } });
      var hasBest = n >= 2;
      models.forEach(function (m, ci) {
        var cx = hx + ci * colW;
        var v = r.get(m);
        var feat = m.name === state.featured;
        var isBest = hasBest && v === best;
        if (isBest) {
          // full-strength red for the featured column, dimmed for rivals
          ctx.fillStyle = feat ? "rgba(198, 40, 40, .24)" : "rgba(198, 40, 40, .08)";
          ctx.fillRect(cx, ty, colW, rh);
        }
        var weight = r.hero ? 800 : (isBest ? (feat ? 800 : 600) : 600);
        ctx.font = weight + " " + (r.hero ? 15 : 13) + "px " + FONT;
        ctx.fillStyle = v == null ? C.muted : C.text;
        ctx.textAlign = "center";
        ctx.fillText(v == null ? "\u2014" : v.toFixed(1), cx + colW / 2, cy + 4.5);
      });
      ty += rh;
    });

    // featured column frame drawn on top of all rows
    var fci = -1;
    models.forEach(function (m, i) { if (m.name === state.featured) fci = i; });
    if (fci >= 0) {
      var fx = hx + fci * colW, fh = HEAD_H + bodyH;
      ctx.fillStyle = C.red;
      ctx.fillRect(fx, y, 1.5, fh);
      ctx.fillRect(fx + colW - 1.5, y, 1.5, fh);
      ctx.fillRect(fx, y, colW, 3);
      ctx.fillRect(fx, y + fh - 2, colW, 2);
    }

    // footer
    ctx.textAlign = "left";
    ctx.fillStyle = C.muted;
    ctx.font = "600 11px " + FONT;
    ctx.fillText("SynhalEES Benchmark" + (state.demo ? " \u00b7 demo data" : ""), x0, y + HEAD_H + bodyH + 28);
    ctx.textAlign = "right";
    ctx.fillText("github.com/SynhalaAI/SynhalEES-Benchmark", x0 + tableW, y + HEAD_H + bodyH + 28);

    // provider logos: preload embedded data URLs (they never taint the canvas,
    // even on file://), paint them over the initial chips, then download
    var logoFiles = {};
    models.forEach(function (m) { var f = providerLogoFile(m.provider); if (f) logoFiles[f] = 1; });
    var logoKeys = Object.keys(logoFiles);
    if (!logoKeys.length) { finish(); return; }
    var pending = logoKeys.length, logoImgs = {};
    logoKeys.forEach(function (f) {
      var img = new Image();
      img.onload = function () { logoImgs[f] = img; if (--pending === 0) { drawLogos(); finish(); } };
      img.onerror = function () { if (--pending === 0) { drawLogos(); finish(); } };
      img.src = (window.SYNHALEES_LOGOS && window.SYNHALEES_LOGOS[f]) ||
                "assets/logos/" + f + ".svg";
    });
    function drawLogos() {
      models.forEach(function (m, ci) {
        var img = logoImgs[providerLogoFile(m.provider)];
        if (!img) return;  // unmapped provider: keep the initial chip
        var midX = hx + ci * colW + colW / 2;
        ctx.fillStyle = "#ffffff";  // white chip so colored logos read on the dark card
        rrect(midX - 13, y + 12, 26, 26, 7);
        ctx.fill();
        ctx.drawImage(img, midX - 10, y + 15, 20, 20);
      });
    }
    function finish() {
      var a = document.createElement("a");
      a.download = "synhalees-head-to-head.png";
      try { a.href = canvas.toDataURL("image/png"); }
      catch (e) { alert("Export failed. If you opened this page via file://, try a local server instead."); return; }
      a.click();
    }
  }

  // Leaderboard table export: same pipeline as exportComparePNG, but renders
  // exactly what the on-screen table shows (tab + pillar + search + Models
  // filter + sort order all respected via tableRows()).
  function exportTablePNG() {
    var rows = tableRows();
    if (!rows.length) return;
    var scoreLbl = scoreLabel();

    var cs = getComputedStyle(document.documentElement);
    function cv(n, fb) { var v = cs.getPropertyValue(n).trim(); return v || fb; }
    var C = {
      bg: cv("--navy-deep", "#1d2230"), card: cv("--navy-card", "#262b3c"),
      navy: cv("--navy", "#20253a"), alt: cv("--row-alt", "#232838"),
      text: cv("--text", "#eef0f6"), muted: cv("--muted", "#9aa1b5"),
      border: cv("--border", "#3a4157"), red: cv("--red-bright", "#e53935")
    };
    var FONT = "'Inter', 'Noto Sans Sinhala', system-ui, sans-serif";
    var PAD = 36, SCALE = 2; // 2x for high-DPI / social-media quality
    var TITLE_H = 70, HEAD_H = 42, ROW_H = 46, FOOT_H = 42;

    var canvas = document.createElement("canvas");
    var ctx = canvas.getContext("2d");

    function fitText(t, maxW) {
      if (ctx.measureText(t).width <= maxW) return t;
      while (t.length > 1 && ctx.measureText(t + "\u2026").width > maxW) t = t.slice(0, -1);
      return t + "\u2026";
    }
    function rrect(x, y, w, h, r) {
      ctx.beginPath();
      ctx.moveTo(x + r, y);
      ctx.arcTo(x + w, y, x + w, y + h, r);
      ctx.arcTo(x + w, y + h, x, y + h, r);
      ctx.arcTo(x, y + h, x, y, r);
      ctx.arcTo(x, y, x + w, y, r);
      ctx.closePath();
    }

    // columns mirror the on-screen thead: # | Model | Provider | score | Text | Vision | Audio | Date
    ctx.font = "700 13.5px " + FONT;
    var nameW = 0, provW = 0, dateW = 0;
    rows.forEach(function (m) {
      nameW = Math.max(nameW, ctx.measureText(m.name).width);
      provW = Math.max(provW, ctx.measureText(m.provider).width);
      dateW = Math.max(dateW, ctx.measureText(m.date || "\u2014").width);
    });
    var cols = [
      { label: "#", w: 52, align: "right" },
      { label: "Model", w: Math.ceil(nameW) + 26, align: "left" },
      { label: "Provider", w: Math.ceil(provW) + 62, align: "left" },
      { label: scoreLbl, w: Math.max(88, Math.ceil(ctx.measureText(scoreLbl).width) + 26), align: "right" },
      { label: "Text", w: 68, align: "right" },
      { label: "Vision", w: 68, align: "right" },
      { label: "Audio", w: 68, align: "right" },
      { label: "Date", w: Math.max(92, Math.ceil(dateW) + 22), align: "right" }
    ];
    var tableW = 0;
    cols.forEach(function (c) { tableW += c.w; });
    var bodyH = rows.length * ROW_H;
    var W = PAD * 2 + tableW;
    var H = PAD + TITLE_H + HEAD_H + bodyH + FOOT_H + 14;

    canvas.width = W * SCALE;
    canvas.height = H * SCALE;
    ctx.scale(SCALE, SCALE);
    ctx.textBaseline = "alphabetic";

    // page background + title block (same pattern as the compare export)
    ctx.fillStyle = C.bg;
    ctx.fillRect(0, 0, W, H);
    var x0 = PAD, y = PAD;
    ctx.textAlign = "left";
    ctx.font = "800 22px " + FONT;
    ctx.fillStyle = C.red;
    ctx.fillText("SynhalEES", x0, y + 24);
    var tw = ctx.measureText("SynhalEES").width;
    ctx.fillStyle = C.text;
    ctx.fillText(" Benchmark", x0 + tw, y + 24);
    ctx.font = "600 12px " + FONT;
    ctx.fillStyle = C.muted;
    ctx.fillText("LLM Leaderboard \u00b7 " + scoreLbl + " \u00b7 " + rows.length + " of " + state.models.length + " models", x0, y + 46);
    var stamp = state.updated || "";
    if (state.demo) stamp = (stamp ? stamp + " \u00b7 " : "") + "DEMO DATA";
    if (stamp) {
      ctx.textAlign = "right";
      ctx.fillText(stamp, x0 + tableW, y + 46);
      ctx.textAlign = "left";
    }
    y += TITLE_H;

    // table card (clip so the header/zebra fills keep the rounded corners)
    ctx.fillStyle = C.card;
    rrect(x0, y, tableW, HEAD_H + bodyH, 12);
    ctx.fill();
    ctx.save();
    rrect(x0, y, tableW, HEAD_H + bodyH, 12);
    ctx.clip();

    // header row
    ctx.fillStyle = C.navy;
    ctx.fillRect(x0, y, tableW, HEAD_H);
    var cx = x0;
    ctx.font = "800 10px " + FONT;
    ctx.fillStyle = C.muted;
    cols.forEach(function (c) {
      ctx.textAlign = c.align;
      ctx.fillText(c.label.toUpperCase(), c.align === "right" ? cx + c.w - 12 : cx + 12, y + 26);
      cx += c.w;
    });
    ctx.fillStyle = C.border;
    ctx.fillRect(x0, y + HEAD_H - 1, tableW, 1);

    // body rows - identical data and order to the on-screen table
    var best = rows.length ? Math.max.apply(null, rows.map(scoreOf)) : -Infinity;
    var MEDAL = { 1: "#f5c542", 2: "#c3cad6", 3: "#c97e4a" };
    rows.forEach(function (m, ri) {
      var ry = y + HEAD_H + ri * ROW_H;
      if (ri % 2 === 1) { ctx.fillStyle = C.alt; ctx.fillRect(x0, ry, tableW, ROW_H); }
      var cy = ry + ROW_H / 2 + 4.5;
      var cxx = x0;
      // rank (gold/silver/bronze disc for the top 3, like .rank-badge)
      var rank = ri + 1;
      if (MEDAL[rank]) {
        ctx.fillStyle = MEDAL[rank];
        ctx.beginPath();
        ctx.arc(cxx + cols[0].w - 24, ry + ROW_H / 2, 10, 0, Math.PI * 2);
        ctx.fill();
        ctx.fillStyle = "#1d2230";
      } else {
        ctx.fillStyle = C.muted;
      }
      ctx.textAlign = "center";
      ctx.font = "800 11.5px " + FONT;
      ctx.fillText(String(rank), cxx + cols[0].w - 24, ry + ROW_H / 2 + 4);
      cxx += cols[0].w;
      // model name + mini score bar
      var sv = scoreOf(m);
      ctx.textAlign = "left";
      ctx.font = "700 13.5px " + FONT;
      ctx.fillStyle = C.text;
      ctx.fillText(fitText(m.name, cols[1].w - 26), cxx + 12, cy - 2);
      if (sv != null) {
        ctx.fillStyle = C.border;
        ctx.fillRect(cxx + 12, ry + ROW_H - 11, cols[1].w - 26, 3);
        ctx.fillStyle = C.red;
        ctx.fillRect(cxx + 12, ry + ROW_H - 11, (cols[1].w - 26) * Math.min(100, Math.max(0, sv)) / 100, 3);
      }
      cxx += cols[1].w;
      // provider (real logo painted later over this white chip)
      m._lx = cxx + 12; m._ly = ry + (ROW_H - 22) / 2;
      ctx.fillStyle = "#ffffff";
      rrect(m._lx, m._ly, 22, 22, 6);
      ctx.fill();
      ctx.fillStyle = C.muted;
      ctx.font = "800 11px " + FONT;
      ctx.textAlign = "center";
      ctx.fillText(m.provider.charAt(0).toUpperCase(), m._lx + 11, m._ly + 15);
      ctx.textAlign = "left";
      ctx.font = "400 12.5px " + FONT;
      ctx.fillText(fitText(m.provider, cols[2].w - 62), cxx + 42, cy);
      cxx += cols[2].w;
      // score (best gets the red accent, like .score-pill.top) + modality numbers
      var vals = [sv, m.modalities.text, m.modalities.vision, m.modalities.audio];
      vals.forEach(function (v, vi) {
        ctx.textAlign = "right";
        ctx.font = (vi === 0 ? "800" : "600") + " 13px " + FONT;
        ctx.fillStyle = v == null ? C.muted : (vi === 0 && v === best ? C.red : C.text);
        ctx.fillText(fmt(v), cxx + cols[3 + vi].w - 12, cy);
        cxx += cols[3 + vi].w;
      });
      // date
      ctx.textAlign = "right";
      ctx.font = "400 12px " + FONT;
      ctx.fillStyle = C.muted;
      ctx.fillText(m.date || "\u2014", cxx + cols[7].w - 12, cy);
    });
    ctx.restore();

    // footer
    ctx.textAlign = "left";
    ctx.fillStyle = C.muted;
    ctx.font = "600 11px " + FONT;
    ctx.fillText("SynhalEES Benchmark" + (state.demo ? " \u00b7 demo data" : ""), x0, y + HEAD_H + bodyH + 28);
    ctx.textAlign = "right";
    ctx.fillText("github.com/SynhalaAI/SynhalEES-Benchmark", x0 + tableW, y + HEAD_H + bodyH + 28);

    // provider logos: preload embedded data URLs (they never taint the canvas,
    // even on file://), paint them over the initial chips, then download
    var logoFiles = {};
    rows.forEach(function (m) { var f = providerLogoFile(m.provider); if (f) logoFiles[f] = 1; });
    var logoKeys = Object.keys(logoFiles);
    if (!logoKeys.length) { finish(); return; }
    var pending = logoKeys.length, logoImgs = {};
    logoKeys.forEach(function (f) {
      var img = new Image();
      img.onload = function () { logoImgs[f] = img; if (--pending === 0) { drawLogos(); finish(); } };
      img.onerror = function () { if (--pending === 0) { drawLogos(); finish(); } };
      img.src = (window.SYNHALEES_LOGOS && window.SYNHALEES_LOGOS[f]) ||
                "assets/logos/" + f + ".svg";
    });
    function drawLogos() {
      rows.forEach(function (m) {
        var img = logoImgs[providerLogoFile(m.provider)];
        if (!img || m._lx == null) return;  // unmapped provider: keep the initial chip
        ctx.fillStyle = "#ffffff";
        rrect(m._lx, m._ly, 22, 22, 6);
        ctx.fill();
        ctx.drawImage(img, m._lx + 3, m._ly + 3, 16, 16);
      });
    }
    function finish() {
      rows.forEach(function (m) { delete m._lx; delete m._ly; });
      var a = document.createElement("a");
      a.download = "synhalees-leaderboard.png";
      try { a.href = canvas.toDataURL("image/png"); }
      catch (e) { alert("Export failed. If you opened this page via file://, try a local server instead."); return; }
      document.body.appendChild(a); // some browsers ignore .click() on detached anchors
      a.click();
      a.remove();
    }
  }

  function buildComparePicker() {
    var list = $("#compare-picker-list");
    if (!list) return;
    // featured-model dropdown (single choice; change handler bound once)
    var fsel = $("#compare-featured");
    if (fsel) {
      fsel.innerHTML = "";
      state.models.forEach(function (m) {
        var opt = document.createElement("option");
        opt.value = m.name;
        opt.textContent = m.name;
        if (m.name === state.featured) opt.selected = true;
        fsel.appendChild(opt);
      });
      if (!fsel.dataset.bound) {
        fsel.dataset.bound = "1";
        fsel.addEventListener("change", function () {
          state.featured = fsel.value;
          state.compare = state.compare.filter(function (n) { return n !== state.featured; });
          // top up rivals if the new featured model was one of them
          for (var i = 0; i < state.models.length && state.compare.length < CMP_MIN - 1; i++) {
            var nm = state.models[i].name;
            if (nm !== state.featured && state.compare.indexOf(nm) === -1) state.compare.push(nm);
          }
          buildComparePicker();
          renderCompare();
        });
      }
    }
    list.innerHTML = "";
    state.models.forEach(function (m) {
      if (m.name === state.featured) return; // pinned via the featured dropdown above
      var item = document.createElement("label");
      item.className = "pk-item";
      var cb = document.createElement("input");
      cb.type = "checkbox";
      cb.checked = state.compare.indexOf(m.name) !== -1;
      cb.addEventListener("change", function () {
        if (cb.checked && state.compare.length >= CMP_MAX - 1) {
          cb.checked = false; flashCompareLimit(); return;
        }
        if (!cb.checked && state.compare.length <= CMP_MIN - 1) {
          cb.checked = true; flashCompareLimit(); return;
        }
        if (cb.checked) state.compare.push(m.name);
        else state.compare = state.compare.filter(function (n) { return n !== m.name; });
        updateComparePickerState();
        renderCompare();
      });
      item.appendChild(cb);
      // providerLogo() returns ready-made HTML (<img> or fallback chip), not a URL
      var logo = providerLogo(m.provider);
      if (logo) {
        var logoWrap = document.createElement("span");
        logoWrap.innerHTML = logo;
        item.appendChild(logoWrap);
      }
      var span = document.createElement("span");
      span.textContent = m.name;
      item.appendChild(span);
      list.appendChild(item);
    });
    updateComparePickerState();
  }
  function buildModelFilter() {
    var list = $("#model-filter-list");
    if (!list) return;
    list.innerHTML = "";
    state.models.forEach(function (m) {
      var item = document.createElement("label");
      item.className = "pk-item";
      var cb = document.createElement("input");
      cb.type = "checkbox";
      cb.checked = !state.hidden[m.name];
      cb.addEventListener("change", function () {
        if (cb.checked) delete state.hidden[m.name];
        else state.hidden[m.name] = true;
        updateModelFilterCount();
        renderTable();
      });
      item.appendChild(cb);
      // providerLogo() returns ready-made HTML (<img> or fallback chip), not a URL
      var logoWrap = document.createElement("span");
      logoWrap.innerHTML = providerLogo(m.provider);
      item.appendChild(logoWrap);
      var span = document.createElement("span");
      span.textContent = m.name;
      item.appendChild(span);
      list.appendChild(item);
    });
    updateModelFilterCount();
  }

  function updateModelFilterCount() {
    var shown = state.models.filter(function (m) { return !state.hidden[m.name]; }).length;
    var cnt = $("#model-filter-count");
    if (cnt) cnt.textContent = shown + " / " + state.models.length + " shown";
    var btn = $("#model-filter-btn");
    if (btn) {
      btn.textContent = shown === state.models.length
        ? "Models"
        : "Models (" + shown + "/" + state.models.length + ")";
      btn.classList.toggle("on", shown !== state.models.length);
    }
  }

  /* ---------- modal + radar chart ---------- */

  function openModal(m) {
    $("#modal-title").textContent = m.name;
    $("#modal-sub").innerHTML =
      providerLogo(m.provider) + esc(m.provider) + " · Overall " + fmt(m.overall) +
      " · Text " + fmt(m.modalities.text) +
      " · Vision " + fmt(m.modalities.vision) +
      " · Audio " + fmt(m.modalities.audio);

    var entries = state.pillars.map(function (p) {
      return { p: p, v: m.pillars[p.slug] };
    }).filter(function (e) { return e.v != null; });
    entries.sort(function (a, b) { return b.v - a.v; });
    var top = entries.slice(0, 3).map(function (e) {
      return "<div>" + icon(e.p.slug) + " <strong>" + esc(e.p.title_en) + "</strong> — " + e.v.toFixed(1) + "</div>";
    }).join("");
    $("#modal-best").innerHTML = top ? "Strongest pillars:" + top : "No pillar data.";

    state.radarModel = m;
    state.radarHover = -1;
    $("#modal").hidden = false;
    animateRadar($("#radar"), state.pillars, m);
  }

  // vertProg: per-vertex growth 0..1 (null = fully drawn). Vertices animate
  // one at a time -- lowest score first, so high scores travel furthest.
  function drawRadar(canvas, pillars, model, vertProg, hoverIdx, pulseT) {
    if (vertProg === undefined) vertProg = null;
    if (hoverIdx === undefined) hoverIdx = -1;
    if (pulseT === undefined) pulseT = null;
    var ctx = canvas.getContext("2d");
    var W = canvas.width, H = canvas.height;
    var cx = W / 2, cy = H / 2 + 8, R = Math.min(W, H) / 2 - 58;
    var n = pillars.length;
    var dark = document.documentElement.getAttribute("data-theme") !== "light";
    var colGrid = dark ? "#3a4157" : "#d9dce6";
    var colText = dark ? "#9aa1b5" : "#5b6274";

    // per-vertex progress (null input = all fully grown)
    var vp = [], progSum = 0;
    for (var pi = 0; pi < n; pi++) {
      var pv = vertProg ? vertProg[pi] : 1;
      vp.push(pv);
      progSum += pv;
    }
    var progMean = progSum / n;

    ctx.clearRect(0, 0, W, H);

    // concentric rings
    [0.25, 0.5, 0.75, 1].forEach(function (f) {
      ctx.beginPath();
      for (var i = 0; i <= n; i++) {
        var a = (Math.PI * 2 * i) / n - Math.PI / 2;
        var x = cx + Math.cos(a) * R * f, y = cy + Math.sin(a) * R * f;
        i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
      }
      ctx.strokeStyle = colGrid;
      ctx.stroke();
    });

    // spokes + labels
    ctx.font = "11px Inter, sans-serif";
    ctx.fillStyle = colText;
    for (var i = 0; i < n; i++) {
      var a = (Math.PI * 2 * i) / n - Math.PI / 2;
      ctx.beginPath();
      ctx.moveTo(cx, cy);
      ctx.lineTo(cx + Math.cos(a) * R, cy + Math.sin(a) * R);
      ctx.strokeStyle = colGrid;
      ctx.stroke();
      var lx = cx + Math.cos(a) * (R + 26), ly = cy + Math.sin(a) * (R + 26);
      ctx.textAlign = Math.abs(Math.cos(a)) < 0.3 ? "center" : Math.cos(a) > 0 ? "left" : "right";
      ctx.fillText(pillars[i].slug.slice(0, 2), lx, ly + 4);
    }

    // data polygon (each vertex grows out on its own schedule)
    ctx.beginPath();
    for (var j = 0; j <= n; j++) {
      var idx = j % n;
      var v = model.pillars[pillars[idx].slug];
      var f = (v == null ? 0 : v / 100) * vp[idx];
      var ang = (Math.PI * 2 * j) / n - Math.PI / 2;
      var px = cx + Math.cos(ang) * R * f, py = cy + Math.sin(ang) * R * f;
      j === 0 ? ctx.moveTo(px, py) : ctx.lineTo(px, py);
    }
    ctx.closePath();
    ctx.fillStyle = "rgba(198, 40, 40, " + (0.30 * progMean).toFixed(3) + ")";
    ctx.fill();
    ctx.strokeStyle = "#e53935";
    ctx.lineWidth = 2;
    ctx.stroke();

    // top-3 strongest pillar indices (get a gold pulse)
    var topSet = {};
    var scored = [];
    for (var t2 = 0; t2 < n; t2++) {
      var sv = model.pillars[pillars[t2].slug];
      if (sv != null) scored.push([sv, t2]);
    }
    scored.sort(function (a, b) { return b[0] - a[0]; });
    for (var t3 = 0; t3 < Math.min(3, scored.length); t3++) topSet[scored[t3][1]] = true;

    // heartbeat curve: two thumps (lub-dub) per cycle
    function heartbeat(ph) {
      return Math.exp(-Math.pow((ph - 0.12) / 0.05, 2)) +
             0.55 * Math.exp(-Math.pow((ph - 0.30) / 0.07, 2));
    }

    // vertex nodes (pop in once the polygon has mostly grown)
    var verts = [];
    for (var k = 0; k < n; k++) {
      var vv = model.pillars[pillars[k].slug];
      var ff = (vv == null ? 0 : vv / 100) * vp[k];
      var aa = (Math.PI * 2 * k) / n - Math.PI / 2;
      var vx = cx + Math.cos(aa) * R * ff, vy = cy + Math.sin(aa) * R * ff;
      var isTop = !!topSet[k];
      verts.push({ x: vx, y: vy, p: pillars[k], v: vv, top: isTop });
      if (vp[k] > 0.85) {
        var dotT = Math.min(1, (vp[k] - 0.85) / 0.15);
        var rr = (hoverIdx === k ? 5 : isTop ? 4 : 3);

        // strongest pillars: the dots beat like a heart, all in sync (1.2s cycle)
        if (isTop && vp[k] === 1 && pulseT != null) {
          var ph = (pulseT / 1000 % 1.2) / 1.2;
          rr *= 1 + 0.5 * heartbeat(ph);
        }

        ctx.beginPath();
        ctx.arc(vx, vy, rr * dotT, 0, Math.PI * 2);
        ctx.fillStyle = hoverIdx === k ? "#ff6f60" : isTop ? "#f5b301" : "#e53935";
        ctx.fill();
        ctx.lineWidth = 1.5;
        ctx.strokeStyle = dark ? "#10131c" : "#ffffff";
        ctx.stroke();
      }
    }
    canvas._radarVerts = verts;
  }
  /* ---------- events ---------- */

  function bindEvents() {
    document.querySelectorAll("#modality-tabs .tab").forEach(function (btn) {
      btn.addEventListener("click", function () {
        document.querySelectorAll("#modality-tabs .tab").forEach(function (b) {
          b.classList.remove("active");
        });
        btn.classList.add("active");
        state.mode = btn.getAttribute("data-mode");
        state.pillar = "";
        $("#pillar-select").value = "";
        renderTable();
      });
    });

    $("#pillar-select").addEventListener("change", function (e) {
      state.pillar = e.target.value;
      renderTable();
    });

    $("#search").addEventListener("input", function (e) {
      state.search = e.target.value.toLowerCase();
      renderTable();
    });

    document.querySelectorAll("#leaderboard th").forEach(function (th) {
      th.addEventListener("click", function () {
        var key = th.getAttribute("data-sort");
        if (key === "rank") return;
        if (state.sortKey === key) state.sortDir *= -1;
        else { state.sortKey = key; state.sortDir = key === "name" || key === "provider" ? 1 : -1; }
        document.querySelectorAll("#leaderboard th").forEach(function (h) {
          h.classList.remove("sorted-desc", "sorted-asc");
        });
        th.classList.add(state.sortDir === -1 ? "sorted-desc" : "sorted-asc");
        renderTable();
        // sweep animation: rows fade back in after a re-sort
        var body2 = $("#lb-body");
        body2.classList.remove("rows-sort");
        void body2.offsetWidth; // restart the CSS animation
        body2.classList.add("rows-sort");
        setTimeout(function () { body2.classList.remove("rows-sort"); }, 400);
      });
    });

    // radar vertex hover -> tooltip with pillar name + score
    var radarCanvas = $("#radar");
    radarCanvas.addEventListener("mousemove", function (e) {
      var verts = radarCanvas._radarVerts;
      if (!verts || !state.radarModel) return;
      var rect = radarCanvas.getBoundingClientRect();
      var mx = (e.clientX - rect.left) * radarCanvas.width / rect.width;
      var my = (e.clientY - rect.top) * radarCanvas.height / rect.height;
      var hit = -1;
      for (var i = 0; i < verts.length; i++) {
        var dx = mx - verts[i].x, dy = my - verts[i].y;
        if (dx * dx + dy * dy < 14 * 14) { hit = i; break; }
      }
      var tip = $("#radar-tip");
      if (hit >= 0) {
        var vt = verts[hit];
        tip.innerHTML = "<strong>" + esc(vt.p.title_en) + "</strong><span>" +
          (vt.v == null ? "No data" : vt.v.toFixed(1) + " / 100") + "</span>" +
          (vt.top ? "<em>Strongest pillar</em>" : "");
        tip.classList.toggle("gold", !!vt.top);
        tip.hidden = false;
        var crect = radarCanvas.parentNode.getBoundingClientRect();
        tip.style.left = (e.clientX - crect.left) + "px";
        tip.style.top = (e.clientY - crect.top - 12) + "px";
        radarCanvas.style.cursor = "pointer";
        if (state.radarHover !== hit) {
          state.radarHover = hit;
          drawRadar(radarCanvas, state.pillars, state.radarModel, null, hit);
        }
      } else {
        tip.hidden = true;
        tip.classList.remove("gold");
        radarCanvas.style.cursor = "";
        if (state.radarHover !== -1) {
          state.radarHover = -1;
          drawRadar(radarCanvas, state.pillars, state.radarModel, null);
        }
      }
    });
    radarCanvas.addEventListener("mouseleave", function () {
      var tip2 = $("#radar-tip");
      tip2.hidden = true;
      tip2.classList.remove("gold");
      radarCanvas.style.cursor = "";
      if (state.radarHover !== -1 && state.radarModel) {
        state.radarHover = -1;
        drawRadar(radarCanvas, state.pillars, state.radarModel, null);
      }
    });

    $("#modal-close").addEventListener("click", function () { $("#modal").hidden = true; });
    $("#modal").addEventListener("click", function (e) {
      if (e.target === $("#modal")) $("#modal").hidden = true;
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") {
        $("#modal").hidden = true;
        var cp = $("#compare-picker");
        if (cp) cp.hidden = true;
        var mf2 = $("#model-filter");
        if (mf2) mf2.hidden = true;
      }
    });

    var cmpBtn = $("#compare-pick");
    if (cmpBtn) {
      cmpBtn.addEventListener("click", function (e) {
        e.stopPropagation();
        var p = $("#compare-picker");
        p.hidden = !p.hidden;
      });
      $("#compare-picker").addEventListener("click", function (e) { e.stopPropagation(); });
      document.addEventListener("click", function () {
        var p = $("#compare-picker");
        if (p) p.hidden = true;
      });
      $("#compare-reset").addEventListener("click", function () {
        state.featured = state.models.length ? state.models[0].name : "";
        state.compare = state.models.slice(1, CMP_MIN).map(function (m) { return m.name; });
        buildComparePicker();
        renderCompare();
      });

      var expBtn = $("#compare-export");
      if (expBtn) expBtn.addEventListener("click", exportComparePNG);
    }
    var lbExp = $("#lb-export");
    if (lbExp) lbExp.addEventListener("click", exportTablePNG);
    var mfBtn = $("#model-filter-btn");
    if (mfBtn) {
      mfBtn.addEventListener("click", function (e) {
        e.stopPropagation();
        var p = $("#model-filter");
        p.hidden = !p.hidden;
        mfBtn.setAttribute("aria-expanded", p.hidden ? "false" : "true");
        if (!p.hidden) {
          var s = $("#model-filter-search");
          if (s) { s.value = ""; s.dispatchEvent(new Event("input")); s.focus(); }
        }
      });
      $("#model-filter").addEventListener("click", function (e) { e.stopPropagation(); });
      document.addEventListener("click", function () {
        var p = $("#model-filter");
        if (p) p.hidden = true;
      });
      $("#model-filter-all").addEventListener("click", function () {
        state.hidden = {};
        buildModelFilter();
        renderTable();
      });
      $("#model-filter-none").addEventListener("click", function () {
        state.hidden = {};
        state.models.forEach(function (m) { state.hidden[m.name] = true; });
        buildModelFilter();
        renderTable();
      });
      var mfSearch = $("#model-filter-search");
      if (mfSearch) {
        mfSearch.addEventListener("input", function () {
          var q = mfSearch.value.trim().toLowerCase();
          var items = document.querySelectorAll("#model-filter-list .pk-item");
          Array.prototype.forEach.call(items, function (it) {
            it.hidden = !!q && it.textContent.toLowerCase().indexOf(q) === -1;
          });
        });
      }
    }

    $("#theme-toggle").addEventListener("click", function () {
      var html = document.documentElement;
      var next = html.getAttribute("data-theme") === "light" ? "dark" : "light";
      html.setAttribute("data-theme", next);
      setThemeIcon(next);
      try { localStorage.setItem("synhalees-theme", next); } catch (e) {}
    });
  }

  function restoreTheme() {
    try {
      var t = localStorage.getItem("synhalees-theme");
      if (t) {
        document.documentElement.setAttribute("data-theme", t);
        setThemeIcon(t);
      }
    } catch (e) {}
  }

  /* ---------- boot ---------- */

  function start(pillars, lb) {
    state.pillars = pillars;
    state.models = lb.models || [];
    state.demo = !!lb.demo;
    state.updated = lb.updated || "";

    if (state.demo) $("#demo-badge").hidden = false;
    $("#updated-line").textContent = state.updated
      ? "Last updated: " + state.updated + " · " + state.models.length + " models ranked"
      : "";

    var sel = $("#pillar-select");
    state.pillars.forEach(function (p) {
      var opt = document.createElement("option");
      opt.value = p.slug;
      opt.textContent = p.slug.slice(0, 2) + " — " + p.title_en;
      sel.appendChild(opt);
    });

    restoreTheme();
    bindEvents();
    renderTable();
    renderPillarGrid();
    state.featured = state.models.length ? state.models[0].name : "";
    state.compare = state.models.slice(1, CMP_MIN).map(function (m) { return m.name; });
    buildModelFilter();
    buildComparePicker();
    renderCompare();
    state.bootAnim = false; // later re-renders (sort/filter/search) don't re-animate
  }

  // Data scripts (assets/data/*.js) work everywhere, including file://.
  // Fall back to fetch() for the GitHub Pages / HTTP case.
  if (window.SYNHALEES_PILLARS && window.SYNHALEES_LEADERBOARD) {
    start(window.SYNHALEES_PILLARS, window.SYNHALEES_LEADERBOARD);
  } else {
    Promise.all([
      fetchJSON("assets/data/pillars.json"),
      fetchJSON("assets/data/leaderboard.json")
    ]).then(function (results) {
      start(results[0], results[1]);
    }).catch(function (err) {
      console.error(err);
      $("#empty-state").hidden = false;
      $("#empty-state").textContent =
        "Could not load leaderboard data. Rebuild it with: python tools/build_leaderboard.py --demo";
    });
  }
})();
