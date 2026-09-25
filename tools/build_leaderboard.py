#!/usr/bin/env python3
"""Build the static leaderboard data (docs/assets/data/leaderboard.json).

Sources, in priority order:

  1. Explicit submission CSVs - files produced by results.save_submission()
     (schema: model,provider,date,pillar,modality,score):

         python tools/build_leaderboard.py submissions/*.csv

  2. ``submissions/*.csv`` - when no path is given the committed
     submissions/ folder is aggregated. An empty folder is valid: it writes
     an EMPTY leaderboard (models: []), so a fresh clone never ships stale
     numbers.

         python tools/build_leaderboard.py

  3. Demo data - clearly-marked placeholder scores so the site is viewable
     before any real benchmark run exists:

         python tools/build_leaderboard.py --demo

``--check`` verifies the committed docs/assets/data files already match what
those sources generate (newline-insensitive, used by CI). It never writes
and exits 1 on drift.

All output is deterministic: ``updated`` is the newest ``date`` found in the
CSVs (never "today"), so CI stays green on later days.

Stdlib only, UTF-8 throughout (Sinhala pillar titles stay intact).
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "assets" / "data" / "leaderboard.json"
# Committed, one CSV per model: the published source of truth for the site.
SUBMISSIONS_DIR = ROOT / "submissions"
PILLARS = [
    ("01_buddhist_culture", "Buddhist Culture & Rituals", "බෞද්ධ සංස්කෘතිය සහ සිරිත්", "🛕"),
    ("02_pali_gatha", "Pali Language & Gatha", "පාලි භාෂාව සහ ගාථා", "📜"),
    ("03_classical_literature", "Classical Literature & Old Sinhala", "සම්භාව්‍ය සාහිත්‍යය සහ පුරාතන සිංහල", "🏛️"),
    ("04_kavi_sindu", "Kavi & Sindu — Poetry & Song", "ජන කවි, සම්භාව්‍ය කවි සහ සිංහල සිංදු", "🎶"),
    ("05_sinhala_grammar", "Sinhala Grammar & Writing", "සිංහල ව්‍යාකරණ ලේඛනය", "✍️"),
    ("06_daily_spoken", "Daily Spoken Sinhala", "දෛනික කථන සිංහල", "💬"),
    ("07_figurative_sinhala", "Sinhala Wordplay & Hidden Meanings", "ව්‍යංග්‍යාර්ථ, යටි අර්ථ සහ උපමා", "🎭"),
    ("08_profanity_nuance", "Profanity Nuance: Banter vs Abuse", "කුණුහරුප සහ අපහාස", "⚠️"),
    ("09_singlish_sms", "Singlish & Short Messaging", "සිංග්ලිෂ් සහ කෙටි පණිවිඩ", "📱"),
    ("10_regional_dialects", "Regional Dialects: Southern, Up-Country, Rajarata", "ප්‍රාදේශීය ව්‍යවහාර", "🗺️"),
    ("11_astrology_beliefs", "Astrology & Folk Beliefs", "ජන විශ්වාස සහ ශාන්තිකර්ම", "🔮"),
    ("12_general_knowledge", "General Knowledge", "ශ්‍රී ලංකා සාමාන්‍ය දැනුම", "🇱🇰"),
    ("13_sri_lanka_law", "Sri Lanka Law & Legal Sinhala", "ශ්‍රී ලංකා නීතිය සහ නීතිමය සිංහල", "⚖️"),
    ("14_culinary_kitchen", "Culinary & Kitchen Nuances", "දේශීය ඉවුම් පිහුම් සහ කුස්සියේ වහර", "🍛"),
    ("15_numbers_maths", "Numbers & Basic Maths", "සිංහල අංක සහ මූලික ගණිතය", "🔢"),
]

# Runner spec prefixes -> human-readable org names shown on the site.
PROVIDER_DISPLAY = {
    "gemini": "Google",
    "google": "Google",
    "openai": "OpenAI",
    "openrouter": "OpenRouter",
    "anthropic": "Anthropic",
    "ollama": "Ollama / Meta",
    "together": "Together AI",
    "deepseek": "DeepSeek",
    "kaggle": "Kaggle", 
}

# API model ids -> display names. Unknown ids are prettified generically.
MODEL_DISPLAY = {
    "gemini-3.5-flash-lite": "Gemini 3.5 Flash Lite",
    "gemini-3.5-flash": "Gemini 3.5 Flash",
    "gemini-3.5-pro": "Gemini 3.5 Pro",
    "gemini-2.5-flash-lite": "Gemini 2.5 Flash Lite",
    "gemini-2.5-flash": "Gemini 2.5 Flash",
    "gemini-2.5-pro": "Gemini 2.5 Pro",
    "gpt-4o": "GPT-4o",
    "gpt-4o-mini": "GPT-4o mini",
    "gpt-4.1": "GPT-4.1",
    "gpt-4.1-mini": "GPT-4.1 mini",
    "claude-sonnet-4-5": "Claude Sonnet 4.5",
    "claude-opus-4-1": "Claude Opus 4.1",
    "claude-3-5-haiku": "Claude 3.5 Haiku",
}


# Tokens in a model id that reveal WHO BUILT the model. The API host
# (openrouter, together, ollama...) is only a reseller, so the brand is
# taken from the model id first: openrouter:openai/gpt-4o -> "OpenAI".
BRAND_TOKENS = (
    ("gemini", "Google"),
    ("gemma", "Google"),
    ("palm", "Google"),
    ("bison", "Google"),
    ("gpt", "OpenAI"),
    ("o1", "OpenAI"),
    ("o3", "OpenAI"),
    ("o4", "OpenAI"),
    ("davinci", "OpenAI"),
    ("claude", "Anthropic"),
    ("llama", "Meta"),
    ("qwen", "Alibaba"),
    ("mistral", "Mistral AI"),
    ("mixtral", "Mistral AI"),
    ("magistral", "Mistral AI"),
    ("deepseek", "DeepSeek"),
    ("command", "Cohere"),
    ("grok", "xAI"),
    ("nova", "Amazon"),
    ("titan", "Amazon"),
    ("jamba", "AI21 Labs"),
)


def pretty_provider(host: str, model_id: str = "") -> str:
    """Org that BUILT the model, not the API host.

    ``pretty_provider("openrouter", "openai/gpt-4o-mini")`` -> ``"OpenAI"``;
    ``pretty_provider("gemini", "gemini-2.5-flash")`` -> ``"Google"``;
    ``pretty_provider("ollama", "llama3.1:8b")`` -> ``"Meta"``.
    """
    haystack = (model_id or "").lower()
    best: tuple[int, str] | None = None
    for token, brand in BRAND_TOKENS:
        pos = haystack.find(token)
        if pos >= 0 and (best is None or pos < best[0]):
            best = (pos, brand)
    if best:
        return best[1]
    key = (host or "").strip()
    return PROVIDER_DISPLAY.get(key.lower(), key or "Unknown")


def pretty_model(name: str) -> str:
    """``"gemini-3.5-flash-lite"`` -> ``"Gemini 3.5 Flash Lite"``."""
    raw = (name or "").strip()
    if raw in MODEL_DISPLAY:
        return MODEL_DISPLAY[raw]
    words = raw.replace("_", "-").split("-")
    fixed = {"gpt": "GPT", "llama": "Llama", "qwen": "Qwen",
             "mistral": "Mistral", "deepseek": "DeepSeek", "claude": "Claude",
             "gemini": "Gemini", "o1": "o1", "o3": "o3"}
    out = []
    for i, word in enumerate(words):
        low = word.lower()
        if i == 0 and low in fixed:
            out.append(fixed[low])
        elif low in {"lite", "mini", "pro", "flash", "turbo", "max", "chat"}:
            out.append(low if low in {"mini", "pro", "lite", "max"} else low.capitalize())
        elif any(ch.isdigit() for ch in word):
            out.append(word.upper() if low in {"4o", "4k"} else word)
        else:
            out.append(word.capitalize())
    return " ".join(out).strip() or "Unknown"


# (name, provider, base_ability, modality support flags)
DEMO_MODELS = [
    ("Gemini 2.5 Pro", "Google", 0.82, ("text", "vision", "audio")),
    ("GPT-4o", "OpenAI", 0.76, ("text", "vision", "audio")),
    ("Gemini 2.5 Flash", "Google", 0.71, ("text", "vision", "audio")),
    ("Claude Sonnet 4.5", "Anthropic", 0.68, ("text", "vision")),
    ("GPT-4o mini", "OpenAI", 0.55, ("text", "vision")),
    ("Llama 3.1 8B (local)", "Ollama / Meta", 0.38, ("text",)),
]

def serialize(varname: str, data) -> tuple[str, str]:
    """Return the (json_text, js_text) pair exactly as it is committed."""
    json_text = json.dumps(data, ensure_ascii=False, indent=2)
    js_text = ("window." + varname + " = " + json.dumps(data, ensure_ascii=False)
               + ";" + chr(10))
    return json_text, js_text


def _normalize(text: str) -> str:
    """Compare across CRLF/LF so Linux CI matches Windows-written files."""
    return text.replace("\r\n", "\n").strip()


def write_json_and_js(path: Path, varname: str, data) -> None:
    """Write data as JSON and as a plain <script> file setting a global.

    The .js variant lets the site load data on file:// URLs, where
    fetch() is blocked by CORS; app.js prefers the global and falls
    back to fetching the JSON over HTTP (GitHub Pages).
    """
    json_text, js_text = serialize(varname, data)
    path.write_text(json_text, encoding="utf-8")
    js = path.with_suffix(".js")
    js.write_text(js_text, encoding="utf-8")
    print(f"wrote {path}")
    print(f"wrote {js}")


def check_json_and_js(path: Path, varname: str, data) -> bool:
    """True when the committed JSON + JS pair already matches *data*.

    Never writes. Reports every stale or missing file so CI output is
    actionable.
    """
    json_text, js_text = serialize(varname, data)
    ok = True
    for target, expected in ((path, json_text), (path.with_suffix(".js"), js_text)):
        if not target.is_file():
            print(f"[!!] missing generated file: {target}")
            ok = False
            continue
        if _normalize(target.read_text(encoding="utf-8")) != _normalize(expected):
            print(f"[!!] stale generated file: {target}")
            ok = False
    return ok


def pillars_data() -> list:
    """The 15 pillar metadata rows shown by the site."""
    return [
        {"slug": slug, "title_en": en, "title_si": si, "icon": icon}
        for slug, en, si, icon in PILLARS
    ]


def write_pillars() -> None:
    write_json_and_js(OUT.parent / "pillars.json", "SYNHALEES_PILLARS", pillars_data())


def demo() -> dict:
    rng = random.Random(19960414)  # deterministic - Sri Lanka independence day
    models = []
    for name, provider, ability, modes in DEMO_MODELS:
        pillar_scores = {}
        for slug, *_ in PILLARS:
            noise = rng.uniform(-0.15, 0.15)
            score = max(5.0, min(98.0, (ability + noise) * 100))
            pillar_scores[slug] = round(score, 1)
        text = round(sum(pillar_scores.values()) / len(pillar_scores), 1)
        modalities = {"text": text}
        for m in modes:
            if m == "text":
                continue
            drop = rng.uniform(4, 14)  # multimodal is harder
            modalities[m] = round(max(0.0, text - drop), 1)
        present = [v for v in modalities.values() if v is not None]
        usage = {
            "overall": {
                "cost": round(rng.uniform(0.2, 9.0), 4),
                "tokens": rng.randint(60_000, 420_000),
                "latency": rng.randint(700, 5200),
            },
        }
        for m in ("text", "vision", "audio"):
            if modalities.get(m) is None:
                continue
            usage[m] = {
                "cost": round(usage["overall"]["cost"] * rng.uniform(0.15, 0.6), 4),
                "tokens": int(usage["overall"]["tokens"] * rng.uniform(0.15, 0.6)),
                "latency": rng.randint(700, 5200),
            }
        models.append({
            "name": name,
            "provider": provider,
            "date": "2026-09-20",
            "overall": round(sum(present) / len(present), 1),
            "modalities": {m: modalities.get(m) for m in ("text", "vision", "audio")},
            "pillars": pillar_scores,
            "usage": usage,
        })
    models.sort(key=lambda m: m["overall"], reverse=True)
    return {"demo": True, "updated": str(date.today()), "models": models}

def empty_payload() -> dict:
    """The published state when no submission CSV exists yet.

    An empty leaderboard is honest; fabricated numbers never are.
    """
    return {"demo": False, "updated": "", "models": []}


def build_real(csv_paths: list[Path]) -> dict:
    """Aggregate submission CSVs.

    Expected CSV columns (superset tolerated):
      model, provider, date, pillar, modality, score
    where ``score`` is 0-100 for one pillar/modality cell of one model.
    No CSVs -> empty_payload().
    """
    if not csv_paths:
        return empty_payload()
    acc: dict[str, dict] = {}
    for path in csv_paths:
        with path.open(newline="", encoding="utf-8-sig") as fh:
            for row in csv.DictReader(fh):
                key = row["model"]
                entry = acc.setdefault(key, {
                    "name": pretty_model(key),
                    "provider": pretty_provider(row.get("provider", ""), key),
                    "date": "",
                    "modalities": {"text": None, "vision": None, "audio": None},
                    "pillars": {},
                    "_mod": {"text": [], "vision": [], "audio": []},
                    "_usage": {k: {"cost": [], "tokens": [], "lat": []}
                               for k in ("overall", "text", "vision", "audio")},
                    "_dates": [],
                })
                if row.get("date"):
                    entry["_dates"].append(row["date"])
                score = float(row["score"])
                pillar, modality = row["pillar"], row.get("modality", "text")
                entry["pillars"].setdefault(pillar, []).append(score)
                if modality in entry["_mod"]:
                    entry["_mod"][modality].append(score)
                # Optional usage telemetry (Kaggle imports only; may be blank).
                bucket = entry["_usage"].get(modality)
                if bucket is not None:
                    for src, dst in (("cost_usd", "cost"),
                                     ("tokens", "tokens"),
                                     ("latency_ms", "lat")):
                        raw = (row.get(src) or "").strip()
                        if raw:
                            bucket[dst].append(float(raw))
                            if dst != "lat":
                                entry["_usage"]["overall"][dst].append(float(raw))
                            else:
                                entry["_usage"]["overall"][dst].append(float(raw))
    models = []
    for entry in acc.values():
        entry["pillars"] = {p: round(sum(v) / len(v), 1)
                            for p, v in entry["pillars"].items()}
        for m, vals in entry["_mod"].items():
            if vals:
                entry["modalities"][m] = round(sum(vals) / len(vals), 1)
        present = [v for v in entry["modalities"].values() if v is not None]
        entry["overall"] = round(sum(present) / len(present), 1) if present else 0.0
        dates = [d for d in entry.pop("_dates", []) if d]
        entry["date"] = max(dates) if dates else entry["date"]
        del entry["_mod"]
        usage = {}
        for mode, agg in entry.pop("_usage", {}).items():
            if not agg["tokens"]:
                continue
            usage[mode] = {
                "cost": round(sum(agg["cost"]), 4),
                "tokens": int(sum(agg["tokens"])),
                "latency": round(sum(agg["lat"]) / len(agg["lat"])) if agg["lat"] else None,
            }
        entry["usage"] = usage
        models.append(entry)
    models.sort(key=lambda m: m["overall"], reverse=True)
    # Deterministic stamp: the newest run date in the CSVs, never "today",
    # so --check stays green on later days.
    updated = max((m["date"] for m in models if m["date"]), default="")
    return {"demo": False, "updated": updated, "models": models}


def default_csvs() -> list[Path]:
    """The committed per-model submission CSVs (sorted: deterministic)."""
    if not SUBMISSIONS_DIR.is_dir():
        return []
    return sorted(SUBMISSIONS_DIR.glob("*.csv"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv", nargs="*", type=Path,
                    help="submission CSVs with model,provider,date,pillar,modality,score "
                         "(default: submissions/*.csv)")
    ap.add_argument("--demo", action="store_true",
                    help="write deterministic DEMO placeholder data")
    ap.add_argument("--check", action="store_true",
                    help="verify the committed docs/assets/data files match the "
                         "sources (no writes; exits 1 on drift)")
    args = ap.parse_args()

    if args.demo and args.check:
        ap.error("--demo and --check cannot be combined")

    payload = demo() if args.demo else build_real(args.csv or default_csvs())

    if args.check:
        ok = check_json_and_js(OUT, "SYNHALEES_LEADERBOARD", payload)
        ok = check_json_and_js(OUT.parent / "pillars.json",
                               "SYNHALEES_PILLARS", pillars_data()) and ok
        if not ok:
            print("\nRebuild with: python tools/build_leaderboard.py")
            return 1
        print(f"OK: docs/assets/data in sync "
              f"({len(payload['models'])} models, demo={payload['demo']})")
        return 0

    OUT.parent.mkdir(parents=True, exist_ok=True)
    write_pillars()
    write_json_and_js(OUT, "SYNHALEES_LEADERBOARD", payload)
    print(f"  -> {len(payload['models'])} models, demo={payload['demo']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
