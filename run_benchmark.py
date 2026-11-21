"""SynhalEES CLI entry point (direct API edition).

Runs the benchmark against a chat-LLM API with crash-safe checkpoints.
Every completed item is appended to a JSONL checkpoint file; if the run is
interrupted (Ctrl+C, quota, network), rerun the same command and it resumes
where it stopped. ``--fresh`` discards the checkpoint and starts over.

Examples:
    python run_benchmark.py --model ollama:llama3.1:8b --pillars 01_buddhist_culture
    python run_benchmark.py --model openrouter:openai/gpt-4o-mini --modality text
    python run_benchmark.py --model gemini:gemini-2.5-flash --judge gemini:gemini-2.5-pro

Keys come from env vars: OPENAI_API_KEY / OPENROUTER_API_KEY / GEMINI_API_KEY /
ANTHROPIC_API_KEY (Ollama needs none).
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

_VALID_MODALITIES = ("text", "vision", "audio", "all")


def add_run_arguments(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    """Attach all local-benchmark runner flags to *parser*."""
    parser.add_argument(
        "--model",
        required=True,
        help='Model spec "provider:name", e.g. "ollama:llama3.1:8b", '
        '"openrouter:openai/gpt-4o-mini", "gemini:gemini-2.5-flash".',
    )
    parser.add_argument(
        "--judge",
        default=None,
        help="Judge model spec for llm_judge rows (default: same as --model).",
    )
    parser.add_argument(
        "--modality",
        choices=_VALID_MODALITIES,
        default="text",
        help="Modalities to evaluate (default: text).",
    )
    parser.add_argument(
        "--pillars",
        default=None,
        help="Comma-separated pillar slugs (default: all 15 pillars).",
    )
    parser.add_argument(
        "--checkpoint",
        default=None,
        help="JSONL checkpoint path (default: runs/<model-slug>/checkpoint.jsonl).",
    )
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="Discard the checkpoint and start the run from scratch.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Scorecard CSV path (default: runs/<model-slug>/submission.csv).",
    )
    parser.add_argument(
        "--runs-dir",
        default="runs",
        help="Root directory for per-model run folders (default: runs).",
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help="OpenAI-compatible base URL for a self-hosted endpoint (Colab + "
        'vLLM, llama.cpp, LM Studio), e.g. "https://xxxx.trycloudflare.com/v1". '
        "Overrides the provider default; env fallback SYNHALEES_BASE_URL.",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="API key for --base-url (default: SYNHALEES_API_KEY, else a "
        "placeholder, since self-hosted servers usually ignore it).",
    )
    return parser


def resolve_endpoint(args: argparse.Namespace) -> tuple[str | None, str | None]:
    """Resolve ``(base_url, api_key)`` from flags with env fallbacks."""
    base_url = (
        getattr(args, "base_url", None) or os.environ.get("SYNHALEES_BASE_URL") or ""
    ).strip()
    api_key = (
        getattr(args, "api_key", None) or os.environ.get("SYNHALEES_API_KEY") or ""
    ).strip()
    return (base_url or None), (api_key or None)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="run_benchmark.py",
        description="Run the SynhalEES multimodal Sinhala benchmark via LLM APIs.",
    )
    add_run_arguments(parser)
    return parser.parse_args(argv)


def run_from_args(args: argparse.Namespace) -> int:
    """Execute a local benchmark run from parsed CLI arguments."""
    from synhalees.runner import SynhalEESBenchmark, model_slug

    modalities = (
        ("text", "vision", "audio") if args.modality == "all" else (args.modality,)
    )
    # One directory per model: results of different models never overwrite.
    run_dir = Path(args.runs_dir) / model_slug(args.model)
    checkpoint = args.checkpoint or run_dir / "checkpoint.jsonl"
    output = args.output or run_dir / "submission.csv"
    pillars = [p.strip() for p in args.pillars.split(",")] if args.pillars else None
    base_url, api_key = resolve_endpoint(args)
    benchmark = SynhalEESBenchmark(
        args.model,
        judge_model=args.judge,
        modalities=modalities,
        pillars=pillars,
        checkpoint=checkpoint,
        fresh=args.fresh,
        base_url=base_url,
        api_key=api_key,
    )
    results = benchmark.run()
    results.print_scorecard()
    results.save_submission(output)
    meta = {
        "model": results.model_name,
        "provider": results.provider,
        "spec": args.model,
        "judge": args.judge,
        "base_url": base_url or "",
        "date": results.run_date,
        "items": len(results.records),
        "overall": round(results.score * 100, 1),
        "errors": sum(1 for r in results.records if r.get("error")),
    }
    (run_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[SynhalEES] raw results : {checkpoint}")
    print(f"[Synhalees] scorecard   : {output}")
    print("[SynhalEES] interrupted? rerun the same command to resume.")
    return 0


def main(argv: list[str] | None = None) -> int:
    return run_from_args(parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())