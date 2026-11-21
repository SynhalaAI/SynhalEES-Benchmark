"""Headless-safe helper modules for the SynhalEES Desktop Studio (`synhalees/gui.py`).

All functions here avoid importing tkinter so they can be unit-tested cleanly
in headless environments:
- Stdout progress & failed-task parsing
- Slim-CSV unregistered model scanning & local run status summaries
- Organization SVG logo installation & `chart.js` / `app.js` / `logo-data.js` sync
- Pure-stdlib 24x24 SVG path rasterizer & monogram grid generator for Tk PhotoImage
- Model scorecard inspector & side-by-side comparison
- Read-only 15-pillar dataset inspector
- Family taxonomy management & legacy scorecard adoption/relocation
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import math
import re
import shutil
import time
import urllib.error
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from synhalees import kaggle_ops
from synhalees.data import load_pillar
from synhalees.http import open_url
from synhalees.kaggle_ops import KG_RESULTS, ROOT, RUNS, load_slim_rows
from synhalees.registry import MODELS_JSON, canonical_model_slug, default_registry

_UNKNOWN_TOKENS = frozenset({"", "other", "unknown"})

_STEP_PROGRESS_RE = re.compile(
    r"^\[(\d+)\s*/\s*(\d+)\]\s*(?:(downloading|skipping|importing|\[dry-run\])\s+)?(.+?)(?:\s+\[(PASS|fail)\])?$"
)
_EVAL_PROGRESS_RE = re.compile(r"^\[(\d+)\s*/\s*(\d+)\]\s+(\S+)\s+(PASS|fail)\b")
_FAILED_TASKS_RE = re.compile(r"(?:download|import)\s+failed\s+for\s+\d+\s+task\(s\):\s*(.+)$", re.IGNORECASE)

# Pillar titles mirrored from tools/build_leaderboard.py without importing it eagerly
_PILLAR_TITLES: tuple[tuple[str, str, str], ...] = (
    ("01_buddhist_culture", "Buddhist Culture", "බෞද්ධ සංස්කෘතිය"),
    ("02_pali_gatha", "Pali Gatha", "පාලි ගාථා"),
    ("03_classical_literature", "Classical Literature", "සම්භාව්‍ය සාහිත්‍යය"),
    ("04_kavi_sindu", "Kavi & Sindu", "කවි හා සින්දු"),
    ("05_sinhala_grammar", "Sinhala Grammar", "සිංහල ව්‍යාකරණය"),
    ("06_daily_spoken", "Daily Spoken", "එදිනෙදා ව්‍යවහාරය"),
    ("07_figurative_sinhala", "Figurative Sinhala", "උපමා හා ප්‍රස්තාව පිරුළු"),
    ("08_profanity_nuance", "Profanity Nuance", "අසභ්‍ය හා උපහාස"),
    ("09_singlish_sms", "Singlish & SMS", "සිංග්ලිෂ් සහ SMS"),
    ("10_regional_dialects", "Regional Dialects", "ප්‍රාදේශීය ව්‍යවහාර"),
    ("11_astrology_beliefs", "Astrology & Beliefs", "ජ්‍යෝතිෂ්‍ය හා විශ්වාස"),
    ("12_general_knowledge", "General Knowledge", "සාමාන්‍ය දැනුම"),
    ("13_sri_lanka_law", "Sri Lanka Law", "ශ්‍රී ලංකා නීතිය"),
    ("14_culinary_kitchen", "Culinary & Kitchen", "ඉවුම් පිහුම්"),
    ("15_numbers_maths", "Numbers & Maths", "ගණිතය හා ඉලක්කම්"),
)
_PILLAR_META = {slug: (en, si) for slug, en, si in _PILLAR_TITLES}


# ---------------------------------------------------------------------------
# 1. Workflow progress & pre-flight scanning helpers
# ---------------------------------------------------------------------------


def parse_progress_line(line: str) -> tuple[int, int, float, str] | None:
    """Extract ``(done, total, percent, status_text)`` from CLI / Colab stdout lines."""
    clean = line.strip()
    if not clean:
        return None
    m = _STEP_PROGRESS_RE.match(clean)
    if m:
        done, total = int(m.group(1)), max(1, int(m.group(2)))
        pct = min(100.0, max(0.0, (done / total) * 100.0))
        return done, total, pct, clean
    m_any = re.search(r"\[(\d+)\s*/\s*(\d+)\]", clean)
    if m_any:
        done, total = int(m_any.group(1)), max(1, int(m_any.group(2)))
        pct = min(100.0, max(0.0, (done / total) * 100.0))
        return done, total, pct, clean
    if clean.startswith("Bundled ") and "local files" in clean:
        return 1, 5, 20.0, f"Colab [1/5] {clean}"
    if "(colab) new" in clean or clean.endswith("colab new"):
        return 2, 5, 35.0, "Colab [2/5] Provisioning Colab session..."
    if "(colab) upload" in clean or "synhalees_bundle.tar.gz" in clean:
        return 3, 5, 50.0, "Colab [3/5] Uploading bundle to Colab..."
    if "(colab) download" in clean or "synhalees_slim_out.tar.gz" in clean:
        return 4, 5, 90.0, "Colab [4/5] Downloading *.slim.csv archive..."
    if clean.startswith("[OK] Synced ") and "slim CSV" in clean:
        return 5, 5, 100.0, f"Colab [5/5] {clean}"
    return None


def parse_eval_item_outcome(line: str) -> tuple[int, int, bool] | None:
    """Return ``(done, total, passed)`` for local runner lines like ``[12/225] ... PASS``."""
    m = _EVAL_PROGRESS_RE.match(line.strip())
    if not m:
        return None
    return int(m.group(1)), max(1, int(m.group(2))), m.group(4) == "PASS"


def extract_failed_tasks(line: str) -> list[str]:
    """Parse task names from ``download failed for N task(s): t1, t2`` lines."""
    m = _FAILED_TASKS_RE.search(line.strip())
    if not m:
        return []
    return [t.strip() for t in m.group(1).split(",") if t.strip()]


# ---------------------------------------------------------------------------
# 1b. Endpoint connection probe (GUI "Test Connection" button)
# ---------------------------------------------------------------------------

_PROVIDER_BASE_URLS = {
    "openai": "https://api.openai.com/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "ollama": "http://localhost:11434/v1",
}
_PROVIDER_KEY_ENVS = {
    "openai": ("OPENAI_API_KEY",),
    "openrouter": ("OPENROUTER_API_KEY",),
    "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    "anthropic": ("ANTHROPIC_API_KEY",),
}
_AUTH_HINT = "Check the API key (--api-key / SYNHALEES_API_KEY) and that the server is still running."


@dataclass
class EndpointProbe:
    """Outcome of :func:`probe_endpoint` (shown in the GUI log + status label)."""

    ok: bool
    target: str
    detail: str
    latency_ms: int = 0
    status: int | None = None
    models: list[str] = field(default_factory=list)
    hint: str = ""

    def summary(self) -> str:
        head = f"{self.target} ({self.latency_ms} ms)" if self.ok else f"{self.target} unreachable"
        if self.status:
            head += f" [HTTP {self.status}]"
        return f"{head} — {self.detail}"


def _env_api_key(provider: str) -> str:
    import os

    for var in _PROVIDER_KEY_ENVS.get(provider, ()):
        value = os.environ.get(var, "").strip()
        if value:
            return value
    return ""


def _http_json(
    url: str,
    headers: dict[str, str],
    *,
    timeout: float,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
) -> tuple[int | None, Any, str]:
    """Return ``(status, json_payload, error_text)`` without raising."""
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Content-Type": "application/json", **headers},
    )
    try:
        with open_url(request, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return int(resp.status), (json.loads(raw) if raw.strip() else {}), ""
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace").strip()[:200]
        return exc.code, None, body or f"HTTP {exc.code}"
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        return None, None, str(reason)[:200]
    except ValueError as exc:  # malformed JSON
        return None, None, f"invalid JSON response: {exc}"[:200]


def _openai_models(base: str, headers: dict[str, str], timeout: float) -> tuple[int | None, list[str], str]:
    status, payload, err = _http_json(f"{base}/models", headers, timeout=timeout)
    models: list[str] = []
    if status == 200 and isinstance(payload, dict):
        for entry in payload.get("data") or []:
            if isinstance(entry, dict) and entry.get("id"):
                models.append(str(entry["id"]))
    return status, models, err


def _chat_probe(base: str, headers: dict[str, str], timeout: float) -> tuple[int | None, str]:
    status, _body, err = _http_json(
        f"{base}/chat/completions", headers, timeout=timeout, method="POST", payload={}
    )
    return status, err


def _friendly_error(err: str) -> str:
    """Turn a socket/HTTP error into something actionable, keeping the raw cause."""
    raw = " ".join((err or "").split())
    low = raw.lower()
    friendly = ""
    if "getaddrinfo" in low or "no such host" in low or "nodename" in low or "name or service" in low:
        friendly = "DNS lookup failed — check the host name in the Base URL"
    elif "refused" in low or "10061" in low:
        friendly = "connection refused — server not running or wrong port"
    elif "timed out" in low or "timeout" in low:
        friendly = "timed out — server/tunnel did not answer"
    elif "certificate_verify_failed" in low or "unable to get local issuer certificate" in low:
        friendly = (
            "TLS certificate is not trusted — refresh the Python/OS CA certificates "
            "or check VPN/proxy TLS inspection"
        )
    elif "certificate" in low or "ssl" in low:
        friendly = "TLS error — check the https URL / tunnel certificate"
    elif "unreachable" in low:
        friendly = "network unreachable — check the tunnel URL and your connection"
    if not friendly:
        return raw
    # Keep the raw reason: the friendly line alone is not debuggable.
    return f"{friendly} · {raw[:120]}" if raw else friendly


def _probe_openai_compatible(
    base: str, key: str, timeout: float, started: float
) -> EndpointProbe:
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    status, models, err = _openai_models(base, headers, timeout)
    latency = int((time.perf_counter() - started) * 1000)
    if status == 200:
        detail = f"{len(models)} model(s) advertised" if models else "endpoint reachable"
        return EndpointProbe(True, f"{base}/models", detail, latency, status, models)
    if status in (401, 403):
        return EndpointProbe(
            False, f"{base}/models", "authentication rejected", latency, status, hint=_AUTH_HINT
        )

    # Validate that chat completions responds without selecting a model or
    # starting generation. HTTP 400/422 is expected for an empty request body.
    chat_status, chat_err = _chat_probe(base, headers, timeout)
    latency = int((time.perf_counter() - started) * 1000)
    if chat_status in (200, 400, 422):
        detail = (
            "endpoint reachable (model-free check; inference not tested)"
            if chat_status != 200
            else "chat endpoint answered without a model"
        )
        hint = "The endpoint is reachable. Select a model only when starting a benchmark run." if chat_status != 200 else ""
        return EndpointProbe(
            True,
            f"{base}/chat/completions",
            detail,
            latency,
            chat_status,
            hint=hint,
        )
    if chat_status in (401, 403):
        return EndpointProbe(
            False, f"{base}/chat/completions", "authentication rejected", latency, chat_status, hint=_AUTH_HINT
        )
    detail = _friendly_error(chat_err or err or "connection refused")
    if chat_status == 404:
        detail = "no /models or /chat/completions here — does the Base URL end with /v1?"
    elif status and status != 404:
        detail = f"/models -> HTTP {status}; chat -> {detail}"
    hint = ""
    if chat_status == 404:
        hint = f"Checked {base}/models and {base}/chat/completions. For vLLM/llama.cpp the base is usually http://host:port/v1."
    return EndpointProbe(
        False, f"{base}/chat/completions", detail[:220], latency, chat_status or status, hint=hint
    )


def probe_endpoint(
    *,
    provider: str = "",
    base_url: str = "",
    api_key: str = "",
    timeout: float = 8.0,
) -> EndpointProbe:
    """Check whether an LLM endpoint answers, without spending real tokens.

    OpenAI-compatible servers (Colab + vLLM, llama.cpp, LM Studio, Ollama,
    OpenRouter, OpenAI) are probed via ``GET /models`` and fall back to a
    model-free validation request. Gemini/Anthropic are probed through their
    native model-listing endpoints. Never raises.
    """
    provider = (provider or "").strip().lower()
    base = (base_url or "").strip().rstrip("/")
    key = (api_key or "").strip() or _env_api_key(provider)
    started = time.perf_counter()

    try:
        if base:
            return _probe_openai_compatible(base, key, timeout, started)
        if provider in _PROVIDER_BASE_URLS:
            return _probe_openai_compatible(_PROVIDER_BASE_URLS[provider], key, timeout, started)
        if provider == "gemini":
            if not key:
                return EndpointProbe(False, "gemini", "no GEMINI_API_KEY set", hint="Set GEMINI_API_KEY (free key: aistudio.google.com/apikey).")
            status, payload, err = _http_json(
                f"https://generativelanguage.googleapis.com/v1beta/models?key={urllib.parse.quote(key)}",
                {},
                timeout=timeout,
            )
            models = [
                str(m.get("name", "")).removeprefix("models/")
                for m in (payload or {}).get("models", [])
                if isinstance(m, dict)
            ] if isinstance(payload, dict) else []
            latency = int((time.perf_counter() - started) * 1000)
            if status == 200:
                return EndpointProbe(True, "generativelanguage.googleapis.com", f"{len(models)} model(s) listed", latency, status, models)
            return EndpointProbe(False, "generativelanguage.googleapis.com", (err or "request failed")[:220], latency, status, hint=_AUTH_HINT)
        if provider == "anthropic":
            if not key:
                return EndpointProbe(False, "api.anthropic.com", "no ANTHROPIC_API_KEY set", hint="Set ANTHROPIC_API_KEY.")
            status, payload, err = _http_json(
                "https://api.anthropic.com/v1/models",
                {"x-api-key": key, "anthropic-version": "2023-06-01"},
                timeout=timeout,
            )
            models = [
                str(m.get("id")) for m in (payload or {}).get("data", []) if isinstance(m, dict)
            ] if isinstance(payload, dict) else []
            latency = int((time.perf_counter() - started) * 1000)
            if status == 200:
                return EndpointProbe(True, "api.anthropic.com", f"{len(models)} model(s) listed", latency, status, models)
            return EndpointProbe(False, "api.anthropic.com", (err or "request failed")[:220], latency, status, hint=_AUTH_HINT)
        return EndpointProbe(
            False,
            "(no base URL)",
            "Set a Base URL (e.g. https://xxxx.trycloudflare.com/v1) or pick a known provider.",
        )
    except Exception as exc:  # a probe must never break the GUI loop
        return EndpointProbe(False, base or provider or "endpoint", f"probe failed: {exc}"[:220])


def find_unregistered_slim_models(
    task_scope: str = "all",
    *,
    slim_dir: Path | None = None,
) -> list[str]:
    """Scan ``kaggle/results/*.slim.csv`` for canonical model slugs missing registration."""
    root = slim_dir or KG_RESULTS
    if not root.is_dir():
        return []
    scope = (task_scope or "all").strip()
    if scope in ("", "all", "text", "vision", "audio", "vision|audio"):
        paths = sorted(root.glob("*.slim.csv"))
    else:
        tname = kaggle_ops.resolve_task_name(scope)
        paths = [root / f"{tname}.slim.csv"]

    seen: list[str] = []
    for p in paths:
        if not p.is_file():
            continue
        for model_slug, _pillar, _mod in load_slim_rows(p):
            canon = canonical_model_slug(model_slug)
            if canon and canon not in seen:
                seen.append(canon)

    unreg: list[str] = []
    for canon in seen:
        v, f, _ = default_registry.resolve_taxonomy(canon)
        if canon not in default_registry.display_names or v in _UNKNOWN_TOKENS or f in _UNKNOWN_TOKENS:
            unreg.append(canon)
    return unreg


def collect_local_runs_summary(runs_dir: Path | None = None) -> list[dict[str, Any]]:
    """Collect status, item counts, errors, and overall scores for folders in ``runs/``."""
    base = runs_dir or RUNS
    if not base.is_dir():
        return []
    rows: list[dict[str, Any]] = []
    for d in sorted(base.iterdir()):
        if not d.is_dir() or d.name.startswith("."):
            continue
        meta_path = d / "meta.json"
        sub_path = d / "submission.csv"
        ckpt_path = d / "checkpoint.jsonl"
        meta: dict[str, Any] = {}
        if meta_path.is_file():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:
                meta = {}
        ckpt_n = 0
        if ckpt_path.is_file():
            try:
                with ckpt_path.open("r", encoding="utf-8", errors="replace") as fh:
                    ckpt_n = sum(1 for ln in fh if ln.strip())
            except Exception:
                ckpt_n = 0
        items_n = meta.get("items") if meta else ckpt_n
        errors_n = meta.get("errors", 0)
        overall = meta.get("overall")
        if overall is None and sub_path.is_file():
            scores = _read_mean_submission_score(sub_path)
            overall = scores
        rows.append({
            "slug": d.name,
            "spec": str(meta.get("spec") or ""),
            "items": int(items_n or 0),
            "errors": int(errors_n or 0),
            "overall": f"{float(overall):.1f}%" if overall is not None else "—",
            "has_csv": sub_path.is_file(),
            "status": "Ready (CSV)" if sub_path.is_file() else "Partial (Resume)",
        })
    return rows


def _read_mean_submission_score(csv_path: Path) -> float | None:
    try:
        with csv_path.open("r", encoding="utf-8", errors="replace", newline="") as fh:
            vals = []
            for row in csv.DictReader(fh):
                try:
                    vals.append(float(row["score"]) * 100.0)
                except (KeyError, TypeError, ValueError):
                    pass
            return (sum(vals) / len(vals)) if vals else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 2. Pure-stdlib 24x24 SVG path rasterizer & monogram grid generator
# ---------------------------------------------------------------------------

_PATH_TOKEN_RE = re.compile(r"[A-Za-z]|[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?")

_GLYPH_5X7: dict[str, tuple[str, ...]] = {
    "A": ("..#..", ".#.#.", "#...#", "#####", "#...#", "#...#", "#...#"),
    "B": ("####.", "#...#", "#...#", "####.", "#...#", "#...#", "####."),
    "C": (".####", "#....", "#....", "#....", "#....", "#....", ".####"),
    "D": ("####.", "#...#", "#...#", "#...#", "#...#", "#...#", "####."),
    "E": ("#####", "#....", "#....", "####.", "#....", "#....", "#####"),
    "F": ("#####", "#....", "#....", "####.", "#....", "#....", "#...."),
    "G": (".####", "#....", "#....", "#..##", "#...#", "#...#", ".####"),
    "H": ("#...#", "#...#", "#...#", "#####", "#...#", "#...#", "#...#"),
    "I": (".###.", "..#..", "..#..", "..#..", "..#..", "..#..", ".###."),
    "J": ("..###", "...#.", "...#.", "...#.", "#..#.", "#..#.", ".##.."),
    "K": ("#...#", "#..#.", "#.#..", "##...", "#.#..", "#..#.", "#...#"),
    "L": ("#....", "#....", "#....", "#....", "#....", "#....", "#####"),
    "M": ("#...#", "##.##", "#.#.#", "#...#", "#...#", "#...#", "#...#"),
    "N": ("#...#", "##..#", "#.#.#", "#..##", "#...#", "#...#", "#...#"),
    "O": (".###.", "#...#", "#...#", "#...#", "#...#", "#...#", ".###."),
    "P": ("####.", "#...#", "#...#", "####.", "#....", "#....", "#...."),
    "Q": (".###.", "#...#", "#...#", "#...#", "#.#.#", "#..#.", ".##.#"),
    "R": ("####.", "#...#", "#...#", "####.", "#.#..", "#..#.", "#...#"),
    "S": (".####", "#....", "#....", ".###.", "....#", "....#", "####."),
    "T": ("#####", "..#..", "..#..", "..#..", "..#..", "..#..", "..#.."),
    "U": ("#...#", "#...#", "#...#", "#...#", "#...#", "#...#", ".###."),
    "V": ("#...#", "#...#", "#...#", "#...#", "#...#", ".#.#.", "..#.."),
    "W": ("#...#", "#...#", "#...#", "#.#.#", "#.#.#", "##.##", "#...#"),
    "X": ("#...#", "#...#", ".#.#.", "..#..", ".#.#.", "#...#", "#...#"),
    "Y": ("#...#", "#...#", ".#.#.", "..#..", "..#..", "..#..", "..#.."),
    "Z": ("#####", "....#", "...#.", "..#..", ".#...", "#....", "#####"),
    "0": (".###.", "#..##", "#.#.#", "#.#.#", "##..#", "#...#", ".###."),
    "1": ("..#..", ".##..", "..#..", "..#..", "..#..", "..#..", ".###."),
    "2": (".###.", "#...#", "....#", "..##.", ".#...", "#....", "#####"),
    "3": ("####.", "....#", "....#", ".###.", "....#", "....#", "####."),
    "4": ("#...#", "#...#", "#...#", "#####", "....#", "....#", "....#"),
    "5": ("#####", "#....", "####.", "....#", "....#", "#...#", ".###."),
    "6": (".###.", "#....", "####.", "#...#", "#...#", "#...#", ".###."),
    "7": ("#####", "....#", "...#.", "..#..", ".#...", ".#...", ".#..."),
    "8": (".###.", "#...#", "#...#", ".###.", "#...#", "#...#", ".###."),
    "9": (".###.", "#...#", "#...#", ".####", "....#", "...#.", ".##.."),
}


def _hex_to_rgb(hex_str: str) -> tuple[int, int, int]:
    s = hex_str.strip().lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    if len(s) != 6:
        return 120, 144, 156
    return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)


def _blend_hex(fg_hex: str, bg_hex: str, alpha: float) -> str:
    fr, fg, fb = _hex_to_rgb(fg_hex)
    br, bg, bb = _hex_to_rgb(bg_hex)
    a = max(0.0, min(1.0, alpha))
    r = round(fr * a + br * (1.0 - a))
    g = round(fg * a + bg * (1.0 - a))
    b = round(fb * a + bb * (1.0 - a))
    return f"#{r:02x}{g:02x}{b:02x}"


def _arc_to_points(
    x0: float,
    y0: float,
    rx: float,
    ry: float,
    phi_deg: float,
    large_arc: int,
    sweep: int,
    x1: float,
    y1: float,
    steps: int = 8,
) -> list[tuple[float, float]]:
    if (x0 == x1 and y0 == y1) or rx == 0 or ry == 0:
        return [(x1, y1)]
    rx, ry = abs(rx), abs(ry)
    phi = math.radians(phi_deg % 360.0)
    cos_p, sin_p = math.cos(phi), math.sin(phi)
    dx2, dy2 = (x0 - x1) / 2.0, (y0 - y1) / 2.0
    x1p = cos_p * dx2 + sin_p * dy2
    y1p = -sin_p * dx2 + cos_p * dy2
    lam = (x1p * x1p) / (rx * rx) + (y1p * y1p) / (ry * ry)
    if lam > 1.0:
        s = math.sqrt(lam)
        rx *= s
        ry *= s
    num = max(0.0, (rx * ry) ** 2 - (rx * y1p) ** 2 - (ry * x1p) ** 2)
    den = (rx * y1p) ** 2 + (ry * x1p) ** 2
    factor = math.sqrt(num / den) if den > 1e-12 else 0.0
    if large_arc == sweep:
        factor = -factor
    cxp = factor * (rx * y1p) / ry
    cyp = factor * (-ry * x1p) / rx
    cx = cos_p * cxp - sin_p * cyp + (x0 + x1) / 2.0
    cy = sin_p * cxp + cos_p * cyp + (y0 + y1) / 2.0

    def _ang(ux: float, uy: float, vx: float, vy: float) -> float:
        dot = ux * vx + uy * vy
        len_ = math.hypot(ux, uy) * math.hypot(vx, vy)
        ang = math.acos(max(-1.0, min(1.0, dot / len_))) if len_ > 1e-12 else 0.0
        return -ang if (ux * vy - uy * vx) < 0 else ang

    t1 = _ang(1.0, 0.0, (x1p - cxp) / rx, (y1p - cyp) / ry)
    dt = _ang((x1p - cxp) / rx, (y1p - cyp) / ry, (-x1p - cxp) / rx, (-y1p - cyp) / ry)
    if not sweep and dt > 0:
        dt -= 2 * math.pi
    elif sweep and dt < 0:
        dt += 2 * math.pi
    pts: list[tuple[float, float]] = []
    for k in range(1, steps + 1):
        ang = t1 + dt * (k / steps)
        px = cx + cos_p * rx * math.cos(ang) - sin_p * ry * math.sin(ang)
        py = cy + sin_p * rx * math.cos(ang) + cos_p * ry * math.sin(ang)
        pts.append((px, py))
    return pts


def _parse_d_to_polygons(d_str: str) -> list[list[tuple[float, float]]]:
    """Flatten an SVG ``path d=...`` string into closed polygon point lists."""
    tokens = _PATH_TOKEN_RE.findall(d_str)
    i = 0
    cmd = ""
    cx = cy = sx = sy = 0.0
    prev_ctrl: tuple[float, float] | None = None
    prev_qctrl: tuple[float, float] | None = None
    polys: list[list[tuple[float, float]]] = []
    cur: list[tuple[float, float]] = []

    def _next_num() -> float:
        nonlocal i
        v = float(tokens[i])
        i += 1
        return v

    def _has_num() -> bool:
        return i < len(tokens) and not tokens[i].isalpha()

    while i < len(tokens):
        tok = tokens[i]
        if tok.isalpha():
            cmd = tok
            i += 1
            if cmd in ("Z", "z"):
                if cur:
                    cur.append((sx, sy))
                    polys.append(cur)
                    cur = []
                cx, cy = sx, sy
                prev_ctrl = prev_qctrl = None
                continue
        elif not cmd:
            i += 1
            continue

        rel = cmd.islower()
        uc = cmd.upper()
        if uc == "M":
            x, y = _next_num(), _next_num()
            if rel:
                x += cx
                y += cy
            if cur:
                polys.append(cur)
            cur = [(x, y)]
            cx = sx = x
            cy = sy = y
            cmd = "l" if rel else "L"
            prev_ctrl = prev_qctrl = None
        elif uc == "L":
            while _has_num():
                x, y = _next_num(), _next_num()
                if rel:
                    x += cx
                    y += cy
                cur.append((x, y))
                cx, cy = x, y
            prev_ctrl = prev_qctrl = None
        elif uc == "H":
            while _has_num():
                x = _next_num() + (cx if rel else 0.0)
                cur.append((x, cy))
                cx = x
            prev_ctrl = prev_qctrl = None
        elif uc == "V":
            while _has_num():
                y = _next_num() + (cy if rel else 0.0)
                cur.append((cx, y))
                cy = y
            prev_ctrl = prev_qctrl = None
        elif uc in ("C", "S"):
            while _has_num():
                if uc == "C":
                    x1, y1 = _next_num(), _next_num()
                    if rel:
                        x1 += cx
                        y1 += cy
                else:
                    if prev_ctrl is not None:
                        x1, y1 = 2 * cx - prev_ctrl[0], 2 * cy - prev_ctrl[1]
                    else:
                        x1, y1 = cx, cy
                x2, y2 = _next_num(), _next_num()
                x3, y3 = _next_num(), _next_num()
                if rel:
                    x2 += cx
                    y2 += cy
                    x3 += cx
                    y3 += cy
                for k in range(1, 7):
                    t = k / 6.0
                    u = 1.0 - t
                    px = u**3 * cx + 3 * u**2 * t * x1 + 3 * u * t**2 * x2 + t**3 * x3
                    py = u**3 * cy + 3 * u**2 * t * y1 + 3 * u * t**2 * y2 + t**3 * y3
                    cur.append((px, py))
                prev_ctrl = (x2, y2)
                prev_qctrl = None
                cx, cy = x3, y3
        elif uc in ("Q", "T"):
            while _has_num():
                if uc == "Q":
                    x1, y1 = _next_num(), _next_num()
                    if rel:
                        x1 += cx
                        y1 += cy
                else:
                    if prev_qctrl is not None:
                        x1, y1 = 2 * cx - prev_qctrl[0], 2 * cy - prev_qctrl[1]
                    else:
                        x1, y1 = cx, cy
                x2, y2 = _next_num(), _next_num()
                if rel:
                    x2 += cx
                    y2 += cy
                for k in range(1, 6):
                    t = k / 5.0
                    u = 1.0 - t
                    px = u**2 * cx + 2 * u * t * x1 + t**2 * x2
                    py = u**2 * cy + 2 * u * t * y1 + t**2 * y2
                    cur.append((px, py))
                prev_qctrl = (x1, y1)
                prev_ctrl = None
                cx, cy = x2, y2
        elif uc == "A":
            while _has_num():
                rx, ry = _next_num(), _next_num()
                rot = _next_num()
                large = int(round(_next_num()))
                sweep = int(round(_next_num()))
                x, y = _next_num(), _next_num()
                if rel:
                    x += cx
                    y += cy
                cur.extend(_arc_to_points(cx, cy, rx, ry, rot, large, sweep, x, y, steps=7))
                cx, cy = x, y
            prev_ctrl = prev_qctrl = None
        else:
            break
    if cur:
        polys.append(cur)
    return [p for p in polys if len(p) >= 3]


def _point_in_even_odd(px: float, py: float, polys: list[list[tuple[float, float]]]) -> bool:
    inside = False
    for poly in polys:
        n = len(poly)
        j = n - 1
        for k in range(n):
            xk, yk = poly[k]
            xj, yj = poly[j]
            if (yk > py) != (yj > py):
                x_int = (xj - xk) * (py - yk) / ((yj - yk) or 1e-12) + xk
                if px < x_int:
                    inside = not inside
            j = k
    return inside


def rasterize_svg_to_grid(
    svg_text: str,
    size: int = 16,
    *,
    fg_fallback: str = "#ffffff",
    bg_light: str = "#f8fafc",
    bg_dark: str = "#1e293b",
) -> list[list[str]] | None:
    """Rasterize a simple SVG (single-path or multi-path) into a ``size x size`` hex-color grid.

    Returns ``None`` if the SVG uses unsupported image wrappers or has no rasterizable ``<path>``.
    """
    if not svg_text or "<image" in svg_text.lower():
        return None
    vb_m = re.search(r'viewBox=["\']\s*([-.\d]+)\s+([-.\d]+)\s+([-.\d]+)\s+([-.\d]+)', svg_text)
    if vb_m:
        vx, vy, vw, vh = (float(vb_m.group(k)) for k in range(1, 5))
    else:
        vx, vy, vw, vh = 0.0, 0.0, 24.0, 24.0
    if vw <= 0 or vh <= 0 or vw > 512 or vh > 512:
        return None

    svg_fill_m = re.search(r'<svg\b[^>]*\bfill=["\'](#[0-9a-fA-F]{3,6})["\']', svg_text)
    default_fill = svg_fill_m.group(1) if svg_fill_m else fg_fallback

    path_tags = re.findall(r"<path\b[^>]*>", svg_text, flags=re.IGNORECASE)
    if not path_tags or len(path_tags) > 24:
        return None

    groups: list[tuple[list[list[tuple[float, float]]], str]] = []
    for tag in path_tags:
        d_m = re.search(r'\bd=["\']([^"\']+)["\']', tag)
        if not d_m:
            continue
        fill_m = re.search(r'\bfill=["\']([^"\']+)["\']', tag)
        fill_col = fill_m.group(1) if fill_m else default_fill
        if fill_col.lower() == "none":
            continue
        polys = _parse_d_to_polygons(d_m.group(1))
        if polys:
            groups.append((polys, fill_col if fill_col.startswith("#") else default_fill))

    if not groups:
        return None

    # Choose contrasting background tile for first foreground color
    r, g, b = _hex_to_rgb(groups[0][1])
    lum = 0.299 * r + 0.587 * g + 0.114 * b
    bg_hex = bg_dark if lum > 175 else bg_light

    pad = 1.0
    span = max(1.0, size - 2 * pad)
    grid: list[list[str]] = []
    sub_offsets = (0.25, 0.75)
    for py_i in range(size):
        row_colors: list[str] = []
        for px_i in range(size):
            # Evaluate each path group (2x2 supersampling)
            cell_col = bg_hex
            for polys, fill_hex in groups:
                hits = 0
                for oy in sub_offsets:
                    sy = vy + ((py_i + oy - pad) / span) * vh
                    for ox in sub_offsets:
                        sx = vx + ((px_i + ox - pad) / span) * vw
                        if _point_in_even_odd(sx, sy, polys):
                            hits += 1
                if hits:
                    cell_col = _blend_hex(fill_hex, cell_col, hits / 4.0)
            row_colors.append(cell_col)
        grid.append(row_colors)
    return grid


def render_monogram_grid(letter: str, bg_hex: str, size: int = 16) -> list[list[str]]:
    """Build a crisp ``size x size`` colored badge with a centered 5x7 white monogram glyph."""
    ch = (letter.strip()[:1] or "?").upper()
    glyph = _GLYPH_5X7.get(ch)
    ox = (size - 5) // 2
    oy = (size - 7) // 2
    border = "#334155"
    grid: list[list[str]] = []
    for y in range(size):
        row: list[str] = []
        for x in range(size):
            if x in (0, size - 1) or y in (0, size - 1):
                row.append(border)
            elif glyph and 0 <= (y - oy) < 7 and 0 <= (x - ox) < 5 and glyph[y - oy][x - ox] == "#":
                row.append("#ffffff")
            else:
                row.append(bg_hex)
        grid.append(row)
    return grid


def load_svg_sources_map(root_dir: Path | None = None) -> dict[str, str]:
    """Load inline SVG strings from ``docs/assets/logos/*.svg`` and ``docs/assets/logo-data.js``."""
    base = root_dir or ROOT
    svgs: dict[str, str] = {}
    logos_dir = base / "docs" / "assets" / "logos"
    if logos_dir.is_dir():
        for p in sorted(logos_dir.glob("*.svg")):
            try:
                svgs[p.stem] = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                pass
    js_path = base / "docs" / "assets" / "logo-data.js"
    if js_path.is_file():
        text = js_path.read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(r'"([^"]+)":\s*"data:image/svg\+xml,([^"]+)"', text):
            if m.group(1) not in svgs:
                svgs[m.group(1)] = urllib.parse.unquote(m.group(2))
    return svgs


# ---------------------------------------------------------------------------
# 3. Organization SVG Logo Installer & JS Map Sync
# ---------------------------------------------------------------------------


def sync_js_provider_logos(
    org_slug: str,
    logo_slug: str,
    *,
    aliases: list[str] | None = None,
    color_hex: str = "",
    root_dir: Path | None = None,
) -> list[Path]:
    """Ensure ``org_slug`` (and aliases) map to ``logo_slug`` in ``chart.js`` & ``app.js``."""
    base = root_dir or ROOT
    keys = []
    for raw in [org_slug, *(aliases or [])]:
        k = re.sub(r"[^a-z0-9]", "", raw.lower())
        if k and k not in keys:
            keys.append(k)
    if not keys:
        return []

    updated: list[Path] = []
    for rel in ("docs/assets/chart.js", "docs/assets/app.js"):
        p = base / rel
        if not p.is_file():
            continue
        raw_bytes = p.read_bytes()
        eol = "\r\n" if b"\r\n" in raw_bytes else "\n"
        text = raw_bytes.decode("utf-8")
        changed = False

        # Update var PROVIDER_LOGOS = { ... };
        text, ch_logo = _insert_js_map_entries(text, "PROVIDER_LOGOS", {k: logo_slug for k in keys}, eol)
        changed = changed or ch_logo

        if rel.endswith("chart.js") and color_hex:
            text, ch_col = _insert_js_map_entries(text, "PROVIDER_COLORS", {k: color_hex for k in keys}, eol)
            changed = changed or ch_col

        if changed:
            p.write_bytes(text.encode("utf-8"))
            updated.append(p)
    return updated


def _insert_js_map_entries(text: str, var_name: str, entries: dict[str, str], eol: str) -> tuple[str, bool]:
    pattern = re.compile(rf"(var\s+{re.escape(var_name)}\s*=\s*\{{)(.*?)(\n?\s*\}};)", re.DOTALL)
    m = pattern.search(text)
    if not m:
        return text, False
    body = m.group(2)
    existing_keys = set(re.findall(r"^\s*([A-Za-z0-9_]+)\s*:", body, flags=re.MULTILINE))
    missing = [(k, v) for k, v in entries.items() if k not in existing_keys]
    if not missing:
        return text, False
    lines = body.rstrip()
    if lines and not lines.endswith(","):
        lines += ","
    for k, v in missing:
        lines += f'{eol}    {k}: "{v}",'
    new_text = text[: m.start(2)] + lines + m.group(3) + text[m.end(3) :]
    return new_text, True


def install_organization_logo(
    logo_slug: str,
    svg_source_path: Path | None = None,
    *,
    org_slug: str = "",
    aliases: list[str] | None = None,
    color_hex: str = "",
    root_dir: Path | None = None,
    rebuild_logo_data: bool = True,
) -> dict[str, Any]:
    """Validate/copy an SVG logo into ``docs/assets/logos/<logo_slug>.svg``, sync JS maps & rebuild."""
    base = root_dir or ROOT
    clean_logo = logo_slug.strip()
    if not clean_logo:
        raise ValueError("Logo slug is required.")
    logos_dir = base / "docs" / "assets" / "logos"
    logos_dir.mkdir(parents=True, exist_ok=True)
    dst = logos_dir / f"{clean_logo}.svg"

    if svg_source_path is not None:
        src = Path(svg_source_path)
        if not src.is_file():
            raise ValueError(f"SVG file not found: {src}")
        if src.suffix.lower() != ".svg":
            raise ValueError("Selected logo file must have a .svg extension.")
        content = src.read_text(encoding="utf-8", errors="replace")
        if "<svg" not in content.lower():
            raise ValueError("Selected file does not appear to be a valid SVG document.")
        if src.resolve() != dst.resolve():
            shutil.copyfile(src, dst)

    updated_js: list[Path] = []
    if org_slug.strip():
        updated_js = sync_js_provider_logos(
            org_slug.strip(),
            clean_logo,
            aliases=aliases,
            color_hex=color_hex,
            root_dir=base,
        )

    rebuilt = False
    if rebuild_logo_data and base.resolve() == ROOT.resolve():
        tool_path = ROOT / "tools" / "build_logo_data.py"
        spec = importlib.util.spec_from_file_location("synhalees_build_logo_data", tool_path)
        if spec and spec.loader:
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            rc = int(mod.main(["--out", str(ROOT / "docs" / "assets" / "logo-data.js")]))
            rebuilt = rc == 0

    return {"svg_path": dst, "updated_js": updated_js, "logo_data_rebuilt": rebuilt}


# ---------------------------------------------------------------------------
# 4. Model Scorecard Inspector & Comparison
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ModelScorecardSummary:
    canonical: str
    display_name: str
    provider: str
    family: str
    open_source: bool
    source_kind: str
    source_path: str
    date: str
    rank_text: int | None
    total_ranked: int
    modalities: dict[str, float | None]
    usage: dict[str, dict[str, float | int | None]]
    pillar_scores: dict[str, dict[str, float]]


def _aggregate_scorecard_rows(rows: list[dict[str, str]]) -> tuple[str, dict[str, float | None], dict[str, dict[str, float | int | None]], dict[str, dict[str, float]]]:
    latest_date = ""
    mod_buckets: dict[str, list[float]] = {"text": [], "vision": [], "audio": []}
    cost_sum: dict[str, float] = {"text": 0.0, "vision": 0.0, "audio": 0.0}
    tok_sum: dict[str, int] = {"text": 0, "vision": 0, "audio": 0}
    lat_buckets: dict[str, list[float]] = {"text": [], "vision": [], "audio": []}
    pillar_scores: dict[str, dict[str, float]] = {}

    for r in rows:
        dt = str(r.get("date") or "").strip()
        if dt > latest_date:
            latest_date = dt
        pillar = str(r.get("pillar") or "").strip()
        mod = str(r.get("modality") or "text").strip().lower()
        try:
            score_pct = float(r.get("score") or 0.0) * 100.0
        except ValueError:
            continue
        if mod in mod_buckets:
            mod_buckets[mod].append(score_pct)
        if pillar:
            p_map = pillar_scores.setdefault(pillar, {})
            p_map[mod] = score_pct
            try:
                c = float(r.get("cost_usd") or 0.0)
                if mod in cost_sum:
                    cost_sum[mod] += c
                p_map[f"{mod}_cost"] = p_map.get(f"{mod}_cost", 0.0) + c
            except ValueError:
                pass
            try:
                t = int(float(r.get("tokens") or 0))
                if mod in tok_sum:
                    tok_sum[mod] += t
                p_map[f"{mod}_tokens"] = p_map.get(f"{mod}_tokens", 0.0) + t
            except ValueError:
                pass
            try:
                lat = float(r.get("latency_ms") or 0.0)
                if lat > 0 and mod in lat_buckets:
                    lat_buckets[mod].append(lat)
                if lat > 0:
                    p_map[f"{mod}_latency"] = lat
            except ValueError:
                pass

    modalities: dict[str, float | None] = {
        m: (round(sum(vals) / len(vals), 2) if vals else None) for m, vals in mod_buckets.items()
    }
    usage: dict[str, dict[str, float | int | None]] = {}
    for m in ("text", "vision", "audio"):
        has = bool(mod_buckets[m])
        usage[m] = {
            "cost": round(cost_sum[m], 4) if has else None,
            "tokens": tok_sum[m] if has else None,
            "latency": round(sum(lat_buckets[m]) / len(lat_buckets[m])) if lat_buckets[m] else None,
        }
    return latest_date, modalities, usage, pillar_scores


def load_model_scorecard_summary(
    canonical_slug: str,
    *,
    root_dir: Path | None = None,
) -> ModelScorecardSummary:
    """Load a model's scorecard summary from ``submissions/``, ``runs/``, or ``kaggle/results/``."""
    base = root_dir or ROOT
    canon = canonical_model_slug(canonical_slug.strip())
    prov, fam, is_oss = default_registry.resolve_taxonomy(canon)
    dname = default_registry.get_model_display_name(canon)

    sub_dir = base / "submissions"
    runs_dir = base / "runs"
    kg_dir = base / "kaggle" / "results"

    # Rank across all submissions by mean text score
    rank_scores: list[tuple[str, float]] = []
    matched_sub: Path | None = None
    if sub_dir.is_dir():
        for csv_p in sorted(sub_dir.rglob("*.csv")):
            s_canon = canonical_model_slug(csv_p.stem)
            if s_canon == canon and matched_sub is None:
                matched_sub = csv_p
            with csv_p.open("r", encoding="utf-8", errors="replace", newline="") as fh:
                text_vals = []
                for row in csv.DictReader(fh):
                    if str(row.get("modality") or "text").strip().lower() == "text":
                        try:
                            text_vals.append(float(row["score"]) * 100.0)
                        except (KeyError, ValueError, TypeError):
                            pass
                if text_vals:
                    rank_scores.append((s_canon, sum(text_vals) / len(text_vals)))
    rank_scores.sort(key=lambda item: item[1], reverse=True)
    rank_map = {slug: idx + 1 for idx, (slug, _) in enumerate(rank_scores)}

    raw_rows: list[dict[str, str]] = []
    source_kind = "unscored"
    source_path = ""
    if matched_sub is not None:
        source_kind = "published"
        source_path = str(matched_sub.relative_to(base)).replace("\\", "/")
        with matched_sub.open("r", encoding="utf-8", errors="replace", newline="") as fh:
            raw_rows = list(csv.DictReader(fh))
    elif (runs_dir / canon / "submission.csv").is_file():
        rp = runs_dir / canon / "submission.csv"
        source_kind = "local_run"
        source_path = str(rp.relative_to(base)).replace("\\", "/")
        with rp.open("r", encoding="utf-8", errors="replace", newline="") as fh:
            raw_rows = list(csv.DictReader(fh))
    elif kg_dir.is_dir():
        for slim_p in sorted(kg_dir.glob("*.slim.csv")):
            with slim_p.open("r", encoding="utf-8", errors="replace", newline="") as fh:
                for r in csv.DictReader(fh):
                    if canonical_model_slug(str(r.get("model") or "")) == canon:
                        raw_rows.append(r)
        if raw_rows:
            source_kind = "kaggle_slim"
            source_path = "kaggle/results/*.slim.csv"

    latest_date, modalities, usage, pillar_scores = _aggregate_scorecard_rows(raw_rows)
    return ModelScorecardSummary(
        canonical=canon,
        display_name=dname,
        provider=prov,
        family=fam,
        open_source=is_oss,
        source_kind=source_kind,
        source_path=source_path,
        date=latest_date or "—",
        rank_text=rank_map.get(canon),
        total_ranked=len(rank_scores),
        modalities=modalities,
        usage=usage,
        pillar_scores=pillar_scores,
    )


def compare_model_scorecards(
    slug_a: str,
    slug_b: str,
    *,
    modality: str = "text",
    root_dir: Path | None = None,
) -> dict[str, Any]:
    """Compute per-modality and 15-pillar comparison deltas (A - B) between two models."""
    a = load_model_scorecard_summary(slug_a, root_dir=root_dir)
    b = load_model_scorecard_summary(slug_b, root_dir=root_dir) if slug_b else None
    mod = modality.lower().strip() or "text"
    rows: list[dict[str, Any]] = []
    wins = losses = ties = 0
    for p_slug, en_title, si_title in _PILLAR_TITLES:
        pa = a.pillar_scores.get(p_slug, {})
        sa = pa.get(mod)
        sb = b.pillar_scores.get(p_slug, {}).get(mod) if b else None
        delta = (sa - sb) if (sa is not None and sb is not None) else None
        if delta is not None:
            if delta > 0.05:
                wins += 1
            elif delta < -0.05:
                losses += 1
            else:
                ties += 1
        rows.append({
            "pillar": p_slug,
            "title": en_title,
            "title_si": si_title,
            "score_a": sa,
            "score_b": sb,
            "delta": delta,
            "cost_a": pa.get(f"{mod}_cost"),
            "tokens_a": pa.get(f"{mod}_tokens"),
            "latency_a": pa.get(f"{mod}_latency"),
        })
    return {"a": a, "b": b, "modality": mod, "rows": rows, "wins": wins, "losses": losses, "ties": ties}


# ---------------------------------------------------------------------------
# 5. Read-Only 15-Pillar Dataset Browser
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PillarInspectionReport:
    slug: str
    title_en: str
    title_si: str
    text_count: int
    vision_count: int
    vision_files_present: int
    audio_count: int
    audio_files_present: int
    eval_type_counts: dict[str, int]
    rows: list[dict[str, Any]]


def inspect_pillar_dataset(pillar_slug: str) -> PillarInspectionReport:
    """Inspect a single pillar's Text/Vision/Audio items, eval types, and media presence."""
    pd = load_pillar(pillar_slug, strict=False)
    en_title, si_title = _PILLAR_META.get(pillar_slug, (pillar_slug, pillar_slug))
    eval_counts: dict[str, int] = {"exact_match": 0, "llm_judge": 0, "wer": 0, "classification": 0}
    rows: list[dict[str, Any]] = []

    for item in pd.text:
        eval_counts[item.eval_type] = eval_counts.get(item.eval_type, 0) + 1
        rows.append({
            "id": item.id,
            "modality": "text",
            "eval_type": item.eval_type,
            "media": "—",
            "media_path": None,
            "media_exists": True,
            "prompt": item.prompt,
            "ground_truth": item.ground_truth,
        })

    vf_present = 0
    for item in pd.vision:
        eval_counts[item.eval_type] = eval_counts.get(item.eval_type, 0) + 1
        exists = bool(item.image_path and item.image_path.is_file())
        if exists:
            vf_present += 1
        rows.append({
            "id": item.id,
            "modality": "vision",
            "eval_type": item.eval_type,
            "media": item.image_file,
            "media_path": item.image_path,
            "media_exists": exists,
            "prompt": item.question,
            "ground_truth": item.ground_truth,
        })

    af_present = 0
    for item in pd.audio:
        eval_counts[item.eval_type] = eval_counts.get(item.eval_type, 0) + 1
        exists = bool(item.audio_path and item.audio_path.is_file())
        if exists:
            af_present += 1
        rows.append({
            "id": item.id,
            "modality": "audio",
            "eval_type": item.eval_type,
            "media": item.audio_file,
            "media_path": item.audio_path,
            "media_exists": exists,
            "prompt": item.question,
            "ground_truth": item.ground_truth,
        })

    return PillarInspectionReport(
        slug=pd.slug,
        title_en=en_title,
        title_si=si_title,
        text_count=len(pd.text),
        vision_count=len(pd.vision),
        vision_files_present=vf_present,
        audio_count=len(pd.audio),
        audio_files_present=af_present,
        eval_type_counts=eval_counts,
        rows=rows,
    )


# ---------------------------------------------------------------------------
# 6. Family Taxonomy CRUD & Adopt/Sync Scorecards
# ---------------------------------------------------------------------------


def save_organization_family(
    org_slug: str,
    family_slug: str,
    *,
    name: str,
    open_source: bool = False,
    match_patterns: list[str] | None = None,
    models_json_path: Path | None = None,
) -> None:
    """Create or update a family under ``providers[org_slug]['families']`` in ``models.json``."""
    clean_org = org_slug.strip().lower()
    clean_fam = family_slug.strip().lower()
    if not clean_org or clean_org in _UNKNOWN_TOKENS:
        raise ValueError(f"Invalid organization slug: {org_slug!r}")
    if not clean_fam or clean_fam in _UNKNOWN_TOKENS:
        raise ValueError(f"Invalid family slug: {family_slug!r}")
    patterns = [p.strip().lower() for p in (match_patterns or []) if p.strip()]
    if clean_fam not in patterns:
        patterns.insert(0, clean_fam)

    path = models_json_path or MODELS_JSON
    data = json.loads(path.read_text(encoding="utf-8"))
    providers = dict(data.get("providers") or {})
    if clean_org not in providers:
        raise ValueError(f"Organization '{clean_org}' does not exist in models.json.")
    prov = dict(providers[clean_org])
    fams = dict(prov.get("families") or {})
    fams[clean_fam] = {
        "name": name.strip() or clean_fam.replace("-", " ").title(),
        "open_source": bool(open_source),
        "match_patterns": patterns,
    }
    prov["families"] = fams
    providers[clean_org] = prov
    data["providers"] = providers
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if path.resolve() == MODELS_JSON.resolve():
        default_registry.load()


def delete_organization_family(
    org_slug: str,
    family_slug: str,
    *,
    models_json_path: Path | None = None,
) -> bool:
    """Delete a family entry from ``providers[org_slug]['families']``."""
    clean_org = org_slug.strip().lower()
    clean_fam = family_slug.strip().lower()
    path = models_json_path or MODELS_JSON
    data = json.loads(path.read_text(encoding="utf-8"))
    providers = dict(data.get("providers") or {})
    if clean_org not in providers:
        return False
    prov = dict(providers[clean_org])
    fams = dict(prov.get("families") or {})
    if clean_fam not in fams:
        return False
    del fams[clean_fam]
    prov["families"] = fams
    providers[clean_org] = prov
    data["providers"] = providers
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if path.resolve() == MODELS_JSON.resolve():
        default_registry.load()
    return True


def adopt_and_sync_scorecards(submissions_dir: Path | None = None) -> dict[str, list[str]]:
    """Run ``adopt_legacy_scorecards()`` and move any CSVs whose vendor/family folder changed."""
    target = submissions_dir or (ROOT / "submissions")
    if not target.is_dir():
        return {"adopted": [], "relocated": []}

    before = {str(p.relative_to(target)) for p in target.rglob("*.csv")}
    default_registry.load()
    default_registry.adopt_legacy_scorecards(target)
    after = {str(p.relative_to(target)) for p in target.rglob("*.csv")}
    adopted = sorted(after - before)

    relocated: list[str] = []
    for csv_p in sorted(target.rglob("*.csv")):
        slug = canonical_model_slug(csv_p.stem)
        vendor, family, _ = default_registry.resolve_taxonomy(slug)
        if vendor in _UNKNOWN_TOKENS or family in _UNKNOWN_TOKENS:
            continue
        expected = target / vendor / family / f"{slug}.csv"
        if csv_p.resolve() == expected.resolve() or expected.exists():
            continue
        old_rel = str(csv_p.relative_to(target)).replace("\\", "/")
        expected.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(csv_p), str(expected))
        new_rel = str(expected.relative_to(target)).replace("\\", "/")
        relocated.append(f"{old_rel} -> {new_rel}")
        # Clean empty parent dirs inside target
        parent = csv_p.parent
        while parent != target and parent.is_dir():
            try:
                next(parent.iterdir())
                break
            except StopIteration:
                parent.rmdir()
                parent = parent.parent

    return {"adopted": adopted, "relocated": relocated}
