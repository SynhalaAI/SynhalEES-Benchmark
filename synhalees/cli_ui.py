"""Pure-stdlib terminal UI, colors, tables, and workflow help for SynhalEES."""

from __future__ import annotations

import argparse
import os
import re
import sys
import unicodedata
from typing import Iterable, Sequence

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

_FORCE_NO_COLOR = False
_COLOR_CACHE: bool | None = None


def _enable_windows_vt() -> bool:
    """Best-effort VT100 escape activation on Windows consoles."""
    if os.name != "nt":
        return True
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        # ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
        return bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))
    except Exception:
        return False


def configure_color(*, no_color: bool = False) -> None:
    """Configure terminal color preference for the current process."""
    global _FORCE_NO_COLOR, _COLOR_CACHE
    _FORCE_NO_COLOR = bool(no_color)
    _COLOR_CACHE = None


def use_color(stream=None) -> bool:
    """Return True when ANSI styling should be emitted."""
    global _COLOR_CACHE
    if _FORCE_NO_COLOR or os.environ.get("NO_COLOR") is not None:
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    if _COLOR_CACHE is not None and stream in (None, sys.stdout):
        return _COLOR_CACHE
    target = stream or sys.stdout
    is_tty = bool(getattr(target, "isatty", lambda: False)())
    if not is_tty:
        # Colab / Jupyter notebooks support ANSI in cell output even without isatty.
        is_tty = "COLAB_RELEASE_TAG" in os.environ or "JPY_PARENT_PID" in os.environ
    if is_tty and os.name == "nt":
        _enable_windows_vt()
    if stream in (None, sys.stdout):
        _COLOR_CACHE = is_tty
    return is_tty


def _style(text: str, *codes: str) -> str:
    if not codes or not use_color():
        return text
    return f"\x1b[{';'.join(codes)}m{text}\x1b[0m"


def bold(text: str) -> str:
    return _style(text, "1")


def dim(text: str) -> str:
    return _style(text, "2")


def accent(text: str) -> str:
    return _style(text, "1", "36")


def cyan(text: str) -> str:
    return _style(text, "36")


def green(text: str) -> str:
    return _style(text, "32")


def yellow(text: str) -> str:
    return _style(text, "33")


def red(text: str) -> str:
    return _style(text, "31")


def blue(text: str) -> str:
    return _style(text, "34")


def code(text: str) -> str:
    return _style(text, "36")


def badge(kind: str, label: str | None = None) -> str:
    """Format a compact status badge like [OK] or [FAIL]."""
    k = kind.upper()
    text = f"[{label or k}]"
    if k in ("OK", "DONE", "PASS", "SYNC"):
        return _style(text, "1", "32")
    if k in ("WARN", "SKIP", "STALE", "PARTIAL"):
        return _style(text, "1", "33")
    if k in ("FAIL", "ERR", "ERROR", "MISS"):
        return _style(text, "1", "31")
    return _style(text, "1", "36")


def ok_line(msg: str) -> None:
    print(f"{badge('OK')} {msg}")


def warn_line(msg: str) -> None:
    print(f"{badge('WARN')} {msg}")


def err_line(msg: str, *, stream=None) -> None:
    print(f"{badge('FAIL')} {msg}", file=stream or sys.stderr)


def info_line(msg: str) -> None:
    print(f"{badge('INFO')} {msg}")


def _utf8_stdout() -> bool:
    enc = (getattr(sys.stdout, "encoding", None) or "").lower()
    return "utf" in enc


def rule(char: str | None = None, width: int = 72) -> str:
    ch = char or ("─" if _utf8_stdout() else "-")
    return dim(ch * width)


def heading(title: str, subtitle: str = "") -> None:
    """Print a clean section heading."""
    print()
    if subtitle:
        print(f"{bold(title)}  {dim(subtitle)}")
    else:
        print(bold(title))
    print(rule())


def visible_len(text: str) -> int:
    """Approximate monospace display width, ignoring ANSI escapes & Mn marks."""
    plain = _ANSI_RE.sub("", str(text))
    width = 0
    for ch in plain:
        if unicodedata.category(ch) in ("Mn", "Me", "Cf"):
            continue
        width += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return width


def pad_cell(text: str, width: int, *, right: bool = False) -> str:
    text = str(text)
    pad = max(0, width - visible_len(text))
    return (" " * pad + text) if right else (text + " " * pad)


