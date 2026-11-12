"""Unified SynhalEES CLI -- evaluate, inspect, Kaggle sync, and leaderboard site.

One entry point for the whole workflow (pure Python stdlib):

    synhalees run --model ollama:llama3.1:8b --pillars 01_buddhist_culture
    synhalees compare | status | pillars | models | guide
    synhalees publish <slug> | build [--check|--demo] | logos [--check] | check
    synhalees site <publish|build|logos|check>
    synhalees kaggle <gen|push|run|coverage|status|logs|pull|submit|publish>

Install with ``pip install -e .`` (console script ``synhalees``) or run via
``python -m synhalees``. All existing commands and scripts stay compatible.
"""

from __future__ import annotations

import argparse
import csv
import importlib
import json
import shutil
import sys
from pathlib import Path
from typing import Any

from synhalees import __version__
from synhalees.cli_ui import (
    SynhaleesHelpFormatter,
    badge,
    bold,
    code,
    configure_color,
    dim,
    err_line,
    heading,
    info_line,
    ok_line,
    print_guide,
    print_overview,
    print_table,
)
from synhalees.kaggle_ops import (
    EXT_HEADER,
    HEADER,
    KG_RESULTS,
    MODALITIES,
    ROOT,
    RUNS,
    SUBMISSIONS,
    TASKS_DIR,
    _write_submission_row,
    collect_task_results,
    display_path,
    expand_import_tasks,
    gather_coverage,
    import_single_slim,
    kaggle_available_models,
    kaggle_bin,
    kaggle_run_usage,
    kaggle_task_models,
    load_scorecard,
    load_slim_rows,
    normalize_pulled_runs,
    parse_modality,
    pillar_from_task,
    pull_single_to_slim,
    resolve_kaggle_model_names,
    resolve_submission_path,
    resolve_task_file,
    resolve_task_name,
    rewrite_model_args,
    sh,
    task_is_cached,
    task_modality,
    task_name_for,
    task_slug,
)
from synhalees.registry import canonical_model_slug, default_registry

__all__ = [
    "EXT_HEADER",
    "HEADER",
    "KG_RESULTS",
    "MODALITIES",
    "ROOT",
    "RUNS",
    "SUBMISSIONS",
    "TASKS_DIR",
    "build_parser",
    "collect_task_results",
    "expand_import_tasks",
    "import_single_slim",
    "kaggle_available_models",
    "kaggle_bin",
    "kaggle_run_usage",
    "kaggle_task_models",
    "load_scorecard",
    "load_slim_rows",
    "main",
    "normalize_pulled_runs",
    "parse_modality",
    "pillar_from_task",
    "pull_single_to_slim",
    "resolve_kaggle_model_names",
    "resolve_submission_path",
    "resolve_task_file",
    "resolve_task_name",
    "rewrite_model_args",
    "task_modality",
    "task_name_for",
    "task_slug",
]


def _ensure_root_on_path() -> None:
    root_str = str(ROOT)
    if root_str not in sys.path:
        sys.path.insert(0, root_str)


def _require_repo_file(rel_path: str) -> Path:
    path = ROOT / rel_path
    if not path.exists():
        raise SystemExit(f"missing {path} -- run from the repo checkout (pip install -e .)")
    return path


def _load_repo_module(rel_path: str, module_name: str):
    _require_repo_file(rel_path)
    _ensure_root_on_path()
    return importlib.import_module(module_name)


def _load_pillar_titles() -> dict[str, dict[str, str]]:
    """Load EN/SI/icon metadata from docs/assets/data/pillars.json if available."""
    pillars_json = ROOT / "docs" / "assets" / "data" / "pillars.json"
    if not pillars_json.is_file():
        return {}
    try:
        rows = json.loads(pillars_json.read_text(encoding="utf-8"))
        return {r["slug"]: r for r in rows if isinstance(r, dict) and "slug" in r}
    except (OSError, ValueError):
        return {}


def _scan_submissions() -> dict[str, dict[str, Any]]:
    """Read committed submissions/**/*.csv -> {canon_slug: {modalities, pillars, date, path}}."""
    out: dict[str, dict[str, Any]] = {}
    if not SUBMISSIONS.is_dir():
        return out
    for csv_path in sorted(SUBMISSIONS.rglob("*.csv")):
        try:
            with open(csv_path, newline="", encoding="utf-8-sig") as fh:
                rows = list(csv.reader(fh))
        except OSError:
            continue
        if not rows or rows[0][:len(HEADER)] != HEADER:
            continue
        for row in rows[1:]:
            if len(row) < 6:
                continue
            slug = canonical_model_slug(row[0])
            entry = out.setdefault(slug, {
                "modalities": set(),
                "pillars": set(),
                "date": "",
                "path": csv_path,
            })
            entry["modalities"].add(row[4].strip().lower())
            entry["pillars"].add(row[3].strip())
            if row[2].strip() > entry["date"]:
                entry["date"] = row[2].strip()
    return out


def _rebuild_and_check() -> int:
    mod = _load_repo_module("tools/build_leaderboard.py", "tools.build_leaderboard")
    rc = mod.run_build(argparse.Namespace(csv=[], demo=False, check=False))
    if rc:
        return rc
    return mod.run_build(argparse.Namespace(csv=[], demo=False, check=True))


# ---------------------------------------------------------------------------
# Evaluate & Compare
# ---------------------------------------------------------------------------

def cmd_run(a: argparse.Namespace) -> int:
    mod = _load_repo_module("run_benchmark.py", "run_benchmark")
    return mod.run_from_args(a)


def cmd_compare(a: argparse.Namespace) -> int:
    mod = _load_repo_module("tools/compare_runs.py", "tools.compare_runs")
    return mod.run_compare(a)


# ---------------------------------------------------------------------------
# Inspect & Discover (status, pillars, models, guide)
# ---------------------------------------------------------------------------

def _validate_datasets() -> tuple[bool, int, dict[str, int]]:
    """Validate CSV schemas & eval_types across all pillars (strict=False)."""
    from synhalees import data as syn_data

    counts = {"text": 0, "vision": 0, "audio": 0}
    pillars = syn_data.list_pillars()
    for p in pillars:
        counts["text"] += len(syn_data.load_text(p))
        try:
            counts["vision"] += len(syn_data.load_vision(p, strict=False))
        except FileNotFoundError:
            pass
        try:
            counts["audio"] += len(syn_data.load_audio(p, strict=False))
        except FileNotFoundError:
            pass
    return True, len(pillars), counts


def _site_sync_status() -> tuple[bool | None, bool | None]:
    """Read-only sync check for logo-data.js and leaderboard data. None if tools missing."""
    import io
    from contextlib import redirect_stderr, redirect_stdout

    logo_ok: bool | None = None
    board_ok: bool | None = None
    if (ROOT / "tools" / "build_logo_data.py").is_file():
        mod = _load_repo_module("tools/build_logo_data.py", "tools.build_logo_data")
        buf = io.StringIO()
        with redirect_stdout(buf), redirect_stderr(buf):
            logo_ok = mod.run_logos(argparse.Namespace(check=True, stdout=False)) == 0
    if (ROOT / "tools" / "build_leaderboard.py").is_file():
        mod = _load_repo_module("tools/build_leaderboard.py", "tools.build_leaderboard")
        buf = io.StringIO()
        with redirect_stdout(buf), redirect_stderr(buf):
            board_ok = mod.run_build(argparse.Namespace(csv=[], demo=False, check=True)) == 0
    return logo_ok, board_ok


