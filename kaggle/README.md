# ⚖️ Kaggle Benchmarks Scoring System & 1:1 Local Parity Specification

> **Architectural Specification & Reference Guide**  
> This document defines how the **Kaggle Benchmarks** task scoring system works, how tasks are structured, and how the **Local Runner (`synhalees run`)** maintains strict **1:1 mathematical, semantic, and prompt-level parity** with Kaggle.

---

## 📌 1. Purpose & The Parity Mandate

In the SynhalEES Benchmark ecosystem:
1. **Frontier & Cloud Models** (Gemini 2.5/3, Claude 3.7, GPT-4o/5, etc.) are evaluated directly on Kaggle server infrastructure using Kaggle's official `kaggle_benchmarks` harness.
2. **Local, Quantized & Fine-Tuned Models** (Gemma, DeepSeek-R1, Qwen, Unsloth models, private endpoints) are evaluated on developer hardware or self-hosted endpoints using `synhalees run` and `synhalees gui`.

### ⚠️ The Core Problem
If Kaggle evaluates a prompt with one prefix, but the local runner uses another; or if Kaggle evaluates LLM Judge criteria with one strictness protocol while local uses another, the resulting scores on the public leaderboard would be **incomparable** (comparing apples to oranges).

### 🎯 The Parity Guarantee
**Every scoring algorithm, prompt augmentation, diacritic normalization rule, and evaluation decision tree is identical between Kaggle and Local.** A model tested locally under `synhalees run` executes the identical evaluation logic as a model evaluated on Kaggle's servers.

---

## 🏛️ 2. Kaggle Task Architecture (The 17 Tasks)

Kaggle Benchmarks requires independent, executable Python tasks. SynhalEES auto-generates **17 task files** into `kaggle/tasks/` via `synhalees kaggle gen` (`kaggle/generate_tasks.py`):

```text
kaggle/tasks/
├── 01_buddhist_culture.py     # Text modality: Buddhist Culture (15 items)
├── 02_pali_gatha.py            # Text modality: Pali Gatha & Stanzas (15 items)
├── ...                         # (15 total per-pillar text tasks)
├── 15_numbers_maths.py         # Text modality: Numbers & Maths (15 items)
├── vision.py                   # Vision modality: All 15 pillars (225 items)
└── audio.py                    # Audio modality: All 15 pillars (225 items)
```

### Execution Flow on Kaggle
Inside each Kaggle task:
1. **Bootstrap Phase:** Pip-installs `synhalees` from GitHub and bootstraps dataset rows from GitHub into `/tmp/synhalees_benchmark_data/`.
2. **Sub-Task (`synhalees_item`):** A `@kbench.task(store_task=False)` function that evaluates a single item (row).
3. **Master Task (`synhalees_task`):** Evaluates all runnable rows in parallel using `kbench.client.enable_cache()` and returns the aggregate mean accuracy in `[0.0, 1.0]`.

---

## 📐 3. The 4 Scoring Engines: Detailed Mechanics & Parity

SynhalEES benchmarks multimodal Sinhala using **4 distinct evaluation engines** defined in `synhalees.evaluators`.

| `eval_type` | Kaggle Implementation | Local Runner Implementation | Parity Mechanism |
|---|---|---|---|
| `exact_match` | `score_response(resp, truth, "exact_match")` | `score_response(resp, truth, "exact_match")` | Identical function imported directly from `synhalees.evaluators` |
| `classification` | Choice injection + single-label prompt + `score_response(..., "classification")` | `add_kaggle_classification_prompts()` + `score_response(..., "classification")` | Exact same sorted choice list and instruction template |
| `wer` | Tokenized Levenshtein distance with threshold $\le 0.3$ | Tokenized Levenshtein distance with threshold $\le 0.3$ | Exact same pure-Python Levenshtein algorithm |
| `llm_judge` | `kbench.assertions.assess_response_with_judge(criteria, ...)` | `build_judge_prompt()` + `parse_judge_verdict()` | Exact same 2 Kaggle rubric criteria + strict boolean conjunction |

---

