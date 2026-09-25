# AGENTS.md — SynhalEES Benchmark (Developer / AI-Agent Conventions)

This file governs how AI agents and contributors should modify this repository.
Always read this file before making changes.

---

## 📌 Critical Rule: Keep `STRUCTURE.md` in sync

> **If the project folder structure changes — files added, removed, renamed, moved,
> or new directories created — you MUST update `STRUCTURE.md` (section 1 "Directory Tree")
> in the same change. Do not ship a structure change without the doc update.**

This applies to:

- ✅ New pillar folders under `benchmark_data/` (e.g. renaming `06_daily_spoken/`)
- ✅ New top-level directories or files (e.g. `synhalees/`, `run_benchmark.py`, `LICENSE`)
- ✅ New sub-structures inside existing folders (e.g. `vision/images/`, `audio/mp3s/`)
- ✅ File renames / moves that change the tree shape

When in doubt: update the tree. `STRUCTURE.md` is the authoritative blueprint —
an outdated one misleads every future contributor and every automated tool.

---

## 🏛️ Pillar Naming Must Stay Aligned (3-way)

The 15 pillar names must be identical across **all three places**:

1. `README.md` → "The 15 Core Pillars" list
2. `STRUCTURE.md` → section 1 directory tree + section 4 cheat-sheet
3. `benchmark_data/<slug>/` → actual folder names

Rules:

- The folder slug (e.g. `10_regional_dialects`) is the **canonical ID**.
- Display titles use the format from `README.md` after a long dash `— `.
- If you rename a pillar anywhere, rename it in **all three places** in the same commit.

## 🧾 Dataset & `eval_type` Conventions

- Data lives **Pillar-First** (never a single mega-CSV).
- Each pillar folder holds `text.csv`, `vision/`, and/or `audio/` per the schemas in `STRUCTURE.md`.
- `eval_type` must be one of exactly: `exact_match`, `llm_judge`, `wer`, `classification`.
- Assign `eval_type` using the decision tree in `STRUCTURE.md` section 3.

## 🖼️ Provider Logos & `docs/assets/logo-data.js`

The static site embeds provider logos and the brand icon as data URLs in
`docs/assets/logo-data.js` so canvas PNG exports keep their icons on `file://`
pages. **This file is generated - never edit it by hand.**

- **Add a logo:** drop `<slug>.svg` into `docs/assets/logos/`, add the
  provider -> slug mapping in **both** `docs/assets/chart.js` and
  `docs/assets/app.js` (`PROVIDER_LOGOS`), then run
  `python tools/build_logo_data.py`.
- **Edit/replace a logo** (`docs/assets/logos/*.svg` or `docs/assets/icon.png`):
  run `python tools/build_logo_data.py` and commit the regenerated
  `logo-data.js` **in the same commit** as the source change.
- **Staleness detection:** `logo-data.js` carries a `SOURCE-SHA256` checksum of
  its sources. CI (`.github/workflows/logo-data.yml`) runs
  `python tools/build_logo_data.py --check` on every push/PR and **fails** when
  the embedded data is stale. Run the check locally before committing.
- Pillow is optional (it only downscales raster-wrapped SVGs);
  `pip install pillow` keeps the generated file small.

## 📊 Leaderboard Data Is Generated Too (`docs/assets/data/`)

`docs/assets/data/{pillars,leaderboard}.{json,js}` are **generated** by
`tools/build_leaderboard.py` from the committed `submissions/*.csv`
(one CSV per model - see `submissions/README.md`). **Never hand-edit them.**

- **Publishing a run:** copy `runs/<model-slug>/submission.csv` to
  `submissions/<model-slug>.csv`, then run `python tools/build_leaderboard.py`
  and commit the CSV **and** the regenerated `docs/assets/data/*` together.
  (`python -m synhalees publish <model-slug>` does all three steps).
- **Only publishable runs** go in `submissions/`: no numbers produced with a
  personal or paid API key that the project cannot re-verify or publish.
- **Staleness detection:** CI (`.github/workflows/leaderboard.yml`) runs
  `python tools/build_leaderboard.py --check` on every push/PR and **fails**
  when the committed data no longer matches `submissions/`. Run the check
  locally before committing.
- **No CSVs = empty leaderboard** (`models: []`). `--demo` output is a local
  preview only and must never be committed.

## 🧹 Output Hygiene (nothing lands in the repo root)

Every command that produces files must target a dedicated **gitignored**
outputs folder. The repo root keeps only the tracked top-level files listed
in `STRUCTURE.md` -- no scratch `.txt` / `.log` / `.csv` dumps, ever.

| output | folder | produced by |
| --- | --- | --- |
| local benchmark runs | `runs/<model-slug>/` | `python run_benchmark.py ...` (defaults) |
| cross-run scorecard + matrix | `runs/all_submissions.csv` | `python tools/compare_runs.py` |
| Kaggle Benchmarks artifacts | `kaggle-results/` | `kaggle b t download <task-slug> -o kaggle-results` |

- **`kaggle b t download` writes into the current directory when `-o` is
  omitted -- always pass `-o kaggle-results`.**
- The only output that leaves these folders is a scorecard copy at
  `submissions/<model-slug>.csv` (committed, see the rule above).
- Remove ad-hoc test outputs when finishing a task; `git status --short` must
  show no unexpected entries at the root.
- **No helper/generator scripts are saved into the project.** Run one-off edits
  and generation as inline commands (`py -3.13 -c "..."` or PowerShell); if a
  script file is genuinely unavoidable, write it outside the repo
  (`$env:TEMP`) and delete it afterwards -- never leave a scratch `.py` at the
  root or in a tracked folder.

---

## ✍️ Sinhala Content

- Keep Sinhala text **Unicode (UTF-8)** at all times — `.gitattributes`/`.editorconfig`
  should not normalize to ASCII. Never transliterate to "Singlish" in ground truths
  unless the pillar is `09_singlish_sms` (and even then, the source data is the spec).
- Preserve diacritics and ZWJ/ZWNJ (e.g. `සම්භාව්‍ය`, `ශාන්තිකර්ම`) exactly as written.
- Example rows in `STRUCTURE.md` schemas are normative examples — keep them valid CSV.

## 🧪 Docs Rule

- `README.md` is user-facing; `STRUCTURE.md` is the technical spec.
- Any README claim that references structure must be verifiable against `STRUCTURE.md`.

## ✅ Definition of Done (checklist)

- [ ] Modified/added folders are reflected in `STRUCTURE.md` section 1 tree
- [ ] Pillar names aligned across README.md, STRUCTURE.md, and folder slugs
- [ ] CSVs (if added) match the schemas in `STRUCTURE.md` section 5
- [ ] `eval_type` values are from the allowed set of 4
- [ ] Logo/icon changes regenerate `docs/assets/logo-data.js` (`python tools/build_logo_data.py --check` passes)
- [ ] `submissions/*.csv` changes regenerate `docs/assets/data/*` (`python tools/build_leaderboard.py --check` passes)
- [ ] Sinhala text is valid UTF-8 with diacritics preserved
- [ ] No new files at the repo root (run outputs -> `runs/`, Kaggle pulls -> `kaggle-results/`)
