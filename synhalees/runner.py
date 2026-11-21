"""Checkpointed benchmark runner for SynhalEES (direct API edition).

Flow per dataset row:

1. Build the item list from the Pillar-First dataset (:mod:`synhalees.data`).
   Vision/audio rows whose media files are not committed yet are skipped.
2. For every row, call the model (text / image / audio prompt), score the
   response with :mod:`synhalees.evaluators` (``llm_judge`` rows get a second
   call to the judge model).
3. Append each result as one JSON line to the checkpoint file **immediately**.

Resume: if the process dies (Ctrl+C, quota, network), just rerun the same
command -- rows already present in the checkpoint are skipped. Use
``fresh=True`` / ``--fresh`` to discard the checkpoint and start over.
"""

from __future__ import annotations

import csv
import json
import os
import re
import threading
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

from . import data as _data
from .evaluators import (
    build_classification_prompt,
    build_judge_prompt,
    parse_judge_verdict,
    score_response,
    strip_thinking,
)
from .models import APIModelError, BaseModel, OpenAIModel, get_model


def model_slug(spec: str) -> str:
    """Filesystem-safe folder name for a model spec.

    ``"openrouter:openai/gpt-4o-mini"`` -> ``"openrouter__openai-gpt-4o-mini"``.
    Keeps one run directory per model so results never overwrite each other.
    """
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", spec.strip())
    return slug.strip("-") or "model"


def item_key(pillar: str, modality: str, item_id: str) -> str:
    """Stable identity of one dataset row inside the checkpoint file."""
    return f"{pillar}/{modality}/{item_id}"


def iter_items(
    pillars: Iterable[str],
    modalities: Iterable[str],
    data_root: str | None = None,
) -> Iterator[dict[str, Any]]:
    """Yield runnable rows (dicts) across pillars and modalities.

    Vision/audio rows without committed media files are skipped; a missing
    vision.csv/audio.csv simply means that modality has no rows yet.
    """
    wanted = set(modalities)
    for pillar in pillars:
        if "text" in wanted:
            for it in _data.load_text(pillar, data_root=data_root):
                yield {
                    "pillar": pillar, "modality": "text", "id": it.id,
                    "prompt": it.prompt, "ground_truth": it.ground_truth,
                    "eval_type": it.eval_type, "media_path": "",
                }
        if "vision" in wanted:
            try:
                items = _data.load_vision(pillar, data_root=data_root, strict=False)
            except FileNotFoundError:
                items = []
            for it in items:
                if it.image_path.is_file():
                    yield {
                        "pillar": pillar, "modality": "vision", "id": it.id,
                        "prompt": it.question, "ground_truth": it.ground_truth,
                        "eval_type": it.eval_type, "media_path": str(it.image_path),
                    }
        if "audio" in wanted:
            try:
                items = _data.load_audio(pillar, data_root=data_root, strict=False)
            except FileNotFoundError:
                items = []
            for it in items:
                if it.audio_path.is_file():
                    yield {
                        "pillar": pillar, "modality": "audio", "id": it.id,
                        "prompt": it.question, "ground_truth": it.ground_truth,
                        "eval_type": it.eval_type, "media_path": str(it.audio_path),
                    }


