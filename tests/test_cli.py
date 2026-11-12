"""Comprehensive CLI and backward-compatibility tests (pure stdlib unittest)."""

from __future__ import annotations

import contextlib
import csv
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from synhalees import cli, kaggle_ops  # noqa: E402


def _run_cli(*argv: str) -> tuple[int, str, str]:
    """Invoke synhalees.cli.main in-process and capture (rc, stdout, stderr)."""
    out_buf = io.StringIO()
    err_buf = io.StringIO()
    with contextlib.redirect_stdout(out_buf), contextlib.redirect_stderr(err_buf):
        try:
            rc = cli.main(["--no-color", *argv])
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
            rc = code
    return rc, out_buf.getvalue(), err_buf.getvalue()


class TestTopLevelAndHelp(unittest.TestCase):
    def test_bare_and_help_print_grouped_overview(self) -> None:
        rc0, out0, _ = _run_cli()
        rc1, out1, _ = _run_cli("--help")
        self.assertEqual(rc0, 0)
        self.assertEqual(rc1, 0)
        for text in (out0, out1):
            self.assertIn("Evaluate & Compare", text)
            self.assertIn("Inspect & Discover", text)
            self.assertIn("Leaderboard & Static Site", text)
            self.assertIn("Kaggle Benchmarks", text)
            self.assertIn("Common Workflows", text)

    def test_help_routing_and_version(self) -> None:
        rc, out, _ = _run_cli("help", "kaggle", "pull")
        self.assertEqual(rc, 0)
        self.assertIn("synhalees kaggle pull", out)
        self.assertIn("--modality", out)

        rc_v, out_v, _ = _run_cli("--version")
        self.assertEqual(rc_v, 0)
        self.assertIn("synhalees", out_v)

    def test_no_color_strips_ansi(self) -> None:
        rc, out, _ = _run_cli("status")
        self.assertEqual(rc, 0)
        self.assertNotIn("\x1b[", out)

    def test_unknown_command_and_strict_flags_fail(self) -> None:
        rc, _, err = _run_cli("not-a-command")
        self.assertEqual(rc, 2)
        self.assertTrue(err)

        rc2, _, err2 = _run_cli("build", "--not-a-flag")
        self.assertEqual(rc2, 2)
        self.assertTrue(err2)

    def test_group_commands_without_action_print_help(self) -> None:
        rc_site, out_site, _ = _run_cli("site")
        self.assertEqual(rc_site, 0)
        self.assertIn("publish", out_site)

        rc_kg, out_kg, _ = _run_cli("kaggle")
        self.assertEqual(rc_kg, 0)
        self.assertIn("coverage", out_kg)


class TestInspectCommands(unittest.TestCase):
    def test_status_and_guide(self) -> None:
        rc, out, _ = _run_cli("status")
        self.assertEqual(rc, 0)
        self.assertIn("Dataset", out)
        self.assertIn("15 pillars", out)
        self.assertIn("Submissions", out)
        self.assertIn("Site Sync", out)
        self.assertIn("Kaggle Slim", out)

        for topic in ("all", "local", "kaggle", "colab", "site"):
            rc_g, out_g, _ = _run_cli("guide", topic)
            self.assertEqual(rc_g, 0)
            self.assertIn("SynhalEES Workflow Guide", out_g)

    def test_pillars_list_and_detail(self) -> None:
        rc, out, _ = _run_cli("pillars", "--eval-types")
        self.assertEqual(rc, 0)
        self.assertIn("01_buddhist_culture", out)
        self.assertIn("15_numbers_maths", out)
        self.assertIn("225", out)

        rc_one, out_one, _ = _run_cli("pillars", "05")
        self.assertEqual(rc_one, 0)
        self.assertIn("05_sinhala_grammar", out_one)
        self.assertIn("TXT_05_001", out_one)

        rc_bad, _, err_bad = _run_cli("pillars", "nonexistent_pillar_xyz")
        self.assertEqual(rc_bad, 1)
        self.assertIn("No pillar matched", err_bad)

    def test_models_filtering(self) -> None:
        rc, out, _ = _run_cli("models", "--provider", "xai")
        self.assertEqual(rc, 0)
        self.assertIn("grok-4.5-0708", out)
        self.assertIn("T,V,A", out)

        rc_bad, _, _ = _run_cli("models", "--published", "--unpublished")
        self.assertNotEqual(rc_bad, 0)