def format_table(
    headers: Sequence[str],
    rows: Iterable[Sequence[object]],
    *,
    right_align: Sequence[int] = (),
    indent: int = 0,
    gap: int = 2,
) -> str:
    """Render an aligned, ANSI-aware plain table."""
    row_list = [[str(cell) for cell in row] for row in rows]
    n_cols = len(headers)
    widths = [visible_len(h) for h in headers]
    for row in row_list:
        for i in range(min(n_cols, len(row))):
            widths[i] = max(widths[i], visible_len(row[i]))

    right_set = set(right_align)
    prefix = " " * indent
    spacer = " " * gap
    sep_char = "─" if _utf8_stdout() else "-"

    lines = [
        prefix + spacer.join(
            bold(pad_cell(headers[i], widths[i], right=(i in right_set)))
            for i in range(n_cols)
        ),
        prefix + dim(spacer.join(sep_char * widths[i] for i in range(n_cols))),
    ]
    for row in row_list:
        padded = [
            pad_cell(row[i] if i < len(row) else "", widths[i], right=(i in right_set))
            for i in range(n_cols)
        ]
        lines.append(prefix + spacer.join(padded))
    return "\n".join(lines)


def print_table(
    headers: Sequence[str],
    rows: Iterable[Sequence[object]],
    *,
    right_align: Sequence[int] = (),
    indent: int = 0,
    gap: int = 2,
) -> None:
    print(format_table(headers, rows, right_align=right_align, indent=indent, gap=gap))


class SynhaleesHelpFormatter(argparse.RawDescriptionHelpFormatter):
    """Spacious argparse formatter for readable subcommand and option help."""

    def __init__(self, prog: str) -> None:
        super().__init__(prog, max_help_position=32, width=92)


def print_overview(version: str) -> None:
    """Print the grouped, self-documenting SynhalEES command overview."""
    print(f"{accent('SynhalEES CLI')} {dim(f'v{version}')} {dim('— The Multimodal Sinhala Cultural Benchmark')}")
    print(rule())

    groups = [
        ("Evaluate & Compare (Local API / Ollama)", [
            ("run", "Evaluate a model locally with crash-safe checkpoints"),
            ("compare", "Compare local runs in runs/ -> matrix + all_submissions.csv"),
        ]),
        ("Inspect & Discover", [
            ("status", "Project overview (dataset, runs, Kaggle archive, site sync)"),
            ("pillars [query]", "List all 15 pillars with item counts & eval types"),
            ("models [query]", "List registered providers/models & published status"),
            ("guide [topic]", "Step-by-step workflow recipes (local/kaggle/colab/site)"),
        ]),
        ("Leaderboard & Static Site  (also under `synhalees site <action>`)", [
            ("site ...", "Grouped site commands: publish | build | logos | check"),
            ("publish <slug>", "Promote runs/<slug>/submission.csv -> submissions/ & rebuild"),
            ("build", "Regenerate docs/assets/data/* + JSON-LD from submissions/"),
            ("logos", "Regenerate docs/assets/logo-data.js from docs/assets/logos/"),
            ("check", "Run all verification gates (logos, data sync, dataset, syntax)"),
        ]),
        ("Kaggle Benchmarks  (`synhalees kaggle <action>`)", [
            ("kaggle gen", "Regenerate the 17 modular task files under kaggle/tasks/"),
            ("kaggle push", "Upload task file(s) to Kaggle (default: all 17)"),
            ("kaggle run", "Start a server-side evaluation run on Kaggle"),
            ("kaggle coverage", "Show local slim-CSV coverage & missing Kaggle runs"),
            ("kaggle status", "Show coverage (default) or remote status for one <task>"),
            ("kaggle logs", "Fetch remote Kaggle run logs for <task>"),
            ("kaggle pull", "Download Kaggle artifacts -> merge into *.slim.csv (alias: download)"),
            ("kaggle submit", "Import slim CSVs or tasks -> submissions/ (alias: import)"),
            ("kaggle publish", "[Kaggle.com] Publish a task leaderboard on Kaggle"),
        ]),
    ]

    for title, items in groups:
        print(f"\n{bold(title)}")
        for cmd, desc in items:
            print(f"  {code(pad_cell(cmd, 20))} {desc}")

    print(f"\n{bold('Common Workflows')}")
    print(f"  {dim('1. Local run   :')} {code('synhalees run --model ollama:llama3.1:8b')} -> {code('synhalees compare')} -> {code('synhalees publish <slug>')}")
    print(f"  {dim('2. Kaggle sync :')} {code('synhalees kaggle pull')} -> {code('synhalees kaggle submit --build')} -> {code('synhalees check')}")
    print(f"  {dim('3. Quick help  :')} {code('synhalees guide')}   {dim('|')}   {code('synhalees <command> --help')}   {dim('|')}   {code('synhalees --no-color')}")
    print()


