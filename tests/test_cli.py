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
from http.server import BaseHTTPRequestHandler
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

    def test_kaggle_winerror_4551_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            py_root = Path(tmp)
            scripts_dir = py_root / "Scripts"
            scripts_dir.mkdir()
            fake_kaggle = scripts_dir / "kaggle.EXE"
            fake_kaggle.write_text("", encoding="utf-8")
            fake_py = py_root / "python.exe"
            fake_py.write_text("", encoding="utf-8")

            seen_cmds: list[list[str]] = []

            def fake_run(cmd, **_kw):
                seen_cmds.append([str(x) for x in cmd])
                if len(seen_cmds) == 1:
                    err = OSError("blocked")
                    err.winerror = 4551
                    raise err
                return mock.Mock(returncode=0, stdout="grok-4.7\n", stderr="")

            with mock.patch.object(kaggle_ops, "kaggle_bin", return_value=str(fake_kaggle)), \
                 mock.patch.object(kaggle_ops, "_sys_python_has_kaggle", return_value=False), \
                 mock.patch.object(kaggle_ops.subprocess, "run", side_effect=fake_run):
                proc = kaggle_ops.run_kaggle_captured(["b", "t", "models"])
            self.assertEqual(proc.returncode, 0)
            self.assertEqual(len(seen_cmds), 2)
            self.assertEqual(seen_cmds[0][0], str(fake_kaggle))
            self.assertEqual(seen_cmds[1][0], str(fake_py))
            self.assertIn("from kaggle.cli import main", seen_cmds[1][2])

            seen_cmds.clear()

            def fake_broken_launcher(cmd, **_kw):
                seen_cmds.append([str(x) for x in cmd])
                if len(seen_cmds) == 1:
                    return mock.Mock(returncode=103, stdout="", stderr="Fatal error in launcher: Unable to create process")
                return mock.Mock(returncode=0, stdout="gpt-6.2-sol\n", stderr="")

            with mock.patch.object(kaggle_ops, "kaggle_bin", return_value=str(fake_kaggle)), \
                 mock.patch.object(kaggle_ops, "_sys_python_has_kaggle", return_value=False), \
                 mock.patch.object(kaggle_ops.subprocess, "run", side_effect=fake_broken_launcher):
                proc2 = kaggle_ops.run_kaggle_captured(["b", "t", "models"])
            self.assertEqual(proc2.returncode, 0)
            self.assertEqual(len(seen_cmds), 2)
            self.assertEqual(seen_cmds[1][0], str(fake_py))

    def test_parse_kaggle_models_filters_warnings(self) -> None:
        sample = "\n".join([
            "Warning: Looks like you're using an outdated API Version",
            "Slug                          Display Name",
            "----------------------------  ----------------",
            "gemma-4-26b-a4b-it            Gemma 4 26B",
            "gemma-4-31b-it                Gemma 4 31B",
            "gpt-6.2-astra                 GPT-6.2 Astra",
            "gpt-6.2-sol                   GPT-6.2 Sol",
        ])
        parsed = kaggle_ops.parse_kaggle_models_output(sample)
        self.assertEqual(
            parsed,
            ["gemma-4-26b-a4b-it", "gemma-4-31b-it", "gpt-6.2-astra", "gpt-6.2-sol"],
        )

    def test_colab_bundle_dry_run_and_extract(self) -> None:
        import tarfile
        from synhalees import colab_ops

        rc, out, _ = _run_cli(
            "kaggle", "pull", "all", "--colab", "--modality", "vision|audio", "-m", "gpt-6.2-sol", "--dry-run"
        )
        self.assertEqual(rc, 0)
        self.assertIn("no git clone", out)
        self.assertIn("--modality vision|audio -m gpt-6.2-sol", out)
        runner_src = colab_ops.build_colab_runner_script(["kaggle", "pull", "all"])
        compile(runner_src, "<colab>", "exec")
        self.assertIn("stdout=subprocess.PIPE", runner_src)
        self.assertIn('"-U", "kaggle"', runner_src)

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            tar_path, count = colab_ops.build_colab_bundle(tmp_path / "bundle.tar.gz")
            self.assertGreater(count, 20)
            with tarfile.open(tar_path, "r:gz") as tf:
                names = tf.getnames()
            self.assertIn("synhalees/cli.py", names)
            self.assertIn("synhalees/kaggle_ops.py", names)
            self.assertFalse(any("__pycache__" in n or n.startswith("benchmark_data/") for n in names))

            slim_tar = tmp_path / "slim.tar.gz"
            sample = tmp_path / "demo.slim.csv"
            sample.write_text("model,provider\ngpt-6.2-sol,kaggle\n", encoding="utf-8")
            evil = tmp_path / "ignore.txt"
            evil.write_text("nope", encoding="utf-8")
            with tarfile.open(slim_tar, "w:gz") as tf:
                tf.add(sample, arcname="demo.slim.csv")
                tf.add(evil, arcname="ignore.txt")
            out_dir = tmp_path / "results"
            extracted = colab_ops.extract_slim_archive(slim_tar, out_dir)
            self.assertEqual([p.name for p in extracted], ["demo.slim.csv"])
            self.assertFalse((out_dir / "ignore.txt").exists())

            seen_colab: list[list[str]] = []

            def fake_colab_run(cmd, **_kw):
                seen_colab.append([str(x) for x in cmd])
                if len(seen_colab) == 1:
                    err = OSError("blocked")
                    err.winerror = 4551
                    raise err
                return mock.Mock(returncode=0)

            scripts = tmp_path / "Scripts"
            scripts.mkdir()
            fake_colab = scripts / "colab.exe"
            fake_colab.write_text("", encoding="utf-8")
            fake_py = tmp_path / "python.exe"
            fake_py.write_text("", encoding="utf-8")
            with mock.patch.object(colab_ops, "_sys_python_has_colab", return_value=False), \
                 mock.patch.object(colab_ops.subprocess, "run", side_effect=fake_colab_run):
                rc_c = colab_ops._colab_sh([str(fake_colab), "new"], echo=False)
            self.assertEqual(rc_c, 0)
            self.assertEqual(seen_colab[1][0], str(fake_py))
            self.assertIn("entry_points(group='console_scripts', name='colab')", seen_colab[1][2])


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