def cmd_status(a: argparse.Namespace) -> int:
    from synhalees import data as syn_data

    heading("SynhalEES Project Status", "dataset · submissions · local runs · Kaggle · site sync")

    # 1. Dataset summary
    pillars = syn_data.list_pillars()
    t_rows = v_rows = v_files = a_rows = a_files = 0
    for p in pillars:
        t_rows += len(syn_data.load_text(p))
        try:
            vi = syn_data.load_vision(p, strict=False)
            v_rows += len(vi)
            v_files += sum(1 for x in vi if x.image_path.is_file())
        except FileNotFoundError:
            pass
        try:
            au = syn_data.load_audio(p, strict=False)
            a_rows += len(au)
            a_files += sum(1 for x in au if x.audio_path.is_file())
        except FileNotFoundError:
            pass
    print(
        f"  {bold('Dataset')}      {len(pillars)} pillars  |  "
        f"Text: {t_rows}  |  Vision: {v_rows} ({v_files} files)  |  Audio: {a_rows} ({a_files} files)"
    )

    # 2. Submissions summary
    subs = _scan_submissions()
    vendors = {default_registry.resolve_taxonomy(m)[0] for m in subs}
    latest = max((s["date"] for s in subs.values() if s["date"]), default="-")
    n_t = sum(1 for s in subs.values() if "text" in s["modalities"])
    n_v = sum(1 for s in subs.values() if "vision" in s["modalities"])
    n_a = sum(1 for s in subs.values() if "audio" in s["modalities"])
    print(
        f"  {bold('Submissions')}  {len(subs)} models across {len(vendors)} vendors  |  "
        f"Text: {n_t}  Vision: {n_v}  Audio: {n_a}  |  Latest: {latest}"
    )

    # 3. Site sync check
    logo_ok, board_ok = _site_sync_status()
    sync_parts = []
    if logo_ok is not None:
        sync_parts.append(f"logo-data.js {badge('OK' if logo_ok else 'STALE')}")
    if board_ok is not None:
        sync_parts.append(f"leaderboard data {badge('OK' if board_ok else 'STALE')}")
    if sync_parts:
        print(f"  {bold('Site Sync')}    " + "   ".join(sync_parts))
        if logo_ok is False or board_ok is False:
            print(f"               {dim('Run:')} {code('synhalees site build')} {dim('or')} {code('synhalees check')}")

    # 4. Local runs
    runs_dir = Path(getattr(a, "runs_dir", RUNS))
    local_rows: list[list[str]] = []
    if runs_dir.is_dir():
        for child in sorted(runs_dir.iterdir()):
            if not child.is_dir():
                continue
            ckpt = child / "checkpoint.jsonl"
            sub_csv = child / "submission.csv"
            meta_p = child / "meta.json"
            if not ckpt.is_file() and not sub_csv.is_file():
                continue
            meta: dict[str, Any] = {}
            if meta_p.is_file():
                try:
                    meta = json.loads(meta_p.read_text(encoding="utf-8"))
                except ValueError:
                    meta = {}
            items = str(meta.get("items", "-"))
            errs = str(meta.get("errors", "-"))
            overall = f"{meta['overall']}%" if "overall" in meta else "-"
            ready = badge("OK", "CSV") if sub_csv.is_file() else badge("WARN", "PARTIAL")
            local_rows.append([child.name, items, errs, overall, ready])

    print()
    print(f"  {bold('Local Runs')}   ({runs_dir})")
    if local_rows:
        print_table(
            ["Folder Slug", "Items", "Errors", "Overall", "Scorecard"],
            local_rows,
            right_align=(1, 2, 3),
            indent=2,
        )
    else:
        print(f"  {dim('No local runs yet. Start one with:')} {code('synhalees run --model ollama:llama3.1:8b')}")

    # 5. Kaggle slim archive coverage
    filt = getattr(a, "filter", None)
    show_missing = getattr(a, "missing", False)
    check_remote = getattr(a, "remote", False)
    try:
        model_tasks, missing_remote, tasks = gather_coverage(
            check_remote=check_remote,
            provider_filter=filt,
        )
    except SystemExit:
        model_tasks, missing_remote, tasks = {}, {}, []

    print()
    total_m = len(model_tasks)
    full_t = sum(1 for st in model_tasks.values() if st["text"] >= 15)
    full_v = sum(1 for st in model_tasks.values() if st["vision"] > 0)
    full_a = sum(1 for st in model_tasks.values() if st["audio"] > 0)
    print(
        f"  {bold('Kaggle Slim')}  {total_m} models in kaggle/results/  |  "
        f"15/15 Text: {full_t}  Vision: {full_v}  Audio: {full_a}"
    )

    incomplete = {
        m: st for m, st in model_tasks.items()
        if st["text"] < 15 or not st["vision"] or not st["audio"]
    }
    rows_to_show = incomplete if show_missing else (model_tasks if filt else incomplete)
    if rows_to_show:
        table_rows = [
            [
                m,
                f"{st['text']}/15",
                badge("OK", "DONE") if st["vision"] else dim("-"),
                badge("OK", "DONE") if st["audio"] else dim("-"),
                badge("OK", "YES") if m in subs else badge("WARN", "UNSUBMITTED"),
            ]
            for m, st in sorted(rows_to_show.items())
        ]
        print()
        print_table(["Model", "Text", "Vision", "Audio", "Published"], table_rows, right_align=(1,), indent=2)
    elif total_m:
        print(f"  {dim('All')} {total_m} {dim('Kaggle models have full 15/15 text + vision + audio coverage.')}")
        print(f"  {dim('Inspect details with:')} {code('synhalees kaggle coverage')} {dim('or')} {code('synhalees models')}")

    if check_remote and missing_remote:
        print()
        _print_missing_remote(missing_remote, len(tasks))
    elif check_remote and not missing_remote:
        print(f"  {badge('OK')} No unpulled completed runs found on Kaggle server.")
    print()
    return 0