### 3.1. Engine 1: `exact_match`

* **Intended Use:** Objective factual queries, single-word answers, historical facts, and entity names.
* **Normalization Protocol (`_normalize`):**
  - Collapses all multiple whitespace sequences, newlines, and tabs into a single ASCII space (`" ".join(text.split())`).
  - **Preserves Sinhala Unicode codepoints, diacritics, and Zero-Width Joiners (ZWJ/ZWNJ)** exactly as written (e.g. `ශ්‍රී`, `සම්භාව්‍ය`).
* **Scoring Rule:**
  $$\text{is\_correct} = (\text{norm\_response} == \text{norm\_truth}) \lor (\text{norm\_truth} \in \text{norm\_response})$$
* **Thinking Isolation:**
  - Extended reasoning / thinking models (e.g. DeepSeek-R1, Gemma, Gemini 2.5 Flash) may emit chain-of-thought blocks (`<think>...</think>`, `<thought>...</thought>`).
  - **`strip_thinking()`** strips all reasoning tags *before* matching, ensuring internal thoughts never contaminate the match.

---

### 3.2. Engine 2: `classification`

* **Intended Use:** Disambiguating categorical intent (e.g. Friendly Banter vs. Toxic Abuse in `08_profanity_nuance`, Acoustic Tone in audio).
* **The Dynamic Choice Discovery:**
  - Kaggle tasks dynamically discover the distinct set of ground truth labels for the active task scope:
    ```python
    choices = sorted({row["ground_truth"] for row in rows if row["eval_type"] == "classification"})
    choice_str = " / ".join(choices)
    ```
  - For text tasks, choices are scoped to the specific pillar.
  - For vision and audio tasks, choices are scoped to the modality.
* **Prompt Augmentation:**
  - The item's prompt is prepended with Kaggle's single-label instruction:
    ```text
    Choose exactly one label from [<choice_1> / <choice_2> / ...]. Reply with only the label. <original_prompt>
    ```
* **Local Parity:**
  - `synhalees.runner.add_kaggle_classification_prompts()` reproduces this exact scoping and prepending before dispatching items to the model.
* **Scoring Rule:**
  $$\text{is\_correct} = \text{norm\_truth} \in \text{norm\_response}$$

---

### 3.3. Engine 3: `wer` (Word Error Rate)

* **Intended Use:** Speech-to-Text (ASR) transcription for audio, and Optical Character Recognition (OCR) for vision.
* **Algorithm (Pure-Python Levenshtein):**
  - Text is tokenized into word lists by whitespace:
    ```python
    ref_tokens = _normalize(reference).split()
    hyp_tokens = _normalize(hypothesis).split()
    ```
  - Computes classic dynamic programming Levenshtein edit distance:
    $$\text{WER} = \frac{\text{LevenshteinDistance}(\text{ref\_tokens}, \text{hyp\_tokens})}{\text{len}(\text{ref\_tokens})}$$
* **Threshold:**
  $$\text{WER\_THRESHOLD} = 0.3$$
  - A hypothesis passes if $\text{WER} \le 0.3$ (i.e. at least $70\%$ word accuracy).
  - Empty reference handles edge cases: returns `0.0` if hypothesis is also empty, `1.0` otherwise.

---

### 3.4. Engine 4: `llm_judge`

* **Intended Use:** Subjective, empathetic, conversational, or culturally nuanced responses (e.g. everyday spoken chats, slang empathy, creative poetry explanations).

#### A. Kaggle Benchmarks Mechanism
On Kaggle, tasks call Kaggle's built-in assertion evaluator:
```python
report = kbench.assertions.assess_response_with_judge(
    criteria=[c.format(ground_truth=ground_truth) for c in _JUDGE_CRITERIA],
    response_text=response,
    judge_llm=kbench.judge_llm,
)
judge_passed = all(r.passed for r in report.results)
```

#### B. The 2 Definitive Assessment Criteria
Both Kaggle and Local use the exact same two criteria:
1. **Meaning Alignment:**
   > *"The response conveys the same meaning as the reference answer: '{ground_truth}'. Wording may differ; the meaning must match."*
