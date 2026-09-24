"""SynhalEES benchmark task for Kaggle Benchmarks.

Push & run from the terminal (see the write-kaggle-benchmarks skill):

    kaggle b init -y                              # one-time credentials
    kaggle b t push kaggle/synhalees_task.py      # upload the task
    kaggle b t run <task-slug> --model <model>    # run server-side
    kaggle b t status <task-slug>                 # check progress
    kaggle b t publish <task-slug>                # public leaderboard

The kernel bootstraps the synhalees package from GitHub (requires internet
enabled on the benchmark kernel). Text modality only for now -- vision/audio
rows join once media files are committed under benchmark_data/.
"""

import subprocess
import sys

subprocess.run(
    [
        sys.executable, "-m", "pip", "install", "-q",
        "git+https://github.com/SynhalaAI/SynhalEES-Benchmark.git",
    ],
    check=True,
)

import kaggle_benchmarks as kbench

from synhalees import data as syn_data
from synhalees.evaluators import score_response

# pip installs only the synhalees package; benchmark_data/ lives at the repo
# root and does NOT ship inside site-packages. Bootstrap the CSVs from the
# public repo into /tmp and override data_dir.
import urllib.request
from pathlib import Path

REPO_RAW = "https://raw.githubusercontent.com/SynhalaAI/SynhalEES-Benchmark/main"
_PILLARS = """01_buddhist_culture
02_pali_gatha
03_classical_literature
04_kavi_sindu
05_sinhala_grammar
06_daily_spoken
07_figurative_sinhala
08_profanity_nuance
09_singlish_sms
10_regional_dialects
11_astrology_beliefs
12_general_knowledge
13_sri_lanka_law
14_culinary_kitchen
15_numbers_maths""".split()

DATA_ROOT = Path("/tmp/synhalees_benchmark_data")
for _p in _PILLARS:
    _f = DATA_ROOT / _p / "text.csv"
    if not _f.is_file():
        _f.parent.mkdir(parents=True, exist_ok=True)
        _f.write_text(
            urllib.request.urlopen(
                f"{REPO_RAW}/benchmark_data/{_p}/text.csv", timeout=60
            ).read().decode("utf-8"),
            encoding="utf-8",
        )

syn_data._DEFAULT_DATA_DIR = DATA_ROOT

_JUDGE_CRITERIA = (
    "The response conveys the same meaning as the reference answer: "
    "'{ground_truth}'. Wording may differ; the meaning must match.",
    "The response reads like a native Sri Lankan Sinhala speaker wrote it -- "
    "natural register and idiom, not translated-sounding English.",
)


@kbench.task(name="synhalees_item", store_task=False)
def synhalees_item(
    llm,
    id: str,
    prompt: str,
    ground_truth: str,
    eval_type: str,
    pillar: str = "",
) -> dict:
    """Score one SynhalEES text item (sub-task; not stored top-level)."""
    response = llm.prompt(prompt)

    judge_passed = None
    if eval_type == "llm_judge":
        report = kbench.assertions.assess_response_with_judge(
            criteria=[c.format(ground_truth=ground_truth) for c in _JUDGE_CRITERIA],
            response_text=response,
            judge_llm=kbench.judge_llm,
        )
        judge_passed = all(r.passed for r in report.results)

    ok = score_response(response, ground_truth, eval_type, judge_passed=judge_passed)
    return {"pillar": pillar, "id": id, "is_correct": ok}


@kbench.task(
    name="synhalees_benchmark",
    description="SynhalEES: overall Sinhala cultural score across 15 pillars.",
)
def synhalees_benchmark(llm) -> float:
    """Main task: every pillar's text rows -> overall accuracy in [0, 1]."""
    import pandas as pd

    rows = [
        {
            "pillar": pillar,
            "id": it.id,
            "prompt": it.prompt,
            "ground_truth": it.ground_truth,
            "eval_type": it.eval_type,
        }
        for pillar in syn_data.list_pillars()
        for it in syn_data.load_text(pillar)
    ]
    df = pd.DataFrame(rows)

    with kbench.client.enable_cache():
        runs = synhalees_item.evaluate(
            llm=[llm],
            evaluation_data=df,
            n_jobs=2,
            max_attempts=2,
            on_failure="continue",
            remove_run_files=True,
        )
    eval_df = runs.as_dataframe()
    correct = eval_df.result.str.get("is_correct").astype("boolean").fillna(False)
    return float(correct.mean())


# Required by the Kaggle Benchmarks workflow: the file executes the task.
synhalees_benchmark.run(kbench.llm)