/* SynhalEES Leaderboard - AA-style comparison bar chart.
   Vanilla JS + Canvas, no dependencies. Reads the same data scripts as app.js. */
(function () {
  "use strict";

  var MAX_BARS = 25;

  var st = {
    metric: "overall",     // overall | text | vision | audio
    provider: "",          // "" = all organizations
    limit: MAX_BARS,       // bars shown when no custom selection
    selected: null         // null = top-N by metric; else map of picked names
  };

  /* ---------- provider brand lookup (mirrors app.js PROVIDER_LOGOS) ---------- */

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

  var PROVIDER_COLORS = {
    google: "#4285F4",
    openai: "#10A37F",
    anthropic: "#D97757",
    meta: "#0082FB",
    mistralai: "#FF7000",
    deepseek: "#4D6BFE",
    alibabacloud: "#FF6A00",
    amazonwebservices: "#FF9900",
    microsoft: "#00A4EF",
    ibm: "#0F62FE",
    perplexity: "#20B8CD",
    xiaomi: "#FF6900",
    synhalaai: "#E04545"
  };

  function providerKey(name) {
    var key = String(name).toLowerCase().replace(/[^a-z0-9]/g, "");
    if (PROVIDER_LOGOS[key] || PROVIDER_COLORS[key]) return key;
    var parts = String(name).toLowerCase().split(/[^a-z0-9]+/).filter(Boolean);
    for (var i = parts.length - 1; i >= 0; i--) {
      if (PROVIDER_LOGOS[parts[i]] || PROVIDER_COLORS[parts[i]]) return parts[i];
    }
    return key;
  }

  function logoFile(name) {
    return PROVIDER_LOGOS[providerKey(name)] || null;
  }

  function barColor(provider) {
    var key = providerKey(provider);
    if (PROVIDER_COLORS[key]) return PROVIDER_COLORS[key];
    // deterministic fallback hue for unmapped orgs
    var h = 0;
    for (var i = 0; i < key.length; i++) h = (h * 31 + key.charCodeAt(i)) >>> 0;
    return "hsl(" + (h % 360) + ",62%,52%)";
  }

  /* ---------- logo image cache (canvas needs decoded images) ---------- */

  var imgCache = {};   // file -> HTMLImageElement | "error"

  function logoImg(file) {
    var hit = imgCache[file];
    if (hit === "error") return null;
    if (hit) return hit.complete && hit.naturalWidth ? hit : null;
    var img = new Image();
    img.src = "assets/logos/" + file + ".svg";
    img.onload = function () { draw(); };
    img.onerror = function () { imgCache[file] = "error"; draw(); };
    imgCache[file] = img;
    return null;
  }

  // brand icon for the watermark badge (PNG with its own light background)
  var brandIcon = null;
  function brandImg() {
    if (brandIcon === "error") return null;
    if (brandIcon) return brandIcon.complete && brandIcon.naturalWidth ? brandIcon : null;
    var img = new Image();
    img.src = "assets/icon.png";
    img.onload = function () { draw(); };
    img.onerror = function () { brandIcon = "error"; draw(); };
    brandIcon = img;
    return null;
  }

  /* ---------- helpers ---------- */

  function $(sel) { return document.querySelector(sel); }

  function esc(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function cssVar(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  function metricOf(m) {
    return st.metric === "overall" ? m.overall : m.modalities[st.metric];
  }

  var METRIC_LABEL = { overall: "Overall", text: "Text", vision: "Vision", audio: "Audio" };

  var state = { models: [] };
  var barRects = [];   // hit-test rects for the current frame

  function visibleModels() {
    var rows = state.models.filter(function (m) {
      if (metricOf(m) == null) return false;
      if (st.provider && m.provider !== st.provider) return false;
      if (st.selected && !st.selected[m.name]) return false;
      return true;
    });
    rows.sort(function (a, b) { return metricOf(b) - metricOf(a); });
    return rows.slice(0, st.selected ? MAX_BARS : st.limit);
  }

  /* ---------- drawing ---------- */

  function draw() {
    var canvas = $("#chart");
    if (!canvas || !state.models.length) return;
    var W = canvas.parentElement.clientWidth;
    var H = 460;
    var dpr = window.devicePixelRatio || 1;
    canvas.width = W * dpr;
    canvas.height = H * dpr;
    canvas.style.width = W + "px";
    canvas.style.height = H + "px";
    var ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);

    var rows = visibleModels();
    var textCol = cssVar("--text") || "#eef0f6";
    var mutedCol = cssVar("--muted") || "#9aa1b5";
    var borderCol = cssVar("--border") || "#3a4157";

    if (!rows.length) {
      ctx.fillStyle = mutedCol;
      ctx.font = "14px Inter, sans-serif";
      ctx.textAlign = "center";
      ctx.fillText("No models match this filter.", W / 2, H / 2);
      barRects = [];
      return;
    }

    var padL = 38, padR = 10, padT = 26;
    var logoH = 28, nameH = 96;                 // bottom band: logos + rotated names
    var plotB = H - logoH - nameH - 6;
    var plotH = plotB - padT;
    var maxV = Math.max.apply(null, rows.map(metricOf));
    var yMax = Math.max(10, Math.ceil(maxV / 10) * 10);

    // dashed gridlines + y labels
    var step = yMax <= 60 ? 10 : 20;
    ctx.font = "11px Inter, sans-serif";
    ctx.textAlign = "right";
    ctx.textBaseline = "middle";
    for (var g = 0; g <= yMax; g += step) {
      var gy = plotB - (g / yMax) * plotH;
      ctx.strokeStyle = borderCol;
      ctx.setLineDash([4, 5]);
      ctx.beginPath();
      ctx.moveTo(padL, gy);
      ctx.lineTo(W - padR, gy);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = mutedCol;
      ctx.fillText(String(g), padL - 7, gy);
    }

    // subtle brand watermark in the top-right corner (AA-style attribution)
    var wmX = W - padR, wmY = 13;
    ctx.font = "600 12px Inter, sans-serif";
    ctx.textAlign = "right";
    ctx.fillStyle = mutedCol;
    ctx.fillText(" Benchmark", wmX, wmY);
    var benchW = ctx.measureText(" Benchmark").width;
    ctx.fillStyle = textCol;
    ctx.fillText("SynhalEES", wmX - benchW, wmY);
    var synW = ctx.measureText("SynhalEES").width;
    var bimg = brandImg();
    if (bimg) {
      // SB brand icon badge (has its own light bg, works on dark mode too)
      ctx.drawImage(bimg, wmX - benchW - synW - 17, wmY - 7, 14, 14);
    } else {
      ctx.fillStyle = "#E04545";         // fallback: SynhalaAI brand red accent dot
      ctx.beginPath();
      ctx.arc(wmX - benchW - synW - 9, wmY, 3.5, 0, Math.PI * 2);
      ctx.fill();
    }
    var n = rows.length;
    var slot = (W - padL - padR) / n;
    var barW = Math.min(64, slot * 0.62);

    barRects = [];
    ctx.textBaseline = "alphabetic";
    rows.forEach(function (m, i) {
      var v = metricOf(m);
      var x = padL + slot * i + (slot - barW) / 2;
      var h = (v / yMax) * plotH;
      var y = plotB - h;
      var col = barColor(m.provider);

      // bar with rounded top
      ctx.fillStyle = col;
      var r = Math.min(6, barW / 3);
      ctx.beginPath();
      ctx.moveTo(x, y + r);
      ctx.arcTo(x, y, x + r, y, r);
      ctx.arcTo(x + barW, y, x + barW, y + r, r);
      ctx.lineTo(x + barW, plotB);
      ctx.lineTo(x, plotB);
      ctx.closePath();
      ctx.fill();

      // value label: inside the bar when it fits, else above it
      var label = String(Math.round(v));
      ctx.font = "700 13px Inter, sans-serif";
      ctx.textAlign = "center";
      if (h > 30 && barW >= 26) {
        ctx.fillStyle = "#fff";
        ctx.fillText(label, x + barW / 2, y + 18);
      } else {
        ctx.fillStyle = textCol;
        ctx.fillText(label, x + barW / 2, y - 6);
      }

      barRects.push({ x: x, y: y, w: barW, h: h, m: m });

      // provider logo (or initial dot) under the axis
      var cx = padL + slot * i + slot / 2;
      var file = logoFile(m.provider);
      var img = file ? logoImg(file) : null;
      if (img) {
        // dark mode: black glyphs (OpenAI, Anthropic) vanish on the navy
        // background, so paint a white rounded badge behind every logo
        var darkTheme = document.documentElement.getAttribute("data-theme") !== "light";
        if (darkTheme) {
          var bx = cx - 12, by = plotB + 5, bs = 24, br = 6;
          ctx.fillStyle = "#ffffff";
          ctx.beginPath();
          ctx.moveTo(bx + br, by);
          ctx.arcTo(bx + bs, by, bx + bs, by + bs, br);
          ctx.arcTo(bx + bs, by + bs, bx, by + bs, br);
          ctx.arcTo(bx, by + bs, bx, by, br);
          ctx.arcTo(bx, by, bx + bs, by, br);
          ctx.closePath();
          ctx.fill();
        }
        ctx.drawImage(img, cx - 9, plotB + 8, 18, 18);
      } else {
        ctx.fillStyle = col;
        ctx.beginPath();
        ctx.arc(cx, plotB + 17, 8, 0, Math.PI * 2);
        ctx.fill();
        ctx.fillStyle = "#fff";
        ctx.font = "700 10px Inter, sans-serif";
        ctx.fillText(m.provider.charAt(0).toUpperCase(), cx, plotB + 21);
      }

      // rotated model name
      ctx.save();
      ctx.translate(cx + 4, plotB + logoH + 12);
      ctx.rotate(-Math.PI / 4);
      ctx.fillStyle = textCol;
      ctx.font = "11px Inter, sans-serif";
      ctx.textAlign = "right";
      var nm = m.name.length > 22 ? m.name.slice(0, 21) + "..." : m.name;
      ctx.fillText(nm, 0, 0);
      ctx.restore();
    });
  }

  /* ---------- tooltip + click-through to the radar modal ---------- */

  function hitBar(mx, my) {
    for (var i = 0; i < barRects.length; i++) {
      var b = barRects[i];
      if (mx >= b.x - 4 && mx <= b.x + b.w + 4 && my >= b.y - 4) return b;
    }
    return null;
  }

  function bindTooltip() {
    var canvas = $("#chart");
    var tip = $("#chart-tip");
    canvas.addEventListener("mousemove", function (e) {
      var rect = canvas.getBoundingClientRect();
      var hit = hitBar(e.clientX - rect.left, e.clientY - rect.top);
      if (!hit) { tip.hidden = true; canvas.style.cursor = ""; return; }
      var m = hit.m;
      tip.innerHTML =
        "<strong>" + esc(m.name) + "</strong>" +
        "<div class='ct-sub'>" + esc(m.provider) + "</div>" +
        ["overall", "text", "vision", "audio"].map(function (k) {
          var v = k === "overall" ? m.overall : m.modalities[k];
          return "<div class='ct-row" + (k === st.metric ? " on" : "") + "'><span>" +
            METRIC_LABEL[k] + "</span><span>" + (v == null ? "-" : v.toFixed(1)) + "</span></div>";
        }).join("");
      tip.hidden = false;
      var wrap = canvas.parentElement.getBoundingClientRect();
      var tx = e.clientX - wrap.left + 14;
      if (tx + tip.offsetWidth > wrap.width) tx = e.clientX - wrap.left - tip.offsetWidth - 14;
      tip.style.left = tx + "px";
      tip.style.top = (e.clientY - wrap.top - 10) + "px";
      canvas.style.cursor = "pointer";
    });
    canvas.addEventListener("mouseleave", function () { tip.hidden = true; });
    canvas.addEventListener("click", function (e) {
      // click a bar -> open that model's radar modal (modal is owned by app.js)
      var rect = canvas.getBoundingClientRect();
      var hit = hitBar(e.clientX - rect.left, e.clientY - rect.top);
      if (!hit) return;
      var rows = document.querySelectorAll("#lb-body tr");
      for (var r = 0; r < rows.length; r++) {
        var nm = rows[r].querySelector(".model-name");
        if (nm && nm.textContent === hit.m.name) { rows[r].click(); return; }
      }
    });
  }
  /* ---------- controls ---------- */

  function poolSize() {
    return state.models.filter(function (m) {
      return metricOf(m) != null && (!st.provider || m.provider === st.provider);
    }).length;
  }

  function updateCountLabel() {
    var el = $("#chart-count");
    if (el) el.textContent = poolSize() + " models";
  }

  function updatePickBtn() {
    var btn = $("#chart-pick");
    if (!btn) return;
    var n = st.selected ? Object.keys(st.selected).length : 0;
    btn.textContent = st.selected ? "Models (" + Math.min(n, MAX_BARS) + " picked)" : "+ Select models";
  }

  function buildControls() {
    // metric tabs (Overall / Text / Vision / Audio)
    Array.prototype.forEach.call(document.querySelectorAll("#chart-tabs .tab"), function (btn) {
      btn.addEventListener("click", function () {
        Array.prototype.forEach.call(document.querySelectorAll("#chart-tabs .tab"), function (b) {
          b.classList.toggle("active", b === btn);
        });
        st.metric = btn.getAttribute("data-cmetric");
        updateCountLabel();
        draw();
      });
    });

    // organization filter
    var provSel = $("#chart-provider");
    var provs = {};
    state.models.forEach(function (m) { provs[m.provider] = true; });
    Object.keys(provs).sort().forEach(function (p) {
      var opt = document.createElement("option");
      opt.value = p;
      opt.textContent = p;
      provSel.appendChild(opt);
    });
    provSel.addEventListener("change", function () {
      st.provider = provSel.value;
      updateCountLabel();
      draw();
    });

    // "N of M models" limit selector
    var limSel = $("#chart-limit");
    [5, 10, 15, 20, MAX_BARS].forEach(function (n) {
      var opt = document.createElement("option");
      opt.value = n;
      opt.textContent = n + " of";
      if (n === MAX_BARS) opt.selected = true;
      limSel.appendChild(opt);
    });
    limSel.addEventListener("change", function () {
      st.limit = +limSel.value;
      st.selected = null;      // switching back to top-N clears custom picks
      updatePickBtn();
      draw();
    });
    updateCountLabel();

    buildPicker();
  }

  /* ---------- model picker dropdown ---------- */

  function buildPicker() {
    var btn = $("#chart-pick");
    var panel = $("#chart-picker");
    var list = $("#chart-picker-list");

    var sorted = state.models.slice().sort(function (a, b) { return b.overall - a.overall; });
    sorted.forEach(function (m) {
      var lab = document.createElement("label");
      lab.className = "pk-item";
      var file = logoFile(m.provider);
      lab.innerHTML =
        "<input type='checkbox' value=\"" + esc(m.name) + "\" checked>" +
        (file ? "<img src='assets/logos/" + file + ".svg' alt='' loading='lazy' onerror='this.remove()'>" : "") +
        "<span>" + esc(m.name) + "</span>";
      list.appendChild(lab);
    });

    list.addEventListener("change", function () {
      var boxes = list.querySelectorAll("input[type=checkbox]");
      var all = true, sel = {};
      Array.prototype.forEach.call(boxes, function (b) {
        if (b.checked) sel[b.value] = true; else all = false;
      });
      st.selected = all ? null : sel;
      updatePickBtn();
      draw();
    });

    function setAll(v) {
      Array.prototype.forEach.call(list.querySelectorAll("input[type=checkbox]"), function (b) {
        b.checked = v;
      });
      st.selected = v ? null : {};
      updatePickBtn();
      draw();
    }
    $("#chart-pick-all").addEventListener("click", function () { setAll(true); });
    $("#chart-pick-none").addEventListener("click", function () { setAll(false); });

    btn.addEventListener("click", function (e) {
      e.stopPropagation();
      panel.hidden = !panel.hidden;
    });
    document.addEventListener("click", function (e) {
      if (!panel.hidden && !panel.contains(e.target) && e.target !== btn) panel.hidden = true;
    });
  }

  /* ---------- boot ---------- */

  function start(lb) {
    state.models = (lb && lb.models) || [];
    if (!state.models.length) { $("#chart-section").hidden = true; return; }
    buildControls();
    bindTooltip();
    draw();

    window.addEventListener("resize", draw);
    // re-paint when the light/dark theme flips
    new MutationObserver(draw).observe(document.documentElement, {
      attributes: true, attributeFilter: ["data-theme"]
    });
  }

  function boot() {
    if (window.SYNHALEES_LEADERBOARD) {
      start(window.SYNHALEES_LEADERBOARD);
    } else {
      fetch("assets/data/leaderboard.json")
        .then(function (r) { return r.json(); })
        .then(start)
        .catch(function () { var s = $("#chart-section"); if (s) s.hidden = true; });
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();