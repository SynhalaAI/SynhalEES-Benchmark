"""Kaggle Benchmarks operations, task mapping, artifact parsing & slim-CSV sync."""

from __future__ import annotations

import csv
import io
import json
import os
import re
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Iterable

from synhalees.registry import canonical_model_slug, default_registry

ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / "runs"
SUBMISSIONS = ROOT / "submissions"
KG_RESULTS = ROOT / "kaggle" / "results"
TASKS_DIR = ROOT / "kaggle" / "tasks"

HEADER = ["model", "provider", "date", "pillar", "modality", "score"]
# Kaggle imports add usage telemetry (optional; local runs may omit it).
EXT_HEADER = HEADER + ["cost_usd", "tokens", "latency_ms"]
MODALITIES = ("text", "vision", "audio", "all")

_STATUS_MODELS_CACHE: dict[str, list[str]] = {}
_MODELS_CACHE: list[str] | None = None


def display_path(path: Path, base: Path = ROOT) -> str:
    """Format *path* relative to *base* when inside it, otherwise as a full path."""
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)


def resolve_submission_path(model_id: str, provider: str = "") -> Path:
    """Resolve or infer hierarchical submission path: submissions/<vendor>/<family>/<model>.csv."""
    del provider  # taxonomy resolves from canonical model slug
    return default_registry.resolve_submission_path(model_id, base_dir=SUBMISSIONS)


def sh(argv: Iterable[object], cwd: Path = ROOT, *, echo: bool = True) -> int:
    """Run a subprocess with inherited stdio; return its exit code."""
    cmd = [str(a) for a in argv]
    if echo:
        print("+ " + " ".join(cmd), flush=True)
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    return subprocess.run(cmd, cwd=str(cwd), env=env).returncode


def py_run(script: Path | str, args: Iterable[object] = ()) -> int:
    """Run a Python script in-repo via the active interpreter."""
    path = Path(script)
    if not path.exists():
        raise SystemExit(f"missing {path} -- run from the repo checkout (pip install -e .)")
    return sh([sys.executable, str(path), *[str(a) for a in args]])


def kaggle_bin() -> str:
    """Locate the `kaggle` CLI executable or raise a helpful SystemExit."""
    found = shutil.which("kaggle") or shutil.which("kaggle.exe")
    if found:
        return found
    for candidate in (
        Path(sys.executable).parent / "Scripts" / "kaggle.exe",
        Path(sys.executable).parent / "kaggle",
        Path.home() / "AppData/Local/Programs/Python/Python313/Scripts/kaggle.exe",
    ):
        if candidate.exists():
            return str(candidate)
    raise SystemExit("kaggle CLI not found on PATH (pip install kaggle, then 'kaggle b init')")


def task_slug(arg: object) -> str:
    """Kaggle normalizes task names: 'My_Task' / 'my task' -> 'my-task'."""
    return re.sub(r"[^a-z0-9]+", "-", str(arg).lower()).strip("-")


def resolve_task_file(arg: object) -> Path:
    """'05_sinhala_grammar' | 'synhalees-05-sinhala-grammar' -> kaggle/tasks/<file>.py"""
    direct = Path(str(arg))
    if not direct.is_absolute():
        direct = ROOT / direct
    if direct.suffix == ".py" and direct.is_file():
        return direct
    name = Path(str(arg)).name.replace("-", "_")
    if name.startswith("synhalees_"):
        name = name[len("synhalees_"):]
    cand = TASKS_DIR / f"{name}.py"
    if cand.is_file():
        return cand
    raise SystemExit(f"no task file for {arg!r} (expected {TASKS_DIR}/*.py)")


def pillar_from_task(slug: str) -> str | None:
    """'synhalees-05-sinhala-grammar' -> '05_sinhala_grammar'; combined tasks -> None."""
    parts = [p for p in slug.split("-") if p]
    if len(parts) >= 3 and parts[0] == "synhalees" and parts[1].isdigit():
        return parts[1] + "_" + "_".join(parts[2:])
    return None


def task_name_for(path: Path | str) -> str:
    """kaggle/tasks/05_sinhala_grammar.py -> 'synhalees-05-sinhala-grammar'."""
    stem = Path(path).stem
    if stem.startswith("synhalees_"):
        stem = stem[len("synhalees_"):]
    return task_slug(f"synhalees_{stem}")


