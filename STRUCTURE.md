# 🛠️ SynhalEES: Architecture & Dataset Developer Specification

This document provides the definitive architectural blueprint, folder hierarchy, decision frameworks, and data schemas for building and maintaining the SynhalEES benchmark.

---

## 1. Directory Tree (Pillar-First Architecture)

To avoid mixing datasets into an unmaintainable single file, SynhalEES is strictly organized **Pillar-First**. Each of the 15 pillars is an isolated, self-contained modular folder containing its respective Text, Vision, and Audio data:

```text
SynhalEES/
│
├── benchmark_data/
│   │
│   ├── 01_buddhist_culture/
│   │   ├── text.csv                           # Text QA / Customs
│   │   ├── vision/                            # Images (Stupas, relics, offerings)
│   │   │   ├── images/
│   │   │   └── vision.csv
│   │   └── audio/                             # Audio (Pirith chanting, sermons)
│   │       ├── mp3s/
│   │       └── audio.csv
│   │
│   ├── 02_pali_gatha/                         # Dhamma verses & Pali OCR/Audio
│   ├── 03_classical_literature/               # Jataka tales & temple murals
│   ├── 04_kavi_sindu/                         # Kavi & Sinhala songs — meters, metaphors, instruments
│   ├── 05_sinhala_grammar/                    # Sinhala grammar rules & writing (ව්‍යාකරණ ලේඛනය)
│   ├── 06_daily_spoken/                       # Everyday chats & conversational voice
│   ├── 07_figurative_sinhala/                 # Wordplay, hidden meanings & similes
│   ├── 08_profanity_nuance/                   # Friendly banter vs. toxic abuse
│   ├── 09_singlish_sms/                       # WhatsApp shorthand & chat screenshots
│   ├── 10_regional_dialects/                  # Southern, Kandy, Rajarata accents
│   ├── 11_astrology_beliefs/                  # Horoscopes, Rahu time, Thovil masks
│   ├── 12_general_knowledge/                  # Sri Lanka general knowledge & national symbols
│   ├── 13_sri_lanka_law/                       # Sri Lanka legal system & legal Sinhala
│   ├── 14_culinary_kitchen/                   # Cooking terminology & food dishes
│   └── 15_numbers_maths/                      # Numbers, counting & basic maths in Sinhala
│
├── synhalees/                                 # Core Python Package
│   ├── __init__.py                            # Public API (data loaders, env helpers, lazy SynhalEESBenchmark)
│   ├── __main__.py                            # python -m synhalees -> the unified CLI
│   ├── cli.py                                 # unified CLI: run/compare/build/publish/check/kaggle *
│   ├── data/                                  # Data Loaders & Registry
│   │   └── __init__.py
│   ├── models/                                # Model adapters (Ollama, OpenRouter, Gemini, OpenAI, Anthropic)
│   │   └── __init__.py
│   ├── evaluators/                            # Scoring engines (exact match, WER, classification, judge)
│   │   └── __init__.py
│   ├── env.py                                 # Kaggle/Local environment detector
│   └── runner.py                              # Checkpointed runner + SynhalEESBenchmark (resume-safe)
│
├── kaggle/                                      # Kaggle Benchmarks tasks (optional leaderboard path)
│   ├── generate_tasks.py                        # regenerates the 17 task files
│   ├── synhalees_task.py                        # all-in-one master task (15 pillars, text)
│   └── tasks/                                   # 17 generated: 15 pillars (text) + vision + audio
│
├── logo/                                        # Brand assets (logo & cover art)
│   ├── logo.png                                 # Primary logo
│   ├── icon.png                                 # App/favicon icon
│   ├── icon-white.png                           # Favicon variant on white rounded chip
│   ├── logo.jpg / logo.af                       # JPG export + Affinity source file
│   └── cover.jpg / cover.af                     # Cover art + Affinity source file
│
├── tools/                                       # Data-collection helper tools
│   ├── compare_runs.py                        # Compares runs/<model-slug>/ folders -> runs/all_submissions.csv + matrix report
│   ├── build_leaderboard.py                     # submissions/*.csv -> docs/assets/data/* (--check = CI sync guard, --demo = preview-only)
│   ├── build_logo_data.py                       # Regenerates docs/assets/logo-data.js (--check = staleness guard, CI-enforced)
│   └── audio-recorder-extension/                # Chrome MV3 mic recorder (for audio pillar data)
│       ├── manifest.json                        # Extension manifest (Simple Audio Recorder)
│       ├── background.js                        # Service worker
│       ├── popup.html / popup.js                # Recorder UI
│       ├── recorder-worklet.js                  # Audio capture worklet
│       ├── lame.min.js                          # MP3 encoder
│       └── icon16.png / icon48.png / icon128.png
│
├── docs/                                        # Static leaderboard website (GitHub Pages)
│   ├── index.html                               # Leaderboard page (table, comparison chart, pillar champions, radar detail)
│   ├── .nojekyll                                # Disable Jekyll processing on Pages
│   └── assets/
│       ├── style.css                            # SynhalaAI theme (logo navy #2B3044 + red #C62828)
│       ├── app.js                               # Vanilla JS: sorting, modality tabs, pillar filter, radar chart
│       ├── chart.js                             # AA-style comparison bar chart (metric tabs, org filter, model picker, max 25 bars)
│       ├── logo-data.js                         # GENERATED by tools/build_logo_data.py — logos+icon as data URLs (taint-free PNG export)
│       ├── logo.png                             # Logo copy for Pages (Pages serves only docs/)
│       ├── icon.png                             # Favicon copy for Pages
│       ├── logos/                                   # Provider brand icons (SVG, simple-icons)
│       │   ├── google.svg / openai.svg / anthropic.svg / meta.svg / mistralai.svg
│       │   ├── deepseek.svg / alibabacloud.svg / amazonwebservices.svg
│       │   ├── microsoft.svg / ibm.svg / perplexity.svg / xiaomi.svg
│       │   └── synhalaAI.svg                          # SynhalaAI brand mark (custom)
│       └── data/                                # Generated by tools/build_leaderboard.py
│           ├── pillars.json / pillars.js        # 15 pillar metadata (slug, EN/SI titles)
│           └── leaderboard.json / leaderboard.js  # Model scores (.js variants work on file://)
│
├── .github/                                   # CI workflows
│   └── workflows/
│       ├── logo-data.yml                        # Fails when docs/assets/logo-data.js is stale (SOURCE-SHA256 check)
│       └── leaderboard.yml                     # Fails when docs/assets/data/* drifts from submissions/ (--check)
│
├── runs/                                      # Gitignored: one folder per model (checkpoint.jsonl, submission.csv, meta.json)
├── kaggle-results/                            # Gitignored: Kaggle Benchmarks pulls (kaggle b t download -o kaggle-results/<task>/<ver>/<model>/<run-id>/)
├── submissions/                                 # COMMITTED: one <model-slug>.csv per model -- the published site data source
│   └── README.md                                  # scorecard schema + publication rules
├── .gitignore                                 # runs/, kaggle-results/, __pycache__, packaging artifacts, lock files
├── pyproject.toml                             # pip install -e . packaging
├── run_benchmark.py                           # CLI entry point (checkpointed API runner)
├── AGENTS.md                                  # AI-agent / contributor conventions
├── CONTRIBUTING.md                            # Contribution guide
├── STRUCTURE.md                               # This file — technical spec
└── README.md
```