class TestGUI(unittest.TestCase):
    def test_gui_help_and_builders(self) -> None:
        from synhalees import gui

        rc, out, _ = _run_cli("gui", "--help")
        self.assertEqual(rc, 0)
        self.assertIn("--tab", out)

        run_args = gui.build_run_args(
            model="gemini:gemini-2.5-flash",
            judge="openai:gpt-5.4",
            modality="vision",
            pillars="01_buddhist_culture",
            fresh=True,
            reasoning_effort="high",
        )
        self.assertEqual(
            run_args,
            [
                "run",
                "--model", "gemini:gemini-2.5-flash",
                "--judge", "openai:gpt-5.4",
                "--modality", "vision",
                "--pillars", "01_buddhist_culture",
                "--fresh",
                "--reasoning-effort", "high",
            ],
        )
        self.assertEqual(
            gui.build_run_args(model="gemini:gemini-2.5-flash", gui_item_events=True),
            ["run", "--model", "gemini:gemini-2.5-flash", "--gui-item-events"],
        )
        import run_benchmark

        parsed = run_benchmark.parse_args(
            [
                "--model", "gemini:gemini-2.5-flash",
                "--gui-item-events",
                "--reasoning-effort", "high",
            ]
        )
        self.assertTrue(parsed.gui_item_events)
        self.assertEqual(parsed.reasoning_effort, "high")

        kg_pull = gui.build_kaggle_args(
            "pull",
            task="all",
            model="grok-4.7",
            modality="Vision + Audio (~730 MB)",
            force=True,
            dry_run=True,
        )
        self.assertEqual(
            kg_pull,
            ["kaggle", "pull", "all", "-m", "grok-4.7", "--modality", "vision|audio", "--force", "--dry-run"],
        )

        kg_cov_online = gui.build_kaggle_args("coverage", model="xai")
        self.assertEqual(kg_cov_online, ["kaggle", "coverage", "--provider", "xai"])

        kg_multi = gui.build_kaggle_args("pull", model="gemma-4-26b-a4b-it, gpt-6.2-sol", colab=True)
        self.assertEqual(
            kg_multi,
            ["kaggle", "pull", "all", "-m", "gemma-4-26b-a4b-it", "-m", "gpt-6.2-sol", "--colab"],
        )

        snap = gui.collect_dashboard_snapshot()
        self.assertEqual(snap.pillar_count, 15)
        self.assertGreater(snap.submission_models, 0)

    def test_judge_call_count_uses_runnable_eval_rows(self) -> None:
        from synhalees.gui import count_judge_calls

        rows = iter(
            [
                {"eval_type": "llm_judge"},
                {"eval_type": "exact_match"},
                {"eval_type": "llm_judge"},
            ]
        )
        with mock.patch("synhalees.runner.iter_items", return_value=rows) as iter_items:
            count = count_judge_calls(["04_kavi_sindu"], ["vision"])
        self.assertEqual(count, 2)
        iter_items.assert_called_once_with(["04_kavi_sindu"], ["vision"])

    def test_gui_apply_tokens_isolated(self) -> None:
        import os
        from synhalees import gui

        with tempfile.TemporaryDirectory() as tmp:
            kdir = Path(tmp) / ".kaggle"
            tfile = Path(tmp) / ".synhalees" / "tokens.json"
            with mock.patch.dict(os.environ, {}, clear=True):
                notes = gui.apply_tokens(
                    kaggle_token='{"username": "synhala", "key": "secret123"}',
                    save_kaggle_json=True,
                    api_keys={
                        "GEMINI_API_KEY": "gem-test",
                        "OPENAI_API_KEY": "openai-test",
                        "SYNHALEES_API_KEY": "custom-test",
                        "SYNHALEES_BASE_URL": "https://saved.example/v1",
                        "SYNHALEES_PROVIDER": "colab",
                    },
                    kaggle_dir=kdir,
                    tokens_file=tfile,
                )
                self.assertEqual(os.environ.get("KAGGLE_USERNAME"), "synhala")
                self.assertEqual(os.environ.get("KAGGLE_KEY"), "secret123")
                self.assertEqual(os.environ.get("GEMINI_API_KEY"), "gem-test")
                self.assertEqual(os.environ.get("SYNHALEES_BASE_URL"), "https://saved.example/v1")
                self.assertEqual(gui.saved_judge_providers(tfile), ["openai", "gemini"])
                self.assertTrue((kdir / "kaggle.json").is_file())
                self.assertTrue(tfile.is_file())
                self.assertTrue(any("Saved" in n for n in notes))
                os.environ["ANTHROPIC_API_KEY"] = "external-only"
                self.assertEqual(gui.saved_judge_providers(tfile), ["openai", "gemini"])

                gui.apply_tokens(kaggle_token="KGAT_abc123", tokens_file=tfile)
                self.assertEqual(os.environ.get("KAGGLE_API_TOKEN"), "KGAT_abc123")
                self.assertEqual(gui.kaggle_auth_status(), "KAGGLE_API_TOKEN set")

            # Simulate restarting GUI with an empty process env
            with mock.patch.dict(os.environ, {}, clear=True):
                loaded = gui.load_saved_tokens(tfile)
                self.assertGreaterEqual(loaded, 3)
                self.assertEqual(os.environ.get("KAGGLE_API_TOKEN"), "KGAT_abc123")
                self.assertEqual(os.environ.get("GEMINI_API_KEY"), "gem-test")
                self.assertEqual(os.environ.get("SYNHALEES_BASE_URL"), "https://saved.example/v1")
                self.assertEqual(os.environ.get("SYNHALEES_PROVIDER"), "colab")

    def test_gui_save_model_display_names_isolated(self) -> None:
        import json
        from synhalees import gui

        with tempfile.TemporaryDirectory() as tmp:
            mj = Path(tmp) / "models.json"
            mj.write_text(json.dumps({"display_names": {"gpt-6-sol": "GPT 6 Sol"}}), encoding="utf-8")
            n = gui.save_model_display_names(
                {"gpt-6.2-sol": "GPT 6.2 Sol", "gemma-4-31b-it": "Gemma 4 31B"},
                models_json_path=mj,
            )
            self.assertEqual(n, 2)
            saved = json.loads(mj.read_text(encoding="utf-8"))
            self.assertEqual(saved["display_names"]["gpt-6.2-sol"], "GPT 6.2 Sol")
            self.assertEqual(saved["display_names"]["gemma-4-31b-it"], "Gemma 4 31B")
            self.assertEqual(saved["display_names"]["gpt-6-sol"], "GPT 6 Sol")

            with self.assertRaises(ValueError):
                gui.save_model_registry_entries(
                    [{"canonical": "x-1", "display_name": "X 1", "org": "other", "family": "x", "open_source": True}],
                    models_json_path=mj,
                )

            n2 = gui.save_model_registry_entries(
                [{"canonical": "command-a", "display_name": "Command A", "org": "cohere", "family": "command", "open_source": False}],
                new_orgs={"cohere": {"name": "Cohere", "logo": "cohere", "color": "teal"}},
                models_json_path=mj,
            )
            self.assertEqual(n2, 1)
            saved2 = json.loads(mj.read_text(encoding="utf-8"))
            self.assertIn("cohere", saved2["providers"])
            self.assertEqual(saved2["display_names"]["command-a"], "Command A")
            self.assertIn("command-a", gui.find_unregistered_models(["command-a", "gemini-2.5-flash"]))

            pending = gui.classify_pending_kaggle_models(
                ["gemini-2.5-flash", "gpt-6-sol", "brand-new-model"],
                {"gemini-2.5-flash": {"text": 15, "vision": 1, "audio": 1}},
                {},
            )
            self.assertEqual(
                pending,
                [
                    ("gpt-6-sol", "gpt-6-sol", "Ready to Pull"),
                    ("brand-new-model", "brand-new-model", "Unregistered"),
                ],
            )

    def test_gui_window_builds_cleanly(self) -> None:
        import tkinter as tk
        from synhalees.gui import SynhaleesGUI, _enable_hidpi

        _enable_hidpi()
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"no display available: {exc}")
        try:
            root.withdraw()
            app = SynhaleesGUI(root, initial_tab="kaggle")
            root.update_idletasks()
            self.assertEqual(app._active_page, "kaggle")
            app.select_page("management")
            self.assertEqual(app._active_page, "management")
            self.assertEqual(len(app.pillars_tree.get_children()), 15)
            self.assertGreater(len(app.models_tree.get_children()), 0)
            self.assertGreater(len(app.mgmt_models_tree.get_children()), 0)
            self.assertGreater(len(app.mgmt_orgs_tree.get_children()), 0)
            self.assertEqual(len(app.site_gate_cards), 4)
            from synhalees.gui import count_judge_calls

            app.run_pillars_var.set("04_kavi_sindu")
            app.run_modality_var.set("all")
            expected_judge_calls = count_judge_calls(
                ["04_kavi_sindu"], ["text", "vision", "audio"]
            )
            self.assertIn(
                f"Estimated judge API calls: {expected_judge_calls}",
                app.run_judge_count_var.get(),
            )
            app._open_eval_item_window()
            self.assertEqual(str(app._eval_detail_prev_btn.cget("state")), "disabled")
            self.assertEqual(str(app._eval_detail_next_btn.cget("state")), "disabled")
            app._apply_eval_detail_event(
                {
                    "phase": "item_start",
                    "index": 1,
                    "total": 2,
                    "key": "04_kavi_sindu/text/TXT_001",
                    "prompt": "Prompt text",
                    "expected": "Expected text",
                }
            )
            self.assertIn("Waiting for model response", app._eval_detail_status_var.get())
            self.assertEqual(
                app._eval_detail_text_widgets["prompt"].get("1.0", "end-1c"),
                "Prompt text",
            )
            self.assertEqual(
                app._eval_detail_text_widgets["expected"].get("1.0", "end-1c"),
                "Expected text",
            )
            app._apply_eval_detail_event(
                {
                    "phase": "model_response",
                    "key": "04_kavi_sindu/text/TXT_001",
                    "response": "Model output",
                }
            )
            self.assertEqual(
                app._eval_detail_text_widgets["response"].get("1.0", "end-1c"),
                "Model output",
            )
            app._apply_eval_detail_event(
                {
                    "phase": "item_result",
                    "key": "04_kavi_sindu/text/TXT_001",
                    "is_correct": False,
                }
            )
            self.assertTrue(app._eval_detail_status_var.get().startswith("FAILED"))
            app._apply_eval_detail_event(
                {
                    "phase": "item_start",
                    "index": 2,
                    "total": 2,
                    "key": "04_kavi_sindu/text/TXT_002",
                    "prompt": "Next prompt",
                    "expected": "Next expected",
                }
            )
            self.assertIn("Item 1 of 2", app._eval_detail_nav_var.get())
            self.assertEqual(str(app._eval_detail_prev_btn.cget("state")), "disabled")
            self.assertEqual(str(app._eval_detail_next_btn.cget("state")), "normal")
            self.assertEqual(
                app._eval_detail_text_widgets["prompt"].get("1.0", "end-1c"),
                "Prompt text",
            )
            app._show_next_eval_item()
            self.assertEqual(str(app._eval_detail_prev_btn.cget("state")), "normal")
            self.assertEqual(str(app._eval_detail_next_btn.cget("state")), "disabled")
            self.assertEqual(
                app._eval_detail_text_widgets["prompt"].get("1.0", "end-1c"),
                "Next prompt",
            )
            app._show_previous_eval_item()
            self.assertEqual(str(app._eval_detail_prev_btn.cget("state")), "disabled")
            self.assertEqual(str(app._eval_detail_next_btn.cget("state")), "normal")
            self.assertTrue(app._eval_detail_status_var.get().startswith("FAILED"))
            app._close_eval_item_window()
            self.assertEqual(app.run_probe_btn.cget("text"), "Test Connection")
            self.assertEqual(app.run_probe_var.get(), "")

            # A failed probe must show the reason inline (and reveal the log).
            from synhalees.gui_helpers import EndpointProbe

            app._apply_probe_result(
                EndpointProbe(
                    ok=False,
                    target="http://127.0.0.1:1/v1/models",
                    detail="connection refused — server not running or wrong port",
                    hint="Is the Colab tunnel still running?",
                )
            )
            self.assertIn("connection refused", app.run_probe_var.get())
            self.assertTrue(app._console_visible)
            self.assertFalse(app.run_model_combo["values"])
            self.assertEqual(str(app.run_start_btn.cget("state")), "disabled")
            app._apply_probe_result(
                EndpointProbe(ok=True, target="http://x/v1/models", detail="1 model(s) advertised", latency_ms=12, models=["m"])
            )
            self.assertIn("Connected", app.run_probe_var.get())
            self.assertEqual(app.run_model_combo["values"], ("colab:m",))
            self.assertEqual(app.run_model_var.get(), "colab:m")
            self.assertEqual(str(app.run_start_btn.cget("state")), "normal")
            app.run_base_url_var.set("https://changed.example/v1")
            self.assertFalse(app.run_model_combo["values"])
            self.assertEqual(str(app.run_start_btn.cget("state")), "disabled")

            # Copy Log copies the console content, not the command preview.
            app._copy_log()
            copied_log = app.root.clipboard_get()
            self.assertIn("[ERR] Connection test:", copied_log)
            self.assertNotIn("[OK] Log copied to clipboard.", copied_log)
            self.assertIn("[OK] Log copied to clipboard.", app.console.get("1.0", "end"))
            # Verify 3-tier logo icon renderer (PNG + SVG + monogram)
            self.assertIsNotNone(app._make_org_preview_icon("openai", "#10A37F"))
            self.assertIsNotNone(app._make_org_preview_icon("nonexistent-org", "#4285F4"))
            app._set_running(True)
            self.assertEqual(app.status_badge.cget("text"), "RUNNING")
            app._set_running(False)
            self.assertEqual(app.status_badge.cget("text"), "IDLE")
        finally:
            root.destroy()

    def test_only_models_advertised_by_a_successful_probe_are_selectable(self) -> None:
        from synhalees.gui import SynhaleesGUI
        from synhalees.gui_helpers import EndpointProbe

        app = object.__new__(SynhaleesGUI)
        app.run_provider_var = mock.Mock()
        app.run_provider_var.get.return_value = "colab"
        app.run_probe_btn = mock.Mock()
        app.run_probe_var = mock.Mock()
        app.run_probe_label = mock.Mock()
        app.run_model_combo = mock.Mock()
        app.run_model_var = mock.Mock()
        app.run_start_btn = mock.Mock()
        app._run_connection_inputs = ()
        app._run_connection_ready = False
        app._run_model_specs = []
        app._console_visible = True
        app._append_log = mock.Mock()

        app._apply_probe_result(
            EndpointProbe(
                ok=True,
                target="https://endpoint/v1/models",
                detail="1 model(s) advertised",
                models=["qwen2.5-7b-instruct"],
            )
        )

        self.assertEqual(
            app.run_model_combo.configure.call_args.kwargs["values"],
            ["colab:qwen2.5-7b-instruct"],
        )
        self.assertEqual(
            app.run_model_var.set.call_args.args[0],
            "colab:qwen2.5-7b-instruct",
        )
        self.assertTrue(app._run_connection_ready)
        app.run_start_btn.configure.assert_called_with(state="normal")

        app._apply_probe_result(
            EndpointProbe(
                ok=False,
                target="https://endpoint/v1/models",
                detail="authentication rejected",
                status=401,
            )
        )
        self.assertFalse(app._run_connection_ready)
        self.assertEqual(app._run_model_specs, [])
        self.assertEqual(
            app.run_model_combo.configure.call_args.kwargs,
            {"values": (), "state": "disabled"},
        )
        app.run_start_btn.configure.assert_called_with(state="disabled")

    def test_judge_models_are_loaded_from_selected_saved_provider(self) -> None:
        import os

        from synhalees.gui import SynhaleesGUI
        from synhalees.gui_helpers import EndpointProbe

        app = object.__new__(SynhaleesGUI)
        app.run_judge_provider_var = mock.Mock()
        app.run_judge_provider_var.get.return_value = "openai"
        app.run_judge_model_combo = mock.Mock()
        app.run_judge_status_var = mock.Mock()
        app.run_judge_var = mock.Mock()
        app._append_log = mock.Mock()
        app._save_judge_selection = mock.Mock()

        with mock.patch.dict(
            os.environ,
            {"SYNHALEES_GUI_JUDGE_MODEL": "openai:gpt-4.1-mini"},
        ):
            app._apply_judge_probe_result(
                "openai",
                EndpointProbe(
                    ok=True,
                    target="https://api.openai.com/v1/models",
                    detail="1 model(s) listed",
                    models=["gpt-4.1-mini"],
                ),
            )
        self.assertEqual(
            app.run_judge_model_combo.configure.call_args.kwargs["values"],
            ["openai:gpt-4.1-mini"],
        )
        self.assertEqual(
            app.run_judge_model_combo.configure.call_args.kwargs["state"],
            "readonly",
        )
        app.run_judge_var.set.assert_called_with("openai:gpt-4.1-mini")
        app._save_judge_selection.assert_called_with("openai", "openai:gpt-4.1-mini")

        app.run_judge_model_combo.configure.reset_mock()
        app.run_judge_provider_var.get.return_value = "gemini"
        app._apply_judge_probe_result(
            "openai",
            EndpointProbe(
                ok=True,
                target="https://api.openai.com/v1/models",
                detail="1 model(s) listed",
                models=["gpt-4.1-mini"],
            ),
        )
        app.run_judge_model_combo.configure.assert_not_called()

    def test_judge_provider_restores_last_saved_provider(self) -> None:
        import os

        from synhalees import gui
        from synhalees.gui import SynhaleesGUI

        app = object.__new__(SynhaleesGUI)
        app.run_judge_provider_combo = mock.Mock()
        app.run_judge_provider_var = mock.Mock()
        app.run_judge_provider_var.get.return_value = ""
        app.run_judge_model_combo = mock.Mock()
        app.run_judge_var = mock.Mock()
        app.run_judge_status_var = mock.Mock()
        app._on_judge_provider_selected = mock.Mock()
        with (
            mock.patch.dict(
                os.environ,
                {gui._JUDGE_PROVIDER_PREF: "gemini"},
            ),
            mock.patch("synhalees.gui.saved_judge_providers", return_value=["openai", "gemini"]),
        ):
            app._refresh_judge_providers()

        app.run_judge_provider_var.set.assert_called_with("gemini")
        app._on_judge_provider_selected.assert_called_once_with()

    def test_delete_selected_local_run(self) -> None:
        from synhalees.gui import SynhaleesGUI
        with tempfile.TemporaryDirectory() as tmp:
            tmp_runs = Path(tmp) / "runs"
            dummy_run = tmp_runs / "dummy-run"
            dummy_run.mkdir(parents=True)
            (dummy_run / "submission.csv").write_text("model,score\ndummy,10.0\n", encoding="utf-8")

            app = object.__new__(SynhaleesGUI)
            app.local_runs_tree = mock.Mock()
            app.local_runs_tree.selection.return_value = ("item1",)
            app.local_runs_tree.item.return_value = ("dummy-run", "10", "0", "10.0%", "Ready (CSV)")
            app.messagebox = mock.Mock()
            app.messagebox.askyesno.return_value = True
            app._append_log = mock.Mock()
            app._populate_local_runs_table = mock.Mock()
            app.pub_combo = {"values": ["dummy-run"]}
            app.pub_slug_var = mock.Mock()
            app.pub_slug_var.get.return_value = "dummy-run"

            with mock.patch("synhalees.gui.RUNS", tmp_runs):
                app._delete_selected_local_run()

            self.assertFalse(dummy_run.exists())
            app._populate_local_runs_table.assert_called_once()
            app.pub_slug_var.set.assert_called_with("")

    def test_gui_management_org_and_model_crud_isolated(self) -> None:
        from synhalees.gui import (
            bulk_update_model_registry_entries,
            delete_model_completely,
            delete_model_registry_entry,
            delete_organization_entry,
            delete_organization_family,
            save_model_registry_entries,
            save_organization_entry,
            save_organization_family,
        )

        with tempfile.TemporaryDirectory() as tmp:
            mj = Path(tmp) / "models.json"
            mj.write_text(json.dumps({"providers": {}, "display_names": {}}), encoding="utf-8")

            save_organization_entry("acme", name="Acme AI", logo="acme", color="blue", aliases=["acme-ai"], models_json_path=mj)
            save_organization_family("acme", "acme-pro", name="Acme Pro", open_source=False, match_patterns=["acme-pro"], models_json_path=mj)
            save_model_registry_entries(
                [{"canonical": "acme-1", "display_name": "Acme 1", "org": "acme", "family": "acme-pro", "open_source": True}],
                models_json_path=mj,
            )
            self.assertEqual(
                bulk_update_model_registry_entries(["acme-1"], open_source=False, models_json_path=mj),
                1,
            )
            data = json.loads(mj.read_text(encoding="utf-8"))
            self.assertIn("acme", data["providers"])
            self.assertEqual(data["display_names"]["acme-1"], "Acme 1")
            self.assertFalse(data["providers"]["acme"]["families"]["acme-pro"]["open_source"])
            self.assertTrue(delete_organization_family("acme", "acme-pro", models_json_path=mj))

            self.assertTrue(delete_model_registry_entry("acme-1", models_json_path=mj))

            sub_dir = Path(tmp) / "submissions" / "acme" / "acme"
            sub_dir.mkdir(parents=True)
            sub_csv = sub_dir / "acme-2.csv"
            sub_csv.write_text("model,score\nacme-2,0.5\n", encoding="utf-8")
            slim_dir = Path(tmp) / "slim"
            slim_dir.mkdir()
            slim_csv = slim_dir / "demo.slim.csv"
            slim_csv.write_text("model,score\nacme-2,0.5\nother-model,0.9\n", encoding="utf-8")
            save_model_registry_entries(
                [{"canonical": "acme-2", "display_name": "Acme 2", "org": "acme", "family": "acme", "open_source": True}],
                models_json_path=mj,
            )
            res = delete_model_completely(
                "acme-2",
                models_json_path=mj,
                submissions_dir=Path(tmp) / "submissions",
                slim_dir=slim_dir,
            )
            self.assertEqual(res, {"registry": 1, "submissions": 1, "slim_rows": 1})
            self.assertFalse(sub_csv.exists())
            self.assertIn("other-model", slim_csv.read_text(encoding="utf-8"))
            self.assertNotIn("acme-2", slim_csv.read_text(encoding="utf-8"))

            self.assertTrue(delete_organization_entry("acme", models_json_path=mj))
            data = json.loads(mj.read_text(encoding="utf-8"))
            self.assertNotIn("acme-1", data["display_names"])
            self.assertNotIn("acme", data["providers"])

    def test_gui_helpers_progress_svg_logo_scorecard_and_pillar_inspector(self) -> None:
        from synhalees.gui_helpers import (
            compare_model_scorecards,
            extract_failed_tasks,
            inspect_pillar_dataset,
            install_organization_logo,
            load_model_scorecard_summary,
            parse_eval_item_outcome,
            parse_gui_item_event,
            parse_progress_line,
            rasterize_svg_to_grid,
            render_monogram_grid,
        )

        prog = parse_progress_line("[7/17] downloading synhalees-07-figurative-sinhala -> slim CSV")
        self.assertIsNotNone(prog)
        assert prog is not None
        self.assertEqual(prog[:2], (7, 17))
        self.assertEqual(parse_eval_item_outcome("[12/225] 01_buddhist_culture/text/T012 PASS"), (12, 225, True))
        event = {"phase": "item_start", "prompt": "Question", "expected": "Answer"}
        self.assertEqual(
            parse_gui_item_event(
                "@@SYNHALEES_GUI_ITEM@@" + json.dumps(event)
            ),
            event,
        )
        self.assertIsNone(parse_gui_item_event("ordinary console output"))
        self.assertEqual(
            extract_failed_tasks("download failed for 2 task(s): synhalees-audio, synhalees-vision"),
            ["synhalees-audio", "synhalees-vision"],
        )

        # SVG rasterizer & monogram fallback
        openai_svg = Path(ROOT / "docs" / "assets" / "logos" / "openai.svg").read_text(encoding="utf-8")
        grid = rasterize_svg_to_grid(openai_svg, size=16)
        self.assertIsNotNone(grid)
        assert grid is not None
        self.assertEqual(len(grid), 16)
        self.assertGreater(len({px for row in grid for px in row}), 1)
        mgrid = render_monogram_grid("C", "#4285F4", size=16)
        self.assertEqual(len(mgrid), 16)

        # Isolated SVG logo installer & JS map sync
        with tempfile.TemporaryDirectory() as tmp:
            troot = Path(tmp)
            assets = troot / "docs" / "assets"
            assets.mkdir(parents=True)
            (assets / "chart.js").write_text('  var PROVIDER_LOGOS = {\n    google: "google",\n  };\n  var PROVIDER_COLORS = {\n    google: "#4285F4",\n  };\n', encoding="utf-8")
            (assets / "app.js").write_text('  var PROVIDER_LOGOS = {\n    google: "google",\n  };\n', encoding="utf-8")
            src_svg = troot / "cohere.svg"
            src_svg.write_text('<svg viewBox="0 0 24 24"><path d="M2 2h20v20H2z"/></svg>', encoding="utf-8")
            out = install_organization_logo(
                "cohere",
                src_svg,
                org_slug="cohere",
                aliases=["cohere-ai"],
                color_hex="#20B8CD",
                root_dir=troot,
                rebuild_logo_data=False,
            )
            self.assertTrue(out["svg_path"].is_file())
            self.assertIn('cohere: "cohere"', (assets / "chart.js").read_text(encoding="utf-8"))
            self.assertIn('cohereai: "cohere"', (assets / "app.js").read_text(encoding="utf-8"))

        # Read-only pillar inspector & model scorecard summary
        rep = inspect_pillar_dataset("01_buddhist_culture")
        self.assertEqual(rep.slug, "01_buddhist_culture")
        self.assertGreater(rep.text_count, 0)
        sc = load_model_scorecard_summary("gemini-4-argon")
        self.assertEqual(sc.canonical, "gemini-4-argon")
        self.assertIsNotNone(sc.modalities["text"])
        cmp = compare_model_scorecards("gemini-4-argon", "gemini-4-argon", modality="text")
        self.assertEqual(len(cmp["rows"]), 15)


