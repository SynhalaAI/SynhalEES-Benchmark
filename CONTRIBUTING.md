# Contributing to SynhalEES

Thanks for your interest in improving the Sinhala benchmark! 🎉
All contributions — data, code, or docs — are welcome.
**Please read [`AGENTS.md`](AGENTS.md) first**: it defines the rules every change must follow.

> 🧠 **Our mission:** measuring how well AI models **think as Sinhalese** — reasoning
> natively inside the Sinhala language and culture, not translating English thoughts.
> Every row you contribute teaches a model to think a little more like a native
> Sinhala human.

---

## 🤝 How to contribute

### 1. Data contributions (the biggest need!)

> 🧑 **Human-first:** Only human-created data is accepted.
> **Do NOT use AI to generate data** — no AI-written or synthetic rows, period.
> Every row must be written/collected by a real person.

> 🧠 **What makes a good row?** Contribute items that test **native Sinhala thinking** —
> idioms, hidden meanings (ව්‍යංග්‍යාර්ථ), banter-vs-abuse tone, dialect logic, cultural
> instincts, everyday empathy — not translated-English trivia. If a translated-English
> model could answer your row correctly *without* understanding Sinhala culture, the
> row isn't testing what this benchmark exists for.

- Pick any pillar folder under `benchmark_data/<slug>/`.
- Add rows to the pillar's CSVs following the exact schemas in
  [`STRUCTURE.md`](STRUCTURE.md) section 5:
  - `text.csv` → `id,prompt,ground_truth,eval_type`
  - `vision/vision.csv` → `id,image_file,question,ground_truth,eval_type`
  - `audio/audio.csv` → `id,audio_file,ground_truth,eval_type`
- `eval_type` must be one of exactly: `exact_match`, `llm_judge`, `wer`, `classification`
  (decision tree: [`STRUCTURE.md`](STRUCTURE.md) section 3).
- Keep Sinhala text in **Unicode UTF-8** — preserve diacritics and ZWJ/ZWNJ exactly.
- Keep each PR small — one pillar per PR makes review easy.

### 2. Code contributions

- `synhalees/` → loaders (`data/`), model adapters (`models/`),
  scoring engines (`evaluators/`), env helpers (`env.py`).
- `run_benchmark.py` → CLI improvements.
- New code must be importable and pass `python -m py_compile`.

### 3. Issues, ideas & questions

- Open a [GitHub Issue](https://github.com/SynhalaAI/SynhalEES-Benchmark/issues)
  with a clear title (e.g. "Add TXT samples for pillar 09").

---

## ✅ Definition of Done

Before opening a PR, check:

- [ ] `STRUCTURE.md` section 1 tree updated if the folder structure changed
- [ ] Pillar names aligned across `README.md`, `STRUCTURE.md`, and folder slugs
- [ ] CSVs match `STRUCTURE.md` section 5 schemas; `eval_type` from the allowed set of 4
- [ ] Sinhala text is valid UTF-8 with diacritics preserved