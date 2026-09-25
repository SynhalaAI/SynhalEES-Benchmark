# `submissions/` — published leaderboard sources

One CSV per model. This folder is the **committed source of truth** for the
public leaderboard in `docs/`, and `tools/build_leaderboard.py` reads it by
default (no arguments needed).

```text
submissions/<model-slug>.csv
```

Schema — exactly the header written by `results.save_submission()`:

```csv
model,provider,date,pillar,modality,score
gemini-3.5-flash,gemini,2026-09-24,05_sinhala_grammar,text,86.7
```

| column | meaning |
| --- | --- |
| `model` | run model spec id (`runs/<model-slug>/` folder name works) |
| `provider` | API host used for the run (`gemini`, `openrouter`, `ollama`, ...) |
| `date` | run date `YYYY-MM-DD`; the site's "Last updated" is the newest one found |
| `pillar` | pillar slug, e.g. `05_sinhala_grammar` |
| `modality` | `text`, `vision`, or `audio` |
| `score` | 0-100 accuracy for that pillar + modality cell |

## Rules

1. **One file per model.** Never merge two models into one CSV.
2. **Only publishable runs.** Do not commit numbers produced with a personal or
   paid API key that the maintainer cannot re-verify, or that a provider's terms
   keep private. Public runs only (own hardware, Kaggle, publishable free tiers).
3. **Never hand-edit the generated files.** `docs/assets/data/leaderboard.json`
   and `leaderboard.js` are generated; edit the CSV and regenerate:

   ```bash
   python tools/build_leaderboard.py           # reads submissions/*.csv
   python tools/build_leaderboard.py --check   # must pass (CI enforces this)
   ```

   Commit the CSV **and** the regenerated `docs/assets/data/*` in the same change.
4. Raw run artifacts stay in `runs/` (gitignored). Copy only the scorecard here,
   renamed after the model slug.

## Empty is a valid state

With no CSVs, the tool writes an **empty** leaderboard (`models: []`) and the
site shows its empty-state note — it never fabricates placeholder rows.
For a local preview with fake data use `--demo` (never commit demo output).
