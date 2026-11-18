"""Feature inspector dialogs for the SynhalEES Desktop Studio (`synhalees/gui.py`).

Provides:
- `open_scorecard_inspector(gui, initial_slug, compare_slug="")`
- `open_pillar_inspector(gui, pillar_slug=None)`
"""

from __future__ import annotations

import json
import webbrowser
from typing import Any

from synhalees.data import list_pillars
from synhalees.gui_helpers import compare_model_scorecards, inspect_pillar_dataset, load_model_scorecard_summary


def open_scorecard_inspector(
    gui: Any,
    initial_slug: str,
    compare_slug: str = "",
) -> None:
    """Open the interactive Model Scorecard & Side-by-Side Comparison dialog."""
    from synhalees.gui import PALETTE, _all_model_slugs

    tk, ttk = gui.tk, gui.ttk
    all_models = _all_model_slugs()
    if not initial_slug and all_models:
        initial_slug = all_models[0]

    dlg = tk.Toplevel(gui.root)
    dlg.title(f"Scorecard Inspector — {initial_slug}")
    dlg.configure(bg=PALETTE["surface"])
    dlg.transient(gui.root)
    gui._center_dialog(dlg, gui._px(980), gui._px(660))
    dlg.minsize(gui._px(780), gui._px(520))

    wrap = tk.Frame(dlg, bg=PALETTE["surface"], padx=gui._px(16), pady=gui._px(14))
    wrap.pack(fill="both", expand=True)

    # Top selector bar
    top = tk.Frame(wrap, bg=PALETTE["surface"])
    top.pack(fill="x", pady=(0, gui._px(10)))

    model_a_var = tk.StringVar(value=initial_slug)
    model_b_var = tk.StringVar(value=compare_slug or "(None — Single Model View)")
    mod_var = tk.StringVar(value="text")

    tk.Label(top, text="Model A:", bg=PALETTE["surface"], fg=PALETTE["text"], font=("Segoe UI", 9, "bold")).pack(side="left")
    cb_a = ttk.Combobox(top, textvariable=model_a_var, values=all_models, width=28, state="readonly")
    cb_a.pack(side="left", padx=(6, 12))

    tk.Label(top, text="Compare with:", bg=PALETTE["surface"], fg=PALETTE["muted"], font=("Segoe UI", 9)).pack(side="left")
    cb_b = ttk.Combobox(
        top,
        textvariable=model_b_var,
        values=["(None — Single Model View)", *all_models],
        width=28,
        state="readonly",
    )
    cb_b.pack(side="left", padx=(6, 8))

    def _swap() -> None:
        b = model_b_var.get()
        if b and not b.startswith("(None"):
            a = model_a_var.get()
            model_a_var.set(b)
            model_b_var.set(a)

    ttk.Button(top, text="Swap A/B", style="Ghost.TButton", command=_swap).pack(side="left", padx=(0, 12))
    tk.Label(top, text="Modality:", bg=PALETTE["surface"], fg=PALETTE["muted"], font=("Segoe UI", 9)).pack(side="left")
    cb_mod = ttk.Combobox(top, textvariable=mod_var, values=["text", "vision", "audio"], width=8, state="readonly")
    cb_mod.pack(side="left", padx=(6, 0))

    meta_var = tk.StringVar(value="")
    tk.Label(wrap, textvariable=meta_var, bg=PALETTE["surface"], fg=PALETTE["info"], font=("Segoe UI", 9), anchor="w").pack(
        fill="x", pady=(0, gui._px(8))
    )

    # 3 Modality KPI Cards
    kpi_row = tk.Frame(wrap, bg=PALETTE["surface"])
    kpi_row.pack(fill="x", pady=(0, gui._px(10)))
    for i in range(3):
        kpi_row.columnconfigure(i, weight=1, uniform="sc_kpi")
    kpi_vars: dict[str, tuple[Any, Any, Any]] = {}
    for idx, mod in enumerate(("text", "vision", "audio")):
        card = tk.Frame(
            kpi_row,
            bg=PALETTE["card"],
            highlightthickness=1,
            highlightbackground=PALETTE["border"],
            padx=gui._px(12),
            pady=gui._px(8),
        )
        card.grid(row=0, column=idx, sticky="nsew", padx=(0 if idx == 0 else gui._px(8), 0))
        tk.Label(card, text=mod.upper(), bg=PALETTE["card"], fg=PALETTE["muted"], font=("Segoe UI", 8, "bold")).pack(anchor="w")
        v_score = tk.StringVar(value="—")
        v_delta = tk.StringVar(value="")
        v_usage = tk.StringVar(value="")
        tk.Label(card, textvariable=v_score, bg=PALETTE["card"], fg=PALETTE["text"], font=("Segoe UI", 14, "bold")).pack(
            anchor="w", pady=(2, 0)
        )
        d_lbl = tk.Label(card, textvariable=v_delta, bg=PALETTE["card"], fg=PALETTE["ok"], font=("Segoe UI", 9, "bold"))
        d_lbl.pack(anchor="w")
        tk.Label(card, textvariable=v_usage, bg=PALETTE["card"], fg=PALETTE["muted"], font=("Segoe UI", 8)).pack(anchor="w")
        kpi_vars[mod] = (v_score, v_delta, v_usage, d_lbl)

    win_var = tk.StringVar(value="")
    tk.Label(wrap, textvariable=win_var, bg=PALETTE["surface"], fg=PALETTE["text"], font=("Segoe UI", 9, "bold"), anchor="w").pack(
        fill="x", pady=(0, gui._px(6))
    )

    tree = gui._make_scrolled_tree(
        wrap,
        columns=("pillar", "title", "score_a", "score_b", "delta", "cost", "tokens", "latency"),
        height=13,
        fill="both",
        expand=True,
    )
    tree.tag_configure("win", foreground=PALETTE["ok"])
    tree.tag_configure("loss", foreground=PALETTE["accent"])
    for col, lbl, w in (
        ("pillar", "Pillar Slug", 180),
        ("title", "Title", 160),
        ("score_a", "Model A", 85),
        ("score_b", "Model B", 85),
        ("delta", "Delta (A-B)", 90),
        ("cost", "Cost ($)", 80),
        ("tokens", "Tokens", 80),
        ("latency", "Latency (ms)", 95),
    ):
        tree.heading(col, text=lbl)
        tree.column(col, width=gui._px(w), anchor="w" if col in ("pillar", "title") else "center")

    last_cmp: dict[str, Any] = {}

    def _refresh(*_args: Any) -> None:
        nonlocal last_cmp
        slug_a = model_a_var.get().strip()
        raw_b = model_b_var.get().strip()
        slug_b = "" if (not raw_b or raw_b.startswith("(None")) else raw_b
        mod = mod_var.get().strip() or "text"
        cmp = compare_model_scorecards(slug_a, slug_b, modality=mod)
        last_cmp = cmp
        sa = cmp["a"]
        sb = cmp["b"]
        dlg.title(f"Scorecard Inspector — {sa.display_name}")

        rank_txt = f"Rank #{sa.rank_text}/{sa.total_ranked}" if sa.rank_text else "Unranked"
        oss_txt = "Open Source" if sa.open_source else "Proprietary"
        src_txt = sa.source_path or sa.source_kind
        meta_var.set(
            f"{sa.display_name} ({sa.canonical})  ·  {sa.provider}/{sa.family}  ·  {oss_txt}  ·  {rank_txt}  ·  Source: {src_txt} ({sa.date})"
        )

        for m in ("text", "vision", "audio"):
            v_sc, v_del, v_usg, d_lbl = kpi_vars[m]
            val_a = sa.modalities.get(m)
            v_sc.set(f"{val_a:.1f}%" if val_a is not None else "—")
            if sb is not None:
                val_b = sb.modalities.get(m)
                if val_a is not None and val_b is not None:
                    diff = val_a - val_b
                    v_del.set(f"vs {sb.display_name}: {diff:+.1f}%")
                    d_lbl.configure(fg=PALETTE["ok"] if diff >= 0 else PALETTE["accent"])
                else:
                    v_del.set(f"vs {sb.display_name}: —")
                    d_lbl.configure(fg=PALETTE["muted"])
            else:
                v_del.set("")
            u = sa.usage.get(m, {})
            parts = []
            if u.get("cost") is not None:
                parts.append(f"${float(u['cost']):.4f}")
            if u.get("tokens") is not None:
                parts.append(f"{int(u['tokens']):,} tok")
            if u.get("latency") is not None:
                parts.append(f"{int(u['latency'])} ms")
            v_usg.set(" · ".join(parts) if parts else "no telemetry")

        if sb is not None:
            win_var.set(
                f"{mod.upper()} Breakdown vs {sb.display_name}:  Wins {cmp['wins']}  ·  Losses {cmp['losses']}  ·  Ties {cmp['ties']}"
            )
        else:
            win_var.set(f"{mod.upper()} Per-Pillar Breakdown (double-click any pillar to inspect dataset samples)")

        tree.delete(*tree.get_children())
        for idx, r in enumerate(cmp["rows"]):
            stripe = "odd" if idx % 2 else "even"
            tags = [stripe]
            d = r["delta"]
            if d is not None:
                if d > 0.05:
                    tags.append("win")
                elif d < -0.05:
                    tags.append("loss")
            tree.insert(
                "",
                "end",
                tags=tuple(tags),
                values=(
                    r["pillar"],
                    r["title"],
                    f"{r['score_a']:.1f}%" if r["score_a"] is not None else "—",
                    f"{r['score_b']:.1f}%" if r["score_b"] is not None else "—",
                    f"{d:+.1f}%" if d is not None else "—",
                    f"${r['cost_a']:.4f}" if r["cost_a"] is not None else "—",
                    f"{int(r['tokens_a']):,}" if r["tokens_a"] else "—",
                    f"{int(r['latency_a'])}" if r["latency_a"] else "—",
                ),
            )

    def _on_pillar_dbl(_e: Any) -> None:
        sel = tree.selection()
        if not sel:
            return
        vals = tree.item(sel[0], "values")
        if vals and vals[0] in list_pillars():
            open_pillar_inspector(gui, vals[0])

    tree.bind("<Double-1>", _on_pillar_dbl)
    for v in (model_a_var, model_b_var, mod_var):
        v.trace_add("write", _refresh)
    _refresh()

    btns = tk.Frame(wrap, bg=PALETTE["surface"])
    btns.pack(fill="x", pady=(gui._px(10), 0))

    def _copy_md() -> None:
        if not last_cmp:
            return
        sa = last_cmp["a"]
        sb = last_cmp["b"]
        mod = last_cmp["modality"]
        lines = [f"| Pillar | {sa.display_name} ({mod}) |" + (f" {sb.display_name} | Delta |" if sb else "")]
        lines.append("| :--- | ---: |" + (" ---: | ---: |" if sb else ""))
        for r in last_cmp["rows"]:
            sa_s = f"{r['score_a']:.1f}%" if r["score_a"] is not None else "—"
            if sb:
                sb_s = f"{r['score_b']:.1f}%" if r["score_b"] is not None else "—"
                d_s = f"{r['delta']:+.1f}%" if r["delta"] is not None else "—"
                lines.append(f"| {r['pillar']} | {sa_s} | {sb_s} | {d_s} |")
            else:
                lines.append(f"| {r['pillar']} | {sa_s} |")
        gui.root.clipboard_clear()
        gui.root.clipboard_append("\n".join(lines))
        gui._append_log(f"[OK] Copied Markdown scorecard for '{sa.canonical}' to clipboard.\n", "ok")

    ttk.Button(btns, text="Copy Markdown Table", style="Ghost.TButton", command=_copy_md).pack(side="left")
    ttk.Button(btns, text="Close", style="Ghost.TButton", command=dlg.destroy).pack(side="right")
    dlg.bind("<Escape>", lambda _e: dlg.destroy())


