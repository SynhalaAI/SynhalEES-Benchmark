#!/usr/bin/env python3
"""Compare every benchmark run under ``runs/`` and build the leaderboard CSV.

Each run lives in its own folder::

    runs/gemini-gemini-2.5-flash/
        checkpoint.jsonl   # raw per-item records (prompt, response, score)
        meta.json          # model, provider, date, error count
        submission.csv     # scorecard in the leaderboard schema

This tool:

  1. prints a per-model summary (overall, items, errors, coverage) and a
     pillar x model accuracy matrix so results are easy to compare;
  2. writes ``runs/all_submissions.csv`` -- every model's rows concatenated
     in the ``model,provider,date,pillar,modality,score`` schema that
     ``tools/build_leaderboard.py`` consumes.

Usage::

    python tools/compare_runs.py
    python tools/compare_runs.py --runs-dir runs --out runs/all_submissions.csv

Stdlib only, UTF-8 throughout.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
from build_leaderboard import pretty_model, pretty_provider  # noqa: E402

HEADER = ["model", "provider", "date", "pillar", "modality", "score"]


def load_run(run_dir: Path) -> dict | None:
    """Read one run folder -> {model, provider, date, rows: [dict]} or None."""
    checkpoint = run_dir / "checkpoint.jsonl"
    if not checkpoint.is_file():
        return None
    records: dict[str, dict] = {}
    with checkpoint.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue  # tolerate a truncated final line from a crash
            records[record["key"]] = record  # last write wins on resume
    if not records:
        return None
    meta: dict = {}
    meta_path = run_dir / "meta.json"
    if meta_path.is_file():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except ValueError:
            meta = {}
    ordered = [records[k] for k in sorted(records)]
    provider = meta.get("provider") or (
        (meta.get("spec") or "").partition(":")[0] or "unknown"
    )
    return {
        "dir": run_dir.name,
        "model": meta.get("model") or run_dir.name,
        "provider": provider,
        "date": meta.get("date") or str(date.today()),
        "records": ordered,
    }


def error_count(run: dict) -> int:
    return sum(1 for r in run["records"] if r.get("error"))


def is_broken(run: dict) -> bool:
    """True when every single item failed with an API error (bad key, no SSL,
    wrong model id...). Such a run carries no signal and is excluded by
    default instead of publishing a misleading 0% score."""
    return bool(run["records"]) and error_count(run) == len(run["records"])


def accuracy(rows: list[dict]) -> float:
    return sum(1 for r in rows if r.get("is_correct")) / len(rows) if rows else 0.0


def submission_rows(run: dict) -> list[list[str]]:
    """One row per (pillar, modality) cell, score as 0-100."""
    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for record in run["records"]:
        grouped[(record["pillar"], record.get("modality", "text"))].append(record)
    rows = []
    for (pillar, modality), items in sorted(grouped.items()):
        rows.append([run["model"], run["provider"], run["date"], pillar,
                     modality, f"{accuracy(items) * 100:.1f}"])
    return rows


def print_report(runs: list[dict]) -> None:
    total_pillars = len({r["pillar"] for run in runs for r in run["records"]})
    print(f"SynhalEES run comparison -- {len(runs)} model(s), "
          f"{total_pillars} pillar(s) covered\n")
    print(f"{'Model':<38}{'Provider':<12}{'Items':>6}{'Errors':>8}{'Overall':>10}")
    print("-" * 74)
    summary = []
    for run in runs:
        errs = sum(1 for r in run["records"] if r.get("error"))
        acc = accuracy(run["records"])
        summary.append((acc, run))
        print(f"{pretty_model(run['model'])[:37]:<38}"
              f"{pretty_provider(run['provider'], run['model'])[:11]:<12}"
              f"{len(run['records']):>6}{errs:>8}{acc:>9.1%}")
    summary.sort(reverse=True, key=lambda pair: pair[0])

    pillars = sorted({r["pillar"] for run in runs for r in run["records"]})
    if len(runs) < 2 or not pillars:
        return
    print("\nAccuracy by pillar (rows = model, columns = pillar)\n")
    short = {p: p.split("_", 1)[0] for p in pillars}
    print(f"{'Model':<30}" + "".join(f"{short[p]:>6}" for p in pillars))
    print("-" * (30 + 6 * len(pillars)))
    for run in runs:
        by_pillar: dict[str, list[dict]] = defaultdict(list)
        for record in run["records"]:
            by_pillar[record["pillar"]].append(record)
        cells = ""
        for pillar in pillars:
            rows = by_pillar.get(pillar)
            cells += f"{accuracy(rows):>6.0%}" if rows else f"{'-':>6}"
        print(f"{pretty_model(run['model'])[:29]:<30}{cells}")


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--runs-dir", type=Path, default=ROOT / "runs",
                    help="Root of per-model run folders (default: runs/).")
    ap.add_argument("--include-broken", action="store_true",
                    help="also publish runs where every item errored")
    ap.add_argument("--out", type=Path, default=None,
                    help="Combined local CSV for inspection "
                         "(default: <runs-dir>/all_submissions.csv; "
                         "publish via submissions/, not this file).")
    args = ap.parse_args()

    if not args.runs_dir.is_dir():
        print(f"ERROR: {args.runs_dir} does not exist. Run the benchmark first.",
              file=sys.stderr)
        return 1
    runs = []
    broken = []
    for child in sorted(args.runs_dir.iterdir()):
        if child.is_dir():
            run = load_run(child)
            if run:
                (broken if is_broken(run) and not args.include_broken else runs).append(run)
    for run in broken:
        print(f"WARNING: skipping {run['dir']} -- all {len(run['records'])} items "
              f"failed with API errors (bad key/SSL/model id?). Fix and rerun, or "
              f"pass --include-broken to publish it anyway.")
    if not runs:
        print(f"ERROR: no runs with a checkpoint.jsonl found in {args.runs_dir}.",
              file=sys.stderr)
        return 1

    print_report(runs)
    out = args.out or args.runs_dir / "all_submissions.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(HEADER)
        for run in runs:
            writer.writerows(submission_rows(run))
    print(f"\nwrote {out} ({len(runs)} model(s))")
    print("next: copy the scorecards you want to publish to")
    print("      submissions/<model-slug>.csv     # one file per model (see submissions/README.md)")
    print("      python tools/build_leaderboard.py && python tools/build_leaderboard.py --check")
    return 0


if __name__ == "__main__":
    sys.exit(main())