_GUIDE_SECTIONS: dict[str, tuple[str, list[tuple[str, str]]]] = {
    "local": (
        "Local Evaluation Workflow (API / Ollama)",
        [
            ("1. Pick a model & run locally (resumes automatically on interruption)",
             "synhalees run --model gemini:gemini-2.5-flash\n"
             "  # or local Ollama: synhalees run --model ollama:llama3.1:8b --pillars 01_buddhist_culture"),
            ("2. Compare local runs under runs/ and inspect the pillar matrix",
             "synhalees compare"),
            ("3. Promote a finished run to submissions/ & rebuild the leaderboard",
             "synhalees publish <model-slug>      # preview first with --dry-run"),
            ("4. Verify all CI gates before committing",
             "synhalees check"),
        ],
    ),
    "kaggle": (
        "Kaggle Benchmarks Server Workflow",
        [
            ("1. Regenerate the 17 task scripts (15 text pillars + vision + audio)",
             "synhalees kaggle gen              # preview with --dry-run"),
            ("2. Upload task scripts to Kaggle",
             "synhalees kaggle push             # or: synhalees kaggle push audio --wait"),
            ("3. Trigger a model run on Kaggle servers",
             "synhalees kaggle run synhalees-audio -m gemini-2.5-flash"),
            ("4. Inspect remote status, logs, or overall local coverage",
             "synhalees kaggle coverage         # local slim archive + missing server runs\n"
             "  synhalees kaggle status synhalees-audio\n"
             "  synhalees kaggle logs synhalees-audio -m gemini-2.5-flash"),
            ("5. Download results into lightweight *.slim.csv (auto-cleans .temp/)",
             "synhalees kaggle pull             # 15 text tasks (~145MB)\n"
             "  synhalees kaggle pull --modality 'vision|audio'"),
            ("6. Submit *.slim.csv into submissions/ & rebuild site data",
             "synhalees kaggle submit all --build"),
            ("7. (Optional) Publish task leaderboard on Kaggle.com",
             "synhalees kaggle publish synhalees-audio"),
        ],
    ),
    "colab": (
        "Low-Bandwidth Colab Export Workflow (0 MB Home Download for Artifacts)",
        [
            ("1. Open kaggle/colab_slim_export.ipynb on free Google Colab",
             "# Cell 1: logs in to Kaggle and prints `synhalees kaggle status`"),
            ("2. Pull heavy run artifacts on Colab cloud into KB-sized *.slim.csv",
             "synhalees kaggle pull all --modality 'vision|audio'"),
            ("3. Download only the updated *.slim.csv files to your PC (kaggle/results/)",
             "# Colab Cell 4 automatically downloads only modified *.slim.csv files"),
            ("4. Submit locally & rebuild the static leaderboard (no internet needed)",
             "synhalees kaggle submit all --build\n"
             "  synhalees check"),
        ],
    ),
    "site": (
        "Leaderboard Site & Registry Maintenance",
        [
            ("1. Register new models/providers/display names in synhalees/models.json",
             "synhalees models                  # inspect registered taxonomy & coverage"),
            ("2. If adding/editing SVG logos in docs/assets/logos/",
             "synhalees site logos              # regenerates docs/assets/logo-data.js"),
            ("3. Rebuild docs/assets/data/* and SEO JSON-LD from submissions/",
             "synhalees site build              # or preview with: synhalees site build --demo"),
            ("4. Verify everything matches CI gates",
             "synhalees site check              # same as: synhalees check"),
        ],
    ),
}


def print_guide(topic: str = "all") -> None:
    """Print step-by-step workflow guides."""
    key = (topic or "all").lower()
    keys = list(_GUIDE_SECTIONS) if key == "all" else [key]
    print(f"{accent('SynhalEES Workflow Guide')} {dim('— copy-pasteable end-to-end recipes')}")
    for k in keys:
        title, steps = _GUIDE_SECTIONS[k]
        heading(title)
        for label, cmd in steps:
            print(f"  {bold(label)}")
            for line in cmd.splitlines():
                if line.lstrip().startswith("#"):
                    print(f"    {dim(line.strip())}")
                else:
                    print(f"    {code(line.strip())}")
    print(f"\n{dim('Tip: run `synhalees guide local|kaggle|colab|site` to focus on one workflow.')}\n")