2. **Native Linguistic Register:**
   > *"The response reads like a native Sri Lankan Sinhala speaker wrote it -- natural register and idiom, not translated-sounding English."*

#### C. Local Parity Protocol (`synhalees.evaluators`)
To replicate Kaggle's `assess_response_with_judge` locally without requiring external Kaggle dependencies, `synhalees.evaluators` provides:
1. **`build_judge_prompt(response, ground_truth)`**:
   Formats an impartial technical evaluator prompt specifying the exact JSON schema that Kaggle uses:
   ```json
   {
     "results": [
       {
         "criterion": "<criterion>",
         "passed": true,
         "reason": "<brief explanation>",
         "confidence": 95
       }
     ]
   }
   ```
2. **`parse_judge_verdict(text)`**:
   Strictly validates that the judge returned valid JSON with results matching both criteria.
   $$\text{judge\_passed} = \text{result}_1[\text{"passed"}] \land \text{result}_2[\text{"passed"}]$$
   If either criterion fails, the item fails.

---

## 🗂️ 4. Data Preprocessing & Modality Parity

### Minimal CSV Schemas & Default Prompt Injection
Neither Kaggle nor the local runner spams boilerplate prompts into dataset CSVs. Prompt injection is unified in `synhalees.data`:

| Modality | `eval_type` | Injected Default Prompt (if CSV question is empty) |
|---|---|---|
| **Vision** | `wer` | `"මේකේ තියෙන දේ අකුරෙන් ලියන්න."` (OCR prompt) |
| **Vision** | `classification` | `"මේක බලලා තෝරන්න: <labels>"` |
| **Vision** | `exact_match` / `llm_judge` | `"මේක බලලා උත්තර දෙන්න."` |
| **Audio** | `wer` | `"මේකේ ඇහෙන දේ අකුරෙන් ලියන්න."` (ASR prompt) |
| **Audio** | `classification` | `"මේකේ අහලා තෝරන්න: <labels>"` |
| **Audio** | `exact_match` / `llm_judge` | `"මේක අහලා උත්තර දෙන්න."` |

---

## 📊 5. Score Aggregation & Leaderboard Schema

Both Kaggle and Local runs produce scorecards in the identical schema consumed by `tools/build_leaderboard.py`:

```csv
model,provider,date,pillar,modality,score
```

* **Accuracy Calculation:**
  $$\text{Accuracy}_{\text{pillar, modality}} = \frac{\sum_{i=1}^{N} \text{is\_correct}_i}{N}$$
* **Score Percentage:**
  $$\text{Score} = \text{round}(\text{Accuracy} \times 100, 1)$$

### Kaggle Slim Archive (`kaggle/results/*.slim.csv`)
Kaggle download artifacts (`*.run.json` containing base64 images/audio) are extracted into tracked, lightweight `*.slim.csv` archives:
```text
model,provider,date,pillar,modality,score,cost_usd,tokens,latency_ms
```
When imported via `synhalees kaggle submit`, rows are canonicalized and placed directly into `submissions/<vendor>/<family>/<model>.csv`.

---

## 🧪 6. Automated Parity Test Suite

To prevent architectural drift between Kaggle and local runners, the test suite (`tests/test_cli.py`) enforces parity on every commit:

1. **`test_classification_prompts_match_kaggle_task_scopes`**:
   Verifies that `add_kaggle_classification_prompts()` produces the identical prompt string and scope as `synhalees_item` in `kaggle/generate_tasks.py`.
2. **`test_judge_prompt_and_parser_use_kaggle_criteria_report`**:
   Verifies that `build_judge_prompt` embeds the 2 Kaggle criteria, requests the Kaggle assessment JSON schema, and `parse_judge_verdict` strictly enforces all-pass criteria.
3. **`test_strip_thinking_and_exact_match_isolation`**:
   Verifies that chain-of-thought tokens (`<think>`) are stripped and never pollute exact match scoring.
4. **`synhalees check`**:
   Ensures all dataset schemas, logo data, and leaderboard models are synchronized.