def add_kaggle_classification_prompts(
    rows: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Apply Kaggle's task-scoped single-label instruction to classification rows."""
    prepared = [dict(row) for row in rows]
    choices_by_scope: dict[tuple[str, str | None], set[str]] = {}
    for row in prepared:
        if row["eval_type"] != "classification":
            continue
        modality = row["modality"]
        scope = (modality, row["pillar"] if modality == "text" else None)
        choices_by_scope.setdefault(scope, set()).add(row["ground_truth"])

    for row in prepared:
        if row["eval_type"] != "classification":
            continue
        modality = row["modality"]
        scope = (modality, row["pillar"] if modality == "text" else None)
        choices = " / ".join(sorted(choices_by_scope[scope]))
        row["prompt"] = build_classification_prompt(row["prompt"], choices)
    return prepared


class CheckpointStore:
    """Append-only JSONL checkpoint; the backbone of crash-safe resume.

    Each completed row is one JSON line keyed by ``pillar/modality/id``.
    Pass ``path=None`` for an in-memory store (tests, dry runs).
    """

    def __init__(self, path: str | Path | None, *, fresh: bool = False) -> None:
        self.path = Path(path) if path else None
        self.records: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        if self.path and self.path.is_file() and not fresh:
            with open(self.path, encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except ValueError:
                        continue  # tolerate a truncated final line from a crash
                    self.records[record["key"]] = record
        elif self.path and fresh and self.path.is_file():
            self.path.unlink()

    def done(self, key: str) -> bool:
        return key in self.records

    def append(self, record: dict[str, Any]) -> None:
        with self._lock:
            self.records[record["key"]] = record
            if self.path:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with open(self.path, "a", encoding="utf-8") as handle:
                    handle.write(
                        json.dumps(record, ensure_ascii=False) + "\n"
                    )
                    handle.flush()
                    os.fsync(handle.fileno())


@dataclass
class BenchmarkResults:
    """Outcome of :meth:`SynhalEESBenchmark.run`."""

    records: list[dict[str, Any]]
    model_name: str
    pillars: list[str] = field(default_factory=list)
    provider: str = ""
    run_date: str = field(default_factory=lambda: str(date.today()))

    @property
    def score(self) -> float:
        """Overall accuracy in [0, 1]; errored rows count as incorrect."""
        if not self.records:
            return 0.0
        return sum(1 for r in self.records if r.get("is_correct")) / len(self.records)

    def scorecard_rows(self) -> list[tuple[str, int, float]]:
        """(pillar, n_items, accuracy) rows for the scorecard."""
        grouped: dict[str, list[dict[str, Any]]] = {}
        for record in self.records:
            grouped.setdefault(record["pillar"], []).append(record)
        return [
            (
                pillar,
                len(rows),
                sum(1 for r in rows if r.get("is_correct")) / len(rows),
            )
            for pillar, rows in sorted(grouped.items())
        ]

    def print_scorecard(self) -> None:
        """Print the overall score and the per-pillar breakdown."""
        print(f"\n===== SynhalEES Scorecard -- model: {self.model_name} =====")
        rows = self.scorecard_rows()
        if rows:
            print(f"{'Pillar':<28}{'Items':>6}{'Accuracy':>10}")
            print("-" * 44)
            for pillar, n, acc in rows:
                print(f"{pillar:<28}{n:>6}{acc:>9.1%}")
            print("-" * 44)
        print(f"{'OVERALL':<28}{len(self.records):>6}{self.score:>9.1%}\n")

    def cell_rows(self) -> list[tuple[str, str, int, float]]:
        """(pillar, modality, n_items, accuracy) for every pillar x modality."""
        grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for record in self.records:
            key = (record["pillar"], record.get("modality", "text"))
            grouped.setdefault(key, []).append(record)
        return [
            (pillar, modality, len(rows),
             sum(1 for r in rows if r.get("is_correct")) / len(rows))
            for (pillar, modality), rows in sorted(grouped.items())
        ]

    def save_submission(self, path: str | Path) -> Path:
        """Write the scorecard in the leaderboard schema.

        Columns: ``model,provider,date,pillar,modality,score`` with ``score``
        as a 0-100 percentage, exactly what ``tools/build_leaderboard.py``
        consumes. One file per model -- never merge different models here.
        """
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["model", "provider", "date", "pillar",
                             "modality", "score"])
            for pillar, modality, n, acc in self.cell_rows():
                writer.writerow([self.model_name, self.provider, self.run_date,
                                 pillar, modality, f"{acc * 100:.1f}"])
        return out

class SynhalEESBenchmark:
    """Run the benchmark against a chat-LLM API, with crash-safe resume.

    Args:
        model: model spec string (``"ollama:llama3.1:8b"``,
            ``"openrouter:openai/gpt-4o-mini"``, ``"gemini:gemini-2.5-flash"``,
            ``"openai:gpt-4o"``) or a ready :class:`BaseModel` instance.
        judge_model: model used to grade ``llm_judge`` rows; ``None`` reuses
            ``model`` (self-judging).
        base_url: override the chat-completions base URL (self-hosted
            OpenAI-compatible server, e.g. a Colab/vLLM tunnel). Only applied to
            the model spec and to a judge spec from the same provider prefix.
        api_key: key for ``base_url`` (self-hosted servers often ignore it).
        checkpoint: JSONL checkpoint path (default ``runs/checkpoint.jsonl``);
            ``None`` disables persistence. Rerunning the same command resumes
            where the previous run stopped; ``fresh=True`` restarts.
    """

    def __init__(
        self,
        model: str | BaseModel,
        *,
        judge_model: str | BaseModel | None = None,
        modalities: Iterable[str] = ("text",),
        pillars: Iterable[str] | None = None,
        data_root: str | None = None,
        checkpoint: str | Path | None = "runs/checkpoint.jsonl",
        fresh: bool = False,
        base_url: str | None = None,
        api_key: str | None = None,
        reasoning_effort: str | None = None,
    ) -> None:
        self.model_spec = model if isinstance(model, str) else None
        endpoint: dict[str, str] = {}
        if base_url:
            endpoint["base_url"] = base_url
        if api_key:
            endpoint["api_key"] = api_key
        self.model = (
            get_model(model, **endpoint) if isinstance(model, str) else model
        )
        if reasoning_effort not in (None, "low", "medium", "high", "none", "off"):
            raise ValueError("reasoning_effort must be low, medium, high, none, off, or None.")
        self.reasoning_effort = reasoning_effort
        judge_endpoint = (
            endpoint
            if isinstance(judge_model, str)
            and isinstance(model, str)
            and judge_model.partition(":")[0] == model.partition(":")[0]
            else {}
        )
        self.judge_model = (
            get_model(judge_model, **judge_endpoint)
            if isinstance(judge_model, str)
            else judge_model
        ) or self.model
        self.modalities = tuple(modalities)
        self.pillars = list(pillars) if pillars else _data.list_pillars(data_root)
        self.data_root = data_root
        self.store = CheckpointStore(checkpoint, fresh=fresh)

    def _run_item(
        self,
        row: dict[str, Any],
        *,
        on_model_response: Callable[[str], None] | None = None,
        on_judge_start: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        """Evaluate one dataset row and return its checkpoint record."""
        key = item_key(row["pillar"], row["modality"], row["id"])
        record: dict[str, Any] = {"key": key, **row}
        reasoning = ""
        try:
            model = self.model
            image_path = row["media_path"] if row["modality"] == "vision" else None
            audio_path = row["media_path"] if row["modality"] == "audio" else None
            if self.reasoning_effort:
                if isinstance(model, OpenAIModel):
                    response, _usage = model.complete_with_usage(
                        row["prompt"],
                        image_path=image_path,
                        audio_path=audio_path,
                        reasoning_effort=self.reasoning_effort,
                    )
                    reasoning = _usage.get("reasoning_content") or ""
                else:
                    response = model.complete(
                        row["prompt"],
                        image_path=image_path,
                        audio_path=audio_path,
                        reasoning_effort=self.reasoning_effort,
                    )
            else:
                if isinstance(model, OpenAIModel):
                    response, _usage = model.complete_with_usage(
                        row["prompt"],
                        image_path=image_path,
                        audio_path=audio_path,
                    )
                    reasoning = _usage.get("reasoning_content") or ""
                else:
                    response = model.complete(
                        row["prompt"],
                        image_path=image_path,
                        audio_path=audio_path,
                    )
        except APIModelError as exc:
            record.update(is_correct=False, response="", error=str(exc))
            return record

        if reasoning:
            record["reasoning"] = reasoning

        response = strip_thinking(response)

        if on_model_response is not None:
            try:
                on_model_response(response, reasoning)
            except TypeError:
                on_model_response(response)

        judge_passed = None
        judge_reason = ""
        if row["eval_type"] == "llm_judge":
            if on_judge_start is not None:
                on_judge_start()
            try:
                verdict = self.judge_model.complete(
                    build_judge_prompt(response, row["ground_truth"])
                )
                judge_passed, judge_reason = parse_judge_verdict(verdict)
            except APIModelError as exc:
                judge_passed = False
                judge_reason = f"Judge failed: {exc}"
                record["error"] = str(exc)

        record.update(
            response=response,
            is_correct=score_response(
                response, row["ground_truth"], row["eval_type"],
                judge_passed=judge_passed,
            ),
            judge_reason=judge_reason,
        )
        return record

    def run(
        self,
        *,
        progress: bool = True,
        item_event: Callable[[dict[str, Any]], None] | None = None,
    ) -> BenchmarkResults:
        """Evaluate every pending row; resume skips checkpointed ones."""
        rows = add_kaggle_classification_prompts(
            iter_items(self.pillars, self.modalities, self.data_root)
        )
        pending = [
            row for row in rows
            if not self.store.done(
                item_key(row["pillar"], row["modality"], row["id"])
            )
        ]
        total = len(rows)
        if progress and self.store.records:
            print(
                f"[SynhalEES] resuming: {len(self.store.records)} done, "
                f"{len(pending)} pending"
            )

        # Preflight: If there are pending llm_judge items, verify that the judge model responds!
        pending_judge = [
            row for row in pending if row.get("eval_type") == "llm_judge"
        ]
        if pending_judge and self.judge_model is not None:
            judge_name = getattr(self.judge_model, "name", "judge")
            if progress:
                print(
                    f"[SynhalEES] Preflight: Verifying judge model '{judge_name}'..."
                )
            test_prompt = build_judge_prompt(
                response="ශ්‍රී ලංකාවේ අගනුවර කොළඹ වේ.",
                ground_truth="ශ්‍රී ලංකාවේ අගනුවර කොළඹ වේ.",
            )
            try:
                verdict = self.judge_model.complete(test_prompt)
                passed, reason = parse_judge_verdict(verdict)
                if progress:
                    print(f"[SynhalEES] Judge verified successfully ({reason or 'ready'}).")
            except APIModelError as exc:
                err_msg = f"Judge model preflight check failed for '{judge_name}': {exc}"
                if progress:
                    print(f"[SynhalEES] ERROR: {err_msg}")
                raise APIModelError(err_msg) from exc

        done_before = total - len(pending)
        for index, row in enumerate(pending, start=1):
            key = item_key(row["pillar"], row["modality"], row["id"])

            def emit(phase: str, **details: Any) -> None:
                if item_event is not None:
                    item_event({"phase": phase, "key": key, **details})

            emit(
                "item_start",
                index=done_before + index,
                total=total,
                pillar=row["pillar"],
                modality=row["modality"],
                id=row["id"],
                prompt=row["prompt"],
                expected=row["ground_truth"],
                eval_type=row["eval_type"],
            )
            record = self._run_item(
                row,
                on_model_response=lambda response, reasoning="": emit(
                    "model_response", response=response, reasoning=reasoning
                ),
                on_judge_start=lambda: emit("judge_waiting"),
            )
            self.store.append(record)
            emit(
                "item_result",
                is_correct=bool(record["is_correct"]),
                response=record.get("response", ""),
                error=record.get("error", ""),
                judge_reason=record.get("judge_reason", ""),
            )
            if progress:
                status = "PASS" if record["is_correct"] else "fail"
                done = total - len(pending) + index
                print(f"[{done}/{total}] {record['key']:<40} {status}")
        records = [
            r for k, r in sorted(self.store.records.items())
            if k.split("/")[0] in self.pillars
        ]
        provider = (self.model_spec or "").partition(":")[0]
        return BenchmarkResults(
            records=records,
            model_name=self.model.name,
            pillars=self.pillars,
            provider=provider or "unknown",
        )


__all__ = [
    "BenchmarkResults",
    "CheckpointStore",
    "SynhalEESBenchmark",
    "item_key",
    "iter_items",
    "model_slug",
]