def cmd_pillars(a: argparse.Namespace) -> int:
    from synhalees import data as syn_data

    titles = _load_pillar_titles()
    all_pillars = syn_data.list_pillars()
    query = (getattr(a, "query", None) or "").strip().lower()
    matched = [
        p for p in all_pillars
        if not query
        or query in p.lower()
        or query in (titles.get(p, {}).get("title_en") or "").lower()
    ]
    if not matched:
        err_line(f"No pillar matched {query!r}. Known pillars: {', '.join(all_pillars)}")
        return 1

    if query and len(matched) == 1:
        p = matched[0]
        meta = titles.get(p, {})
        title_en = meta.get("title_en") or p.partition("_")[2].replace("_", " ").title()
        title_si = meta.get("title_si") or ""
        t_items = syn_data.load_text(p)
        try:
            v_items = syn_data.load_vision(p, strict=False)
        except FileNotFoundError:
            v_items = []
        try:
            a_items = syn_data.load_audio(p, strict=False)
        except FileNotFoundError:
            a_items = []
        v_files = sum(1 for x in v_items if x.image_path.is_file())
        a_files = sum(1 for x in a_items if x.audio_path.is_file())

        heading(f"Pillar {p}", f"{title_en}" + (f" ({title_si})" if title_si else ""))
        print(f"  Path   : benchmark_data/{p}/")
        print(f"  Text   : {len(t_items)} row(s)")
        print(f"  Vision : {len(v_items)} row(s)  ({v_files}/{len(v_items)} image files on disk)")
        print(f"  Audio  : {len(a_items)} row(s)  ({a_files}/{len(a_items)} audio files on disk)")

        def _ev_summary(items: list[Any]) -> str:
            counts: dict[str, int] = {}
            for it in items:
                counts[it.eval_type] = counts.get(it.eval_type, 0) + 1
            return ", ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "-"

        print()
        print_table(
            ["Modality", "Rows", "Media Ready", "Eval Types", "Sample IDs"],
            [
                ["text", len(t_items), f"{len(t_items)}/{len(t_items)}", _ev_summary(t_items), ", ".join(x.id for x in t_items[:3])],
                ["vision", len(v_items), f"{v_files}/{len(v_items)}", _ev_summary(v_items), ", ".join(x.id for x in v_items[:3])],
                ["audio", len(a_items), f"{a_files}/{len(a_items)}", _ev_summary(a_items), ", ".join(x.id for x in a_items[:3])],
            ],
            right_align=(1, 2),
            indent=2,
        )
        print()
        return 0

    show_eval = bool(getattr(a, "eval_types", False))
    heading(f"SynhalEES Benchmark Pillars ({len(matched)}/{len(all_pillars)})")
    rows: list[list[Any]] = []
    tot_t = tot_v = tot_vf = tot_a = tot_af = 0
    for p in matched:
        meta = titles.get(p, {})
        title_en = meta.get("title_en") or p.partition("_")[2].replace("_", " ").title()
        t_items = syn_data.load_text(p)
        try:
            v_items = syn_data.load_vision(p, strict=False)
        except FileNotFoundError:
            v_items = []
        try:
            a_items = syn_data.load_audio(p, strict=False)
        except FileNotFoundError:
            a_items = []
        vf = sum(1 for x in v_items if x.image_path.is_file())
        af = sum(1 for x in a_items if x.audio_path.is_file())
        tot_t += len(t_items)
        tot_v += len(v_items)
        tot_vf += vf
        tot_a += len(a_items)
        tot_af += af

        row: list[Any] = [p, title_en, len(t_items), f"{vf}/{len(v_items)}", f"{af}/{len(a_items)}"]
        if show_eval:
            ev_set = sorted({x.eval_type for x in (*t_items, *v_items, *a_items)})
            row.append(", ".join(ev_set))
        rows.append(row)

    headers = ["Slug", "Title", "Text", "Vision", "Audio"]
    if show_eval:
        headers.append("Eval Types")
    rows.append(
        [bold("TOTAL"), bold(f"{len(matched)} pillar(s)"), bold(str(tot_t)), bold(f"{tot_vf}/{tot_v}"), bold(f"{tot_af}/{tot_a}")]
        + ([""] if show_eval else [])
    )
    print_table(headers, rows, right_align=(2, 3, 4), indent=2)
    print(f"\n  {dim('Tip: inspect one pillar in detail with')} {code('synhalees pillars 05')}\n")
    return 0


def cmd_models(a: argparse.Namespace) -> int:
    subs = _scan_submissions()
    try:
        kg_cov, _, _ = gather_coverage(check_remote=False)
    except SystemExit:
        kg_cov = {}

    all_slugs = sorted(
        set(default_registry.display_names.keys()) | set(subs.keys()) | set(kg_cov.keys())
    )
    query = (getattr(a, "query", None) or "").strip().lower()
    prov_filter = (getattr(a, "provider", None) or "").strip().lower()
    oss_only = bool(getattr(a, "open_source", False))
    pub_only = bool(getattr(a, "published", False))
    unpub_only = bool(getattr(a, "unpublished", False))

    if pub_only and unpub_only:
        raise SystemExit("--published and --unpublished cannot be combined")

    resolved_prov = default_registry.resolve_provider(prov_filter) if prov_filter else None

    rows: list[list[str]] = []
    for slug in all_slugs:
        vendor_slug, fam_slug, is_oss = default_registry.resolve_taxonomy(slug)
        prov_obj = default_registry.providers.get(vendor_slug)
        vendor_name = prov_obj.name if prov_obj else vendor_slug.title()
        fam_obj = prov_obj.families.get(fam_slug) if prov_obj else None
        fam_name = fam_obj.name if fam_obj else fam_slug
        disp = default_registry.get_model_display_name(slug)

        if prov_filter:
            target_slug = resolved_prov.slug if resolved_prov else prov_filter
            if (
                vendor_slug != target_slug
                and prov_filter not in vendor_name.lower()
                and prov_filter not in slug.lower()
            ):
                continue
        if oss_only and not is_oss:
            continue
        is_pub = slug in subs
        if pub_only and not is_pub:
            continue
        if unpub_only and is_pub:
            continue
        if query and not any(query in s.lower() for s in (slug, disp, vendor_name, fam_name)):
            continue

        mod_order = ("text", "vision", "audio")
        mods_sorted = sorted(
            subs[slug]["modalities"],
            key=lambda m: (mod_order.index(m) if m in mod_order else 99, m),
        )
        pub_str = ",".join(m[0].upper() for m in mods_sorted) if is_pub else "-"
        kg_info = kg_cov.get(slug)
        if kg_info:
            kg_str = (
                f"{kg_info['text']}T"
                + (" V" if kg_info["vision"] else "")
                + (" A" if kg_info["audio"] else "")
            )
        else:
            kg_str = "-"
        lic_str = badge("OK", "OSS") if is_oss else dim("Closed")
        rows.append([slug, disp, f"{vendor_name} / {fam_name}", lic_str, pub_str, kg_str])

    heading(f"SynhalEES Models ({len(rows)} shown)", "registry: synhalees/models.json")
    if rows:
        print_table(
            ["Model Slug", "Display Name", "Vendor / Family", "License", "Published", "Kaggle Slim"],
            rows,
            indent=2,
        )
    else:
        print(f"  {dim('No models matched your filters.')}")

    prov_names = ", ".join(p.name for p in default_registry.providers.values())
    print(f"\n  {bold('Providers:')} {dim(prov_names)}")
    print(
        f"  {dim('Filter examples:')} {code('synhalees models --provider google')}  "
        f"{dim('|')}  {code('synhalees models --unpublished')}\n"
    )
    return 0