class TestSelfHostedEndpoint(unittest.TestCase):
    """`--base-url` support for OpenAI-compatible servers (Colab + vLLM, llama.cpp)."""

    def test_custom_provider_needs_base_url_and_defaults_key(self) -> None:
        import os

        from synhalees.models import APIModelError, OpenAIModel, get_model

        with self.assertRaises(APIModelError):
            get_model("colab:qwen2.5-7b-instruct")

        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SYNHALEES_API_KEY", None)
            model = get_model("colab:qwen2.5-7b-instruct", base_url="https://x.trycloudflare.com/v1/")
        self.assertIsInstance(model, OpenAIModel)
        self.assertEqual(model.base_url, "https://x.trycloudflare.com/v1")
        self.assertEqual(model.name, "qwen2.5-7b-instruct")
        self.assertEqual(model.api_key, "local")

        explicit = get_model("colab:x", base_url="https://y/v1", api_key="secret")
        self.assertEqual(explicit.api_key, "secret")

        with mock.patch.dict(os.environ, {"SYNHALEES_API_KEY": "env-key"}):
            env_model = get_model("colab:x", base_url="https://y/v1")
        self.assertEqual(env_model.api_key, "env-key")

    def test_run_flags_env_fallback_and_runner_wiring(self) -> None:
        import os

        import run_benchmark
        from synhalees.runner import SynhalEESBenchmark

        rc, out, _ = _run_cli("run", "--help")
        self.assertEqual(rc, 0)
        self.assertIn("--base-url", out)
        self.assertIn("--api-key", out)
        self.assertIn("--reasoning-effort", out)

        args = run_benchmark.parse_args(
            ["--model", "colab:qwen2.5-7b-instruct", "--base-url", "https://flag/v1", "--api-key", "k1"]
        )
        self.assertEqual(run_benchmark.resolve_endpoint(args), ("https://flag/v1", "k1"))

        with mock.patch.dict(
            os.environ,
            {"SYNHALEES_BASE_URL": "https://env/v1", "SYNHALEES_API_KEY": "env-k"},
        ):
            env_args = run_benchmark.parse_args(["--model", "colab:qwen2.5-7b-instruct"])
            self.assertEqual(run_benchmark.resolve_endpoint(env_args), ("https://env/v1", "env-k"))
            # explicit flags always win over the environment
            self.assertEqual(run_benchmark.resolve_endpoint(args), ("https://flag/v1", "k1"))

        bench = SynhalEESBenchmark(
            "colab:qwen2.5-7b-instruct",
            base_url="https://flag/v1",
            api_key="k1",
            checkpoint=None,
            modalities=("text",),
            pillars=["01_buddhist_culture"],
        )
        self.assertEqual(bench.model.base_url, "https://flag/v1")
        self.assertIs(bench.judge_model, bench.model)

        same_provider = SynhalEESBenchmark(
            "colab:qwen2.5-7b-instruct",
            judge_model="colab:judge-model",
            base_url="https://flag/v1",
            checkpoint=None,
            pillars=["01_buddhist_culture"],
        )
        self.assertEqual(same_provider.judge_model.base_url, "https://flag/v1")

        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "gem"}):
            other_provider = SynhalEESBenchmark(
                "colab:qwen2.5-7b-instruct",
                judge_model="gemini:gemini-2.5-flash",
                base_url="https://flag/v1",
                checkpoint=None,
                pillars=["01_buddhist_culture"],
            )
        self.assertNotEqual(other_provider.judge_model.base_url, "https://flag/v1")

    def test_gui_run_args_carry_endpoint(self) -> None:
        from synhalees import gui

        self.assertEqual(
            gui.build_run_args(
                model="colab:qwen2.5-7b-instruct",
                base_url="https://x.trycloudflare.com/v1",
                api_key="k1",
            ),
            [
                "run", "--model", "colab:qwen2.5-7b-instruct",
                "--base-url", "https://x.trycloudflare.com/v1",
                "--api-key", "k1",
            ],
        )
        # empty endpoint fields stay out of the preview / command line
        self.assertEqual(
            gui.build_run_args(model="ollama:llama3.1:8b", base_url="  ", api_key=""),
            ["run", "--model", "ollama:llama3.1:8b"],
        )
        # the console preview never echoes the endpoint key
        preview = gui.format_cmd_preview(
            gui.build_run_args(model="colab:x", base_url="https://x/v1", api_key="s3cret")
        )
        self.assertIn("--api-key ***", preview)
        self.assertNotIn("s3cret", preview)

    def test_openai_model_audio_fallback_to_audio_url(self) -> None:
        import tempfile
        from pathlib import Path
        from synhalees.models import APIModelError, OpenAIModel

        model = OpenAIModel("test-audio", api_key="dummy", base_url="https://test/v1")
        tmp_path = Path("tests") / "_temp_test_audio.mp3"
        tmp_path.write_bytes(b"fake mp3 bytes")
        try:
            calls = []
            def fake_post(url, headers, payload, **kwargs):
                calls.append(payload)
                if len(calls) == 1:
                    raise APIModelError("HTTP 400: input_audio not supported")
                return {"choices": [{"message": {"content": "transcribed text"}}]}

            with mock.patch("synhalees.models._post_json", side_effect=fake_post):
                resp = model.complete("Transcribe", audio_path=str(tmp_path))

            self.assertEqual(resp, "transcribed text")
            self.assertEqual(len(calls), 2)
            self.assertEqual(calls[0]["messages"][0]["content"][1]["type"], "input_audio")
            self.assertEqual(calls[1]["messages"][0]["content"][1]["type"], "audio_url")
        finally:
            if tmp_path.exists():
                tmp_path.unlink()

    def test_reasoning_probe_requires_reported_reasoning_tokens(self) -> None:
        from synhalees.gui_helpers import probe_reasoning

        cases = (
            ({"completion_tokens_details": {"reasoning_tokens": 12}}, True),
            ({"completion_tokens_details": {"reasoning_tokens": 0}}, False),
            ({"completion_tokens_details": {}}, False),
            ({}, False),
        )
        for usage, confirmed in cases:
            with self.subTest(usage=usage), mock.patch(
                "synhalees.models._post_json",
                return_value={
                    "choices": [{"message": {"content": "3"}}],
                    "usage": usage,
                },
            ) as post:
                probe = probe_reasoning(
                    model_spec="openai:test-model",
                    api_key="test-key",
                )
                self.assertEqual(probe.confirmed, confirmed)
                self.assertEqual(
                    post.call_args.args[2]["reasoning_effort"],
                    "high",
                )

    def test_runner_applies_reasoning_effort_only_to_main_model(self) -> None:
        from synhalees.models import OpenAIModel
        from synhalees.runner import SynhalEESBenchmark

        model = OpenAIModel("main", api_key="main-key")
        judge = OpenAIModel("judge", api_key="judge-key")
        judge_report = json.dumps(
            {
                "results": [
                    {"criterion": "meaning", "passed": True, "reason": "ok", "confidence": 90},
                    {"criterion": "style", "passed": True, "reason": "ok", "confidence": 90},
                ]
            }
        )
        benchmark = SynhalEESBenchmark(
            model,
            judge_model=judge,
            reasoning_effort="high",
            checkpoint=None,
            pillars=["p1"],
        )
        row = {
            "pillar": "p1",
            "modality": "text",
            "id": "item-1",
            "prompt": "Question",
            "ground_truth": "Reference",
            "eval_type": "llm_judge",
            "media_path": "",
        }
        with (
            mock.patch.object(
                model,
                "complete_with_usage",
                return_value=("Candidate answer", {}),
            ) as model_call,
            mock.patch.object(judge, "complete", return_value=judge_report) as judge_call,
        ):
            benchmark._run_item(row)

        self.assertEqual(model_call.call_args.kwargs["reasoning_effort"], "high")
        self.assertNotIn("reasoning_effort", judge_call.call_args.kwargs)

    def test_gui_enables_reasoning_only_after_positive_probe(self) -> None:
        from synhalees.gui import SynhaleesGUI

        app = object.__new__(SynhaleesGUI)
        app.run_model_var = mock.Mock()
        app.run_model_var.get.return_value = "openai:test-model"
        app._reasoning_test_running = True
        app._run_reasoning_effort = None
        app._run_reasoning_model = ""
        app._run_reasoning_probe_generation = 1
        app.run_reasoning_status_var = mock.Mock()
        app._append_log = mock.Mock()
        app._update_reasoning_button_state = mock.Mock()
        app._update_run_preview = mock.Mock()

        app._apply_reasoning_probe_result(
            "openai:test-model",
            1,
            mock.Mock(confirmed=True, detail="High reasoning confirmed (12 tokens)."),
        )
        self.assertEqual(app._run_reasoning_effort, "high")
        self.assertEqual(app._run_reasoning_model, "openai:test-model")

        app._apply_reasoning_probe_result(
            "openai:test-model",
            1,
            mock.Mock(confirmed=False, detail="No reasoning token metadata."),
        )
        self.assertIsNone(app._run_reasoning_effort)
        app._apply_reasoning_probe_result(
            "openai:test-model",
            0,
            mock.Mock(confirmed=True, detail="stale positive result"),
        )
        self.assertIsNone(app._run_reasoning_effort)

