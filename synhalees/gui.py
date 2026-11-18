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
from synhalees.kaggle_ops import (
    ROOT,
    RUNS,
    expand_import_tasks,
    gather_coverage,
    parse_kaggle_models_output,
    run_kaggle_captured,
)
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
) -> list[str]:
    """Build `synhalees run ...` CLI arguments from form values."""
    args = ["run", "--model", model.strip()]
    if judge.strip():
        args.extend(["--judge", judge.strip()])
    if modality and modality != "text":
        args.extend(["--modality", modality])
    if pillars.strip() and pillars.strip().lower() != "all":
        args.extend(["--pillars", pillars.strip()])
    if fresh:
        args.append("--fresh")
    return args


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


def format_cmd_preview(args: list[str]) -> str:
    """Format a human-readable `synhalees ...` preview string."""
    return "synhalees " + " ".join(shlex.quote(a) for a in args)


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
    providers[clean_slug] = {
        "name": clean_name,
        "slug": clean_slug,
        "logo": clean_logo,
        "color": clean_color,
        "aliases": clean_aliases,
        "families": dict(existing.get("families") or {}),
    }
    data["providers"] = providers
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if path.resolve() == MODELS_JSON.resolve():
        default_registry.load()


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
    """Apply Kaggle and API tokens to os.environ and persist them to ~/.synhalees/tokens.json.

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
        from tkinter import messagebox, scrolledtext, ttk

        self.tk = tk
        self.ttk = ttk
        self.messagebox = messagebox
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
        self._last_args: list[str] = ["status"]
        self._last_cmd_text: str = "synhalees status"
        self._active_page = "overview"
        self._nav_buttons: dict[str, Any] = {}
        self._pages: dict[str, Any] = {}
        self._console_visible = False

        self._configure_styles()
        self._build_topbar()
        self._build_shell(scrolledtext)
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

        # Bottom Live Output Console controls (packed inside console_wrap created above)
        self.progress_wrap = tk.Frame(self.console_wrap, bg=PALETTE["surface"])
        self.progress_msg_var = tk.StringVar(value="")
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
        self.progress_bar.pack(side="right", padx=(self._px(10), 0))

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

        self.cmd_label_var = tk.StringVar(value="synhalees status")
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
            style="Ghost.TButton",
            command=self.stop_command,
            state="disabled",
        )
        self.stop_btn.pack(side="right", padx=(6, 0))
        ttk.Button(cbar, text="Clear", style="Ghost.TButton", command=self._clear_log).pack(side="right", padx=(6, 0))
        ttk.Button(cbar, text="Copy Cmd", style="Ghost.TButton", command=self._copy_last_cmd).pack(side="right", padx=(6, 0))
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
    ) -> Any:
        """Create a Treeview paired with a vertical inside scrollbar."""
        tk, ttk = self.tk, self.ttk
        wrap = tk.Frame(parent, bg=PALETTE["card"])
        wrap.pack(fill=fill, expand=expand)
        tree = ttk.Treeview(wrap, columns=columns, show=show, height=height, selectmode=selectmode)
        sb = ttk.Scrollbar(wrap, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=sb.set)
        tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        return tree

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

    def _step_card(self, parent: Any, step_num: str, title: str, hint: str) -> Any:
        tk, ttk = self.tk, self.ttk
        frame = tk.Frame(
            parent,
            bg=PALETTE["card"],
            highlightthickness=1,
            highlightbackground=PALETTE["border"],
            padx=self._px(14),
            pady=self._px(12),
        )
        frame.pack(fill="x", pady=(0, self._px(12)))

        head = tk.Frame(frame, bg=PALETTE["card"])
        head.pack(fill="x", pady=(0, self._px(8)))
        if step_num:
            tk.Label(
                head,
                text=step_num,
                bg=PALETTE["step_badge"],
                fg=PALETTE["info"],
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

        # Workflow Launchpad Cards
        c_kaggle = self._step_card(
            parent,
            "WORKFLOW A",
            "Sync New Models from Kaggle Benchmarks",
            "Inspect what new models ran on Kaggle, pull lightweight *.slim.csv results, import into submissions/, and rebuild the site.",
        )
        r_kg = tk.Frame(c_kaggle, bg=PALETTE["card"])
        r_kg.pack(fill="x")
        ttk.Button(
            r_kg,
            text="Open Kaggle Sync Workflow  ->",
            style="Accent.TButton",
            command=lambda: self.select_page("kaggle"),
        ).pack(side="left", padx=(0, 8))
        ttk.Button(
            r_kg,
            text="Check Missing Server Runs Now",
            style="Ghost.TButton",
            command=lambda: self.run_cli(["kaggle", "coverage"]),
        ).pack(side="left", padx=(0, 8))
        ttk.Button(
            r_kg,
            text="Discover Kaggle Models",
            style="Ghost.TButton",
            command=self._discover_kaggle_models,
        ).pack(side="left")

        c_local = self._step_card(
            parent,
            "WORKFLOW B",
            "Run & Publish Local Model Evaluation (API / Ollama)",
            "Evaluate a local Ollama or API model checkpoint-safely across pillars/modalities, compare runs, and promote to submissions/.",
        )
        r_loc = tk.Frame(c_local, bg=PALETTE["card"])
        r_loc.pack(fill="x")
        ttk.Button(
            r_loc,
            text="Open Local Eval Workflow  ->",
            style="Ghost.TButton",
            command=lambda: self.select_page("run"),
        ).pack(side="left", padx=(0, 8))
        ttk.Button(
            r_loc,
            text="Manage Models & Orgs  ->",
            style="Ghost.TButton",
            command=lambda: self.select_page("management"),
        ).pack(side="left", padx=(0, 8))
        ttk.Button(
            r_loc,
            text="Verify All Gates (check)",
            style="Ghost.TButton",
            command=lambda: self.run_cli(["check"]),
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

        p_card = self._step_card(exp_left, "PILLARS", "The 15 Core Pillars", "")
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

        m_card = self._step_card(exp_right, "REGISTRY", "Registered & Published Models", "")
        fbar = tk.Frame(m_card, bg=PALETTE["card"])
        fbar.pack(fill="x", pady=(0, self._px(6)))
        tk.Label(fbar, text="Filter:", bg=PALETTE["card"], fg=PALETTE["muted"], font=("Segoe UI", 9)).pack(side="left")
        self.model_search_var = tk.StringVar()
        self.model_search_var.trace_add("write", lambda *_: self._populate_models_table())
        ttk.Entry(fbar, textvariable=self.model_search_var, width=26).pack(side="left", padx=(8, 0), fill="x", expand=True)

        self.models_tree = self._make_scrolled_tree(
            m_card,
            columns=("model", "provider", "modalities"),
            height=8,
            fill="both",
            expand=True,
        )
        for col, lbl, w in (
            ("model", "Canonical Slug", 210),
            ("provider", "Provider", 110),
            ("modalities", "T / V / A", 90),
        ):
            self.models_tree.heading(col, text=lbl)
            self.models_tree.column(col, width=self._px(w), anchor="w" if col != "modalities" else "center")

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
        self.kg_cov_filter_var = tk.StringVar(value="All")

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
            text="Check Missing Runs",
            style="Accent.TButton",
            command=lambda: self.run_cli(build_kaggle_args("coverage", model=self.kg_model_var.get())),
        ).pack(side="left", padx=(0, 6))
        ttk.Button(
            r1,
            text="List New Models",
            style="Ghost.TButton",
            command=self._discover_kaggle_models,
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
            width=20,
        )
        cov_filt.pack(side="right")
        cov_filt.bind("<<ComboboxSelected>>", lambda _e: self._populate_kaggle_table())

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

        # STEP 2: Pull Results (in left_col — always visible!)
        s2 = self._step_card(
            left_col,
            "STEP 2",
            "Download Finished Kaggle Results into *.slim.csv",
            "Downloads to .temp/, merges scores per (model, pillar, modality), and auto-cleans .temp/.",
        )
        g2 = tk.Frame(s2, bg=PALETTE["card"])
        g2.pack(fill="x", pady=(0, self._px(8)))
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

        f2 = tk.Frame(g2, bg=PALETTE["card"])
        f2.grid(row=1, column=0, columnspan=4, sticky="ew", pady=(4, 0))
        ttk.Checkbutton(f2, text="Dry run (preview only)", variable=self.kg_dry_var).pack(side="left", padx=(0, 14))
        ttk.Checkbutton(f2, text="Force re-download cached", variable=self.kg_force_var).pack(side="left")
        ttk.Button(
            f2,
            text="Clear Selection",
            style="Ghost.TButton",
            command=self._clear_selected_kaggle_models,
        ).pack(side="right")

        r2 = tk.Frame(s2, bg=PALETTE["card"])
        r2.pack(fill="x")
        ttk.Button(
            r2,
            text="Pull via Google Colab (Slim Only)",
            style="Accent.TButton",
            command=lambda: self._guard_and_run_kaggle(
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
            ),
        ).pack(side="left")

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

        model_specs = [f"{default_registry.resolve_taxonomy(m)[0]}:{m}" for m in _all_model_slugs()]
        self.run_model_var = tk.StringVar(value="ollama:llama3.1:8b")
        self.run_judge_var = tk.StringVar(value="")
        self.run_modality_var = tk.StringVar(value="text")
        self.run_pillars_var = tk.StringVar(value="all")
        self.run_fresh_var = tk.BooleanVar(value=False)

        s1 = self._step_card(
            parent,
            "STEP 1",
            "Configure & Start Local Benchmark Run",
            "Results checkpoint automatically per item so interrupted runs resume cleanly unless 'Fresh run' is checked.",
        )
        form = tk.Frame(s1, bg=PALETTE["card"])
        form.pack(fill="x", pady=(0, self._px(10)))
        form.columnconfigure(1, weight=1)
        form.columnconfigure(3, weight=1)

        ttk.Label(form, text="Model spec", style="CardText.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=5)
        ttk.Combobox(form, textvariable=self.run_model_var, values=model_specs).grid(row=0, column=1, sticky="ew", padx=(0, 14), pady=5)

        ttk.Label(form, text="Judge spec (opt)", style="CardText.TLabel").grid(row=0, column=2, sticky="w", padx=(0, 8), pady=5)
        ttk.Combobox(form, textvariable=self.run_judge_var, values=["", *model_specs]).grid(row=0, column=3, sticky="ew", pady=5)

        ttk.Label(form, text="Modality", style="CardText.TLabel").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=5)
        ttk.Combobox(
            form,
            textvariable=self.run_modality_var,
            values=["text", "vision", "audio", "all"],
            state="readonly",
        ).grid(row=1, column=1, sticky="ew", padx=(0, 14), pady=5)

        ttk.Label(form, text="Pillars", style="CardText.TLabel").grid(row=1, column=2, sticky="w", padx=(0, 8), pady=5)
        ttk.Combobox(form, textvariable=self.run_pillars_var, values=["all", *list_pillars()]).grid(row=1, column=3, sticky="ew", pady=5)

        ttk.Checkbutton(form, text="Fresh run (discard prior checkpoint)", variable=self.run_fresh_var).grid(
            row=2, column=1, sticky="w", pady=(4, 0)
        )

        self.run_preview_var = tk.StringVar()
        for var in (self.run_model_var, self.run_judge_var, self.run_modality_var, self.run_pillars_var, self.run_fresh_var):
            var.trace_add("write", lambda *_: self._update_run_preview())
        self._update_run_preview()

        prev = tk.Frame(s1, bg=PALETTE["console_bg"], padx=self._px(10), pady=self._px(7))
        prev.pack(fill="x", pady=(0, self._px(10)))
        tk.Label(
            prev,
            textvariable=self.run_preview_var,
            bg=PALETTE["console_bg"],
            fg=PALETTE["info"],
            font=("Consolas", 9),
            anchor="w",
        ).pack(fill="x")

        r1 = tk.Frame(s1, bg=PALETTE["card"])
        r1.pack(fill="x")
        ttk.Button(r1, text="Start Local Run", style="Accent.TButton", command=self._start_local_run).pack(side="left")

        # STEP 2: Compare
        s2 = self._step_card(
            parent,
            "STEP 2",
            "Compare Completed Local Runs",
            "Aggregates runs/*/submission.csv into runs/all_submissions.csv and prints a pillar comparison matrix.",
        )
        ttk.Button(s2, text="Compare Local Runs (compare)", style="Ghost.TButton", command=lambda: self.run_cli(["compare"])).pack(
            anchor="w"
        )

        # STEP 3: Publish
        s3 = self._step_card(
            parent,
            "STEP 3",
            "Promote Finished Run to submissions/ & Rebuild Site",
            "Copies runs/<slug>/submission.csv into submissions/<vendor>/<family>/<slug>.csv and rebuilds docs/assets/data/.",
        )
        prow = tk.Frame(s3, bg=PALETTE["card"])
        prow.pack(fill="x")
        self.pub_slug_var = tk.StringVar(value="")
        self.pub_combo = ttk.Combobox(prow, textvariable=self.pub_slug_var, values=[])
        self.pub_combo.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.pub_dry_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(prow, text="Dry run", variable=self.pub_dry_var).pack(side="left", padx=(0, 10))
        ttk.Button(prow, text="Publish & Rebuild Site", style="Accent.TButton", command=self._publish_selected_run).pack(side="left")

    # ------------------------------------------------------------------ 4. Management

    def _build_management_page(self, parent: Any) -> None:
        tk, ttk = self.tk, self.ttk
        self._page_header(
            parent,
            "Model & Organization Management",
            "Manage registered models (display names, organizations, families, open-source flags) and organizations in synhalees/models.json.",
        )

        # Model Management Card
        m_card = self._step_card(
            parent,
            "MODEL MANAGEMENT",
            "Registered Models",
            "Add, edit, or remove model registry entries (display_names & taxonomy in synhalees/models.json).",
        )
        m_bar = tk.Frame(m_card, bg=PALETTE["card"])
        m_bar.pack(fill="x", pady=(0, self._px(8)))
        tk.Label(m_bar, text="Filter:", bg=PALETTE["card"], fg=PALETTE["muted"], font=("Segoe UI", 9)).pack(side="left")
        self.mgmt_model_search_var = tk.StringVar()
        self.mgmt_model_search_var.trace_add("write", lambda *_: self._populate_mgmt_tables())
        ttk.Entry(m_bar, textvariable=self.mgmt_model_search_var, width=28).pack(side="left", padx=(8, 12))
        ttk.Button(
            m_bar,
            text="+ Add Model",
            style="Accent.TButton",
            command=lambda: self._open_mgmt_model_dialog(edit_selected=False),
        ).pack(side="left", padx=(0, 8))
        ttk.Button(
            m_bar,
            text="Edit Selected",
            style="Ghost.TButton",
            command=lambda: self._open_mgmt_model_dialog(edit_selected=True),
        ).pack(side="left", padx=(0, 8))
        ttk.Button(
            m_bar,
            text="Delete Selected",
            style="Ghost.TButton",
            command=self._delete_selected_mgmt_model,
        ).pack(side="left")

        self.mgmt_models_tree = self._make_scrolled_tree(
            m_card,
            columns=("canonical", "display", "org", "family", "oss", "tva"),
            height=9,
            selectmode="browse",
        )
        for col, lbl, w in (
            ("canonical", "Canonical Slug", 210),
            ("display", "Display Name", 210),
            ("org", "Organization", 110),
            ("family", "Family", 110),
            ("oss", "Open Source", 95),
            ("tva", "T / V / A", 85),
        ):
            self.mgmt_models_tree.heading(col, text=lbl)
            self.mgmt_models_tree.column(col, width=self._px(w), anchor="w" if col not in ("oss", "tva") else "center")
        self.mgmt_models_tree.bind("<Double-1>", lambda _e: self._open_mgmt_model_dialog(edit_selected=True))

        # Organization Management Card
        o_card = self._step_card(
            parent,
            "ORGANIZATION MANAGEMENT",
            "Organizations / Providers",
            "Create, edit, or remove organizations, logos, colors, and aliases in synhalees/models.json.",
        )
        o_bar = tk.Frame(o_card, bg=PALETTE["card"])
        o_bar.pack(fill="x", pady=(0, self._px(8)))
        ttk.Button(
            o_bar,
            text="+ Add Organization",
            style="Accent.TButton",
            command=lambda: self._open_mgmt_org_dialog(edit_selected=False),
        ).pack(side="left", padx=(0, 8))
        ttk.Button(
            o_bar,
            text="Edit Selected",
            style="Ghost.TButton",
            command=lambda: self._open_mgmt_org_dialog(edit_selected=True),
        ).pack(side="left", padx=(0, 8))
        ttk.Button(
            o_bar,
            text="Delete Selected",
            style="Ghost.TButton",
            command=self._delete_selected_mgmt_org,
        ).pack(side="left")

        self.mgmt_orgs_tree = self._make_scrolled_tree(
            o_card,
            columns=("slug", "name", "logo", "color", "families", "aliases"),
            height=8,
            selectmode="browse",
            show="tree headings",
        )
        self.mgmt_orgs_tree.heading("#0", text="Badge")
        self.mgmt_orgs_tree.column("#0", width=self._px(58), stretch=False, anchor="center")
        for col, lbl, w in (
            ("slug", "Org Slug", 120),
            ("name", "Display Name", 150),
            ("logo", "Logo", 110),
            ("color", "Color", 90),
            ("families", "Families", 180),
            ("aliases", "Aliases", 180),
        ):
            self.mgmt_orgs_tree.heading(col, text=lbl)
            self.mgmt_orgs_tree.column(col, width=self._px(w), anchor="w")
        self.mgmt_orgs_tree.bind("<Double-1>", lambda _e: self._open_mgmt_org_dialog(edit_selected=True))

    # ------------------------------------------------------------------ 5. Site & CI

    def _build_site_page(self, parent: Any) -> None:
        tk, ttk = self.tk, self.ttk
        self._page_header(
            parent,
            "Static Site & Verification Gates",
            "Keep docs/assets/data/* and docs/assets/logo-data.js in sync with submissions/ and run CI checks before committing.",
        )

        self.site_sync_detail_var = tk.StringVar(value="")
        c1 = self._step_card(
            parent,
            "STEP 1",
            "Run Pre-Commit Verification Gates",
            "Checks logo-data.js checksum, leaderboard JSON sync, all 15 dataset CSV schemas, and Python syntax compilation.",
        )
        tk.Label(
            c1,
            textvariable=self.site_sync_detail_var,
            bg=PALETTE["card"],
            fg=PALETTE["info"],
            font=("Segoe UI", 9, "bold"),
        ).pack(anchor="w", pady=(0, self._px(8)))
        ttk.Button(c1, text="Run All Checks (synhalees check)", style="Accent.TButton", command=lambda: self.run_cli(["check"])).pack(
            anchor="w"
        )

        c2 = self._step_card(
            parent,
            "STEP 2",
            "Regenerate Generated Site Files",
            "Never hand-edit docs/assets/data/* or docs/assets/logo-data.js — regenerate them directly here.",
        )
        r2 = tk.Frame(c2, bg=PALETTE["card"])
        r2.pack(fill="x")
        ttk.Button(r2, text="Rebuild Leaderboard Data (site build)", style="Ghost.TButton", command=lambda: self.run_cli(["site", "build"])).pack(
            side="left", padx=(0, 8)
        )
        ttk.Button(r2, text="Rebuild Logo Data (site logos)", style="Ghost.TButton", command=lambda: self.run_cli(["site", "logos"])).pack(
            side="left", padx=(0, 8)
        )
        ttk.Button(r2, text="Preview docs/index.html", style="Ghost.TButton", command=self._open_leaderboard).pack(side="left")

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

        self.pub_combo["values"] = snap.local_runs
        if snap.local_runs and not self.pub_slug_var.get():
            self.pub_slug_var.set(snap.local_runs[0])

        self._populate_pillars_table()
        self._populate_models_table()
        self._populate_mgmt_tables()
        self._populate_kaggle_table()
        self._sync_token_button()

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
        for p in list_pillars():
            pd = load_pillar(p, strict=False)
            self.pillars_tree.insert("", "end", values=(p, len(pd.text), len(pd.vision), len(pd.audio)))

    def _populate_models_table(self) -> None:
        from synhalees.cli import _scan_submissions

        self.models_tree.delete(*self.models_tree.get_children())
        subs = _scan_submissions()
        q = self.model_search_var.get().strip().lower() if hasattr(self, "model_search_var") else ""
        for m in _all_model_slugs():
            prov = default_registry.resolve_taxonomy(m)[0]
            if q and q not in m.lower() and q not in prov.lower():
                continue
            mods = subs.get(m, {}).get("modalities", set())
            tva = "".join(k[0].upper() if k in mods else "-" for k in ("text", "vision", "audio"))
            self.models_tree.insert("", "end", values=(m, prov, tva))

    def _populate_mgmt_tables(self) -> None:
        if not hasattr(self, "mgmt_models_tree"):
            return
        from synhalees.cli import _scan_submissions

        data = json.loads(MODELS_JSON.read_text(encoding="utf-8"))
        providers = dict(data.get("providers") or {})
        disp = dict(data.get("display_names") or {})
        subs = _scan_submissions()
        q = self.mgmt_model_search_var.get().strip().lower() if hasattr(self, "mgmt_model_search_var") else ""

        self.mgmt_models_tree.delete(*self.mgmt_models_tree.get_children())
        all_slugs = sorted({*_all_model_slugs(), *disp.keys()}, key=str.lower)
        for m in all_slugs:
            dname = str(disp.get(m) or default_registry.get_model_display_name(m))
            prov, fam, is_oss = default_registry.resolve_taxonomy(m)
            if q and q not in m.lower() and q not in dname.lower() and q not in prov.lower() and q not in fam.lower():
                continue
            mods = subs.get(m, {}).get("modalities", set())
            tva = "".join(k[0].upper() if k in mods else "-" for k in ("text", "vision", "audio"))
            self.mgmt_models_tree.insert(
                "",
                "end",
                values=(m, dname, prov, fam, "Yes" if is_oss else "No", tva),
            )

        named_colors = dict(data.get("named_colors") or {})
        self._org_icons: dict[tuple[str, str], Any] = {}
        self.mgmt_orgs_tree.delete(*self.mgmt_orgs_tree.get_children())
        for slug in sorted(providers):
            p = providers[slug]
            fams = ", ".join(sorted((p.get("families") or {}).keys()))
            aliases = ", ".join(p.get("aliases") or [])
            logo_slug = str(p.get("logo", slug) or slug)
            color_name = str(p.get("color", "gray") or "gray")
            hex_color = str(named_colors.get(color_name, color_name if color_name.startswith("#") else "#78909C"))
            icon = self._make_org_preview_icon(logo_slug, hex_color)
            self.mgmt_orgs_tree.insert(
                "",
                "end",
                text="",
                image=icon,
                values=(
                    slug,
                    p.get("name", slug),
                    logo_slug,
                    color_name,
                    fams,
                    aliases,
                ),
            )

    def _load_logo_png_b64(self) -> dict[str, str]:
        if hasattr(self, "_logo_png_cache"):
            return self._logo_png_cache
        cache: dict[str, str] = {}
        js_path = ROOT / "docs" / "assets" / "logo-data.js"
        if js_path.exists():
            import re

            text = js_path.read_text(encoding="utf-8", errors="replace")
            for m in re.finditer(r'"([^"]+)":\s*"data:image/png;base64,([^"]+)"', text):
                cache[m.group(1)] = m.group(2)
        self._logo_png_cache = cache
        return cache

    def _make_org_preview_icon(self, logo_slug: str, hex_color: str) -> Any:
        key = (logo_slug, hex_color)
        if key in self._org_icons:
            return self._org_icons[key]
        tk = self.tk
        badge = tk.PhotoImage(master=self.root, width=38, height=16)

        # Left 16x16: actual PNG logo if embedded in logo-data.js, else colored icon tile
        png_b64 = self._load_logo_png_b64().get(logo_slug)
        drew_png = False
        if png_b64:
            try:
                raw = tk.PhotoImage(master=self.root, data=png_b64)
                step = max(1, max(raw.width(), raw.height()) // 16)
                small = raw.subsample(step, step) if step > 1 else raw
                w = min(16, small.width())
                h = min(16, small.height())
                ox = (16 - w) // 2
                oy = (16 - h) // 2
                badge.put("#FFFFFF", to=(0, 0, 16, 16))
                for y in range(h):
                    for x in range(w):
                        if not small.transparency_get(x, y):
                            r, g, b = small.get(x, y)
                            badge.put(f"#{r:02x}{g:02x}{b:02x}", to=(ox + x, oy + y))
                drew_png = True
            except Exception:
                drew_png = False
        if not drew_png:
            badge.put("#334155", to=(0, 0, 16, 16))
            badge.put(hex_color, to=(1, 1, 15, 15))
            badge.put("#FFFFFF", to=(6, 6, 10, 10))

        # Right 14x14: actual color swatch
        badge.put("#475569", to=(22, 1, 36, 15))
        badge.put(hex_color, to=(23, 2, 35, 14))
        self._org_icons[key] = badge
        return badge

    def _open_mgmt_model_dialog(self, *, edit_selected: bool = False) -> None:
        tk, ttk = self.tk, self.ttk
        sel = self.mgmt_models_tree.selection() if edit_selected else ()
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
        org_choices = sorted((data.get("providers") or {}).keys())

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
        ttk.Combobox(wrap, textvariable=org_var, values=org_choices, state="readonly").grid(row=2, column=1, sticky="ew", padx=(10, 0), pady=6)

        tk.Label(wrap, text="Family", bg=PALETTE["surface"], fg=PALETTE["muted"], font=("Segoe UI", 9)).grid(row=3, column=0, sticky="w", pady=6)
        ttk.Entry(wrap, textvariable=fam_var).grid(row=3, column=1, sticky="ew", padx=(10, 0), pady=6)

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

    def _delete_selected_mgmt_model(self) -> None:
        sel = self.mgmt_models_tree.selection()
        if not sel:
            self.messagebox.showinfo("Select Model", "Select a model row to delete.")
            return
        canon = str(self.mgmt_models_tree.item(sel[0], "values")[0])
        if not self.messagebox.askyesno(
            "Delete Model",
            f"Delete '{canon}' from models.json, submissions/, and kaggle/results/*.slim.csv?",
        ):
            return
        res = delete_model_completely(canon)
        if res["registry"] or res["submissions"] or res["slim_rows"]:
            self._append_log(
                f"[OK] Deleted '{canon}' (registry={res['registry']}, submissions={res['submissions']}, slim_rows={res['slim_rows']}).\n",
                "ok",
            )
            self.refresh_all()
            if res["submissions"] > 0:
                self.run_cli(["build"])
        else:
            self.messagebox.showinfo("Nothing to Delete", f"No files or registry entries found for '{canon}'.")

    def _open_mgmt_org_dialog(self, *, edit_selected: bool = False) -> None:
        tk, ttk = self.tk, self.ttk
        sel = self.mgmt_orgs_tree.selection() if edit_selected else ()
        if edit_selected and not sel:
            self.messagebox.showinfo("Select Organization", "Select an organization row first to edit.")
            return
        vals = self.mgmt_orgs_tree.item(sel[0], "values") if sel else ("", "", "", "gray", "", "")

        data = json.loads(MODELS_JSON.read_text(encoding="utf-8"))
        named_colors = dict(data.get("named_colors") or {"gray": "#78909C"})
        color_choices = sorted(named_colors.keys())

        dlg = tk.Toplevel(self.root)
        dlg.title("Edit Organization" if edit_selected else "Add Organization")
        dlg.configure(bg=PALETTE["surface"])
        dlg.transient(self.root)
        dlg.grab_set()
        self._center_dialog(dlg, self._px(520), self._px(310))

        wrap = tk.Frame(dlg, bg=PALETTE["surface"], padx=self._px(18), pady=self._px(16))
        wrap.pack(fill="both", expand=True)
        wrap.columnconfigure(1, weight=1)

        slug_var = tk.StringVar(value=str(vals[0]))
        name_var = tk.StringVar(value=str(vals[1]))
        logo_var = tk.StringVar(value=str(vals[2]))
        color_var = tk.StringVar(value=str(vals[3]) or "gray")
        aliases_var = tk.StringVar(value=str(vals[5]))

        tk.Label(wrap, text="Org Slug", bg=PALETTE["surface"], fg=PALETTE["muted"], font=("Segoe UI", 9)).grid(row=0, column=0, sticky="w", pady=6)
        slug_ent = ttk.Entry(wrap, textvariable=slug_var)
        slug_ent.grid(row=0, column=1, sticky="ew", padx=(10, 0), pady=6)
        if edit_selected:
            slug_ent.configure(state="readonly")

        tk.Label(wrap, text="Display Name", bg=PALETTE["surface"], fg=PALETTE["muted"], font=("Segoe UI", 9)).grid(row=1, column=0, sticky="w", pady=6)
        ttk.Entry(wrap, textvariable=name_var).grid(row=1, column=1, sticky="ew", padx=(10, 0), pady=6)

        tk.Label(wrap, text="Logo Slug", bg=PALETTE["surface"], fg=PALETTE["muted"], font=("Segoe UI", 9)).grid(row=2, column=0, sticky="w", pady=6)
        logo_row = tk.Frame(wrap, bg=PALETTE["surface"])
        logo_row.grid(row=2, column=1, sticky="ew", padx=(10, 0), pady=6)
        logo_row.columnconfigure(0, weight=1)
        ttk.Entry(logo_row, textvariable=logo_var).grid(row=0, column=0, sticky="ew")
        logo_preview_lbl = tk.Label(logo_row, bg=PALETTE["surface"], fg=PALETTE["muted"], font=("Segoe UI", 8))
        logo_preview_lbl.grid(row=0, column=1, padx=(8, 0))

        tk.Label(wrap, text="Color", bg=PALETTE["surface"], fg=PALETTE["muted"], font=("Segoe UI", 9)).grid(row=3, column=0, sticky="w", pady=6)
        color_row = tk.Frame(wrap, bg=PALETTE["surface"])
        color_row.grid(row=3, column=1, sticky="ew", padx=(10, 0), pady=6)
        color_row.columnconfigure(0, weight=1)
        ttk.Combobox(color_row, textvariable=color_var, values=color_choices, state="readonly").grid(row=0, column=0, sticky="ew")
        swatch_box = tk.Frame(color_row, width=self._px(24), height=self._px(20), bg="#78909C", highlightthickness=1, highlightbackground=PALETTE["border"])
        swatch_box.grid(row=0, column=1, padx=(8, 4))
        swatch_hex_lbl = tk.Label(color_row, text="#78909C", bg=PALETTE["surface"], fg=PALETTE["muted"], font=("Consolas", 9))
        swatch_hex_lbl.grid(row=0, column=2)

        def _sync_org_previews(*_args: Any) -> None:
            cname = color_var.get().strip()
            hcol = str(named_colors.get(cname, cname if cname.startswith("#") else "#78909C"))
            swatch_box.configure(bg=hcol)
            swatch_hex_lbl.configure(text=hcol)
            lslug = logo_var.get().strip()
            svg_exists = bool(lslug and (ROOT / "docs" / "assets" / "logos" / f"{lslug}.svg").exists())
            icon = self._make_org_preview_icon(lslug or slug_var.get().strip() or "org", hcol)
            logo_preview_lbl.configure(
                image=icon,
                compound="left",
                text=f" {'OK' if svg_exists else 'no .svg'}",
                fg=PALETTE["ok"] if svg_exists else PALETTE["warn"],
            )
            logo_preview_lbl._icon_ref = icon

        color_var.trace_add("write", _sync_org_previews)
        logo_var.trace_add("write", _sync_org_previews)
        _sync_org_previews()

        tk.Label(wrap, text="Aliases (comma-sep)", bg=PALETTE["surface"], fg=PALETTE["muted"], font=("Segoe UI", 9)).grid(row=4, column=0, sticky="w", pady=6)
        ttk.Entry(wrap, textvariable=aliases_var).grid(row=4, column=1, sticky="ew", padx=(10, 0), pady=6)

        btns = tk.Frame(wrap, bg=PALETTE["surface"])
        btns.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(14, 0))

        def _save() -> None:
            try:
                save_organization_entry(
                    slug_var.get(),
                    name=name_var.get(),
                    logo=logo_var.get(),
                    color=color_var.get(),
                    aliases=[a.strip() for a in aliases_var.get().split(",") if a.strip()],
                )
            except ValueError as exc:
                self.messagebox.showerror("Validation Error", str(exc), parent=dlg)
                return
            self._append_log(f"[OK] Saved organization '{slug_var.get().strip().lower()}' in synhalees/models.json.\n", "ok")
            dlg.destroy()
            self.refresh_all()

        ttk.Button(btns, text="Save Organization", style="Accent.TButton", command=_save).pack(side="right", padx=(8, 0))
        ttk.Button(btns, text="Cancel", style="Ghost.TButton", command=dlg.destroy).pack(side="right")

    def _delete_selected_mgmt_org(self) -> None:
        sel = self.mgmt_orgs_tree.selection()
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
        try:
            model_tasks, _, _ = gather_coverage(check_remote=False)
        except Exception:
            model_tasks = {}
        for m in sorted(model_tasks):
            st = model_tasks[m]
            is_complete = st["text"] >= 15 and st["vision"] > 0 and st["audio"] > 0
            if mode == "Incomplete only" and is_complete:
                continue
            if mode == "Complete 15/15 T+V+A" and not is_complete:
                continue
            self.kg_tree.insert(
                "",
                "end",
                values=(
                    m,
                    f"{st['text']}/15",
                    "DONE" if st["vision"] > 0 else "missing",
                    "DONE" if st["audio"] > 0 else "missing",
                ),
            )

    # ------------------------------------------------------------------ Actions

    def _update_run_preview(self) -> None:
        args = build_run_args(
            model=self.run_model_var.get(),
            judge=self.run_judge_var.get(),
            modality=self.run_modality_var.get(),
            pillars=self.run_pillars_var.get(),
            fresh=self.run_fresh_var.get(),
        )
        self.run_preview_var.set(format_cmd_preview(args))

    def _start_local_run(self) -> None:
        if not self.run_model_var.get().strip():
            self._append_log("[ERR] Please enter a model spec (e.g. ollama:llama3.1:8b).\n", "err")
            return
        self._start_worker_if_free(
            lambda: self.run_cli(
                build_run_args(
                    model=self.run_model_var.get(),
                    judge=self.run_judge_var.get(),
                    modality=self.run_modality_var.get(),
                    pillars=self.run_pillars_var.get(),
                    fresh=self.run_fresh_var.get(),
                )
            )
        )

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
        self.run_cli(args)

    def _open_leaderboard(self) -> None:
        index_html = ROOT / "docs" / "index.html"
        if index_html.is_file():
            webbrowser.open(index_html.resolve().as_uri())
            self._append_log(f"[OK] Opened {index_html}\n", "ok")
        else:
            self._append_log(f"[ERR] Missing {index_html}\n", "err")

    def _open_tokens_dialog(self) -> None:
        """Open a modal dialog to configure Kaggle tokens & Local Eval API keys."""
        tk, ttk = self.tk, self.ttk
        dlg = tk.Toplevel(self.root)
        dlg.title("Tokens & API Keys")
        dlg.configure(bg=PALETTE["surface"])
        dlg.transient(self.root)
        dlg.grab_set()
        w, h = self._px(540), self._px(490)
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
        tk.Label(
            wrap,
            text=f"Kaggle Auth: {kaggle_auth_status()}  ·  Paste KAGGLE_API_TOKEN (KGAT_...) or kaggle.json",
            bg=PALETTE["surface"],
            fg=PALETTE["info"],
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
            dlg.destroy()

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
            self._append_log("[OK] Cleared saved Kaggle token overrides (will use ~/.kaggle/kaggle.json if present).\n", "ok")
            dlg.destroy()

        ttk.Button(btns, text="Cancel", style="Ghost.TButton", command=dlg.destroy).pack(side="right")
        ttk.Button(btns, text="Clear Kaggle", style="Ghost.TButton", command=_clear_kaggle_token).pack(
            side="right", padx=(0, 8)
        )
        ttk.Button(btns, text="Save Tokens", style="Accent.TButton", command=_save_and_close).pack(
            side="right", padx=(0, 8)
        )

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
        """Block proceeding to Step 2/3/4 if selected/new models lack valid org/name/OSS."""
        if action in ("pull", "submit", "run", "publish"):
            selected = [m.strip() for m in re.split(r"[,\s]+", self.kg_model_var.get().strip()) if m.strip()]
            if action == "run" and not selected:
                self.kg_discover_status_var.set("Select a model on the right before running on Kaggle.")
                self._append_log("[WARN] Select a model on the right before running on Kaggle.\n", "warn")
                return
            check_list = selected
            if not check_list and hasattr(self, "kg_new_tree"):
                check_list = [
                    str(self.kg_new_tree.item(i, "values")[0])
                    for i in self.kg_new_tree.get_children()
                    if self.kg_new_tree.item(i, "values")
                ]
            unreg = find_unregistered_models(check_list)
            if unreg:
                msg = f"Complete Canonical, Organization & Open Source for: {', '.join(unreg)}"
                self.kg_discover_status_var.set(msg)
                self._append_log(f"[WARN] {msg}\n", "warn")
                self._open_edit_models_dialog(only_slugs=set(unreg))
                return
        self.run_cli(cli_args)

    def _open_edit_models_dialog(self, *, only_slugs: set[str] | None = None) -> None:
        """Centered dialog to edit Canonical, Display Name, Org (+ New Org), Family & OSS."""
        tk, ttk = self.tk, self.ttk
        sel = self.kg_new_tree.selection() or self.kg_new_tree.get_children()
        if only_slugs:
            matched = [
                i for i in self.kg_new_tree.get_children()
                if canonical_model_slug(str(self.kg_new_tree.item(i, "values")[0])) in only_slugs
            ]
            if matched:
                sel = tuple(matched)
        if not sel:
            self._append_log("[WARN] No models to edit. Click 'List New Models' first.\n", "warn")
            return

        dlg = tk.Toplevel(self.root)
        dlg.title("Edit Canonical, Organization & License")
        dlg.configure(bg=PALETTE["surface"])
        dlg.transient(self.root)
        dlg.grab_set()
        w = self._px(940)
        h = min(self._px(220 + len(sel) * 44), self._px(580))
        dlg.minsize(self._px(780), self._px(270))
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

        new_orgs_pending: dict[str, dict[str, str]] = {}
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

        for idx, item_id in enumerate(sel, start=1):
            vals = self.kg_new_tree.item(item_id, "values")
            if not vals:
                continue
            raw_slug = str(vals[0])
            cur_canon = str(vals[1])
            cur_disp = default_registry.get_model_display_name(cur_canon)
            v_slug, f_slug, is_oss = default_registry.resolve_taxonomy(cur_canon)
            init_org = "" if v_slug in _UNKNOWN_ORG_TOKENS else v_slug
            init_fam = "" if f_slug in _UNKNOWN_ORG_TOKENS else f_slug

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
            self._center_dialog(sub, self._px(420), self._px(250))
            swrap = tk.Frame(sub, bg=PALETTE["surface"], padx=self._px(16), pady=self._px(14))
            swrap.pack(fill="both", expand=True)
            swrap.columnconfigure(1, weight=1)

            slug_v = tk.StringVar()
            name_v = tk.StringVar()
            col_v = tk.StringVar(value="blue")
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

            tk.Label(swrap, textvariable=serr_v, bg=PALETTE["surface"], fg=PALETTE["accent"], font=("Segoe UI", 9)).grid(
                row=3, column=0, columnspan=2, sticky="w", pady=(4, 6)
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
            sbtns.grid(row=4, column=0, columnspan=2, sticky="e")
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
            except ValueError as exc:
                err_var.set(str(exc))
                return

            for (item_id, raw_slug, c_var, *_), ent in zip(rows_data, entries):
                self.kg_new_tree.item(item_id, values=(raw_slug, ent["canonical"], "Registered"))

            if saved:
                self._append_log(f"[OK] Saved {saved} model(s) to synhalees/models.json\n", "ok")
                self.kg_discover_status_var.set(f"Saved {saved} model(s) to synhalees/models.json.")
                self.refresh_all()
            dlg.destroy()

        ttk.Button(btns, text="Cancel", style="Ghost.TButton", command=dlg.destroy).pack(side="right")
        ttk.Button(btns, text="Save to models.json", style="Accent.TButton", command=_save_models).pack(
            side="right", padx=(0, 8)
        )

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

    def _discover_kaggle_models(self) -> None:
        """Query `kaggle b t models` in background and display new models in Step 1."""
        with self._proc_lock:
            if self._proc is not None and self._proc.poll() is None:
                self._append_log("[WARN] A command is already running. Stop it first.\n", "warn")
                return

        self.select_page("kaggle")
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

    def run_cli(self, args: list[str]) -> None:
        with self._proc_lock:
            if self._proc is not None and self._proc.poll() is None:
                self._append_log("[WARN] A command is already running. Stop it first.\n", "warn")
                return

        self._last_args = list(args)
        self._last_err_line = ""
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
            if not self.progress_wrap.winfo_ismapped():
                self.progress_wrap.pack(fill="x", padx=self._px(12), pady=(self._px(6), 0), before=self.cbar)
            self.progress_bar.start(12)
        else:
            self.status_badge.configure(text="IDLE", bg=PALETTE["card"], fg=PALETTE["ok"])
            self.stop_btn.configure(state="disabled")
            self.progress_bar.stop()
            self.progress_wrap.pack_forget()
            self._set_workflow_buttons_state("normal")

    def _drain_queue(self) -> None:
        try:
            while True:
                kind, payload = self._log_queue.get_nowait()
                if kind == "line":
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
                elif kind == "discovered":
                    total_avail, pending = payload
                    self.kg_new_tree.delete(*self.kg_new_tree.get_children())
                    if pending:
                        for raw, canon, st_lbl in pending:
                            self.kg_new_tree.insert("", "end", values=(raw, canon, st_lbl))
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
                    if (
                        rc == 0
                        and len(getattr(self, "_last_args", [])) >= 2
                        and self._last_args[0] == "kaggle"
                        and self._last_args[1] == "pull"
                        and "--dry-run" not in self._last_args
                    ):
                        self._open_pull_complete_dialog()
        except queue.Empty:
            pass
        self.root.after(70, self._drain_queue)

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

    def _copy_last_cmd(self) -> None:
        preview = self._last_cmd_text or format_cmd_preview(self._last_args)
        self.root.clipboard_clear()
        self.root.clipboard_append(preview)
        self._append_log(f"[OK] Copied to clipboard: {preview}\n", "muted")


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
