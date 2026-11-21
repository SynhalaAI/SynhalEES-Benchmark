"""Pure-stdlib desktop studio for the SynhalEES Benchmark (tkinter / ttk).

Organized around guided, step-by-step workflows with sidebar navigation:
    - Overview      : project health snapshot, quick launch cards, 15 pillars & registry
    - Kaggle Sync   : Step 1 Discover -> Step 2 Run -> Step 3 Pull -> Step 4 Submit & Build
    - Local Eval    : Step 1 Configure & Run -> Step 2 Compare -> Step 3 Publish
    - Management    : Model Management & Organization Management (synhalees/models.json)
    - Site & CI     : verification gates, rebuild leaderboard/logos, live preview

Launch via:
    synhalees gui [--tab overview|run|kaggle|management|site]
    python -m synhalees gui
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import re
import shlex
import subprocess
import sys
import threading
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from synhalees import kaggle_ops
from synhalees.data import list_pillars, load_pillar
from synhalees.gui_dialogs import open_pillar_inspector, open_scorecard_inspector
from synhalees.gui_helpers import (
    EndpointProbe,
    ReasoningProbe,
    adopt_and_sync_scorecards,
    collect_local_runs_summary,
    compare_model_scorecards,
    delete_organization_family,
    extract_failed_tasks,
    find_unregistered_slim_models,
    inspect_pillar_dataset,
    install_organization_logo,
    load_model_scorecard_summary,
    load_svg_sources_map,
    parse_eval_item_outcome,
    parse_gui_item_event,
    parse_progress_line,
    probe_endpoint,
    probe_judge,
    probe_reasoning,
    rasterize_svg_to_grid,
    render_monogram_grid,
    save_organization_family,
    sync_js_provider_logos,
)
from synhalees.kaggle_ops import (
    ROOT,
    RUNS,
    expand_import_tasks,
    gather_coverage,
    parse_kaggle_models_output,
    run_kaggle_captured,
)
from synhalees.evaluators import strip_thinking
from synhalees.registry import MODELS_JSON, canonical_model_slug, default_registry


# ---------------------------------------------------------------------------
# Pure-Python helpers (testable without opening a Tk window)
# ---------------------------------------------------------------------------

TAB_KEYS = ("overview", "kaggle", "run", "management", "site")

_MODALITY_FLAG_MAP = {
    "Text only (15 tasks · ~145 MB)": "text",
    "Vision only": "vision",
    "Audio only": "audio",
    "Vision + Audio (~730 MB)": "vision|audio",
    "All 17 tasks (T+V+A)": "all",
}


@dataclass(frozen=True)
class DashboardSnapshot:
    """Read-only summary of dataset, submissions, site sync, and local runs."""

    pillar_count: int
    text_items: int
    vision_items: int
    audio_items: int
    submission_models: int
    submission_vendors: int
    latest_date: str
    logos_ok: bool
    site_data_ok: bool
    kaggle_models: int
    kaggle_full_tva: int
    local_runs: list[str]


def _all_model_slugs() -> list[str]:
    from synhalees.cli import _scan_submissions

    subs = _scan_submissions()
    try:
        kg_cov, _, _ = gather_coverage(check_remote=False)
    except Exception:
        kg_cov = {}
    return sorted(
        set(default_registry.display_names.keys()) | set(subs.keys()) | set(kg_cov.keys())
    )


def collect_dashboard_snapshot() -> DashboardSnapshot:
    """Gather fast local repository metrics for the Overview & Status cards."""
    from synhalees.cli import _scan_submissions, _site_sync_status

    pillars = list_pillars()
    text_n = vision_n = audio_n = 0
    for p in pillars:
        pd = load_pillar(p, strict=False)
        text_n += len(pd.text)
        vision_n += len(pd.vision)
        audio_n += len(pd.audio)

    subs = _scan_submissions()
    vendors = {default_registry.resolve_taxonomy(m)[0] for m in subs}
    dates = [info["date"] for info in subs.values() if info.get("date")]
    latest = max(dates) if dates else "none"
    logos_ok, site_ok = _site_sync_status()

    try:
        model_tasks, _, _ = gather_coverage(check_remote=False)
    except Exception:
        model_tasks = {}

    full_tva = sum(
        1
        for st in model_tasks.values()
        if st["text"] >= 15 and st["vision"] > 0 and st["audio"] > 0
    )

    local_runs: list[str] = []
    if RUNS.is_dir():
        for d in sorted(RUNS.iterdir()):
            if d.is_dir() and (d / "submission.csv").is_file():
                local_runs.append(d.name)

    return DashboardSnapshot(
        pillar_count=len(pillars),
        text_items=text_n,
        vision_items=vision_n,
        audio_items=audio_n,
        submission_models=len(subs),
        submission_vendors=len(vendors),
        latest_date=latest,
        logos_ok=logos_ok,
        site_data_ok=site_ok,
        kaggle_models=len(model_tasks),
        kaggle_full_tva=full_tva,
        local_runs=local_runs,
    )


def build_run_args(
    *,
    model: str,
    judge: str = "",
    modality: str = "text",
    pillars: str = "",
    fresh: bool = False,
    base_url: str = "",
    api_key: str = "",
    reasoning_effort: str = "",
    gui_item_events: bool = False,
) -> list[str]:
    """Build `synhalees run ...` CLI arguments from form values."""
    args = ["run", "--model", model.strip()]
    if judge.strip():
        args.extend(["--judge", judge.strip()])
    if base_url.strip():
        args.extend(["--base-url", base_url.strip()])
    if api_key.strip():
        args.extend(["--api-key", api_key.strip()])
    if modality and modality != "text":
        args.extend(["--modality", modality])
    if pillars.strip() and pillars.strip().lower() != "all":
        args.extend(["--pillars", pillars.strip()])
    if fresh:
        args.append("--fresh")
    if reasoning_effort.strip():
        args.extend(["--reasoning-effort", reasoning_effort.strip()])
    if gui_item_events:
        args.append("--gui-item-events")
    return args


def count_judge_calls(pillars: list[str], modalities: list[str]) -> int:
    """Count runnable ``llm_judge`` rows using the same iterator as benchmark runs."""
    from synhalees.runner import iter_items

    return sum(
        1 for row in iter_items(pillars, modalities)
        if row["eval_type"] == "llm_judge"
    )


def build_kaggle_args(
    action: str,
    *,
    task: str = "all",
    model: str = "",
    modality: str = "",
    force: bool = False,
    dry_run: bool = False,
    build: bool = False,
    no_remote: bool = False,
    colab: bool = False,
) -> list[str]:
    """Build `synhalees kaggle <action> ...` CLI arguments from form values."""
    act = action.strip().lower()
    args = ["kaggle", act]
    if act == "gen":
        if dry_run:
            args.append("--dry-run")
        return args
    models = [m.strip() for m in re.split(r"[,\s]+", model.strip()) if m.strip()]

    if act == "coverage":
        if no_remote:
            args.append("--no-remote")
        if models:
            args.extend(["--provider", models[0]])
        return args
    if act in ("push", "status", "pull", "submit", "publish"):
        args.append(task.strip() or "all")
    elif act in ("run", "logs"):
        args.append(task.strip() or "synhalees-audio")

    if act in ("run", "logs", "pull", "status") and models:
        for m in models:
            args.extend(["-m", m])
    if act == "pull" and modality.strip():
        resolved_mod = _MODALITY_FLAG_MAP.get(modality.strip(), modality.strip())
        args.extend(["--modality", resolved_mod])
    if act == "pull" and force:
        args.append("--force")
    if act == "pull" and colab:
        args.append("--colab")
    if act in ("pull", "submit") and dry_run:
        args.append("--dry-run")
    if act == "submit" and build:
        args.append("--build")
    return args


def _shorten(text: str, limit: int) -> str:
    """Trim a UI status string to *limit* characters with an ellipsis."""
    clean = " ".join(str(text).split())
    return clean if len(clean) <= limit else clean[: limit - 1].rstrip() + "…"


def format_cmd_preview(args: list[str]) -> str:
    """Format a human-readable `synhalees ...` preview string.

    ``--api-key`` values are masked so a self-hosted endpoint key never lands
    in the GUI console log (which users copy into bug reports).
    """
    text = "synhalees " + " ".join(shlex.quote(a) for a in args)
    return re.sub(r"(--api-key\s+)\S+", r"\1***", text)


_UNKNOWN_ORG_TOKENS = {"", "other", "unknown", "none", "-"}


def find_unregistered_models(models: list[str] | tuple[str, ...]) -> list[str]:
    """Return canonical slugs missing a display_name or valid provider/family taxonomy."""
    missing: list[str] = []
    for raw in models:
        canon = canonical_model_slug(raw)
        if not canon:
            continue
        vendor, fam, _ = default_registry.resolve_taxonomy(canon)
        if (
            canon not in default_registry.display_names
            or vendor.lower() in _UNKNOWN_ORG_TOKENS
            or fam.lower() in _UNKNOWN_ORG_TOKENS
        ):
            if canon not in missing:
                missing.append(canon)
    return missing


def save_model_registry_entries(
    entries: list[dict[str, Any]],
    *,
    new_orgs: dict[str, dict[str, str]] | None = None,
    models_json_path: Path | None = None,
) -> int:
    """Validate and persist model display_names, orgs, families & open_source flags."""
    path = models_json_path or MODELS_JSON
    data = json.loads(path.read_text(encoding="utf-8"))
    providers = dict(data.get("providers") or {})
    disp = dict(data.get("display_names") or {})

    for org_slug, meta in (new_orgs or {}).items():
        clean_org = org_slug.strip().lower()
        if not clean_org or clean_org in _UNKNOWN_ORG_TOKENS:
            raise ValueError(f"Invalid organization slug: {org_slug!r}")
        if clean_org not in providers:
            providers[clean_org] = {
                "name": meta.get("name", clean_org.title()).strip() or clean_org.title(),
                "slug": clean_org,
                "logo": meta.get("logo", clean_org).strip() or clean_org,
                "color": meta.get("color", "gray").strip() or "gray",
                "aliases": [clean_org],
                "families": {},
            }

    saved = 0
    for item in entries:
        canon = str(item.get("canonical") or "").strip()
        dname = str(item.get("display_name") or "").strip()
        org = str(item.get("org") or "").strip().lower()
        fam = str(item.get("family") or "").strip().lower()
        oss = bool(item.get("open_source", False))

        if not canon or not dname:
            raise ValueError("Canonical Slug and Display Name are required for every model.")
        if org in _UNKNOWN_ORG_TOKENS:
            raise ValueError(f"Select or create a valid Organization for '{canon}'.")
        if fam in _UNKNOWN_ORG_TOKENS:
            raise ValueError(f"Select or enter a valid Family for '{canon}'.")
        if org not in providers:
            raise ValueError(f"Organization '{org}' does not exist yet — create it with '+ New Org'.")

        prov_obj = providers[org]
        fams = dict(prov_obj.get("families") or {})
        fam_entry = dict(fams.get(fam) or {
            "name": fam.replace("-", " ").title(),
            "open_source": oss,
            "match_patterns": [fam],
        })
        fam_entry["open_source"] = oss
        patterns = list(fam_entry.get("match_patterns") or [])
        c_low = canon.lower()
        if not any(p.lower() in c_low for p in patterns):
            patterns.append(c_low)
        fam_entry["match_patterns"] = patterns
        fams[fam] = fam_entry
        prov_obj["families"] = fams
        providers[org] = prov_obj

        disp[canon] = dname
        saved += 1

    if not saved:
        return 0

    data["providers"] = providers
    data["display_names"] = dict(sorted(disp.items(), key=lambda kv: kv[0].lower()))
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if path.resolve() == MODELS_JSON.resolve():
        default_registry.load()
    return saved


def delete_model_registry_entry(
    canonical: str,
    models_json_path: Path | None = None,
) -> bool:
    """Remove a canonical model entry from display_names in synhalees/models.json."""
    canon = canonical.strip()
    if not canon:
        return False
    path = models_json_path or MODELS_JSON
    data = json.loads(path.read_text(encoding="utf-8"))
    disp = dict(data.get("display_names") or {})
    if canon not in disp:
        return False
    del disp[canon]
    data["display_names"] = dict(sorted(disp.items(), key=lambda kv: kv[0].lower()))
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if path.resolve() == MODELS_JSON.resolve():
        default_registry.load()
    return True


def delete_model_completely(
    canonical: str,
    *,
    models_json_path: Path | None = None,
    submissions_dir: Path | None = None,
    slim_dir: Path | None = None,
) -> dict[str, int]:
    """Delete a model from models.json, submissions/, and kaggle/results/*.slim.csv."""
    import csv

    canon = canonical_model_slug(canonical.strip())
    if not canon:
        return {"registry": 0, "submissions": 0, "slim_rows": 0}

    reg_removed = 1 if delete_model_registry_entry(canon, models_json_path=models_json_path) else 0

    sub_root = submissions_dir or (ROOT / "submissions")
    sub_removed = 0
    if sub_root.exists():
        for csv_path in list(sub_root.rglob("*.csv")):
            if csv_path.stem == canon or canonical_model_slug(csv_path.stem) == canon:
                csv_path.unlink()
                sub_removed += 1

    slim_root = slim_dir or (ROOT / "kaggle" / "results")
    slim_removed = 0
    if slim_root.exists():
        for slim_path in sorted(slim_root.glob("*.slim.csv")):
            raw_text = slim_path.read_text(encoding="utf-8", errors="replace")
            rows = list(csv.DictReader(raw_text.splitlines()))
            if not rows:
                continue
            fieldnames = list(rows[0].keys())
            kept: list[dict[str, str]] = []
            removed_here = 0
            for r in rows:
                m = str(r.get("model") or "").strip()
                if m == canon or (m and canonical_model_slug(m) == canon):
                    removed_here += 1
                else:
                    kept.append(r)
            if removed_here:
                with slim_path.open("w", encoding="utf-8", newline="") as fh:
                    writer = csv.DictWriter(fh, fieldnames=fieldnames)
                    writer.writeheader()
                    writer.writerows(kept)
                slim_removed += removed_here

    return {"registry": reg_removed, "submissions": sub_removed, "slim_rows": slim_removed}


def save_organization_entry(
    slug: str,
    *,
    name: str,
    logo: str = "",
    color: str = "gray",
    aliases: list[str] | None = None,
    families: dict[str, dict[str, Any]] | None = None,
    models_json_path: Path | None = None,
) -> None:
    """Create or update an organization/provider entry in synhalees/models.json."""
    clean_slug = slug.strip().lower()
    if not clean_slug or clean_slug in _UNKNOWN_ORG_TOKENS:
        raise ValueError(f"Invalid organization slug: {slug!r}")
    clean_name = name.strip() or clean_slug.title()
    clean_logo = logo.strip() or clean_slug
    clean_color = color.strip() or "gray"
    clean_aliases = [a.strip().lower() for a in (aliases or []) if a.strip()]
    if clean_slug not in clean_aliases:
        clean_aliases.insert(0, clean_slug)

    path = models_json_path or MODELS_JSON
    data = json.loads(path.read_text(encoding="utf-8"))
    providers = dict(data.get("providers") or {})
    existing = dict(providers.get(clean_slug) or {})
    norm_fams = dict(existing.get("families") or {})
    if families is not None:
        norm_fams = {}
        for fk, fv in families.items():
            f_slug = str(fk).strip().lower()
            if not f_slug or f_slug in _UNKNOWN_ORG_TOKENS:
                continue
            pats = [str(p).strip().lower() for p in (fv.get("match_patterns") or []) if str(p).strip()]
            if f_slug not in pats:
                pats.insert(0, f_slug)
            norm_fams[f_slug] = {
                "name": str(fv.get("name") or f_slug.replace("-", " ").title()).strip(),
                "open_source": bool(fv.get("open_source", False)),
                "match_patterns": pats,
            }
    providers[clean_slug] = {
        "name": clean_name,
        "slug": clean_slug,
        "logo": clean_logo,
        "color": clean_color,
        "aliases": clean_aliases,
        "families": norm_fams,
    }
    data["providers"] = providers
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if path.resolve() == MODELS_JSON.resolve():
        default_registry.load()


def bulk_update_model_registry_entries(
    canonical_slugs: list[str],
    *,
    org: str | None = None,
    family: str | None = None,
    open_source: bool | None = None,
    models_json_path: Path | None = None,
) -> int:
    """Bulk-update organization, family, and/or open_source flag for multiple canonical models."""
    path = models_json_path or MODELS_JSON
    if path.resolve() == MODELS_JSON.resolve():
        default_registry.load()
        reg = default_registry
    else:
        from synhalees.registry import Registry
        reg = Registry(path)
    entries: list[dict[str, Any]] = []
    for raw in canonical_slugs:
        canon = canonical_model_slug(raw.strip())
        if not canon:
            continue
        cur_org, cur_fam, cur_oss = reg.resolve_taxonomy(canon)
        entries.append({
            "canonical": canon,
            "display_name": reg.get_model_display_name(canon),
            "org": (org.strip().lower() if org else cur_org),
            "family": (family.strip().lower() if family else cur_fam),
            "open_source": (bool(open_source) if open_source is not None else cur_oss),
        })
    return save_model_registry_entries(entries, models_json_path=path)


def delete_organization_entry(
    slug: str,
    models_json_path: Path | None = None,
) -> bool:
    """Remove an organization/provider entry from synhalees/models.json."""
    clean_slug = slug.strip().lower()
    if not clean_slug:
        return False
    path = models_json_path or MODELS_JSON
    data = json.loads(path.read_text(encoding="utf-8"))
    providers = dict(data.get("providers") or {})
    if clean_slug not in providers:
        return False
    del providers[clean_slug]
    data["providers"] = providers
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if path.resolve() == MODELS_JSON.resolve():
        default_registry.load()
    return True


def save_model_display_names(
    entries: dict[str, str],
    models_json_path: Path | None = None,
) -> int:
    """Register or update canonical -> display_name entries in synhalees/models.json."""
    clean = {k.strip(): v.strip() for k, v in entries.items() if k.strip() and v.strip()}
    if not clean:
        return 0
    path = models_json_path or MODELS_JSON
    data = json.loads(path.read_text(encoding="utf-8"))
    disp = dict(data.get("display_names") or {})
    disp.update(clean)
    data["display_names"] = dict(sorted(disp.items(), key=lambda kv: kv[0].lower()))
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if path.resolve() == MODELS_JSON.resolve():
        default_registry.load()
    return len(clean)


def classify_pending_kaggle_models(
    avail: list[str],
    kg_cov: dict[str, dict[str, int]],
    subs: dict[str, Any],
) -> list[tuple[str, str, str]]:
    """Return `(raw_slug, canonical, status)` for Kaggle models not yet 15T+V+A complete."""
    pending: list[tuple[str, str, str]] = []
    for raw in avail:
        canon = canonical_model_slug(raw)
        cov = kg_cov.get(canon)
        is_full_cov = bool(cov and cov.get("text", 0) >= 15 and cov.get("vision", 0) > 0 and cov.get("audio", 0) > 0)
        if is_full_cov:
            continue
        if find_unregistered_models([canon]):
            status = "Unregistered"
        elif cov:
            status = f"Partial ({cov.get('text', 0)}T/{'V' if cov.get('vision', 0) else '-'}V/{'A' if cov.get('audio', 0) else '-'}A)"
        elif canon in subs:
            status = "In Submissions"
        else:
            status = "Ready to Pull"
        pending.append((raw, canon, status))
    return pending


TOKENS_FILE = Path.home() / ".synhalees" / "tokens.json"
_API_KEY_ENV_BY_PROVIDER = {
    "openai": "OPENAI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
}
_JUDGE_PROVIDER_PREF = "SYNHALEES_GUI_JUDGE_PROVIDER"
_JUDGE_MODEL_PREF = "SYNHALEES_GUI_JUDGE_MODEL"


def saved_judge_providers(tokens_file: Path | None = None) -> list[str]:
    """Return providers whose API keys were saved in the local Tokens & Keys settings."""
    path = tokens_file or TOKENS_FILE
    try:
        if not path.is_file():
            return []
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, dict):
        raise RuntimeError(f"Saved API keys in {path} must be a JSON object.")
    return [
        provider
        for provider, env_name in _API_KEY_ENV_BY_PROVIDER.items()
        if isinstance(data.get(env_name), str) and data[env_name].strip()
    ]


def load_saved_tokens(tokens_file: Path | None = None, *, overwrite: bool = False) -> int:
    """Load persisted tokens from ~/.synhalees/tokens.json into os.environ."""
    path = tokens_file or TOKENS_FILE
    try:
        if not path.is_file():
            return 0
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return 0
    if not isinstance(data, dict):
        return 0
    loaded = 0
    for k, v in data.items():
        if isinstance(k, str) and isinstance(v, str) and v.strip():
            if overwrite or not os.environ.get(k):
                os.environ[k] = v.strip()
                loaded += 1
    return loaded


def kaggle_auth_status() -> str:
    """Return a concise summary of active Kaggle credentials."""
    if os.environ.get("KAGGLE_API_TOKEN"):
        return "KAGGLE_API_TOKEN set"
    if os.environ.get("KAGGLE_USERNAME") and os.environ.get("KAGGLE_KEY"):
        return f"Env ({os.environ['KAGGLE_USERNAME']})"
    cfg = Path.home() / ".kaggle" / "kaggle.json"
    try:
        if cfg.is_file():
            return "~/.kaggle/kaggle.json"
    except OSError:
        pass
    return "Not configured"


def is_reasoning_model(model_name: str) -> bool:
    """Return True if model name indicates an extended thinking / reasoning model."""
    low = model_name.lower()
    return any(
        k in low
        for k in (
            "thinking",
            "think",
            "reason",
            "deepseek-r1",
            "-r1",
            "/r1",
            "qwq",
            "o1",
            "o3",
            "o4",
        )
    )


def apply_tokens(
    *,
    kaggle_token: str = "",
    kaggle_username: str = "",
    kaggle_key: str = "",
    save_kaggle_json: bool = False,
    api_keys: dict[str, str] | None = None,
    kaggle_dir: Path | None = None,
    persist: bool = True,
    tokens_file: Path | None = None,
) -> list[str]:
    """Apply credentials and endpoint settings to os.environ and persist them locally.

    Supports pasting either a raw `KAGGLE_API_TOKEN` (e.g. `KGAT_...`) or a full
    `{"username": "...", "key": "..."}` JSON snippet into *kaggle_token*.
    Returns human-readable notes describing what was updated.
    """
    notes: list[str] = []
    tok = kaggle_token.strip()
    user = kaggle_username.strip()
    key = kaggle_key.strip()

    if tok.startswith("{") and tok.endswith("}"):
        try:
            parsed = json.loads(tok)
            if isinstance(parsed, dict):
                user = str(parsed.get("username") or user).strip()
                key = str(parsed.get("key") or key).strip()
                tok = ""
        except json.JSONDecodeError:
            pass

    if tok:
        os.environ["KAGGLE_API_TOKEN"] = tok
        notes.append("KAGGLE_API_TOKEN updated")
    if user:
        os.environ["KAGGLE_USERNAME"] = user
    if key:
        os.environ["KAGGLE_KEY"] = key
    if user and key:
        notes.append(f"KAGGLE_USERNAME/KAGGLE_KEY set for '{user}'")
        if save_kaggle_json:
            target_dir = kaggle_dir or (Path.home() / ".kaggle")
            target_dir.mkdir(parents=True, exist_ok=True)
            cfg = target_dir / "kaggle.json"
            cfg.write_text(json.dumps({"username": user, "key": key}, indent=2) + "\n", encoding="utf-8")
            notes.append(f"Saved {cfg}")

    if tok or (user and key):
        kaggle_ops._MODELS_CACHE = None
        kaggle_ops._STATUS_MODELS_CACHE.clear()

    for env_name, val in (api_keys or {}).items():
        clean = val.strip()
        if clean:
            os.environ[env_name] = clean
            notes.append(f"{env_name} updated")

    if persist:
        tfile = tokens_file or TOKENS_FILE
        existing: dict[str, str] = {}
        if tfile.is_file():
            try:
                raw = json.loads(tfile.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    existing = {str(k): str(v) for k, v in raw.items() if isinstance(v, str) and v.strip()}
            except Exception:
                existing = {}

        if tok:
            existing["KAGGLE_API_TOKEN"] = tok
        elif not kaggle_token.strip() and "KAGGLE_API_TOKEN" in existing and not os.environ.get("KAGGLE_API_TOKEN"):
            existing.pop("KAGGLE_API_TOKEN", None)
        if user:
            existing["KAGGLE_USERNAME"] = user
        elif "KAGGLE_USERNAME" in existing and not os.environ.get("KAGGLE_USERNAME"):
            existing.pop("KAGGLE_USERNAME", None)
        if key:
            existing["KAGGLE_KEY"] = key
        elif "KAGGLE_KEY" in existing and not os.environ.get("KAGGLE_KEY"):
            existing.pop("KAGGLE_KEY", None)
        for env_name, val in (api_keys or {}).items():
            clean = val.strip()
            if clean:
                existing[env_name] = clean
            else:
                existing.pop(env_name, None)
                os.environ.pop(env_name, None)

        if existing:
            tfile.parent.mkdir(parents=True, exist_ok=True)
            tfile.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
            try:
                if os.name != "nt":
                    tfile.chmod(0o600)
            except Exception:
                pass
            notes.append(f"Persisted tokens to {tfile}")
        elif tfile.is_file():
            tfile.unlink()

    return notes


# ---------------------------------------------------------------------------
# HiDPI & Palette
# ---------------------------------------------------------------------------


def _enable_hidpi() -> None:
    """Enable crisp HiDPI rendering on Windows before creating the Tk root."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        try:
            if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
                return
        except Exception:
            pass
        shcore = getattr(ctypes.windll, "shcore", None)
        if shcore is not None:
            for level in (2, 1):
                try:
                    shcore.SetProcessDpiAwareness(level)
                    return
                except Exception:
                    pass
        user32.SetProcessDPIAware()
    except Exception:
        pass


PALETTE = {
    "bg": "#141824",
    "sidebar": "#191e2e",
    "surface": "#1d2336",
    "card": "#252c44",
    "card_hover": "#2d3654",
    "border": "#364060",
    "text": "#eef1f8",
    "muted": "#9aa3ba",
    "accent": "#e04545",
    "accent_hover": "#f15a5a",
    "ok": "#2fb368",
    "warn": "#f5b301",
    "info": "#56a8ff",
    "step_badge": "#303a5a",
    "console_bg": "#0e111b",
}


# ---------------------------------------------------------------------------
# Desktop Studio Application (tkinter / ttk)
# ---------------------------------------------------------------------------


class SynhaleesGUI:
    """Streamlined SynhalEES Benchmark Studio with sidebar workflows."""

    def __init__(self, root: Any, *, initial_tab: str = "overview") -> None:
        import tkinter as tk
        from tkinter import filedialog, messagebox, scrolledtext, ttk

        self.tk = tk
        self.ttk = ttk
        self.messagebox = messagebox
        self.filedialog = filedialog
        self.root = root
        load_saved_tokens()
        try:
            self._scale = max(1.0, float(root.winfo_fpixels("1i")) / 96.0)
        except Exception:
            self._scale = 1.0

        self.root.title("SynhalEES Benchmark Studio")
        sw = max(800, root.winfo_screenwidth())
        sh = max(600, root.winfo_screenheight())
        win_w = min(self._px(1180), int(sw * 0.93))
        win_h = min(self._px(780), int(sh * 0.91))
        min_w = min(self._px(920), int(sw * 0.85))
        min_h = min(self._px(620), int(sh * 0.80))
        self.root.geometry(f"{win_w}x{win_h}")
        self.root.minsize(min_w, min_h)
        self.root.configure(bg=PALETTE["bg"])

        self._proc: subprocess.Popen[str] | None = None
        self._proc_lock = threading.Lock()
        self._log_queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._last_args: list[str] = []
        self._last_cmd_text: str = ""
        self._active_page = "overview"
        self._nav_buttons: dict[str, Any] = {}
        self._pages: dict[str, Any] = {}
        self._console_visible = False
        self._pipeline_queue: list[tuple[str, list[str]]] = []
        self._pipeline_on_complete: Callable[[], None] | None = None
        self._failed_kaggle_tasks: list[str] = []
        self._eval_done = 0
        self._eval_pass = 0
        self._eval_detail_window: Any = None
        self._eval_detail_text_widgets: dict[str, Any] = {}
        self._eval_detail_seen_item = False
        self._eval_detail_items: list[dict[str, Any]] = []
        self._eval_detail_item_indexes: dict[str, int] = {}
        self._eval_detail_index = -1
        self._eval_detail_active_key: str | None = None

        self._configure_styles()
        self._build_topbar()
        self._build_shell(scrolledtext)
        self._bind_shortcuts()
        self.select_page(initial_tab if initial_tab in TAB_KEYS else "overview")
        self.refresh_all()
        self.root.after(70, self._drain_queue)

    def _px(self, value: int) -> int:
        return max(1, round(value * self._scale))

    def _build_checkbutton_icons(self) -> tuple[Any, Any, Any]:
        """Render crisp DPI-scaled unchecked/hover/checkmark PhotoImages (no clam 'X')."""
        tk = self.tk
        sz = max(14, self._px(15))

        def _make_box(fill: str, border: str, checked: bool) -> Any:
            img = tk.PhotoImage(master=self.root, width=sz, height=sz)
            img.put(fill, to=(0, 0, sz, sz))
            # 1px crisp border
            img.put(border, to=(0, 0, sz, 1))
            img.put(border, to=(0, sz - 1, sz, sz))
            img.put(border, to=(0, 0, 1, sz))
            img.put(border, to=(sz - 1, 0, sz, sz))
            if checked:
                # Draw a crisp 2px white checkmark (✓) normalized to [0..1]
                pts = (
                    (0.22, 0.52), (0.30, 0.60), (0.38, 0.68), (0.43, 0.72),
                    (0.49, 0.63), (0.56, 0.53), (0.63, 0.43), (0.71, 0.33), (0.78, 0.25),
                )
                r = max(1, sz // 14)
                for nx, ny in pts:
                    cx = round(nx * (sz - 1))
                    cy = round(ny * (sz - 1))
                    img.put("#ffffff", to=(max(2, cx - r), max(2, cy - r), min(sz - 2, cx + r + 1), min(sz - 2, cy + r + 1)))
            return img

        return (
            _make_box(PALETTE["console_bg"], PALETTE["border"], False),
            _make_box(PALETTE["console_bg"], PALETTE["info"], False),
            _make_box(PALETTE["accent"], PALETTE["accent"], True),
        )

    def _configure_styles(self) -> None:
        s = self.ttk.Style(self.root)
        try:
            s.theme_use("clam")
        except Exception:
            pass

        bg = PALETTE["bg"]
        sb = PALETTE["sidebar"]
        surf = PALETTE["surface"]
        card = PALETTE["card"]
        fg = PALETTE["text"]
        muted = PALETTE["muted"]
        acc = PALETTE["accent"]
        bdr = PALETTE["border"]

        s.configure(".", background=bg, foreground=fg, font=("Segoe UI", 10))
        s.configure("TFrame", background=bg)
        s.configure("Sidebar.TFrame", background=sb)
        s.configure("Surface.TFrame", background=surf)
        s.configure("Card.TFrame", background=card, relief="flat")

        s.configure("TLabel", background=bg, foreground=fg)
        s.configure("Muted.TLabel", background=bg, foreground=muted, font=("Segoe UI", 9))
        s.configure("PageTitle.TLabel", background=bg, foreground=fg, font=("Segoe UI", 14, "bold"))
        s.configure("PageSub.TLabel", background=bg, foreground=muted, font=("Segoe UI", 9))
        s.configure("CardTitle.TLabel", background=card, foreground=muted, font=("Segoe UI", 8, "bold"))
        s.configure("CardValue.TLabel", background=card, foreground=fg, font=("Segoe UI", 13, "bold"))
        s.configure("CardSub.TLabel", background=card, foreground=muted, font=("Segoe UI", 9))
        s.configure("StepTitle.TLabel", background=card, foreground=fg, font=("Segoe UI", 11, "bold"))
        s.configure("StepHint.TLabel", background=card, foreground=muted, font=("Segoe UI", 9))
        s.configure("CardText.TLabel", background=card, foreground=fg, font=("Segoe UI", 9))
        s.configure("Surface.TLabel", background=surf, foreground=fg)

        s.configure(
            "Accent.TButton",
            background=acc,
            foreground="#ffffff",
            borderwidth=0,
            padding=[14, 7],
            font=("Segoe UI", 9, "bold"),
        )
        s.map(
            "Accent.TButton",
            background=[("active", PALETTE["accent_hover"]), ("disabled", bdr)],
            foreground=[("disabled", muted)],
        )

        s.configure(
            "Ghost.TButton",
            background=surf,
            foreground=fg,
            borderwidth=1,
            bordercolor=bdr,
            padding=[11, 6],
            font=("Segoe UI", 9),
        )
        s.map(
            "Ghost.TButton",
            background=[("active", PALETTE["card_hover"]), ("disabled", sb)],
            foreground=[("disabled", muted)],
        )

        s.configure(
            "Danger.TButton",
            background=surf,
            foreground="#ff7b7b",
            borderwidth=1,
            bordercolor="#7f2d2d",
            padding=[11, 6],
            font=("Segoe UI", 9),
        )
        s.map(
            "Danger.TButton",
            background=[("active", acc), ("disabled", sb)],
            foreground=[("active", "#ffffff"), ("disabled", muted)],
        )

        s.configure(
            "TEntry",
            fieldbackground=PALETTE["console_bg"],
            foreground=fg,
            insertcolor=fg,
            bordercolor=bdr,
            padding=5,
        )
        s.configure(
            "TCombobox",
            fieldbackground=PALETTE["console_bg"],
            background=surf,
            foreground=fg,
            arrowcolor=fg,
            bordercolor=bdr,
            padding=4,
        )
        s.map(
            "TCombobox",
            background=[("active", PALETTE["card_hover"]), ("readonly", surf)],
            fieldbackground=[("readonly", PALETTE["console_bg"])],
            selectbackground=[("readonly", card)],
            selectforeground=[("readonly", fg)],
        )
        s.configure("TCheckbutton", background=card, foreground=fg, font=("Segoe UI", 9))
        s.map("TCheckbutton", background=[("active", card)], foreground=[("active", "#ffffff")])
        s.configure("Surface.TCheckbutton", background=surf, foreground=fg, font=("Segoe UI", 9))
        s.map("Surface.TCheckbutton", background=[("active", surf)], foreground=[("active", "#ffffff")])

        self._cb_off, self._cb_hover, self._cb_on = self._build_checkbutton_icons()
        try:
            s.element_create(
                "SynhalEES.Checkbutton.indicator",
                "image",
                self._cb_off,
                ("selected", "active", self._cb_on),
                ("selected", self._cb_on),
                ("active", self._cb_hover),
                sticky="w",
                padding=(0, 0, self._px(6), 0),
            )
        except Exception:
            pass
        cb_layout = [(
            "Checkbutton.padding",
            {
                "sticky": "nswe",
                "children": [
                    ("SynhalEES.Checkbutton.indicator", {"side": "left", "sticky": ""}),
                    (
                        "Checkbutton.focus",
                        {
                            "side": "left",
                            "sticky": "w",
                            "children": [("Checkbutton.label", {"sticky": "nswe"})],
                        },
                    ),
                ],
            },
        )]
        for cb_style in ("TCheckbutton", "Surface.TCheckbutton"):
            try:
                s.layout(cb_style, cb_layout)
            except Exception:
                pass

        s.configure(
            "TScrollbar",
            background=card,
            troughcolor=sb,
            arrowcolor=fg,
            bordercolor=bdr,
        )
        s.map("TScrollbar", background=[("active", PALETTE["card_hover"])])

        s.configure(
            "Treeview",
            background=PALETTE["console_bg"],
            fieldbackground=PALETTE["console_bg"],
            foreground=fg,
            borderwidth=0,
            rowheight=self._px(26),
            font=("Segoe UI", 9),
        )
        s.configure(
            "Treeview.Heading",
            background=card,
            foreground=fg,
            relief="flat",
            font=("Segoe UI", 9, "bold"),
        )
        s.map(
            "Treeview.Heading",
            background=[("active", PALETTE["card_hover"]), ("pressed", bdr)],
            foreground=[("active", "#ffffff")],
        )
        s.map("Treeview", background=[("selected", "#354066")], foreground=[("selected", "#ffffff")])
        s.configure(
            "Horizontal.TProgressbar",
            troughcolor=PALETTE["console_bg"],
            background=acc,
            bordercolor=bdr,
            lightcolor=acc,
            darkcolor=acc,
            thickness=self._px(6),
        )

    # ------------------------------------------------------------------ layout

    def _build_topbar(self) -> None:
        tk, ttk = self.tk, self.ttk
        bar = tk.Frame(self.root, bg=PALETTE["sidebar"], highlightthickness=1, highlightbackground=PALETTE["border"])
        bar.pack(fill="x")

        inner = tk.Frame(bar, bg=PALETTE["sidebar"])
        inner.pack(fill="x", padx=self._px(16), pady=self._px(10))

        left = tk.Frame(inner, bg=PALETTE["sidebar"])
        left.pack(side="left")
        tk.Label(
            left,
            text="SynhalEES",
            bg=PALETTE["sidebar"],
            fg=PALETTE["accent"],
            font=("Segoe UI", 15, "bold"),
        ).pack(side="left")
        tk.Label(
            left,
            text=" Studio",
            bg=PALETTE["sidebar"],
            fg=PALETTE["text"],
            font=("Segoe UI", 15, "bold"),
        ).pack(side="left")
        tk.Label(
            left,
            text="  ·  Guided Benchmark Control Center",
            bg=PALETTE["sidebar"],
            fg=PALETTE["muted"],
            font=("Segoe UI", 9),
        ).pack(side="left", padx=(6, 0))

        right = tk.Frame(inner, bg=PALETTE["sidebar"])
        right.pack(side="right")
        self.status_badge = tk.Label(
            right,
            text="IDLE",
            bg=PALETTE["card"],
            fg=PALETTE["ok"],
            font=("Segoe UI", 9, "bold"),
            padx=self._px(10),
            pady=self._px(4),
        )
        self.status_badge.pack(side="left", padx=(0, 8))
        ttk.Button(right, text="Tokens & Keys", style="Ghost.TButton", command=self._open_tokens_dialog).pack(
            side="left", padx=(0, 6)
        )
        ttk.Button(right, text="Refresh", style="Ghost.TButton", command=self.refresh_all).pack(side="left", padx=(0, 6))
        ttk.Button(
            right,
            text="Open Leaderboard",
            style="Ghost.TButton",
            command=self._open_leaderboard,
        ).pack(side="left")

    def _build_shell(self, scrolledtext: Any) -> None:
        tk, ttk = self.tk, self.ttk

        # Bottom Live Output Console Drawer (packed first with side="bottom" so
        # hiding the log collapses it to a slim 1-line footer taking zero extra height)
        self.console_wrap = tk.Frame(
            self.root,
            bg=PALETTE["surface"],
            highlightthickness=1,
            highlightbackground=PALETTE["border"],
        )
        self.console_wrap.pack(side="bottom", fill="x")

        body = ttk.Frame(self.root)
        body.pack(side="top", fill="both", expand=True)

        # Left Navigation Rail
        sidebar = tk.Frame(
            body,
            bg=PALETTE["sidebar"],
            width=self._px(220),
            highlightthickness=1,
            highlightbackground=PALETTE["border"],
        )
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)

        tk.Label(
            sidebar,
            text="WORKFLOWS",
            bg=PALETTE["sidebar"],
            fg=PALETTE["muted"],
            font=("Segoe UI", 8, "bold"),
            anchor="w",
        ).pack(fill="x", padx=self._px(16), pady=(self._px(14), self._px(6)))

        nav_items = [
            ("overview", "1. Overview", "Status, workflows & explorer"),
            ("kaggle", "2. Kaggle Sync", "Discover, run, pull & import"),
            ("run", "3. Local Eval", "Run API / Ollama & publish"),
            ("management", "4. Management", "Models & organizations"),
            ("site", "5. Site & CI", "Build data, logos & verify"),
        ]
        for key, title, sub in nav_items:
            btn = tk.Frame(sidebar, bg=PALETTE["sidebar"], cursor="hand2")
            btn.pack(fill="x", padx=self._px(8), pady=self._px(2))
            accent_bar = tk.Frame(btn, bg=PALETTE["sidebar"], width=self._px(3))
            accent_bar.pack(side="left", fill="y")
            txt_wrap = tk.Frame(btn, bg=PALETTE["sidebar"])
            txt_wrap.pack(side="left", fill="x", expand=True, padx=self._px(10), pady=self._px(8))
            lbl_t = tk.Label(
                txt_wrap,
                text=title,
                bg=PALETTE["sidebar"],
                fg=PALETTE["text"],
                font=("Segoe UI", 10, "bold"),
                anchor="w",
            )
            lbl_t.pack(fill="x")
            lbl_s = tk.Label(
                txt_wrap,
                text=sub,
                bg=PALETTE["sidebar"],
                fg=PALETTE["muted"],
                font=("Segoe UI", 8),
                anchor="w",
            )
            lbl_s.pack(fill="x")

            for w in (btn, accent_bar, txt_wrap, lbl_t, lbl_s):
                w.bind("<Button-1>", lambda _e, k=key: self.select_page(k))

            self._nav_buttons[key] = (btn, accent_bar, txt_wrap, lbl_t, lbl_s)

        # Workspace container (right)
        self.content_host = ttk.Frame(body)
        self.content_host.pack(side="left", fill="both", expand=True)

        self._pages["overview"] = self._make_scrollable_page("overview")
        self._pages["kaggle"] = self._make_scrollable_page("kaggle")
        self._pages["run"] = self._make_scrollable_page("run")
        self._pages["management"] = self._make_scrollable_page("management")
        self._pages["site"] = self._make_scrollable_page("site")

        self._build_overview_page(self._pages["overview"].inner)
        self._build_kaggle_page(self._pages["kaggle"].inner)
        self._build_run_page(self._pages["run"].inner)
        self._build_management_page(self._pages["management"].inner)
        self._build_site_page(self._pages["site"].inner)

        # Drag-resizable top sash above the Activity Log drawer
        self.console_sash = tk.Frame(
            self.console_wrap,
            bg=PALETTE["border"],
            height=self._px(4),
            cursor="sb_v_double_arrow",
        )
        self.console_sash.pack(fill="x", side="top")

        def _on_sash_drag(event: Any) -> None:
            if not self._console_visible:
                self._toggle_console()
            root_bottom = self.root.winfo_rooty() + self.root.winfo_height()
            desired_px = max(
                self._px(80),
                min(int(self.root.winfo_height() * 0.65), root_bottom - event.y_root - self._px(40)),
            )
            line_px = max(12, self._px(16))
            new_lines = max(4, min(32, desired_px // line_px))
            self.console.configure(height=new_lines)

        self.console_sash.bind("<B1-Motion>", _on_sash_drag)
        self.console_sash.bind("<Enter>", lambda _e: self.console_sash.configure(bg=PALETTE["info"]))
        self.console_sash.bind("<Leave>", lambda _e: self.console_sash.configure(bg=PALETTE["border"]))
        self.console_sash.bind("<Double-1>", lambda _e: self._toggle_console())

        # Bottom Live Output Console controls (packed inside console_wrap created above)
        self.progress_wrap = tk.Frame(self.console_wrap, bg=PALETTE["surface"])
        self.progress_msg_var = tk.StringVar(value="")
        self.progress_pct_var = tk.StringVar(value="")
        tk.Label(
            self.progress_wrap,
            textvariable=self.progress_msg_var,
            bg=PALETTE["surface"],
            fg=PALETTE["warn"],
            font=("Segoe UI", 9, "bold"),
            anchor="w",
        ).pack(side="left", fill="x", expand=True)
        self.progress_bar = ttk.Progressbar(
            self.progress_wrap,
            mode="indeterminate",
            length=self._px(220),
            style="Horizontal.TProgressbar",
        )
        self.progress_bar.pack(side="right", padx=(self._px(8), 0))
        tk.Label(
            self.progress_wrap,
            textvariable=self.progress_pct_var,
            bg=PALETTE["surface"],
            fg=PALETTE["info"],
            font=("Consolas", 9, "bold"),
            anchor="e",
        ).pack(side="right", padx=(self._px(8), 0))

        self.cbar = tk.Frame(self.console_wrap, bg=PALETTE["surface"])
        cbar = self.cbar
        cbar.pack(fill="x", padx=self._px(12), pady=self._px(6))
        tk.Label(
            cbar,
            text="ACTIVITY LOG",
            bg=PALETTE["surface"],
            fg=PALETTE["muted"],
            font=("Segoe UI", 8, "bold"),
        ).pack(side="left")

        self.cmd_label_var = tk.StringVar(value="Ready — no command yet")
        tk.Label(
            cbar,
            textvariable=self.cmd_label_var,
            bg=PALETTE["surface"],
            fg=PALETTE["info"],
            font=("Consolas", 9),
        ).pack(side="left", padx=(self._px(12), 0))

        self.stop_btn = ttk.Button(
            cbar,
            text="Stop",
            style="Danger.TButton",
            command=self.stop_command,
            state="disabled",
        )
        self.stop_btn.pack(side="right", padx=(6, 0))
        ttk.Button(cbar, text="Clear", style="Ghost.TButton", command=self._clear_log).pack(side="right", padx=(6, 0))
        ttk.Button(cbar, text="Copy Log", style="Ghost.TButton", command=self._copy_log).pack(side="right", padx=(6, 0))
        self.toggle_log_btn = ttk.Button(cbar, text="Show Log", style="Ghost.TButton", command=self._toggle_console)
        self.toggle_log_btn.pack(side="right")

        self.console_body = tk.Frame(self.console_wrap, bg=PALETTE["console_bg"])
        # Hidden by default so workflow content gets full vertical space.

        self.console = scrolledtext.ScrolledText(
            self.console_body,
            height=8,
            bg=PALETTE["console_bg"],
            fg=PALETTE["text"],
            insertbackground=PALETTE["text"],
            font=("Consolas", 9),
            relief="flat",
            borderwidth=0,
            wrap="word",
            state="disabled",
        )
        self.console.pack(fill="both", expand=True)
        self.console.tag_config("cmd", foreground=PALETTE["info"], font=("Consolas", 9, "bold"))
        self.console.tag_config("ok", foreground=PALETTE["ok"])
        self.console.tag_config("warn", foreground=PALETTE["warn"])
        self.console.tag_config("err", foreground=PALETTE["accent"])
        self.console.tag_config("muted", foreground=PALETTE["muted"])

    def _make_scrollable_page(self, key: str) -> Any:
        tk, ttk = self.tk, self.ttk
        outer = ttk.Frame(self.content_host)
        canvas = tk.Canvas(outer, bg=PALETTE["bg"], highlightthickness=0, borderwidth=0)
        vsb = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        inner = ttk.Frame(canvas, padding=(self._px(20), self._px(16), self._px(20), self._px(20)))
        win_id = canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=vsb.set)

        canvas.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        def _sync_region(_e=None) -> None:
            canvas.configure(scrollregion=canvas.bbox("all"))

        def _sync_width(e: Any) -> None:
            canvas.itemconfigure(win_id, width=e.width)

        def _on_wheel(e: Any) -> None:
            w = getattr(e, "widget", None)
            if isinstance(w, (ttk.Treeview, tk.Text, tk.Listbox)):
                try:
                    if w.yview() != (0.0, 1.0):
                        return
                except Exception:
                    pass
            try:
                delta = int(-1 * (e.delta / 120)) if e.delta else 0
                if delta:
                    canvas.yview_scroll(delta, "units")
            except Exception:
                pass

        inner.bind("<Configure>", _sync_region)
        canvas.bind("<Configure>", _sync_width)
        canvas.bind("<Enter>", lambda _e: canvas.bind_all("<MouseWheel>", _on_wheel))
        canvas.bind("<Leave>", lambda _e: canvas.unbind_all("<MouseWheel>"))

        outer.inner = inner  # type: ignore[attr-defined]
        outer._key = key     # type: ignore[attr-defined]
        return outer

    def _make_scrolled_tree(
        self,
        parent: Any,
        *,
        columns: tuple[str, ...],
        height: int,
        selectmode: str = "browse",
        show: str = "headings",
        fill: str = "x",
        expand: bool = False,
        sortable: bool = True,
    ) -> Any:
        """Create a Treeview paired with an inside vertical scrollbar, zebra tags, and sorting."""
        tk, ttk = self.tk, self.ttk
        wrap = tk.Frame(parent, bg=PALETTE["card"])
        wrap.pack(fill=fill, expand=expand)
        tree = ttk.Treeview(wrap, columns=columns, show=show, height=height, selectmode=selectmode)
        sb = ttk.Scrollbar(wrap, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=sb.set)
        tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        tree.tag_configure("even", background=PALETTE["console_bg"])
        tree.tag_configure("odd", background="#141929")
        tree.tag_configure("ok", foreground=PALETTE["ok"])
        tree.tag_configure("warn", foreground=PALETTE["warn"])
        tree.tag_configure("err", foreground=PALETTE["accent"])
        tree.tag_configure("info", foreground=PALETTE["info"])
        tree.tag_configure("muted", foreground=PALETTE["muted"])
        if sortable:
            self._enable_tree_sorting(tree, columns)
        return tree

    @staticmethod
    def _sort_key_for_cell(val: Any) -> tuple[int, Any]:
        s = str(val).strip()
        m_frac = re.match(r"^(\d+)\s*/\s*(\d+)", s)
        if m_frac:
            return (0, (float(m_frac.group(1)), float(m_frac.group(2))))
        cleaned = s.replace("%", "").replace("$", "").replace(",", "")
        try:
            return (0, (float(cleaned), 0.0))
        except ValueError:
            return (1, s.lower())

    def _enable_tree_sorting(self, tree: Any, columns: tuple[str, ...]) -> None:
        tree._sort_col = None
        tree._sort_desc = False
        tree._heading_texts = {}

        def _sort_by(col: str) -> None:
            for c in columns:
                if c not in tree._heading_texts:
                    tree._heading_texts[c] = tree.heading(c, "text")
            items = [i for i in tree.get_children("") if i != "__empty__"]
            if not items:
                return
            desc = (not tree._sort_desc) if tree._sort_col == col else False
            tree._sort_col = col
            tree._sort_desc = desc
            scored = [(self._sort_key_for_cell(tree.set(i, col)), i) for i in items]
            scored.sort(key=lambda t: t[0], reverse=desc)
            for idx, (_, iid) in enumerate(scored):
                tree.move(iid, "", idx)
                cur_tags = [t for t in tree.item(iid, "tags") if t not in ("even", "odd")]
                cur_tags.insert(0, "odd" if idx % 2 else "even")
                tree.item(iid, tags=tuple(cur_tags))
            for c in columns:
                base = tree._heading_texts.get(c, c)
                arrow = (" ▼" if desc else " ▲") if c == col else ""
                tree.heading(c, text=f"{base}{arrow}")

        for col in columns:
            tree.heading(col, command=lambda c=col: _sort_by(c))

    def _ensure_tree_empty_state(self, tree: Any, message: str) -> None:
        if tree.get_children(""):
            return
        cols = tree["columns"]
        vals = (message,) + ("",) * (max(0, len(cols) - 1))
        tree.insert("", "end", iid="__empty__", values=vals, tags=("even", "muted"))

    def _copy_text_to_clipboard(self, text: str, label: str = "text") -> None:
        if not text:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self._append_log(f"[OK] Copied {label} '{text}' to clipboard.\n", "ok")

    def _bind_tree_context_menu(self, tree: Any, actions_fn: Callable[[tuple[str, ...]], list[tuple[str, Callable[[], None]] | None]]) -> None:
        tk = self.tk

        def _on_right_click(event: Any) -> str | None:
            row = tree.identify_row(event.y)
            if not row or row == "__empty__":
                return None
            if row not in tree.selection():
                tree.selection_set(row)
            vals = tuple(str(v) for v in (tree.item(row, "values") or ()))
            items = actions_fn(vals)
            if not items:
                return None
            menu = tk.Menu(
                tree,
                tearoff=0,
                bg=PALETTE["card"],
                fg=PALETTE["text"],
                activebackground=PALETTE["accent"],
                activeforeground="#ffffff",
                relief="flat",
                bd=1,
            )
            for entry in items:
                if entry is None:
                    menu.add_separator()
                else:
                    lbl, cmd = entry
                    menu.add_command(label=lbl, command=cmd)
            try:
                menu.tk_popup(event.x_root, event.y_root)
            finally:
                menu.grab_release()
            return "break"

        tree.bind("<Button-3>", _on_right_click)
        tree.bind("<Button-2>", _on_right_click)

    def _focus_active_search(self) -> None:
        target = {
            "overview": getattr(self, "model_search_entry", None),
            "kaggle": getattr(self, "kg_cov_search_entry", None),
            "management": getattr(self, "mgmt_model_search_entry", None),
        }.get(self._active_page)
        if target is not None:
            target.focus_set()
            try:
                target.selection_range(0, "end")
            except Exception:
                pass

    def _bind_shortcuts(self) -> None:
        self.root.bind("<Control-r>", lambda _e: self.refresh_all())
        self.root.bind("<F5>", lambda _e: self.refresh_all())
        self.root.bind("<Control-l>", lambda _e: self._toggle_console())
        self.root.bind("<Control-f>", lambda _e: self._focus_active_search())
        for idx, key in enumerate(TAB_KEYS, start=1):
            self.root.bind(f"<Control-Key-{idx}>", lambda _e, k=key: self.select_page(k))

    def select_page(self, key: str) -> None:
        if key not in self._pages:
            key = "overview"
        self._active_page = key
        for k, page in self._pages.items():
            if k == key:
                page.pack(fill="both", expand=True)
            else:
                page.pack_forget()
        for k, (btn, bar, wrap, lt, ls) in self._nav_buttons.items():
            active = k == key
            bg = PALETTE["card"] if active else PALETTE["sidebar"]
            btn.configure(bg=bg)
            wrap.configure(bg=bg)
            lt.configure(bg=bg, fg="#ffffff" if active else PALETTE["text"])
            ls.configure(bg=bg)
            bar.configure(bg=PALETTE["accent"] if active else bg)

    def _toggle_console(self) -> None:
        self._console_visible = not self._console_visible
        if self._console_visible:
            self.console_body.pack(fill="both", expand=True, padx=self._px(10), pady=(0, self._px(8)))
            self.toggle_log_btn.configure(text="Hide Log")
        else:
            self.console_body.pack_forget()
            self.toggle_log_btn.configure(text="Show Log")

    # ------------------------------------------------------------------ Card helpers

    def _page_header(self, parent: Any, title: str, subtitle: str) -> None:
        ttk = self.ttk
        ttk.Label(parent, text=title, style="PageTitle.TLabel").pack(anchor="w")
        ttk.Label(parent, text=subtitle, style="PageSub.TLabel").pack(anchor="w", pady=(2, self._px(12)))

    def _step_card(
        self,
        parent: Any,
        step_num: str,
        title: str,
        hint: str,
        *,
        fill: str = "x",
        expand: bool = False,
    ) -> Any:
        tk, ttk = self.tk, self.ttk
        frame = tk.Frame(
            parent,
            bg=PALETTE["card"],
            highlightthickness=1,
            highlightbackground=PALETTE["border"],
            padx=self._px(14),
            pady=self._px(12),
        )
        frame.pack(fill=fill, expand=expand, pady=(0, self._px(12)))

        head = tk.Frame(frame, bg=PALETTE["card"])
        head.pack(fill="x", pady=(0, self._px(8)))
        if step_num:
            is_opt = "OPTIONAL" in step_num.upper()
            tk.Label(
                head,
                text=step_num,
                bg=PALETTE["surface"] if is_opt else PALETTE["step_badge"],
                fg=PALETTE["muted"] if is_opt else PALETTE["info"],
                font=("Segoe UI", 8, "bold"),
                padx=self._px(8),
                pady=self._px(2),
            ).pack(side="left", padx=(0, self._px(10)))
        ttk.Label(head, text=title, style="StepTitle.TLabel").pack(side="left")
        if hint:
            ttk.Label(frame, text=hint, style="StepHint.TLabel", wraplength=self._px(780), justify="left").pack(
                anchor="w", pady=(0, self._px(8))
            )
        return frame

    # ------------------------------------------------------------------ 1. Overview

    def _build_overview_page(self, parent: Any) -> None:
        tk, ttk = self.tk, self.ttk
        self._page_header(
            parent,
            "Project Overview",
            "Live snapshot of your dataset, published leaderboard submissions, Kaggle archive, and site sync.",
        )

        # 4 Summary KPI Cards
        cards_row = ttk.Frame(parent)
        cards_row.pack(fill="x", pady=(0, self._px(14)))
        for col in range(4):
            cards_row.columnconfigure(col, weight=1, uniform="kpi")

        self.card_vars: dict[str, tuple[Any, Any, Any]] = {}
        specs = [
            ("dataset", "DATASET"),
            ("submissions", "PUBLISHED MODELS"),
            ("site", "SITE SYNC"),
            ("kaggle", "KAGGLE SLIM ARCHIVE"),
        ]
        for idx, (key, title) in enumerate(specs):
            card = tk.Frame(
                cards_row,
                bg=PALETTE["card"],
                highlightthickness=1,
                highlightbackground=PALETTE["border"],
                padx=self._px(12),
                pady=self._px(10),
            )
            card.grid(row=0, column=idx, sticky="nsew", padx=(0 if idx == 0 else self._px(8), 0))
            ttk.Label(card, text=title, style="CardTitle.TLabel").pack(anchor="w")
            v_main = tk.StringVar(value="—")
            v_sub = tk.StringVar(value="")
            v_lbl = tk.Label(
                card,
                textvariable=v_main,
                bg=PALETTE["card"],
                fg=PALETTE["text"],
                font=("Segoe UI", 13, "bold"),
                anchor="w",
            )
            v_lbl.pack(anchor="w", pady=(4, 2))
            ttk.Label(card, textvariable=v_sub, style="CardSub.TLabel").pack(anchor="w")
            self.card_vars[key] = (v_main, v_sub, v_lbl)

        # Workflow Launchpad Cards (2-column compact row)
        wf_row = ttk.Frame(parent)
        wf_row.pack(fill="x", pady=(0, self._px(12)))
        wf_row.columnconfigure(0, weight=1, uniform="ov_wf")
        wf_row.columnconfigure(1, weight=1, uniform="ov_wf")
        wf_left = ttk.Frame(wf_row)
        wf_left.grid(row=0, column=0, sticky="nsew", padx=(0, self._px(10)))
        wf_right = ttk.Frame(wf_row)
        wf_right.grid(row=0, column=1, sticky="nsew")

        c_kaggle = self._step_card(
            wf_left,
            "WORKFLOW A",
            "Sync New Models from Kaggle",
            "Discover new runs, pull slim CSVs via Colab, import & rebuild.",
            fill="both",
            expand=True,
        )
        r_kg = tk.Frame(c_kaggle, bg=PALETTE["card"])
        r_kg.pack(fill="x")
        ttk.Button(
            r_kg,
            text="Kaggle Sync ->",
            style="Accent.TButton",
            command=lambda: self.select_page("kaggle"),
        ).pack(side="left", padx=(0, 6))
        ttk.Button(
            r_kg,
            text="One-Click Sync",
            style="Ghost.TButton",
            command=self._start_one_click_kaggle_sync,
        ).pack(side="left", padx=(0, 6))
        ttk.Button(
            r_kg,
            text="Discover Models",
            style="Ghost.TButton",
            command=self._discover_kaggle_models,
        ).pack(side="left")

        c_local = self._step_card(
            wf_right,
            "WORKFLOW B",
            "Local Eval & Site Management",
            "Run Ollama/API models locally, manage orgs/models, or verify CI.",
            fill="both",
            expand=True,
        )
        r_loc = tk.Frame(c_local, bg=PALETTE["card"])
        r_loc.pack(fill="x")
        ttk.Button(
            r_loc,
            text="Local Eval ->",
            style="Ghost.TButton",
            command=lambda: self.select_page("run"),
        ).pack(side="left", padx=(0, 6))
        ttk.Button(
            r_loc,
            text="Manage Models ->",
            style="Ghost.TButton",
            command=lambda: self.select_page("management"),
        ).pack(side="left", padx=(0, 6))
        ttk.Button(
            r_loc,
            text="Sync & Verify Site",
            style="Ghost.TButton",
            command=self._sync_and_verify_site,
        ).pack(side="left")

        # Explorer Content (below workflows): 15 Core Pillars + Registered & Published Models
        exp_cols = ttk.Frame(parent)
        exp_cols.pack(fill="both", expand=True)
        exp_cols.columnconfigure(0, weight=1, uniform="ov_exp")
        exp_cols.columnconfigure(1, weight=1, uniform="ov_exp")
        exp_left = ttk.Frame(exp_cols)
        exp_left.grid(row=0, column=0, sticky="nsew", padx=(0, self._px(10)))
        exp_right = ttk.Frame(exp_cols)
        exp_right.grid(row=0, column=1, sticky="nsew")

        p_card = self._step_card(exp_left, "PILLARS", "The 15 Core Pillars", "", fill="both", expand=True)
        pbar = tk.Frame(p_card, bg=PALETTE["card"])
        pbar.pack(fill="x", pady=(0, self._px(6)))
        tk.Label(pbar, text="Filter:", bg=PALETTE["card"], fg=PALETTE["muted"], font=("Segoe UI", 9)).pack(side="left")
        self.pillar_search_var = tk.StringVar()
        self.pillar_search_var.trace_add("write", lambda *_: self._populate_pillars_table())
        p_ent = ttk.Entry(pbar, textvariable=self.pillar_search_var, width=20)
        p_ent.pack(side="left", padx=(8, 8), fill="x", expand=True)
        p_ent.bind("<Escape>", lambda _e: self.pillar_search_var.set(""))
        ttk.Button(
            pbar,
            text="Inspect Pillar",
            style="Ghost.TButton",
            command=self._open_selected_pillar_inspector,
        ).pack(side="right")

        self.pillars_tree = self._make_scrolled_tree(
            p_card,
            columns=("slug", "text", "vision", "audio"),
            height=9,
            fill="both",
            expand=True,
        )
        for col, lbl, w in (
            ("slug", "Pillar Slug", 220),
            ("text", "Text", 65),
            ("vision", "Vision", 65),
            ("audio", "Audio", 65),
        ):
            self.pillars_tree.heading(col, text=lbl)
            self.pillars_tree.column(col, width=self._px(w), anchor="w" if col == "slug" else "center")
        self.pillars_tree.bind("<Double-1>", lambda _e: self._open_selected_pillar_inspector())
        self.pillars_tree.bind("<Return>", lambda _e: self._open_selected_pillar_inspector())

        m_card = self._step_card(exp_right, "REGISTRY", "Registered & Published Models", "", fill="both", expand=True)
        fbar = tk.Frame(m_card, bg=PALETTE["card"])
        fbar.pack(fill="x", pady=(0, self._px(6)))
        tk.Label(fbar, text="Filter:", bg=PALETTE["card"], fg=PALETTE["muted"], font=("Segoe UI", 9)).pack(side="left")
        self.model_search_var = tk.StringVar()
        self.model_search_var.trace_add("write", lambda *_: self._populate_models_table())
        self.model_search_entry = ttk.Entry(fbar, textvariable=self.model_search_var, width=20)
        self.model_search_entry.pack(side="left", padx=(8, 8), fill="x", expand=True)
        self.model_search_entry.bind("<Escape>", lambda _e: self.model_search_var.set(""))
        ttk.Button(
            fbar,
            text="Inspect / Compare",
            style="Ghost.TButton",
            command=self._open_scorecard_inspector_from_overview,
        ).pack(side="right")

        self.models_tree = self._make_scrolled_tree(
            m_card,
            columns=("model", "provider", "modalities"),
            height=9,
            show="tree headings",
            fill="both",
            expand=True,
        )
        self.models_tree.heading("#0", text="")
        self.models_tree.column("#0", width=self._px(30), stretch=False, anchor="center")
        for col, lbl, w in (
            ("model", "Canonical Slug", 200),
            ("provider", "Provider", 105),
            ("modalities", "T / V / A", 85),
        ):
            self.models_tree.heading(col, text=lbl)
            self.models_tree.column(col, width=self._px(w), anchor="w" if col != "modalities" else "center")
        self.models_tree.bind("<Double-1>", lambda _e: self._open_scorecard_inspector_from_overview())
        self.models_tree.bind("<Return>", lambda _e: self._open_scorecard_inspector_from_overview())
        self._bind_tree_context_menu(
            self.models_tree,
            lambda vals: [
                ("Inspect Scorecard / Compare...", lambda: open_scorecard_inspector(self, vals[0])),
                ("Copy Model Slug", lambda: self._copy_text_to_clipboard(vals[0], "slug")),
                None,
                ("Target in Kaggle Sync (-m)", lambda: (self.kg_model_var.set(vals[0]), self.select_page("kaggle"))),
                ("Manage Models & Orgs ->", lambda: (self.mgmt_model_search_var.set(vals[0]), self.select_page("management"))),
            ],
        )

    # ------------------------------------------------------------------ 2. Kaggle Sync

    def _build_kaggle_page(self, parent: Any) -> None:
        tk, ttk = self.tk, self.ttk
        self._page_header(
            parent,
            "Kaggle Benchmarks Workflow",
            "Follow Steps 1 to 4 in order. Hover nothing — every action shows its exact CLI command in the preview bar.",
        )

        self.kg_task_var = tk.StringVar(value="all")
        self.kg_model_var = tk.StringVar(value="")
        self.kg_mod_label_var = tk.StringVar(value="All 17 tasks (T+V+A)")
        self.kg_force_var = tk.BooleanVar(value=False)
        self.kg_dry_var = tk.BooleanVar(value=False)
        self.kg_auto_build_var = tk.BooleanVar(value=True)
        self.kg_cov_filter_var = tk.StringVar(value="All")
        self.kg_cov_search_var = tk.StringVar(value="")

        # Split Kaggle Sync into 2 columns so Steps 1-4 stay visible without scrolling
        cols = ttk.Frame(parent)
        cols.pack(fill="both", expand=True)
        cols.columnconfigure(0, weight=3)
        cols.columnconfigure(1, weight=2)

        left_col = ttk.Frame(cols)
        left_col.grid(row=0, column=0, sticky="nsew", padx=(0, self._px(12)))
        right_col = ttk.Frame(cols)
        right_col.grid(row=0, column=1, sticky="nsew")

        # STEP 1: Discover (compact)
        s1 = self._step_card(
            left_col,
            "STEP 1",
            "Check Kaggle Server for New or Missing Models",
            "Check which models on Kaggle are missing locally or newly added on Kaggle.",
        )
        r1 = tk.Frame(s1, bg=PALETTE["card"])
        r1.pack(fill="x", pady=(0, self._px(6)))
        ttk.Button(
            r1,
            text="One-Click Sync",
            style="Accent.TButton",
            command=self._start_one_click_kaggle_sync,
        ).pack(side="left", padx=(0, 6))
        ttk.Button(
            r1,
            text="List New Models",
            style="Ghost.TButton",
            command=self._discover_kaggle_models,
        ).pack(side="left", padx=(0, 6))
        ttk.Button(
            r1,
            text="Check Missing Runs",
            style="Ghost.TButton",
            command=lambda: self.run_cli(build_kaggle_args("coverage", model=self.kg_model_var.get())),
        ).pack(side="left", padx=(0, 6))
        ttk.Button(
            r1,
            text="Task Status",
            style="Ghost.TButton",
            command=lambda: self.run_cli(build_kaggle_args("status", task=self.kg_task_var.get(), model=self.kg_model_var.get())),
        ).pack(side="left", padx=(0, 6))
        self.kg_token_btn = ttk.Button(
            r1,
            text="Token",
            style="Ghost.TButton",
            command=self._open_tokens_dialog,
        )
        self._sync_token_button()

        self.kg_discover_status_var = tk.StringVar(
            value="Click 'List New Models' to scan Kaggle. Click any table row on the right to select a model."
        )
        tk.Label(
            s1,
            textvariable=self.kg_discover_status_var,
            bg=PALETTE["card"],
            fg=PALETTE["info"],
            font=("Segoe UI", 9),
            anchor="w",
        ).pack(fill="x")

        # RIGHT COLUMN: New Models card (shown when discovered) + Local Slim Coverage card
        self.kg_new_frame = tk.Frame(
            right_col,
            bg=PALETTE["card"],
            highlightthickness=1,
            highlightbackground=PALETTE["ok"],
            padx=self._px(12),
            pady=self._px(10),
        )
        new_hdr = tk.Frame(self.kg_new_frame, bg=PALETTE["card"])
        new_hdr.pack(fill="x", pady=(0, self._px(4)))
        tk.Label(
            new_hdr,
            text="New Models on Kaggle (Ctrl/Shift-click for multi):",
            bg=PALETTE["card"],
            fg=PALETTE["ok"],
            font=("Segoe UI", 9, "bold"),
        ).pack(side="left")
        ttk.Button(
            new_hdr,
            text="Select All",
            style="Ghost.TButton",
            command=self._select_all_new_kaggle_models,
        ).pack(side="right")
        ttk.Button(
            new_hdr,
            text="Edit Names",
            style="Ghost.TButton",
            command=self._open_edit_models_dialog,
        ).pack(side="right", padx=(0, 6))
        self.kg_new_tree = self._make_scrolled_tree(
            self.kg_new_frame,
            columns=("slug", "canonical", "status"),
            height=4,
            selectmode="extended",
        )
        for col, lbl, w in (
            ("slug", "Kaggle Slug", 150),
            ("canonical", "Canonical", 140),
            ("status", "Status", 90),
        ):
            self.kg_new_tree.heading(col, text=lbl)
            self.kg_new_tree.column(col, width=self._px(w), anchor="w" if col != "status" else "center")
        self._bind_toggle_tree(self.kg_new_tree)
        self.kg_new_tree.bind("<<TreeviewSelect>>", self._on_select_new_kaggle_model)
        self.kg_new_tree.bind("<Double-1>", lambda _e: self._open_edit_models_dialog())
        self.kg_new_tree.bind("<Return>", lambda _e: self._open_edit_models_dialog())
        self.kg_new_tree.bind("<Escape>", lambda _e: self._clear_selected_kaggle_models())

        self.kg_cov_card = tk.Frame(
            right_col,
            bg=PALETTE["card"],
            highlightthickness=1,
            highlightbackground=PALETTE["border"],
            padx=self._px(12),
            pady=self._px(10),
        )
        self.kg_cov_card.pack(fill="both", expand=True)
        self.kg_cov_bar = tk.Frame(self.kg_cov_card, bg=PALETTE["card"])
        self.kg_cov_bar.pack(fill="x", pady=(0, self._px(6)))
        tk.Label(
            self.kg_cov_bar,
            text="Local Slim Coverage",
            bg=PALETTE["card"],
            fg=PALETTE["text"],
            font=("Segoe UI", 9, "bold"),
        ).pack(side="left")
        cov_filt = ttk.Combobox(
            self.kg_cov_bar,
            textvariable=self.kg_cov_filter_var,
            values=["All", "Incomplete only", "Complete 15/15 T+V+A"],
            state="readonly",
            width=18,
        )
        cov_filt.pack(side="right")
        cov_filt.bind("<<ComboboxSelected>>", lambda _e: self._populate_kaggle_table())
        self.kg_cov_search_entry = ttk.Entry(self.kg_cov_bar, textvariable=self.kg_cov_search_var, width=14)
        self.kg_cov_search_entry.pack(side="right", padx=(6, 6))
        self.kg_cov_search_entry.bind("<Escape>", lambda _e: self.kg_cov_search_var.set(""))
        self.kg_cov_search_var.trace_add("write", lambda *_: self._populate_kaggle_table())

        self.kg_tree = self._make_scrolled_tree(
            self.kg_cov_card,
            columns=("model", "text", "vision", "audio"),
            height=14,
            fill="both",
            expand=True,
        )
        for col, lbl, w in (
            ("model", "Model Slug", 165),
            ("text", "Text", 60),
            ("vision", "Vision", 65),
            ("audio", "Audio", 65),
        ):
            self.kg_tree.heading(col, text=lbl)
            self.kg_tree.column(col, width=self._px(w), anchor="w" if col == "model" else "center")
        self._bind_toggle_tree(self.kg_tree)
        self.kg_tree.bind("<<TreeviewSelect>>", self._on_select_cov_model)
        self._bind_tree_context_menu(
            self.kg_tree,
            lambda vals: [
                ("Inspect Scorecard / Compare...", lambda: open_scorecard_inspector(self, vals[0])),
                ("Copy Model Slug", lambda: self._copy_text_to_clipboard(vals[0], "slug")),
                ("Edit in Registry...", lambda: self._open_edit_models_dialog(target_slugs=[vals[0]])),
            ],
        )

        # STEP 2: Pull Results (in left_col — always visible!)
        s2 = self._step_card(
            left_col,
            "STEP 2",
            "Download Finished Kaggle Results via Google Colab (*.slim.csv)",
            "Runs kaggle pull on Colab, keeps any succeeded tasks, merges scores locally, and auto-cleans .temp/.",
        )
        g2 = tk.Frame(s2, bg=PALETTE["card"])
        g2.pack(fill="x", pady=(0, self._px(6)))
        g2.columnconfigure(1, weight=1)
        g2.columnconfigure(3, weight=1)

        task_choices = ["all", "synhalees-vision", "synhalees-audio", *expand_import_tasks("text")]
        ttk.Label(g2, text="Scope / Task", style="CardText.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=4)
        ttk.Combobox(g2, textvariable=self.kg_task_var, values=task_choices).grid(row=0, column=1, sticky="ew", padx=(0, 14), pady=4)

        ttk.Label(g2, text="Modality Preset", style="CardText.TLabel").grid(row=0, column=2, sticky="w", padx=(0, 8), pady=4)
        ttk.Combobox(
            g2,
            textvariable=self.kg_mod_label_var,
            values=list(_MODALITY_FLAG_MAP.keys()),
            state="readonly",
        ).grid(row=0, column=3, sticky="ew", pady=4)

        ttk.Label(g2, text="Target Model (-m)", style="CardText.TLabel").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=4)
        ttk.Entry(g2, textvariable=self.kg_model_var).grid(row=1, column=1, columnspan=2, sticky="ew", padx=(0, 8), pady=4)
        ttk.Button(
            g2,
            text="Clear Selection",
            style="Ghost.TButton",
            command=self._clear_selected_kaggle_models,
        ).grid(row=1, column=3, sticky="e", pady=4)

        f2 = tk.Frame(g2, bg=PALETTE["card"])
        f2.grid(row=2, column=0, columnspan=4, sticky="ew", pady=(4, 0))
        ttk.Checkbutton(f2, text="Dry run (preview)", variable=self.kg_dry_var).pack(side="left", padx=(0, 12))
        ttk.Checkbutton(f2, text="Force re-download", variable=self.kg_force_var).pack(side="left", padx=(0, 12))
        ttk.Checkbutton(f2, text="Auto-Submit, Rebuild & Check after Pull", variable=self.kg_auto_build_var).pack(side="left")

        self.kg_preview_var = tk.StringVar()
        for var in (self.kg_task_var, self.kg_model_var, self.kg_mod_label_var, self.kg_force_var, self.kg_dry_var):
            var.trace_add("write", lambda *_: self._update_kg_preview())
        self._update_kg_preview()
        kg_prev = tk.Frame(s2, bg=PALETTE["console_bg"], padx=self._px(10), pady=self._px(6))
        kg_prev.pack(fill="x", pady=(0, self._px(8)))
        tk.Label(
            kg_prev,
            textvariable=self.kg_preview_var,
            bg=PALETTE["console_bg"],
            fg=PALETTE["info"],
            font=("Consolas", 8),
            anchor="w",
        ).pack(fill="x")

        r2 = tk.Frame(s2, bg=PALETTE["card"])
        r2.pack(fill="x")
        ttk.Button(
            r2,
            text="Pull via Google Colab (Slim Only)",
            style="Accent.TButton",
            command=self._start_kaggle_pull,
        ).pack(side="left", padx=(0, 8))
        self.kg_retry_btn = ttk.Button(
            r2,
            text="Retry Failed Tasks",
            style="Ghost.TButton",
            command=self._retry_failed_kaggle_tasks,
        )

        # STEP 3: Submit & Rebuild Site
        s3 = self._step_card(
            left_col,
            "STEP 3",
            "Import Slim CSVs into submissions/ & Rebuild Leaderboard Site",
            "Writes canonical scorecards to submissions/<vendor>/<family>/<model>.csv and regenerates docs/assets/data/*.",
        )
        r3 = tk.Frame(s3, bg=PALETTE["card"])
        r3.pack(fill="x")
        ttk.Button(
            r3,
            text="Import & Rebuild Site (submit --build)",
            style="Accent.TButton",
            command=lambda: self._guard_and_run_kaggle(
                "submit",
                build_kaggle_args(
                    "submit",
                    task=self.kg_task_var.get(),
                    dry_run=self.kg_dry_var.get(),
                    build=True,
                ),
            ),
        ).pack(side="left", padx=(0, 8))
        ttk.Button(
            r3,
            text="Verify All Gates (check)",
            style="Ghost.TButton",
            command=lambda: self.run_cli(["check"]),
        ).pack(side="left", padx=(0, 8))
        ttk.Button(
            r3,
            text="Open Leaderboard Preview",
            style="Ghost.TButton",
            command=self._open_leaderboard,
        ).pack(side="left")

        # STEP 4: Advanced / Trigger New Server Runs
        s4 = self._step_card(
            left_col,
            "STEP 4 · OPTIONAL",
            "Push Task Scripts or Start New Evaluations on Kaggle Servers",
            "Use this only when updating benchmark tasks or triggering a new `-m <model>` evaluation directly on Kaggle.",
        )
        r4 = tk.Frame(s4, bg=PALETTE["card"])
        r4.pack(fill="x")
        for label, act in (
            ("1. Gen Tasks", "gen"),
            ("2. Push Tasks", "push"),
            ("3. Run Model", "run"),
            ("Fetch Logs", "logs"),
            ("Publish on Kaggle.com", "publish"),
        ):
            ttk.Button(
                r4,
                text=label,
                style="Ghost.TButton",
                command=lambda a=act: self._guard_and_run_kaggle(
                    a,
                    build_kaggle_args(
                        a,
                        task=self.kg_task_var.get(),
                        model=self.kg_model_var.get(),
                        dry_run=self.kg_dry_var.get(),
                    ),
                ),
            ).pack(side="left", padx=(0, 8))

    # ------------------------------------------------------------------ 3. Local Eval

    def _build_run_page(self, parent: Any) -> None:
        tk, ttk = self.tk, self.ttk
        self._page_header(
            parent,
            "Local Evaluation Workflow (API / Ollama)",
            "Run crash-safe evaluations locally into runs/<model-slug>/, compare across runs, and promote finished runs.",
        )

        saved_provider = os.environ.get("SYNHALEES_PROVIDER", "colab").strip().lower() or "colab"
        api_key_env = _API_KEY_ENV_BY_PROVIDER.get(saved_provider, "SYNHALEES_API_KEY")
        self.run_provider_var = tk.StringVar(value=saved_provider)
        self.run_model_var = tk.StringVar(value="")
        self.run_judge_var = tk.StringVar(value="")
        self.run_judge_provider_var = tk.StringVar(value="")
        self.run_modality_var = tk.StringVar(value="text")
        self.run_pillars_var = tk.StringVar(value="all")
        self.run_fresh_var = tk.BooleanVar(value=False)
        self.run_auto_publish_var = tk.BooleanVar(value=False)
        self.run_base_url_var = tk.StringVar(value=os.environ.get("SYNHALEES_BASE_URL", ""))
        self.run_api_key_var = tk.StringVar(value=os.environ.get(api_key_env, ""))
        self.run_judge_status_var = tk.StringVar(value="")
        self.run_judge_count_var = tk.StringVar(value="")
        self.run_reasoning_var = tk.StringVar(value="high")
        self.run_reasoning_status_var = tk.StringVar(
            value="Reasoning effort: High (default)."
        )
        self._run_connection_ready = False
        self._run_model_specs: list[str] = []
        self._run_reasoning_effort: str | None = None
        self._run_reasoning_model = ""
        self._run_reasoning_probe_generation = 0
        self._reasoning_test_running = False

        s1 = self._step_card(
            parent,
            "STEP 1",
            "Connect to Model Server",
            "Choose a provider prefix, enter its Base URL/API key if needed, then test the connection. "
            "The model selector is populated only from advertised models; connection settings are saved locally when tested.",
        )
        form = tk.Frame(s1, bg=PALETTE["card"])
        form.pack(fill="x", pady=(0, self._px(10)))
        form.columnconfigure(1, weight=1)
        ttk.Label(form, text="Provider prefix", style="CardText.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=5)
        self.run_provider_entry = ttk.Combobox(
            form,
            textvariable=self.run_provider_var,
            values=["colab", "openai", "openrouter", "ollama", "gemini", "anthropic"],
        )
        self.run_provider_entry.grid(row=0, column=1, sticky="ew", pady=5)
        self.run_provider_entry.bind("<<ComboboxSelected>>", self._on_run_provider_selected)

        ttk.Label(form, text="Base URL (opt)", style="CardText.TLabel").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=5)
        self.run_base_url_entry = ttk.Entry(form, textvariable=self.run_base_url_var)
        self.run_base_url_entry.grid(row=1, column=1, sticky="ew", pady=5)

        ttk.Label(form, text="API key (opt)", style="CardText.TLabel").grid(row=2, column=0, sticky="w", padx=(0, 8), pady=5)
        self.run_api_key_entry = ttk.Entry(form, textvariable=self.run_api_key_var, show="*")
        self.run_api_key_entry.grid(row=2, column=1, sticky="ew", pady=5)
        self._run_connection_inputs = (
            self.run_provider_entry,
            self.run_base_url_entry,
            self.run_api_key_entry,
        )

        connection_row = tk.Frame(s1, bg=PALETTE["card"])
        connection_row.pack(fill="x")
        self.run_probe_btn = ttk.Button(
            connection_row, text="Test Connection", style="Accent.TButton", command=self._test_local_connection
        )
        self.run_probe_btn.pack(side="left")
        self.run_probe_var = tk.StringVar(value="")
        self.run_probe_label = tk.Label(
            connection_row,
            textvariable=self.run_probe_var,
            bg=PALETTE["card"],
            fg=PALETTE["muted"],
            font=("Segoe UI", 9),
            anchor="w",
        )
        self.run_probe_label.pack(side="left", padx=(10, 0))

        # STEP 2: Select only a model ID reported by the tested endpoint.
        s2 = self._step_card(
            parent,
            "STEP 2",
            "Select Model & Configure Benchmark Run",
            "Models are loaded after a successful connection test. Results checkpoint per item; "
            "judge providers require an API key saved in Settings. Enable 'Fresh run' to discard a prior checkpoint.",
        )
        form = tk.Frame(s2, bg=PALETTE["card"])
        form.pack(fill="x", pady=(0, self._px(10)))
        form.columnconfigure(1, weight=1)
        form.columnconfigure(3, weight=1)

        ttk.Label(form, text="Model spec", style="CardText.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=5)
        self.run_model_combo = ttk.Combobox(
            form, textvariable=self.run_model_var, values=(), state="disabled"
        )
        self.run_model_combo.grid(row=0, column=1, sticky="ew", padx=(0, 14), pady=5)

        ttk.Label(form, text="Judge provider", style="CardText.TLabel").grid(row=0, column=2, sticky="w", padx=(0, 8), pady=5)
        self.run_judge_provider_combo = ttk.Combobox(
            form,
            textvariable=self.run_judge_provider_var,
            values=(),
            state="disabled",
        )
        self.run_judge_provider_combo.grid(row=0, column=3, sticky="ew", pady=5)
        self.run_judge_provider_combo.bind(
            "<<ComboboxSelected>>", self._on_judge_provider_selected
        )

        ttk.Label(form, text="Modality", style="CardText.TLabel").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=5)
        ttk.Combobox(
            form,
            textvariable=self.run_modality_var,
            values=["text", "vision", "audio", "all"],
            state="readonly",
        ).grid(row=1, column=1, sticky="ew", padx=(0, 14), pady=5)

        ttk.Label(form, text="Pillars", style="CardText.TLabel").grid(row=1, column=2, sticky="w", padx=(0, 8), pady=5)
        ttk.Combobox(form, textvariable=self.run_pillars_var, values=["all", *list_pillars()]).grid(row=1, column=3, sticky="ew", pady=5)

        ttk.Label(form, text="Reasoning / Thinking", style="CardText.TLabel").grid(row=2, column=0, sticky="w", padx=(0, 8), pady=5)
        self.run_reasoning_combo = ttk.Combobox(
            form,
            textvariable=self.run_reasoning_var,
            values=["high", "medium", "low", "off"],
            state="readonly",
        )
        self.run_reasoning_combo.grid(row=2, column=1, sticky="ew", padx=(0, 14), pady=5)

        ttk.Label(form, text="Judge model (opt)", style="CardText.TLabel").grid(row=2, column=2, sticky="w", padx=(0, 8), pady=5)
        self.run_judge_model_combo = ttk.Combobox(
            form,
            textvariable=self.run_judge_var,
            values=(),
            state="disabled",
        )
        self.run_judge_model_combo.grid(row=2, column=3, sticky="ew", pady=5)
        self.run_judge_model_combo.bind(
            "<<ComboboxSelected>>", self._on_judge_model_selected
        )

        ttk.Label(
            form,
            textvariable=self.run_judge_status_var,
            style="Muted.TLabel",
            wraplength=self._px(550),
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=4)
        judge_btn_box = tk.Frame(form, bg=PALETTE["card"])
        judge_btn_box.grid(row=3, column=2, columnspan=2, sticky="e", pady=4)
        self.run_test_judge_btn = ttk.Button(
            judge_btn_box,
            text="Test Judge",
            style="Ghost.TButton",
            command=self._test_judge_model,
        )
        self.run_test_judge_btn.pack(side="right", padx=(6, 0))
        ttk.Button(
            judge_btn_box,
            text="API Key Settings",
            style="Ghost.TButton",
            command=self._open_tokens_dialog,
        ).pack(side="right")

        ttk.Label(
            form,
            textvariable=self.run_judge_count_var,
            style="Muted.TLabel",
            wraplength=self._px(900),
        ).grid(row=4, column=0, columnspan=4, sticky="w", pady=4)

        chk_row = tk.Frame(form, bg=PALETTE["card"])
        chk_row.grid(row=5, column=0, columnspan=4, sticky="w", pady=(4, 0))
        ttk.Checkbutton(chk_row, text="Fresh run (discard prior checkpoint)", variable=self.run_fresh_var).pack(
            side="left", padx=(0, 16)
        )
        ttk.Checkbutton(
            chk_row,
            text="Auto-publish & rebuild site on finish (0 errors)",
            variable=self.run_auto_publish_var,
        ).pack(side="left")

        self.run_preview_var = tk.StringVar()
        for var in (
            self.run_model_var,
            self.run_judge_var,
            self.run_modality_var,
            self.run_pillars_var,
            self.run_fresh_var,
            self.run_reasoning_var,
        ):
            var.trace_add("write", lambda *_: self._update_run_preview())
        self.run_model_var.trace_add("write", lambda *_: self._on_run_model_changed())
        for var in (self.run_provider_var, self.run_base_url_var, self.run_api_key_var):
            var.trace_add("write", lambda *_: self._on_run_connection_input_changed())
        for var in (self.run_modality_var, self.run_pillars_var):
            var.trace_add("write", lambda *_: self._update_judge_call_estimate())
        self._update_run_preview()
        self._update_judge_call_estimate()
        self._refresh_judge_providers()

        prev = tk.Frame(s2, bg=PALETTE["console_bg"], padx=self._px(10), pady=self._px(7))
        prev.pack(fill="x", pady=(0, self._px(10)))
        tk.Label(
            prev,
            textvariable=self.run_preview_var,
            bg=PALETTE["console_bg"],
            fg=PALETTE["info"],
            font=("Consolas", 9),
            anchor="w",
        ).pack(fill="x")

        r1 = tk.Frame(s2, bg=PALETTE["card"])
        r1.pack(fill="x")
        self.run_start_btn = ttk.Button(
            r1, text="Start Local Run", style="Accent.TButton", command=self._start_local_run, state="disabled"
        )
        self.run_start_btn.pack(side="left")
        self.run_reasoning_btn = ttk.Button(
            r1,
            text="Test Reasoning (High)",
            style="Ghost.TButton",
            command=self._test_run_reasoning,
            state="disabled",
        )
        self.run_reasoning_btn.pack(side="left", padx=(self._px(8), 0))
        ttk.Label(
            r1,
            textvariable=self.run_reasoning_status_var,
            style="Muted.TLabel",
            wraplength=self._px(500),
        ).pack(side="left", padx=(self._px(10), 0), fill="x", expand=True)

        # STEP 3: Local Runs Status & Compare
        s3 = self._step_card(
            parent,
            "STEP 3",
            "Local Runs Status & Comparison",
            "Inspect completed or partial checkpoints in runs/*/, resume an interrupted run, or build runs/all_submissions.csv.",
        )
        self.local_runs_tree = self._make_scrolled_tree(
            s3,
            columns=("slug", "items", "errors", "overall", "status"),
            height=5,
            fill="x",
        )
        for col, lbl, w in (
            ("slug", "Run Slug", 220),
            ("items", "Items", 80),
            ("errors", "Errors", 80),
            ("overall", "Score", 90),
            ("status", "Status", 140),
        ):
            self.local_runs_tree.heading(col, text=lbl)
            self.local_runs_tree.column(col, width=self._px(w), anchor="w" if col == "slug" else "center")
        self.local_runs_tree.bind("<<TreeviewSelect>>", self._on_select_local_run)
        s3_btns = tk.Frame(s3, bg=PALETTE["card"])
        s3_btns.pack(fill="x", pady=(self._px(8), 0))
        ttk.Button(s3_btns, text="Resume Selected Run", style="Ghost.TButton", command=self._resume_selected_local_run).pack(
            side="left", padx=(0, 8)
        )
        ttk.Button(s3_btns, text="Compare Local Runs (compare)", style="Ghost.TButton", command=lambda: self.run_cli(["compare"])).pack(
            side="left", padx=(0, 8)
        )
        ttk.Button(
            s3_btns,
            text="Inspect Run Scorecard",
            style="Ghost.TButton",
            command=lambda: open_scorecard_inspector(self, self.pub_slug_var.get().strip()),
        ).pack(side="left", padx=(0, 8))
        ttk.Button(
            s3_btns,
            text="Delete Run",
            style="Ghost.TButton",
            command=self._delete_selected_local_run,
        ).pack(side="left")

        # STEP 4: Publish
        s4 = self._step_card(
            parent,
            "STEP 4",
            "Promote Finished Run to submissions/ & Rebuild Site",
            "Copies runs/<slug>/submission.csv into submissions/<vendor>/<family>/<slug>.csv and rebuilds docs/assets/data/.",
        )
        prow = tk.Frame(s4, bg=PALETTE["card"])
        prow.pack(fill="x")
        self.pub_slug_var = tk.StringVar(value="")
        self.pub_combo = ttk.Combobox(prow, textvariable=self.pub_slug_var, values=[])
        self.pub_combo.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.pub_dry_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(prow, text="Dry run", variable=self.pub_dry_var).pack(side="left", padx=(0, 10))
        ttk.Button(
            prow,
            text="Inspect",
            style="Ghost.TButton",
            command=lambda: open_scorecard_inspector(self, self.pub_slug_var.get().strip()),
        ).pack(side="left", padx=(0, 8))
        ttk.Button(prow, text="Publish & Rebuild Site", style="Accent.TButton", command=self._publish_selected_run).pack(side="left")

    # ------------------------------------------------------------------ 4. Management

    def _build_management_page(self, parent: Any) -> None:
        tk, ttk = self.tk, self.ttk
        self._page_header(
            parent,
            "Model & Organization Management",
            "Side-by-side workspace: manage models, bulk-edit taxonomy, adopt scorecards, and configure org logos & families.",
        )

        cols = ttk.Frame(parent)
        cols.pack(fill="both", expand=True)
        cols.columnconfigure(0, weight=3)
        cols.columnconfigure(1, weight=2)
        left_m = ttk.Frame(cols)
        left_m.grid(row=0, column=0, sticky="nsew", padx=(0, self._px(10)))
        right_o = ttk.Frame(cols)
        right_o.grid(row=0, column=1, sticky="nsew")

        # Model Management Card (Left)
        m_card = self._step_card(
            left_m,
            "MODEL MANAGEMENT",
            "Registered Models",
            "Ctrl/Shift-click for bulk edits · Double-click to edit · Right-click for quick actions.",
            fill="both",
            expand=True,
        )
        m_bar = tk.Frame(m_card, bg=PALETTE["card"])
        m_bar.pack(fill="x", pady=(0, self._px(6)))
        tk.Label(m_bar, text="Filter:", bg=PALETTE["card"], fg=PALETTE["muted"], font=("Segoe UI", 9)).pack(side="left")
        self.mgmt_model_search_var = tk.StringVar()
        self.mgmt_model_search_var.trace_add("write", lambda *_: self._populate_mgmt_tables())
        self.mgmt_model_search_entry = ttk.Entry(m_bar, textvariable=self.mgmt_model_search_var, width=22)
        self.mgmt_model_search_entry.pack(side="left", padx=(8, 8), fill="x", expand=True)
        self.mgmt_model_search_entry.bind("<Escape>", lambda _e: self.mgmt_model_search_var.set(""))
        self.mgmt_model_count_var = tk.StringVar(value="0")
        tk.Label(m_bar, textvariable=self.mgmt_model_count_var, bg=PALETTE["card"], fg=PALETTE["info"], font=("Segoe UI", 8, "bold")).pack(
            side="right"
        )

        m_btns = tk.Frame(m_card, bg=PALETTE["card"])
        m_btns.pack(fill="x", pady=(0, self._px(8)))
        ttk.Button(
            m_btns,
            text="+ Add",
            style="Accent.TButton",
            command=lambda: self._open_mgmt_model_dialog(edit_selected=False),
        ).pack(side="left", padx=(0, 6))
        ttk.Button(
            m_btns,
            text="Edit",
            style="Ghost.TButton",
            command=lambda: self._open_mgmt_model_dialog(edit_selected=True),
        ).pack(side="left", padx=(0, 6))
        ttk.Button(
            m_btns,
            text="Bulk Edit…",
            style="Ghost.TButton",
            command=self._open_bulk_edit_models_dialog,
        ).pack(side="left", padx=(0, 6))
        ttk.Button(
            m_btns,
            text="Inspect",
            style="Ghost.TButton",
            command=self._open_scorecard_inspector_from_mgmt,
        ).pack(side="left", padx=(0, 6))
        ttk.Button(
            m_btns,
            text="Adopt & Sync",
            style="Ghost.TButton",
            command=self._run_adopt_and_sync_scorecards,
        ).pack(side="left", padx=(0, 6))
        ttk.Button(
            m_btns,
            text="Delete",
            style="Danger.TButton",
            command=self._delete_selected_mgmt_model,
        ).pack(side="right")

        self.mgmt_models_tree = self._make_scrolled_tree(
            m_card,
            columns=("canonical", "display", "org", "family", "oss", "tva"),
            height=15,
            selectmode="extended",
            show="tree headings",
            fill="both",
            expand=True,
        )
        self.mgmt_models_tree.heading("#0", text="")
        self.mgmt_models_tree.column("#0", width=self._px(28), stretch=False, anchor="center")
        for col, lbl, w in (
            ("canonical", "Canonical Slug", 160),
            ("display", "Display Name", 155),
            ("org", "Org", 80),
            ("family", "Family", 80),
            ("oss", "OSS", 55),
            ("tva", "TVA", 55),
        ):
            self.mgmt_models_tree.heading(col, text=lbl)
            self.mgmt_models_tree.column(col, width=self._px(w), anchor="w" if col not in ("oss", "tva") else "center")
        self._bind_toggle_tree(self.mgmt_models_tree)
        self.mgmt_models_tree.bind("<Double-1>", lambda _e: self._open_mgmt_model_dialog(edit_selected=True))
        self.mgmt_models_tree.bind("<Return>", lambda _e: self._open_mgmt_model_dialog(edit_selected=True))
        self.mgmt_models_tree.bind("<Delete>", lambda _e: self._delete_selected_mgmt_model())
        self.mgmt_models_tree.bind("<Control-c>", lambda _e: self._copy_tree_selection(self.mgmt_models_tree, 0, "slug"))
        self._bind_tree_context_menu(
            self.mgmt_models_tree,
            lambda vals: [
                ("Edit Model… (Enter)", lambda: self._open_mgmt_model_dialog(edit_selected=True)),
                ("Bulk Edit Selected…", self._open_bulk_edit_models_dialog),
                ("Inspect Scorecard / Compare…", lambda: open_scorecard_inspector(self, vals[0])),
                None,
                ("Copy Canonical Slug (Ctrl+C)", lambda: self._copy_text_to_clipboard(vals[0], "slug")),
                ("Filter by Organization", lambda: self.mgmt_model_search_var.set(vals[2])),
                ("Target in Kaggle Sync (-m)", lambda: (self.kg_model_var.set(vals[0]), self.select_page("kaggle"))),
                None,
                ("Delete Selected… (Del)", self._delete_selected_mgmt_model),
            ],
        )

        # Organization Management Card (Right)
        o_card = self._step_card(
            right_o,
            "ORGANIZATION MANAGEMENT",
            "Organizations & Families",
            "Logos (SVG/PNG), brand colors, aliases & family taxonomy.",
            fill="both",
            expand=True,
        )
        o_filt = tk.Frame(o_card, bg=PALETTE["card"])
        o_filt.pack(fill="x", pady=(0, self._px(6)))
        tk.Label(o_filt, text="Filter:", bg=PALETTE["card"], fg=PALETTE["muted"], font=("Segoe UI", 9)).pack(side="left")
        self.mgmt_org_search_var = tk.StringVar()
        self.mgmt_org_search_var.trace_add("write", lambda *_: self._populate_mgmt_tables())
        o_ent = ttk.Entry(o_filt, textvariable=self.mgmt_org_search_var, width=18)
        o_ent.pack(side="left", padx=(8, 8), fill="x", expand=True)
        o_ent.bind("<Escape>", lambda _e: self.mgmt_org_search_var.set(""))
        self.mgmt_org_count_var = tk.StringVar(value="0")
        tk.Label(o_filt, textvariable=self.mgmt_org_count_var, bg=PALETTE["card"], fg=PALETTE["info"], font=("Segoe UI", 8, "bold")).pack(
            side="right"
        )

        o_bar = tk.Frame(o_card, bg=PALETTE["card"])
        o_bar.pack(fill="x", pady=(0, self._px(8)))
        ttk.Button(
            o_bar,
            text="+ Add Org",
            style="Accent.TButton",
            command=lambda: self._open_mgmt_org_dialog(edit_selected=False),
        ).pack(side="left", padx=(0, 6))
        ttk.Button(
            o_bar,
            text="Edit / Families",
            style="Ghost.TButton",
            command=lambda: self._open_mgmt_org_dialog(edit_selected=True),
        ).pack(side="left", padx=(0, 6))
        ttk.Button(
            o_bar,
            text="Delete",
            style="Danger.TButton",
            command=self._delete_selected_mgmt_org,
        ).pack(side="right")

        self.mgmt_orgs_tree = self._make_scrolled_tree(
            o_card,
            columns=("slug", "name", "logo", "color", "families", "aliases"),
            height=15,
            selectmode="browse",
            show="tree headings",
            fill="both",
            expand=True,
        )
        self.mgmt_orgs_tree.heading("#0", text="Badge")
        self.mgmt_orgs_tree.column("#0", width=self._px(54), stretch=False, anchor="center")
        for col, lbl, w in (
            ("slug", "Slug", 85),
            ("name", "Name", 105),
            ("logo", "Logo", 80),
            ("color", "Color", 80),
            ("families", "Families", 110),
            ("aliases", "Aliases", 110),
        ):
            self.mgmt_orgs_tree.heading(col, text=lbl)
            self.mgmt_orgs_tree.column(col, width=self._px(w), anchor="w")
        self.mgmt_orgs_tree.bind("<Double-1>", lambda _e: self._open_mgmt_org_dialog(edit_selected=True))
        self.mgmt_orgs_tree.bind("<Return>", lambda _e: self._open_mgmt_org_dialog(edit_selected=True))
        self.mgmt_orgs_tree.bind("<Delete>", lambda _e: self._delete_selected_mgmt_org())
        self.mgmt_orgs_tree.bind("<Control-c>", lambda _e: self._copy_tree_selection(self.mgmt_orgs_tree, 0, "org"))
        self._bind_tree_context_menu(
            self.mgmt_orgs_tree,
            lambda vals: [
                ("Edit Org / Families… (Enter)", lambda: self._open_mgmt_org_dialog(edit_selected=True)),
                ("Filter Models by This Org", lambda: self.mgmt_model_search_var.set(vals[0])),
                ("Copy Org Slug (Ctrl+C)", lambda: self._copy_text_to_clipboard(vals[0], "org")),
                None,
                ("Delete Organization… (Del)", self._delete_selected_mgmt_org),
            ],
        )

    # ------------------------------------------------------------------ 5. Site & CI

    def _build_site_page(self, parent: Any) -> None:
        tk, ttk = self.tk, self.ttk
        self._page_header(
            parent,
            "Static Site & Verification Gates",
            "Regenerate docs/assets/logo-data.js & docs/assets/data/* and verify all 4 CI gates before committing.",
        )

        gates_row = ttk.Frame(parent)
        gates_row.pack(fill="x", pady=(0, self._px(12)))
        for col in range(4):
            gates_row.columnconfigure(col, weight=1, uniform="ci_gates")

        self.site_gate_cards: dict[str, tuple[Any, Any, Any]] = {}
        for idx, (gkey, gtitle) in enumerate((
            ("logos", "1. LOGO-DATA.JS"),
            ("data", "2. LEADERBOARD DATA"),
            ("schemas", "3. 15 PILLAR SCHEMAS"),
            ("compile", "4. PYTHON COMPILE"),
        )):
            card = tk.Frame(
                gates_row,
                bg=PALETTE["card"],
                highlightthickness=1,
                highlightbackground=PALETTE["border"],
                padx=self._px(12),
                pady=self._px(10),
            )
            card.grid(row=0, column=idx, sticky="nsew", padx=(0 if idx == 0 else self._px(8), 0))
            ttk.Label(card, text=gtitle, style="CardTitle.TLabel").pack(anchor="w")
            v_state = tk.StringVar(value="—")
            v_detail = tk.StringVar(value="")
            lbl = tk.Label(
                card,
                textvariable=v_state,
                bg=PALETTE["card"],
                fg=PALETTE["text"],
                font=("Segoe UI", 12, "bold"),
                anchor="w",
            )
            lbl.pack(anchor="w", pady=(4, 2))
            ttk.Label(card, textvariable=v_detail, style="CardSub.TLabel").pack(anchor="w")
            self.site_gate_cards[gkey] = (v_state, v_detail, lbl)

        self.site_sync_detail_var = tk.StringVar(value="")
        c1 = self._step_card(
            parent,
            "STEP 1 · PIPELINE",
            "Regenerate & Verify All Static Site Gates",
            "Chains `site logos` -> `site build` -> `synhalees check` in order so all generated assets stay synced.",
        )
        tk.Label(
            c1,
            textvariable=self.site_sync_detail_var,
            bg=PALETTE["card"],
            fg=PALETTE["info"],
            font=("Segoe UI", 9, "bold"),
        ).pack(anchor="w", pady=(0, self._px(8)))
        r1 = tk.Frame(c1, bg=PALETTE["card"])
        r1.pack(fill="x")
        ttk.Button(
            r1,
            text="Sync & Verify All (Logos -> Build -> Check)",
            style="Accent.TButton",
            command=self._sync_and_verify_site,
        ).pack(side="left", padx=(0, 8))
        ttk.Button(r1, text="Run Checks Only (check)", style="Ghost.TButton", command=lambda: self.run_cli(["check"])).pack(
            side="left", padx=(0, 8)
        )
        ttk.Button(r1, text="Preview docs/index.html", style="Ghost.TButton", command=self._open_leaderboard).pack(side="left")

        c2 = self._step_card(
            parent,
            "STEP 2 · INDIVIDUAL",
            "Granular Site Rebuild Actions",
            "Never hand-edit docs/assets/data/* or docs/assets/logo-data.js — regenerate individual artifacts here.",
        )
        r2 = tk.Frame(c2, bg=PALETTE["card"])
        r2.pack(fill="x")
        ttk.Button(r2, text="Rebuild Leaderboard Data (site build)", style="Ghost.TButton", command=lambda: self.run_cli(["site", "build"])).pack(
            side="left", padx=(0, 8)
        )
        ttk.Button(r2, text="Rebuild Logo Data (site logos)", style="Ghost.TButton", command=lambda: self.run_cli(["site", "logos"])).pack(
            side="left"
        )

    # ------------------------------------------------------------------ Data refresh

    def refresh_all(self) -> None:
        snap = collect_dashboard_snapshot()
        self.card_vars["dataset"][0].set(f"{snap.pillar_count} pillars")
        self.card_vars["dataset"][1].set(f"T:{snap.text_items}  V:{snap.vision_items}  A:{snap.audio_items}")
        self.card_vars["submissions"][0].set(f"{snap.submission_models} models")
        self.card_vars["submissions"][1].set(f"{snap.submission_vendors} vendors · {snap.latest_date}")
        sync_ok = snap.logos_ok and snap.site_data_ok
        self.card_vars["site"][0].set("Synced" if sync_ok else "Needs Rebuild")
        self.card_vars["site"][2].configure(fg=PALETTE["ok"] if sync_ok else PALETTE["warn"])
        self.card_vars["site"][1].set(
            f"logos:{'OK' if snap.logos_ok else 'STALE'}  data:{'OK' if snap.site_data_ok else 'STALE'}"
        )
        self.card_vars["kaggle"][0].set(f"{snap.kaggle_models} slim models")
        self.card_vars["kaggle"][1].set(f"{snap.kaggle_full_tva}/{snap.kaggle_models} full T+V+A")
        self.site_sync_detail_var.set(
            f"Current Sync: logo-data.js={'OK' if snap.logos_ok else 'STALE'}  |  docs/assets/data={'OK' if snap.site_data_ok else 'STALE'}"
        )
        self._update_site_gate_cards(snap)

        self.pub_combo["values"] = snap.local_runs
        if snap.local_runs and (not self.pub_slug_var.get() or self.pub_slug_var.get() not in snap.local_runs):
            self.pub_slug_var.set(snap.local_runs[0])

        self._populate_pillars_table()
        self._populate_models_table()
        self._populate_mgmt_tables()
        self._populate_local_runs_table()
        self._populate_kaggle_table()
        self._sync_token_button()

    def _update_site_gate_cards(self, snap: DashboardSnapshot) -> None:
        if not hasattr(self, "site_gate_cards"):
            return
        updates = {
            "logos": ("PASS" if snap.logos_ok else "STALE", "checksum verified" if snap.logos_ok else "run site logos", PALETTE["ok"] if snap.logos_ok else PALETTE["warn"]),
            "data": ("PASS" if snap.site_data_ok else "STALE", f"{snap.submission_models} models" if snap.site_data_ok else "run site build", PALETTE["ok"] if snap.site_data_ok else PALETTE["warn"]),
            "schemas": ("READY", f"T:{snap.text_items} V:{snap.vision_items} A:{snap.audio_items}", PALETTE["info"]),
            "compile": ("READY", "synhalees + tools", PALETTE["info"]),
        }
        for key, (st, det, col) in updates.items():
            v_st, v_det, lbl = self.site_gate_cards[key]
            v_st.set(st)
            v_det.set(det)
            lbl.configure(fg=col)

    def _set_site_gate(self, key: str, state: str, detail: str, color: str) -> None:
        if not hasattr(self, "site_gate_cards") or key not in self.site_gate_cards:
            return
        v_st, v_det, lbl = self.site_gate_cards[key]
        v_st.set(state)
        if detail:
            v_det.set(detail)
        lbl.configure(fg=color)

    def _sync_token_button(self) -> None:
        if not hasattr(self, "kg_token_btn"):
            return
        if kaggle_auth_status() == "Not configured":
            if not self.kg_token_btn.winfo_ismapped():
                self.kg_token_btn.pack(side="left")
        else:
            self.kg_token_btn.pack_forget()

    def _populate_pillars_table(self) -> None:
        self.pillars_tree.delete(*self.pillars_tree.get_children())
        q = self.pillar_search_var.get().strip().lower() if hasattr(self, "pillar_search_var") else ""
        idx = 0
        for p in list_pillars():
            if q and q not in p.lower():
                continue
            pd = load_pillar(p, strict=False)
            stripe = "odd" if idx % 2 else "even"
            self.pillars_tree.insert("", "end", tags=(stripe,), values=(p, len(pd.text), len(pd.vision), len(pd.audio)))
            idx += 1
        self._ensure_tree_empty_state(self.pillars_tree, "No matching pillars")

    def _populate_models_table(self) -> None:
        from synhalees.cli import _scan_submissions

        self.models_tree.delete(*self.models_tree.get_children())
        subs = _scan_submissions()
        q = self.model_search_var.get().strip().lower() if hasattr(self, "model_search_var") else ""
        data = json.loads(MODELS_JSON.read_text(encoding="utf-8"))
        providers = dict(data.get("providers") or {})
        named_colors = dict(data.get("named_colors") or {})
        idx = 0
        for m in _all_model_slugs():
            prov = default_registry.resolve_taxonomy(m)[0]
            if q and q not in m.lower() and q not in prov.lower():
                continue
            mods = subs.get(m, {}).get("modalities", set())
            tva = "".join(k[0].upper() if k in mods else "-" for k in ("text", "vision", "audio"))
            p_meta = providers.get(prov, {})
            logo_slug = str(p_meta.get("logo") or prov or "org")
            cname = str(p_meta.get("color") or "gray")
            hex_color = str(named_colors.get(cname, cname if cname.startswith("#") else "#78909C"))
            icon = self._get_org_logo_only_icon(logo_slug, hex_color)
            stripe = "odd" if idx % 2 else "even"
            tags = (stripe, "warn") if prov in _UNKNOWN_ORG_TOKENS else (stripe,)
            self.models_tree.insert("", "end", text="", image=icon, tags=tags, values=(m, prov, tva))
            idx += 1
        self._ensure_tree_empty_state(self.models_tree, "No matching models — press Esc to clear")

    def _populate_local_runs_table(self) -> None:
        if not hasattr(self, "local_runs_tree"):
            return
        self.local_runs_tree.delete(*self.local_runs_tree.get_children())
        rows = collect_local_runs_summary()
        for idx, r in enumerate(rows):
            stripe = "odd" if idx % 2 else "even"
            tag = "ok" if r["has_csv"] else "warn"
            self.local_runs_tree.insert(
                "",
                "end",
                tags=(stripe, tag),
                values=(r["slug"], r["items"], r["errors"], r["overall"], r["status"]),
            )
        self._ensure_tree_empty_state(self.local_runs_tree, "No local runs found under runs/")

    def _populate_mgmt_tables(self) -> None:
        if not hasattr(self, "mgmt_models_tree"):
            return
        from synhalees.cli import _scan_submissions

        data = json.loads(MODELS_JSON.read_text(encoding="utf-8"))
        providers = dict(data.get("providers") or {})
        named_colors = dict(data.get("named_colors") or {})
        disp = dict(data.get("display_names") or {})
        subs = _scan_submissions()
        q = self.mgmt_model_search_var.get().strip().lower() if hasattr(self, "mgmt_model_search_var") else ""

        self._org_icons: dict[tuple[str, str], Any] = {}
        self._org_small_icons: dict[tuple[str, str], Any] = {}
        self.mgmt_models_tree.delete(*self.mgmt_models_tree.get_children())
        all_slugs = sorted({*_all_model_slugs(), *disp.keys()}, key=str.lower)
        shown_m = 0
        for m in all_slugs:
            dname = str(disp.get(m) or default_registry.get_model_display_name(m))
            prov, fam, is_oss = default_registry.resolve_taxonomy(m)
            if q and q not in m.lower() and q not in dname.lower() and q not in prov.lower() and q not in fam.lower():
                continue
            mods = subs.get(m, {}).get("modalities", set())
            tva = "".join(k[0].upper() if k in mods else "-" for k in ("text", "vision", "audio"))
            p_meta = providers.get(prov, {})
            logo_slug = str(p_meta.get("logo") or prov or "org")
            cname = str(p_meta.get("color") or "gray")
            hex_color = str(named_colors.get(cname, cname if cname.startswith("#") else "#78909C"))
            icon = self._get_org_logo_only_icon(logo_slug, hex_color)
            stripe = "odd" if shown_m % 2 else "even"
            needs_reg = m not in disp or prov in _UNKNOWN_ORG_TOKENS or fam in _UNKNOWN_ORG_TOKENS
            tags = (stripe, "warn") if needs_reg else (stripe,)
            self.mgmt_models_tree.insert(
                "",
                "end",
                text="",
                image=icon,
                tags=tags,
                values=(m, dname, prov, fam, "Yes" if is_oss else "No", tva),
            )
            shown_m += 1
        self._ensure_tree_empty_state(self.mgmt_models_tree, "No matching models — press Esc to clear")
        if hasattr(self, "mgmt_model_count_var"):
            self.mgmt_model_count_var.set(f"{shown_m}/{len(all_slugs)}")

        oq = self.mgmt_org_search_var.get().strip().lower() if hasattr(self, "mgmt_org_search_var") else ""
        self.mgmt_orgs_tree.delete(*self.mgmt_orgs_tree.get_children())
        shown_o = 0
        for slug in sorted(providers):
            p = providers[slug]
            name = str(p.get("name", slug))
            fams = ", ".join(sorted((p.get("families") or {}).keys()))
            aliases = ", ".join(p.get("aliases") or [])
            if oq and oq not in slug.lower() and oq not in name.lower() and oq not in fams.lower() and oq not in aliases.lower():
                continue
            logo_slug = str(p.get("logo", slug) or slug)
            color_name = str(p.get("color", "gray") or "gray")
            hex_color = str(named_colors.get(color_name, color_name if color_name.startswith("#") else "#78909C"))
            icon = self._make_org_preview_icon(logo_slug, hex_color)
            stripe = "odd" if shown_o % 2 else "even"
            self.mgmt_orgs_tree.insert(
                "",
                "end",
                text="",
                image=icon,
                tags=(stripe,),
                values=(slug, name, logo_slug, color_name, fams, aliases),
            )
            shown_o += 1
        self._ensure_tree_empty_state(self.mgmt_orgs_tree, "No matching organizations")
        if hasattr(self, "mgmt_org_count_var"):
            self.mgmt_org_count_var.set(f"{shown_o}/{len(providers)}")

    def _invalidate_icon_caches(self) -> None:
        for attr in ("_logo_png_cache", "_logo_svg_cache"):
            if hasattr(self, attr):
                delattr(self, attr)
        self._org_icons = {}
        self._org_small_icons = {}

    def _load_logo_png_b64(self) -> dict[str, str]:
        if hasattr(self, "_logo_png_cache"):
            return self._logo_png_cache
        cache: dict[str, str] = {}
        js_path = ROOT / "docs" / "assets" / "logo-data.js"
        if js_path.exists():
            text = js_path.read_text(encoding="utf-8", errors="replace")
            for m in re.finditer(r'"([^"]+)":\s*"data:image/png;base64,([^"]+)"', text):
                cache[m.group(1)] = m.group(2)
            brand_m = re.search(r'SYNHALEES_BRAND_ICON\s*=\s*"data:image/png;base64,([^"]+)"', text)
            if brand_m:
                cache["synhalaAI"] = brand_m.group(1)
                cache["synhalaai"] = brand_m.group(1)
        icon_png = ROOT / "docs" / "assets" / "icon.png"
        if "synhalaAI" not in cache and icon_png.is_file():
            import base64
            b64 = base64.b64encode(icon_png.read_bytes()).decode("ascii")
            cache["synhalaAI"] = b64
            cache["synhalaai"] = b64
        self._logo_png_cache = cache
        return cache

    def _load_logo_svg_map(self) -> dict[str, str]:
        if hasattr(self, "_logo_svg_cache"):
            return self._logo_svg_cache
        self._logo_svg_cache = load_svg_sources_map(ROOT)
        return self._logo_svg_cache

    def _draw_logo_16x16_into(self, target_img: Any, ox: int, oy: int, logo_slug: str, hex_color: str) -> None:
        """Render Tier 1 PNG -> Tier 2 pure-stdlib SVG -> Tier 3 monogram into a 16x16 slot."""
        tk = self.tk
        # Tier 1: PNG from logo-data.js / icon.png (with luminance-aware tile & block averaging)
        png_b64 = self._load_logo_png_b64().get(logo_slug)
        if png_b64:
            try:
                raw = tk.PhotoImage(master=self.root, data=png_b64)
                rw, rh = max(1, raw.width()), max(1, raw.height())
                # Detect foreground luminance to choose contrasting tile background (e.g. white xAI logo)
                lum_vals: list[float] = []
                sample_step = max(1, max(rw, rh) // 16)
                for sy in range(0, rh, sample_step):
                    for sx in range(0, rw, sample_step):
                        if not raw.transparency_get(sx, sy):
                            r, g, b = raw.get(sx, sy)
                            lum_vals.append(0.299 * r + 0.587 * g + 0.114 * b)
                avg_lum = (sum(lum_vals) / len(lum_vals)) if lum_vals else 0.0
                bg_tile = "#1e293b" if avg_lum > 185 else "#f8fafc"
                br, bg_c, bb = int(bg_tile[1:3], 16), int(bg_tile[3:5], 16), int(bg_tile[5:7], 16)
                target_img.put(bg_tile, to=(ox, oy, ox + 16, oy + 16))
                scale_x = rw / 16.0
                scale_y = rh / 16.0
                for dy in range(16):
                    y0 = int(dy * scale_y)
                    y1 = max(y0 + 1, min(rh, int((dy + 1) * scale_y)))
                    for dx in range(16):
                        x0 = int(dx * scale_x)
                        x1 = max(x0 + 1, min(rw, int((dx + 1) * scale_x)))
                        rs = gs = bs = tot = opq = 0
                        for py in range(y0, y1):
                            for px in range(x0, x1):
                                tot += 1
                                if not raw.transparency_get(px, py):
                                    r, g, b = raw.get(px, py)
                                    rs += r
                                    gs += g
                                    bs += b
                                    opq += 1
                        if opq:
                            alpha = opq / tot
                            r_f = round((rs / opq) * alpha + br * (1.0 - alpha))
                            g_f = round((gs / opq) * alpha + bg_c * (1.0 - alpha))
                            b_f = round((bs / opq) * alpha + bb * (1.0 - alpha))
                            target_img.put(f"#{r_f:02x}{g_f:02x}{b_f:02x}", to=(ox + dx, oy + dy))
                return
            except Exception:
                pass

        # Tier 2: Pure-stdlib 24x24 SVG path rasterizer
        svg_txt = self._load_logo_svg_map().get(logo_slug, "")
        if svg_txt:
            grid = rasterize_svg_to_grid(svg_txt, size=16, fg_fallback=hex_color)
            if grid is not None:
                for gy, row_cols in enumerate(grid):
                    target_img.put("{" + " ".join(row_cols) + "}", to=(ox, oy + gy))
                return

        # Tier 3: Crisp 5x7 monogram glyph on brand-colored tile
        mgrid = render_monogram_grid(logo_slug[:1] or "?", hex_color, size=16)
        for gy, row_cols in enumerate(mgrid):
            target_img.put("{" + " ".join(row_cols) + "}", to=(ox, oy + gy))

    def _get_org_logo_only_icon(self, logo_slug: str, hex_color: str) -> Any:
        if not hasattr(self, "_org_small_icons"):
            self._org_small_icons = {}
        key = (logo_slug, hex_color)
        if key in self._org_small_icons:
            return self._org_small_icons[key]
        img = self.tk.PhotoImage(master=self.root, width=16, height=16)
        self._draw_logo_16x16_into(img, 0, 0, logo_slug, hex_color)
        self._org_small_icons[key] = img
        return img

    def _make_org_preview_icon(self, logo_slug: str, hex_color: str) -> Any:
        if not hasattr(self, "_org_icons"):
            self._org_icons = {}
        key = (logo_slug, hex_color)
        if key in self._org_icons:
            return self._org_icons[key]
        badge = self.tk.PhotoImage(master=self.root, width=38, height=16)
        self._draw_logo_16x16_into(badge, 0, 0, logo_slug, hex_color)
        badge.put("#475569", to=(22, 1, 36, 15))
        badge.put(hex_color, to=(23, 2, 35, 14))
        self._org_icons[key] = badge
        return badge

    def _copy_tree_selection(self, tree: Any, col_idx: int, label: str) -> None:
        sel = tree.selection()
        if not sel or sel[0] == "__empty__":
            return
        vals = tree.item(sel[0], "values")
        if vals and len(vals) > col_idx:
            self._copy_text_to_clipboard(str(vals[col_idx]), label)

    def _open_selected_pillar_inspector(self) -> None:
        sel = self.pillars_tree.selection()
        if sel and sel[0] != "__empty__":
            vals = self.pillars_tree.item(sel[0], "values")
            if vals:
                open_pillar_inspector(self, str(vals[0]))
                return
        open_pillar_inspector(self, None)

    def _open_scorecard_inspector_from_overview(self) -> None:
        sel = self.models_tree.selection()
        slug = str(self.models_tree.item(sel[0], "values")[0]) if (sel and sel[0] != "__empty__") else ""
        open_scorecard_inspector(self, slug)

    def _open_scorecard_inspector_from_mgmt(self) -> None:
        sel = [i for i in self.mgmt_models_tree.selection() if i != "__empty__"]
        slug_a = str(self.mgmt_models_tree.item(sel[0], "values")[0]) if sel else ""
        slug_b = str(self.mgmt_models_tree.item(sel[1], "values")[0]) if len(sel) >= 2 else ""
        open_scorecard_inspector(self, slug_a, slug_b)

    def _open_mgmt_model_dialog(self, *, edit_selected: bool = False) -> None:
        tk, ttk = self.tk, self.ttk
        sel = [i for i in self.mgmt_models_tree.selection() if i != "__empty__"] if edit_selected else []
        if edit_selected and not sel:
            self.messagebox.showinfo("Select Model", "Select a model row first to edit.")
            return
        vals = self.mgmt_models_tree.item(sel[0], "values") if sel else ("", "", "", "", "No", "---")
        init_canon = str(vals[0])
        init_disp = str(vals[1])
        init_org = str(vals[2]) if str(vals[2]) not in _UNKNOWN_ORG_TOKENS else ""
        init_fam = str(vals[3]) if str(vals[3]) not in _UNKNOWN_ORG_TOKENS else ""
        init_oss = str(vals[4]) == "Yes"

        data = json.loads(MODELS_JSON.read_text(encoding="utf-8"))
        providers = dict(data.get("providers") or {})
        org_choices = sorted(providers.keys())

        dlg = tk.Toplevel(self.root)
        dlg.title("Edit Model" if edit_selected else "Add Model")
        dlg.configure(bg=PALETTE["surface"])
        dlg.transient(self.root)
        dlg.grab_set()
        self._center_dialog(dlg, self._px(500), self._px(300))

        wrap = tk.Frame(dlg, bg=PALETTE["surface"], padx=self._px(18), pady=self._px(16))
        wrap.pack(fill="both", expand=True)
        wrap.columnconfigure(1, weight=1)

        canon_var = tk.StringVar(value=init_canon)
        disp_var = tk.StringVar(value=init_disp)
        org_var = tk.StringVar(value=init_org or (org_choices[0] if org_choices else ""))
        fam_var = tk.StringVar(value=init_fam)
        oss_var = tk.BooleanVar(value=init_oss)

        tk.Label(wrap, text="Canonical Slug", bg=PALETTE["surface"], fg=PALETTE["muted"], font=("Segoe UI", 9)).grid(row=0, column=0, sticky="w", pady=6)
        canon_ent = ttk.Entry(wrap, textvariable=canon_var)
        canon_ent.grid(row=0, column=1, sticky="ew", padx=(10, 0), pady=6)
        if edit_selected:
            canon_ent.configure(state="readonly")

        tk.Label(wrap, text="Display Name", bg=PALETTE["surface"], fg=PALETTE["muted"], font=("Segoe UI", 9)).grid(row=1, column=0, sticky="w", pady=6)
        ttk.Entry(wrap, textvariable=disp_var).grid(row=1, column=1, sticky="ew", padx=(10, 0), pady=6)

        tk.Label(wrap, text="Organization", bg=PALETTE["surface"], fg=PALETTE["muted"], font=("Segoe UI", 9)).grid(row=2, column=0, sticky="w", pady=6)
        org_cb = ttk.Combobox(wrap, textvariable=org_var, values=org_choices, state="readonly")
        org_cb.grid(row=2, column=1, sticky="ew", padx=(10, 0), pady=6)

        tk.Label(wrap, text="Family", bg=PALETTE["surface"], fg=PALETTE["muted"], font=("Segoe UI", 9)).grid(row=3, column=0, sticky="w", pady=6)
        fam_cb = ttk.Combobox(wrap, textvariable=fam_var, values=sorted((providers.get(org_var.get(), {}).get("families") or {}).keys()))
        fam_cb.grid(row=3, column=1, sticky="ew", padx=(10, 0), pady=6)

        def _on_org_picked(_e: Any = None) -> None:
            o = org_var.get().strip().lower()
            fams = sorted((providers.get(o, {}).get("families") or {}).keys())
            fam_cb["values"] = fams
            if fams and fam_var.get().strip() not in fams:
                fam_var.set(fams[0])
            fobj = (providers.get(o, {}).get("families") or {}).get(fam_var.get().strip())
            if isinstance(fobj, dict):
                oss_var.set(bool(fobj.get("open_source", False)))

        org_cb.bind("<<ComboboxSelected>>", _on_org_picked)

        ttk.Checkbutton(wrap, text="Open-source / open-weights model", variable=oss_var).grid(row=4, column=1, sticky="w", padx=(10, 0), pady=6)

        btns = tk.Frame(wrap, bg=PALETTE["surface"])
        btns.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(14, 0))

        def _save() -> None:
            canon = canonical_model_slug(canon_var.get().strip())
            if not canon:
                self.messagebox.showerror("Invalid Model", "Canonical Slug is required.", parent=dlg)
                return
            try:
                save_model_registry_entries([{
                    "canonical": canon,
                    "display_name": disp_var.get().strip(),
                    "org": org_var.get().strip(),
                    "family": fam_var.get().strip(),
                    "open_source": oss_var.get(),
                }])
            except ValueError as exc:
                self.messagebox.showerror("Validation Error", str(exc), parent=dlg)
                return
            self._append_log(f"[OK] Saved model '{canon}' in synhalees/models.json.\n", "ok")
            dlg.destroy()
            self.refresh_all()

        ttk.Button(btns, text="Save Model", style="Accent.TButton", command=_save).pack(side="right", padx=(8, 0))
        ttk.Button(btns, text="Cancel", style="Ghost.TButton", command=dlg.destroy).pack(side="right")
        dlg.bind("<Escape>", lambda _e: dlg.destroy())
        dlg.bind("<Return>", lambda _e: _save())

    def _open_bulk_edit_models_dialog(self) -> None:
        tk, ttk = self.tk, self.ttk
        sel = [i for i in self.mgmt_models_tree.selection() if i != "__empty__"]
        if not sel:
            self.messagebox.showinfo("Select Models", "Select one or more models first (Ctrl/Shift-click) to bulk edit.")
            return
        slugs = [str(self.mgmt_models_tree.item(i, "values")[0]) for i in sel]
        data = json.loads(MODELS_JSON.read_text(encoding="utf-8"))
        providers = dict(data.get("providers") or {})
        org_choices = ["(Keep current)", *sorted(providers.keys())]

        dlg = tk.Toplevel(self.root)
        dlg.title(f"Bulk Edit {len(slugs)} Model(s)")
        dlg.configure(bg=PALETTE["surface"])
        dlg.transient(self.root)
        dlg.grab_set()
        self._center_dialog(dlg, self._px(480), self._px(270))

        wrap = tk.Frame(dlg, bg=PALETTE["surface"], padx=self._px(18), pady=self._px(16))
        wrap.pack(fill="both", expand=True)
        wrap.columnconfigure(1, weight=1)

        tk.Label(
            wrap,
            text=f"Updating {len(slugs)} model(s): {', '.join(slugs[:4])}{'…' if len(slugs) > 4 else ''}",
            bg=PALETTE["surface"],
            fg=PALETTE["info"],
            font=("Segoe UI", 9, "bold"),
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))

        org_var = tk.StringVar(value="(Keep current)")
        fam_var = tk.StringVar(value="")
        oss_var = tk.StringVar(value="(Keep current)")

        tk.Label(wrap, text="Organization", bg=PALETTE["surface"], fg=PALETTE["muted"], font=("Segoe UI", 9)).grid(row=1, column=0, sticky="w", pady=6)
        org_cb = ttk.Combobox(wrap, textvariable=org_var, values=org_choices, state="readonly")
        org_cb.grid(row=1, column=1, sticky="ew", padx=(10, 0), pady=6)

        tk.Label(wrap, text="Family (blank=keep)", bg=PALETTE["surface"], fg=PALETTE["muted"], font=("Segoe UI", 9)).grid(row=2, column=0, sticky="w", pady=6)
        fam_cb = ttk.Combobox(wrap, textvariable=fam_var, values=[])
        fam_cb.grid(row=2, column=1, sticky="ew", padx=(10, 0), pady=6)

        def _on_org(_e: Any = None) -> None:
            o = org_var.get()
            fam_cb["values"] = sorted((providers.get(o, {}).get("families") or {}).keys()) if o in providers else []

        org_cb.bind("<<ComboboxSelected>>", _on_org)

        tk.Label(wrap, text="Open Source", bg=PALETTE["surface"], fg=PALETTE["muted"], font=("Segoe UI", 9)).grid(row=3, column=0, sticky="w", pady=6)
        ttk.Combobox(
            wrap,
            textvariable=oss_var,
            values=["(Keep current)", "Yes (Open Source)", "No (Proprietary)"],
            state="readonly",
        ).grid(row=3, column=1, sticky="ew", padx=(10, 0), pady=6)

        btns = tk.Frame(wrap, bg=PALETTE["surface"])
        btns.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(14, 0))

        def _apply() -> None:
            o_val = None if org_var.get() == "(Keep current)" else org_var.get().strip().lower()
            f_val = fam_var.get().strip().lower() or None
            oss_choice = oss_var.get()
            oss_val = True if oss_choice.startswith("Yes") else (False if oss_choice.startswith("No") else None)
            try:
                cnt = bulk_update_model_registry_entries(slugs, org=o_val, family=f_val, open_source=oss_val)
            except ValueError as exc:
                self.messagebox.showerror("Validation Error", str(exc), parent=dlg)
                return
            self._append_log(f"[OK] Bulk-updated {cnt} model(s) in synhalees/models.json.\n", "ok")
            dlg.destroy()
            self.refresh_all()

        ttk.Button(btns, text=f"Apply to {len(slugs)} Model(s)", style="Accent.TButton", command=_apply).pack(side="right", padx=(8, 0))
        ttk.Button(btns, text="Cancel", style="Ghost.TButton", command=dlg.destroy).pack(side="right")
        dlg.bind("<Escape>", lambda _e: dlg.destroy())

    def _run_adopt_and_sync_scorecards(self) -> None:
        res = adopt_and_sync_scorecards()
        adopted = res["adopted"]
        relocated = res["relocated"]
        for item in adopted:
            self._append_log(f"[OK] Adopted legacy scorecard: {item}\n", "ok")
        for item in relocated:
            self._append_log(f"[OK] Relocated taxonomy CSV: {item}\n", "ok")
        if not adopted and not relocated:
            self._append_log("[OK] All submissions/ scorecards already use canonical names and folders.\n", "ok")
            self.messagebox.showinfo("Adopt & Sync", "All scorecards already match canonical slugs and folders.")
            return
        self.refresh_all()
        if self.messagebox.askyesno(
            "Rebuild Leaderboard?",
            f"Adopted {len(adopted)} and relocated {len(relocated)} scorecard(s).\nRebuild docs/assets/data/* now?",
        ):
            self.run_cli(["site", "build"])

    def _delete_selected_mgmt_model(self) -> None:
        sel = [i for i in self.mgmt_models_tree.selection() if i != "__empty__"]
        if not sel:
            self.messagebox.showinfo("Select Model", "Select a model row to delete.")
            return
        canons = [str(self.mgmt_models_tree.item(i, "values")[0]) for i in sel]
        prompt = (
            f"Delete '{canons[0]}' from models.json, submissions/, and kaggle/results/*.slim.csv?"
            if len(canons) == 1
            else f"Delete {len(canons)} selected models from models.json, submissions/, and kaggle/results/*.slim.csv?"
        )
        if not self.messagebox.askyesno("Delete Model", prompt):
            return
        tot_reg = tot_sub = tot_slim = 0
        for canon in canons:
            res = delete_model_completely(canon)
            tot_reg += res["registry"]
            tot_sub += res["submissions"]
            tot_slim += res["slim_rows"]
        if tot_reg or tot_sub or tot_slim:
            self._append_log(
                f"[OK] Deleted {len(canons)} model(s) (registry={tot_reg}, submissions={tot_sub}, slim_rows={tot_slim}).\n",
                "ok",
            )
            self.refresh_all()
            if tot_sub > 0:
                self.run_cli(["build"])
        else:
            self.messagebox.showinfo("Nothing to Delete", "No files or registry entries found for the selected model(s).")

    def _open_mgmt_org_dialog(self, *, edit_selected: bool = False) -> None:
        tk, ttk = self.tk, self.ttk
        sel = [i for i in self.mgmt_orgs_tree.selection() if i != "__empty__"] if edit_selected else []
        if edit_selected and not sel:
            self.messagebox.showinfo("Select Organization", "Select an organization row first to edit.")
            return
        vals = self.mgmt_orgs_tree.item(sel[0], "values") if sel else ("", "", "", "gray", "", "")

        data = json.loads(MODELS_JSON.read_text(encoding="utf-8"))
        providers = dict(data.get("providers") or {})
        named_colors = dict(data.get("named_colors") or {"gray": "#78909C"})
        color_choices = sorted(named_colors.keys())
        svg_choices = sorted(p.stem for p in (ROOT / "docs" / "assets" / "logos").glob("*.svg")) if (ROOT / "docs" / "assets" / "logos").is_dir() else []

        org_slug_init = str(vals[0])
        families_draft: dict[str, dict[str, Any]] = dict((providers.get(org_slug_init) or {}).get("families") or {})
        picked_svg: list[Path | None] = [None]

        dlg = tk.Toplevel(self.root)
        dlg.title("Edit Organization & Families" if edit_selected else "Add Organization")
        dlg.configure(bg=PALETTE["surface"])
        dlg.transient(self.root)
        dlg.grab_set()
        self._center_dialog(dlg, self._px(690), self._px(580))

        wrap = tk.Frame(dlg, bg=PALETTE["surface"], padx=self._px(18), pady=self._px(14))
        wrap.pack(fill="both", expand=True)
        wrap.columnconfigure(1, weight=1)

        slug_var = tk.StringVar(value=org_slug_init)
        name_var = tk.StringVar(value=str(vals[1]))
        logo_var = tk.StringVar(value=str(vals[2]))
        color_var = tk.StringVar(value=str(vals[3]) or "gray")
        aliases_var = tk.StringVar(value=str(vals[5]))

        tk.Label(wrap, text="Org Slug", bg=PALETTE["surface"], fg=PALETTE["muted"], font=("Segoe UI", 9)).grid(row=0, column=0, sticky="w", pady=5)
        slug_ent = ttk.Entry(wrap, textvariable=slug_var)
        slug_ent.grid(row=0, column=1, sticky="ew", padx=(10, 0), pady=5)
        if edit_selected:
            slug_ent.configure(state="readonly")

        tk.Label(wrap, text="Display Name", bg=PALETTE["surface"], fg=PALETTE["muted"], font=("Segoe UI", 9)).grid(row=1, column=0, sticky="w", pady=5)
        ttk.Entry(wrap, textvariable=name_var).grid(row=1, column=1, sticky="ew", padx=(10, 0), pady=5)

        tk.Label(wrap, text="Logo Slug / SVG", bg=PALETTE["surface"], fg=PALETTE["muted"], font=("Segoe UI", 9)).grid(row=2, column=0, sticky="w", pady=5)
        logo_row = tk.Frame(wrap, bg=PALETTE["surface"])
        logo_row.grid(row=2, column=1, sticky="ew", padx=(10, 0), pady=5)
        logo_row.columnconfigure(0, weight=1)
        ttk.Combobox(logo_row, textvariable=logo_var, values=svg_choices).grid(row=0, column=0, sticky="ew")

        def _browse_svg() -> None:
            chosen = self.filedialog.askopenfilename(
                parent=dlg,
                title="Select Organization SVG Logo",
                filetypes=[("SVG Vector Files", "*.svg"), ("All files", "*.*")],
            )
            if not chosen:
                return
            p = Path(chosen)
            picked_svg[0] = p
            if not logo_var.get().strip():
                logo_var.set(slug_var.get().strip().lower() or p.stem.lower())
            _sync_org_previews()

        ttk.Button(logo_row, text="Browse .svg…", style="Ghost.TButton", command=_browse_svg).grid(row=0, column=1, padx=(8, 4))
        logo_preview_lbl = tk.Label(logo_row, bg=PALETTE["surface"], fg=PALETTE["muted"], font=("Segoe UI", 8))
        logo_preview_lbl.grid(row=0, column=2, padx=(4, 0))

        tk.Label(wrap, text="Color", bg=PALETTE["surface"], fg=PALETTE["muted"], font=("Segoe UI", 9)).grid(row=3, column=0, sticky="w", pady=5)
        color_row = tk.Frame(wrap, bg=PALETTE["surface"])
        color_row.grid(row=3, column=1, sticky="ew", padx=(10, 0), pady=5)
        color_row.columnconfigure(0, weight=1)
        ttk.Combobox(color_row, textvariable=color_var, values=color_choices, state="readonly").grid(row=0, column=0, sticky="ew")
        swatch_box = tk.Frame(color_row, width=self._px(24), height=self._px(20), bg="#78909C", highlightthickness=1, highlightbackground=PALETTE["border"])
        swatch_box.grid(row=0, column=1, padx=(8, 4))
        swatch_hex_lbl = tk.Label(color_row, text="#78909C", bg=PALETTE["surface"], fg=PALETTE["muted"], font=("Consolas", 9))
        swatch_hex_lbl.grid(row=0, column=2)

        # Curated 24-color site-harmonized swatch grid (no custom hex mismatch)
        palette_strip = tk.Frame(wrap, bg=PALETTE["surface"])
        palette_strip.grid(row=4, column=1, sticky="w", padx=(10, 0), pady=(0, 6))
        swatch_chips: dict[str, Any] = {}
        for c_idx, (cname, hx) in enumerate(named_colors.items()):
            chip = tk.Frame(
                palette_strip,
                width=self._px(20),
                height=self._px(18),
                bg=hx,
                highlightthickness=2,
                highlightbackground=PALETTE["border"],
                cursor="hand2",
            )
            chip.grid(row=c_idx // 12, column=c_idx % 12, padx=(0, 4), pady=(0, 4))
            chip.bind("<Button-1>", lambda _e, c=cname: color_var.set(c))
            swatch_chips[cname] = chip

        def _sync_org_previews(*_args: Any) -> None:
            cname = color_var.get().strip()
            hcol = str(named_colors.get(cname, named_colors.get("gray", "#78909C")))
            swatch_box.configure(bg=hcol)
            swatch_hex_lbl.configure(text=hcol)
            for sc_name, chip in swatch_chips.items():
                chip.configure(highlightbackground="#ffffff" if sc_name == cname else PALETTE["border"])
            lslug = logo_var.get().strip() or slug_var.get().strip() or "org"
            svg_exists = bool((ROOT / "docs" / "assets" / "logos" / f"{lslug}.svg").exists())
            if picked_svg[0] is not None:
                badge_txt = f" staged: {picked_svg[0].name}"
                badge_fg = PALETTE["info"]
            elif svg_exists:
                badge_txt = " SVG OK"
                badge_fg = PALETTE["ok"]
            else:
                badge_txt = " monogram"
                badge_fg = PALETTE["warn"]
            icon = self._make_org_preview_icon(lslug, hcol)
            logo_preview_lbl.configure(image=icon, compound="left", text=badge_txt, fg=badge_fg)
            logo_preview_lbl._icon_ref = icon

        color_var.trace_add("write", _sync_org_previews)
        logo_var.trace_add("write", _sync_org_previews)
        _sync_org_previews()

        tk.Label(wrap, text="Aliases (comma-sep)", bg=PALETTE["surface"], fg=PALETTE["muted"], font=("Segoe UI", 9)).grid(row=5, column=0, sticky="w", pady=5)
        ttk.Entry(wrap, textvariable=aliases_var).grid(row=5, column=1, sticky="ew", padx=(10, 0), pady=5)

        # Embedded Model Families (Taxonomy & Match Patterns) Editor
        fam_card = tk.Frame(wrap, bg=PALETTE["card"], padx=self._px(10), pady=self._px(8), highlightthickness=1, highlightbackground=PALETTE["border"])
        fam_card.grid(row=6, column=0, columnspan=2, sticky="nsew", pady=(8, 6))
        wrap.rowconfigure(6, weight=1)
        tk.Label(fam_card, text="Model Families (Taxonomy & Match Patterns)", bg=PALETTE["card"], fg=PALETTE["text"], font=("Segoe UI", 9, "bold")).pack(
            anchor="w", pady=(0, self._px(4))
        )
        fam_tree = self._make_scrolled_tree(fam_card, columns=("slug", "name", "oss", "patterns"), height=4, fill="both", expand=True)
        for col, lbl, w in (("slug", "Family Slug", 110), ("name", "Display Name", 130), ("oss", "OSS", 55), ("patterns", "Match Patterns", 230)):
            fam_tree.heading(col, text=lbl)
            fam_tree.column(col, width=self._px(w), anchor="w" if col != "oss" else "center")

        f_edit = tk.Frame(fam_card, bg=PALETTE["card"])
        f_edit.pack(fill="x", pady=(self._px(6), 0))
        f_slug_v, f_name_v, f_pat_v, f_oss_v = tk.StringVar(), tk.StringVar(), tk.StringVar(), tk.BooleanVar(value=False)
        ttk.Entry(f_edit, textvariable=f_slug_v, width=12).pack(side="left", padx=(0, 4))
        ttk.Entry(f_edit, textvariable=f_name_v, width=14).pack(side="left", padx=(0, 4))
        ttk.Entry(f_edit, textvariable=f_pat_v, width=22).pack(side="left", fill="x", expand=True, padx=(0, 6))
        ttk.Checkbutton(f_edit, text="OSS", variable=f_oss_v).pack(side="left", padx=(0, 6))

        def _reload_fams() -> None:
            fam_tree.delete(*fam_tree.get_children())
            for idx, fk in enumerate(sorted(families_draft)):
                fv = families_draft[fk]
                fam_tree.insert(
                    "",
                    "end",
                    tags=("odd" if idx % 2 else "even",),
                    values=(fk, fv.get("name", fk), "Yes" if fv.get("open_source") else "No", ", ".join(fv.get("match_patterns") or [])),
                )

        def _on_fam_select(_e: Any = None) -> None:
            fsel = fam_tree.selection()
            if not fsel:
                return
            fv = fam_tree.item(fsel[0], "values")
            f_slug_v.set(fv[0])
            f_name_v.set(fv[1])
            f_oss_v.set(fv[2] == "Yes")
            f_pat_v.set(fv[3])

        def _upsert_fam() -> None:
            fk = f_slug_v.get().strip().lower()
            if not fk or fk in _UNKNOWN_ORG_TOKENS:
                self.messagebox.showerror("Invalid Family", "Enter a valid lowercase family slug.", parent=dlg)
                return
            pats = [p.strip().lower() for p in f_pat_v.get().split(",") if p.strip()]
            if fk not in pats:
                pats.insert(0, fk)
            families_draft[fk] = {
                "name": f_name_v.get().strip() or fk.replace("-", " ").title(),
                "open_source": bool(f_oss_v.get()),
                "match_patterns": pats,
            }
            _reload_fams()

        def _remove_fam() -> None:
            fk = f_slug_v.get().strip().lower()
            if fk in families_draft:
                del families_draft[fk]
                f_slug_v.set("")
                f_name_v.set("")
                f_pat_v.set("")
                _reload_fams()

        fam_tree.bind("<<TreeviewSelect>>", _on_fam_select)
        ttk.Button(f_edit, text="Add/Update", style="Ghost.TButton", command=_upsert_fam).pack(side="left", padx=(0, 4))
        ttk.Button(f_edit, text="Remove", style="Danger.TButton", command=_remove_fam).pack(side="left")
        _reload_fams()

        btns = tk.Frame(wrap, bg=PALETTE["surface"])
        btns.grid(row=7, column=0, columnspan=2, sticky="ew", pady=(10, 0))

        def _save() -> None:
            oslug = slug_var.get().strip().lower()
            lslug = (logo_var.get().strip() or oslug)
            alias_list = [a.strip() for a in aliases_var.get().split(",") if a.strip()]
            cname = color_var.get().strip() or "gray"
            hcol = str(named_colors.get(cname, cname if cname.startswith("#") else "#78909C"))
            try:
                save_organization_entry(
                    oslug,
                    name=name_var.get(),
                    logo=lslug,
                    color=cname,
                    aliases=alias_list,
                    families=families_draft,
                )
                if picked_svg[0] is not None:
                    res = install_organization_logo(
                        lslug,
                        picked_svg[0],
                        org_slug=oslug,
                        aliases=alias_list,
                        color_hex=hcol,
                        rebuild_logo_data=True,
                    )
                    self._invalidate_icon_caches()
                    self._append_log(
                        f"[OK] Installed {res['svg_path'].name} & rebuilt logo-data.js.\n",
                        "ok",
                    )
            except ValueError as exc:
                self.messagebox.showerror("Validation Error", str(exc), parent=dlg)
                return
            self._append_log(f"[OK] Saved organization '{oslug}' in synhalees/models.json.\n", "ok")
            dlg.destroy()
            self.refresh_all()

        ttk.Button(btns, text="Save Organization", style="Accent.TButton", command=_save).pack(side="right", padx=(8, 0))
        ttk.Button(btns, text="Cancel", style="Ghost.TButton", command=dlg.destroy).pack(side="right")
        dlg.bind("<Escape>", lambda _e: dlg.destroy())

    def _delete_selected_mgmt_org(self) -> None:
        sel = [i for i in self.mgmt_orgs_tree.selection() if i != "__empty__"]
        if not sel:
            self.messagebox.showinfo("Select Organization", "Select an organization row to delete.")
            return
        slug = str(self.mgmt_orgs_tree.item(sel[0], "values")[0])
        if not self.messagebox.askyesno("Delete Organization", f"Delete organization '{slug}' from synhalees/models.json?"):
            return
        if delete_organization_entry(slug):
            self._append_log(f"[OK] Removed organization '{slug}' from synhalees/models.json.\n", "ok")
            self.refresh_all()

    def _populate_kaggle_table(self) -> None:
        self.kg_tree.delete(*self.kg_tree.get_children())
        mode = self.kg_cov_filter_var.get() if hasattr(self, "kg_cov_filter_var") else "All"
        q = self.kg_cov_search_var.get().strip().lower() if hasattr(self, "kg_cov_search_var") else ""
        try:
            model_tasks, _, _ = gather_coverage(check_remote=False)
        except Exception:
            model_tasks = {}
        idx = 0
        for m in sorted(model_tasks):
            if q and q not in m.lower():
                continue
            st = model_tasks[m]
            is_complete = st["text"] >= 15 and st["vision"] > 0 and st["audio"] > 0
            if mode == "Incomplete only" and is_complete:
                continue
            if mode == "Complete 15/15 T+V+A" and not is_complete:
                continue
            stripe = "odd" if idx % 2 else "even"
            status_tag = "ok" if is_complete else ("warn" if (st["text"] or st["vision"] or st["audio"]) else "muted")
            self.kg_tree.insert(
                "",
                "end",
                tags=(stripe, status_tag),
                values=(
                    m,
                    f"{st['text']}/15",
                    "DONE" if st["vision"] > 0 else "missing",
                    "DONE" if st["audio"] > 0 else "missing",
                ),
            )
            idx += 1
        self._ensure_tree_empty_state(self.kg_tree, "No matching slim coverage models")

    # ------------------------------------------------------------------ Actions

    def _update_kg_preview(self) -> None:
        if not hasattr(self, "kg_preview_var"):
            return
        args = build_kaggle_args(
            "pull",
            task=self.kg_task_var.get(),
            model=self.kg_model_var.get(),
            modality=self.kg_mod_label_var.get(),
            force=self.kg_force_var.get(),
            dry_run=self.kg_dry_var.get(),
            colab=True,
        )
        self.kg_preview_var.set(format_cmd_preview(args))

    def _start_kaggle_pull(self) -> None:
        self._guard_and_run_kaggle(
            "pull",
            build_kaggle_args(
                "pull",
                task=self.kg_task_var.get(),
                model=self.kg_model_var.get(),
                modality=self.kg_mod_label_var.get(),
                force=self.kg_force_var.get(),
                dry_run=self.kg_dry_var.get(),
                colab=True,
            ),
        )

    def _retry_failed_kaggle_tasks(self) -> None:
        failed = list(getattr(self, "_failed_kaggle_tasks", []))
        if not failed:
            return
        steps: list[tuple[str, list[str]]] = [
            (
                f"Retry {idx}/{len(failed)}: {t}",
                build_kaggle_args(
                    "pull",
                    task=t,
                    model=self.kg_model_var.get(),
                    force=True,
                    colab=True,
                ),
            )
            for idx, t in enumerate(failed, start=1)
        ]
        if self.kg_auto_build_var.get():
            steps.append(("Importing & rebuilding site...", build_kaggle_args("submit", task="all", build=True)))
            steps.append(("Verifying CI gates...", ["check"]))
        self._failed_kaggle_tasks = []
        if hasattr(self, "kg_retry_btn"):
            self.kg_retry_btn.pack_forget()
        self.run_pipeline(steps)

    def _start_one_click_kaggle_sync(self) -> None:
        """Seamless Discover -> (Register if needed) -> Pull -> Submit -> Build -> Check."""
        self.select_page("kaggle")
        self._discover_kaggle_models(
            on_done=lambda: self.root.after(50, self._start_kaggle_pull)
        )

    def _sync_and_verify_site(self) -> None:
        """Chain `site logos` -> `site build` -> `check` into one seamless pipeline."""
        self._set_site_gate("logos", "RUNNING", "rebuilding...", PALETTE["warn"])
        self._set_site_gate("data", "QUEUED", "waiting...", PALETTE["muted"])
        self._set_site_gate("schemas", "QUEUED", "waiting...", PALETTE["muted"])
        self._set_site_gate("compile", "QUEUED", "waiting...", PALETTE["muted"])
        self.run_pipeline([
            ("Step 1/3 · Rebuilding provider logos (site logos)...", ["site", "logos"]),
            ("Step 2/3 · Rebuilding leaderboard data (site build)...", ["site", "build"]),
            ("Step 3/3 · Verifying all 4 CI gates (check)...", ["check"]),
        ])

    def _on_select_local_run(self, _e: Any = None) -> None:
        sel = self.local_runs_tree.selection()
        if not sel or sel[0] == "__empty__":
            return
        vals = self.local_runs_tree.item(sel[0], "values")
        if vals and vals[0] in self.pub_combo["values"]:
            self.pub_slug_var.set(str(vals[0]))

    def _resume_selected_local_run(self) -> None:
        sel = self.local_runs_tree.selection()
        if not sel or sel[0] == "__empty__":
            self.messagebox.showinfo("Select Run", "Select a local run in Step 3 first.")
            return
        slug = str(self.local_runs_tree.item(sel[0], "values")[0])
        meta_path = RUNS / slug / "meta.json"
        saved_spec = ""
        saved_base_url = ""
        if meta_path.is_file():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                saved_spec = str(meta.get("spec") or "")
                saved_base_url = str(meta.get("base_url") or "")
            except Exception:
                pass
        if not self._run_connection_ready:
            if saved_spec:
                provider = saved_spec.partition(":")[0] if ":" in saved_spec else "openai"
                self.run_provider_var.set(provider)
            self.run_base_url_var.set(saved_base_url)
            self.run_model_var.set("")
            self.run_fresh_var.set(False)
            self._append_log(
                "[HINT] Test the saved endpoint, select its advertised model, then resume the run.\n",
                "warn",
            )
            return
        if saved_spec not in self._run_model_specs:
            self._append_log(
                "[ERR] The saved model is not advertised by the tested endpoint. Test the correct endpoint before resuming.\n",
                "err",
            )
            return
        self.run_model_var.set(saved_spec)
        self.run_fresh_var.set(False)
        self._start_local_run()

    def _delete_selected_local_run(self) -> None:
        sel = self.local_runs_tree.selection()
        if not sel or sel[0] == "__empty__":
            self.messagebox.showinfo("Select Run", "Select a local run in Step 3 first.")
            return
        slug = str(self.local_runs_tree.item(sel[0], "values")[0])
        target = RUNS / slug
        if not target.is_dir():
            self.messagebox.showinfo("Delete Run", f"Run folder '{slug}' does not exist.")
            return
        if not self.messagebox.askyesno(
            "Delete Local Run",
            f"Are you sure you want to delete local run '{slug}' from runs/?\n\n"
            f"This will remove its checkpoints and raw outputs from disk.\n"
            f"(It will NOT affect any published scorecards in submissions/)."
        ):
            return
        import shutil
        shutil.rmtree(target, ignore_errors=True)
        self._append_log(f"[OK] Deleted local run '{slug}' from runs/.\n", "ok")
        self._populate_local_runs_table()
        available = (
            sorted(d.name for d in RUNS.iterdir() if d.is_dir() and (d / "submission.csv").is_file())
            if RUNS.is_dir()
            else []
        )
        self.pub_combo["values"] = available
        if self.pub_slug_var.get() == slug:
            self.pub_slug_var.set(available[0] if available else "")

    def _clear_run_model_selection(self) -> None:
        self._run_connection_ready = False
        self._run_model_specs = []
        self.run_model_combo.configure(values=(), state="disabled")
        self.run_model_var.set("")
        self.run_start_btn.configure(state="disabled")
        self._update_reasoning_button_state()

    def _on_run_connection_input_changed(self) -> None:
        self._clear_run_model_selection()
        self.run_probe_var.set("Connection details changed — test again")
        self.run_probe_label.configure(fg=PALETTE["muted"])
        self._update_run_preview()

    def _effective_reasoning_effort(self, model: str) -> str:
        choice = (
            self.run_reasoning_var.get().strip().lower()
            if hasattr(self, "run_reasoning_var")
            else "high"
        )
        if choice in ("low", "medium", "high", "off", "none"):
            return choice if choice not in ("off", "none") else "off"
        if self._run_reasoning_effort and self._run_reasoning_model == model:
            return self._run_reasoning_effort
        return "high"

    def _update_run_preview(self) -> None:
        model = self.run_model_var.get().strip() if hasattr(self, "run_model_var") else ""
        args = build_run_args(
            model=model,
            judge=self.run_judge_var.get() if hasattr(self, "run_judge_var") else "",
            modality=self.run_modality_var.get() if hasattr(self, "run_modality_var") else "text",
            pillars=self.run_pillars_var.get() if hasattr(self, "run_pillars_var") else "all",
            fresh=self.run_fresh_var.get() if hasattr(self, "run_fresh_var") else False,
            base_url=self.run_base_url_var.get() if hasattr(self, "run_base_url_var") else "",
            api_key=self.run_api_key_var.get() if hasattr(self, "run_api_key_var") else "",
            reasoning_effort=self._effective_reasoning_effort(model),
        )
        if hasattr(self, "run_preview_var"):
            self.run_preview_var.set(format_cmd_preview(args))

    def _on_run_model_changed(self) -> None:
        model = self.run_model_var.get().strip()
        if model != self._run_reasoning_model:
            self._run_reasoning_probe_generation += 1
            self._run_reasoning_effort = None
            self._run_reasoning_model = ""
            self._reasoning_test_running = False
            if hasattr(self, "run_reasoning_var"):
                self.run_reasoning_var.set("high")
                self.run_reasoning_status_var.set(
                    f"Model selected ({model}); reasoning effort set to High (default)."
                    if model
                    else "Reasoning effort: High (default)."
                )
            elif hasattr(self, "run_reasoning_status_var"):
                self.run_reasoning_status_var.set("Reasoning effort: High (default).")
        self._update_reasoning_button_state()
        self._update_run_preview()

    def _update_reasoning_button_state(self) -> None:
        if not hasattr(self, "run_reasoning_btn"):
            return
        model = self.run_model_var.get().strip()
        enabled = (
            self._run_connection_ready
            and bool(model)
            and not self._reasoning_test_running
        )
        self.run_reasoning_btn.configure(state="normal" if enabled else "disabled")

    def _test_run_reasoning(self) -> None:
        model_spec = self.run_model_var.get().strip()
        if not self._run_connection_ready or not model_spec:
            self._append_log(
                "[ERR] Test the connection and select an advertised model before testing reasoning.\n",
                "err",
            )
            return
        self._run_reasoning_effort = None
        self._run_reasoning_model = model_spec
        self._run_reasoning_probe_generation += 1
        probe_generation = self._run_reasoning_probe_generation
        self._reasoning_test_running = True
        self.run_reasoning_status_var.set("Testing high reasoning usage...")
        self._update_reasoning_button_state()
        base_url = self.run_base_url_var.get()
        api_key = self.run_api_key_var.get()
        self._append_log(
            f"[..] Testing high reasoning usage for {model_spec} ...\n",
            "cmd",
        )

        def worker() -> None:
            probe = probe_reasoning(
                model_spec=model_spec,
                base_url=base_url,
                api_key=api_key,
            )
            self._log_queue.put(
                ("reasoning_probe", (model_spec, probe_generation, probe))
            )

        threading.Thread(target=worker, daemon=True).start()

    def _apply_reasoning_probe_result(
        self,
        model_spec: str,
        probe_generation: int,
        probe: ReasoningProbe,
    ) -> None:
        if (
            probe_generation != self._run_reasoning_probe_generation
            or model_spec != self.run_model_var.get().strip()
        ):
            return
        self._reasoning_test_running = False
        self._run_reasoning_model = model_spec
        if probe.confirmed:
            self._run_reasoning_effort = "high"
            if hasattr(self, "run_reasoning_var"):
                self.run_reasoning_var.set("high")
            self.run_reasoning_status_var.set(probe.detail)
            self._append_log(f"[OK] Reasoning test: {probe.detail}\n", "ok")
        else:
            self._run_reasoning_effort = None
            if hasattr(self, "run_reasoning_var"):
                self.run_reasoning_var.set("off")
            self.run_reasoning_status_var.set(probe.detail)
            self._append_log(f"[WARN] Reasoning test: {probe.detail}\n", "warn")
        self._update_reasoning_button_state()
        self._update_run_preview()

    def _update_judge_call_estimate(self) -> None:
        selected = self.run_pillars_var.get().strip()
        pillars = (
            list_pillars()
            if not selected or selected.lower() == "all"
            else [pillar.strip() for pillar in selected.split(",") if pillar.strip()]
        )
        known = set(list_pillars())
        unknown = [pillar for pillar in pillars if pillar not in known]
        if unknown:
            self.run_judge_count_var.set(
                f"Judge-call estimate unavailable: unknown pillar(s): {', '.join(unknown)}"
            )
            return
        modality = self.run_modality_var.get().strip().lower()
        modalities = ("text", "vision", "audio") if modality == "all" else (modality,)
        try:
            count = count_judge_calls(pillars, list(modalities))
        except (OSError, ValueError) as exc:
            self.run_judge_count_var.set(f"Could not estimate judge calls: {exc}")
            return
        self.run_judge_count_var.set(
            f"Estimated judge API calls: {count} "
            "(one per llm_judge row; resumes or model failures can reduce the actual count)"
        )

    def _on_run_provider_selected(self, _event: Any = None) -> None:
        provider = self.run_provider_var.get().strip().lower()
        env_name = _API_KEY_ENV_BY_PROVIDER.get(provider, "SYNHALEES_API_KEY")
        self.run_api_key_var.set(os.environ.get(env_name, ""))

    def _refresh_judge_providers(self) -> None:
        if not hasattr(self, "run_judge_provider_combo"):
            return
        try:
            providers = saved_judge_providers()
        except RuntimeError as exc:
            providers = []
            self._append_log(f"[ERR] {exc}\n", "err")
            self.run_judge_status_var.set("Could not read saved API keys. Check the console log.")
        else:
            self.run_judge_status_var.set(
                "Choose a provider to load its judge models."
                if providers
                else "Save an API key in Settings → Tokens & Keys to enable judge providers."
            )
        self.run_judge_provider_combo.configure(
            values=providers,
            state="readonly" if providers else "disabled",
        )
        preferred = os.environ.get(_JUDGE_PROVIDER_PREF, "").strip().lower()
        current = self.run_judge_provider_var.get().strip().lower()
        selected = (
            preferred
            if preferred in providers
            else current
            if current in providers
            else providers[0]
            if providers
            else ""
        )
        self.run_judge_provider_var.set(selected)
        self.run_judge_model_combo.configure(values=(), state="disabled")
        self.run_judge_var.set("")
        if selected:
            self._on_judge_provider_selected()

    def _save_judge_selection(self, provider: str, model: str = "") -> None:
        try:
            apply_tokens(
                api_keys={
                    _JUDGE_PROVIDER_PREF: provider,
                    _JUDGE_MODEL_PREF: model,
                }
            )
        except OSError as exc:
            self._append_log(f"[ERR] Could not save judge selection locally: {exc}\n", "err")

    def _on_judge_provider_selected(self, _event: Any = None) -> None:
        provider = self.run_judge_provider_var.get().strip().lower()
        self.run_judge_var.set("")
        self.run_judge_model_combo.configure(values=(), state="disabled")
        if not provider:
            self.run_judge_status_var.set(
                "Save an API key in Settings → Tokens & Keys to enable judge providers."
            )
            return
        previous_provider = os.environ.get(_JUDGE_PROVIDER_PREF, "").strip().lower()
        if provider != previous_provider:
            self._save_judge_selection(provider)
        try:
            providers = saved_judge_providers()
        except RuntimeError as exc:
            self.run_judge_status_var.set("Could not read saved API keys. Check the console log.")
            self._append_log(f"[ERR] {exc}\n", "err")
            return
        if provider not in providers:
            self.run_judge_status_var.set(
                f"Save the {provider} API key in Settings → Tokens & Keys first."
            )
            return
        key_env = _API_KEY_ENV_BY_PROVIDER[provider]
        api_key = os.environ.get(key_env, "").strip()
        if not api_key:
            self.run_judge_status_var.set(
                f"Save the {provider} API key in Settings → Tokens & Keys first."
            )
            return

        self.run_judge_status_var.set(f"Loading {provider} models...")

        def worker() -> None:
            probe = probe_endpoint(provider=provider, api_key=api_key)
            self._log_queue.put(("judge_probe", (provider, probe)))

        threading.Thread(target=worker, daemon=True).start()

    def _on_judge_model_selected(self, _event: Any = None) -> None:
        provider = self.run_judge_provider_var.get().strip().lower()
        model = self.run_judge_var.get().strip()
        if provider and model:
            self._save_judge_selection(provider, model)
            self._test_judge_model()

    def _test_judge_model(self) -> None:
        jspec = self.run_judge_var.get().strip()
        if not jspec:
            self.run_judge_status_var.set("Select a judge model first.")
            return
        provider = jspec.partition(":")[0].lower()
        key_env = _API_KEY_ENV_BY_PROVIDER.get(provider, "")
        api_key = os.environ.get(key_env, "").strip() if key_env else ""
        self.run_judge_status_var.set(f"Testing judge model {jspec}...")
        self._append_log(f"Testing judge model {jspec}...\n", "cmd")

        def worker() -> None:
            probe = probe_judge(judge_spec=jspec, api_key=api_key)
            self._log_queue.put(("judge_test_result", (jspec, probe)))

        threading.Thread(target=worker, daemon=True).start()

    def _apply_judge_test_result(self, jspec: str, probe: Any) -> None:
        if jspec != self.run_judge_var.get().strip():
            return
        if getattr(probe, "ok", False):
            self.run_judge_status_var.set(f"✓ {probe.summary()}")
            self._append_log(f"[OK] {probe.summary()}\n", "ok")
        else:
            self.run_judge_status_var.set(f"⚠ {probe.summary()}")
            self._append_log(f"[ERR] {probe.summary()}\n", "err")

    def _apply_judge_probe_result(self, provider: str, probe: EndpointProbe) -> None:
        if provider != self.run_judge_provider_var.get().strip().lower():
            return
        model_ids = list(dict.fromkeys(model.strip() for model in probe.models if model.strip()))
        model_specs = [f"{provider}:{model}" for model in model_ids] if probe.ok else []
        self.run_judge_model_combo.configure(
            values=model_specs,
            state="readonly" if model_specs else "disabled",
        )
        if model_specs:
            preferred_model = os.environ.get(_JUDGE_MODEL_PREF, "").strip()
            if preferred_model in model_specs:
                selected_model = preferred_model
            else:
                fast_candidates = [
                    m for m in model_specs
                    if any(token in m.lower() for token in ("flash", "mini", "haiku"))
                ]
                selected_model = fast_candidates[0] if fast_candidates else model_specs[0]
            self.run_judge_var.set(selected_model)
            self._save_judge_selection(provider, selected_model)
            self.run_judge_status_var.set(f"{len(model_specs)} {provider} judge model(s) available.")
            return
        if probe.ok:
            self.run_judge_status_var.set(
                "Connected, but this provider did not advertise any judge models."
            )
        else:
            self.run_judge_status_var.set(
                f"Could not load {provider} models. Save/check its API key in Settings → Tokens & Keys."
            )
            self._append_log(f"[ERR] Judge provider: {probe.summary()}\n", "err")

    def _ensure_kaggle_auth(self, then_call: Callable[[], None]) -> bool:
        if kaggle_auth_status() != "Not configured":
            return True
        self._open_tokens_dialog(
            reason="Kaggle credentials (KAGGLE_API_TOKEN or kaggle.json) are required to continue.",
            on_saved=then_call,
        )
        return False

    def _ensure_local_api_key(self, model_spec: str, judge_spec: str, then_call: Callable[[], None]) -> bool:
        # Self-hosted endpoints (Colab/vLLM, llama.cpp) take an optional key, so
        # never block the run on a cloud API key when a Base URL is set.
        if self.run_base_url_var.get().strip():
            return True
        for spec in (model_spec, judge_spec):
            prov = spec.split(":", 1)[0].strip().lower() if ":" in spec else ""
            env_var = _API_KEY_ENV_BY_PROVIDER.get(prov)
            if env_var and not os.environ.get(env_var, "").strip():
                self._open_tokens_dialog(
                    reason=f"{env_var} is required before running '{spec}'.",
                    on_saved=then_call,
                )
                return False
        return True

    def _start_local_run(self) -> None:
        if not self._run_connection_ready:
            self._append_log(
                "[ERR] Test the connection and select an advertised model before starting a run.\n",
                "err",
            )
            return
        mspec = self.run_model_var.get().strip()
        jspec = self.run_judge_var.get().strip()
        if not mspec:
            self._append_log("[ERR] Select a model advertised by the tested endpoint.\n", "err")
            return
        if not self._ensure_local_api_key(mspec, jspec, self._start_local_run):
            return
        args = build_run_args(
            model=mspec,
            judge=jspec,
            modality=self.run_modality_var.get(),
            pillars=self.run_pillars_var.get(),
            fresh=self.run_fresh_var.get(),
            base_url=self.run_base_url_var.get(),
            api_key=self.run_api_key_var.get(),
            reasoning_effort=self._effective_reasoning_effort(mspec),
            gui_item_events=True,
        )
        self._open_eval_item_window()
        self._start_worker_if_free(
            lambda: self.run_cli(args)
        )

    def _open_eval_item_window(self) -> None:
        tk, ttk = self.tk, self.ttk
        if self._eval_detail_window is not None and self._eval_detail_window.winfo_exists():
            window = self._eval_detail_window
        else:
            window = tk.Toplevel(self.root)
            window.withdraw()
            self._eval_detail_window = window
            window.title("Live Benchmark Item")
            window.configure(bg=PALETTE["surface"])
            window.transient(self.root)
            window.minsize(self._px(620), self._px(620))
            self._center_dialog(window, self._px(760), self._px(780))

            wrap = tk.Frame(
                window,
                bg=PALETTE["surface"],
                padx=self._px(16),
                pady=self._px(14),
            )
            wrap.pack(fill="both", expand=True)
            tk.Label(
                wrap,
                text="Live Benchmark Check",
                bg=PALETTE["surface"],
                fg=PALETTE["text"],
                font=("Segoe UI", 13, "bold"),
            ).pack(anchor="w")
            self._eval_detail_item_var = tk.StringVar(value="Waiting for first item…")
            tk.Label(
                wrap,
                textvariable=self._eval_detail_item_var,
                bg=PALETTE["surface"],
                fg=PALETTE["muted"],
                font=("Segoe UI", 9),
                anchor="w",
            ).pack(fill="x", pady=(self._px(5), self._px(8)))

            self._eval_detail_status_var = tk.StringVar(value="Starting benchmark…")
            self._eval_detail_status_label = tk.Label(
                wrap,
                textvariable=self._eval_detail_status_var,
                bg=PALETTE["surface"],
                fg=PALETTE["info"],
                font=("Segoe UI", 10, "bold"),
                anchor="w",
            )
            self._eval_detail_status_label.pack(fill="x", pady=(0, self._px(10)))

            self._eval_detail_text_widgets = {}
            for key, title, height in (
                ("prompt", "Prompt", 6),
                ("expected", "Expected answer", 4),
                ("response", "Model response", 7),
            ):
                tk.Label(
                    wrap,
                    text=title,
                    bg=PALETTE["surface"],
                    fg=PALETTE["muted"],
                    font=("Segoe UI", 9, "bold"),
                    anchor="w",
                ).pack(fill="x", pady=(self._px(4), self._px(3)))
                text_frame = tk.Frame(wrap, bg=PALETTE["surface"])
                text_frame.pack(fill="both", expand=key == "response")
                text_widget = tk.Text(
                    text_frame,
                    height=height,
                    wrap="word",
                    state="disabled",
                    bg=PALETTE["console_bg"],
                    fg=PALETTE["text"],
                    insertbackground=PALETTE["text"],
                    relief="flat",
                    padx=self._px(8),
                    pady=self._px(6),
                    font=("Segoe UI", 9),
                )
                scrollbar = ttk.Scrollbar(
                    text_frame, orient="vertical", command=text_widget.yview
                )
                text_widget.configure(yscrollcommand=scrollbar.set)
                text_widget.pack(side="left", fill="both", expand=True)
                scrollbar.pack(side="right", fill="y")
                self._eval_detail_text_widgets[key] = text_widget

            nav = tk.Frame(wrap, bg=PALETTE["surface"])
            nav.pack(fill="x", pady=(self._px(10), 0))
            self._eval_detail_prev_btn = ttk.Button(
                nav,
                text="← Back",
                style="Ghost.TButton",
                command=self._show_previous_eval_item,
            )
            self._eval_detail_prev_btn.pack(side="left")
            self._eval_detail_nav_var = tk.StringVar(value="No items yet")
            tk.Label(
                nav,
                textvariable=self._eval_detail_nav_var,
                bg=PALETTE["surface"],
                fg=PALETTE["muted"],
                font=("Segoe UI", 9),
            ).pack(side="left", expand=True)
            self._eval_detail_next_btn = ttk.Button(
                nav,
                text="Next →",
                style="Ghost.TButton",
                command=self._show_next_eval_item,
            )
            self._eval_detail_next_btn.pack(side="right")

            window.protocol("WM_DELETE_WINDOW", self._close_eval_item_window)

        self._eval_detail_seen_item = False
        self._eval_detail_items = []
        self._eval_detail_item_indexes = {}
        self._eval_detail_index = -1
        self._eval_detail_active_key = None
        self._eval_detail_item_var.set("Waiting for first item…")
        self._set_eval_detail_text("prompt", "")
        self._set_eval_detail_text("expected", "")
        self._set_eval_detail_text("response", "Waiting for model response…")
        self._set_eval_detail_status("Starting benchmark…", PALETTE["info"])
        self._update_eval_detail_nav()
        window.deiconify()
        window.lift()

    def _close_eval_item_window(self) -> None:
        if self._eval_detail_window is not None:
            self._eval_detail_window.destroy()
        self._eval_detail_window = None
        self._eval_detail_text_widgets = {}
        self._eval_detail_items = []
        self._eval_detail_item_indexes = {}
        self._eval_detail_index = -1
        self._eval_detail_active_key = None

    def _update_eval_detail_nav(self) -> None:
        if self._eval_detail_window is None or not self._eval_detail_window.winfo_exists():
            return
        count = len(self._eval_detail_items)
        if self._eval_detail_index < 0:
            self._eval_detail_nav_var.set("No items yet")
        else:
            self._eval_detail_nav_var.set(
                f"Item {self._eval_detail_index + 1} of {count}"
            )
        self._eval_detail_prev_btn.configure(
            state="normal" if self._eval_detail_index > 0 else "disabled"
        )
        self._eval_detail_next_btn.configure(
            state="normal"
            if 0 <= self._eval_detail_index < count - 1
            else "disabled"
        )

    def _show_eval_item(self, index: int) -> None:
        if not 0 <= index < len(self._eval_detail_items):
            return
        self._eval_detail_index = index
        item = self._eval_detail_items[index]
        self._eval_detail_item_var.set(
            f"[{item['progress_index']}/{item['progress_total']}] {item['key']}"
        )
        self._set_eval_detail_text("prompt", item["prompt"])
        self._set_eval_detail_text("expected", item["expected"])
        self._set_eval_detail_text("response", item["response"])
        self._set_eval_detail_status(item["status"], item["status_color"])
        self._update_eval_detail_nav()

    def _show_previous_eval_item(self) -> None:
        self._show_eval_item(self._eval_detail_index - 1)

    def _show_next_eval_item(self) -> None:
        self._show_eval_item(self._eval_detail_index + 1)

    def _set_eval_detail_text(self, key: str, value: str) -> None:
        if self._eval_detail_window is None or not self._eval_detail_window.winfo_exists():
            return
        widget = self._eval_detail_text_widgets.get(key)
        if widget is None:
            return
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", value)
        widget.configure(state="disabled")

    def _set_eval_detail_status(self, message: str, color: str) -> None:
        if self._eval_detail_window is None or not self._eval_detail_window.winfo_exists():
            return
        self._eval_detail_status_var.set(message)
        self._eval_detail_status_label.configure(fg=color)

    def _apply_eval_detail_event(self, event: dict[str, Any]) -> None:
        if self._eval_detail_window is None or not self._eval_detail_window.winfo_exists():
            return
        phase = event.get("phase")
        if phase == "item_start":
            self._eval_detail_seen_item = True
            key = str(event.get("key", ""))
            item = {
                "key": key,
                "progress_index": event.get("index", "?"),
                "progress_total": event.get("total", "?"),
                "prompt": str(event.get("prompt", "")),
                "expected": str(event.get("expected", "")),
                "response": "Waiting for model response…",
                "status": "Waiting for model response…",
                "status_color": PALETTE["info"],
            }
            self._eval_detail_item_indexes[key] = len(self._eval_detail_items)
            self._eval_detail_items.append(item)
            self._eval_detail_active_key = key
            if self._eval_detail_index < 0:
                self._show_eval_item(0)
            else:
                self._update_eval_detail_nav()
            return

        key = str(event.get("key", ""))
        index = self._eval_detail_item_indexes.get(key)
        if index is None:
            return
        item = self._eval_detail_items[index]
        if phase == "model_response":
            response = str(event.get("response", ""))
            clean_resp = strip_thinking(response)
            item["response"] = (
                clean_resp if clean_resp else "(Model returned an empty response.)"
            )
            item["status"] = "Response received; scoring…"
            item["status_color"] = PALETTE["info"]
        elif phase == "judge_waiting":
            item["status"] = "Response received; waiting for judge verdict…"
            item["status_color"] = PALETTE["info"]
        elif phase == "item_result":
            error = str(event.get("error", "")).strip()
            if error:
                item["response"] = f"Model request failed:\n{error}"
                item["status"] = "FAILED — model request error"
                item["status_color"] = PALETTE["accent"]
            else:
                passed = bool(event.get("is_correct"))
                reason = str(event.get("judge_reason", "")).strip()
                status = "PASSED" if passed else "FAILED"
                if reason:
                    status += f" — {reason}"
                item["status"] = status[:240]
                item["status_color"] = (
                    PALETTE["ok"] if passed else PALETTE["accent"]
                )
            if self._eval_detail_active_key == key:
                self._eval_detail_active_key = None
        if index == self._eval_detail_index:
            self._show_eval_item(index)

    def _test_local_connection(self) -> None:
        """Probe the configured endpoint (no tokens) and report in the log."""
        provider = self.run_provider_var.get().strip().lower()
        if not provider:
            self._append_log("[ERR] Enter a provider prefix before testing the connection.\n", "err")
            return
        if provider != self.run_provider_var.get():
            self.run_provider_var.set(provider)
        base_url = self.run_base_url_var.get().strip()
        api_key = self.run_api_key_var.get().strip()
        key_env = _API_KEY_ENV_BY_PROVIDER.get(provider, "SYNHALEES_API_KEY")
        try:
            notes = apply_tokens(
                api_keys={
                    "SYNHALEES_PROVIDER": provider,
                    "SYNHALEES_BASE_URL": base_url,
                    key_env: api_key,
                }
            )
        except OSError as exc:
            self._append_log(f"[ERR] Could not save endpoint settings locally: {exc}\n", "err")
            return
        for note in notes:
            self._append_log(f"[OK] {note}\n", "ok")
        self._refresh_judge_providers()
        target = base_url or provider or "endpoint"
        self._clear_run_model_selection()
        self.run_probe_var.set(f"Testing {target} ...")
        self.run_probe_label.configure(fg=PALETTE["info"])
        self.run_probe_btn.configure(state="disabled")
        for widget in self._run_connection_inputs:
            widget.configure(state="disabled")
        self._append_log(f"[..] Testing connection to {target} ...\n", "cmd")

        def worker() -> None:
            probe = probe_endpoint(
                provider=provider,
                base_url=base_url,
                api_key=api_key,
            )
            self._log_queue.put(("probe", probe))

        threading.Thread(target=worker, daemon=True).start()

    def _apply_probe_result(self, probe: EndpointProbe) -> None:
        """Render a finished connection probe (called on the Tk main thread)."""
        self.run_probe_btn.configure(state="normal")
        for widget in self._run_connection_inputs:
            widget.configure(state="normal")
        self._clear_run_model_selection()
        if probe.ok:
            provider = self.run_provider_var.get().strip().lower()
            model_ids = list(dict.fromkeys(model.strip() for model in probe.models if model.strip()))
            model_specs = [f"{provider}:{model}" for model in model_ids]
            if model_specs:
                self._run_model_specs = model_specs
                self.run_model_combo.configure(values=model_specs, state="readonly")
                self.run_model_var.set(model_specs[0])
                self._run_connection_ready = True
                self.run_start_btn.configure(state="normal")
                self.run_probe_var.set(f"Connected · {probe.latency_ms} ms · {len(model_specs)} model(s)")
            else:
                self.run_probe_var.set(f"Connected · {probe.latency_ms} ms · no models advertised")
            self.run_probe_label.configure(fg=PALETTE["ok"])
            self._append_log(f"[OK] Connection test: {probe.summary()}\n", "ok")
            if not model_specs:
                self._append_log(
                    "[HINT] The endpoint responded but advertised no model IDs; model selection and benchmark runs remain disabled.\n",
                    "warn",
                )
            self._update_reasoning_button_state()
        else:
            reason = probe.detail or "unknown error"
            if probe.hint:
                reason = f"{reason} — {probe.hint}"
            self.run_probe_var.set(f"Failed · {_shorten(reason, 78)}")
            self.run_probe_label.configure(fg=PALETTE["accent"])
            self._append_log(f"[ERR] Connection test: {probe.summary()}\n", "err")
            # The full reason lives in the log; surface it instead of hiding it.
            if not self._console_visible:
                self._toggle_console()
        if probe.hint and (not probe.ok or probe.models):
            self._append_log(f"[HINT] {probe.hint}\n", "warn")

    def _start_worker_if_free(self, fn: Callable[[], None]) -> None:
        fn()

    def _publish_selected_run(self) -> None:
        slug = self.pub_slug_var.get().strip()
        if not slug:
            self._append_log("[WARN] No local run selected under runs/.\n", "warn")
            return
        args = ["site", "publish", slug]
        if self.pub_dry_var.get():
            args.append("--dry-run")
        unreg = find_unregistered_models([slug])
        if unreg and not self.pub_dry_var.get():
            self._open_edit_models_dialog(
                only_slugs=set(unreg),
                target_slugs=unreg,
                on_saved=lambda: self.run_cli(args),
            )
            return
        self.run_cli(args)

    def _open_leaderboard(self) -> None:
        index_html = ROOT / "docs" / "index.html"
        if index_html.is_file():
            webbrowser.open(index_html.resolve().as_uri())
            self._append_log(f"[OK] Opened {index_html}\n", "ok")
        else:
            self._append_log(f"[ERR] Missing {index_html}\n", "err")

    def _open_tokens_dialog(
        self,
        *,
        reason: str = "",
        on_saved: Callable[[], None] | None = None,
    ) -> None:
        """Open a modal dialog to configure Kaggle tokens & Local Eval API keys."""
        tk, ttk = self.tk, self.ttk
        dlg = tk.Toplevel(self.root)
        dlg.title("Tokens & API Keys")
        dlg.configure(bg=PALETTE["surface"])
        dlg.transient(self.root)
        dlg.grab_set()
        w, h = self._px(540), self._px(500)
        dlg.minsize(self._px(460), self._px(430))
        self._center_dialog(dlg, w, h)

        wrap = tk.Frame(dlg, bg=PALETTE["surface"], padx=self._px(18), pady=self._px(14))
        wrap.pack(fill="both", expand=True)

        tk.Label(
            wrap,
            text="Tokens & API Keys (Saved Locally)",
            bg=PALETTE["surface"],
            fg=PALETTE["text"],
            font=("Segoe UI", 12, "bold"),
        ).pack(anchor="w")
        sub_txt = reason or f"Kaggle Auth: {kaggle_auth_status()}  ·  Paste KAGGLE_API_TOKEN (KGAT_...) or kaggle.json"
        tk.Label(
            wrap,
            text=sub_txt,
            bg=PALETTE["surface"],
            fg=PALETTE["warn"] if reason else PALETTE["info"],
            font=("Segoe UI", 9),
        ).pack(anchor="w", pady=(2, self._px(10)))

        show_var = tk.BooleanVar(value=False)
        k_tok_var = tk.StringVar(value=os.environ.get("KAGGLE_API_TOKEN", ""))
        k_user_var = tk.StringVar(value=os.environ.get("KAGGLE_USERNAME", ""))
        k_key_var = tk.StringVar(value=os.environ.get("KAGGLE_KEY", ""))
        save_json_var = tk.BooleanVar(value=False)

        api_vars = {
            "GEMINI_API_KEY": tk.StringVar(value=os.environ.get("GEMINI_API_KEY", "")),
            "OPENAI_API_KEY": tk.StringVar(value=os.environ.get("OPENAI_API_KEY", "")),
            "ANTHROPIC_API_KEY": tk.StringVar(value=os.environ.get("ANTHROPIC_API_KEY", "")),
            "OPENROUTER_API_KEY": tk.StringVar(value=os.environ.get("OPENROUTER_API_KEY", "")),
            "SYNHALEES_API_KEY": tk.StringVar(value=os.environ.get("SYNHALEES_API_KEY", "")),
        }

        form = tk.Frame(wrap, bg=PALETTE["card"], padx=self._px(12), pady=self._px(10))
        form.pack(fill="x", pady=(0, self._px(10)))
        form.columnconfigure(1, weight=1)

        secret_entries: list[Any] = []

        def _row(r: int, label: str, var: Any, secret: bool = True) -> None:
            tk.Label(
                form,
                text=label,
                bg=PALETTE["card"],
                fg=PALETTE["text"],
                font=("Segoe UI", 9),
            ).grid(row=r, column=0, sticky="w", padx=(0, 10), pady=4)
            ent = ttk.Entry(form, textvariable=var, show="*" if secret else "")
            ent.grid(row=r, column=1, sticky="ew", pady=4)
            if secret:
                secret_entries.append(ent)

        _row(0, "KAGGLE_API_TOKEN", k_tok_var, secret=True)
        _row(1, "KAGGLE_USERNAME (opt)", k_user_var, secret=False)
        _row(2, "KAGGLE_KEY (opt)", k_key_var, secret=True)
        ttk.Checkbutton(
            form,
            text="Also save username + key to ~/.kaggle/kaggle.json",
            variable=save_json_var,
        ).grid(row=3, column=1, sticky="w", pady=(2, 6))

        _row(4, "GEMINI_API_KEY", api_vars["GEMINI_API_KEY"], secret=True)
        _row(5, "OPENAI_API_KEY", api_vars["OPENAI_API_KEY"], secret=True)
        _row(6, "ANTHROPIC_API_KEY", api_vars["ANTHROPIC_API_KEY"], secret=True)
        _row(7, "OPENROUTER_API_KEY", api_vars["OPENROUTER_API_KEY"], secret=True)
        _row(8, "SYNHALEES_API_KEY (custom)", api_vars["SYNHALEES_API_KEY"], secret=True)

        def _toggle_show() -> None:
            mask = "" if show_var.get() else "*"
            for ent in secret_entries:
                ent.configure(show=mask)

        btns = tk.Frame(wrap, bg=PALETTE["surface"])
        btns.pack(fill="x")
        ttk.Checkbutton(
            btns,
            text="Show tokens",
            variable=show_var,
            command=_toggle_show,
            style="Surface.TCheckbutton",
        ).pack(side="left")

        def _save_and_close() -> None:
            if not k_tok_var.get().strip():
                os.environ.pop("KAGGLE_API_TOKEN", None)
            if not k_user_var.get().strip():
                os.environ.pop("KAGGLE_USERNAME", None)
            if not k_key_var.get().strip():
                os.environ.pop("KAGGLE_KEY", None)
            notes = apply_tokens(
                kaggle_token=k_tok_var.get(),
                kaggle_username=k_user_var.get(),
                kaggle_key=k_key_var.get(),
                save_kaggle_json=save_json_var.get(),
                api_keys={k: v.get() for k, v in api_vars.items()},
            )
            if notes:
                for n in notes:
                    self._append_log(f"[OK] {n}\n", "ok")
            else:
                self._append_log("[WARN] No tokens saved.\n", "warn")
            self._sync_token_button()
            self._refresh_judge_providers()
            dlg.destroy()
            if on_saved is not None:
                self.root.after(10, on_saved)

        def _clear_kaggle_token() -> None:
            k_tok_var.set("")
            k_user_var.set("")
            k_key_var.set("")
            os.environ.pop("KAGGLE_API_TOKEN", None)
            os.environ.pop("KAGGLE_USERNAME", None)
            os.environ.pop("KAGGLE_KEY", None)
            apply_tokens(
                kaggle_token="",
                kaggle_username="",
                kaggle_key="",
                api_keys={k: v.get() for k, v in api_vars.items()},
            )
            self._sync_token_button()
            self._refresh_judge_providers()
            self._append_log("[OK] Cleared saved Kaggle token overrides (will use ~/.kaggle/kaggle.json if present).\n", "ok")
            dlg.destroy()

        ttk.Button(btns, text="Cancel", style="Ghost.TButton", command=dlg.destroy).pack(side="right")
        ttk.Button(btns, text="Clear Kaggle", style="Ghost.TButton", command=_clear_kaggle_token).pack(
            side="right", padx=(0, 8)
        )
        ttk.Button(
            btns,
            text="Save & Continue ->" if on_saved else "Save Tokens",
            style="Accent.TButton",
            command=_save_and_close,
        ).pack(side="right", padx=(0, 8))
        dlg.bind("<Escape>", lambda _e: dlg.destroy())

    def _center_dialog(self, dlg: Any, w: int, h: int) -> None:
        """Center a modal dialog on the main Studio window."""
        self.root.update_idletasks()
        rx = self.root.winfo_rootx()
        ry = self.root.winfo_rooty()
        rw = max(self.root.winfo_width(), w)
        rh = max(self.root.winfo_height(), h)
        x = max(0, rx + (rw - w) // 2)
        y = max(0, ry + (rh - h) // 2)
        dlg.geometry(f"{w}x{h}+{x}+{y}")

    def _guard_and_run_kaggle(self, action: str, cli_args: list[str]) -> None:
        """Validate auth & model registration before proceeding, auto-resuming on save."""
        if action in ("pull", "run", "publish", "logs") and "--dry-run" not in cli_args:
            if not self._ensure_kaggle_auth(lambda: self._guard_and_run_kaggle(action, cli_args)):
                return
        if action in ("pull", "submit", "run", "publish"):
            selected = [m.strip() for m in re.split(r"[,\s]+", self.kg_model_var.get().strip()) if m.strip()]
            if action == "run" and not selected:
                self.kg_discover_status_var.set("Select a model on the right before running on Kaggle.")
                self._append_log("[WARN] Select a model on the right before running on Kaggle.\n", "warn")
                return
            check_list = list(selected)
            if not check_list and hasattr(self, "kg_new_tree"):
                check_list = [
                    str(self.kg_new_tree.item(i, "values")[0])
                    for i in self.kg_new_tree.get_children()
                    if self.kg_new_tree.item(i, "values") and i != "__empty__"
                ]
            unreg = find_unregistered_models(check_list)
            if action == "submit":
                for slim_unreg in find_unregistered_slim_models(self.kg_task_var.get()):
                    if slim_unreg not in unreg:
                        unreg.append(slim_unreg)
            if unreg:
                msg = f"Complete Canonical, Organization & Open Source for: {', '.join(unreg)}"
                self.kg_discover_status_var.set(msg)
                self._append_log(f"[WARN] {msg}\n", "warn")
                self._open_edit_models_dialog(
                    only_slugs=set(unreg),
                    target_slugs=unreg,
                    on_saved=lambda: self._guard_and_run_kaggle(action, cli_args),
                )
                return
        self.run_cli(cli_args)

    def _open_edit_models_dialog(
        self,
        *,
        only_slugs: set[str] | None = None,
        target_slugs: list[str] | None = None,
        on_saved: Callable[[], None] | None = None,
    ) -> None:
        """Centered dialog to edit Canonical, Display Name, Org (+ New Org + SVG), Family & OSS."""
        tk, ttk = self.tk, self.ttk
        row_specs: list[tuple[Any | None, str, str]] = []
        if target_slugs:
            for s in target_slugs:
                row_specs.append((None, s, canonical_model_slug(s)))
        else:
            sel = [i for i in (self.kg_new_tree.selection() or self.kg_new_tree.get_children()) if i != "__empty__"]
            if only_slugs:
                matched = [
                    i for i in self.kg_new_tree.get_children()
                    if i != "__empty__" and canonical_model_slug(str(self.kg_new_tree.item(i, "values")[0])) in only_slugs
                ]
                if matched:
                    sel = matched
            for item_id in sel:
                vals = self.kg_new_tree.item(item_id, "values")
                if vals:
                    row_specs.append((item_id, str(vals[0]), str(vals[1])))

        if not row_specs:
            self._append_log("[WARN] No models to edit. Click 'List New Models' first.\n", "warn")
            return

        dlg = tk.Toplevel(self.root)
        dlg.title("Edit Canonical, Organization & License")
        dlg.configure(bg=PALETTE["surface"])
        dlg.transient(self.root)
        dlg.grab_set()
        w = self._px(950)
        h = min(self._px(240 + len(row_specs) * 44), self._px(590))
        dlg.minsize(self._px(780), self._px(280))
        self._center_dialog(dlg, w, h)

        wrap = tk.Frame(dlg, bg=PALETTE["surface"], padx=self._px(16), pady=self._px(12))
        wrap.pack(fill="both", expand=True)

        head_row = tk.Frame(wrap, bg=PALETTE["surface"])
        head_row.pack(fill="x")
        tk.Label(
            head_row,
            text="Register / Edit Models in synhalees/models.json",
            bg=PALETTE["surface"],
            fg=PALETTE["text"],
            font=("Segoe UI", 11, "bold"),
        ).pack(side="left")

        if on_saved is not None:
            tk.Label(
                wrap,
                text="Registration required before continuing — verify Organization, Family & License, then click 'Save & Continue ->'.",
                bg=PALETTE["surface"],
                fg=PALETTE["warn"],
                font=("Segoe UI", 9),
                anchor="w",
            ).pack(fill="x", pady=(2, 0))

        new_orgs_pending: dict[str, dict[str, str]] = {}
        new_org_svgs: dict[str, Path] = {}
        org_choices = sorted(default_registry.providers.keys())

        def _families_for(org_slug: str) -> list[str]:
            p = default_registry.providers.get(org_slug.strip().lower())
            return sorted(p.families.keys()) if p else []

        grid = tk.Frame(wrap, bg=PALETTE["card"], padx=self._px(12), pady=self._px(10))
        grid.pack(fill="both", expand=True, pady=(self._px(6), self._px(8)))
        grid.columnconfigure(1, weight=2)
        grid.columnconfigure(2, weight=2)
        grid.columnconfigure(3, weight=1)
        grid.columnconfigure(4, weight=1)

        for col_i, hdr in enumerate(
            ("Kaggle Slug", "Canonical Slug", "Display Name", "Organization", "Family", "Open Source")
        ):
            tk.Label(
                grid,
                text=hdr,
                bg=PALETTE["card"],
                fg=PALETTE["muted"],
                font=("Segoe UI", 8, "bold"),
            ).grid(row=0, column=col_i, sticky="w", padx=(0, 8), pady=(0, 6))

        rows_data: list[tuple[Any, str, Any, Any, Any, Any, Any]] = []
        org_combos: list[Any] = []

        for idx, (item_id, raw_slug, cur_canon) in enumerate(row_specs, start=1):
            cur_disp = default_registry.get_model_display_name(cur_canon)
            v_slug, f_slug, is_oss = default_registry.resolve_taxonomy(cur_canon)
            init_org = "" if v_slug in _UNKNOWN_ORG_TOKENS else v_slug
            if not init_org and "/" in raw_slug:
                hint_p = default_registry.resolve_provider(raw_slug.partition("/")[0])
                if hint_p not in _UNKNOWN_ORG_TOKENS:
                    init_org = hint_p
            init_fam = "" if f_slug in _UNKNOWN_ORG_TOKENS else f_slug
            if not init_fam and "-" in cur_canon:
                lead = cur_canon.split("-", 1)[0].strip().lower()
                if lead.isalpha():
                    init_fam = lead

            tk.Label(
                grid,
                text=raw_slug,
                bg=PALETTE["card"],
                fg=PALETTE["text"],
                font=("Consolas", 9),
            ).grid(row=idx, column=0, sticky="w", padx=(0, 8), pady=4)

            canon_var = tk.StringVar(value=cur_canon)
            disp_var = tk.StringVar(value=cur_disp)
            org_var = tk.StringVar(value=init_org)
            fam_var = tk.StringVar(value=init_fam)
            oss_var = tk.BooleanVar(value=bool(is_oss))

            ttk.Entry(grid, textvariable=canon_var).grid(row=idx, column=1, sticky="ew", padx=(0, 8), pady=4)
            ttk.Entry(grid, textvariable=disp_var).grid(row=idx, column=2, sticky="ew", padx=(0, 8), pady=4)

            org_cb = ttk.Combobox(grid, textvariable=org_var, values=org_choices, width=14)
            org_cb.grid(row=idx, column=3, sticky="ew", padx=(0, 8), pady=4)
            org_combos.append(org_cb)

            fam_cb = ttk.Combobox(grid, textvariable=fam_var, values=_families_for(init_org), width=14)
            fam_cb.grid(row=idx, column=4, sticky="ew", padx=(0, 8), pady=4)

            def _on_org_change(_e: Any = None, ov: Any = org_var, fcb: Any = fam_cb, fv: Any = fam_var, osv: Any = oss_var) -> None:
                o = ov.get().strip().lower()
                fams = _families_for(o)
                fcb["values"] = fams
                if fams and fv.get().strip() not in fams:
                    fv.set(fams[0])
                p = default_registry.providers.get(o)
                if p and fv.get().strip() in p.families:
                    osv.set(p.families[fv.get().strip()].open_source)

            org_cb.bind("<<ComboboxSelected>>", _on_org_change)

            ttk.Checkbutton(grid, text="OSS", variable=oss_var).grid(row=idx, column=5, sticky="w", pady=4)
            rows_data.append((item_id, raw_slug, canon_var, disp_var, org_var, fam_var, oss_var))

        def _create_new_org() -> None:
            sub = tk.Toplevel(dlg)
            sub.title("Create Organization")
            sub.configure(bg=PALETTE["surface"])
            sub.transient(dlg)
            sub.grab_set()
            self._center_dialog(sub, self._px(460), self._px(290))
            swrap = tk.Frame(sub, bg=PALETTE["surface"], padx=self._px(16), pady=self._px(14))
            swrap.pack(fill="both", expand=True)
            swrap.columnconfigure(1, weight=1)

            slug_v = tk.StringVar()
            name_v = tk.StringVar()
            col_v = tk.StringVar(value="blue")
            svg_lbl_v = tk.StringVar(value="(optional)")
            picked_svg: list[Path | None] = [None]
            serr_v = tk.StringVar()

            for r_i, (lbl, var, choices) in enumerate((
                ("Org Slug (lowercase)", slug_v, None),
                ("Display Name", name_v, None),
                ("Brand Color", col_v, sorted(default_registry.named_colors.keys())),
            )):
                tk.Label(swrap, text=lbl, bg=PALETTE["surface"], fg=PALETTE["text"], font=("Segoe UI", 9)).grid(
                    row=r_i, column=0, sticky="w", padx=(0, 10), pady=5
                )
                if choices:
                    ttk.Combobox(swrap, textvariable=var, values=choices, state="readonly").grid(
                        row=r_i, column=1, sticky="ew", pady=5
                    )
                else:
                    ttk.Entry(swrap, textvariable=var).grid(row=r_i, column=1, sticky="ew", pady=5)

            tk.Label(swrap, text="SVG Logo (opt)", bg=PALETTE["surface"], fg=PALETTE["text"], font=("Segoe UI", 9)).grid(
                row=3, column=0, sticky="w", padx=(0, 10), pady=5
            )
            svg_row = tk.Frame(swrap, bg=PALETTE["surface"])
            svg_row.grid(row=3, column=1, sticky="ew", pady=5)

            def _pick_new_svg() -> None:
                p = self.filedialog.askopenfilename(
                    parent=sub, title="Select SVG Logo", filetypes=[("SVG", "*.svg")]
                )
                if p:
                    picked_svg[0] = Path(p)
                    svg_lbl_v.set(Path(p).name)

            ttk.Button(svg_row, text="Browse .svg…", style="Ghost.TButton", command=_pick_new_svg).pack(side="left")
            tk.Label(svg_row, textvariable=svg_lbl_v, bg=PALETTE["surface"], fg=PALETTE["muted"], font=("Segoe UI", 8)).pack(
                side="left", padx=(8, 0)
            )

            tk.Label(swrap, textvariable=serr_v, bg=PALETTE["surface"], fg=PALETTE["accent"], font=("Segoe UI", 9)).grid(
                row=4, column=0, columnspan=2, sticky="w", pady=(4, 6)
            )

            def _add_org() -> None:
                oslug = slug_v.get().strip().lower()
                oname = name_v.get().strip()
                if not oslug or oslug in _UNKNOWN_ORG_TOKENS or not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", oslug):
                    serr_v.set("Enter a valid lowercase slug (e.g. cohere, mistralai).")
                    return
                if not oname:
                    serr_v.set("Enter a Display Name (e.g. Cohere).")
                    return
                new_orgs_pending[oslug] = {"name": oname, "logo": oslug, "color": col_v.get() or "gray"}
                if picked_svg[0] is not None:
                    new_org_svgs[oslug] = picked_svg[0]
                if oslug not in org_choices:
                    org_choices.append(oslug)
                    org_choices.sort()
                for cb in org_combos:
                    cb["values"] = org_choices
                for _, _, _, _, ov, _, _ in rows_data:
                    if not ov.get().strip():
                        ov.set(oslug)
                sub.destroy()

            sbtns = tk.Frame(swrap, bg=PALETTE["surface"])
            sbtns.grid(row=5, column=0, columnspan=2, sticky="e")
            ttk.Button(sbtns, text="Cancel", style="Ghost.TButton", command=sub.destroy).pack(side="right")
            ttk.Button(sbtns, text="Create Org", style="Accent.TButton", command=_add_org).pack(side="right", padx=(0, 8))

        ttk.Button(head_row, text="+ New Org", style="Ghost.TButton", command=_create_new_org).pack(side="right")

        err_var = tk.StringVar()
        tk.Label(wrap, textvariable=err_var, bg=PALETTE["surface"], fg=PALETTE["accent"], font=("Segoe UI", 9, "bold")).pack(
            anchor="w", pady=(0, self._px(6))
        )

        btns = tk.Frame(wrap, bg=PALETTE["surface"])
        btns.pack(fill="x")

        def _save_models() -> None:
            entries: list[dict[str, Any]] = []
            for item_id, raw_slug, c_var, d_var, o_var, f_var, oss_var in rows_data:
                entries.append({
                    "canonical": c_var.get().strip(),
                    "display_name": d_var.get().strip(),
                    "org": o_var.get().strip().lower(),
                    "family": f_var.get().strip().lower(),
                    "open_source": bool(oss_var.get()),
                })
            try:
                saved = save_model_registry_entries(entries, new_orgs=new_orgs_pending)
                for oslug, svg_path in new_org_svgs.items():
                    meta = new_orgs_pending.get(oslug, {})
                    install_organization_logo(
                        oslug,
                        svg_path,
                        org_slug=oslug,
                        color_hex=default_registry.resolve_color_hex(oslug),
                        rebuild_logo_data=True,
                    )
                    self._invalidate_icon_caches()
            except ValueError as exc:
                err_var.set(str(exc))
                return

            for (item_id, raw_slug, c_var, *_), ent in zip(rows_data, entries):
                if item_id is not None and self.kg_new_tree.exists(item_id):
                    self.kg_new_tree.item(item_id, tags=("even", "ok"), values=(raw_slug, ent["canonical"], "Registered"))

            if saved:
                self._append_log(f"[OK] Saved {saved} model(s) to synhalees/models.json\n", "ok")
                self.kg_discover_status_var.set(f"Saved {saved} model(s) to synhalees/models.json.")
                self.refresh_all()
            dlg.destroy()
            if on_saved is not None:
                self.root.after(10, on_saved)

        ttk.Button(btns, text="Cancel", style="Ghost.TButton", command=dlg.destroy).pack(side="right")
        ttk.Button(
            btns,
            text="Save & Continue ->" if on_saved else "Save to models.json",
            style="Accent.TButton",
            command=_save_models,
        ).pack(side="right", padx=(0, 8))
        dlg.bind("<Escape>", lambda _e: dlg.destroy())

    def _bind_toggle_tree(self, tree: Any) -> None:
        """Allow clicking the single selected row again (without Ctrl) to deselect it."""
        def _on_click(event: Any) -> str | None:
            if tree.identify_region(event.x, event.y) != "cell":
                return None
            row = tree.identify_row(event.y)
            if not row:
                return None
            # Shift (0x1) / Control (0x4): keep native multi-select behavior
            if not (event.state & 0x5) and tree.selection() == (row,):
                tree.selection_remove(row)
                return "break"
            return None

        tree.bind("<Button-1>", _on_click, add=True)

    def _select_all_new_kaggle_models(self) -> None:
        items = self.kg_new_tree.get_children()
        if items:
            self.kg_new_tree.selection_set(items)
            self._on_select_new_kaggle_model()

    def _clear_selected_kaggle_models(self) -> None:
        self._syncing_kg_sel = True
        try:
            self.kg_model_var.set("")
            for tree in (self.kg_new_tree, self.kg_tree):
                sel = tree.selection()
                if sel:
                    tree.selection_remove(sel)
            self.kg_discover_status_var.set("Model Filter cleared (all models targeted).")
        finally:
            self._syncing_kg_sel = False

    def _sync_kaggle_selection(self, active_tree: Any, other_tree: Any) -> None:
        if getattr(self, "_syncing_kg_sel", False):
            return
        self._syncing_kg_sel = True
        try:
            sel = active_tree.selection()
            if sel:
                other_sel = other_tree.selection()
                if other_sel:
                    other_tree.selection_remove(other_sel)
            names: list[str] = []
            for tree in (self.kg_new_tree, self.kg_tree):
                for item in tree.selection():
                    vals = tree.item(item, "values")
                    if vals and str(vals[0]) not in names:
                        names.append(str(vals[0]))
            if not names:
                self.kg_model_var.set("")
                self.kg_discover_status_var.set("Model Filter cleared (all models targeted).")
            elif len(names) == 1:
                self.kg_model_var.set(names[0])
                self.kg_discover_status_var.set(f"Selected '{names[0]}' in Model Filter (-m) for Pull / Run.")
            else:
                self.kg_model_var.set(", ".join(names))
                self.kg_discover_status_var.set(f"Selected {len(names)} models in Model Filter (-m) for Pull / Run.")
        finally:
            self._syncing_kg_sel = False

    def _on_select_new_kaggle_model(self, _event: Any = None) -> None:
        self._sync_kaggle_selection(self.kg_new_tree, self.kg_tree)

    def _on_select_cov_model(self, _event: Any = None) -> None:
        self._sync_kaggle_selection(self.kg_tree, self.kg_new_tree)

    def _discover_kaggle_models(self, *, on_done: Callable[[], None] | None = None) -> None:
        """Query `kaggle b t models` in background and display new models in Step 1."""
        if not self._ensure_kaggle_auth(lambda: self._discover_kaggle_models(on_done=on_done)):
            return
        with self._proc_lock:
            if self._proc is not None and self._proc.poll() is None:
                self._append_log("[WARN] A command is already running. Stop it first.\n", "warn")
                return

        self.select_page("kaggle")
        self._discover_on_done = on_done
        self._last_args = ["kaggle", "models"]
        self._last_cmd_text = "kaggle b t models"
        self.kg_discover_status_var.set("Querying Kaggle servers (`kaggle b t models`)...")
        self.cmd_label_var.set("kaggle b t models (new vs local)")
        self._append_log("\n$ kaggle b t models  # comparing against local models\n", "cmd")
        self._set_running(True)

        from synhalees.cli import _scan_submissions

        try:
            kg_cov, _, _ = gather_coverage(check_remote=False)
        except Exception:
            kg_cov = {}
        subs = _scan_submissions()

        def worker() -> None:
            rc = 0
            try:
                proc = run_kaggle_captured(["b", "t", "models"], timeout=60)
                if proc.returncode != 0:
                    parts = [s.strip() for s in (proc.stderr or "", proc.stdout or "") if s and s.strip()]
                    exe_used = Path(proc.args[0]).name if getattr(proc, "args", None) else "kaggle"
                    detail = "\n".join(parts) if parts else (
                        f"exit code {proc.returncode} from {exe_used} (auth: {kaggle_auth_status()})"
                    )
                    self._log_queue.put(("line", f"[ERR] `kaggle b t models` failed: {detail}\n"))
                    self._log_queue.put(("discover_err", f"Failed: {detail.splitlines()[0][:90]}"))
                    if "401" in detail or "auth" in detail.lower() or "token" in detail.lower() or not parts:
                        self._log_queue.put(("line", "[HINT] Check internet/VPN or click 'Tokens & Keys' to verify KAGGLE_API_TOKEN.\n"))
                    rc = proc.returncode or 1
                else:
                    avail = parse_kaggle_models_output(proc.stdout or "")
                    pending = classify_pending_kaggle_models(avail, kg_cov, subs)
                    self._log_queue.put((
                        "line",
                        f"Found {len(avail)} model(s) on Kaggle; {len(pending)} pending/incomplete:\n",
                    ))
                    for raw, canon, st_lbl in pending:
                        self._log_queue.put(("line", f"  [NEW] {raw}  -> canonical: {canon} ({st_lbl})\n"))
                    if not pending:
                        self._log_queue.put(("line", "[OK] All Kaggle platform models already have full 15T+V+A coverage.\n"))
                    self._log_queue.put(("discovered", (len(avail), pending)))
            except OSError as exc:
                self._log_queue.put(("line", f"[ERR] Failed to launch kaggle: {exc}\n"))
                self._log_queue.put(("discover_err", f"Launch error: {exc}"))
                rc = 1
            except Exception as exc:
                self._log_queue.put(("line", f"[ERR] {exc}\n"))
                self._log_queue.put(("discover_err", f"Error: {exc}"))
                rc = 1
            finally:
                self._log_queue.put(("done", rc))

        threading.Thread(target=worker, daemon=True).start()

    # ------------------------------------------------------------------ Subprocess runner

    def run_pipeline(
        self,
        steps: list[tuple[str, list[str]]],
        *,
        on_complete: Callable[[], None] | None = None,
    ) -> None:
        """Run multiple CLI subcommands sequentially with shared pipeline progress."""
        clean_steps = [(msg, list(cmd)) for msg, cmd in steps if cmd]
        if not clean_steps:
            return
        msg, first_args = clean_steps[0]
        self._pipeline_queue = clean_steps[1:]
        self._pipeline_on_complete = on_complete
        self._append_log(f"[PIPELINE] {msg}\n", "cmd")
        self.run_cli(first_args, _keep_pipeline=True)

    def run_cli(self, args: list[str], *, _keep_pipeline: bool = False) -> None:
        with self._proc_lock:
            if self._proc is not None and self._proc.poll() is None:
                self._append_log("[WARN] A command is already running. Stop it first.\n", "warn")
                return

        if not _keep_pipeline:
            self._pipeline_queue = []
            self._pipeline_on_complete = None

        self._last_args = list(args)
        self._last_err_line = ""
        self._eval_done = 0
        self._eval_pass = 0
        if len(args) >= 2 and args[:2] == ["kaggle", "pull"]:
            self._failed_kaggle_tasks = []
            if hasattr(self, "kg_retry_btn") and self.kg_retry_btn.winfo_ismapped():
                self.kg_retry_btn.pack_forget()

        preview = format_cmd_preview(args)
        self._last_cmd_text = preview
        self.cmd_label_var.set(preview)
        self._append_log(f"\n$ {preview}\n", "cmd")
        self._set_running(True)

        cmd = [sys.executable, "-m", "synhalees", "--no-color", *args]
        env = dict(
            os.environ,
            PYTHONIOENCODING="utf-8",
            PYTHONUTF8="1",
            PYTHONUNBUFFERED="1",
        )

        def worker() -> None:
            rc = 1
            use_pipe_stdin = "--colab" in args
            try:
                proc = subprocess.Popen(
                    cmd,
                    cwd=str(ROOT),
                    env=env,
                    stdin=subprocess.PIPE if use_pipe_stdin else subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                )
                with self._proc_lock:
                    self._proc = proc
                assert proc.stdout is not None
                for line in proc.stdout:
                    self._log_queue.put(("line", line))
                rc = proc.wait()
            except OSError as exc:
                self._log_queue.put(("line", f"[ERR] Failed to launch process: {exc}\n"))
            finally:
                with self._proc_lock:
                    self._proc = None
                self._log_queue.put(("done", rc))

        threading.Thread(target=worker, daemon=True).start()

    def stop_command(self) -> None:
        self._pipeline_queue = []
        self._pipeline_on_complete = None
        with self._proc_lock:
            proc = self._proc
        if proc is not None and proc.poll() is None:
            self._append_log("[WARN] Stopping active process...\n", "warn")
            try:
                proc.terminate()
            except Exception:
                proc.kill()

    def _set_workflow_buttons_state(self, state: str) -> None:
        """Enable or disable all step/action buttons across pages while a job runs."""
        def _walk(widget: Any) -> None:
            for child in widget.winfo_children():
                if isinstance(child, self.ttk.Button):
                    try:
                        child.configure(state=state)
                    except Exception:
                        pass
                _walk(child)

        _walk(self.content_host)

    def _set_running(self, running: bool) -> None:
        if running:
            self.status_badge.configure(text="RUNNING", bg=PALETTE["accent"], fg="#ffffff")
            self.stop_btn.configure(state="normal")
            self._set_workflow_buttons_state("disabled")
            self.progress_msg_var.set("Running... please wait until this step finishes.")
            self.progress_pct_var.set("")
            if not self.progress_wrap.winfo_ismapped():
                self.progress_wrap.pack(fill="x", padx=self._px(12), pady=(self._px(6), 0), before=self.cbar)
            self.progress_bar.configure(mode="indeterminate", maximum=100.0, value=0.0)
            self.progress_bar.start(12)
        else:
            self.status_badge.configure(text="IDLE", bg=PALETTE["card"], fg=PALETTE["ok"])
            self.stop_btn.configure(state="disabled")
            self.progress_bar.stop()
            self.progress_pct_var.set("")
            self.progress_wrap.pack_forget()
            self._set_workflow_buttons_state("normal")
            self._update_reasoning_button_state()

    def _drain_queue(self) -> None:
        try:
            while True:
                kind, payload = self._log_queue.get_nowait()
                if kind == "line":
                    item_event = parse_gui_item_event(str(payload))
                    if item_event is not None:
                        self._apply_eval_detail_event(item_event)
                        continue
                    tag = None
                    if "[OK]" in payload or payload.startswith("OK:") or "[NEW]" in payload:
                        tag = "ok"
                    elif "[WARN]" in payload or "WinError 4551" in payload:
                        tag = "warn"
                    elif "[ERR]" in payload or "[FAIL]" in payload or "Traceback" in payload:
                        tag = "err"
                    self._append_log(payload, tag)
                    clean_line = str(payload).strip()
                    if clean_line and self.progress_wrap.winfo_ismapped():
                        self.progress_msg_var.set(clean_line[:110])
                        ev = parse_eval_item_outcome(clean_line)
                        if ev:
                            done_n, total_n, passed = ev
                            self._eval_done += 1
                            if passed:
                                self._eval_pass += 1
                            pct = min(100.0, (done_n / total_n) * 100.0)
                            acc = (self._eval_pass / max(1, self._eval_done)) * 100.0
                            if str(self.progress_bar.cget("mode")) != "determinate":
                                self.progress_bar.stop()
                                self.progress_bar.configure(mode="determinate", maximum=100.0)
                            self.progress_bar["value"] = pct
                            self.progress_pct_var.set(f"{done_n}/{total_n} ({pct:.0f}%) · Acc {acc:.1f}%")
                            self.status_badge.configure(text=f"RUNNING {pct:.0f}%")
                        else:
                            prog = parse_progress_line(clean_line)
                            if prog:
                                done_n, total_n, pct, _ = prog
                                if str(self.progress_bar.cget("mode")) != "determinate":
                                    self.progress_bar.stop()
                                    self.progress_bar.configure(mode="determinate", maximum=100.0)
                                self.progress_bar["value"] = pct
                                self.progress_pct_var.set(f"{done_n}/{total_n} ({pct:.0f}%)")
                                self.status_badge.configure(text=f"RUNNING {pct:.0f}%")

                    if "logo-data.js fresh" in clean_line:
                        ok_g = clean_line.startswith("[OK]") or clean_line.startswith("OK:")
                        self._set_site_gate("logos", "PASS" if ok_g else "FAIL", "checksum verified" if ok_g else "stale checksum", PALETTE["ok"] if ok_g else PALETTE["accent"])
                    elif "docs/assets/data in sync" in clean_line:
                        ok_g = clean_line.startswith("[OK]") or clean_line.startswith("OK:")
                        self._set_site_gate("data", "PASS" if ok_g else "FAIL", "in sync" if ok_g else "stale data", PALETTE["ok"] if ok_g else PALETTE["accent"])
                    elif "dataset schemas valid" in clean_line:
                        ok_g = clean_line.startswith("[OK]") or clean_line.startswith("OK:")
                        self._set_site_gate("schemas", "PASS" if ok_g else "FAIL", "15 pillars valid" if ok_g else "schema error", PALETTE["ok"] if ok_g else PALETTE["accent"])
                    elif "python compiles" in clean_line:
                        ok_g = clean_line.startswith("[OK]") or clean_line.startswith("OK:")
                        self._set_site_gate("compile", "PASS" if ok_g else "FAIL", "syntax OK" if ok_g else "compile error", PALETTE["ok"] if ok_g else PALETTE["accent"])

                    failed_now = extract_failed_tasks(clean_line)
                    if failed_now:
                        for ft in failed_now:
                            if ft not in self._failed_kaggle_tasks:
                                self._failed_kaggle_tasks.append(ft)

                    if clean_line and (
                        "[ERR]" in clean_line
                        or "Error:" in clean_line
                        or "Exception:" in clean_line
                        or clean_line.startswith(("OSError:", "TimeoutError:", "SystemExit:"))
                    ):
                        self._last_err_line = clean_line[:110]
                    auth_m = re.search(r"https://accounts\.google\.com/o/oauth2/auth\S+", payload)
                    if auth_m:
                        auth_url = auth_m.group(0)
                        if getattr(self, "_last_auth_url", None) != auth_url:
                            self._last_auth_url = auth_url
                            webbrowser.open(auth_url)
                            self._append_log("[OK] Opened Google Colab login URL in your web browser.\n", "ok")
                            self._open_colab_auth_dialog(auth_url)
                    if "WinError 4551" in payload:
                        self._append_log(
                            "[HINT] Windows Application Control blocked kaggle.exe / Python313. "
                            "Install `kaggle` in an allowed Python (e.g. `py -3.11 -m pip install kaggle`) "
                            "and launch with `py -3.11 -m synhalees gui`, or use Colab (`kaggle/colab_slim_export.ipynb`).\n",
                            "warn",
                        )
                elif kind == "probe":
                    self._apply_probe_result(payload)
                elif kind == "judge_probe":
                    provider, probe = payload
                    self._apply_judge_probe_result(provider, probe)
                elif kind == "judge_test_result":
                    jspec, probe = payload
                    self._apply_judge_test_result(jspec, probe)
                elif kind == "reasoning_probe":
                    model_spec, probe_generation, probe = payload
                    self._apply_reasoning_probe_result(
                        model_spec,
                        probe_generation,
                        probe,
                    )
                elif kind == "discovered":
                    total_avail, pending = payload
                    self.kg_new_tree.delete(*self.kg_new_tree.get_children())
                    if pending:
                        for idx, (raw, canon, st_lbl) in enumerate(pending):
                            stripe = "odd" if idx % 2 else "even"
                            st_tag = "warn" if ("Unregistered" in st_lbl or "Partial" in st_lbl) else "info"
                            self.kg_new_tree.insert("", "end", tags=(stripe, st_tag), values=(raw, canon, st_lbl))
                        self.kg_new_frame.pack(fill="x", pady=(0, self._px(10)), before=self.kg_cov_card)
                        self._select_all_new_kaggle_models()
                        self.kg_discover_status_var.set(
                            f"Found {len(pending)} pending/incomplete model(s) out of {total_avail} on Kaggle — all selected."
                        )
                    else:
                        self.kg_new_frame.pack_forget()
                        self.kg_discover_status_var.set(
                            f"Checked {total_avail} Kaggle models — all have full 15T+V+A coverage."
                        )
                elif kind == "discover_err":
                    self.kg_discover_status_var.set(str(payload))
                elif kind == "done":
                    self._last_auth_url = None
                    rc = int(payload)
                    if "--gui-item-events" in self._last_args:
                        self._finish_eval_item_window(rc)
                    self._set_running(False)
                    if rc == 0:
                        self._append_log("[OK] Finished (exit 0).\n", "ok")
                        self.cmd_label_var.set(f"{self._last_cmd_text}  [OK · next steps ready]")
                    else:
                        self._append_log(f"[ERR] Exited with code {rc}.\n", "err")
                        summary = getattr(self, "_last_err_line", "") or f"exit code {rc}"
                        self.cmd_label_var.set(f"[ERR] {summary}")
                        if not self._console_visible:
                            self._toggle_console()
                    self.refresh_all()

                    if rc != 0:
                        self._pipeline_queue = []
                        self._pipeline_on_complete = None
                        self._discover_on_done = None
                        if self._failed_kaggle_tasks and hasattr(self, "kg_retry_btn"):
                            self.kg_retry_btn.configure(text=f"Retry {len(self._failed_kaggle_tasks)} Failed Task(s)")
                            if not self.kg_retry_btn.winfo_ismapped():
                                self.kg_retry_btn.pack(side="left")
                        continue

                    if self._last_args == ["kaggle", "models"] and getattr(self, "_discover_on_done", None) is not None:
                        cb = self._discover_on_done
                        self._discover_on_done = None
                        if self.kg_new_tree.get_children():
                            cb()
                        continue

                    if self._pipeline_queue:
                        nxt_msg, nxt_args = self._pipeline_queue.pop(0)
                        self._append_log(f"[PIPELINE] {nxt_msg}\n", "cmd")
                        self.run_cli(nxt_args, _keep_pipeline=True)
                        continue

                    if self._pipeline_on_complete is not None:
                        done_cb = self._pipeline_on_complete
                        self._pipeline_on_complete = None
                        done_cb()

                    if (
                        len(getattr(self, "_last_args", [])) >= 2
                        and self._last_args[:2] == ["kaggle", "pull"]
                        and "--dry-run" not in self._last_args
                    ):
                        if getattr(self, "kg_auto_build_var", None) and self.kg_auto_build_var.get():
                            submit_args = build_kaggle_args("submit", task=self.kg_task_var.get(), build=True)
                            unreg = find_unregistered_slim_models(self.kg_task_var.get())
                            if unreg:
                                self._guard_and_run_kaggle("submit", submit_args)
                            else:
                                self.run_pipeline([
                                    ("Auto-Build 1/2 · Importing slim CSVs & rebuilding leaderboard...", submit_args),
                                    ("Auto-Build 2/2 · Verifying all 4 CI gates...", ["check"]),
                                ])
                        else:
                            self._open_pull_complete_dialog()
                    elif (
                        len(getattr(self, "_last_args", [])) >= 1
                        and self._last_args[0] == "run"
                        and "--dry-run" not in self._last_args
                    ):
                        finished_slug = self._extract_finished_run_slug(self._last_args)
                        if finished_slug and finished_slug in self.pub_combo["values"]:
                            self.pub_slug_var.set(finished_slug)
                        if getattr(self, "run_auto_publish_var", None) and self.run_auto_publish_var.get() and finished_slug:
                            self._publish_selected_run()
                        elif finished_slug:
                            self._open_run_complete_dialog(finished_slug)
        except queue.Empty:
            pass
        self.root.after(70, self._drain_queue)

    @staticmethod
    def _extract_finished_run_slug(args: list[str]) -> str:
        from synhalees.runner import model_slug

        for idx, tok in enumerate(args):
            if tok == "--model" and idx + 1 < len(args):
                return model_slug(args[idx + 1])
            if tok.startswith("--model="):
                return model_slug(tok.split("=", 1)[1])
        return ""

    def _finish_eval_item_window(self, return_code: int) -> None:
        if self._eval_detail_window is None or not self._eval_detail_window.winfo_exists():
            return
        if return_code == 0:
            if not self._eval_detail_seen_item:
                self._set_eval_detail_status(
                    "Run complete — no new runnable items.",
                    PALETTE["ok"],
                )
            return
        active_key = self._eval_detail_active_key
        active_index = self._eval_detail_item_indexes.get(active_key or "")
        if active_index is not None:
            active_item = self._eval_detail_items[active_index]
            active_item["status"] = (
                "Run stopped — this item did not receive a final score. "
                "Check the run log."
            )
            active_item["status_color"] = PALETTE["accent"]
            self._eval_detail_active_key = None
            if active_index == self._eval_detail_index:
                self._show_eval_item(active_index)
        elif not self._eval_detail_seen_item:
            self._set_eval_detail_status(
                "Run failed before a benchmark item started. Check the run log.",
                PALETTE["accent"],
            )

    def _open_run_complete_dialog(self, slug: str) -> None:
        """Notify when a local evaluation completes and offer Publish or Compare."""
        tk, ttk = self.tk, self.ttk
        meta_path = RUNS / slug / "meta.json"
        summary_txt = f"Run '{slug}' finished."
        if meta_path.is_file():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                ov = meta.get("overall")
                ov_s = f"{float(ov):.1f}%" if ov is not None else "—"
                summary_txt = f"Model: {meta.get('spec', slug)}\nItems: {meta.get('items', '—')}  ·  Errors: {meta.get('errors', 0)}  ·  Overall: {ov_s}"
            except Exception:
                pass

        dlg = tk.Toplevel(self.root)
        dlg.title("Local Evaluation Complete")
        dlg.configure(bg=PALETTE["surface"])
        dlg.transient(self.root)
        dlg.grab_set()
        self._center_dialog(dlg, self._px(490), self._px(210))

        wrap = tk.Frame(dlg, bg=PALETTE["surface"], padx=self._px(18), pady=self._px(16))
        wrap.pack(fill="both", expand=True)
        tk.Label(wrap, text="Local Run Complete", bg=PALETTE["surface"], fg=PALETTE["ok"], font=("Segoe UI", 12, "bold")).pack(anchor="w")
        tk.Label(wrap, text=summary_txt, bg=PALETTE["surface"], fg=PALETTE["text"], font=("Segoe UI", 9), justify="left").pack(
            anchor="w", pady=(self._px(8), self._px(14))
        )
        btns = tk.Frame(wrap, bg=PALETTE["surface"])
        btns.pack(fill="x", side="bottom")
        ttk.Button(
            btns,
            text="Publish & Rebuild Site Now",
            style="Accent.TButton",
            command=lambda: (dlg.destroy(), self._publish_selected_run()),
        ).pack(side="right", padx=(self._px(8), 0))
        ttk.Button(
            btns,
            text="Compare All Runs",
            style="Ghost.TButton",
            command=lambda: (dlg.destroy(), self.run_cli(["compare"])),
        ).pack(side="right", padx=(self._px(8), 0))
        ttk.Button(btns, text="Close", style="Ghost.TButton", command=dlg.destroy).pack(side="right")
        dlg.bind("<Escape>", lambda _e: dlg.destroy())

    def _open_pull_complete_dialog(self) -> None:
        """Notify the user when Kaggle/Colab pull finishes and offer Step 3 Build."""
        tk, ttk = self.tk, self.ttk
        dlg = tk.Toplevel(self.root)
        dlg.title("Pull Complete")
        dlg.configure(bg=PALETTE["surface"])
        dlg.transient(self.root)
        dlg.grab_set()
        self._center_dialog(dlg, self._px(480), self._px(195))

        wrap = tk.Frame(dlg, bg=PALETTE["surface"], padx=self._px(18), pady=self._px(16))
        wrap.pack(fill="both", expand=True)

        tk.Label(
            wrap,
            text="Pull Complete — Ready to Build",
            bg=PALETTE["surface"],
            fg=PALETTE["ok"],
            font=("Segoe UI", 12, "bold"),
        ).pack(anchor="w")
        tk.Label(
            wrap,
            text=(
                "All selected slim CSVs have been downloaded and merged into kaggle/results/.\n"
                "You can now run Step 3 to import submissions and rebuild the leaderboard."
            ),
            bg=PALETTE["surface"],
            fg=PALETTE["text"],
            font=("Segoe UI", 9),
            justify="left",
            wraplength=self._px(440),
        ).pack(anchor="w", pady=(self._px(8), self._px(14)))

        btns = tk.Frame(wrap, bg=PALETTE["surface"])
        btns.pack(fill="x", side="bottom")

        def _build_now() -> None:
            dlg.destroy()
            self._guard_and_run_kaggle(
                "submit",
                build_kaggle_args(
                    "submit",
                    task=self.kg_task_var.get(),
                    dry_run=self.kg_dry_var.get(),
                    build=True,
                ),
            )

        ttk.Button(btns, text="Import & Rebuild Site Now", style="Accent.TButton", command=_build_now).pack(
            side="right", padx=(self._px(8), 0)
        )
        ttk.Button(btns, text="Close", style="Ghost.TButton", command=dlg.destroy).pack(side="right")
        dlg.bind("<Escape>", lambda _e: dlg.destroy())

    def _open_colab_auth_dialog(self, auth_url: str) -> None:
        """Prompt for Google Colab OAuth code after auto-opening the browser."""
        tk, ttk = self.tk, self.ttk
        dlg = tk.Toplevel(self.root)
        dlg.title("Google Colab Authorization")
        dlg.configure(bg=PALETTE["surface"])
        dlg.transient(self.root)
        dlg.grab_set()
        self._center_dialog(dlg, self._px(500), self._px(210))

        wrap = tk.Frame(dlg, bg=PALETTE["surface"], padx=self._px(16), pady=self._px(14))
        wrap.pack(fill="both", expand=True)

        tk.Label(
            wrap,
            text="Authorize Google Colab CLI",
            bg=PALETTE["surface"],
            fg=PALETTE["text"],
            font=("Segoe UI", 11, "bold"),
        ).pack(anchor="w")
        tk.Label(
            wrap,
            text="Browser opened automatically. Approve access, then paste the authorization code below:",
            bg=PALETTE["surface"],
            fg=PALETTE["muted"],
            font=("Segoe UI", 9),
            wraplength=self._px(460),
            justify="left",
        ).pack(anchor="w", pady=(3, self._px(10)))

        code_var = tk.StringVar()
        ent = ttk.Entry(wrap, textvariable=code_var)
        ent.pack(fill="x", pady=(0, self._px(12)))
        ent.focus_set()

        def _submit(_e: Any = None) -> None:
            code = code_var.get().strip()
            if not code:
                return
            with self._proc_lock:
                proc = self._proc
            if proc is not None and proc.stdin is not None:
                try:
                    proc.stdin.write(code + "\n")
                    proc.stdin.flush()
                    self._append_log("[OK] Sent authorization code to colab-cli.\n", "ok")
                except Exception as exc:
                    self._append_log(f"[ERR] Failed to send auth code: {exc}\n", "err")
            dlg.destroy()

        def _cancel() -> None:
            with self._proc_lock:
                proc = self._proc
            if proc is not None and proc.stdin is not None:
                try:
                    proc.stdin.close()
                except Exception:
                    pass
            dlg.destroy()

        ent.bind("<Return>", _submit)
        btns = tk.Frame(wrap, bg=PALETTE["surface"])
        btns.pack(fill="x")
        ttk.Button(btns, text="Re-open Browser", style="Ghost.TButton", command=lambda: webbrowser.open(auth_url)).pack(
            side="left"
        )
        ttk.Button(btns, text="Cancel", style="Ghost.TButton", command=_cancel).pack(side="right")
        ttk.Button(btns, text="Submit Code", style="Accent.TButton", command=_submit).pack(side="right", padx=(0, 8))

    def _append_log(self, text: str, tag: str | None = None) -> None:
        self.console.configure(state="normal")
        if tag:
            self.console.insert("end", text, tag)
        else:
            self.console.insert("end", text)
        self.console.see("end")
        self.console.configure(state="disabled")

    def _clear_log(self) -> None:
        self.console.configure(state="normal")
        self.console.delete("1.0", "end")
        self.console.configure(state="disabled")

    def _copy_log(self) -> None:
        log_text = self.console.get("1.0", "end-1c")
        if not log_text.strip():
            self._append_log("[WARN] The log is empty — nothing to copy.\n", "warn")
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(log_text)
        self.root.update()
        self._append_log("[OK] Log copied to clipboard.\n", "muted")


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def add_gui_arguments(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    """Attach `synhalees gui` flags to *parser*."""
    parser.add_argument(
        "--tab",
        choices=TAB_KEYS,
        default="overview",
        help="Initial workflow section to focus (default: overview).",
    )
    return parser


def launch_gui(initial_tab: str = "overview") -> int:
    """Create the Tk root window and run the SynhalEES Studio loop."""
    _enable_hidpi()
    import tkinter as tk

    root = tk.Tk()
    icon_png = ROOT / "docs" / "assets" / "icon.png"
    if icon_png.is_file():
        try:
            photo = tk.PhotoImage(file=str(icon_png))
            root.iconphoto(True, photo)
            root._synhalees_icon = photo  # prevent GC
        except Exception:
            pass

    SynhaleesGUI(root, initial_tab=initial_tab)
    try:
        root.state("zoomed")
    except Exception:
        try:
            root.attributes("-zoomed", True)
        except Exception:
            pass
    root.mainloop()
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="synhalees gui", description="Launch the SynhalEES desktop GUI.")
    add_gui_arguments(ap)
    args = ap.parse_args(argv)
    return launch_gui(initial_tab=args.tab)


if __name__ == "__main__":
    raise SystemExit(main())