def open_pillar_inspector(gui: Any, pillar_slug: str | None = None) -> None:
    """Open the read-only 15-Pillar Dataset & Sinhala Sample Inspector dialog."""
    from synhalees.gui import PALETTE

    tk, ttk = gui.tk, gui.ttk
    pillars = list_pillars()
    init_slug = pillar_slug if pillar_slug in pillars else (pillars[0] if pillars else "01_buddhist_culture")

    dlg = tk.Toplevel(gui.root)
    dlg.title(f"Pillar Dataset Browser — {init_slug}")
    dlg.configure(bg=PALETTE["surface"])
    dlg.transient(gui.root)
    gui._center_dialog(dlg, gui._px(980), gui._px(680))
    dlg.minsize(gui._px(800), gui._px(540))

    wrap = tk.Frame(dlg, bg=PALETTE["surface"], padx=gui._px(16), pady=gui._px(14))
    wrap.pack(fill="both", expand=True)

    pillar_var = tk.StringVar(value=init_slug)
    mod_var = tk.StringVar(value="All")
    eval_var = tk.StringVar(value="All")
    search_var = tk.StringVar(value="")

    # Header bar
    hdr = tk.Frame(wrap, bg=PALETTE["surface"])
    hdr.pack(fill="x", pady=(0, gui._px(8)))
    tk.Label(hdr, text="Pillar:", bg=PALETTE["surface"], fg=PALETTE["text"], font=("Segoe UI", 9, "bold")).pack(side="left")
    ttk.Combobox(hdr, textvariable=pillar_var, values=pillars, width=28, state="readonly").pack(side="left", padx=(6, 12))
    title_var = tk.StringVar(value="")
    tk.Label(hdr, textvariable=title_var, bg=PALETTE["surface"], fg=PALETTE["info"], font=("Segoe UI", 10, "bold")).pack(side="left")

    stats_var = tk.StringVar(value="")
    tk.Label(wrap, textvariable=stats_var, bg=PALETTE["surface"], fg=PALETTE["muted"], font=("Segoe UI", 9), anchor="w").pack(
        fill="x", pady=(0, gui._px(8))
    )

    # Filter bar
    fbar = tk.Frame(wrap, bg=PALETTE["surface"])
    fbar.pack(fill="x", pady=(0, gui._px(8)))
    tk.Label(fbar, text="Modality:", bg=PALETTE["surface"], fg=PALETTE["muted"], font=("Segoe UI", 9)).pack(side="left")
    ttk.Combobox(fbar, textvariable=mod_var, values=["All", "text", "vision", "audio"], width=10, state="readonly").pack(
        side="left", padx=(6, 12)
    )
    tk.Label(fbar, text="Eval Type:", bg=PALETTE["surface"], fg=PALETTE["muted"], font=("Segoe UI", 9)).pack(side="left")
    ttk.Combobox(
        fbar,
        textvariable=eval_var,
        values=["All", "exact_match", "llm_judge", "wer", "classification"],
        width=14,
        state="readonly",
    ).pack(side="left", padx=(6, 12))
    tk.Label(fbar, text="Search:", bg=PALETTE["surface"], fg=PALETTE["muted"], font=("Segoe UI", 9)).pack(side="left")
    ttk.Entry(fbar, textvariable=search_var, width=28).pack(side="left", padx=(6, 0), fill="x", expand=True)

    tree = gui._make_scrolled_tree(
        wrap,
        columns=("id", "modality", "eval_type", "media", "prompt", "gt"),
        height=10,
        fill="both",
        expand=True,
    )
    for col, lbl, w in (
        ("id", "ID", 90),
        ("modality", "Modality", 75),
        ("eval_type", "Eval Type", 110),
        ("media", "Media File", 150),
        ("prompt", "Prompt / Question", 250),
        ("gt", "Ground Truth", 230),
    ):
        tree.heading(col, text=lbl)
        tree.column(col, width=gui._px(w), anchor="w" if col not in ("id", "modality") else "center")

    # Detail preview pane (UTF-8 Sinhala read-only Text widgets)
    detail = tk.Frame(wrap, bg=PALETTE["card"], padx=gui._px(12), pady=gui._px(10))
    detail.pack(fill="x", pady=(gui._px(10), 0))
    detail.columnconfigure(0, weight=1)
    detail.columnconfigure(1, weight=1)

    tk.Label(detail, text="Full Prompt (with loader defaults)", bg=PALETTE["card"], fg=PALETTE["muted"], font=("Segoe UI", 8, "bold")).grid(
        row=0, column=0, sticky="w"
    )
    tk.Label(detail, text="Ground Truth (UTF-8 Sinhala)", bg=PALETTE["card"], fg=PALETTE["muted"], font=("Segoe UI", 8, "bold")).grid(
        row=0, column=1, sticky="w", padx=(10, 0)
    )

    prompt_box = tk.Text(
        detail, height=4, bg=PALETTE["console_bg"], fg=PALETTE["text"], font=("Segoe UI", 10), wrap="word", relief="flat", state="disabled"
    )
    prompt_box.grid(row=1, column=0, sticky="ew", pady=(4, 6))
    gt_box = tk.Text(
        detail, height=4, bg=PALETTE["console_bg"], fg=PALETTE["text"], font=("Segoe UI", 10), wrap="word", relief="flat", state="disabled"
    )
    gt_box.grid(row=1, column=1, sticky="ew", padx=(10, 0), pady=(4, 6))

    media_var = tk.StringVar(value="")
    media_bar = tk.Frame(detail, bg=PALETTE["card"])
    media_bar.grid(row=2, column=0, columnspan=2, sticky="ew")
    tk.Label(media_bar, textvariable=media_var, bg=PALETTE["card"], fg=PALETTE["info"], font=("Consolas", 9)).pack(side="left")

    current_rows: list[dict[str, Any]] = []
    filtered_map: dict[str, dict[str, Any]] = {}
    selected_row: dict[str, Any] = {}

    def _set_text(box: Any, txt: str) -> None:
        box.configure(state="normal")
        box.delete("1.0", "end")
        box.insert("1.0", txt)
        box.configure(state="disabled")

    def _open_media() -> None:
        mp = selected_row.get("media_path")
        if mp and mp.is_file():
            webbrowser.open(mp.as_uri())

    def _copy_json() -> None:
        if not selected_row:
            return
        payload = {k: v for k, v in selected_row.items() if k != "media_path"}
        gui.root.clipboard_clear()
        gui.root.clipboard_append(json.dumps(payload, indent=2, ensure_ascii=False))
        gui._append_log(f"[OK] Copied sample '{selected_row.get('id')}' JSON to clipboard.\n", "ok")

    open_media_btn = ttk.Button(media_bar, text="Open Media File", style="Ghost.TButton", command=_open_media, state="disabled")
    open_media_btn.pack(side="right", padx=(8, 0))
    ttk.Button(media_bar, text="Copy Sample JSON", style="Ghost.TButton", command=_copy_json).pack(side="right")

    def _load_pillar(*_args: Any) -> None:
        nonlocal current_rows
        rep = inspect_pillar_dataset(pillar_var.get())
        dlg.title(f"Pillar Dataset Browser — {rep.slug}")
        title_var.set(f"{rep.title_en}  —  {rep.title_si}")
        ec = rep.eval_type_counts
        stats_var.set(
            f"Text: {rep.text_count}  ·  Vision: {rep.vision_files_present}/{rep.vision_count} files  ·  "
            f"Audio: {rep.audio_files_present}/{rep.audio_count} files   |   "
            f"exact_match: {ec.get('exact_match', 0)}  ·  llm_judge: {ec.get('llm_judge', 0)}  ·  "
            f"wer: {ec.get('wer', 0)}  ·  classification: {ec.get('classification', 0)}"
        )
        current_rows = rep.rows
        _apply_filter()

    def _apply_filter(*_args: Any) -> None:
        filtered_map.clear()
        tree.delete(*tree.get_children())
        m_f = mod_var.get()
        e_f = eval_var.get()
        q = search_var.get().strip().lower()
        idx = 0
        for row in current_rows:
            if m_f != "All" and row["modality"] != m_f:
                continue
            if e_f != "All" and row["eval_type"] != e_f:
                continue
            if q and q not in f"{row['id']} {row['prompt']} {row['ground_truth']} {row['media']}".lower():
                continue
            stripe = "odd" if idx % 2 else "even"
            tags = (stripe,) if row["media_exists"] else (stripe, "warn")
            iid = tree.insert(
                "",
                "end",
                tags=tags,
                values=(
                    row["id"],
                    row["modality"],
                    row["eval_type"],
                    row["media"],
                    row["prompt"].replace("\n", " ")[:70],
                    row["ground_truth"].replace("\n", " ")[:60],
                ),
            )
            filtered_map[iid] = row
            idx += 1
        children = tree.get_children()
        if children:
            tree.selection_set(children[0])
            _on_select()
        else:
            selected_row.clear()
            _set_text(prompt_box, "")
            _set_text(gt_box, "")
            media_var.set("No matching items.")
            open_media_btn.configure(state="disabled")

    def _on_select(_e: Any = None) -> None:
        sel = tree.selection()
        if not sel or sel[0] not in filtered_map:
            return
        row = filtered_map[sel[0]]
        selected_row.clear()
        selected_row.update(row)
        _set_text(prompt_box, row["prompt"])
        _set_text(gt_box, row["ground_truth"])
        if row["media_path"] is None:
            media_var.set("Text-only item (no media file)")
            open_media_btn.configure(state="disabled")
        else:
            status = "OK on disk" if row["media_exists"] else "MISSING on disk"
            media_var.set(f"{row['media']} [{status}]")
            open_media_btn.configure(state="normal" if row["media_exists"] else "disabled")

    tree.bind("<<TreeviewSelect>>", _on_select)
    pillar_var.trace_add("write", _load_pillar)
    mod_var.trace_add("write", _apply_filter)
    eval_var.trace_add("write", _apply_filter)
    search_var.trace_add("write", _apply_filter)
    _load_pillar()

    ttk.Button(wrap, text="Close", style="Ghost.TButton", command=dlg.destroy).pack(anchor="e", pady=(gui._px(10), 0))
    dlg.bind("<Escape>", lambda _e: dlg.destroy())
