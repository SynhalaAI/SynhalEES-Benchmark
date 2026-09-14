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
│   ├── 07_genz_slang/                         # Modern slang, TikTok text, voice notes
│   ├── 08_profanity_nuance/                   # Friendly banter vs. toxic abuse
│   ├── 09_singlish_sms/                       # WhatsApp shorthand & chat screenshots
│   ├── 10_regional_dialects/                  # Southern, Kandy, Rajarata accents
│   ├── 11_astrology_beliefs/                  # Horoscopes, Rahu time, Thovil masks
│   ├── 12_memes_humor/                        # Sinhala photo memes & dubbing parodies
│   ├── 13_multiethnic_loanwords/              # Tamil/Malay/Colonial loan terms
│   ├── 14_culinary_kitchen/                   # Cooking terminology & food dishes
│   └── 15_official_media/                     # Gazettes, official letters, news scripts
│
├── synhalees/                                 # Core Python Package
│   ├── __init__.py                            # Public API (env helpers, version)
│   ├── data/                                  # Data Loaders & Registry
│   │   └── __init__.py
│   ├── models/                                # Model Adapters (HF, APIs, Whisper)
│   │   └── __init__.py
│   ├── evaluators/                            # Scoring Engines (Exact Match, Judge, WER)
│   │   └── __init__.py
│   └── env.py                                 # Kaggle/Local environment detector
│
├── run_benchmark.py                           # CLI Entry point
├── CONTRIBUTING.md                            # Contribution guide
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
| `06_daily_spoken` | Text, Vision, Audio | `llm_judge` | `llm_judge` | `wer` (ASR) |
| `07_genz_slang` | Text, Vision, Audio | `llm_judge` | `llm_judge` | `wer` (ASR) |
| `08_profanity_nuance` | Text, Vision, Audio | `classification` | `classification` | `classification` (Tone) |
| `09_singlish_sms` | Text, Vision, Audio | `llm_judge` | `wer` (OCR) | `wer` |
| `10_regional_dialects` | Text, Vision, Audio | `exact_match` | `exact_match` | `wer` (Regional ASR) |
| `11_astrology_beliefs` | Text, Vision, Audio | `exact_match` | `exact_match` | `classification` |
| `12_memes_humor` | Text, Vision, Audio | `llm_judge` | `llm_judge` | `llm_judge` |
| `13_multiethnic_loanwords` | Text, Vision, Audio | `exact_match` | `exact_match` | `wer` |
| `14_culinary_kitchen` | Text, Vision, Audio | `exact_match` | `exact_match` | `wer` |
| `15_official_media` | Text, Vision, Audio | `llm_judge` | `wer` (Doc OCR) | `wer` |

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
> (`eval_type = "wer"`), the `question` column may be left **empty** — the
> benchmark loader then injects the standard OCR prompt
> **`මේ රූපයේ තියෙන පාඨය හරියටම ලියන්න.`** automatically, so contributors
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
id,audio_file,ground_truth,eval_type
AUD_10_001,southern_accent_01.mp3,"මොකෝ බොල තෝ දුවන්නේ",wer
AUD_08_003,shouting_anger_03.mp3,"aggressive_abuse",classification
```