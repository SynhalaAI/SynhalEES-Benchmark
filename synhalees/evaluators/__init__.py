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
import textwrap

#: Maximum Word Error Rate tolerated for a ``wer`` row to pass.
WER_THRESHOLD = 0.3


def strip_thinking(text: str) -> str:
    """Remove chain-of-thought blocks so only the clean final answer is evaluated.

    Strips <think>...</think>, <thought>...</thought>, <reasoning>...</reasoning>,
    and unclosed leading/trailing thinking tags.
    """
    if not text:
        return ""
    # Strip paired <think>...</think>, <thought>...</thought>, <reasoning>...</reasoning>
    cleaned = re.sub(
        r"(?is)<\s*(?:think|thought|reasoning)\s*>.*?<\s*/\s*(?:think|thought|reasoning)\s*>",
        "",
        str(text),
    )
    # Strip unclosed leading tag (e.g. <think>... with no closing tag before end of string)
    cleaned = re.sub(r"(?is)^<\s*(?:think|thought|reasoning)\s*>.*$", "", cleaned)
    # Strip dangling close tag at the beginning
    cleaned = re.sub(r"(?is)^.*?<\s*/\s*(?:think|thought|reasoning)\s*>", "", cleaned)
    return cleaned.strip()


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


def _extract_json_payload(text: str) -> Any:
    """Extract and parse JSON from raw text, markdown code blocks, or wrapped commentary."""
    clean = strip_thinking(text).strip()
    if not clean:
        return None

    # 1. Direct parse attempt
    try:
        return json.loads(clean)
    except json.JSONDecodeError:
        pass

    # 2. Extract from markdown code fence ```json ... ``` or ``` ... ```
    fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", clean)
    if fence_match:
        try:
            return json.loads(fence_match.group(1).strip())
        except json.JSONDecodeError:
            pass

    # 3. Find outermost JSON object { ... }
    start_brace = clean.find("{")
    end_brace = clean.rfind("}")
    if start_brace != -1 and end_brace > start_brace:
        try:
            return json.loads(clean[start_brace : end_brace + 1])
        except json.JSONDecodeError:
            pass

    return None


def parse_judge_verdict(text: str) -> tuple[bool, str]:
    """Parse Kaggle's per-criterion assessment report into an all-pass verdict."""
    payload = _extract_json_payload(text)
    if payload is None:
        return False, "Judge response was not valid assessment JSON"
    if not isinstance(payload, dict):
        return False, "Judge response was not an assessment object"
    results = payload.get("results")
    if not isinstance(results, list) or len(results) != len(_JUDGE_CRITERIA):
        return False, "Judge response must contain one result per criterion"
    for result in results:
        if not isinstance(result, dict):
            return False, "Judge response did not match the assessment schema"
        passed_val = result.get("passed")
        if not isinstance(passed_val, bool):
            if isinstance(passed_val, str) and passed_val.lower() in ("true", "1"):
                result["passed"] = True
            elif isinstance(passed_val, (int, float)) and passed_val == 1:
                result["passed"] = True
            elif isinstance(passed_val, str) and passed_val.lower() in ("false", "0"):
                result["passed"] = False
            elif isinstance(passed_val, (int, float)) and passed_val == 0:
                result["passed"] = False
            else:
                return False, "Judge response did not match the assessment schema"
        if not isinstance(result.get("reason"), str):
            result["reason"] = str(result.get("reason", ""))
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
    clean_resp = strip_thinking(response)
    norm_resp = _normalize(clean_resp)
    norm_truth = _normalize(ground_truth)
    if eval_type == "exact_match":
        return norm_resp == norm_truth or norm_truth in norm_resp
    if eval_type == "classification":
        return norm_truth in norm_resp
    if eval_type == "wer":
        return word_error_rate(ground_truth, clean_resp) <= wer_threshold
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
    "strip_thinking",
    "word_error_rate",
]