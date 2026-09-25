"""Pillar-First dataset loaders and registry.

Loads ``benchmark_data/<slug>/`` per STRUCTURE.md section 5:

- ``text.csv``           -> TextItem   (id, prompt, ground_truth, eval_type)
- ``vision/vision.csv``  -> VisionItem (id, image_file, question, ground_truth, eval_type)
- ``audio/audio.csv``    -> AudioItem  (id, audio_file, ground_truth, eval_type)

Vision rows with ``eval_type = "wer"`` may leave ``question`` empty -- or
omit the column entirely on wer-only files (the OCR exception in
STRUCTURE.md section 5B): the loader then injects ``DEFAULT_OCR_PROMPT`` so
callers always receive a ready-to-use prompt. Non-OCR rows must carry an
image-specific question and are validated.

Audio rows behave the same way (STRUCTURE.md section 5C): an empty
``question`` on a ``wer`` row injects ``DEFAULT_ASR_PROMPT``; question-less
``classification`` rows receive ``GENERIC_CLASSIFY_PREFIX`` plus the
pillar's distinct ground_truth labels (a "listen and choose" option set);
other comprehension rows receive ``GENERIC_AUDIO_PROMPT`` ("listen and
answer") -- so pillar CSVs never need a question column just to satisfy
the loader.
"""

from __future__ import annotations

import csv
import os
from dataclasses import dataclass
from pathlib import Path

# The standard transcription prompt injected for OCR vision rows whose
# question cell is empty. Kept byte-identical with STRUCTURE.md section 5B.
DEFAULT_OCR_PROMPT = "මේකේ තියෙන දේ අකුරෙන් ලියන්න."

# Default prompt for audio rows without a question: plain transcription.
DEFAULT_ASR_PROMPT = "මේකේ ඇහෙන දේ අකුරෙන් ලියන්න."

# Generic prompts injected for audio rows that carry no question of their
# own (byte-identical with STRUCTURE.md section 5C): comprehension rows get
# "listen and answer" wording -- the answer still has to come from the
# audio; classification rows get "listen and choose" plus the pillar's
# distinct ground_truth labels as the option set to choose from.
GENERIC_AUDIO_PROMPT = "මේක අහලා උත්තර දෙන්න."
GENERIC_CLASSIFY_PREFIX = "මේක අහලා තෝරන්න: "

