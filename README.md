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
results.save_submission("submission.csv")
```

### 4. Run from the CLI
```bash
python run_benchmark.py --model ollama:llama3.1:8b --pillars 01_buddhist_culture
```

### Optional: Kaggle leaderboard
Want a public leaderboard on Kaggle Benchmarks instead? The task file in
[`kaggle/synhalees_task.py`](kaggle/synhalees_task.py) is push-ready -- with a
Kaggle API token (`kaggle.json`) the whole flow runs from the terminal:
`kaggle b t push` -> `kaggle b t run` -> `kaggle b t publish`.
Runs are server-side, so closing your laptop will not interrupt them.

For per-pillar leaderboards, `kaggle/tasks/` holds **17 generated task files**
(15 pillars on text + 1 vision + 1 audio); regenerate with
`python kaggle/generate_tasks.py` and push all with
`Get-ChildItem kaggle/tasks/*.py | % { kaggle b t push $_.FullName }`.
Note: vision/audio tasks need the media files committed first.

### Crash-safe resume
Every item is appended to `runs/checkpoint.jsonl` the moment it completes.
If the run is interrupted (Ctrl+C, quota, network), **rerun the same command** --
completed items are skipped automatically. Use `--fresh` to start over.

---

## 🤝 Contributing

Contributions are welcome — data, code, or feedback. See
[`CONTRIBUTING.md`](CONTRIBUTING.md) for the full guide and Definition of Done.
Every pillar folder is a small, reviewable PR — no giant CSVs.

---

## 📜 License & Organization
* **License:** This project is licensed under the **[SynhalaAI Open Research License (SORL)](https://github.com/SynhalaAI/SynhalaAI-Open-Research-License-SORL)** - research & non-commercial use only, with attribution.
* **Maintained by:** **[SynhalaAI](https://github.com/SynhalaAI)** - An open-source initiative empowering Sinhala AI.