def cmd_guide(a: argparse.Namespace) -> int:
    print_guide(getattr(a, "topic", "all"))
    return 0


# ---------------------------------------------------------------------------
# Leaderboard & Static Site (`publish`, `build`, `logos`, `check`, `site *`)
# ---------------------------------------------------------------------------

def cmd_publish(a: argparse.Namespace) -> int:
    src = RUNS / a.slug / "submission.csv"
    dst = resolve_submission_path(a.slug)
    if not src.is_file():
        available = (
            sorted(d.name for d in RUNS.iterdir() if d.is_dir() and (d / "submission.csv").is_file())
            if RUNS.is_dir()
            else []
        )
        hint = (
            f"Available local runs with submission.csv: {', '.join(available)}"
            if available
            else "No local runs found under runs/. Run `synhalees run --model ...` first, or use `synhalees kaggle submit` for Kaggle results."
        )
        raise SystemExit(f"missing {src}\n  [hint] {hint}")

    rows = load_scorecard(src)
    if len(rows) < 2:
        raise SystemExit(f"{src}: no data rows")
    if a.dry_run:
        info_line(f"would copy {display_path(src)} -> {display_path(dst)} ({len(rows) - 1} rows)")
        for r in rows[1:]:
            print("  " + ",".join(r))
        return 0
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)
    ok_line(f"copied {display_path(src)} -> {display_path(dst)}")
    if getattr(a, "no_build", False):
        print(f"  {dim('hint:')} run {code('synhalees build')} when ready to regenerate docs/assets/data/*")
        return 0
    code_rc = _rebuild_and_check()
    if not code_rc:
        ok_line(f"published {a.slug}: commit {display_path(dst)} with docs/assets/data/*")
    return code_rc


def cmd_build(a: argparse.Namespace) -> int:
    mod = _load_repo_module("tools/build_leaderboard.py", "tools.build_leaderboard")
    if getattr(a, "demo", False) and getattr(a, "check", False):
        raise SystemExit("--demo and --check cannot be combined")
    return mod.run_build(a)


def cmd_logos(a: argparse.Namespace) -> int:
    mod = _load_repo_module("tools/build_logo_data.py", "tools.build_logo_data")
    return mod.run_logos(a)


def cmd_check(a: argparse.Namespace) -> int:
    heading("SynhalEES Verification Gates")
    failed: list[str] = []

    try:
        logos_mod = _load_repo_module("tools/build_logo_data.py", "tools.build_logo_data")
        rc1 = logos_mod.run_logos(argparse.Namespace(check=True, stdout=False))
    except SystemExit as exc:
        print(str(exc), file=sys.stderr)
        rc1 = 1
    if rc1:
        err_line("logo-data.js fresh")
        failed.append("logo-data.js fresh")
    else:
        ok_line("logo-data.js fresh")

    try:
        board_mod = _load_repo_module("tools/build_leaderboard.py", "tools.build_leaderboard")
        rc2 = board_mod.run_build(argparse.Namespace(csv=[], demo=False, check=True))
    except SystemExit as exc:
        print(str(exc), file=sys.stderr)
        rc2 = 1
    if rc2:
        err_line("docs/assets/data in sync")
        failed.append("docs/assets/data in sync")
    else:
        ok_line("docs/assets/data in sync")

    if not getattr(a, "no_data", False):
        try:
            _ok, n_pil, counts = _validate_datasets()
            ok_line(
                f"dataset schemas valid ({n_pil} pillars: "
                f"{counts['text']} text, {counts['vision']} vision, {counts['audio']} audio)"
            )
        except Exception as exc:
            err_line(f"dataset schemas valid ({exc})")
            failed.append("dataset schemas valid")

    rc4 = sh(
        [
            sys.executable, "-m", "compileall", "-q",
            str(ROOT / "synhalees"), str(ROOT / "tools"),
            str(ROOT / "kaggle"), str(ROOT / "run_benchmark.py"),
        ],
        echo=False,
    )
    if rc4:
        err_line("python compiles")
        failed.append("python compiles")
    else:
        ok_line("python compiles")

    print()
    if failed:
        err_line("FAILED: " + ", ".join(failed))
        return 1
    ok_line("All verification gates passed!")
    return 0


# ---------------------------------------------------------------------------
# Kaggle Benchmarks (`synhalees kaggle ...`)
# ---------------------------------------------------------------------------

def cmd_kaggle_gen(a: argparse.Namespace) -> int:
    mod = _load_repo_module("kaggle/generate_tasks.py", "kaggle.generate_tasks")
    out_dir = Path(a.out) if getattr(a, "out", None) else mod.OUT_DIR
    mod.generate(out_dir, dry_run=bool(getattr(a, "dry_run", False)))
    return 0


def cmd_kaggle_push(a: argparse.Namespace, extra: list[str] | None = None) -> int:
    files = sorted(TASKS_DIR.glob("*.py")) if a.task in (None, "all") else [resolve_task_file(a.task)]
    if not files:
        raise SystemExit(f"no task files in {TASKS_DIR} (run: synhalees kaggle gen)")
    fwd = list(extra or [])
    if a.wait:
        fwd.append("--wait")
    for i, f in enumerate(files, 1):
        name = task_name_for(f)
        print(f"[{i}/{len(files)}] {name}  <-  {display_path(f)}")
        rc = sh([kaggle_bin(), "b", "t", "push", name, "-f", str(f), *fwd])
        if rc:
            err_line(f"stopped: push failed for {name} ({len(files) - i} task(s) left)")
            return rc
    ok_line(f"pushed {len(files)} task file(s)")
    return 0


def _model_flags(models: list[str] | None) -> list[str]:
    out: list[str] = []
    for m in models or []:
        out.extend(["-m", m])
    return out


def cmd_kaggle_run(a: argparse.Namespace, extra: list[str] | None = None) -> int:
    fwd = _model_flags(getattr(a, "model", None)) + list(extra or [])
    return sh([kaggle_bin(), "b", "t", "run", resolve_task_name(a.task), *fwd])


def _print_missing_remote(missing: dict[str, list[str]], total_tasks: int) -> None:
    print(f"  {bold(f'Completed on Kaggle, missing locally ({len(missing)} model(s)):')}")
    for s in sorted(missing):
        ts = missing[s]
        shown = ", ".join(x.replace("synhalees-", "") for x in ts[:3])
        more = f", +{len(ts) - 3} more" if len(ts) > 3 else ""
        print(f"    {s:<30} {len(ts):>2}/{total_tasks} task(s)  ({shown}{more})")
    print(f"  {dim('hint:')} {code('synhalees kaggle pull all')}  (downloads only these missing models)")