---

## 2. The 4 Evaluation Types (`eval_type`)

Data is scored using one of 4 dedicated evaluation engines specified in the `eval_type` column of each dataset:

| `eval_type` | Description | Used For | Scored By |
|---|---|---|---|
| `exact_match` | Absolute factual correctness | MCQs, historical facts, entity names | Exact string / option match (0 or 1) |
| `llm_judge` | Subjective naturalness & empathy | Slang, conversational empathy, metaphors | Multi-criteria Judge Rubric (1 to 5) |
| `wer` | Word Error Rate & transcription accuracy | Speech-to-Text (ASR) and OCR reading | Normalized Levenshtein edit distance |
| `classification`| Categorical intent disambiguation | Banter vs. Abuse, Acoustic tone | Precision, Recall, F1 / Accuracy |

---

## 3. Decision Tree: How to Assign `eval_type`

When creating a dataset item, use this 3-question decision flow to determine the correct `eval_type`:

```
                    [ Question / Sample to Annotate ]
                                   │
                                   ▼
  [Question 1]: Does it have ONLY ONE fixed, objective correct answer?
         ├──► YES ──► Set `eval_type = "exact_match"`
         │
         ▼ (NO)
  [Question 2]: Is it transcribing speech from audio or text from an image (OCR)?
         ├──► YES ──► Set `eval_type = "wer"`
         │
         ▼ (NO)
  [Question 3]: Is it selecting between discrete intent labels (e.g., Banter vs Abuse)?
         ├──► YES ──► Set `eval_type = "classification"`
         │
         ▼ (NO)
  [Default]: Is it conversational, empathetic, subjective, or creative?
         └──► Set `eval_type = "llm_judge"`
```

