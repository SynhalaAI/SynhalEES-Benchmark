"""Scoring engines for the four ``eval_type`` values (STRUCTURE.md section 2).

- ``exact_match``    -- whitespace-normalized equality / containment.
- ``classification`` -- normalized containment of the label (e.g. ``friendly_banter``).
- ``wer``            -- Word Error Rate over whitespace-tokenized text;
  passes when ``wer <= WER_THRESHOLD`` (pure-Python Levenshtein, no deps).
- ``llm_judge``      -- a judge model grades the response against a rubric;
  see :func:`build_judge_prompt` / :func:`parse_judge_verdict`.

Everything here is pure stdlib so it can be unit-tested offline.
"""

from __future__ import annotations

import json
import re

#: Maximum Word Error Rate tolerated for a ``wer`` row to pass.
WER_THRESHOLD = 0.3


def _normalize(text: str) -> str:
    """Collapse all whitespace runs; keep Sinhala codepoints untouched."""
    return " ".join(str(text).split())


def _levenshtein(a: list[str], b: list[str]) -> int:
    """Classic DP edit distance over token lists."""
    if len(a) < len(b):
        a, b = b, a
    previous = list(range(len(b) + 1))
    for i, tok_a in enumerate(a, start=1):
        current = [i]
        for j, tok_b in enumerate(b, start=1):
            current.append(
                min(
                    previous[j] + 1,
                    current[j - 1] + 1,
                    previous[j - 1] + (tok_a != tok_b),
                )
            )
        previous = current
    return previous[-1]


def word_error_rate(reference: str, hypothesis: str) -> float:
    """WER over whitespace-tokenized text (normalized Levenshtein distance)."""
    ref_tokens = _normalize(reference).split()
    hyp_tokens = _normalize(hypothesis).split()
    if not ref_tokens:
        return 0.0 if not hyp_tokens else 1.0
    return _levenshtein(ref_tokens, hyp_tokens) / len(ref_tokens)


_JUDGE_TEMPLATE = """You are grading an answer from a Sinhala-language cultural benchmark.

Question asked to the model:
{prompt}

Reference answer (ground truth):
{ground_truth}

Model answer to grade:
{response}

Criteria:
1. The model answer conveys the same meaning as the reference answer (wording may differ).
2. It reads like a native Sri Lankan Sinhala speaker wrote it, not a translated machine.

Reply with ONLY a JSON object, no markdown fences:
{{"passed": true or false, "reason": "one short sentence"}}"""


def build_judge_prompt(prompt: str, response: str, ground_truth: str) -> str:
    """Prompt sent to the judge model for ``llm_judge`` rows."""
    return _JUDGE_TEMPLATE.format(
        prompt=prompt, response=response, ground_truth=ground_truth
    )


def parse_judge_verdict(text: str) -> tuple[bool, str]:
    """Parse the judge reply into (passed, reason); lenient on formatting."""
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if match:
        try:
            payload = json.loads(match.group(0))
            return bool(payload.get("passed")), str(payload.get("reason", ""))
        except (ValueError, AttributeError):
            pass
    lowered = text.lower()
    return "true" in lowered and "passed" in lowered, text.strip()[:200]


def score_response(
    response: str,
    ground_truth: str,
    eval_type: str,
    *,
    wer_threshold: float = WER_THRESHOLD,
    judge_passed: bool | None = None,
) -> bool:
    """Pass/fail for one model response.

    ``llm_judge`` rows require ``judge_passed`` (the verdict parsed from the
    judge model); the other three eval types are fully deterministic.
    """
    norm_resp = _normalize(response)
    norm_truth = _normalize(ground_truth)
    if eval_type == "exact_match":
        return norm_resp == norm_truth or norm_truth in norm_resp
    if eval_type == "classification":
        return norm_truth in norm_resp
    if eval_type == "wer":
        return word_error_rate(ground_truth, response) <= wer_threshold
    if eval_type == "llm_judge":
        if judge_passed is None:
            raise ValueError("llm_judge rows need judge_passed")
        return judge_passed
    raise ValueError(f"Unknown eval_type {eval_type!r}")


__all__ = [
    "WER_THRESHOLD",
    "build_judge_prompt",
    "parse_judge_verdict",
    "score_response",
    "word_error_rate",
]