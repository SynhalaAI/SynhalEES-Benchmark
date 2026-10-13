"""Unified SynhalEES CLI -- run, compare, build, publish, check, Kaggle.

One entry point for the whole workflow (stdlib only):

    synhalees run --model ollama:llama3.1:8b --pillars 01_buddhist_culture
    synhalees compare | build [--check|--demo] | logos [--check] | check
    synhalees publish <model-slug>
    synhalees kaggle gen|push|run|status|logs|publish|pull|import|slim-export|slim-import

Install with `pip install -e .` (console script `synhalees`); also runs as
`python -m synhalees` from a checkout. Output hygiene: local runs land in
`runs/<model-slug>/`, Kaggle artifacts in `kaggle-results/` -- never the repo
root (AGENTS.md "Output Hygiene").
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / "runs"
from synhalees.registry import canonical_model_slug, default_registry

SUBMISSIONS = ROOT / "submissions"
KG_RESULTS = ROOT / "kaggle-results"
TASKS_DIR = ROOT / "kaggle" / "tasks"
HEADER = ["model", "provider", "date", "pillar", "modality", "score"]
# Kaggle imports add usage telemetry (optional; local runs may omit it).
EXT_HEADER = HEADER + ["cost_usd", "tokens", "latency_ms"]


def resolve_submission_path(model_id: str, provider: str = "") -> Path:
    """Resolve or infer hierarchical submission file path: submissions/<vendor>/<family>/<model>.csv."""
    return default_registry.resolve_submission_path(model_id, base_dir=SUBMISSIONS)


def sh(argv, cwd=ROOT):
    """Run a subprocess with inherited stdio; return its exit code."""
    print("+ " + " ".join(str(a) for a in argv), flush=True)
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    return subprocess.run([str(a) for a in argv], cwd=str(cwd), env=env).returncode


def py_run(script, args=()):
    script = Path(script)
    if not script.exists():
        raise SystemExit(f"missing {script} -- run from the repo checkout (pip install -e .)")
    return sh([sys.executable, str(script), *[str(a) for a in args]])


def kaggle_bin():
    found = shutil.which("kaggle") or shutil.which("kaggle.exe")
    if found:
        return found
    for c in (Path(sys.executable).parent / "Scripts" / "kaggle.exe",
              Path.home() / "AppData/Local/Programs/Python/Python313/Scripts/kaggle.exe"):
        if c.exists():
            return str(c)
    raise SystemExit("kaggle CLI not found on PATH (pip install kaggle, then 'kaggle b init')")


def task_slug(arg):
    """Kaggle normalizes task names: 'My_Task' / 'my task' -> 'my-task'."""
    return re.sub(r"[^a-z0-9]+", "-", str(arg).lower()).strip("-")


def resolve_task_file(arg):
    """'05_sinhala_grammar' | 'synhalees-05-sinhala-grammar' -> kaggle/tasks/<file>.py"""
    direct = Path(arg)
    if not direct.is_absolute():
        direct = ROOT / direct          # every returned path stays absolute
    if direct.suffix == ".py" and direct.is_file():
        return direct
    name = Path(arg).name.replace("-", "_")
    if name.startswith("synhalees_"):
        name = name[len("synhalees_"):]
    cand = TASKS_DIR / f"{name}.py"
    if cand.is_file():
        return cand
    raise SystemExit(f"no task file for {arg!r} (expected {TASKS_DIR}/*.py)")


def pillar_from_task(slug):
    """'synhalees-05-sinhala-grammar' -> '05_sinhala_grammar'; combined tasks -> None."""
    parts = [p for p in slug.split("-") if p]
    if len(parts) >= 3 and parts[0] == "synhalees" and parts[1].isdigit():
        return parts[1] + "_" + "_".join(parts[2:])
    return None


def task_name_for(path):
    """kaggle/tasks/05_sinhala_grammar.py -> "synhalees-05-sinhala-grammar".

    Kaggle hosts a task under the name passed to "kaggle b t push" (it
    normalizes "_" to "-"), so this is the single source of truth for every
    push / run / status / log / publish / download call. The name already in
    use on Kaggle (synhalees-05-sinhala-grammar) is produced by this rule.
    """
    stem = Path(path).stem
    if stem.startswith("synhalees_"):
        stem = stem[len("synhalees_"):]
    return task_slug(f"synhalees_{stem}")


def resolve_task_name(arg):
    """'audio' | 'audio.py' | 'synhalees-audio' -> 'synhalees-audio'."""
    text = str(arg)
    if text.lower().endswith(".py"):
        return task_name_for(text)
    slug = task_slug(text)
    return slug if slug.startswith("synhalees-") else task_slug(f"synhalees_{slug}")


MODALITIES = ("text", "vision", "audio", "all")


def task_modality(name):
    """'synhalees-vision' -> 'vision'; 'synhalees-audio' -> 'audio'; pillars -> 'text'."""
    if name in ("synhalees-vision", "synhalees-audio"):
        return name.split("-", 1)[1]
    return "text"


def parse_modality(value):
    """'text' | 'vision | audio' | 'vision,audio' -> tuple of modality tokens.

    'all' wins over anything else; duplicates collapse in order.
    """
    parts = []
    for chunk in str(value).replace("|", ",").split(","):
        parts += [w.lower() for w in str(chunk).split() if w]
    if not parts:
        raise SystemExit(f"--modality needs one of: {', '.join(MODALITIES)}")
    bad = [p for p in parts if p not in MODALITIES]
    if bad:
        raise SystemExit(f"--modality must be one or more of: {', '.join(MODALITIES)} "
                         f"(combine with | or ,): {', '.join(bad)}")
    if "all" in parts:
        return ("all",)
    return tuple(dict.fromkeys(parts))


def pop_modality(args):
    """Pull --modality [x] / --modality=x out of raw args -> ((tokens|None), rest).

    'pull' argv is often forwarded without argparse (see _KFWD), so the flag is
    extracted here and validated the same way for both entry paths.
    """
    modality = None
    rest = []
    i = 0
    while i < len(args):
        tok = str(args[i])
        if tok == "--modality":
            if i + 1 >= len(args):
                raise SystemExit(f"--modality needs one of: {', '.join(MODALITIES)}")
            modality = args[i + 1]
            i += 2
            continue
        if tok.startswith("--modality="):
            modality = tok.split("=", 1)[1]
            i += 1
            continue
        rest.append(tok)
        i += 1
    return (parse_modality(modality) if modality is not None else None), rest


def pop_force(args):
    """Pull --force out of raw args -> (bool, rest).

    Same pattern as pop_modality: the pull fast path bypasses argparse, so we
    extract the flag manually from the forwarded argv.
    """
    force = False
    rest = []
    for tok in args:
        if str(tok) == "--force":
            force = True
        else:
            rest.append(str(tok))
    return force, rest


def load_scorecard(path):
    with open(path, newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.reader(fh))
    if not rows or rows[0][:len(HEADER)] != HEADER:
        raise SystemExit(f"{path}: expected header {','.join(HEADER)}")
    return rows


def rebuild_and_check():
    return (py_run(ROOT / "tools" / "build_leaderboard.py")
            or py_run(ROOT / "tools" / "build_leaderboard.py", ["--check"]))


def cmd_run(a):
    return py_run(ROOT / "run_benchmark.py", a.args)


def cmd_compare(a):
    return py_run(ROOT / "tools" / "compare_runs.py", a.args)


def cmd_build(a):
    return py_run(ROOT / "tools" / "build_leaderboard.py", a.args)


def cmd_logos(a):
    return py_run(ROOT / "tools" / "build_logo_data.py", a.args)


def cmd_check(_a):
    steps = (
        ("logo-data.js fresh", [sys.executable, str(ROOT / "tools" / "build_logo_data.py"), "--check"]),
        ("docs/assets/data in sync", [sys.executable, str(ROOT / "tools" / "build_leaderboard.py"), "--check"]),
        ("python compiles", [sys.executable, "-m", "compileall", "-q", str(ROOT / "synhalees"),
                             str(ROOT / "tools"), str(ROOT / "kaggle"), str(ROOT / "run_benchmark.py")]),
    )
    failed = []
    for name, argv in steps:
        code = sh(argv)
        print(f"[{'OK' if code == 0 else 'FAIL'}] {name}")
        if code:
            failed.append(name)
    if failed:
        print("FAILED: " + ", ".join(failed))
        return 1
    print("all checks passed")
    return 0


def cmd_publish(a):
    src = RUNS / a.slug / "submission.csv"
    dst = resolve_submission_path(a.slug)
    if not src.is_file():
        raise SystemExit(f"missing {src} (run the benchmark first)")
    rows = load_scorecard(src)
    if len(rows) < 2:
        raise SystemExit(f"{src}: no data rows")
    if a.dry_run:
        print(f"would copy {src.relative_to(ROOT)} -> {dst.relative_to(ROOT)} ({len(rows) - 1} rows)")
        for r in rows[1:]:
            print("  " + ",".join(r))
        return 0
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)
    print(f"copied {src.relative_to(ROOT)} -> {dst.relative_to(ROOT)}")
    code = rebuild_and_check()
    if not code:
        print(f"published {a.slug}: commit {dst.relative_to(ROOT)} with docs/assets/data/*")
    return code


def cmd_kaggle_gen(a):
    args = []
    if getattr(a, "dry_run", False):
        args.append("--dry-run")
    if getattr(a, "out", None):
        args += ["--out", a.out]
    return py_run(ROOT / "kaggle" / "generate_tasks.py", args)


def cmd_kaggle_push(a):
    files = sorted(TASKS_DIR.glob("*.py")) if a.task in (None, "all") else [resolve_task_file(a.task)]
    if not files:
        raise SystemExit(f"no task files in {TASKS_DIR} (run: synhalees kaggle gen)")
    extra = list(a.args)
    if a.wait:
        extra.append("--wait")
    for i, f in enumerate(files, 1):
        name = task_name_for(f)
        print(f"[{i}/{len(files)}] {name}  <-  {f.relative_to(ROOT)}")
        rc = sh([kaggle_bin(), "b", "t", "push", name, "-f", str(f), *extra])
        if rc:
            print(f"stopped: push failed for {name} ({len(files) - i} task(s) left)")
            return rc
    print(f"pushed {len(files)} task file(s)")
    return 0


def cmd_kaggle_run(a):
    return sh([kaggle_bin(), "b", "t", "run", resolve_task_name(a.task), *a.args])


def cmd_kaggle_status(a):
    task = getattr(a, "task", None)
    if task in (None, "all", "coverage", "local"):
        # Local slim-benchmark coverage dashboard
        tasks = expand_import_tasks("all")
        model_tasks = {}
        for t in tasks:
            slim_path = KG_RESULTS / f"{t}.slim.csv"
            if slim_path.is_file():
                for key in load_slim_rows(slim_path):
                    m, p, mod = key
                    if m not in model_tasks:
                        model_tasks[m] = {"text": 0, "vision": 0, "audio": 0}
                    if mod in model_tasks[m]:
                        model_tasks[m][mod] += 1
        if not model_tasks:
            print("no benchmark results found in kaggle-results/*.slim.csv")
            return 0
        hdr = "%-30s %-16s %-10s %-10s" % ("Model", "Text (of 15)", "Vision", "Audio")
        print(f"Benchmark Coverage ({len(model_tasks)} model(s) in kaggle-results/):")
        print(hdr)
        print("-" * len(hdr))
        for m, stat in sorted(model_tasks.items()):
            v_str = "DONE" if stat["vision"] else "-"
            a_str = "DONE" if stat["audio"] else "-"
            print("%-30s %2d/15           %-10s %-10s" % (m, stat["text"], v_str, a_str))
        return 0
    return sh([kaggle_bin(), "b", "t", "status", resolve_task_name(task), *a.args])


def cmd_kaggle_logs(a):
    return sh([kaggle_bin(), "b", "t", "log", resolve_task_name(a.task), *a.args])


def cmd_kaggle_publish(a):
    return sh([kaggle_bin(), "b", "t", "publish", resolve_task_name(a.task), *a.args])


def _task_is_cached(task_slug):
    """True when task already has a slim CSV with data."""
    slim_path = KG_RESULTS / f"{task_slug}.slim.csv"
    return slim_path.is_file() and slim_path.stat().st_size > 50


def _pull_single_to_slim(name, extra_args=(), slug_override=None):
    """Download one task into .temp/, extract scores into slim CSV, clean up.

    Flow: kaggle download → .temp/<task>/ → normalize → collect results
          → merge into kaggle-results/<task>.slim.csv → verify → rm .temp/<task>/
    Returns 0 on success, 1 on failure.
    """
    temp_dir = KG_RESULTS / ".temp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    rc = sh([kaggle_bin(), "b", "t", "download", name, "-o", str(temp_dir), *extra_args])
    if rc:
        return rc

    # Normalize model folder names in the temp download
    task_temp = temp_dir / name
    _note_normalized(name, normalize_pulled_runs(name, base_dir=temp_dir))

    # Collect scores from the downloaded artifacts
    found = collect_task_results(task_temp)
    if not found:
        print(f"  [!] no parseable results in downloaded artifacts for {name}")
        # Clean up the temp folder even on failure
        shutil.rmtree(task_temp, ignore_errors=True)
        if not any(temp_dir.iterdir()):
            temp_dir.rmdir()
        return 1

    # Merge into the slim CSV
    slim_path = KG_RESULTS / f"{name}.slim.csv"
    previous = load_slim_rows(slim_path)
    merged = dict(previous)
    modality = "vision" if name == "synhalees-vision" else "audio" if name == "synhalees-audio" else "text"
    pillar = pillar_from_task(name)
    if name in ("synhalees-vision", "synhalees-audio"):
        pillar = "__overall__"
    new_count = 0
    for model_name in sorted(found):
        fin, pct, result_path = found[model_name]
        model = slug_override or model_name
        date = fin[:10]
        cost, tokens, latency = kaggle_run_usage(result_path)
        key = (model, pillar, modality)
        merged[key] = [model, "kaggle", date, pillar, modality,
                       f"{round(pct * 100, 1):.1f}", cost, tokens, latency]
        new_count += 1
        status = "updated" if key in previous else "new"
        print(f"  {model:<30} {modality} {round(pct * 100, 1):>6} ({date}) [{status}]")

    # Write the merged slim CSV
    import io as _io
    buf = _io.StringIO()
    w = csv.writer(buf)
    w.writerow(list(EXT_HEADER))
    for key in sorted(merged):
        w.writerow(merged[key])
    slim_path.parent.mkdir(parents=True, exist_ok=True)
    slim_path.write_text(buf.getvalue(), encoding="utf-8", newline="")

    # Verify: re-read and confirm row count
    verify = load_slim_rows(slim_path)
    if len(verify) < len(merged):
        print(f"  [!] VERIFY FAIL: wrote {len(merged)} rows but read back {len(verify)}")
        return 1
    kept = len(merged) - new_count
    print(f"  ✓ {slim_path.name}: {len(merged)} row(s) "
          f"({new_count} from pull, {kept} kept) [{slim_path.stat().st_size} bytes]")

    # Clean up temp download
    shutil.rmtree(task_temp, ignore_errors=True)
    # Remove .temp/ itself if empty
    try:
        if temp_dir.is_dir() and not any(temp_dir.iterdir()):
            temp_dir.rmdir()
    except OSError:
        pass
    return 0


def cmd_kaggle_pull(a):
    task = getattr(a, "task", None)
    raw = list(getattr(a, "args", None) or [])
    if task is not None and str(task).startswith("-"):
        raw.insert(0, str(task))      # fast path ate the flag: 'pull --modality vision'
        task = None
    modality, args = pop_modality(raw)
    force, args = pop_force(args)
    if not force:
        force = getattr(a, "force", False)
    if modality is None:
        cli_mod = getattr(a, "modality", None)
        modality = parse_modality(cli_mod) if cli_mod else ("text",)
    if "-o" in args or "--output" in args:
        raise SystemExit("drop -o: artifacts always go to kaggle-results/ (repo hygiene)")
    if task in (None, "all"):
        files = sorted(TASKS_DIR.glob("*.py"))
        if not files:
            raise SystemExit(f"no task files in {TASKS_DIR} (run: synhalees kaggle gen)")
        sel = {"text", "vision", "audio"} if "all" in modality else set(modality)
        picked = [f for f in files if task_modality(task_name_for(f)) in sel]
        if not picked:
            raise SystemExit(f"no '{'|'.join(modality)}' task files in {TASKS_DIR} "
                             f"(run: synhalees kaggle gen)")
        failed, skipped = [], 0
        for i, f in enumerate(picked, 1):
            name = task_name_for(f)
            if not force and _task_is_cached(name):
                slim_f = KG_RESULTS / f"{name}.slim.csv"
                n_models = len(load_slim_rows(slim_f))
                print(f"[{i}/{len(picked)}] skipping {name} "
                      f"(cached: {n_models} model(s) in {slim_f.name}; --force to re-download)")
                skipped += 1
                continue
            print(f"[{i}/{len(picked)}] pulling {name} -> slim CSV")
            rc = _pull_single_to_slim(name, extra_args=args)
            if rc:
                failed.append(name)
        pulled = len(picked) - skipped - len(failed)
        parts = []
        if pulled:
            parts.append(f"pulled {pulled}")
        if skipped:
            parts.append(f"skipped {skipped} (cached)")
        if failed:
            parts.append(f"failed {len(failed)}")
            print(f"pull failed for {len(failed)} task(s): {', '.join(failed)}")
        print(f"{', '.join(parts)} [{'|'.join(modality)}] task(s) -> {KG_RESULTS.relative_to(ROOT)}/*.slim.csv")
        if skipped and not force:
            print("hint: --force to re-download cached tasks")
        if sel == {"text"}:
            print("note: vision+audio skipped by default (~730MB more) - "
                  "add --modality 'vision|audio' (or vision / audio / all) to pull them")
        return 1 if failed else 0
    name = resolve_task_name(task)
    if not force and _task_is_cached(name):
        slim_f = KG_RESULTS / f"{name}.slim.csv"
        n_models = len(load_slim_rows(slim_f))
        print(f"skipping {name} (cached: {n_models} model(s) in {slim_f.name}; --force to re-download)")
        return 0
    print(f"pulling {name} -> slim CSV")
    return _pull_single_to_slim(name, extra_args=args)


def _note_normalized(task_slug, count):
    """One line after a pull, so a renamed folder never looks like a mystery."""
    if count:
        print(f"normalized {count} run folder(s) in {task_slug}/ to canonical slugs")


def normalize_pulled_runs(task_slug, base_dir=None):
    """Rename Kaggle's run folders under <base_dir>/<task>/ to canonical slugs.

    Kaggle names those folders with its own pin marker ('claude-opus-5-default'),
    so the next pull would otherwise drop a legacy folder next to the canonical
    one and duplicate every run. Run ids are unique, so canonical names that
    collide merge their run folders rather than clobbering them. Returns the
    number of folders renamed; safe to run on an already-normal tree.
    """
    base = base_dir or KG_RESULTS
    task_dir = base / task_slug
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


def kaggle_run_usage(result_path):
    """Cost/tokens/latency for one Kaggle run, from its sibling *.run.json.

    Returns (cost_usd, tokens, avg_latency_ms) or ("", "", "") when the
    artifact is missing. Costs arrive as nanodollars per token bucket.
    """
    runs = sorted(result_path.parent.glob("*.run.json"))
    if not runs:
        return "", "", ""
    try:
        data = json.loads(runs[0].read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "", "", ""

    conversations = []

    def walk(node):
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
    latencies = []
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


def collect_task_results(task_dir):
    """Collect newest scorecard result per model under task_dir.

    Handles two Kaggle artifact layouts:
      1. Legacy layout: *.result.json with agent_info + verifier_result.rewards.score
      2. Modern layout: *.run.json with modelVersion.slug + results[...].numericResult.value

    Returns dict mapping canonical model name -> (finished_at, score_pct_float, path_for_usage).
    Logs warnings if runs fail to parse, alerting users to Kaggle schema drift.
    """
    found = {}
    unparsed_warnings = []

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

    # Pass 2: *.run.json (modern fallback for runs without a sibling result.json)
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
            # Check if run completed or failed/errored
            state = data.get("state") or "UNKNOWN"
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


def expand_import_tasks(arg):
    """'all' -> all 17 task slugs; 'text'/'vision'/'audio' -> that modality's slugs;
    anything else -> [single resolved slug]. Raises SystemExit on bad input."""
    key = str(arg).lower()
    files = sorted(TASKS_DIR.glob("[0-9][0-9]_*.py")) + [TASKS_DIR / "vision.py", TASKS_DIR / "audio.py"]
    if key == "all":
        names = [task_name_for(f) for f in files if f.is_file()]
    elif key in ("text", "vision", "audio"):
        names = [task_name_for(f) for f in files
                 if f.is_file() and task_modality(task_name_for(f)) == key]
    else:
        return [resolve_task_name(arg)]
    if not names:
        raise SystemExit(f"no {key!r} task files in {TASKS_DIR} (run: synhalees kaggle gen)")
    return names


def cmd_kaggle_import(a):
    """Import one task (auto-pulls when missing), or 'all' tasks (local only).

    Checks slim CSVs first (modern path), then raw folders (legacy).
    'all' never downloads: tasks without local data are skipped.
    """
    key = str(a.task).lower()
    if key in ("all", "text", "vision", "audio"):
        if a.slug:
            raise SystemExit("--slug cannot be used with import all/text/vision/audio")
        names = expand_import_tasks(key)
        failed, skipped, done = [], [], 0
        for i, slug in enumerate(names, 1):
            slim_f = KG_RESULTS / f"{slug}.slim.csv"
            t_dir = KG_RESULTS / slug
            has_slim = slim_f.is_file() and slim_f.stat().st_size > 50
            has_raw = t_dir.is_dir() and (any(t_dir.rglob("*.result.json")) or any(t_dir.rglob("*.run.json")))
            if not has_slim and not has_raw:
                print(f"[{i}/{len(names)}] skipping {slug} (no local data; "
                      f"pull first: synhalees kaggle pull {slug})")
                skipped.append(slug)
                continue
            if has_slim and not has_raw:
                # Slim-only: use slim-import path directly
                print(f"[{i}/{len(names)}] importing {slug} (from slim CSV)")
                rc = import_single_slim(slim_f)
                if rc:
                    done += 1
                continue
            print(f"[{i}/{len(names)}] importing {slug}")
            rc = cmd_kaggle_import(argparse.Namespace(
                task=slug, slug=None, dry_run=a.dry_run, no_pull=True, build=False, _in_batch=True))
            if rc:
                failed.append(slug)
            else:
                done += 1
        if failed:
            print(f"import failed for {len(failed)} task(s): {', '.join(failed)}")
            return 1
        if not a.dry_run:
            print(f"imported {done} Kaggle task(s)" +
                  (f"; skipped {len(skipped)} with no local data: " +
                   ", ".join(skipped) if skipped else ""))
            if getattr(a, "build", False):
                return rebuild_and_check()
            print("hint: run 'synhalees build' to regenerate docs/assets/data/*")
        return 0
    slug = resolve_task_name(a.task)
    pillar = pillar_from_task(slug)
    if slug in ("synhalees-vision", "synhalees-audio"):
        pillar = "__overall__"
    elif pillar is None:
        raise SystemExit(f"{slug} is not an importable text pillar or combined vision/audio task")
    task_dir = KG_RESULTS / slug

    newest = collect_task_results(task_dir)
    if not newest and not a.no_pull:
        print(f"no local artifacts under {task_dir}; pulling from Kaggle...")
        rc = sh([kaggle_bin(), "b", "t", "download", slug, "-o", str(KG_RESULTS)])
        if rc:
            return rc
        _note_normalized(slug, normalize_pulled_runs(slug))
        newest = collect_task_results(task_dir)
    if not newest:
        raise SystemExit(f"no artifacts (*.result.json / *.run.json) under {task_dir} (synhalees kaggle pull {slug})")
    if a.slug and len(newest) > 1:
        raise SystemExit(f"--slug needs exactly one model; found {', '.join(sorted(newest))}")
    planned = []
    for name in sorted(newest):
        fin, pct, result_path = newest[name]
        model = a.slug or name
        date = fin[:10]
        modality = "vision" if slug == "synhalees-vision" else "audio" if slug == "synhalees-audio" else "text"
        cost, tokens, latency = kaggle_run_usage(result_path)
        row = [model, "kaggle", date, pillar, modality, f"{round(pct * 100, 1):.1f}",
               cost, tokens, latency]
        planned.append(row)
        usage = f"  ${cost}  {tokens} tok  {latency} ms" if tokens else ""
        print(f"{model:<30} {modality} overall {row[5]:>6} ({date}){usage}")
    if a.dry_run:
        print("dry run: nothing written")
        return 0
    for row in planned:
        path = resolve_submission_path(row[0], row[1])
        keep = []
        if path.is_file():
            keep = [r for r in load_scorecard(path)[1:]
                    if len(r) >= 6 and (r[3], r[4]) != (row[3], row[4])]
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(EXT_HEADER)
            for old_row in keep:
                w.writerow(old_row + [""] * (len(EXT_HEADER) - len(old_row)))
            w.writerow(row)
        print(f"wrote {path.relative_to(ROOT)} ({len(keep) + 1} rows)")
    if getattr(a, "_in_batch", False):
        return 0
    if getattr(a, "build", False):
        code = rebuild_and_check()
        if not code:
            print("import done: commit submissions/*.csv with docs/assets/data/*")
        return code
    print("hint: run 'synhalees build' to regenerate docs/assets/data/*")
    return 0


def cmd_kaggle_slim_export(a):
    """Export ONLY the newest result per model to a tiny CSV (Colab-friendly)."""
    import io as _io
    slug = resolve_task_name(a.task)
    pillar = pillar_from_task(slug)
    if slug in ("synhalees-vision", "synhalees-audio"):
        pillar = "__overall__"
    elif pillar is None:
        raise SystemExit(f"{slug} is not an importable text pillar or combined vision/audio task")
    task_dir = KG_RESULTS / slug
    found = collect_task_results(task_dir)
    if not found:
        raise SystemExit(f"no artifacts (*.result.json / *.run.json) under {task_dir} (synhalees kaggle pull {slug})")
    out = Path(a.output) if getattr(a, "output", None) else (KG_RESULTS / (slug + ".slim.csv"))
    previous = {} if getattr(a, "replace", False) else load_slim_rows(out)
    merged = dict(previous)
    fresh = set()
    modality = "vision" if slug == "synhalees-vision" else "audio" if slug == "synhalees-audio" else "text"
    for name in sorted(found):
        fin, pct, result_path = found[name]
        model = a.slug or name
        date = fin[:10]
        cost, tokens, latency = kaggle_run_usage(result_path)
        merged[(model, pillar, modality)] = [model, "kaggle", date, pillar, modality,
                                              f"{round(pct * 100, 1):.1f}", cost, tokens, latency]
        fresh.add((model, pillar, modality))
        print(f"{model:<30} {modality} overall {round(pct * 100, 1):>6} ({date})")
    buf = _io.StringIO()
    w = csv.writer(buf)
    w.writerow(list(EXT_HEADER))
    for key in sorted(merged):
        w.writerow(merged[key])
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(buf.getvalue(), encoding="utf-8", newline="")
    print(f"wrote {out} ({out.stat().st_size} bytes) -- {len(merged)} row(s):"
          f" {len(fresh)} from this run, {len(merged) - len(fresh)} kept from earlier exports")
    return 0


def load_slim_rows(path):
    """Existing slim-CSV rows keyed by (model, pillar, modality); {} when absent.

    slim-export merges into this so a filtered pull (PROVIDER = 'gemini') never
    drops the rows an earlier full pull wrote for the other providers.
    """
    if not Path(path).is_file():
        return {}
    with open(path, newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.reader(fh))
    if not rows or rows[0][:len(EXT_HEADER)] != EXT_HEADER:
        return {}
    keyed = {}
    for row in rows[1:]:
        if len(row) < len(HEADER):
            continue
        full = list(row)[:len(EXT_HEADER)] + [""] * (len(EXT_HEADER) - len(row))
        keyed[(full[0], full[3], full[4])] = full
    return keyed


def import_single_slim(src: Path) -> int:
    """Import rows from a single slim CSV file into submissions/."""
    if not src.is_file():
        raise SystemExit(f"slim file not found: {src}")
    with open(src, newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.reader(fh))
    if not rows or rows[0][:len(HEADER)] != HEADER:
        raise SystemExit(f"{src}: expected header {chr(44).join(HEADER)}")
    imported = 0
    for row in rows[1:]:
        if len(row) < 6:
            raise SystemExit(f"{src}: bad row: {row}")
        model, prov, _date, pillar, modality = row[0], row[1], row[2], row[3], row[4]
        full = list(row) + [""] * (len(EXT_HEADER) - len(row))
        path = resolve_submission_path(model, prov)
        keep = []
        if path.is_file():
            keep = [r for r in load_scorecard(path)[1:]
                    if len(r) >= 6 and (r[3], r[4]) != (pillar, modality)]
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(EXT_HEADER)
            for old_row in keep:
                w.writerow(old_row + [""] * (len(EXT_HEADER) - len(old_row)))
            w.writerow(full[:len(EXT_HEADER)])
        print(f"wrote {path.relative_to(ROOT)} ({len(keep) + 1} rows)")
        imported += 1
    return imported


def cmd_kaggle_slim_import(a):
    """Import a slim CSV (exported on Colab) or all slim files into submissions/*.csv + rebuild."""
    target = getattr(a, "file", "all")
    if target == "all":
        slim_files = sorted(KG_RESULTS.glob("*.slim.csv"))
        if not slim_files:
            raise SystemExit(f"no *.slim.csv files found in {KG_RESULTS.relative_to(ROOT)}")
        for sf in slim_files:
            print(f"\n--- Importing {sf.name} ---")
            import_single_slim(sf)
    else:
        src = Path(target)
        if not src.is_file() and not src.is_absolute():
            candidate = KG_RESULTS / target
            if candidate.is_file():
                src = candidate
            elif (KG_RESULTS / f"{target}.slim.csv").is_file():
                src = KG_RESULTS / f"{target}.slim.csv"
        import_single_slim(src)

    if getattr(a, "build", False):
        code = rebuild_and_check()
        if not code:
            print("slim-import done: commit submissions/*.csv with docs/assets/data/*")
        return code
    print("\nhint: run 'synhalees build' to regenerate docs/assets/data/*")
    return 0

def build_parser():
    top = argparse.ArgumentParser(
        prog="synhalees",
        description="Unified SynhalEES CLI: run / compare / build / publish / check / kaggle",
        epilog="Extra arguments after the subcommand are forwarded to the underlying script.",
    )
    sub = top.add_subparsers(dest="cmd")

    def fwd(name, func, text):
        p = sub.add_parser(name, help=text, description=text)
        p.add_argument("args", nargs=argparse.REMAINDER, help="forwarded to the underlying script")
        p.set_defaults(func=func)

    fwd("run", cmd_run, "run the benchmark locally (-> run_benchmark.py)")
    fwd("compare", cmd_compare, "per-model summary + pillar x matrix (-> tools/compare_runs.py)")
    fwd("build", cmd_build, "regenerate docs/assets/data/* from submissions/ (add --check / --demo)")
    fwd("logos", cmd_logos, "regenerate docs/assets/logo-data.js (add --check)")
    p = sub.add_parser("publish", help="runs/<slug>/submission.csv -> submissions/, rebuild, verify")
    p.add_argument("slug", help="model slug (= runs/ folder name)")
    p.add_argument("--dry-run", action="store_true", help="show what would be copied")
    p.set_defaults(func=cmd_publish)
    p = sub.add_parser("check", help="run every local gate (logos, leaderboard, compile)")
    p.set_defaults(func=cmd_check)

    k = sub.add_parser("kaggle", help="Kaggle Benchmarks: gen push run status logs publish pull import")
    ks = k.add_subparsers(dest="kcmd")
    p = ks.add_parser("gen", help="regenerate the 17 task files under kaggle/tasks/")
    p.add_argument("--dry-run", action="store_true",
                   help="print what would be written, write nothing")
    p.add_argument("--out", default=None, help="output directory (default: kaggle/tasks/)")
    p.set_defaults(func=cmd_kaggle_gen)
    p = ks.add_parser("push", help="upload task file(s) (default: all 17)")
    p.add_argument("task", nargs="?", default="all", help="pillar/task slug, .py path, or all")
    p.add_argument("--wait", action="store_true", help="wait for the server-side build to finish")
    p.add_argument("args", nargs=argparse.REMAINDER, help="extra flags for kaggle b t push")
    p.set_defaults(func=cmd_kaggle_push)
    p = ks.add_parser("run", help="start a run: synhalees kaggle run <task> -m <model> [...]")
    p.add_argument("task")
    p.add_argument("args", nargs=argparse.REMAINDER, help="extra flags for 'kaggle b t run'")
    p.set_defaults(func=cmd_kaggle_run)
    p = ks.add_parser("status", help="benchmark coverage (default: all) or server-side task status")
    p.add_argument("task", nargs="?", default="all", help="task slug or 'all' for coverage dashboard (default: all)")
    p.add_argument("args", nargs=argparse.REMAINDER, help="extra flags, e.g. -m <model>")
    p.set_defaults(func=cmd_kaggle_status)
    p = ks.add_parser("logs", help="fetch run logs: synhalees kaggle logs <task> -m <model>")
    p.add_argument("task")
    p.add_argument("args", nargs=argparse.REMAINDER, help="extra flags for 'kaggle b t log'")
    p.set_defaults(func=cmd_kaggle_logs)
    p = ks.add_parser("publish", help="publish the Kaggle task leaderboard")
    p.add_argument("task")
    p.add_argument("args", nargs=argparse.REMAINDER, help="extra flags for kaggle b t publish")
    p.set_defaults(func=cmd_kaggle_publish)
    p = ks.add_parser("slim-export", help="newest result per model -> tiny CSV (Colab: download only this)")
    p.add_argument("task", help="task slug (e.g. synhalees-05-sinhala-grammar)")
    p.add_argument("--slug", default=None, help="force the model slug (single model only)")
    p.add_argument("-o", "--output", default=None, help="output CSV path (default: kaggle-results/<task>.slim.csv)")
    p.add_argument("--replace", action="store_true",
                   help="rebuild from scratch instead of merging into an existing slim CSV")
    p.set_defaults(func=cmd_kaggle_slim_export)
    p = ks.add_parser("slim-import", help="slim CSV -> submissions/*.csv (home PC, no download)")
    p.add_argument("file", nargs="?", default="all", help="slim CSV path or 'all' (default: all *.slim.csv in kaggle-results/)")
    p.add_argument("--build", action="store_true", help="auto-rebuild leaderboard data after importing (default: off; run 'synhalees build' when ready)")
    p.set_defaults(func=cmd_kaggle_slim_import)
    p = ks.add_parser("pull", help="download Kaggle artifacts -> .temp/ -> merge into slim CSVs (auto-cleanup)")
    p.add_argument("task", nargs="?", default="all", help="task slug or 'all' (default: all)")
    p.add_argument("--modality", default=None,
                   help="modality filter for 'all': text (default, 15 tasks ~145MB) | "
                        "vision | audio | all; combine with | or , "
                        "(e.g. 'vision|audio'); an explicit task slug always wins")
    p.add_argument("--force", action="store_true", default=False,
                   help="re-download even if artifacts are already cached locally")
    p.add_argument("args", nargs=argparse.REMAINDER, help="extra flags, e.g. -m <model>")
    p.set_defaults(func=cmd_kaggle_pull)
    p = ks.add_parser("import", help="import tasks -> submissions/*.csv (single pulls when missing; all/text/vision/audio use local artifacts only, skip the rest)")
    p.add_argument("task", nargs="?", default="all", help="task slug, or all/text/vision/audio (default: all)")
    p.add_argument("--slug", default=None, help="force the model slug (single model only)")
    p.add_argument("--dry-run", action="store_true", help="print the rows, write nothing")
    p.add_argument("--no-pull", action="store_true", help="use local artifacts only")
    p.add_argument("--build", action="store_true", help="auto-rebuild leaderboard data after importing (default: off; run 'synhalees build' when ready)")
    p.set_defaults(func=cmd_kaggle_import)
    return top


# Subcommands whose flags belong to the underlying tool: their argv is
# forwarded raw (argparse REMAINDER cannot swallow tokens that start with '-').
_FWD = {"run": cmd_run, "compare": cmd_compare, "build": cmd_build, "logos": cmd_logos}
_KFWD = {"run": cmd_kaggle_run, "logs": cmd_kaggle_logs, "pull": cmd_kaggle_pull}


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    argv = list(sys.argv[1:] if argv is None else argv)
    ns = argparse.Namespace
    if argv and argv[0] in _FWD:
        return (_FWD[argv[0]](ns(args=argv[1:])) or 0)
    if len(argv) > 2 and argv[0] == "kaggle" and argv[1] in _KFWD and argv[2] not in ("-h", "--help"):
        return (_KFWD[argv[1]](ns(task=argv[2], args=argv[3:])) or 0)
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.cmd == "kaggle" and not getattr(args, "func", None):
        parser.parse_args(["kaggle", "--help"])   # prints help, exits 0
    if not getattr(args, "func", None):
        parser.print_help()
        return 2
    return args.func(args) or 0


if __name__ == "__main__":
    raise SystemExit(main())
