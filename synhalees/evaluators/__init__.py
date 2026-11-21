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
import textwrap

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


_JUDGE_CRITERIA = (
    "The response conveys the same meaning as the reference answer: "
    "'{ground_truth}'. Wording may differ; the meaning must match.",
    "The response reads like a native Sri Lankan Sinhala speaker wrote it -- "
    "natural register and idiom, not translated-sounding English.",
)


def build_classification_prompt(prompt: str, choices: str) -> str:
    """Apply the same single-label instruction used by Kaggle tasks."""
    if not choices:
        return prompt
    return (
        f"Choose exactly one label from [{choices}]. "
        f"Reply with only the label. {prompt}"
    )


def _judge_criteria(ground_truth: str) -> tuple[str, ...]:
    return tuple(
        criterion.format(ground_truth=ground_truth)
        for criterion in _JUDGE_CRITERIA
    )


def build_judge_prompt(response: str, ground_truth: str) -> str:
    """Build the same criteria-based assessment prompt used by Kaggle."""
    criteria = _judge_criteria(ground_truth)
    formatted_criteria = "\n".join(
        f"{index}. {criterion}" for index, criterion in enumerate(criteria, start=1)
    )
    schema = json.dumps(
        {
            "results": [
                {
                    "criterion": "<criterion>",
                    "passed": True,
                    "reason": "<brief explanation>",
                    "confidence": 0,
                }
            ]
        },
        indent=2,
    )
    template = """\
You are an impartial and strict technical evaluator.

### Task
Verify if the **Generated Response** complies strictly with the **Criteria**.

### Input Data
**Generated Response:**
{response}

**Criteria:**
{formatted_criteria}

### Evaluation Protocol
1. Analyze the response against the criteria.
2. Be strict. If the text is ambiguous, the check fails.
3. Output one result per criterion in the requested JSON structure.

Return only valid JSON with this structure:
{schema}
The results must be in the same order as the criteria. confidence must be an integer.
"""
    return textwrap.dedent(template).format(
        response=response,
        formatted_criteria=formatted_criteria,
        schema=schema,
    ).strip()


def parse_judge_verdict(text: str) -> tuple[bool, str]:
    """Parse Kaggle's per-criterion assessment report into an all-pass verdict."""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return False, "Judge response was not valid assessment JSON"
    if not isinstance(payload, dict):
        return False, "Judge response was not an assessment object"
    results = payload.get("results")
    if not isinstance(results, list) or len(results) != len(_JUDGE_CRITERIA):
        return False, "Judge response must contain one result per criterion"
    for result in results:
        if (
            not isinstance(result, dict)
            or not isinstance(result.get("criterion"), str)
            or type(result.get("passed")) is not bool
            or not isinstance(result.get("reason"), str)
            or type(result.get("confidence")) is not int
        ):
            return False, "Judge response did not match the assessment schema"
    return (
        all(result["passed"] for result in results),
        "; ".join(result["reason"] for result in results)[:200],
    )


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
    "build_classification_prompt",
    "build_judge_prompt",
    "parse_judge_verdict",
    "score_response",
    "word_error_rate",
]