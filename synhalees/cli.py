"""Unified SynhalEES CLI -- run, compare, build, publish, check, Kaggle.

One entry point for the whole workflow (stdlib only):

    synhalees run --model ollama:llama3.1:8b --pillars 01_buddhist_culture
    synhalees compare | build [--check|--demo] | logos [--check] | check
    synhalees publish <model-slug>
    synhalees kaggle gen|push|run|status|logs|publish|pull|import

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
SUBMISSIONS = ROOT / "submissions"
KG_RESULTS = ROOT / "kaggle-results"
TASKS_DIR = ROOT / "kaggle" / "tasks"
HEADER = ["model", "provider", "date", "pillar", "modality", "score"]


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
    if direct.suffix == ".py" and direct.is_file():
        return direct
    name = direct.name.replace("-", "_")
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


def load_scorecard(path):
    with open(path, newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.reader(fh))
    if not rows or rows[0] != HEADER:
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
    dst = SUBMISSIONS / f"{a.slug}.csv"
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
    SUBMISSIONS.mkdir(exist_ok=True)
    shutil.copyfile(src, dst)
    print(f"copied {src.relative_to(ROOT)} -> {dst.relative_to(ROOT)}")
    code = rebuild_and_check()
    if not code:
        print(f"published {a.slug}: commit submissions/{a.slug}.csv with docs/assets/data/*")
    return code


def cmd_kaggle_gen(_a):
    return py_run(ROOT / "kaggle" / "generate_tasks.py")


def cmd_kaggle_push(a):
    files = sorted(TASKS_DIR.glob("*.py")) if a.task in (None, "all") else [resolve_task_file(a.task)]
    if not files:
        raise SystemExit(f"no task files in {TASKS_DIR} (run: synhalees kaggle gen)")
    rc = 0
    for f in files:
        rc = sh([kaggle_bin(), "b", "t", "push", str(f)]) or rc
        if rc:
            break
    print(f"pushed {files.index(Path(f)) + 1 if not rc else 'stopped after failure in'} file(s)")
    return rc


def cmd_kaggle_run(a):
    return sh([kaggle_bin(), "b", "t", "run", a.task, *a.args])


def cmd_kaggle_status(a):
    return sh([kaggle_bin(), "b", "t", "status", a.task])


def cmd_kaggle_logs(a):
    return sh([kaggle_bin(), "b", "t", "log", a.task, *a.args])


def cmd_kaggle_publish(a):
    return sh([kaggle_bin(), "b", "t", "publish", a.task])


def cmd_kaggle_pull(a):
    if "-o" in a.args or "--output" in a.args:
        raise SystemExit("drop -o: artifacts always go to kaggle-results/ (repo hygiene)")
    return sh([kaggle_bin(), "b", "t", "download", task_slug(a.task), "-o", str(KG_RESULTS), *a.args])


def cmd_kaggle_import(a):
    slug = task_slug(a.task)
    pillar = pillar_from_task(slug)
    if pillar is None:
        raise SystemExit(f"{slug} is not a single-pillar task; its one overall score cannot be "
                         "split per pillar (import supports the 15 'synhalees-NN-...' text tasks)")
    task_dir = KG_RESULTS / slug

    def collect():
        found = {}
        for f in sorted(task_dir.rglob("*.result.json")):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            info = data.get("agent_info") or {}
            name = (info.get("model_info") or {}).get("name") or f.parent.parent.name
            fin = data.get("finished_at") or ""
            if name not in found or fin > found[name][0]:
                found[name] = (fin, data)
        return found

    newest = collect()
    if not newest and not a.no_pull:
        print(f"no local artifacts under {task_dir}; pulling from Kaggle...")
        rc = sh([kaggle_bin(), "b", "t", "download", slug, "-o", str(KG_RESULTS)])
        if rc:
            return rc
        newest = collect()
    if not newest:
        raise SystemExit(f"no *.result.json under {task_dir} (synhalees kaggle pull {slug})")
    if a.slug and len(newest) > 1:
        raise SystemExit(f"--slug needs exactly one model; found {', '.join(sorted(newest))}")
    planned = []
    for name in sorted(newest):
        fin, data = newest[name]
        score = ((data.get("verifier_result") or {}).get("rewards") or {}).get("score")
        try:
            pct = float(score)
        except (TypeError, ValueError):
            raise SystemExit(f"{name}: result.json has no numeric rewards.score")
        model = a.slug or name.split("/")[-1]
        date = (fin or data.get("started_at") or "")[:10]
        row = [model, "kaggle", date, pillar, "text", f"{round(pct * 100, 1):.1f}"]
        planned.append(row)
        print(f"{model:<30} {pillar} text {row[5]:>6} ({date})")
    if a.dry_run:
        print("dry run: nothing written")
        return 0
    for row in planned:
        path = SUBMISSIONS / f"{row[0]}.csv"
        keep = []
        if path.is_file():
            keep = [r for r in load_scorecard(path)[1:]
                    if len(r) == 6 and (r[3], r[4]) != (row[3], row[4])]
        SUBMISSIONS.mkdir(exist_ok=True)
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(HEADER)
            w.writerows(keep + [row])
        print(f"wrote {path.relative_to(ROOT)} ({len(keep) + 1} rows)")
    code = rebuild_and_check()
    if not code:
        print("import done: commit submissions/*.csv with docs/assets/data/*")
    return code


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
    p.set_defaults(func=cmd_kaggle_gen)
    p = ks.add_parser("push", help="upload task file(s) (default: all 17)")
    p.add_argument("task", nargs="?", default="all", help="pillar/task slug, .py path, or all")
    p.set_defaults(func=cmd_kaggle_push)
    p = ks.add_parser("run", help="start a run: synhalees kaggle run <task> -m <model> [...]")
    p.add_argument("task")
    p.add_argument("args", nargs=argparse.REMAINDER, help="extra flags for 'kaggle b t run'")
    p.set_defaults(func=cmd_kaggle_run)
    p = ks.add_parser("status", help="server-side run status")
    p.add_argument("task")
    p.set_defaults(func=cmd_kaggle_status)
    p = ks.add_parser("logs", help="fetch run logs: synhalees kaggle logs <task> -m <model>")
    p.add_argument("task")
    p.add_argument("args", nargs=argparse.REMAINDER, help="extra flags for 'kaggle b t log'")
    p.set_defaults(func=cmd_kaggle_logs)
    p = ks.add_parser("publish", help="publish the Kaggle task leaderboard")
    p.add_argument("task")
    p.set_defaults(func=cmd_kaggle_publish)
    p = ks.add_parser("pull", help="download artifacts -> kaggle-results/ (the -o is forced)")
    p.add_argument("task")
    p.add_argument("args", nargs=argparse.REMAINDER, help="extra flags, e.g. -m <model>, -f")
    p.set_defaults(func=cmd_kaggle_pull)
    p = ks.add_parser("import", help="single-pillar results -> submissions/<slug>.csv (+rebuild)")
    p.add_argument("task", help="e.g. synhalees-05-sinhala-grammar")
    p.add_argument("--slug", default=None, help="force the model slug (single model only)")
    p.add_argument("--dry-run", action="store_true", help="print the rows, write nothing")
    p.add_argument("--no-pull", action="store_true", help="use local artifacts only")
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
