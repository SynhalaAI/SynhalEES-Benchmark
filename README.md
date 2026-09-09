# 🇱🇰 SynhalEES: Sinhala Linguistic & Cultural Alignment Benchmark

[![SynhalaAI](https://img.shields.io/badge/Maintained%20by-SynhalaAI-blue.svg)](https://github.com/SynhalaAI)
[![Hugging Face](https://img.shields.io/badge/%F0%9F%A4%97-Hugging%20Face-yellow)](https://huggingface.co/SynhalaAI)

> **Evaluating how authentically Large Language Models (LLMs) can speak, reason, understand, and behave like a native Sri Lankan Sinhala human.**

---

## 📖 Overview

Most modern LLM evaluations focus exclusively on raw cognitive intelligence (IQ) via direct English translations of datasets like MMLU or GSM8K. This results in models that produce stiff, robotic, "translationese" Sinhala and fail drastically in cultural context, pragmatic nuance, and conversational authenticity.

**SynhalEES** (developed by **[SynhalaAI](https://github.com/SynhalaAI)**) is the first multidimensional evaluation suite designed to assess an LLM's **Cultural IQ (CQ)**, **Linguistic Spectrum Depth**, and **Human-likeness** in Sinhala.

From sacred Pali roots, Buddhist traditions, and classical literature to modern day-to-day spoken registers, WhatsApp/SMS shorthand Singlish, and Gen Z street slang—**SynhalEES benchmarks the whole soul of the language.**

---

## 🏛️ The 15 Core Evaluation Dimensions

SynhalEES is structured into **5 Master Domains** covering 15 interconnected dimensions:

```
                                [ SynhalEES Benchmark Suite ]
                                              │
    ┌─────────────────┬───────────────────────┼───────────────────────┬─────────────────┐
    ▼                 ▼                       ▼                       ▼                 ▼
[ Domain 1 ]      [ Domain 2 ]            [ Domain 3 ]            [ Domain 4 ]      [ Domain 5 ]
Sacred & Heritage Aesthetics & Arts       Living Human Persona    Digital & Chat    Local Knowledge
• Buddhist Culture • Classical Folk Kavi   • Native Spoken Sinhala • Singlish & SMS  • Sri Lanka GK
• Pali & Dhamma   • Song Metaphors        • Gen Z & Street Slang  • Code-Switching  • Local Common
• Old Literature  • Idioms & Tropes       • Profanity & Banter    • Register Shifts   Sense
```

### Domain 1: Sacred & Classical Heritage (ආගමික හා සම්භාව්‍ය උරුමය)
* **1. Buddhist Culture & Rituals (බෞද්ධ සංස්කෘතිය):** Temple etiquette, clergy addressing formats (*ඔබවහන්සේ, දානය වළඳනවා*), merit-making, funeral customs.
* **2. Pali & Dhamma Intertextuality (පාලි සහ ධර්ම ඥානය):** Semantic interpretation of Pali stanzas (*ගාථා*), Tripitaka concepts, Buddhist philosophy.
* **3. Classical Literature (සම්භාව්‍ය සාහිත්‍යය):** Analysis of Jataka tales, Buthsarana, Amavathura, Saddharmarathnavaliya.
* **4. Old Sinhala & Inscriptions (පුරාණ සිංහල):** Historical grammar, Epigraphical Sinhala (සෙල්ලිපි), and linguistic transitions.

### Domain 2: Aesthetics, Poetry & Metaphor (කලා, කවි සහ ගීත සාහිත්‍යය)
* **5. Folk Poetry & Meters (ජන කවි):** Rhythm, rhyming (*එළිසමය*), and empathy in Pel Kavi, Pathal Kavi, Karaththa Kavi, and Sigiri Kurutu Gee.
* **6. Modern Lyricism & Metaphor (ගීත සාහිත්‍යය):** Understanding subtext, romanticism, and sorrow in literary works (Mahagama Sekara, Amaradeva, Premakeerthi).
* **7. Idioms & Proverbs (ප්‍රස්ථාව පිරුළු හා උපමා):** Contextual meaning of cultural figures of speech (*"ඉඟුරු දීලා මිරිස් ගත්තා වගේ"*).

### Domain 3: The Living Human Persona (දෛනික ව්‍යවහාරය සහ මනුස්ස ගතිය)
* **8. Spoken Naturalness & Empathy (කථන බස හා ලෙන්ගතුකම):** Eliminating robotic/written Sinhala (*ලිඛිත බස*) in casual conversation; using natural particles (*අනේ, නේද, කෝ, බං*).
* **9. Gen Z Vernacular & Internet Slang:** Current youth slang (*"Ado", "Vibe", "Full chora", "Cringe", "Scene ekak na"*).
* **10. Profanity, Boundaries & Banter Nuance (කුණුහරුප සහ සීමා):** Context awareness to distinguish between toxic hate speech vs. friendly colloquial banter.
* **11. Regional Dialects (ප්‍රාදේශීය ව්‍යවහාර):** Southern (*දකුණේ*), Up-Country (*නුවර*), and Rajarata (*රජරට*) variations.

### Domain 4: Digital Communication & Multilingual Mix (ඩිජිටල් බස)
* **12. Singlish Comprehension:** Latin-scripted Sinhala with irregular phonetic spelling variations.
* **13. Short-Messaging Shorthand (SMS/Chat):** Hyper-contracted chat forms (*"mk", "khmda", "cl ekk gnnm", "thx mcn"*).
* **14. Multi-ethnic & Colonial Loan Words:** Seamless usage of Tamil, Portuguese, Dutch, and Malay linguistic heritage embedded in Sinhala.

### Domain 5: Sri Lankan Grounded Knowledge (දේශීය සාමාන්‍ය දැනුම)
* **15. Local Common Sense & Bureaucracy:** Daily Sri Lankan life, food culture (*පදම, තෙම්පරාදුව*), traffic habits, government processes (Grama Niladhari), and history.

---

## ⚙️ Evaluation Pipeline Architecture

SynhalEES uses a **Dual-Engine Evaluation Framework**:

```
                              [ Model Generation ]
                                       │
                   ┌───────────────────┴───────────────────┐
                   ▼                                       ▼
       [ Engine A: Deterministic ]             [ Engine B: LLM-as-a-Judge ]
        • Pali / Dhamma MCQs                    • Conversational Naturalness
        • Sri Lankan General Knowledge          • Cultural Persona Alignment
        • Grammar & Literary Identification     • Banter vs Abuse Disambiguation
                   │                                       │
                   └───────────────────┬───────────────────┘
                                       ▼
                         [ SynhalEES Composite Index ]
```

### 1. Engine A: Objective & Log-Likelihood
* Integrated with EleutherAI's `lm-evaluation-harness`.
* Tests exact factual accuracy, Pali translation accuracy, and objective cultural MCQs using Perplexity / Log-Likelihood and Exact Match (EM).

### 2. Engine B: Cultural LLM-as-a-Judge
* For open-ended natural conversations, multi-turn roleplays, and Gen Z slang.
* Evaluated by an aligned state-of-the-art judge model using strict multi-dimensional rubrics (scored from 1 to 5 for *Naturalness, Cultural Appropriateness, and Pragmatic Flow*).

---

## 🗂️ Data Schema Example

All test cases are curated in standardized JSONL files:

```json
{
  "id": "SYN_TR03_SLANG_0102",
  "domain": "Living_Human_Persona",
  "sub_track": "gen_z_slang",
  "input": {
    "system": "ඔයා ලංකාවේ තරුණ කොල්ලෙක් වගේ කතා කරන්න.",
    "user": "අඩෝ ඊයේ මගේ presentation එක full chora වුණා බං. මට සිරාවටම ලැජ්ජා හිතුණා ඕයි.."
  },
  "eval_type": "llm_as_a_judge",
  "rubric": {
    "positive_traits": ["Uses casual spoken Sinhala particles", "Empathetic friendly tone"],
    "negative_traits": ["Robotic textbook Sinhala (ලිඛිත බස)", "Overly formal apologetic tone"],
    "reference_concept": "The user is feeling embarrassed about a ruined presentation; console them like a true friend."
  }
}
```

---

## 🚀 Quick Start

### 1. Installation

```bash
git clone https://github.com/SynhalaAI/SynhalEES.git
cd SynhalEES
pip install -e .
```

### 2. Run Evaluation on an Open-Weights Model

Run local evaluations using our integrated runner:

```bash
python -m synhalees.eval \
    --model_path meta-llama/Meta-Llama-3-8B-Instruct \
    --domains sacred,living,singlish \
    --output_dir ./results
```

### 3. Run API-Based Models (LLM-as-a-Judge)

```bash
export OPENAI_API_KEY="your-key"
python -m synhalees.judge \
    --eval_target gpt-4o-mini \
    --judge_model gpt-4o \
    --dataset datasets/synhalees_core.jsonl
```

---

## 🔮 Multimodal Roadmap: SynhalEES-Omni

SynhalaAI is actively expanding SynhalEES beyond text:

- [x] **v1.0 (Core):** 15-Dimensional Text, Literature, Slang, and Singlish benchmark.
- [ ] **v2.0 (Vision - VQA):** Recognizing Sri Lankan artifacts, traditional masks, ola-leaf manuscripts (*පුස්කොල පොත්*), road signboards, and local photo memes.
- [ ] **v3.0 (Audio - Speech):** Automatic Speech Recognition (ASR) for regional accents (Southern, Up-country), tone/sarcasm detection, and Kavi/Gatha chant comprehension.

---

## 🤝 Contributing to SynhalaAI

We strongly believe that cultural benchmarks should be created by the community, not just translated by machines.

* **Submit Test Cases:** Help curate authentic Sinhala prompts, idioms, and slang in `datasets/`.
* **Annotate & Validate:** Review open-ended responses against our cultural rubrics.
* **Code Contributions:** Improve our evaluation harnesses and metric calculators.

Please read our [CONTRIBUTING.md](CONTRIBUTING.md) to get started!

---

## 🏛️ Organized by SynhalaAI

Developed and maintained with ❤️ by **[SynhalaAI](https://github.com/SynhalaAI)** — empowering the Sinhala language in the era of Artificial General Intelligence.