def cmd_kaggle_coverage(a: argparse.Namespace) -> int:
    filt = getattr(a, "provider", None) or getattr(a, "filter", None)
    check_remote = not getattr(a, "no_remote", False)
    only_missing = bool(getattr(a, "missing", False))

    model_tasks, missing, tasks = gather_coverage(
        check_remote=check_remote,
        provider_filter=filt,
    )
    if not model_tasks and not missing:
        print("no benchmark results found in kaggle/results/*.slim.csv")
        return 0

    if only_missing:
        display = {
            m: st for m, st in model_tasks.items()
            if st["text"] < 15 or not st["vision"] or not st["audio"]
        }
    else:
        display = model_tasks

    heading(
        f"Kaggle Benchmark Coverage ({len(display)} model(s) in kaggle/results/)",
        f"filter={filt}" if filt else "",
    )
    if display:
        rows = [
            [
                m,
                f"{st['text']:>2}/15",
                badge("OK", "DONE") if st["vision"] else dim("-"),
                badge("OK", "DONE") if st["audio"] else dim("-"),
            ]
            for m, st in sorted(display.items())
        ]
        print_table(["Model", "Text (of 15)", "Vision", "Audio"], rows, right_align=(1,), indent=2)
    else:
        ok_line("All matching models have full 15/15 text + vision + audio coverage.")

    if missing:
        print()
        _print_missing_remote(missing, len(tasks))
    print()
    return 0


def cmd_kaggle_status(a: argparse.Namespace, extra: list[str] | None = None) -> int:
    task = getattr(a, "task", None)
    if task in (None, "all", "coverage", "local"):
        if extra:
            raise SystemExit(f"unrecognized arguments: {' '.join(extra)}")
        return cmd_kaggle_coverage(a)
    fwd = _model_flags(getattr(a, "model", None)) + list(extra or [])
    return sh([kaggle_bin(), "b", "t", "status", resolve_task_name(task), *fwd])


def cmd_kaggle_logs(a: argparse.Namespace, extra: list[str] | None = None) -> int:
    fwd = _model_flags(getattr(a, "model", None)) + list(extra or [])
    return sh([kaggle_bin(), "b", "t", "log", resolve_task_name(a.task), *fwd])


def cmd_kaggle_publish(a: argparse.Namespace, extra: list[str] | None = None) -> int:
    return sh([kaggle_bin(), "b", "t", "publish", resolve_task_name(a.task), *(extra or [])])


def cmd_kaggle_pull(a: argparse.Namespace, extra: list[str] | None = None) -> int:
    task = getattr(a, "task", None)
    cli_mod = getattr(a, "modality", None)
    modality = parse_modality(cli_mod) if cli_mod else ("text",)
    force = bool(getattr(a, "force", False))
    dry_run = bool(getattr(a, "dry_run", False))
    fwd_extra = list(extra or [])

    if "-o" in fwd_extra or "--output" in fwd_extra:
        raise SystemExit("drop -o: artifacts always go to kaggle/results/ (repo hygiene)")

    models_req = list(getattr(a, "model", None) or [])
    requested_models = {canonical_model_slug(m) for m in models_req}
    base_model_flags = _model_flags(models_req) + fwd_extra

    if task in (None, "all"):
        files = sorted(TASKS_DIR.glob("*.py"))
        if not files:
            raise SystemExit(f"no task files in {TASKS_DIR} (run: synhalees kaggle gen)")
        sel = {"text", "vision", "audio"} if "all" in modality else set(modality)
        picked = [f for f in files if task_modality(task_name_for(f)) in sel]
        if not picked:
            raise SystemExit(
                f"no '{'|'.join(modality)}' task files in {TASKS_DIR} (run: synhalees kaggle gen)"
            )
        failed: list[str] = []
        skipped = 0
        for i, f in enumerate(picked, 1):
            name = task_name_for(f)
            cached, n_models, missing = task_is_cached(
                name, requested_models=requested_models, check_remote=not dry_run
            )
            if not force and cached:
                slim_f = KG_RESULTS / f"{name}.slim.csv"
                print(
                    f"[{i}/{len(picked)}] skipping {name} "
                    f"(all {n_models} model(s) already in {slim_f.name}; --force to re-download)"
                )
                skipped += 1
                continue

            task_args = list(base_model_flags)
            if missing and not force and not requested_models:
                preview = ", ".join(missing[:3]) + ("..." if len(missing) > 3 else "")
                msg = f"({len(missing)} missing model(s): {preview}) -> slim CSV"
                for m in missing:
                    task_args.extend(["-m", m])
            else:
                msg = "-> slim CSV"

            if dry_run:
                print(f"[{i}/{len(picked)}] [dry-run] would download {name} {msg}")
                continue
            print(f"[{i}/{len(picked)}] downloading {name} {msg}")
            rc = pull_single_to_slim(name, extra_args=task_args)
            if rc:
                failed.append(name)

        pulled = len(picked) - skipped - len(failed)
        parts: list[str] = []
        if pulled:
            verb = "would download" if dry_run else "downloaded"
            parts.append(f"{verb} {pulled}")
        if skipped:
            parts.append(f"skipped {skipped} (cached)")
        if failed:
            parts.append(f"failed {len(failed)}")
            err_line(f"download failed for {len(failed)} task(s): {', '.join(failed)}")
        print(
            f"{', '.join(parts)} [{'|'.join(modality)}] task(s) -> "
            f"{display_path(KG_RESULTS)}/*.slim.csv"
        )
        if skipped and not force:
            print(f"  {dim('hint:')} add {code('--force')} to re-download cached tasks")
        if sel == {"text"}:
            mod_flag = '--modality "vision|audio"'
            print(
                f"  {dim('note:')} vision+audio skipped by default (~730MB more) - "
                f"add {code(mod_flag)} (or vision / audio / all)"
            )
        return 1 if failed else 0

    name = resolve_task_name(task)
    cached, n_models, missing = task_is_cached(
        name, requested_models=requested_models, check_remote=not dry_run
    )
    if not force and cached:
        slim_f = KG_RESULTS / f"{name}.slim.csv"
        print(
            f"skipping {name} (all {n_models} model(s) already in {slim_f.name}; "
            "--force to re-download)"
        )
        return 0
    task_args = list(base_model_flags)
    if missing and not force and not requested_models:
        msg = f"({len(missing)} missing model(s): {', '.join(missing)}) -> slim CSV"
        for m in missing:
            task_args.extend(["-m", m])
    else:
        msg = "-> slim CSV"
    if dry_run:
        info_line(f"[dry-run] would download {name} {msg}")
        return 0
    print(f"downloading {name} {msg}")
    return pull_single_to_slim(name, extra_args=task_args)