class TestEndpointProbe(unittest.TestCase):
    """`Test Connection` button backend and HTTPS verification."""

    def test_https_helper_keeps_certificate_verification_enabled(self) -> None:
        import ssl

        from synhalees.http import _default_ssl_context

        context = _default_ssl_context()
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.get_ca_certs())

    def test_untrusted_certificate_error_is_actionable(self) -> None:
        from synhalees.gui_helpers import _friendly_error

        message = _friendly_error(
            "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: "
            "unable to get local issuer certificate (_ssl.c:1010)"
        )
        self.assertIn("CA certificates", message)
        self.assertIn("CERTIFICATE_VERIFY_FAILED", message)

    def _serve(self, **attrs):
        import threading
        from http.server import ThreadingHTTPServer

        handler = type("ProbeHandler", (_ProbeHandler,), attrs)
        srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        return srv, f"http://127.0.0.1:{srv.server_address[1]}/v1"

    def test_openai_compatible_models_probe(self) -> None:
        from synhalees.gui_helpers import probe_endpoint

        _srv, base = self._serve(models=["qwen2.5-7b-instruct"])
        probe = probe_endpoint(base_url=base, api_key="k")
        self.assertTrue(probe.ok, probe.summary())
        self.assertEqual(probe.status, 200)
        self.assertIn("qwen2.5-7b-instruct", probe.models)
        self.assertIn("ms", probe.summary())
        self.assertGreaterEqual(probe.latency_ms, 0)
        self.assertEqual(probe.hint, "")

    def test_falls_back_to_model_free_chat_probe_when_models_missing(self) -> None:
        from synhalees.gui_helpers import probe_endpoint

        srv, base = self._serve(models_status=404, chat_status=400)
        probe = probe_endpoint(base_url=base)
        self.assertTrue(probe.ok, probe.summary())
        self.assertIn("/chat/completions", probe.target)
        self.assertNotIn("model", srv.RequestHandlerClass.received_chat_payload)
        self.assertIn("inference not tested", probe.detail)
        self.assertIn("Select a model only when starting", probe.hint)

        # Nothing at /models or /chat/completions -> actionable "/v1" guidance.
        _srv2, base2 = self._serve(models_status=404, chat_status=404)
        wrong = probe_endpoint(base_url=base2)
        self.assertFalse(wrong.ok)
        self.assertIn("/v1", wrong.detail + wrong.hint)

    def test_auth_failure_and_unreachable(self) -> None:
        from synhalees.gui_helpers import probe_endpoint

        srv, base = self._serve(models_status=401, chat_status=401)
        self.addCleanup(srv.shutdown)
        probe = probe_endpoint(base_url=base, api_key="wrong")
        self.assertFalse(probe.ok)
        self.assertEqual(probe.status, 401)
        self.assertTrue(probe.hint)

        import socket

        with socket.socket() as sock:  # grab a free port, then close it -> refused
            sock.bind(("127.0.0.1", 0))
            dead_port = sock.getsockname()[1]
        dead = probe_endpoint(base_url=f"http://127.0.0.1:{dead_port}/v1", timeout=2)
        self.assertFalse(dead.ok)
        self.assertTrue(
            "refused" in dead.summary() or "timed out" in dead.summary(),
            dead.summary(),
        )

    def test_missing_base_url_and_provider_key(self) -> None:
        import os

        from synhalees.gui_helpers import probe_endpoint

        no_target = probe_endpoint(provider="mystery")
        self.assertFalse(no_target.ok)
        self.assertIn("Set a Base URL", no_target.detail)

        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("GEMINI_API_KEY", None)
            os.environ.pop("GOOGLE_API_KEY", None)
            gemini = probe_endpoint(provider="gemini")
        self.assertFalse(gemini.ok)
        self.assertIn("GEMINI_API_KEY", gemini.detail)


