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

_VALID_MODALITIES = ("text", "vision", "audio", "all")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="synhalees",
        description="Run the SynhalEES multimodal Sinhala benchmark via LLM APIs.",
    )
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
        default="runs/checkpoint.jsonl",
        help="JSONL checkpoint path (default: runs/checkpoint.jsonl).",
    )
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="Discard the checkpoint and start the run from scratch.",
    )
    parser.add_argument(
        "--output",
        default="submission.csv",
        help="Where to write the scorecard CSV (default: submission.csv).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    from synhalees.runner import SynhalEESBenchmark

    modalities = (
        ("text", "vision", "audio") if args.modality == "all" else (args.modality,)
    )
    pillars = [p.strip() for p in args.pillars.split(",")] if args.pillars else None
    benchmark = SynhalEESBenchmark(
        args.model,
        judge_model=args.judge,
        modalities=modalities,
        pillars=pillars,
        checkpoint=args.checkpoint,
        fresh=args.fresh,
    )
    results = benchmark.run()
    results.print_scorecard()
    results.save_submission(args.output)
    print(f"[SynhalEES] scorecard written to {args.output}")
    print("[SynhalEES] interrupted? rerun the same command to resume.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())