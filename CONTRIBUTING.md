# Contributing to SynhalEES

Thanks for your interest in improving the Sinhala benchmark! 🎉
All contributions — data, code, or docs — are welcome.
**Please read [`AGENTS.md`](AGENTS.md) first**: it defines the rules every change must follow.

> 🧠 **Our mission:** measuring how well AI models **think as Sinhalese** — reasoning
> natively inside the Sinhala language and culture, not translating English thoughts.
> Every row you contribute teaches a model to think a little more like a native
> Sinhala human.

> 📏 **Scoring yardstick:** a row's `ground_truth` must be what a **native-born Sri Lankan
> Sinhala human would answer from a lifetime of living the language** — the context a person
> gathers from birth (jokes, rituals, tone, dialects, pragmatics). High scores require **real,
> practical data + cultural awareness (සංස්කෘතික බුද්ධිය) + genuine reasoning** together; a
> translated-English model must not be able to top a row without that lived Sinhala context.

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
  - `vision/vision.csv` → `id,image_file,ground_truth,eval_type` (minimal schema! `question` column is optional)
  - `audio/audio.csv` → `id,audio_file,ground_truth,eval_type` (minimal schema! `question` column is optional)
- **🚫 Do NOT spam repetitive prompt columns in CSVs:**
  - When contributing new pillars or media rows, avoid repeating boilerplate questions in CSV files.
  - Rely on the loader script (`synhalees.data`) to inject generic prompts automatically:
    - Vision `wer` (OCR) rows receive `"මේකේ තියෙන දේ අකුරෙන් ලියන්න."`
    - Vision `classification` rows dynamically receive `"මේක බලලා තෝරන්න: "` + unique candidate options extracted from the pillar's `ground_truth` values.
    - Vision comprehension rows (`exact_match`, `llm_judge`) receive `"මේක බලලා උත්තර දෙන්න."`
    - Audio `wer` rows receive `"මේකේ ඇහෙන දේ අකුරෙන් ලියන්න."`
    - Audio `classification` rows dynamically receive `"මේක අහලා තෝරන්න: "` + unique candidate options extracted from the pillar's `ground_truth` values.
    - Audio comprehension rows (`exact_match`, `llm_judge`) receive `"මේක අහලා උත්තර දෙන්න."`
  - Only provide a custom `question` if the specific row genuinely requires a distinct prompt.
- `eval_type` must be one of exactly: `exact_match`, `llm_judge`, `wer`, `classification`
  (decision tree: [`STRUCTURE.md`](STRUCTURE.md) section 3).
- Keep Sinhala text in **Unicode UTF-8** — preserve diacritics and ZWJ/ZWNJ exactly.
- Keep each PR small — one pillar per PR makes review easy.

### 2. Code contributions

- `synhalees/` → loaders (`data/`), model adapters (`models/`),
  scoring engines (`evaluators/`), env helpers (`env.py`).
- `run_benchmark.py` → CLI improvements.
- New code must be importable and pass `python -m py_compile`.

### 4. Publishing benchmark results

The leaderboard in `docs/` is built from `submissions/` - one
`<model-slug>.csv` per model (schema `model,provider,date,pillar,modality,score`,
`score` = 0-100). After a run:

```bash
python tools/compare_runs.py                 # sanity-check every run under runs/
# copy runs/<model-slug>/submission.csv -> submissions/<model-slug>.csv
python tools/build_leaderboard.py            # regenerate docs/assets/data/*
python tools/build_leaderboard.py --check    # must pass (CI runs this)
```

`python -m synhalees publish <model-slug>` does the copy + rebuild +
`--check` in one step (and `python -m synhalees check` runs every gate).

Only **publishable** runs belong in `submissions/`: no results produced with a
personal or paid API key that the project cannot re-verify or publish. Never
hand-edit `docs/assets/data/*` - it is generated. See
[`submissions/README.md`](submissions/README.md) for the full rules.

Kaggle Benchmarks runs store their artifacts server-side; pull them into the
dedicated outputs folder with
`kaggle b t download <task-slug> -o kaggle-results`. Download output never
lands in the repo root (`runs/` is ignored; `kaggle-results/` is the committed
Kaggle run archive).

### 3. Issues, ideas & questions

- Open a [GitHub Issue](https://github.com/SynhalaAI/SynhalEES-Benchmark/issues)
  with a clear title (e.g. "Add TXT samples for pillar 09").

---

## ✅ Definition of Done

Before opening a PR, check:

- [ ] `STRUCTURE.md` section 1 tree updated if the folder structure changed
- [ ] Pillar names aligned across `README.md`, `STRUCTURE.md`, and folder slugs
- [ ] CSVs match `STRUCTURE.md` section 5 schemas (no redundant prompt column spam; loader scripts handle generic & classification prompts); `eval_type` from the allowed set of 4
- [ ] Sinhala text is valid UTF-8 with diacritics preserved
- [ ] `submissions/*.csv` changes regenerate `docs/assets/data/*` (`python tools/build_leaderboard.py --check` passes)