def resolve_task_name(arg: object) -> str:
    """'audio' | 'audio.py' | 'synhalees-audio' -> 'synhalees-audio'."""
    text = str(arg)
    if text.lower().endswith(".py"):
        return task_name_for(text)
    slug = task_slug(text)
    return slug if slug.startswith("synhalees-") else task_slug(f"synhalees_{slug}")


def task_modality(name: str) -> str:
    """'synhalees-vision' -> 'vision'; 'synhalees-audio' -> 'audio'; pillars -> 'text'."""
    if name in ("synhalees-vision", "synhalees-audio"):
        return name.split("-", 1)[1]
    return "text"


def parse_modality(value: object) -> tuple[str, ...]:
    """'text' | 'vision | audio' | 'vision,audio' -> normalized tuple of modality tokens."""
    parts: list[str] = []
    for chunk in str(value).replace("|", ",").split(","):
        parts += [w.lower() for w in str(chunk).split() if w]
    if not parts:
        raise SystemExit(f"--modality needs one of: {', '.join(MODALITIES)}")
    bad = [p for p in parts if p not in MODALITIES]
    if bad:
        raise SystemExit(
            f"--modality must be one or more of: {', '.join(MODALITIES)} "
            f"(combine with | or ,): {', '.join(bad)}"
        )
    if "all" in parts:
        return ("all",)
    return tuple(dict.fromkeys(parts))


def kaggle_available_models() -> list[str]:
    """List globally available Kaggle model slugs via `kaggle b t models`."""
    global _MODELS_CACHE
    if _MODELS_CACHE is not None:
        return _MODELS_CACHE
    try:
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
        proc = subprocess.run(
            [str(kaggle_bin()), "b", "t", "models"],
            cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=60,
        )
    except (Exception, SystemExit):
        return []
    if proc.returncode:
        return []
    models: list[str] = []
    for line in (proc.stdout or "").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("Slug", "Display", "Avail", "---")):
            continue
        parts = stripped.split()
        if parts:
            models.append(parts[0])
    _MODELS_CACHE = models
    return models


def kaggle_task_models(task_name: str) -> list[str]:
    """List model slugs Kaggle knows for one task via `kaggle b t status <task>`."""
    if task_name in _STATUS_MODELS_CACHE:
        return _STATUS_MODELS_CACHE[task_name]
    try:
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
        proc = subprocess.run(
            [str(kaggle_bin()), "b", "t", "status", task_name],
            cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=120,
        )
    except (Exception, SystemExit):
        return []
    if proc.returncode:
        return []
    models: list[str] = []
    for line in (proc.stdout or "").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith((
            "Task:", "Version:", "Status:", "Created:",
            "Public:", "Task URL:", "Model", "─", "View logs:",
        )):
            continue
        parts = stripped.split()
        if not parts:
            continue
        if not any(
            w in stripped
            for w in ("Completed", "Running", "Queued", "Failed", "Cancelled", "Pending")
        ):
            continue
        models.append(parts[0])
    _STATUS_MODELS_CACHE[task_name] = models
    return models


def resolve_kaggle_model_names(
    requested: Iterable[str],
    available: Iterable[str],
) -> tuple[list[str], list[str]]:
    """Map each user-requested `-m` name to Kaggle's exact server name(s)."""
    avail_list = list(available)
    by_canon: dict[str, list[str]] = {}
    for avail in avail_list:
        by_canon.setdefault(canonical_model_slug(avail), []).append(avail)

    resolved: list[str] = []
    unmatched: list[str] = []
    for req in requested:
        if req in avail_list:
            if req not in resolved:
                resolved.append(req)
            continue
        canon = canonical_model_slug(req)
        hits = list(by_canon.get(canon, []))
        for canon_key, names in by_canon.items():
            if canon_key != canon and canon_key.startswith(canon + "-"):
                for n in names:
                    if n not in hits:
                        hits.append(n)
        if hits:
            for h in hits:
                if h not in resolved:
                    resolved.append(h)
            if len(hits) == 1 and hits[0] != req:
                print(f"  [i] model '{req}' -> Kaggle name '{hits[0]}'")
            elif len(hits) > 1:
                print(f"  [i] model '{req}' -> {len(hits)} Kaggle name(s): {', '.join(hits)}")
        else:
            unmatched.append(req)
            resolved.append(req)
    if unmatched:
        print(
            f"  [!] no Kaggle match for: {', '.join(unmatched)} "
            "(run 'synhalees kaggle status <task>' to list available models)"
        )
    return resolved, unmatched