class _ProbeHandler(BaseHTTPRequestHandler):
    """Tiny OpenAI-compatible stub used by the connection-probe tests."""

    models_status = 200
    chat_status = 200
    models: list[str] = []
    received_chat_payload: dict = {}

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - http.server API
        if self.path.endswith("/models"):
            if self.models_status != 200:
                return self._json(self.models_status, {"error": "models disabled"})
            return self._json(200, {"data": [{"id": m} for m in self.models]})
        return self._json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802 - http.server API
        length = int(self.headers.get("Content-Length") or 0)
        self.__class__.received_chat_payload = json.loads(self.rfile.read(length) or b"{}")
        if self.chat_status != 200:
            return self._json(self.chat_status, {"error": "chat disabled"})
        return self._json(200, {"choices": [{"message": {"content": "ok"}}]})

    def log_message(self, *args) -> None:
        pass


class TestKaggleScoringParity(unittest.TestCase):
    def test_classification_prompts_match_kaggle_task_scopes(self) -> None:
        from synhalees.runner import add_kaggle_classification_prompts

        rows = [
            {
                "pillar": "p1", "modality": "text", "eval_type": "classification",
                "ground_truth": "Zulu", "prompt": "Classify this.",
            },
            {
                "pillar": "p1", "modality": "text", "eval_type": "classification",
                "ground_truth": "Alpha", "prompt": "Classify this too.",
            },
            {
                "pillar": "p2", "modality": "text", "eval_type": "classification",
                "ground_truth": "Beta", "prompt": "Another pillar.",
            },
            {
                "pillar": "p1", "modality": "vision", "eval_type": "classification",
                "ground_truth": "Zulu", "prompt": "Look and choose.",
            },
            {
                "pillar": "p2", "modality": "vision", "eval_type": "classification",
                "ground_truth": "Alpha", "prompt": "Look and choose.",
            },
        ]

        prepared = add_kaggle_classification_prompts(rows)
        self.assertEqual(
            prepared[0]["prompt"],
            "Choose exactly one label from [Alpha / Zulu]. "
            "Reply with only the label. Classify this.",
        )
        self.assertIn("[Beta]", prepared[2]["prompt"])
        self.assertIn("[Alpha / Zulu]", prepared[3]["prompt"])
        self.assertEqual(rows[0]["prompt"], "Classify this.")

    def test_judge_prompt_and_parser_use_kaggle_criteria_report(self) -> None:
        from synhalees.evaluators import build_judge_prompt, parse_judge_verdict

        prompt = build_judge_prompt("Model answer", "Reference")
        self.assertIn("impartial and strict technical evaluator", prompt)
        self.assertIn("same meaning as the reference answer: 'Reference'", prompt)
        self.assertIn("native Sri Lankan Sinhala speaker", prompt)
        self.assertNotIn("Question asked to the model", prompt)

        report = {
            "results": [
                {
                    "criterion": "meaning",
                    "passed": True,
                    "reason": "Meaning matches.",
                    "confidence": 95,
                },
                {
                    "criterion": "naturalness",
                    "passed": True,
                    "reason": "Natural Sinhala.",
                    "confidence": 90,
                },
            ]
        }
        self.assertEqual(
            parse_judge_verdict(json.dumps(report)),
            (True, "Meaning matches.; Natural Sinhala."),
        )
        # Markdown code fence wrapping
        fenced = f"```json\n{json.dumps(report)}\n```"
        self.assertEqual(
            parse_judge_verdict(fenced),
            (True, "Meaning matches.; Natural Sinhala."),
        )
        # Conversational text preamble + code fence
        wrapped = f"Here is the evaluation:\n```json\n{json.dumps(report)}\n```\nDone."
        self.assertEqual(
            parse_judge_verdict(wrapped),
            (True, "Meaning matches.; Natural Sinhala."),
        )
        report["results"][1]["passed"] = False
        self.assertFalse(parse_judge_verdict(json.dumps(report))[0])
        self.assertFalse(parse_judge_verdict('{"passed": true}')[0])

    def test_runner_emits_live_events_before_and_after_judging(self) -> None:
        from synhalees.models import BaseModel
        from synhalees.runner import SynhalEESBenchmark

        class StaticModel(BaseModel):
            def __init__(self, name: str, answer: str) -> None:
                self.name = name
                self.answer = answer

            def complete(self, prompt, *, image_path=None, audio_path=None) -> str:
                return self.answer

        row = {
            "pillar": "p1",
            "modality": "text",
            "id": "item-1",
            "prompt": "Question",
            "ground_truth": "Reference",
            "eval_type": "llm_judge",
            "media_path": "",
        }
        judge_report = json.dumps(
            {
                "results": [
                    {"criterion": "meaning", "passed": True, "reason": "ok", "confidence": 90},
                    {"criterion": "style", "passed": True, "reason": "ok", "confidence": 90},
                ]
            }
        )
        benchmark = SynhalEESBenchmark(
            StaticModel("main", "Model response"),
            judge_model=StaticModel("judge", judge_report),
            modalities=("text",),
            pillars=["p1"],
            checkpoint=None,
        )
        events: list[dict] = []
        with mock.patch("synhalees.runner.iter_items", return_value=iter([row])):
            result = benchmark.run(progress=False, item_event=events.append)

        self.assertTrue(result.records[0]["is_correct"])
        self.assertEqual(
            [event["phase"] for event in events],
            ["item_start", "model_response", "judge_waiting", "item_result"],
        )
        self.assertEqual(events[0]["prompt"], "Question")
        self.assertEqual(events[0]["expected"], "Reference")
        self.assertEqual(events[1]["response"], "Model response")

    def test_strip_thinking_and_exact_match_isolation(self) -> None:
        from synhalees.evaluators import score_response, strip_thinking

        # 1. Stripping thinking tags
        raw = "<think>\nLet's analyze step-by-step: the answer is Colombo.\n</think>\nකොළඹ"
        self.assertEqual(strip_thinking(raw), "කොළඹ")

        # 2. Exact match passes on clean answer, ignoring thinking blocks
        self.assertTrue(score_response(raw, "කොළඹ", "exact_match"))

        # 3. Unclosed tag handling
        unclosed = "<thought>Thinking only..."
        self.assertEqual(strip_thinking(unclosed), "")

    def test_judge_model_api_error_does_not_crash_benchmark(self) -> None:
        from synhalees.models import APIModelError, BaseModel
        from synhalees.runner import SynhalEESBenchmark

        class FlakyJudgeModel(BaseModel):
            def __init__(self) -> None:
                self.name = "flaky-judge"
                self.calls = 0

            def complete(self, prompt, *, image_path=None, audio_path=None, reasoning_effort=None) -> str:
                self.calls += 1
                if self.calls == 1:
                    # Preflight check passes
                    return json.dumps({
                        "results": [
                            {"criterion": "meaning", "passed": True, "reason": "ok", "confidence": 90},
                            {"criterion": "style", "passed": True, "reason": "ok", "confidence": 90},
                        ]
                    })
                # Item execution call fails
                raise APIModelError("The read operation timed out")

        class MainModel(BaseModel):
            def __init__(self) -> None:
                self.name = "main"

            def complete(self, prompt, *, image_path=None, audio_path=None, reasoning_effort=None) -> str:
                return "Model answer"

        row = {
            "pillar": "p1",
            "modality": "text",
            "id": "item-1",
            "prompt": "Question",
            "ground_truth": "Reference",
            "eval_type": "llm_judge",
            "media_path": "",
        }
        benchmark = SynhalEESBenchmark(
            MainModel(),
            judge_model=FlakyJudgeModel(),
            modalities=("text",),
            pillars=["p1"],
            checkpoint=None,
        )
        with mock.patch("synhalees.runner.iter_items", return_value=iter([row])):
            results = benchmark.run(progress=False)

        self.assertEqual(len(results.records), 1)
        record = results.records[0]
        self.assertFalse(record["is_correct"])
        self.assertIn("The read operation timed out", record["error"])
        self.assertIn("Judge failed", record["judge_reason"])

    def test_judge_model_preflight_fails_fast_when_judge_unreachable(self) -> None:
        from synhalees.models import APIModelError, BaseModel
        from synhalees.runner import SynhalEESBenchmark

        class BrokenJudge(BaseModel):
            name = "broken-judge"

            def complete(self, prompt, *, image_path=None, audio_path=None, reasoning_effort=None) -> str:
                raise APIModelError("The read operation timed out")

        class DummyModel(BaseModel):
            name = "dummy"

            def complete(self, prompt, *, image_path=None, audio_path=None, reasoning_effort=None) -> str:
                return "answer"

        row = {
            "pillar": "p1",
            "modality": "text",
            "id": "item-1",
            "prompt": "Question",
            "ground_truth": "Reference",
            "eval_type": "llm_judge",
            "media_path": "",
        }
        benchmark = SynhalEESBenchmark(
            DummyModel(),
            judge_model=BrokenJudge(),
            modalities=("text",),
            pillars=["p1"],
            checkpoint=None,
        )
        with mock.patch("synhalees.runner.iter_items", return_value=iter([row])):
            with self.assertRaises(APIModelError) as cm:
                benchmark.run(progress=False)
        self.assertIn("preflight check failed", str(cm.exception))

    def test_probe_judge_success_and_failure(self) -> None:
        from synhalees.gui_helpers import probe_judge

        with mock.patch("synhalees.models.get_model") as mock_get:
            mock_model = mock.MagicMock()
            mock_model.complete.return_value = json.dumps({
                "results": [
                    {"criterion": "meaning", "passed": True, "reason": "ok", "confidence": 90},
                    {"criterion": "style", "passed": True, "reason": "ok", "confidence": 90},
                ]
            })
            mock_get.return_value = mock_model
            probe = probe_judge(judge_spec="gemini:gemini-2.5-flash")
            self.assertTrue(probe.ok)
            self.assertIn("Criteria verified", probe.detail)

            # Failure case
            mock_model.complete.side_effect = RuntimeError("Endpoint timed out")
            probe = probe_judge(judge_spec="gemini:broken-model")
            self.assertFalse(probe.ok)
            self.assertIn("Endpoint timed out", probe.detail)


if __name__ == "__main__":
    unittest.main()
