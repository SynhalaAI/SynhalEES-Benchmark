"""SynhalEES CLI entry point.

Usage:
    python run_benchmark.py --model gpt-4o --provider openai --modality all
"""

from __future__ import annotations

import argparse

_VALID_MODALITIES = ("text", "vision", "audio", "all")
_VALID_PROVIDERS = ("huggingface", "openai", "anthropic", "local")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="synhalees",
        description="Run the SynhalEES multimodal Sinhala benchmark.",
    )
    parser.add_argument(
        "--model",
        default="meta-llama/Meta-Llama-3-8B-Instruct",
        help="Model name or HuggingFace ID (default: Meta-Llama-3-8B-Instruct).",
    )
    parser.add_argument(
        "--provider",
        choices=_VALID_PROVIDERS,
        default="local",
        help="Model provider (default: local).",
    )
    parser.add_argument(
        "--modality",
        choices=_VALID_MODALITIES,
        default="all",
        help="Modalities to evaluate (default: all).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    # TODO: wire the benchmark runner (synhalees.SynhalEESBenchmark). The CLI
    # currently only validates arguments and prints the run configuration.
    print(
        f"[SynhalEES] run requested: model={args.model!r} "
        f"provider={args.provider!r} modality={args.modality!r}"
    )
    print("[SynhalEES] runner not implemented yet - scaffolding milestone only.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())