def cmd_kaggle_import(a: argparse.Namespace) -> int:
    """Import slim CSVs, raw task folders, or a .csv file -> submissions/*.csv."""
    key = str(a.task)
    dry_run = bool(getattr(a, "dry_run", False))
    slug_override = getattr(a, "slug", None)

    if key.endswith(".csv") or Path(key).suffix == ".csv":
        src = Path(key)
        if not src.is_file() and not src.is_absolute():
            candidate = KG_RESULTS / key
            if candidate.is_file():
                src = candidate
        count = import_single_slim(src, dry_run=dry_run, slug_override=slug_override)
        verb = "would import" if dry_run else "imported"
        ok_line(f"{verb} {count} row(s) from {src.name}")
        if dry_run:
            return 0
        if getattr(a, "build", False):
            code_rc = _rebuild_and_check()
            if not code_rc:
                ok_line("import done: commit submissions/*.csv with docs/assets/data/*")
            return code_rc
        print(f"  {dim('hint:')} run {code('synhalees build')} to regenerate docs/assets/data/*")
        return 0

    key_low = key.lower()
    if key_low in ("all", "text", "vision", "audio"):
        if slug_override:
            raise SystemExit("--slug cannot be used with submit all/text/vision/audio")
        names = expand_import_tasks(key_low)
        failed: list[str] = []
        skipped: list[str] = []
        done = 0
        for i, slug in enumerate(names, 1):
            slim_f = KG_RESULTS / f"{slug}.slim.csv"
            t_dir = KG_RESULTS / slug
            has_slim = slim_f.is_file() and slim_f.stat().st_size > 50
            has_raw = t_dir.is_dir() and (
                any(t_dir.rglob("*.result.json")) or any(t_dir.rglob("*.run.json"))
            )
            if not has_slim and not has_raw:
                print(
                    f"[{i}/{len(names)}] skipping {slug} (no local data; "
                    f"download first: synhalees kaggle pull {slug})"
                )
                skipped.append(slug)
                continue
            if has_slim and not has_raw:
                print(f"[{i}/{len(names)}] importing {slug} (from {slim_f.name})")
                imported = import_single_slim(slim_f, dry_run=dry_run)
                if imported:
                    done += 1
                continue
            print(f"[{i}/{len(names)}] importing {slug}")
            rc = cmd_kaggle_import(argparse.Namespace(
                task=slug, slug=None, dry_run=dry_run,
                no_pull=True, build=False, _in_batch=True,
            ))
            if rc:
                failed.append(slug)
            else:
                done += 1

        if failed:
            err_line(f"import failed for {len(failed)} task(s): {', '.join(failed)}")
            return 1
        verb = "would import" if dry_run else "imported"
        print(
            f"{verb} {done} Kaggle task(s)"
            + (f"; skipped {len(skipped)} with no local data: {', '.join(skipped)}" if skipped else "")
        )
        if dry_run:
            return 0
        if getattr(a, "build", False):
            return _rebuild_and_check()
        print(f"  {dim('hint:')} run {code('synhalees build')} to regenerate docs/assets/data/*")
        return 0

    slug = resolve_task_name(a.task)
    pillar = pillar_from_task(slug)
    if slug in ("synhalees-vision", "synhalees-audio"):
        pillar = "__overall__"
    elif pillar is None:
        raise SystemExit(f"{slug} is not an importable text pillar or combined vision/audio task")

    slim_f = KG_RESULTS / f"{slug}.slim.csv"
    task_dir = KG_RESULTS / slug
    newest = collect_task_results(task_dir) if task_dir.is_dir() else {}

    if not newest and slim_f.is_file() and slim_f.stat().st_size > 50:
        imported = import_single_slim(slim_f, dry_run=dry_run, slug_override=slug_override)
        verb = "would import" if dry_run else "imported"
        ok_line(f"{verb} {imported} row(s) from {slim_f.name}")
        if dry_run or getattr(a, "_in_batch", False):
            return 0
        if getattr(a, "build", False):
            return _rebuild_and_check()
        print(f"  {dim('hint:')} run {code('synhalees build')} to regenerate docs/assets/data/*")
        return 0

    if not newest and not getattr(a, "no_pull", False):
        print(f"no local artifacts under {task_dir}; pulling from Kaggle...")
        rc = sh([kaggle_bin(), "b", "t", "download", slug, "-o", str(KG_RESULTS)])
        if rc:
            return rc
        normalize_pulled_runs(slug)
        newest = collect_task_results(task_dir)
    if not newest:
        raise SystemExit(f"no artifacts under {task_dir} (run: synhalees kaggle pull {slug})")
    if slug_override and len(newest) > 1:
        raise SystemExit(f"--slug needs exactly one model; found {', '.join(sorted(newest))}")

    planned: list[list[str]] = []
    modality = task_modality(slug)
    for name in sorted(newest):
        fin, pct, result_path = newest[name]
        model = slug_override or name
        date = fin[:10]
        cost, tokens, latency = kaggle_run_usage(result_path)
        row = [
            model, "kaggle", date, pillar, modality,
            f"{round(pct * 100, 1):.1f}", cost, tokens, latency,
        ]
        planned.append(row)
        usage = f"  ${cost}  {tokens} tok  {latency} ms" if tokens else ""
        print(f"  {model:<30} {modality} overall {row[5]:>6} ({date}){usage}")

    if dry_run:
        print("dry run: nothing written")
        return 0

    for row in planned:
        path, total_rows = _write_submission_row(row, dry_run=False)
        print(f"  wrote {display_path(path)} ({total_rows} rows)")
    if getattr(a, "_in_batch", False):
        return 0
    if getattr(a, "build", False):
        code_rc = _rebuild_and_check()
        if not code_rc:
            ok_line("import done: commit submissions/*.csv with docs/assets/data/*")
        return code_rc
    print(f"  {dim('hint:')} run {code('synhalees build')} to regenerate docs/assets/data/*")
    return 0


# ---------------------------------------------------------------------------
# Parser Construction
# ---------------------------------------------------------------------------