# The only four eval engines defined by STRUCTURE.md section 2.
ALLOWED_EVAL_TYPES = frozenset(
    {"exact_match", "llm_judge", "wer", "classification"}
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_DATA_DIR = _REPO_ROOT / "benchmark_data"


@dataclass(frozen=True)
class TextItem:
    """One row of ``text.csv``."""

    id: str
    prompt: str
    ground_truth: str
    eval_type: str


@dataclass(frozen=True)
class VisionItem:
    """One row of ``vision/vision.csv`` with the image path resolved."""

    id: str
    image_file: str  # file name exactly as listed in vision.csv
    image_path: Path
    question: str  # never empty -- OCR rows receive DEFAULT_OCR_PROMPT
    ground_truth: str
    eval_type: str


@dataclass(frozen=True)
class AudioItem:
    """One row of ``audio/audio.csv`` with the audio path resolved."""

    id: str
    audio_file: str
    audio_path: Path
    question: str  # never empty -- defaults injected when the CSV omits one
    ground_truth: str
    eval_type: str


@dataclass(frozen=True)
class PillarData:
    """All three modalities of one pillar."""

    slug: str
    text: list[TextItem]
    vision: list[VisionItem]
    audio: list[AudioItem]


def data_dir(override: str | os.PathLike[str] | None = None) -> Path:
    """Return the benchmark_data directory (override for packaged installs)."""
    return Path(override) if override else _DEFAULT_DATA_DIR


def list_pillars(override: str | os.PathLike[str] | None = None) -> list[str]:
    """Return every pillar slug (e.g. ``01_buddhist_culture``), sorted."""
    root = data_dir(override)
    return sorted(
        d.name for d in root.iterdir() if d.is_dir() and d.name[:2].isdigit()
    )


def _pillar_dir(pillar: str, override: str | os.PathLike[str] | None = None) -> Path:
    path = data_dir(override) / pillar
    if not path.is_dir():
        raise FileNotFoundError(
            f"Unknown pillar '{pillar}'. Known pillars: {', '.join(list_pillars())}"
        )
    return path


def _read_csv(path: Path) -> list[dict[str, str]]:
    """Read a UTF-8 CSV into dicts; missing file raises a clear error."""
    if not path.is_file():
        raise FileNotFoundError(f"Missing dataset file: {path}")
    with open(path, encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    return rows


def _check_eval_type(value: str, *, where: str) -> str:
    if value not in ALLOWED_EVAL_TYPES:
        raise ValueError(
            f"{where}: invalid eval_type '{value}' "
            f"(allowed: {sorted(ALLOWED_EVAL_TYPES)})"
        )
    return value


def _require_columns(row: dict[str, str], needed: set[str], *, where: str) -> None:
    missing = needed - row.keys()
    if missing:
        raise ValueError(f"{where}: missing columns {sorted(missing)}")


def load_text(
    pillar: str, *, data_root: str | os.PathLike[str] | None = None
) -> list[TextItem]:
    """Load ``text.csv`` for one pillar."""
    path = _pillar_dir(pillar, data_root) / "text.csv"
    items: list[TextItem] = []
    for row in _read_csv(path):
        where = f"{pillar}/text.csv[{row.get('id', '?')}]"
        _require_columns(row, {"id", "prompt", "ground_truth", "eval_type"}, where=where)
        items.append(
            TextItem(
                id=row["id"],
                prompt=row["prompt"],
                ground_truth=row["ground_truth"],
                eval_type=_check_eval_type(row["eval_type"], where=where),
            )
        )
    return items


def load_vision(
    pillar: str,
    *,
    data_root: str | os.PathLike[str] | None = None,
    strict: bool = True,
) -> list[VisionItem]:
    """Load ``vision/vision.csv`` for one pillar.

    Empty (or column-omitted) questions on ``wer`` (OCR) rows receive
    ``DEFAULT_OCR_PROMPT`` (STRUCTURE.md section 5B). With ``strict=True`` every referenced image
    must exist on disk; set ``strict=False`` to browse in-progress pillars
    whose media has not been committed yet.
    """
    base = _pillar_dir(pillar, data_root)
    path = base / "vision" / "vision.csv"
    items: list[VisionItem] = []
    for row in _read_csv(path):
        where = f"{pillar}/vision.csv[{row.get('id', '?')}]"
        _require_columns(
            row, {"id", "image_file", "ground_truth", "eval_type"}, where=where
        )
        eval_type = _check_eval_type(row["eval_type"], where=where)
        # question is optional: wer-only files may omit the column entirely.
        question = row.get("question", "").strip()
        if not question:
            if eval_type != "wer":
                raise ValueError(
                    f"{where}: empty question is only allowed for eval_type 'wer' "
                    "(OCR exception, STRUCTURE.md section 5B)"
                )
            question = DEFAULT_OCR_PROMPT
        image_path = base / "vision" / "images" / row["image_file"]
        if strict and not image_path.is_file():
            raise FileNotFoundError(f"{where}: missing image file {image_path}")
        items.append(
            VisionItem(
                id=row["id"],
                image_file=row["image_file"],
                image_path=image_path,
                question=question,
                ground_truth=row["ground_truth"],
                eval_type=eval_type,
            )
        )
    return items


def load_audio(
    pillar: str,
    *,
    data_root: str | os.PathLike[str] | None = None,
    strict: bool = True,
) -> list[AudioItem]:
    """Load ``audio/audio.csv`` for one pillar.

    An optional ``question`` column carries comprehension questions
    (STRUCTURE.md section 5C); empty questions on ``wer`` (transcription)
    rows receive ``DEFAULT_ASR_PROMPT``; question-less ``classification``
    rows receive ``GENERIC_CLASSIFY_PREFIX`` plus the pillar's distinct
    labels, and other comprehension rows receive ``GENERIC_AUDIO_PROMPT``.

    With ``strict=True`` every referenced mp3 must exist on disk; set
    ``strict=False`` to browse in-progress pillars.
    """
    base = _pillar_dir(pillar, data_root)
    path = base / "audio" / "audio.csv"
    rows = list(_read_csv(path))
    # Distinct classification labels of this pillar (file order): they turn
    # the generic "listen and choose" prompt into a real option set.
    labels: list[str] = []
    for row in rows:
        if (row.get("eval_type") or "").strip() == "classification":
            gt = (row.get("ground_truth") or "").strip()
            if gt and gt not in labels:
                labels.append(gt)
    choose_prompt = (
        GENERIC_CLASSIFY_PREFIX + ", ".join(labels) if labels else GENERIC_AUDIO_PROMPT
    )
    items: list[AudioItem] = []
    for row in rows:
        where = f"{pillar}/audio.csv[{row.get('id', '?')}]"
        _require_columns(
            row, {"id", "audio_file", "ground_truth", "eval_type"}, where=where
        )
        eval_type = _check_eval_type(row["eval_type"], where=where)
        # Optional question column (STRUCTURE.md section 5C): empty on
        # transcription rows -> default ASR prompt; classification rows ->
        # GENERIC_CLASSIFY_PREFIX + the pillar's labels; other comprehension
        # rows -> GENERIC_AUDIO_PROMPT.
        question = row.get("question", "").strip()
        if not question:
            if eval_type == "wer":
                question = DEFAULT_ASR_PROMPT
            elif eval_type == "classification":
                question = choose_prompt
            else:
                question = GENERIC_AUDIO_PROMPT
        audio_path = base / "audio" / "mp3s" / row["audio_file"]
        if strict and not audio_path.is_file():
            raise FileNotFoundError(f"{where}: missing audio file {audio_path}")
        items.append(
            AudioItem(
                id=row["id"],
                audio_file=row["audio_file"],
                audio_path=audio_path,
                question=question,
                ground_truth=row["ground_truth"],
                eval_type=eval_type,
            )
        )
    return items


def load_pillar(
    pillar: str,
    *,
    data_root: str | os.PathLike[str] | None = None,
    strict: bool = True,
) -> PillarData:
    """Load all three modalities of one pillar in a single call."""
    return PillarData(
        slug=pillar,
        text=load_text(pillar, data_root=data_root),
        vision=load_vision(pillar, data_root=data_root, strict=strict),
        audio=load_audio(pillar, data_root=data_root, strict=strict),
    )


__all__ = [
    "ALLOWED_EVAL_TYPES",
    "DEFAULT_ASR_PROMPT",
    "DEFAULT_OCR_PROMPT",
    "GENERIC_AUDIO_PROMPT",
    "GENERIC_CLASSIFY_PREFIX",
    "AudioItem",
    "PillarData",
    "TextItem",
    "VisionItem",
    "data_dir",
    "list_pillars",
    "load_audio",
    "load_pillar",
    "load_text",
    "load_vision",
]
