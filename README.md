<p align="center">
  <img src="logo/cover.jpg" alt="SynhalEES Benchmark — The Multimodal Sinhala Cultural Benchmark" width="100%"/>
</p>

# 🇱🇰 SynhalEES: The Multimodal Sinhala Cultural Benchmark

[![Maintained by SynhalaAI](https://img.shields.io/badge/Maintained%20by-SynhalaAI-blue.svg)](https://github.com/SynhalaAI)
[![License: SSRL-1.0](https://img.shields.io/badge/License-SSRL--1.0-red.svg)](https://github.com/SynhalaAI/License/blob/main/SSRL.md)
[![Providers](https://img.shields.io/badge/Providers-Ollama%20%C2%B7%20OpenRouter%20%C2%B7%20Gemini%20%C2%B7%20OpenAI-blue.svg)]()
[![Live Leaderboard](https://img.shields.io/badge/Live-Leaderboard-brightgreen.svg)](https://synhalaai.github.io/SynhalEES-Benchmark/)

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

### 🎯 What a score means

Every score in SynhalEES is calibrated against one yardstick: **a native-born Sri Lankan Sinhala human**.
The target is not trivia recall — it is the Sinhala context a person gathers **through a lifetime of living it**:
the jokes, the rituals, the tone, the dialects, the everyday pragmatics. A model reaches the top of this
benchmark only when it combines **real, practical data**, **cultural awareness (සංස්කෘතික බුද්ධිය)** and
**genuine reasoning** — answering the way a Sinhala human would, not the way a translation engine would.
A model that merely translates English thoughts into Sinhala words scores low here *by design*: matching a
native's lifetime of gathered context is exactly what this benchmark measures.

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

### 4. Unified All-In-One CLI (`synhalees`)
Installing the benchmark with `pip install -e .` exposes the unified `synhalees` CLI (or `python -m synhalees`). Everything from running evaluations to Kaggle deployment and leaderboard generation is managed through this single tool:

```bash
# Run evaluations locally
synhalees run --model gemini:gemini-2.5-flash
synhalees run --model ollama:llama3.1:8b --pillars 01_buddhist_culture
synhalees run --model openrouter:openai/gpt-4o-mini --modality text

# Compare runs & view accuracy matrix across models
synhalees compare

# Publish scorecard to leaderboard (auto-resolves submissions/<vendor>/<family>/<model>.csv)
synhalees publish gemini-2.5-flash

# Verify all repository checks & CI gates locally
synhalees check
```

#### CLI Command Reference

| Command | Action | Description |
|---|---|---|
| **`synhalees run`** | Run benchmark | Evaluates models locally with crash-safe checkpointing. |
| **`synhalees compare`** | Compare runs | Generates cross-model scorecards and accuracy matrix at `runs/all_submissions.csv`. |
| **`synhalees publish <model-slug>`** | Publish run | Resolves taxonomy hierarchy, copies scorecard to `submissions/<vendor>/<family>/`, rebuilds docs, and verifies integrity. |
| **`synhalees build [--check]`** | Build leaderboard | Re-generates `docs/assets/data/*` from `submissions/` (`--check` validates CI freshness). |
| **`synhalees logos [--check]`** | Build logo data | Re-generates `docs/assets/logo-data.js` for canvas exports. |
| **`synhalees check`** | Run all gates | Runs logo check, leaderboard sync check, and Python bytecode compilation in one go. |
| **`synhalees kaggle <action>`** | Kaggle Benchmarks | Full suite to generate, push, run, and sync tasks with Kaggle. |

---

### 🌐 Kaggle Benchmarks Integration
SynhalEES includes built-in integration with Kaggle Benchmarks so you can run tasks on Kaggle's infrastructure without keeping your local terminal open:

```bash
# 1. Regenerate task files (17 modular tasks: 15 pillars + vision + audio)
synhalees kaggle gen

# 2. Upload tasks to Kaggle without running any evaluations
synhalees kaggle push            # uploads all 17 tasks
synhalees kaggle push audio      # uploads only the audio task
synhalees kaggle push 01_buddhist_culture

# 3. Start a server-side evaluation run on Kaggle
synhalees kaggle run synhalees-audio -m gemini-2.5-flash

# 4. Check status & logs
synhalees kaggle status synhalees-audio
synhalees kaggle logs synhalees-audio -m gemini-2.5-flash

# 5. Download results & import into local leaderboard
synhalees kaggle pull all                    # default: the 15 text tasks only (~145MB)
synhalees kaggle pull all --modality all     # + vision & audio (~730MB more; or vision | audio alone)
synhalees kaggle import synhalees-audio
```

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

**👉 [View the live benchmark results](https://synhalaai.github.io/SynhalEES-Benchmark/)**

| | |
|---|---|
| 🌐 **Live site** | <https://synhalaai.github.io/SynhalEES-Benchmark/> |
| 📁 **Source** | [`docs/`](docs/index.html) — static, GitHub Pages-ready site (no build step) |
| 📊 **Data** | [`submissions/*.csv`](submissions/README.md) |

Deployed automatically by the [GitHub Pages workflow](.github/workflows/pages.yml)
on every push to `main` (**Settings → Pages → Source: GitHub Actions**).
The site shows Text / Vision / Audio scores across all 15 pillars,
plus cost, token and latency telemetry per model.

Update the data after running models:

```bash
# 1. Compare every run under runs/ and print a pillar x model accuracy matrix
synhalees compare

# 2. Publish a model's scorecard to submissions/ and rebuild the site in one step
synhalees publish <model-slug>

# 3. Rebuild site data or verify CI status
synhalees build
synhalees build --check

# 4. Or preview the site locally with demo placeholder data (never commit demo data)
synhalees build --demo
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

Contributions are welcome — new models, evaluation data, code, or bug reports!
- **Adding new models:** Register taxonomy, vendor branding, and open-source status in `synhalees/models.json`, evaluate, and publish via `synhalees publish <model-slug>`.
- **Benchmark datasets:** Each pillar folder is small and modular — see [`CONTRIBUTING.md`](CONTRIBUTING.md) for data schemas and Definition of Done.

---

## 📜 License & Organization
* **License:** This project is licensed under the **[SynhalaAI Sovereign Research License (SSRL-1.0)](https://github.com/SynhalaAI/License-Hub/blob/main/SSRL.md)** - a sovereign public trust license open exclusively to Sri Lankan citizens and Sri Lankan academic/non-profit institutions, for non-commercial research only. Commercial use is permanently prohibited.
* **Maintained by:** **[SynhalaAI](https://github.com/SynhalaAI)** - An open-source initiative empowering Sinhala AI.

---
<p align="center">
  Licensed under <a href="https://github.com/SynhalaAI/License-Hub/blob/main/SSRL.md">SSRL-1.0</a> · Full license text: <a href="https://github.com/SynhalaAI/License-Hub">SynhalaAI/License-Hub</a> · Sri Lanka only, non-commercial research — commercial use permanently prohibited.
</p>
<p align="center">
  <a href="https://github.com/SynhalaAI/License-Hub/blob/main/assets/SSRL%20Banner.jpg">
    <img src="https://raw.githubusercontent.com/SynhalaAI/License-Hub/main/assets/SSRL%20Banner.jpg" alt="SynhalaAI Sovereign Research License (SSRL-1.0)" width="1000"/>
  </a>
</p>