def _add_site_subcommands(
    target_subparsers: argparse._SubParsersAction,
    common: argparse.ArgumentParser,
) -> dict[str, argparse.ArgumentParser]:
    """Register publish/build/logos/check either under `site` or at top level."""
    parsers: dict[str, argparse.ArgumentParser] = {}

    p = target_subparsers.add_parser(
        "publish",
        parents=[common],
        formatter_class=SynhaleesHelpFormatter,
        help="runs/<slug>/submission.csv -> submissions/, rebuild & check",
        description="Promote a finished local run into submissions/<vendor>/<family>/<slug>.csv and rebuild the site.",
    )
    p.add_argument("slug", help="model slug (= folder name under runs/)")
    p.add_argument("--dry-run", action="store_true", help="show what would be copied without writing")
    p.add_argument("--no-build", action="store_true", help="copy scorecard only, skip rebuilding docs/assets/data/*")
    p.set_defaults(func=cmd_publish)
    parsers["publish"] = p

    p = target_subparsers.add_parser(
        "build",
        parents=[common],
        formatter_class=SynhaleesHelpFormatter,
        help="regenerate docs/assets/data/* + JSON-LD from submissions/",
        description="Rebuild static leaderboard JSON/JS and SEO JSON-LD from committed submissions/.",
    )
    if (ROOT / "tools" / "build_leaderboard.py").is_file():
        _load_repo_module("tools/build_leaderboard.py", "tools.build_leaderboard").add_build_arguments(p)
    else:
        p.add_argument("csv", nargs="*", type=Path)
        p.add_argument("--demo", action="store_true")
        p.add_argument("--check", action="store_true")
    p.set_defaults(func=cmd_build)
    parsers["build"] = p

    p = target_subparsers.add_parser(
        "logos",
        parents=[common],
        formatter_class=SynhaleesHelpFormatter,
        help="regenerate docs/assets/logo-data.js from SVGs",
        description="Embed docs/assets/logos/*.svg and icon.png into docs/assets/logo-data.js.",
    )
    if (ROOT / "tools" / "build_logo_data.py").is_file():
        _load_repo_module("tools/build_logo_data.py", "tools.build_logo_data").add_logo_arguments(p)
    else:
        p.add_argument("--check", action="store_true")
        p.add_argument("--stdout", action="store_true")
    p.set_defaults(func=cmd_logos)
    parsers["logos"] = p

    p = target_subparsers.add_parser(
        "check",
        parents=[common],
        formatter_class=SynhaleesHelpFormatter,
        help="run all local gates (logos, data sync, dataset schemas, syntax)",
        description="Run every CI and dataset validation gate locally before committing.",
    )
    p.add_argument("--no-data", action="store_true", help="skip benchmark_data CSV schema validation")
    p.set_defaults(func=cmd_check)
    parsers["check"] = p

    return parsers


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--no-color",
        action="store_true",
        default=argparse.SUPPRESS,
        help="disable colored terminal output",
    )

    top = argparse.ArgumentParser(
        prog="synhalees",
        parents=[common],
        formatter_class=SynhaleesHelpFormatter,
        description="SynhalEES CLI: evaluate models, manage Kaggle tasks, and maintain the leaderboard.",
        epilog="Run `synhalees guide` for step-by-step workflows or `synhalees <cmd> --help` for flags.",
    )
    top.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = top.add_subparsers(dest="cmd", metavar="<command>")

    # 1. Evaluate & Compare
    p = sub.add_parser(
        "run",
        parents=[common],
        formatter_class=SynhaleesHelpFormatter,
        help="run the benchmark locally (API / Ollama, crash-safe resume)",
        description="Evaluate a chat-LLM API or local Ollama model with crash-safe JSONL checkpointing.",
        epilog=(
            "Examples:\n"
            "  synhalees run --model ollama:llama3.1:8b --pillars 01_buddhist_culture\n"
            "  synhalees run --model gemini:gemini-2.5-flash --judge gemini:gemini-2.5-pro\n"
            "  synhalees run --model openrouter:openai/gpt-4o-mini --modality all"
        ),
    )
    if (ROOT / "run_benchmark.py").is_file():
        _load_repo_module("run_benchmark.py", "run_benchmark").add_run_arguments(p)
    else:
        p.add_argument("--model", required=True)
    p.set_defaults(func=cmd_run)

    p = sub.add_parser(
        "compare",
        parents=[common],
        formatter_class=SynhaleesHelpFormatter,
        help="compare local runs under runs/ -> summary + matrix",
        description="Print per-model summary + pillar x model accuracy matrix and write runs/all_submissions.csv.",
    )
    if (ROOT / "tools" / "compare_runs.py").is_file():
        _load_repo_module("tools/compare_runs.py", "tools.compare_runs").add_compare_arguments(p)
    else:
        p.add_argument("--runs-dir", type=Path, default=RUNS)
        p.add_argument("--include-broken", action="store_true")
        p.add_argument("--out", type=Path, default=None)
    p.set_defaults(func=cmd_compare)

    # 2. Inspect & Discover
    p = sub.add_parser(
        "status",
        parents=[common],
        formatter_class=SynhaleesHelpFormatter,
        help="project dashboard (dataset, submissions, runs, Kaggle, site sync)",
        description="Show dataset counts, published submissions, site sync status, local runs, and Kaggle archive coverage.",
    )
    p.add_argument("--filter", default=None, help="filter Kaggle models by substring (e.g. gemini, claude)")
    p.add_argument("--missing", action="store_true", help="show Kaggle models missing full coverage")
    p.add_argument("--remote", action="store_true", help="also query Kaggle server for unpulled runs")
    p.add_argument("--runs-dir", type=Path, default=RUNS, help="local runs folder (default: runs/)")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser(
        "pillars",
        parents=[common],
        formatter_class=SynhaleesHelpFormatter,
        help="list all 15 pillars (or inspect one pillar in detail)",
        description="Browse the 15 SynhalEES pillars with text/vision/audio item and media counts.",
    )
    p.add_argument("query", nargs="?", default=None, help="optional pillar number, slug, or keyword (e.g. 05, grammar)")
    p.add_argument("--eval-types", action="store_true", help="show eval_type breakdown per pillar")
    p.set_defaults(func=cmd_pillars)

    p = sub.add_parser(
        "models",
        parents=[common],
        formatter_class=SynhaleesHelpFormatter,
        help="list registered providers, model families & published status",
        description="Inspect models from synhalees/models.json, submissions/, and kaggle/results/.",
    )
    p.add_argument("query", nargs="?", default=None, help="optional filter substring")
    p.add_argument("--provider", default=None, help="filter by vendor/provider (e.g. google, openai, anthropic)")
    p.add_argument("--open-source", action="store_true", help="show only open-source/open-weights models")
    p.add_argument("--published", action="store_true", help="show only models present in submissions/")
    p.add_argument("--unpublished", action="store_true", help="show only models not yet in submissions/")
    p.set_defaults(func=cmd_models)

    p = sub.add_parser(
        "guide",
        parents=[common],
        formatter_class=SynhaleesHelpFormatter,
        help="step-by-step workflow cheat-sheets (local/kaggle/colab/site)",
        description="Show copy-pasteable end-to-end recipes for common SynhalEES workflows.",
    )
    p.add_argument(
        "topic",
        nargs="?",
        default="all",
        choices=("all", "local", "kaggle", "colab", "site"),
        help="workflow topic to display (default: all)",
    )
    p.set_defaults(func=cmd_guide)

    # 3. Leaderboard & Site (grouped + top-level shortcuts)
    site_parser = sub.add_parser(
        "site",
        aliases=["leaderboard"],
        parents=[common],
        formatter_class=SynhaleesHelpFormatter,
        help="leaderboard & static site (publish | build | logos | check)",
        description="Manage submissions, leaderboard data, embedded logos, and CI gates for docs/.",
    )
    site_sub = site_parser.add_subparsers(dest="site_cmd", metavar="<action>")
    _add_site_subcommands(site_sub, common)
    site_parser.set_defaults(_group_parser=site_parser)

    _add_site_subcommands(sub, common)

    # 4. Kaggle Benchmarks pipeline
    k = sub.add_parser(
        "kaggle",
        parents=[common],
        formatter_class=SynhaleesHelpFormatter,
        help="Kaggle Benchmarks: gen push run coverage status logs pull submit publish",
        description="Generate, upload, run, monitor, pull, and import Kaggle Benchmarks tasks.",
    )
    ks = k.add_subparsers(dest="kcmd", metavar="<action>")
    k.set_defaults(_group_parser=k)

    p = ks.add_parser(
        "gen",
        aliases=["generate"],
        parents=[common],
        formatter_class=SynhaleesHelpFormatter,
        help="regenerate the 17 task files under kaggle/tasks/",
    )
    p.add_argument("--dry-run", action="store_true", help="print what would be written without writing")
    p.add_argument("--out", default=None, help="output directory (default: kaggle/tasks/)")
    p.set_defaults(func=cmd_kaggle_gen)

    p = ks.add_parser(
        "push",
        parents=[common],
        formatter_class=SynhaleesHelpFormatter,
        help="upload task file(s) to Kaggle (default: all 17)",
    )
    p.add_argument("task", nargs="?", default="all", help="pillar/task slug, .py path, or 'all'")
    p.add_argument("--wait", action="store_true", help="wait for server-side build to finish")
    p.set_defaults(func=cmd_kaggle_push, _allow_extra=True)

    p = ks.add_parser(
        "run",
        parents=[common],
        formatter_class=SynhaleesHelpFormatter,
        help="start a server-side run: synhalees kaggle run <task> -m <model>",
    )
    p.add_argument("task", help="task slug (e.g. synhalees-audio or 05_sinhala_grammar)")
    p.add_argument("-m", "--model", action="append", default=[], help="Kaggle model slug (repeatable)")
    p.set_defaults(func=cmd_kaggle_run, _allow_extra=True)

    p = ks.add_parser(
        "coverage",
        parents=[common],
        formatter_class=SynhaleesHelpFormatter,
        help="local slim-CSV coverage + missing Kaggle server runs",
    )
    p.add_argument("filter", nargs="?", default=None, help="optional model/provider substring filter")
    p.add_argument("--provider", default=None, help="filter by model/provider substring")
    p.add_argument("--no-remote", action="store_true", help="offline mode: skip checking Kaggle server")
    p.add_argument("--missing", action="store_true", help="show only models missing full coverage")
    p.set_defaults(func=cmd_kaggle_coverage)

    p = ks.add_parser(
        "status",
        parents=[common],
        formatter_class=SynhaleesHelpFormatter,
        help="coverage dashboard (default: all) or remote status for one <task>",
    )
    p.add_argument("task", nargs="?", default="all", help="task slug, or 'all' for coverage dashboard")
    p.add_argument("-m", "--model", action="append", default=[], help="filter remote task status by model")
    p.add_argument("--provider", default=None, help="filter coverage dashboard by model/provider")
    p.add_argument("--no-remote", action="store_true", help="coverage mode: skip checking Kaggle server")
    p.add_argument("--missing", action="store_true", help="coverage mode: show only incomplete models")
    p.set_defaults(func=cmd_kaggle_status, _allow_extra=True)

    p = ks.add_parser(
        "logs",
        aliases=["log"],
        parents=[common],
        formatter_class=SynhaleesHelpFormatter,
        help="fetch remote run logs: synhalees kaggle logs <task> -m <model>",
    )
    p.add_argument("task", help="task slug")
    p.add_argument("-m", "--model", action="append", default=[], help="Kaggle model slug")
    p.set_defaults(func=cmd_kaggle_logs, _allow_extra=True)

    p = ks.add_parser(
        "pull",
        aliases=["download"],
        parents=[common],
        formatter_class=SynhaleesHelpFormatter,
        help="download Kaggle artifacts -> .temp/ -> merge into *.slim.csv",
        description="Download Kaggle run artifacts into .temp/, extract scores into *.slim.csv, and auto-clean.",
    )
    p.add_argument("task", nargs="?", default="all", help="task slug or 'all' (default: all)")
    p.add_argument(
        "--modality",
        default=None,
        help="filter for 'all': text (default ~145MB) | vision | audio | all; combine with | or ,",
    )
    p.add_argument("--force", action="store_true", default=False, help="re-download even if cached locally")
    p.add_argument("-m", "--model", action="append", default=[], help="target model slug(s) (auto-expands dated pins)")
    p.add_argument("--dry-run", action="store_true", help="preview cached vs downloadable tasks without network calls")
    p.set_defaults(func=cmd_kaggle_pull, _allow_extra=True)

    p = ks.add_parser(
        "submit",
        aliases=["import"],
        parents=[common],
        formatter_class=SynhaleesHelpFormatter,
        help="import slim CSVs or tasks -> submissions/*.csv for leaderboard",
        description="Import Kaggle *.slim.csv files or raw run folders into submissions/<vendor>/<family>/<model>.csv.",
    )
    p.add_argument("task", nargs="?", default="all", help="task slug, all/text/vision/audio, or a .csv path")
    p.add_argument("--slug", default=None, help="force model slug (single-model import only)")
    p.add_argument("--dry-run", action="store_true", help="preview rows without writing files")
    p.add_argument("--no-pull", dest="no_pull", action="store_true", help="use local artifacts only")
    p.add_argument("--build", action="store_true", help="auto-rebuild docs/assets/data/* after submitting")
    p.set_defaults(func=cmd_kaggle_import)

    p = ks.add_parser(
        "publish",
        parents=[common],
        formatter_class=SynhaleesHelpFormatter,
        help="[Kaggle.com] publish a task's public leaderboard on Kaggle",
        description="Publish a Kaggle Benchmarks task leaderboard on Kaggle.com (use `synhalees publish` for the local site).",
    )
    p.add_argument("task", help="task slug on Kaggle")
    p.set_defaults(func=cmd_kaggle_publish, _allow_extra=True)

    return top


