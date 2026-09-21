# NOTICE - Third-Party Content Attribution

SynhalEES includes some media files (images and audio) sourced from **publicly
available third-party materials**. These files are **not** owned by SynhalaAI /
SKY PRODUCTION and are **not** licensed under SORL. They are redistributed here
solely for **non-commercial research and evaluation purposes**; all rights remain
with their respective owners and the original licenses/terms apply.

---

## What SORL covers vs. what it does not

| Covered by SORL v1.0 (see `LICENSE.md`) | NOT covered (third-party) |
|---|---|
| All source code (`synhalees/`, `run_benchmark.py`, `kaggle/`, `tools/`) | Media files listed in the register below |
| `text.csv` datasets and authored CSV metadata | Any image/audio sourced from public third-party material |
| Original team recordings and images | |

---

## Attribution Register

> **Status: audit in progress.** Every third-party asset in
> `benchmark_data/*/vision/images/` and `benchmark_data/*/audio/mp3s/` is being
> catalogued with its source, original license, and required attribution.
> Entries are added as they are verified.

| # | File | Pillar | Type | Source | Original License | Required Attribution |
|---|------|--------|------|--------|------------------|----------------------|
| - | _pending audit_ | - | - | - | - | - |

---

## Takedown / Credit Requests

If you are the rights holder of any asset included here and would like it
credited differently or removed, please open an issue:

**https://github.com/SynhalaAI/SynhalEES-Benchmark/issues**

We respond promptly and remove or re-credit content on request.

---

## Rules for Contributors

When adding media to any pillar:

1. **Self-created** content (your own photos / recordings) - goes under SORL v1.0.
2. **CC0 / Public Domain** content - allowed; record the source below.
3. **CC-BY / CC-BY-SA** content - allowed; record source + author + license below.
4. **Anything else** (no clear license, found on the web) - do **not** commit it.
5. **Never** apply SORL to third-party content, and never remove an existing
   attribution entry.

> Planned improvement: `vision.csv` and `audio.csv` schemas will gain `source`
> and `license` columns so provenance lives next to every row.