#!/usr/bin/env python3
"""Build the static leaderboard data (docs/assets/data/leaderboard.json).

Two modes:

  1. Real data  - point it at one or more submission CSV files produced by
     results.save_submission("submission.csv") and it aggregates scores
     into the JSON consumed by the static site:

         python tools/build_leaderboard.py submissions/*.csv

  2. Demo data  - generate clearly-marked placeholder scores so the site is
     viewable before any real benchmark run exists:

         python tools/build_leaderboard.py --demo

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

# (name, provider, base_ability, modality support flags)
DEMO_MODELS = [
    ("Gemini 2.5 Pro", "Google", 0.82, ("text", "vision", "audio")),
    ("GPT-4o", "OpenAI", 0.76, ("text", "vision", "audio")),
    ("Gemini 2.5 Flash", "Google", 0.71, ("text", "vision", "audio")),
    ("Claude Sonnet 4.5", "Anthropic", 0.68, ("text", "vision")),
    ("GPT-4o mini", "OpenAI", 0.55, ("text", "vision")),
    ("Llama 3.1 8B (local)", "Ollama / Meta", 0.38, ("text",)),
]

def write_json_and_js(path: Path, varname: str, data) -> None:
    """Write data as JSON and as a plain <script> file setting a global.

    The .js variant lets the site load data on file:// URLs, where
    fetch() is blocked by CORS; app.js prefers the global and falls
    back to fetching the JSON over HTTP (GitHub Pages).
    """
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    js = path.with_suffix(".js")
    js.write_text("window." + varname + " = " + json.dumps(data, ensure_ascii=False) + ";" + chr(10),
                  encoding="utf-8")
    print(f"wrote {path}")
    print(f"wrote {js}")


def write_pillars() -> None:
    data = [
        {"slug": slug, "title_en": en, "title_si": si, "icon": icon}
        for slug, en, si, icon in PILLARS
    ]
    write_json_and_js(OUT.parent / "pillars.json", "SYNHALEES_PILLARS", data)


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
        models.append({
            "name": name,
            "provider": provider,
            "date": "2026-09-20",
            "overall": round(sum(present) / len(present), 1),
            "modalities": {m: modalities.get(m) for m in ("text", "vision", "audio")},
            "pillars": pillar_scores,
        })
    models.sort(key=lambda m: m["overall"], reverse=True)
    return {"demo": True, "updated": str(date.today()), "models": models}

def build_real(csv_paths: list[Path]) -> dict:
    """Aggregate submission CSVs.

    Expected CSV columns (superset tolerated):
      model, provider, date, pillar, modality, score
    where ``score`` is 0-100 for one pillar/modality cell of one model.
    """
    acc: dict[str, dict] = {}
    for path in csv_paths:
        with path.open(newline="", encoding="utf-8-sig") as fh:
            for row in csv.DictReader(fh):
                key = row["model"]
                entry = acc.setdefault(key, {
                    "name": key,
                    "provider": row.get("provider", ""),
                    "date": row.get("date", str(date.today())),
                    "modalities": {"text": None, "vision": None, "audio": None},
                    "pillars": {},
                    "_mod": {"text": [], "vision": [], "audio": []},
                })
                score = float(row["score"])
                pillar, modality = row["pillar"], row.get("modality", "text")
                entry["pillars"].setdefault(pillar, []).append(score)
                if modality in entry["_mod"]:
                    entry["_mod"][modality].append(score)
    models = []
    for entry in acc.values():
        entry["pillars"] = {p: round(sum(v) / len(v), 1)
                            for p, v in entry["pillars"].items()}
        for m, vals in entry["_mod"].items():
            if vals:
                entry["modalities"][m] = round(sum(vals) / len(vals), 1)
        present = [v for v in entry["modalities"].values() if v is not None]
        entry["overall"] = round(sum(present) / len(present), 1) if present else 0.0
        del entry["_mod"]
        models.append(entry)
    models.sort(key=lambda m: m["overall"], reverse=True)
    return {"demo": False, "updated": str(date.today()), "models": models}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv", nargs="*", type=Path,
                    help="submission CSVs with model,provider,date,pillar,modality,score")
    ap.add_argument("--demo", action="store_true",
                    help="write deterministic DEMO placeholder data")
    args = ap.parse_args()

    if not args.demo and not args.csv:
        ap.error("provide submission CSVs, or use --demo")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    write_pillars()
    payload = demo() if args.demo else build_real(args.csv)
    write_json_and_js(OUT, "SYNHALEES_LEADERBOARD", payload)
    print(f"  -> {len(payload['models'])} models, demo={payload['demo']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