class TestSiteAndChecks(unittest.TestCase):
    def test_run_command_plumbing(self) -> None:
        captured = {}

        def fake_run_from_args(args):
            captured["model"] = args.model
            captured["modality"] = args.modality
            captured["pillars"] = args.pillars
            captured["fresh"] = args.fresh
            return 0

        import run_benchmark
        with mock.patch.object(run_benchmark, "run_from_args", side_effect=fake_run_from_args):
            rc, _, _ = _run_cli(
                "run", "--model", "ollama:llama3.1:8b",
                "--modality", "text", "--pillars", "01_buddhist_culture", "--fresh",
            )
        self.assertEqual(rc, 0)
        self.assertEqual(captured, {
            "model": "ollama:llama3.1:8b",
            "modality": "text",
            "pillars": "01_buddhist_culture",
            "fresh": True,
        })

    def test_site_and_top_level_checks(self) -> None:
        for cmd in (
            ("check",),
            ("site", "check"),
            ("build", "--check"),
            ("site", "build", "--check"),
            ("logos", "--check"),
            ("site", "logos", "--check"),
        ):
            rc, _, _ = _run_cli(*cmd)
            self.assertEqual(rc, 0, f"failed command: {cmd}")

    def test_compare_and_publish_isolated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            runs_dir = tmp_path / "runs"
            run_dir = runs_dir / "gemini-2.5-flash"
            run_dir.mkdir(parents=True)
            subs_dir = tmp_path / "submissions"

            (run_dir / "checkpoint.jsonl").write_text(
                json.dumps({
                    "key": "01_buddhist_culture/text/TXT_01_001",
                    "pillar": "01_buddhist_culture",
                    "modality": "text",
                    "is_correct": True,
                }) + "\n",
                encoding="utf-8",
            )
            (run_dir / "meta.json").write_text(
                json.dumps({
                    "model": "gemini-2.5-flash",
                    "provider": "gemini",
                    "date": "2026-11-12",
                    "items": 1,
                    "overall": 100.0,
                    "errors": 0,
                }),
                encoding="utf-8",
            )
            (run_dir / "submission.csv").write_text(
                "model,provider,date,pillar,modality,score\n"
                "gemini-2.5-flash,gemini,2026-11-12,01_buddhist_culture,text,100.0\n",
                encoding="utf-8",
            )

            rc_cmp, out_cmp, _ = _run_cli("compare", "--runs-dir", str(runs_dir))
            self.assertEqual(rc_cmp, 0)
            self.assertTrue((runs_dir / "all_submissions.csv").is_file())
            self.assertIn("Gemini 2.5 Flash", out_cmp)

            with mock.patch.object(cli, "RUNS", runs_dir), \
                 mock.patch.object(kaggle_ops, "SUBMISSIONS", subs_dir):
                rc_dry, out_dry, _ = _run_cli("publish", "gemini-2.5-flash", "--dry-run")
                self.assertEqual(rc_dry, 0)
                self.assertIn("would copy", out_dry)
                self.assertFalse((subs_dir / "google" / "gemini" / "gemini-2.5-flash.csv").exists())

                rc_pub, _, _ = _run_cli("site", "publish", "gemini-2.5-flash", "--no-build")
                self.assertEqual(rc_pub, 0)
                self.assertTrue((subs_dir / "google" / "gemini" / "gemini-2.5-flash.csv").is_file())