---

## 4. Master 15-Pillar Mapping Cheat-Sheet

| Pillar ID | Primary Modalities | Default Text `eval_type` | Default Vision `eval_type` | Default Audio `eval_type` |
|---|---|---|---|---|
| `01_buddhist_culture` | Text, Vision, Audio | `exact_match` | `exact_match` | `wer` |
| `02_pali_gatha` | Text, Vision, Audio | `exact_match` | `wer` (OCR) | `wer` |
| `03_classical_literature` | Text, Vision, Audio | `exact_match` | `exact_match` | `wer` |
| `04_kavi_sindu` | Text, Vision, Audio | `exact_match` / `llm_judge` | `wer` (OCR) / `exact_match` | `classification` (Meter/Emotion) |
| `05_sinhala_grammar` | Text, Vision, Audio | `exact_match` / `llm_judge` | `wer` (OCR) / `exact_match` | `wer` (Dictation) |
| `06_daily_spoken` | Text, Vision, Audio | `classification` | `llm_judge` | `wer` (ASR) |
| `07_figurative_sinhala` | Text, Vision, Audio | `classification` | `exact_match` | `exact_match` (figurative) |
| `08_profanity_nuance` | Text, Vision, Audio | `classification` | `classification` | `classification` (Tone) |
| `09_singlish_sms` | Text, Vision, Audio | `llm_judge` | `wer` (OCR) | `wer` |
| `10_regional_dialects` | Text, Vision, Audio | `exact_match` | `exact_match` | `wer` (Regional ASR) |
| `11_astrology_beliefs` | Text, Vision, Audio | `exact_match` | `exact_match` | `classification` |
| `12_general_knowledge` | Text, Vision, Audio | `exact_match` / `classification` | `exact_match` | `exact_match` / `wer` |
| `13_sri_lanka_law` | Text, Vision, Audio | `exact_match` / `classification` | `exact_match` | `exact_match` / `wer` |
| `14_culinary_kitchen` | Text, Vision, Audio | `exact_match` | `exact_match` | `wer` |
| `15_numbers_maths` | Text, Vision, Audio | `exact_match` / `classification` | `exact_match` | `exact_match` / `wer` |

---

## 5. CSV Data Schemas

### A. Text Schema (`text.csv`)
Located inside each pillar directory (e.g., `benchmark_data/01_buddhist_culture/text.csv`):
```csv
id,prompt,ground_truth,eval_type
TXT_01_001,"හාමුදුරුවන්ට දානය පූජා කරන එකට කියන වචනේ මොකක්ද?","දානේ වළඳනවා",exact_match
TXT_07_005,"අඩෝ ඊයේ presentation එක full chora වුණා බං, ඒකට මට සෑහෙන්න අව්ල් ගියා.","friendly_empathetic_response",llm_judge
TXT_08_002,"තෝ නම් කාලකණ්ණි බල්ලෙක් බං හිනාවෙලා මැරෙනවා","friendly_banter",classification
```

### B. Vision Schema (`vision/vision.csv`)
Located inside each pillar directory (e.g., `benchmark_data/01_buddhist_culture/vision/vision.csv`):
```csv
id,image_file,question,ground_truth,eval_type
VIS_01_001,dana_bowl_01.jpg,"මේ පින්තූරේ තියෙන පූජා භාණ්ඩේ මොකක්ද?","පාත්තරය",exact_match
VIS_12_004,meme_bus_04.png,"මේ මීම් එකේ ජෝර්ක් එක මොකක්ද?","humor_explanation",llm_judge
```

> **OCR exception (`question` may be empty):** for image-OCR rows
> (`eval_type = "wer"`), the `question` column may be left **empty** (or the
> column omitted entirely on wer-only files) — the
> benchmark loader then injects the standard OCR prompt
> **`මේකේ තියෙන දේ අකුරෙන් ලියන්න.`** automatically, so contributors
> don't have to repeat the same transcription prompt on every row. This
> applies only to `wer` rows: `exact_match`, `llm_judge` and
> `classification` rows must always carry an image-specific `question`,
> because the script cannot infer what to ask about a given image.
>
> ```csv
> VIS_02_003,03.jpg,,ධම්මං සරණං ගච්ඡාමි,wer
> ```

