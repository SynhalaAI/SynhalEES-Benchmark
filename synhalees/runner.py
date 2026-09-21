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
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator

from . import data as _data
from .evaluators import (
    build_judge_prompt,
    parse_judge_verdict,
    score_response,
)
from .models import APIModelError, BaseModel, get_model


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

    def save_submission(self, path: str | Path) -> Path:
        """Write the scorecard as a CSV."""
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["pillar", "n_items", "accuracy"])
            for pillar, n, acc in self.scorecard_rows():
                writer.writerow([pillar, n, f"{acc:.4f}"])
            writer.writerow(["OVERALL", len(self.records), f"{self.score:.4f}"])
        return out

class SynhalEESBenchmark:
    """Run the benchmark against a chat-LLM API, with crash-safe resume.

    Args:
        model: model spec string (``"ollama:llama3.1:8b"``,
            ``"openrouter:openai/gpt-4o-mini"``, ``"gemini:gemini-2.5-flash"``,
            ``"openai:gpt-4o"``) or a ready :class:`BaseModel` instance.
        judge_model: model used to grade ``llm_judge`` rows; ``None`` reuses
            ``model`` (self-judging).
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
    ) -> None:
        self.model = get_model(model) if isinstance(model, str) else model
        self.judge_model = (
            get_model(judge_model) if isinstance(judge_model, str) else judge_model
        ) or self.model
        self.modalities = tuple(modalities)
        self.pillars = list(pillars) if pillars else _data.list_pillars(data_root)
        self.data_root = data_root
        self.store = CheckpointStore(checkpoint, fresh=fresh)

    def _run_item(self, row: dict[str, Any]) -> dict[str, Any]:
        """Evaluate one dataset row and return its checkpoint record."""
        key = item_key(row["pillar"], row["modality"], row["id"])
        record: dict[str, Any] = {"key": key, **row}
        try:
            response = self.model.complete(
                row["prompt"],
                image_path=row["media_path"] if row["modality"] == "vision" else None,
                audio_path=row["media_path"] if row["modality"] == "audio" else None,
            )
        except APIModelError as exc:
            record.update(is_correct=False, response="", error=str(exc))
            return record

        judge_passed = None
        judge_reason = ""
        if row["eval_type"] == "llm_judge":
            verdict = self.judge_model.complete(
                build_judge_prompt(row["prompt"], response, row["ground_truth"])
            )
            judge_passed, judge_reason = parse_judge_verdict(verdict)

        record.update(
            response=response,
            is_correct=score_response(
                response, row["ground_truth"], row["eval_type"],
                judge_passed=judge_passed,
            ),
            judge_reason=judge_reason,
        )
        return record

    def run(self, *, progress: bool = True) -> BenchmarkResults:
        """Evaluate every pending row; resume skips checkpointed ones."""
        pending = [
            row for row in iter_items(self.pillars, self.modalities, self.data_root)
            if not self.store.done(
                item_key(row["pillar"], row["modality"], row["id"])
            )
        ]
        total = len(pending) + sum(
            1 for k in self.store.records if k.split("/")[0] in self.pillars
        )
        if progress and self.store.records:
            print(
                f"[SynhalEES] resuming: {len(self.store.records)} done, "
                f"{len(pending)} pending"
            )
        for index, row in enumerate(pending, start=1):
            record = self._run_item(row)
            self.store.append(record)
            if progress:
                status = "PASS" if record["is_correct"] else "fail"
                done = total - len(pending) + index
                print(f"[{done}/{total}] {record['key']:<40} {status}")
        records = [
            r for k, r in sorted(self.store.records.items())
            if k.split("/")[0] in self.pillars
        ]
        return BenchmarkResults(
            records=records,
            model_name=self.model.name,
            pillars=self.pillars,
        )


__all__ = [
    "BenchmarkResults",
    "CheckpointStore",
    "SynhalEESBenchmark",
    "item_key",
    "iter_items",
]