class TestKaggleOperations(unittest.TestCase):
    def test_help_pages_not_hijacked_by_forwarding(self) -> None:
        for args in (
            ("kaggle", "download", "--help"),
            ("kaggle", "download", "all", "--help"),
            ("kaggle", "pull", "--help"),
            ("kaggle", "status", "all", "--help"),
            ("kaggle", "coverage", "--help"),
            ("kaggle", "submit", "--help"),
            ("run", "--help"),
            ("compare", "--help"),
            ("build", "--help"),
            ("logos", "--help"),
        ):
            rc, out, _ = _run_cli(*args)
            self.assertEqual(rc, 0, f"--help failed for {args}")
            self.assertIn("usage: synhalees", out, f"bad usage line for {args}")

    def test_kaggle_gen_dry_run_and_coverage(self) -> None:
        rc_gen, out_gen, _ = _run_cli("kaggle", "gen", "--dry-run")
        self.assertEqual(rc_gen, 0)
        self.assertIn("17 task files", out_gen)

        rc_cov, out_cov, _ = _run_cli("kaggle", "coverage", "--no-remote", "--provider", "grok")
        self.assertEqual(rc_cov, 0)
        self.assertIn("grok-4.7", out_cov)

        rc_st, out_st, _ = _run_cli("kaggle", "status", "--no-remote")
        self.assertEqual(rc_st, 0)
        self.assertIn("Kaggle Benchmark Coverage", out_st)

        rc_bad, _, _ = _run_cli("kaggle", "status", "all", "--bogus-flag")
        self.assertNotEqual(rc_bad, 0)

    def test_kaggle_pull_dry_run_and_output_hygiene(self) -> None:
        rc, out, _ = _run_cli("kaggle", "pull", "all", "--modality", "vision|audio", "--dry-run")
        self.assertEqual(rc, 0)
        self.assertIn("synhalees-vision", out)

        rc_o, _, _ = _run_cli("kaggle", "download", "audio", "-o", "bad_dir")
        self.assertNotEqual(rc_o, 0)

    def test_kaggle_run_and_remote_status_forwarding(self) -> None:
        calls: list[list[str]] = []

        def fake_sh(argv, **_kw):
            calls.append([str(x) for x in argv])
            return 0

        with mock.patch.object(cli, "kaggle_bin", return_value="kaggle"), \
             mock.patch.object(cli, "sh", side_effect=fake_sh):
            rc1, _, _ = _run_cli("kaggle", "run", "audio", "-m", "gemini-2.5-flash")
            rc2, _, _ = _run_cli("kaggle", "status", "audio", "-m", "gemini-2.5-flash")
            rc3, _, _ = _run_cli("kaggle", "logs", "audio", "-m", "gemini-2.5-flash")
        self.assertEqual((rc1, rc2, rc3), (0, 0, 0))
        self.assertEqual(calls[0], ["kaggle", "b", "t", "run", "synhalees-audio", "-m", "gemini-2.5-flash"])
        self.assertEqual(calls[1], ["kaggle", "b", "t", "status", "synhalees-audio", "-m", "gemini-2.5-flash"])
        self.assertEqual(calls[2], ["kaggle", "b", "t", "log", "synhalees-audio", "-m", "gemini-2.5-flash"])

    def test_kaggle_submit_dry_run_and_slim_import(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            subs_dir = tmp_path / "submissions"
            slim_csv = tmp_path / "sample.slim.csv"
            with slim_csv.open("w", encoding="utf-8", newline="") as fh:
                w = csv.writer(fh)
                w.writerow(cli.EXT_HEADER)
                w.writerow([
                    "gemini-3.8-flash", "kaggle", "2026-11-12",
                    "01_buddhist_culture", "text", "86.7", "0.01", "1000", "500",
                ])

            with mock.patch.object(kaggle_ops, "SUBMISSIONS", subs_dir):
                rc_dry, out_dry, _ = _run_cli("kaggle", "submit", "all", "--dry-run")
                self.assertEqual(rc_dry, 0)
                self.assertIn("would import", out_dry)
                self.assertFalse(subs_dir.exists())

                rc_imp, out_imp, _ = _run_cli("kaggle", "submit", str(slim_csv))
                self.assertEqual(rc_imp, 0)
                self.assertIn("imported 1 row(s)", out_imp)
                target = subs_dir / "google" / "gemini" / "gemini-3.8-flash.csv"
                self.assertTrue(target.is_file())


class TestStandaloneScripts(unittest.TestCase):
    def test_direct_script_invocations_still_work(self) -> None:
        for script_args in (
            ["run_benchmark.py", "--help"],
            ["tools/compare_runs.py", "--help"],
            ["tools/build_leaderboard.py", "--check"],
            ["tools/build_logo_data.py", "--check"],
            ["kaggle/generate_tasks.py", "--dry-run"],
        ):
            proc = subprocess.run(
                [sys.executable, *script_args],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 0, f"{script_args} failed: {proc.stderr}")


if __name__ == "__main__":
    unittest.main()
