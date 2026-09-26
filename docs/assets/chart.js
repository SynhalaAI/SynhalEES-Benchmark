/* SynhalEES Leaderboard - AA-style comparison bar chart.
   Vanilla JS + Canvas, no dependencies. Reads the same data scripts as app.js. */
(function () {
  "use strict";

  var MAX_BARS = 25;

  var st = {
    metric: "text",         // text | vision | audio
    view: "bar",            // bar (score comparison) | pareto (score vs efficiency)
    px: "cost",             // pareto x-axis: cost | latency | tokens
    barM: "score",          // bar-view measure: score | cost | latency | tokens
    provider: "",          // "" = all organizations
    limit: MAX_BARS,       // bars shown when no custom selection
    selected: null,        // null = top-N by metric; else map of picked names
    hover: null            // model under the cursor (Pareto emphasis reset)
  };

  /* ---------- provider brand lookup (mirrors app.js PROVIDER_LOGOS) ---------- */

  var PROVIDER_LOGOS = {
    google: "google",
    gemini: "google",
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
    // embedded data URL first: it never taints the canvas (file:// safe export)
    img.src = (window.SYNHALEES_LOGOS && window.SYNHALEES_LOGOS[file]) ||
              "assets/logos/" + file + ".svg";
    img.onload = function () { draw(); };
    img.onerror = function () { imgCache[file] = "error"; draw(); };
    imgCache[file] = img;
    return null;
  }

  // Chrome lumps a huge source straight down to a 16-24px chip in one step (the
  // Google mark ships as a 3840px raster inside its SVG), which goes mushy, so
  // every logo is sampled through one cached ~96px intermediate instead.
  var smallCache = {};

  function logoSmall(file, img) {
    var hit = smallCache[file];
    if (hit) return hit;
    var s = 96;
    var c = document.createElement("canvas");
    c.width = s;
    c.height = s;
    c.getContext("2d").drawImage(img, 0, 0, s, s);
    smallCache[file] = c;
    return c;
  }

  // Drawable logo art, or null while it loads / when the export retries without
  // images (a tainted local SVG would break toDataURL).
  function logoArt(file, noImgs) {
    if (!file || noImgs) return null;
    var img = logoImg(file);
    return img ? logoSmall(file, img) : null;
  }

  /* ---------- brand chip ---------- */

  // One brand mark everywhere (bar axis + Pareto points): a light disc, the
  // provider glyph inset with breathing room, and the model colour used only as
  // the RING. The colour is never painted *behind* the artwork -- doing that is
  // what used to swallow the glyph, because the logo filled the disc edge to
  // edge and the ring was stroked straight through the middle of it.
  function drawBrandChip(ctx, cx, cy, dia, ringCol, art, initial) {
    var r = dia / 2;
    if (art) {
      ctx.fillStyle = "#ffffff";
      ctx.beginPath();
      ctx.arc(cx, cy, r, 0, Math.PI * 2);
      ctx.fill();
      var box = dia - (dia >= 20 ? 7 : 5);   // glyph inset: ring clears the art
      ctx.save();
      ctx.beginPath();
      ctx.arc(cx, cy, r - 0.5, 0, Math.PI * 2);
      ctx.clip();
      ctx.drawImage(art, cx - box / 2, cy - box / 2, box, box);
      ctx.restore();
    } else {
      // no logo on file: model-colour disc with the provider initial
      ctx.fillStyle = ringCol;
      ctx.beginPath();
      ctx.arc(cx, cy, r, 0, Math.PI * 2);
      ctx.fill();
      ctx.fillStyle = "#fff";
      ctx.font = "700 " + Math.round(dia * 0.42) + "px Inter, sans-serif";
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText(initial, cx, cy + 0.5);
    }
    ctx.strokeStyle = ringCol;
    ctx.lineWidth = dia >= 20 ? 2 : 1.5;
    ctx.beginPath();
    ctx.arc(cx, cy, r, 0, Math.PI * 2);
    ctx.stroke();
    ctx.lineWidth = 1;
  }

  // brand icon for the watermark badge (PNG with its own light background)
  var brandIcon = null;
  function brandImg() {
    if (brandIcon === "error") return null;
    if (brandIcon) return brandIcon.complete && brandIcon.naturalWidth ? brandIcon : null;
    var img = new Image();
    img.src = window.SYNHALEES_BRAND_ICON || "assets/icon.png";
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
    return m.modalities[st.metric];
  }

  var METRIC_LABEL = { text: "Text", vision: "Vision", audio: "Audio" };
  var PX_LABEL = { cost: "Cost ($/run)", latency: "Latency (ms)", tokens: "Tokens" };

  /* bar-view measure helpers: bars plot score (default) or usage telemetry */
  function barVal(m) {
    if (st.barM === "score") return metricOf(m);
    var u = m.usage && m.usage[st.metric];
    return u && u[st.barM] != null ? u[st.barM] : null;
  }

  // label drawn on/above each bar (score stays an integer, usage keeps units)
  function fmtBarVal(v) {
    if (st.barM === "score") return String(Math.round(v));
    if (st.barM === "cost") return "$" + v.toFixed(2);
    return Math.round(v).toLocaleString("en-US");
  }

  // y-axis tick label for the active bar measure
  function fmtBarTick(v) {
    if (st.barM === "score") return String(v);
    if (st.barM === "cost") return v === 0 ? "$0" : v < 0.01 ? "$" + v.toFixed(4) : "$" + v.toFixed(2);
    return Math.round(v).toLocaleString("en-US");
  }

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

  // Featured model (picked via the chart's own dropdown, independent of
  // the head-to-head compare table): its bar gets the SynhalaAI red while
  // every rival bar is dimmed to a neutral tone -- same emphasis pattern
  // as the compare table + PNG export.
  function featuredName() {
    var fsel = $("#chart-featured");
    if (fsel) return fsel.value;   // "" = "No highlight"
    return state.models.length ? state.models[0].name : "";
  }

  function draw(opts) {
    if (st.view === "pareto") return drawPareto(opts);
    scatterPts = [];
    var canvas = (opts && opts.canvas) || $("#chart");
    if (!canvas || !state.models.length) return;
    var W = (opts && opts.width) || canvas.parentElement.clientWidth;
    var H = (opts && opts.height) || 460;
    var dpr = (opts && opts.dpr) || window.devicePixelRatio || 1;
    canvas.width = W * dpr;
    canvas.height = H * dpr;
    canvas.style.width = W + "px";
    canvas.style.height = H + "px";
    var ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);

    var rows = visibleModels();
    if (st.barM !== "score") {
      // usage measure: drop models without telemetry, then order best-first.
      // Lower is better for cost, latency and tokens, so the cheapest / fastest
      // / leanest bar leads - the mirror image of the score view, where the
      // highest bar wins. (Ranking membership is still decided upstream by
      // score, so the Top-N pool never changes with the measure.)
      rows = rows.filter(function (m) { return barVal(m) != null; });
      rows.sort(function (a, b) { return barVal(a) - barVal(b); });
    }
    var textCol = cssVar("--text") || "#eef0f6";
    var mutedCol = cssVar("--muted") || "#9aa1b5";
    var borderCol = cssVar("--border") || "#3a4157";
    var featured = featuredName();
    var hasFeatured = rows.some(function (m) { return m.name === featured; });
    var featRed = cssVar("--red-bright") || "#E04545";

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
    var maxV = Math.max.apply(null, rows.map(barVal));
    var ticks, yMax;
    if (st.barM === "score") {
      yMax = Math.max(10, Math.ceil(maxV / 10) * 10);
      var step = yMax <= 60 ? 10 : 20;
      ticks = [];
      for (var g = 0; g <= yMax; g += step) ticks.push(g);
    } else {
      // nice ticks for usage measures ($ / ms / tokens), spanning the top bar
      ticks = niceTicks(0, maxV, 5);
      yMax = ticks[ticks.length - 1];
      var tstep = ticks.length > 1 ? ticks[1] - ticks[0] : maxV;
      while (yMax < maxV) { yMax += tstep; ticks.push(yMax); }
      if (!(yMax > 0)) { yMax = 1; ticks = [0, 1]; }   // all-zero measure: keep the axis sane
    }

    // dashed gridlines + y labels (the left gutter widens for wide usage labels)
    ctx.font = "11px Inter, sans-serif";
    if (st.barM !== "score") {
      var lw = 0;
      ticks.forEach(function (t) { lw = Math.max(lw, ctx.measureText(fmtBarTick(t)).width); });
      padL = Math.max(padL, Math.ceil(lw) + 16);
    }
    ctx.textAlign = "right";
    ctx.textBaseline = "middle";
    ticks.forEach(function (t) {
      var gy = plotB - (t / yMax) * plotH;
      ctx.strokeStyle = borderCol;
      ctx.setLineDash([4, 5]);
      ctx.beginPath();
      ctx.moveTo(padL, gy);
      ctx.lineTo(W - padR, gy);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = mutedCol;
      ctx.fillText(fmtBarTick(t), padL - 7, gy);
    });

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
    var bimg = (opts && opts.noImgs) ? null : brandImg();
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
      var v = barVal(m);
      var x = padL + slot * i + (slot - barW) / 2;
      var h = (v / yMax) * plotH;
      var y = plotB - h;
      var isFeat = hasFeatured && m.name === featured;
      var col = hasFeatured ? (isFeat ? featRed : mutedCol) : barColor(m.provider);

      // full-height background column behind the featured bar so it is
      // easy to spot at a glance (same pattern as the reference leaderboard)
      if (isFeat) {
        ctx.globalAlpha = document.documentElement.getAttribute("data-theme") !== "light" ? 0.14 : 0.08;
        ctx.fillStyle = featRed;
        var bandW = Math.min(slot - 2, barW * 1.4);  // hug the bar, not the whole slot
        ctx.fillRect(x + (barW - bandW) / 2, padT, bandW, plotH);
        ctx.globalAlpha = 1;
      }

      // bar with rounded top (rivals dimmed when a featured model is shown)
      ctx.fillStyle = col;
      if (hasFeatured && !isFeat) ctx.globalAlpha = 0.55;
      var r = Math.min(6, barW / 3);
      ctx.beginPath();
      ctx.moveTo(x, y + r);
      ctx.arcTo(x, y, x + r, y, r);
      ctx.arcTo(x + barW, y, x + barW, y + r, r);
      ctx.lineTo(x + barW, plotB);
      ctx.lineTo(x, plotB);
      ctx.closePath();
      ctx.fill();
      ctx.globalAlpha = 1;

      // value label: inside the bar when it fits, else above it
      var label = fmtBarVal(v);
      ctx.font = "700 13px Inter, sans-serif";
      ctx.textAlign = "center";
      var labelW = ctx.measureText(label).width;
      if (h > 30 && barW >= 26 && labelW <= barW - 8) {
        ctx.fillStyle = hasFeatured && !isFeat ? textCol : "#fff";
        ctx.fillText(label, x + barW / 2, y + 18);
      } else {
        ctx.fillStyle = hasFeatured && !isFeat ? mutedCol : textCol;
        ctx.fillText(label, x + barW / 2, y - 6);
      }

      barRects.push({ x: x, y: y, w: barW, h: h, m: m });

      // provider brand chip (or initial disc) under the axis
      var cx = padL + slot * i + slot / 2;
      drawBrandChip(ctx, cx, plotB + 17, 24, col,
        logoArt(logoFile(m.provider), opts && opts.noImgs),
        m.provider.charAt(0).toUpperCase());

      // rotated model name
      ctx.save();
      ctx.translate(cx + 4, plotB + logoH + 12);
      ctx.rotate(-Math.PI / 4);
      ctx.fillStyle = hasFeatured ? (isFeat ? featRed : mutedCol) : textCol;
      ctx.font = (isFeat ? "700 " : "") + "11px Inter, sans-serif";
      ctx.textAlign = "right";
      var nm = m.name.length > 22 ? m.name.slice(0, 21) + "..." : m.name;
      ctx.fillText(nm, 0, 0);
      ctx.restore();
    });
  }

  /* ---------- Pareto frontier scatter (score vs efficiency) ---------- */

  var scatterPts = [];   // hit-test circles for the Pareto view

  function usageVal(m) {
    var u = m.usage && m.usage[st.metric];
    return u && u[st.px] != null ? u[st.px] : null;
  }

  function paretoRows() {
    return visibleModels().filter(function (m) {
      return metricOf(m) != null && usageVal(m) != null;
    });
  }

  // A point is Pareto-optimal when no rival model has an equal-or-better
  // score at an equal-or-lower cost (at least one of the two strictly better).
  function paretoMask(rows) {
    return rows.map(function (a, i) {
      var s = metricOf(a), x = usageVal(a);
      return !rows.some(function (b, j) {
        if (i === j) return false;
        var bs = metricOf(b), bx = usageVal(b);
        return bx <= x && bs >= s && (bx < x || bs > s);
      });
    });
  }

  function fmtPx(v, k) {
    k = k || st.px;
    if (k === "cost") return "$" + v.toFixed(4);
    if (k === "latency") return Math.round(v).toLocaleString("en-US") + " ms";
    return Math.round(v).toLocaleString("en-US");
  }

  function fmtTick(v) {
    if (st.px === "cost") return "$" + v.toFixed(2);
    return Math.round(v).toLocaleString("en-US");
  }

  // linear "nice" ticks (1 / 2 / 2.5 / 5 / 10 x 10^k)
  function niceTicks(lo, hi, count) {
    var span = hi - lo;
    if (span <= 0) return [lo];
    var raw = span / count;
    var mag = Math.pow(10, Math.floor(Math.log10(raw)));
    var norm = raw / mag;
    var step = (norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 2.5 ? 2.5 : norm <= 5 ? 5 : 10) * mag;
    var out = [];
    for (var v = Math.ceil(lo / step) * step; v <= hi + step * 1e-9; v += step) {
      out.push(+v.toFixed(10));
    }
    return out;
  }

  // log-domain ticks; muls = mantissas to label per decade
  function logTicks(lo, hi, muls) {
    var out = [];
    for (var e = Math.floor(lo) - 1; e <= Math.ceil(hi) + 1; e++) {
      for (var i = 0; i < muls.length; i++) {
        var v = muls[i] * Math.pow(10, e);
        var lv = Math.log10(v);
        if (lv >= lo - 1e-9 && lv <= hi + 1e-9) out.push(v);
      }
    }
    return out;
  }

  function drawPareto(opts) {
    var canvas = (opts && opts.canvas) || $("#chart");
    if (!canvas || !state.models.length) return;
    var W = (opts && opts.width) || canvas.parentElement.clientWidth;
    var H = (opts && opts.height) || 460;
    var dpr = (opts && opts.dpr) || window.devicePixelRatio || 1;
    canvas.width = W * dpr;
    canvas.height = H * dpr;
    canvas.style.width = W + "px";
    canvas.style.height = H + "px";
    var ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);
    barRects = [];
    scatterPts = [];
    var labelBoxes = [];   // placed label rects (collision avoidance)

    var rows = paretoRows();
    var textCol = cssVar("--text") || "#eef0f6";
    var mutedCol = cssVar("--muted") || "#9aa1b5";
    var borderCol = cssVar("--border") || "#3a4157";
    var goldCol = cssVar("--gold") || "#f5b301";
    var featRed = cssVar("--red-bright") || "#E04545";
    // colour of the surface the chart sits on: the card on screen, the export
    // sheet in the PNG. Used to halo label text so dashed lines never cut it.
    var surfaceCol = (opts && opts.bg) || cssVar("--navy-card") || cssVar("--navy") || "#2b3044";
    // model under the cursor keeps full emphasis (PNG exports pass hover: null)
    var hoverNm = opts && "hover" in opts ? opts.hover : st.hover;
    var featured = featuredName();
    var hasFeatured = rows.some(function (m) { return m.name === featured; });

    if (!rows.length) {
      ctx.fillStyle = mutedCol;
      ctx.font = "14px Inter, sans-serif";
      ctx.textAlign = "center";
      ctx.fillText("No models match this filter.", W / 2, H / 2);
      return;
    }

    var padL = 62, padR = 26, padT = 34, padB = 56;
    var plotB = H - padB, plotH = plotB - padT, plotW = W - padL - padR;

    // y domain: benchmark score (0..100)
    var maxS = Math.max.apply(null, rows.map(metricOf));
    var yMax = Math.min(100, Math.max(10, Math.ceil(maxS / 10) * 10));

    // x domain (+ optional log10 mapping)
    var xs = rows.map(usageVal);
    var xmin = Math.min.apply(null, xs), xmax = Math.max.apply(null, xs);
    // auto scale: log only when the range actually needs it (> half a decade)
    var useLog = xmin > 0 && xmax / xmin >= 5;
    var lo, hi;
    if (useLog) {
      lo = Math.log10(xmin); hi = Math.log10(xmax);
      var lpad = Math.max((hi - lo) * 0.08, 0.12);
      lo -= lpad; hi += lpad;
    } else {
      var span = xmax - xmin || Math.abs(xmin) * 0.2 || 1;
      lo = xmin - span * 0.08; hi = xmax + span * 0.08;
      if (lo < 0 && xmin >= 0) lo = 0;
    }

    function X(v) {
      var t = useLog ? Math.log10(v) : v;
      return padL + (t - lo) / (hi - lo) * plotW;
    }
    function Y(s) { return plotB - (s / yMax) * plotH; }

    // ---- dominance zones: the frontier ceiling splits the plane in two ----
    // step(x) = the best score any shipped model reaches at that x or cheaper:
    //   above it -> "Efficient"   (no model is both cheaper and better)
    //   below it -> "Inefficient" (a rival is at least as cheap AND scores more)
    // Both tints are painted first so gridlines, the frontier and the chips all
    // sit on top of them.
    var mask = paretoMask(rows);
    var front = [];
    rows.forEach(function (m, i) { if (mask[i]) front.push(m); });
    front.sort(function (a, b) { return usageVal(a) - usageVal(b); });
    if (front.length) {
      var zoneGood = cssVar("--good") || "#2e9e5b";
      var zoneBad = cssVar("--red-bright") || "#E04545";
      var light = document.documentElement.getAttribute("data-theme") === "light";
      // the inefficient wash covers most of the plane, so it stays lighter than
      // the efficient one - two readable zones without turning the card red
      var zGoodA = light ? 0.085 : 0.115;
      var zBadA = light ? 0.055 : 0.08;
      var zL = padL, zR = W - padR, zT = padT, zB = plotB;
      var stepPts = front.map(function (m) {
        return { x: X(usageVal(m)), y: Y(metricOf(m)) };
      });
      // staircase: flat at each frontier score, then a step up at that model x
      var stair = [[zL, zB], [stepPts[0].x, zB]];
      stepPts.forEach(function (p, i) {
        if (i) stair.push([p.x, stepPts[i - 1].y]);
        stair.push([p.x, p.y]);
      });
      stair.push([zR, stepPts[stepPts.length - 1].y]);
      var fillZone = function (pts, col, alpha) {
        ctx.globalAlpha = alpha;
        ctx.fillStyle = col;
        ctx.beginPath();
        pts.forEach(function (p, i) { if (i) ctx.lineTo(p[0], p[1]); else ctx.moveTo(p[0], p[1]); });
        ctx.closePath();
        ctx.fill();
        ctx.globalAlpha = 1;
      };
      fillZone([[zL, zT]].concat(stair, [[zR, zT]]), zoneGood, zGoodA);
      fillZone(stair.concat([[zR, zB]]), zoneBad, zBadA);

      // zone captions: top-left of the efficient half, bottom-right of the pocket
      ctx.font = "600 11px Inter, sans-serif";
      ctx.textBaseline = "middle";
      ctx.textAlign = "left";
      ctx.fillStyle = zoneGood;
      ctx.fillText("Efficient", zL + 9, zT + 11);
      var effW = ctx.measureText("Efficient").width;
      ctx.textAlign = "right";
      ctx.fillStyle = zoneBad;
      ctx.fillText("Inefficient", zR - 9, zB - 11);
      var ineffW = ctx.measureText("Inefficient").width;
      // keep model names off the captions (same reserve list as the watermark)
      labelBoxes.push({ x0: zL + 4, x1: zL + 13 + effW, y0: zT + 2, y1: zT + 21 });
      labelBoxes.push({ x0: zR - 13 - ineffW, x1: zR - 4, y0: zB - 21, y1: zB - 2 });
    }

    // ---- gridlines + tick labels ----
    ctx.font = "11px Inter, sans-serif";
    ctx.textBaseline = "middle";
    ctx.textAlign = "right";
    var yStep = yMax <= 60 ? 10 : 20;
    for (var g = 0; g <= yMax; g += yStep) {
      var gy = Y(g);
      ctx.strokeStyle = borderCol;
      ctx.setLineDash([4, 5]);
      ctx.beginPath();
      ctx.moveTo(padL, gy);
      ctx.lineTo(W - padR, gy);
      ctx.stroke();
      ctx.fillStyle = mutedCol;
      ctx.fillText(String(g), padL - 7, gy);
    }
    var xTicks = useLog
      ? (function () {
          var t = logTicks(lo, hi, [1, 2, 3, 5, 7]);
          return t.length > 8 ? logTicks(lo, hi, [1]) : t;
        })()
      : niceTicks(lo, hi, 5);
    ctx.textAlign = "center";
    ctx.textBaseline = "top";
    xTicks.forEach(function (tv) {
      var gx = X(tv);
      if (gx < padL - 1 || gx > W - padR + 1) return;
      ctx.strokeStyle = borderCol;
      ctx.setLineDash([4, 5]);
      ctx.beginPath();
      ctx.moveTo(gx, padT);
      ctx.lineTo(gx, plotB);
      ctx.stroke();
      ctx.fillStyle = mutedCol;
      ctx.fillText(fmtTick(tv), gx, plotB + 8);
    });
    ctx.setLineDash([]);

    // plot frame
    ctx.strokeStyle = borderCol;
    ctx.beginPath();
    ctx.moveTo(padL, padT);
    ctx.lineTo(padL, plotB);
    ctx.lineTo(W - padR, plotB);
    ctx.stroke();

    // subtle brand watermark in the top-right corner (mirrors the bar view)
    var wmX = W - padR, wmY = 14;
    ctx.font = "600 12px Inter, sans-serif";
    ctx.textAlign = "right";
    ctx.textBaseline = "alphabetic";
    ctx.fillStyle = mutedCol;
    ctx.fillText(" Benchmark", wmX, wmY);
    var benchW = ctx.measureText(" Benchmark").width;
    ctx.fillStyle = textCol;
    ctx.fillText("SynhalEES", wmX - benchW, wmY);
    var synW = ctx.measureText("SynhalEES").width;
    var bimg = (opts && opts.noImgs) ? null : brandImg();
    if (bimg) {
      ctx.drawImage(bimg, wmX - benchW - synW - 17, wmY - 10, 14, 14);
    } else {
      ctx.fillStyle = "#E04545";
      ctx.beginPath();
      ctx.arc(wmX - benchW - synW - 9, wmY - 3.5, 3.5, 0, Math.PI * 2);
      ctx.fill();
    }
    // reserve the watermark band so no model name is printed over the brand
    labelBoxes.push({ x0: wmX - benchW - synW - 24, x1: wmX + 2, y0: 1, y1: 23 });

    // ---- Pareto frontier (dashed gold staircase, lower-left -> upper-right) ----
    if (front.length > 1) {
      ctx.strokeStyle = goldCol;
      ctx.lineWidth = 2;
      ctx.setLineDash([7, 6]);
      ctx.beginPath();
      front.forEach(function (m, i) {
        var fx = X(usageVal(m)), fy = Y(metricOf(m));
        if (i === 0) ctx.moveTo(fx, fy); else ctx.lineTo(fx, fy);
      });
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.lineWidth = 1;
    }

    // ---- points ----
    var chipR = 9.5;                 // brand-chip radius (was a bare 8px disc)
    rows.forEach(function (m, i) {
      var x = X(usageVal(m)), y = Y(metricOf(m));
      var isFeat = hasFeatured && m.name === featured;
      var opt = mask[i];
      var col = hasFeatured ? (isFeat ? featRed : mutedCol) : barColor(m.provider);

      // emphasis: a highlighted model outranks the frontier emphasis; with no
      // highlight the frontier leads, so the points that are NOT on it recede
      // (name included). Whatever the cursor is on is always drawn full strength.
      var dim = 1;
      if (hasFeatured) dim = isFeat ? 1 : 0.6;
      else if (!opt) dim = 0.55;
      if (hoverNm && m.name === hoverNm) dim = 1;
      ctx.globalAlpha = dim;

      // gold halo marks the Pareto-optimal set
      if (opt) {
        ctx.strokeStyle = goldCol;
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.arc(x, y, chipR + 3.5, 0, Math.PI * 2);
        ctx.stroke();
        ctx.lineWidth = 1;
      }

      // brand chip: logo on its own light disc, model colour as the ring only
      drawBrandChip(ctx, x, y, chipR * 2, col,
        logoArt(logoFile(m.provider), opts && opts.noImgs),
        m.provider.charAt(0).toUpperCase());
      ctx.globalAlpha = 1;   // reset: the name halo below stays opaque

      // model name ABOVE the point by default: the point sits on a gridline
      // and the dashed frontier runs through that same row, which used to
      // slice the name in half. It only moves beside the point when the slot
      // above is taken by another label, the brand watermark or a plot edge.
      ctx.font = (isFeat ? "700 " : "600 ") + "11px Inter, sans-serif";
      ctx.fillStyle = hasFeatured ? (isFeat ? featRed : mutedCol) : textCol;
      ctx.textBaseline = "middle";
      var nm = m.name.length > 20 ? m.name.slice(0, 19) + "..." : m.name;
      var tw = ctx.measureText(nm).width;
      var lh = 14;
      var gap = chipR + (opt ? 5 : 3);   // clear the gold halo when there is one
      var candidates = [
        { align: "center", lx: x, ly: y - gap - lh / 2 },
        { align: "left", lx: x + chipR + 8, ly: y },
        { align: "right", lx: x - chipR - 8, ly: y }
      ];
      var placed = null;
      for (var ci = 0; ci < candidates.length; ci++) {
        var cand = candidates[ci];
        var x0 = cand.align === "left" ? cand.lx
               : cand.align === "right" ? cand.lx - tw
               : cand.lx - tw / 2;
        var box = { x0: x0, x1: x0 + tw, y0: cand.ly - lh / 2, y1: cand.ly + lh / 2 };
        // keep the name inside the canvas and off the axis band
        if (box.x0 < padL - 6 || box.x1 > W - 6 || box.y0 < 2 || box.y1 > plotB - 2) continue;
        var hits = labelBoxes.some(function (b) {
          return box.x0 < b.x1 && box.x1 > b.x0 && box.y0 < b.y1 && box.y1 > b.y0;
        });
        if (!hits) { placed = { cand: cand, box: box }; break; }
      }
      if (!placed) placed = { cand: candidates[0], box: null };  // give up: draw anyway
      ctx.textAlign = placed.cand.align;
      // halo in the surface colour keeps the name readable wherever a dashed
      // frontier segment or gridline still passes underneath it
      ctx.lineJoin = "round";
      ctx.lineWidth = 3;
      ctx.strokeStyle = surfaceCol;
      ctx.strokeText(nm, placed.cand.lx, placed.cand.ly);
      ctx.lineWidth = 1;
      ctx.globalAlpha = dim;     // the name carries the point's emphasis
      ctx.fillText(nm, placed.cand.lx, placed.cand.ly);
      ctx.globalAlpha = 1;
      if (placed.box) labelBoxes.push(placed.box);

      scatterPts.push({ x: x, y: y, r: chipR + 5, m: m, opt: opt, xv: usageVal(m) });
    });

    // ---- axis titles ----
    ctx.save();
    ctx.translate(16, padT + plotH / 2);
    ctx.rotate(-Math.PI / 2);
    ctx.fillStyle = mutedCol;
    ctx.font = "600 12px Inter, sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(METRIC_LABEL[st.metric] + " score", 0, 0);
    ctx.restore();
    ctx.textAlign = "center";
    ctx.textBaseline = "top";
    ctx.font = "600 12px Inter, sans-serif";
    ctx.fillStyle = mutedCol;
    ctx.fillText(PX_LABEL[st.px] + (useLog ? " (log scale)" : ""), padL + plotW / 2, H - 20);
  }

  /* ---------- tooltip + click-through to the radar modal ---------- */

  function hitBar(mx, my) {
    for (var i = 0; i < barRects.length; i++) {
      var b = barRects[i];
      if (mx >= b.x - 4 && mx <= b.x + b.w + 4 && my >= b.y - 4) return b;
    }
    return null;
  }

  function hitPoint(mx, my) {
    for (var i = 0; i < scatterPts.length; i++) {
      var p = scatterPts[i];
      var dx = mx - p.x, dy = my - p.y;
      if (dx * dx + dy * dy <= p.r * p.r) return p;
    }
    return null;
  }

  function hitAny(mx, my) {
    return st.view === "pareto" ? hitPoint(mx, my) : hitBar(mx, my);
  }

  // controls that change the plotted rows/axes must drop the open tooltip,
  // otherwise it keeps showing the pre-switch values (e.g. "Cost" while the
  // chart already shows Latency)
  function hideTip() {
    var tip = $("#chart-tip");
    if (tip) tip.hidden = true;
    st.hover = null;   // every caller redraws, so the emphasis resets with it
  }

  // The Pareto view fades the points that are not on the frontier, so the one
  // under the cursor is pulled back to full strength while it is hovered.
  function setHover(name) {
    if (st.hover === name) return;
    st.hover = name;
    if (st.view === "pareto") draw();
  }

  function bindTooltip() {
    var canvas = $("#chart");
    var tip = $("#chart-tip");
    canvas.addEventListener("mousemove", function (e) {
      var rect = canvas.getBoundingClientRect();
      var hit = hitAny(e.clientX - rect.left, e.clientY - rect.top);
      if (!hit) { tip.hidden = true; canvas.style.cursor = ""; setHover(null); return; }
      setHover(st.view === "pareto" ? hit.m.name : null);
      var m = hit.m;
      if (st.view === "pareto") {
        tip.innerHTML =
          "<strong>" + esc(m.name) + "</strong>" +
          "<div class='ct-sub'>" + esc(m.provider) +
          (hit.opt ? " &middot; <span class='ct-opt'>Pareto-optimal</span>" : "") + "</div>" +
          "<div class='ct-row'><span>Score</span><span>" + metricOf(m).toFixed(1) + "</span></div>" +
          // highlight the measure on the x-axis, the same row the bar view
          // lights up - the score is on the y-axis and reads as a plain value
          "<div class='ct-row on'><span>" + PX_LABEL[st.px] + "</span><span>" + fmtPx(hit.xv) + "</span></div>";
        tip.hidden = false;
        var pwrap = canvas.parentElement.getBoundingClientRect();
        var ptx = e.clientX - pwrap.left + 14;
        if (ptx + tip.offsetWidth > pwrap.width) ptx = e.clientX - pwrap.left - tip.offsetWidth - 14;
        tip.style.left = ptx + "px";
        tip.style.top = (e.clientY - pwrap.top - 10) + "px";
        canvas.style.cursor = "pointer";
        return;
      }
      var mrow = st.barM === "score" ? "" :
        "<div class='ct-row on'><span>" + PX_LABEL[st.barM] + "</span><span>" +
        fmtPx(barVal(m), st.barM) + "</span></div>";
      tip.innerHTML =
        "<strong>" + esc(m.name) + "</strong>" +
        "<div class='ct-sub'>" + esc(m.provider) + "</div>" + mrow +
        ["text", "vision", "audio"].map(function (k) {
          var v = m.modalities[k];
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
    canvas.addEventListener("mouseleave", function () { tip.hidden = true; setHover(null); });
    canvas.addEventListener("click", function (e) {
      // click a bar -> open that model's radar modal (modal is owned by app.js)
      var rect = canvas.getBoundingClientRect();
      var hit = hitAny(e.clientX - rect.left, e.clientY - rect.top);
      if (!hit) return;
      var rows = document.querySelectorAll("#lb-body tr");
      for (var r = 0; r < rows.length; r++) {
        var nm = rows[r].querySelector(".model-name");
        if (nm && nm.textContent === hit.m.name) { rows[r].click(); return; }
      }
    });
  }
  /* ---------- PNG export ---------- */

  // Renders the current chart view (same filters + highlight as on screen)
  // onto an offscreen canvas with the section background and a title line.
  // file:// pages taint any canvas that drew a local SVG/PNG image, so on a
  // SecurityError we retry with placeholder dots instead of brand images.
  function exportChartPNG() {
    var src = $("#chart");
    if (!src || !state.models.length) return;
    var dpr = window.devicePixelRatio || 1;
    var W = src.parentElement.clientWidth, H = 460;
    var padX = 24 * dpr, padT = 18 * dpr, titleH = 30 * dpr, padB = 16 * dpr;

    function buildPng(skipImgs) {
      var chartC = document.createElement("canvas");
      draw({
        canvas: chartC, width: W, height: H, dpr: dpr, noImgs: skipImgs,
        bg: cssVar("--navy") || "#2b3044", hover: null
      });
      var c = document.createElement("canvas");
      c.width = chartC.width + padX * 2;
      c.height = chartC.height + padT + titleH + padB;
      var ctx = c.getContext("2d");
      ctx.fillStyle = cssVar("--navy") || "#2b3044";
      ctx.fillRect(0, 0, c.width, c.height);
      var shown = visibleModels();
      if (st.view === "bar" && st.barM !== "score") {
        shown = shown.filter(function (m) { return barVal(m) != null; });
      }
      var title;
      if (st.view === "pareto") {
        shown = paretoRows();
        title = "SynhalEES \u2014 Pareto Frontier \u00b7 " + METRIC_LABEL[st.metric] +
          " score vs " + PX_LABEL[st.px] + " \u00b7 " +
          shown.length + " of " + poolSize() + " models";
      } else {
        title = "SynhalEES \u2014 " + METRIC_LABEL[st.metric] + " Comparison" +
          (st.barM === "score" ? "" : " \u00b7 " + PX_LABEL[st.barM]) +
          " \u00b7 " + shown.length + " of " + poolSize() + " models";
      }
      var feat = featuredName();
      if (feat && shown.some(function (m) { return m.name === feat; })) {
        title += " \u00b7 Featured: " + feat;
      }
      ctx.fillStyle = cssVar("--text") || "#eef0f6";
      ctx.font = "700 " + Math.round(15 * dpr) + "px Inter, sans-serif";
      ctx.textAlign = "left";
      ctx.textBaseline = "middle";
      ctx.fillText(title, padX, padT + titleH / 2);
      ctx.drawImage(chartC, padX, padT + titleH);
      return c.toDataURL("image/png");
    }

    var url;
    try { url = buildPng(false); }
    catch (e) {
      try { url = buildPng(true); }
      catch (e2) { alert("PNG export failed in this browser."); return; }
    }
    var a = document.createElement("a");
    a.download = st.view === "pareto"
      ? "synhalees-pareto-" + st.metric + "-" + st.px + ".png"
      : "synhalees-" + st.metric + (st.barM === "score" ? "" : "-" + st.barM) + "-comparison.png";
    a.href = url;
    a.click();
  }

  /* ---------- controls ---------- */

  function poolSize() {
    return state.models.filter(function (m) {
      if (metricOf(m) == null) return false;
      if (st.view === "bar" && st.barM !== "score" && barVal(m) == null) return false;
      return !st.provider || m.provider === st.provider;
    }).length;
  }

  function updateCountLabel() {
    var el = $("#chart-count");
    if (el) el.textContent = "/ " + poolSize();
  }

  function updatePickBtn() {
    var btn = $("#chart-pick");
    if (!btn) return;
    var n = st.selected ? Object.keys(st.selected).length : 0;
    btn.textContent = st.selected ? "Models (" + Math.min(n, MAX_BARS) + " picked)" : "+ Select models";
  }

  // measure toolbar: visible in both views - bars pick what to plot, Pareto
  // picks its x-axis (the Score option hides there: y is always the score)
  function syncMeasureControls() {
    var bar = $("#pareto-bar");
    if (bar) bar.hidden = false;
    var sel = $("#chart-pareto-x");
    if (sel) {
      var scoreOpt = sel.querySelector('option[value="score"]');
      if (scoreOpt) scoreOpt.hidden = st.view === "pareto";
      sel.value = st.view === "pareto" ? st.px : st.barM;
      sel.setAttribute("aria-label", st.view === "pareto"
        ? "X-axis efficiency metric" : "Bar chart measure");
    }
    var hint = $("#pareto-bar .pareto-hint");
    if (hint) hint.textContent = st.view === "pareto"
      ? "Dashed frontier = best score for the lowest " + PX_LABEL[st.px] + " \u00b7 click a point for details"
      : (st.barM === "score"
        ? "Ranked by score \u00b7 higher is better"
        : "Ranked by " + PX_LABEL[st.barM] + " \u00b7 lowest first (lower is better)");
  }

  // canvas accessible name follows the active view + measure
  function syncChartAria() {
    var cv = $("#chart");
    if (!cv) return;
    cv.setAttribute("aria-label", st.view === "pareto"
      ? "Score versus efficiency Pareto frontier scatter chart"
      : (st.barM === "score"
        ? "Model comparison bar chart"
        : "Model comparison bar chart by " + PX_LABEL[st.barM]));
  }

  // Cross-fade the canvas while redrawing so view/axis switches morph
  // smoothly instead of snapping. Repeated calls restart the fade cleanly.
  var swapTimer = null;
  function swapDraw() {
    var cv = $("#chart");
    if (!cv) { draw(); return; }
    if (swapTimer) clearTimeout(swapTimer);
    cv.classList.add("chart-swap");        // fades to transparent (160ms CSS)
    swapTimer = setTimeout(function () {
      draw();                               // paint the new view off-fade
      swapTimer = null;
      requestAnimationFrame(function () {
        cv.classList.remove("chart-swap"); // fade back in
      });
    }, 170);
  }

  function buildControls() {
    // view switch next to the chart title: tab-style toggle between the two
    // analytics (comparison bars <-> Pareto frontier scatter), same look as
    // the metric tabs. Both tabs always render -> fixed footprint.
    var vt = $("#chart-view-toggle");
    if (vt) {
      var vtTabs = vt.querySelectorAll(".tab");
      var syncViewTabs = function () {
        Array.prototype.forEach.call(vtTabs, function (b) {
          var on = b.getAttribute("data-cview") === st.view;
          b.classList.toggle("active", on);
          b.setAttribute("aria-selected", on ? "true" : "false");
        });
      };
      Array.prototype.forEach.call(vtTabs, function (b) {
        b.addEventListener("click", function () {
          if (b.getAttribute("data-cview") === st.view) return;
          st.view = b.getAttribute("data-cview");
          syncViewTabs();
          hideTip();
          syncMeasureControls();
          syncChartAria();
          swapDraw();
        });
      });
      syncViewTabs();
    }

    // measure selector: bars pick score/cost/latency/tokens; Pareto view uses
    // the same control as its x-axis (the Score option hides - y is the score)
    var pxSel = $("#chart-pareto-x");
    if (pxSel) {
      pxSel.addEventListener("change", function () {
        if (st.view === "pareto") {
          if (pxSel.value === "score") { pxSel.value = st.px; return; }  // hidden option guard
          st.px = pxSel.value;
        } else {
          st.barM = pxSel.value;
        }
        hideTip();
        updateCountLabel();
        syncMeasureControls();
        syncChartAria();
        swapDraw();
      });
    }
    syncMeasureControls();

    // metric tabs (Text / Vision / Audio)
    Array.prototype.forEach.call(document.querySelectorAll("#chart-tabs .tab"), function (btn) {
      btn.addEventListener("click", function () {
        Array.prototype.forEach.call(document.querySelectorAll("#chart-tabs .tab"), function (b) {
          b.classList.toggle("active", b === btn);
        });
        st.metric = btn.getAttribute("data-cmetric");
        hideTip();
        updateCountLabel();
        draw();
      });
    });

    // featured-model selector (independent of the head-to-head compare's one)
    var featSel = $("#chart-featured");
    if (featSel) {
      var noneOpt = document.createElement("option");
      noneOpt.value = "";
      noneOpt.textContent = "No highlight";
      featSel.appendChild(noneOpt);
      state.models.forEach(function (m) {
        var opt = document.createElement("option");
        opt.value = m.name;
        opt.textContent = m.name;
        featSel.appendChild(opt);
      });
      // default stays "" ("No highlight") - every bar keeps its provider color
      featSel.addEventListener("change", function () { hideTip(); draw(); });
    }

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
      hideTip();
      updateCountLabel();
      draw();
    });

    // "N of M models" limit selector
    var limSel = $("#chart-limit");
    [5, 10, 15, 20, MAX_BARS].forEach(function (n) {
      var opt = document.createElement("option");
      opt.value = n;
      opt.textContent = "Top " + n;
      if (n === MAX_BARS) opt.selected = true;
      limSel.appendChild(opt);
    });
    limSel.addEventListener("change", function () {
      st.limit = +limSel.value;
      st.selected = null;      // switching back to top-N clears custom picks
      hideTip();
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

    var sorted = state.models.slice().sort(function (a, b) { return (b.modalities.text || 0) - (a.modalities.text || 0); });
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
      hideTip();
      updatePickBtn();
      draw();
    });

    function setAll(v) {
      Array.prototype.forEach.call(list.querySelectorAll("input[type=checkbox]"), function (b) {
        b.checked = v;
      });
      st.selected = v ? null : {};
      hideTip();
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
    var expBtn = $("#chart-export");
    if (expBtn) expBtn.addEventListener("click", exportChartPNG);
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