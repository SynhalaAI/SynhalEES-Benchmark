<p align="center">
  <img src="logo/cover.jpg" alt="SynhalEES Benchmark — The Multimodal Sinhala Cultural Benchmark" width="100%"/>
</p>

# 🇱🇰 SynhalEES: The Multimodal Sinhala Cultural Benchmark

[![Maintained by SynhalaAI](https://img.shields.io/badge/Maintained%20by-SynhalaAI-blue.svg)](https://github.com/SynhalaAI)
[![Providers](https://img.shields.io/badge/Providers-Ollama%20%C2%B7%20OpenRouter%20%C2%B7%20Gemini%20%C2%B7%20OpenAI-blue.svg)]()

> **Measuring how authentically Multimodal AI Models (LLMs, VLMs, Audio-LLMs) THINK as a native Sri Lankan Sinhala human — reasoning in Sinhala, seeing the world through Sinhala eyes, and speaking like a native — not like a translated English machine.**

---

## 📖 Overview

Standard AI benchmarks test models using translated English datasets. The result: models that don't just *speak* robotic Sinhala — they **think in English and translate**. They miss the humor behind a meme, the hidden meaning inside a kavi, the line between friendly banter and abuse, and the cultural reflexes every native Sinhala human grows up with.

Developed by **[SynhalaAI](https://github.com/SynhalaAI)**, **SynhalEES** is the first tri-modal benchmark (**Text, Vision, Audio**) built to answer one question: **does a model think as a Sinhala human — or does it merely translate English thoughts into Sinhala words?**

It probes a model's native reasoning across **15 distinct cultural and linguistic pillars**—ranging from sacred Pali stanzas and classical literature to wordplay and hidden meanings, Singlish SMS, regional accents, and local culinary wisdom. Each pillar measures whether the model's *thought process* is natively Sinhala: Does it get the joke the way a Colombo teen does? Feel the empathy a village elder would? Know instinctively when "තෝ බල්ලෙක්" is banter and when it is an insult?

### 🧠 What "thinking as Sinhalese" means

- **Reason in Sinhala, not through English** — idioms, ව්‍යංග්‍යාර්ථ (hidden meanings), and similes must be understood from within the language itself.
- **Cultural instincts, not trivia** — knowing *why* a ritual is performed matters more than reciting *what* it is.
- **Native pragmatics** — tone, register, banter-vs-abuse, regional dialects, and Singlish must be handled the way a native speaker would react.
- **Multimodal like a native** — look at a temple image, hear a dialect accent, or read a chat screenshot, and respond with local context — not generic, translated answers.

---

## 🏛️ The 15 Core Pillars

SynhalEES evaluates AI systems across 15 self-contained modular pillars.
Folder slug (`benchmark_data/<slug>/`) is the canonical ID — display title is aligned 1:1 with `STRUCTURE.md`:

1. **Buddhist Culture & Rituals (බෞද්ධ සංස්කෘතිය සහ සිරිත්)** — `01_buddhist_culture`
2. **Pali Language & Gatha (පාලි භාෂාව සහ ගාථා)** — `02_pali_gatha`
3. **Classical Literature & Old Sinhala (සම්භාව්‍ය සාහිත්‍යය සහ පුරාතන සිංහල)** — `03_classical_literature`
4. **Kavi & Sindu — Poetry & Song (ජන කවි, සම්භාව්‍ය කවි සහ සිංහල සිංදු)** — `04_kavi_sindu`
5. **Sinhala Grammar & Writing (සිංහල ව්‍යාකරණ ලේඛනය)** — `05_sinhala_grammar`
6. **Daily Spoken Sinhala (දෛනික කථන සිංහල)** — `06_daily_spoken`
7. **Sinhala Wordplay & Hidden Meanings (ව්‍යංග්‍යාර්ථ, යටි අර්ථ සහ උපමා)** — `07_figurative_sinhala`
8. **Profanity Nuance: Banter vs Abuse (කුණුහරුප සහ අපහාස)** — `08_profanity_nuance`
9. **Singlish & Short Messaging (සිංග්ලිෂ් සහ කෙටි පණිවිඩ)** — `09_singlish_sms`
10. **Regional Dialects: Southern, Up-Country (Kandy), Rajarata (ප්‍රාදේශීය ව්‍යවහාර)** — `10_regional_dialects`
11. **Astrology & Folk Beliefs (ජන විශ්වාස සහ ශාන්තිකර්ම)** — `11_astrology_beliefs`
12. **General Knowledge (ශ්‍රී ලංකා සාමාන්‍ය දැනුම)** — `12_general_knowledge`
13. **Sri Lanka Law & Legal Sinhala (ශ්‍රී ලංකා නීතිය සහ නීතිමය සිංහල)** — `13_sri_lanka_law`
14. **Culinary & Kitchen Nuances (දේශීය ඉවුම් පිහුම් සහ කුස්සියේ වහර)** — `14_culinary_kitchen`
15. **Numbers & Basic Maths (සිංහල අංක සහ මූලික ගණිතය)** — `15_numbers_maths`

> 📌 **Detailed technical specifications, folder mapping, and dataset schemas can be found in [`STRUCTURE.md`](STRUCTURE.md).**

---

## ⚡ Quickstart (API-driven, no GPU needed)

SynhalEES talks **directly to chat-LLM APIs** -- pure-stdlib HTTP, zero third-party
dependencies, with crash-safe checkpointing built in.

### 1. Install
```bash
git clone https://github.com/SynhalaAI/SynhalEES-Benchmark.git
cd SynhalEES-Benchmark
pip install -e .
```

### 2. Pick a provider
| Provider | Model spec | API key env var |
|---|---|---|
| Ollama (local, free) | `ollama:llama3.1:8b` | -- none -- |
| OpenRouter | `openrouter:openai/gpt-4o-mini` | `OPENROUTER_API_KEY` |
| Gemini | `gemini:gemini-2.5-flash` | `GEMINI_API_KEY` |
| OpenAI | `openai:gpt-4o` | `OPENAI_API_KEY` |
| Anthropic (no audio) | `anthropic:claude-sonnet-4-5` | `ANTHROPIC_API_KEY` |

### 3. Run from Python
```python
from synhalees import SynhalEESBenchmark

benchmark = SynhalEESBenchmark(
    "gemini:gemini-2.5-flash",
    judge_model="gemini:gemini-2.5-pro",   # optional; defaults to the same model
    modalities=["text"],                   # add "vision", "audio" once media is committed
)
results = benchmark.run()
results.print_scorecard()
results.save_submission("runs/<model-slug>/submission.csv")
```

### 4. One CLI for everything
`pip install -e .` installs a single `synhalees` command (also
`python -m synhalees`); each subcommand forwards its flags to the underlying
script, so nothing has to be memorized twice:

| Command | Does |
|---|---|
| `synhalees run --model <spec> [...]` | run the benchmark locally (`synhalees run --help` shows the flags) |
| `synhalees compare` | per-model summary + pillar x model matrix (`runs/all_submissions.csv`) |
| `synhalees build [--check]` | regenerate `docs/assets/data/*` from `submissions/` (`--check` = CI gate) |
| `synhalees logos [--check]` | regenerate `docs/assets/logo-data.js` |
| `synhalees publish <model-slug>` | `runs/<slug>/submission.csv` -> `submissions/`, rebuild + verify |
| `synhalees check` | every local gate in one go (logos, leaderboard, compile) |
| `synhalees kaggle gen` | regenerate the 17 task files |
| `synhalees kaggle push [task]` | upload task file(s); default: all 17 |
| `synhalees kaggle run <task> -m <model>` | start a server-side run |
| `synhalees kaggle status/logs/publish <task>` | inspect / publish the Kaggle side |
| `synhalees kaggle pull <task>` | artifacts -> `kaggle-results/` (the `-o` is forced) |
| `synhalees kaggle import <task>` | single-pillar Kaggle results -> `submissions/<slug>.csv` + rebuild |

```bash
synhalees run --model ollama:llama3.1:8b --pillars 01_buddhist_culture
python run_benchmark.py --model ...   # the script form still works
```

### Optional: Kaggle leaderboard
Want a public leaderboard on Kaggle Benchmarks instead? The task file in
[`kaggle/synhalees_task.py`](kaggle/synhalees_task.py) is push-ready -- with a
Kaggle API token (`kaggle.json`) the whole flow runs from the terminal:
`kaggle b t push` -> `kaggle b t run` -> `kaggle b t publish`, then pull the
artifacts with `kaggle b t download <task-slug> -o kaggle-results` (gitignored --
outputs never land in the repo root).
The unified CLI wraps the same flow: `synhalees kaggle push|run|status|publish|pull|import`.
Runs are server-side, so closing your laptop will not interrupt them.

For per-pillar leaderboards, `kaggle/tasks/` holds **17 generated task files**
(15 pillars on text + 1 vision + 1 audio); regenerate with
`python kaggle/generate_tasks.py` and push all with
`Get-ChildItem kaggle/tasks/*.py | % { kaggle b t push $_.FullName }`.
Note: vision/audio tasks need the media files committed first.

### Per-model run folders
Every run gets its own folder, so different models never overwrite each other:

```
runs/<model-slug>/
  checkpoint.jsonl   # raw per-item records: prompt, response, ground truth, score, error
  submission.csv     # scorecard: model,provider,date,pillar,modality,score (0-100)
  meta.json          # model, provider, date, item count, overall, error count
```

Override with `--runs-dir`, `--checkpoint` or `--output` if you need to.

### Crash-safe resume
Every item is appended to `runs/<model-slug>/checkpoint.jsonl` the moment it
completes. If the run is interrupted (Ctrl+C, quota, network), **rerun the same
command** -- completed items are skipped automatically. Use `--fresh` to start
over. Items that ended in an API error count as wrong answers: delete those
lines from the checkpoint (or rerun with `--fresh`) once the problem is fixed.

---

## 🏆 Leaderboard

The public model leaderboard lives in [`docs/`](docs/index.html) — a static,
GitHub Pages-ready site (no build step). Enable it via
**Settings → Pages → Deploy from branch → `main` / `docs`**, and it appears at
`https://synhalaai.github.io/SynhalEES-Benchmark/`.

Update the data after running models:

```bash
# 1. compare every run under runs/ and print a pillar x model accuracy matrix
python tools/compare_runs.py

# 2. publish each model's scorecard as submissions/<model-slug>.csv
#    (one file per model -- see submissions/README.md)

# 3. regenerate the site data from submissions/ (no arguments needed)
python tools/build_leaderboard.py

# verify the committed data is in sync -- this is what CI runs
python tools/build_leaderboard.py --check

# or preview the site with clearly-marked placeholder data (never commit this)
python tools/build_leaderboard.py --demo
```

`submissions/` is the committed source of truth for the leaderboard: one
`<model-slug>.csv` per model with the schema
`model,provider,date,pillar,modality,score`. Only **publishable** runs belong
there. An empty folder publishes an empty leaderboard (`models: []`) -- the site
never shows fabricated rows -- and CI fails whenever `docs/assets/data/*` drifts
from `submissions/`.

`tools/compare_runs.py` prints a per-model summary (overall accuracy, item
count, API errors) plus a pillar x model accuracy matrix, so you can see which
model is strong on which pillar before publishing.

---

## 🤝 Contributing

Contributions are welcome — data, code, or feedback. See
[`CONTRIBUTING.md`](CONTRIBUTING.md) for the full guide and Definition of Done.
Every pillar folder is a small, reviewable PR — no giant CSVs.

---

## 📜 License & Organization
* **License:** This project is licensed under the **[SynhalaAI Sovereign Research License (SSRL-1.0)](https://github.com/SynhalaAI/License/blob/main/SSRL.md)** - a sovereign public trust license open exclusively to Sri Lankan citizens and Sri Lankan academic/non-profit institutions, for non-commercial research only. Commercial use is permanently prohibited.
* **Maintained by:** **[SynhalaAI](https://github.com/SynhalaAI)** - An open-source initiative empowering Sinhala AI.