def _handle_help_shortcut(argv: list[str]) -> list[str] | None:
    """Handle bare `synhalees`, `synhalees --help`, and `synhalees help [cmd...]`."""
    clean = [t for t in argv if t != "--no-color"]
    if not clean or clean in (["-h"], ["--help"], ["help"]):
        print_overview(__version__)
        return None
    if clean[0] == "help":
        return [*clean[1:], "--help"]
    return argv


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    raw = list(sys.argv[1:] if argv is None else argv)
    no_color = "--no-color" in raw
    configure_color(no_color=no_color)

    routed = _handle_help_shortcut(raw)
    if routed is None:
        return 0

    parser = build_parser()
    args, extras = parser.parse_known_args(routed)

    if getattr(args, "no_color", False):
        configure_color(no_color=True)

    group_parser = getattr(args, "_group_parser", None)
    func = getattr(args, "func", None)

    if group_parser is not None and func is None:
        if extras:
            group_parser.error(f"unrecognized arguments: {' '.join(extras)}")
        group_parser.print_help()
        return 0

    if func is None:
        if extras:
            parser.error(f"unrecognized arguments: {' '.join(extras)}")
        print_overview(__version__)
        return 0

    allow_extra = bool(getattr(args, "_allow_extra", False))
    if extras and not allow_extra:
        parser.error(f"unrecognized arguments: {' '.join(extras)}")

    if allow_extra:
        return func(args, extras) or 0
    return func(args) or 0


if __name__ == "__main__":
    raise SystemExit(main())