def rewrite_model_args(extra_args: list[str], task_name: str) -> list[str]:
    """Rewrite `-m` values in download args to Kaggle's exact server names."""
    if not extra_args:
        return list(extra_args)
    wanted: list[str] = []
    for i, tok in enumerate(extra_args):
        if str(tok) in ("-m", "--model") and i + 1 < len(extra_args):
            wanted.append(str(extra_args[i + 1]))
    if not wanted:
        return list(extra_args)
    available = kaggle_task_models(task_name)
    if not available:
        return list(extra_args)

    resolve_kaggle_model_names(wanted, available)

    by_canon: dict[str, list[str]] = {}
    for avail in available:
        by_canon.setdefault(canonical_model_slug(avail), []).append(avail)
    expansions: list[list[str]] = []
    for req in wanted:
        canon = canonical_model_slug(req)
        hits = [a for a in available if a == req]
        for ck, names in by_canon.items():
            if ck == canon or ck.startswith(canon + "-"):
                for n in names:
                    if n not in hits:
                        hits.append(n)
        expansions.append(hits or [req])

    out: list[str] = []
    k = 0
    args = list(extra_args)
    i = 0
    while i < len(args):
        if str(args[i]) in ("-m", "--model") and i + 1 < len(args) and k < len(expansions):
            flag = args[i]
            for h in expansions[k]:
                out.extend([flag, h])
            k += 1
            i += 2
        else:
            out.append(args[i])
            i += 1
    return out


def load_scorecard(path: Path) -> list[list[str]]:
    """Read a scorecard CSV and validate its required header."""
    with open(path, newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.reader(fh))
    if not rows or rows[0][:len(HEADER)] != HEADER:
        raise SystemExit(f"{path}: expected header {','.join(HEADER)}")
    return rows


def load_slim_rows(path: Path) -> dict[tuple[str, str, str], list[str]]:
    """Load existing slim-CSV rows keyed by (model, pillar, modality); {} when absent."""
    if not Path(path).is_file():
        return {}
    with open(path, newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.reader(fh))
    if not rows or rows[0][:len(EXT_HEADER)] != EXT_HEADER:
        return {}
    keyed: dict[tuple[str, str, str], list[str]] = {}
    for row in rows[1:]:
        if len(row) < len(HEADER):
            continue
        full = list(row)[:len(EXT_HEADER)] + [""] * (len(EXT_HEADER) - len(row))
        full[0] = canonical_model_slug(full[0])
        key = (full[0], full[3], full[4])
        previous = keyed.get(key)
        if previous is None or full[2] >= previous[2]:
            keyed[key] = full
    return keyed


def normalize_pulled_runs(task_slug_name: str, base_dir: Path | None = None) -> int:
    """Rename Kaggle's run folders under <base_dir>/<task>/ to canonical slugs."""
    base = base_dir or KG_RESULTS
    task_dir = base / task_slug_name
    if not task_dir.is_dir():
        return 0
    renamed = 0
    for version in sorted(d for d in task_dir.iterdir() if d.is_dir()):
        for model_dir in sorted(d for d in version.iterdir() if d.is_dir()):
            canon = canonical_model_slug(model_dir.name)
            if not canon or canon == model_dir.name:
                continue
            target = model_dir.with_name(canon)
            if target.exists():
                for run in sorted(r for r in model_dir.iterdir() if r.is_dir()):
                    dest = target / run.name
                    if not dest.exists():
                        run.rename(dest)
                if not any(model_dir.iterdir()):
                    model_dir.rmdir()
                    renamed += 1
                continue
            model_dir.rename(target)
            renamed += 1
    return renamed


def _note_normalized(task_slug_name: str, count: int) -> None:
    if count:
        print(f"  [i] normalized {count} run folder(s) in {task_slug_name}/ to canonical slugs")