### C. Audio Schema (`audio/audio.csv`)
Located inside each pillar directory (e.g., `benchmark_data/10_regional_dialects/audio/audio.csv`):
```csv
id,audio_file,question,ground_truth,eval_type
AUD_10_001,southern_accent_01.mp3,,"මොකෝ බොල තෝ දුවන්නේ",wer
AUD_07_001,hidden_meaning_01.mp3,මේ කතාවෙන් ඇත්තටම කියන්නේ මොකක්ද?,අතපාසා ඉල්ලීම,exact_match
AUD_08_003,shouting_anger_03.mp3,,"aggressive_abuse",classification
```

> **Optional `question` column:** empty (or omitted) on `wer` rows -> the
> loader injects the default transcription prompt
> **`මේකේ ඇහෙන දේ අකුරෙන් ලියන්න.`** automatically. Comprehension
> rows (`exact_match`, `classification`, `llm_judge`) may omit it too:
> `exact_match` / `llm_judge` receive the generic media prompt
> **`මේක අහලා උත්තර දෙන්න.`** (listen and answer) and `classification`
> receives **`මේක අහලා තෝරන්න: <label 1>, <label 2>, ...`** -- the pillar's
> distinct `ground_truth` labels comma-separated, so the model knows the
> option set to choose from. Pillars never need a question column just to
> satisfy the loader. A row may still carry its own specific question when
> the task needs one (e.g. what the hidden meaning of a spoken figure of
> speech is).

---

## 6. Site Data Pipeline (`submissions/` -> `docs/`)

The public leaderboard in `docs/` is a static site: **no database and no backend**.
Its only data source is a committed folder of per-model scorecards.

```text
runs/<model-slug>/submission.csv              # local run scorecard -- gitignored
kaggle-results/<task>/<ver>/<model>/<run>/    # Kaggle Benchmarks pull -- gitignored
        |
        |  copy the scorecard, renamed after the model slug
        v
submissions/<model-slug>.csv            # COMMITTED: one file per model (source of truth)
        |
        |  python tools/build_leaderboard.py          (writes)
        |  python tools/build_leaderboard.py --check  (verifies, CI)
        v
docs/assets/data/leaderboard.json + .js # GENERATED -- never hand-edited
docs/assets/data/pillars.json     + .js # GENERATED -- never hand-edited
        |
        v
docs/index.html + assets/app.js + assets/chart.js   # static site (GitHub Pages)
```

Rules:

1. **Schema.** `submissions/<model-slug>.csv` holds the scorecard rows
   `model,provider,date,pillar,modality,score` with `score` as a 0-100
   percentage -- exactly what `results.save_submission()` writes. One file per
   model; never merge two models into one CSV.
2. **Generated files are read-only.** `docs/assets/data/*` is produced by
   `tools/build_leaderboard.py`; commit the CSV and the regenerated data files
   in the same change. The `.js` variants duplicate the JSON so the site also
   works from `file://`, where `fetch()` is CORS-blocked.
3. **Deterministic output.** `updated` is the newest `date` found in the CSVs,
   never "today", so `--check` stays green on later days.
4. **CI guard.** `.github/workflows/leaderboard.yml` runs
   `python tools/build_leaderboard.py --check` on every push/PR and fails when
   a generated file is stale or missing. The comparison ignores CRLF/LF so
   Linux CI matches Windows-written files.
5. **Empty is valid.** With no CSVs the tool writes an empty leaderboard
   (`models: []`) and the site shows its empty-state note; it never fabricates
   placeholder rows. `--demo` output is a local preview only -- never commit it.
6. **Only publishable runs.** No numbers produced with a personal or paid API
   key that the project cannot re-verify or publish.

7. **Kaggle pulls.** Kaggle Benchmarks artifacts are downloaded into the
   gitignored `kaggle-results/` folder (`kaggle b t download <task-slug> -o
   kaggle-results`) -- never the repo root. Each run exposes only an overall
   score, so a task scoped to one pillar maps to one scorecard row;
   multi-pillar (vision/audio) tasks cannot be split per pillar yet (see
   `submissions/README.md`).