def kaggle_run_usage(result_path: Path) -> tuple[str, str, str]:
    """Return (cost_usd, tokens, avg_latency_ms) from sibling *.run.json, or blanks."""
    runs = sorted(result_path.parent.glob("*.run.json"))
    if not runs:
        return "", "", ""
    try:
        data = json.loads(runs[0].read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "", "", ""

    conversations: list[dict] = []

    def walk(node: object) -> None:
        if isinstance(node, dict):
            conversations.extend(node.get("conversations") or [])
            for sub in node.get("subruns") or []:
                walk(sub)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(data)
    tokens = 0
    nanodollars = 0
    latencies: list[int] = []
    for conv in conversations:
        metrics = conv.get("metrics") or {}
        if not metrics:
            continue
        tokens += int(metrics.get("inputTokens") or 0)
        tokens += int(metrics.get("outputTokens") or 0)
        nanodollars += int(metrics.get("inputTokensCostNanodollars") or 0)
        nanodollars += int(metrics.get("outputTokensCostNanodollars") or 0)
        latency = metrics.get("totalBackendLatencyMs")
        if latency:
            latencies.append(int(latency))
    if not tokens:
        return "", "", ""
    latency_ms = round(sum(latencies) / len(latencies)) if latencies else ""
    return f"{nanodollars / 1e9:.6f}", str(tokens), str(latency_ms)


def collect_task_results(task_dir: Path) -> dict[str, tuple[str, float, Path]]:
    """Collect newest scorecard result per canonical model under task_dir."""
    found: dict[str, tuple[str, float, Path]] = {}
    unparsed_warnings: list[str] = []

    # Pass 1: *.result.json (legacy)
    for f in sorted(task_dir.rglob("*.result.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError) as err:
            unparsed_warnings.append(f"{f.name}: unreadable JSON ({err})")
            continue
        info = data.get("agent_info") or {}
        name = (info.get("model_info") or {}).get("name") or f.parent.parent.name
        fin = data.get("finished_at") or data.get("started_at") or ""
        score = ((data.get("verifier_result") or {}).get("rewards") or {}).get("score")
        try:
            pct = float(score)
        except (TypeError, ValueError):
            unparsed_warnings.append(f"{f.name}: missing/invalid rewards.score ({score!r})")
            continue
        cname = canonical_model_slug(name)
        if cname not in found or fin > found[cname][0]:
            found[cname] = (fin, pct, f)

    # Pass 2: *.run.json (modern fallback)
    for f in sorted(task_dir.rglob("*.run.json")):
        if list(f.parent.glob("*.result.json")):
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError) as err:
            unparsed_warnings.append(f"{f.name}: unreadable JSON ({err})")
            continue
        mv = data.get("modelVersion") or {}
        name = mv.get("slug") or f.parent.parent.name
        fin = data.get("endTime") or data.get("startTime") or ""
        res_list = data.get("results") or []
        score = None
        for r in res_list:
            if isinstance(r, dict) and "numericResult" in r:
                score = r["numericResult"].get("value")
                break
        if score is None:
            state = str(data.get("state") or "UNKNOWN")
            if "COMPLETED" in state:
                score = 0.0
            else:
                unparsed_warnings.append(f"{f.name}: state={state}, no numericResult found in results")
                continue
        try:
            pct = float(score)
        except (TypeError, ValueError):
            unparsed_warnings.append(f"{f.name}: invalid numericResult value ({score!r})")
            continue
        cname = canonical_model_slug(name)
        if cname not in found or fin > found[cname][0]:
            found[cname] = (fin, pct, f)

    if unparsed_warnings:
        print(f"  [!] warning: {len(unparsed_warnings)} run(s) skipped in {task_dir.name}:")
        for w in unparsed_warnings[:5]:
            print(f"      - {w}")
        if len(unparsed_warnings) > 5:
            print(f"      ... and {len(unparsed_warnings) - 5} more")

    return found


def expand_import_tasks(arg: object) -> list[str]:
    """'all' -> 17 task slugs; 'text'/'vision'/'audio' -> modality slugs; else [slug]."""
    key = str(arg).lower()
    files = sorted(TASKS_DIR.glob("[0-9][0-9]_*.py")) + [TASKS_DIR / "vision.py", TASKS_DIR / "audio.py"]
    if key == "all":
        names = [task_name_for(f) for f in files if f.is_file()]
    elif key in ("text", "vision", "audio"):
        names = [
            task_name_for(f)
            for f in files
            if f.is_file() and task_modality(task_name_for(f)) == key
        ]
    else:
        return [resolve_task_name(arg)]
    if not names:
        raise SystemExit(f"no {key!r} task files in {TASKS_DIR} (run: synhalees kaggle gen)")
    return names


def task_is_cached(
    task_slug_name: str,
    requested_models: set[str] | None = None,
    *,
    check_remote: bool = True,
) -> tuple[bool, int, list[str]]:
    """Check if requested (or all known) models already exist in task's slim CSV."""
    slim_path = KG_RESULTS / f"{task_slug_name}.slim.csv"
    if not slim_path.is_file() or slim_path.stat().st_size <= 50:
        return False, 0, []
    rows = load_slim_rows(slim_path)
    existing_models = {k[0] for k in rows}
    if not existing_models:
        return False, 0, []

    if requested_models:
        missing = [m for m in sorted(requested_models) if m not in existing_models]
        return len(missing) == 0, len(existing_models), missing

    all_known_models: set[str] = set()
    for f in KG_RESULTS.glob("*.slim.csv"):
        for k in load_slim_rows(f):
            all_known_models.add(k[0])

    missing = {m for m in all_known_models if m not in existing_models}
    if check_remote:
        local_canons = {canonical_model_slug(m) for m in existing_models}
        for s in kaggle_task_models(task_slug_name):
            canon = canonical_model_slug(s)
            if canon and canon not in local_canons:
                missing.add(s)

    if missing:
        return False, len(existing_models), sorted(missing)
    return True, len(existing_models), []


def pull_single_to_slim(
    name: str,
    extra_args: Iterable[str] = (),
    slug_override: str | None = None,
) -> int:
    """Download one task into .temp/, merge into slim CSV, verify, and clean up."""
    temp_dir = KG_RESULTS / ".temp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    rewritten = rewrite_model_args(list(extra_args), name)
    rc = sh([kaggle_bin(), "b", "t", "download", name, "-o", str(temp_dir), *rewritten])
    if rc:
        return rc

    task_temp = temp_dir / name
    _note_normalized(name, normalize_pulled_runs(name, base_dir=temp_dir))

    found = collect_task_results(task_temp)
    if not found:
        print(f"  [i] no parseable results extracted for {name} (runs may have completed with no scores)")
        has_filter = any(str(t) in ("-m", "--model") for t in rewritten)
        if has_filter:
            print(
                f"  [hint] a -m filter was used but matched nothing: run "
                f"'synhalees kaggle status {name}' to see Kaggle's exact model names, then retry"
            )
        shutil.rmtree(task_temp, ignore_errors=True)
        try:
            if temp_dir.is_dir() and not any(temp_dir.iterdir()):
                temp_dir.rmdir()
        except OSError:
            pass
        return 0

    slim_path = KG_RESULTS / f"{name}.slim.csv"
    previous = load_slim_rows(slim_path)
    merged = dict(previous)
    modality = task_modality(name)
    pillar = "__overall__" if name in ("synhalees-vision", "synhalees-audio") else pillar_from_task(name)
    if pillar is None:
        raise SystemExit(f"{name} is not a supported text pillar or vision/audio task")

    new_count = 0
    for model_name in sorted(found):
        fin, pct, result_path = found[model_name]
        model = slug_override or model_name
        date = fin[:10]
        cost, tokens, latency = kaggle_run_usage(result_path)
        key = (model, pillar, modality)
        merged[key] = [
            model, "kaggle", date, pillar, modality,
            f"{round(pct * 100, 1):.1f}", cost, tokens, latency,
        ]
        new_count += 1
        status = "updated" if key in previous else "new"
        print(f"  {model:<30} {modality} {round(pct * 100, 1):>6} ({date}) [{status}]")

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(list(EXT_HEADER))
    for key in sorted(merged):
        w.writerow(merged[key])
    slim_path.parent.mkdir(parents=True, exist_ok=True)
    slim_path.write_text(buf.getvalue(), encoding="utf-8", newline="")

    verify = load_slim_rows(slim_path)
    if len(verify) < len(merged):
        print(f"  [!] VERIFY FAIL: wrote {len(merged)} rows but read back {len(verify)}")
        return 1
    kept = len(merged) - new_count
    print(
        f"  [OK] {slim_path.name}: {len(merged)} row(s) "
        f"({new_count} from pull, {kept} kept) [{slim_path.stat().st_size} bytes]"
    )

    shutil.rmtree(task_temp, ignore_errors=True)
    try:
        if temp_dir.is_dir() and not any(temp_dir.iterdir()):
            temp_dir.rmdir()
    except OSError:
        pass
    return 0


def _write_submission_row(row: list[str], *, dry_run: bool = False) -> tuple[Path, int]:
    """Write or update one (pillar, modality) row in submissions/<vendor>/<family>/<model>.csv."""
    model, prov, _date, pillar, modality = (
        canonical_model_slug(row[0]), row[1], row[2], row[3], row[4]
    )
    full = list(row) + [""] * max(0, len(EXT_HEADER) - len(row))
    full[0] = model
    path = resolve_submission_path(model, prov)
    keep: list[list[str]] = []
    if path.is_file():
        keep = [
            r for r in load_scorecard(path)[1:]
            if len(r) >= 6 and (r[3], r[4]) != (pillar, modality)
        ]
    if dry_run:
        return path, len(keep) + 1
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(EXT_HEADER)
        for old_row in keep:
            w.writerow(old_row + [""] * (len(EXT_HEADER) - len(old_row)))
        w.writerow(full[:len(EXT_HEADER)])
    return path, len(keep) + 1


def import_single_slim(
    src: Path,
    *,
    dry_run: bool = False,
    slug_override: str | None = None,
) -> int:
    """Import rows from a single slim CSV file into submissions/."""
    if not src.is_file():
        raise SystemExit(f"slim file not found: {src}")
    with open(src, newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.reader(fh))
    if not rows or rows[0][:len(HEADER)] != HEADER:
        raise SystemExit(f"{src}: expected header {','.join(HEADER)}")
    data_rows = [r for r in rows[1:] if r]
    if slug_override and len({canonical_model_slug(r[0]) for r in data_rows if r}) > 1:
        raise SystemExit(f"--slug needs exactly one model in {src.name}")

    imported = 0
    for row in data_rows:
        if len(row) < 6:
            raise SystemExit(f"{src}: bad row: {row}")
        row_out = list(row)
        if slug_override:
            row_out[0] = slug_override
        path, total_rows = _write_submission_row(row_out, dry_run=dry_run)
        verb = "would write" if dry_run else "wrote"
        print(f"  {verb} {display_path(path)} ({total_rows} rows)")
        imported += 1
    return imported


def gather_coverage(
    *,
    check_remote: bool = True,
    provider_filter: str | None = None,
) -> tuple[dict[str, dict[str, int]], dict[str, list[str]], list[str]]:
    """Collect local slim-CSV coverage and optional Kaggle server missing runs.

    Returns (model_tasks, missing_remote, all_task_slugs).
    """
    try:
        tasks = expand_import_tasks("all")
    except SystemExit:
        tasks = (
            sorted(p.name[:-len(".slim.csv")] for p in KG_RESULTS.glob("*.slim.csv"))
            if KG_RESULTS.is_dir()
            else []
        )
    filt = (provider_filter or "").strip().lower()
    model_tasks: dict[str, dict[str, int]] = {}
    for t in tasks:
        slim_path = KG_RESULTS / f"{t}.slim.csv"
        if not slim_path.is_file():
            continue
        for key in load_slim_rows(slim_path):
            m, _p, mod = key
            if filt and filt not in m.lower():
                continue
            bucket = model_tasks.setdefault(m, {"text": 0, "vision": 0, "audio": 0})
            if mod in bucket:
                bucket[mod] += 1

    missing: dict[str, list[str]] = {}
    if check_remote:
        try:
            with ThreadPoolExecutor(max_workers=8) as pool:
                server_by_task = dict(zip(tasks, pool.map(kaggle_task_models, tasks)))
        except Exception:
            server_by_task = {}
        for t in tasks:
            slim = KG_RESULTS / f"{t}.slim.csv"
            local = (
                {canonical_model_slug(k[0]) for k in load_slim_rows(slim)}
                if slim.is_file()
                else set()
            )
            for s in server_by_task.get(t) or []:
                canon = canonical_model_slug(s)
                if filt and filt not in canon.lower() and filt not in s.lower():
                    continue
                if canon and canon not in local:
                    missing.setdefault(s, []).append(t)

    return model_tasks, missing, tasks
