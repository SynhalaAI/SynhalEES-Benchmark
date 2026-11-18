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
            ],
        )

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
                    api_keys={"GEMINI_API_KEY": "gem-test"},
                    kaggle_dir=kdir,
                    tokens_file=tfile,
                )
                self.assertEqual(os.environ.get("KAGGLE_USERNAME"), "synhala")
                self.assertEqual(os.environ.get("KAGGLE_KEY"), "secret123")
                self.assertEqual(os.environ.get("GEMINI_API_KEY"), "gem-test")
                self.assertTrue((kdir / "kaggle.json").is_file())
                self.assertTrue(tfile.is_file())
                self.assertTrue(any("Saved" in n for n in notes))

                gui.apply_tokens(kaggle_token="KGAT_abc123", tokens_file=tfile)
                self.assertEqual(os.environ.get("KAGGLE_API_TOKEN"), "KGAT_abc123")
                self.assertEqual(gui.kaggle_auth_status(), "KAGGLE_API_TOKEN set")

            # Simulate restarting GUI with an empty process env
            with mock.patch.dict(os.environ, {}, clear=True):
                loaded = gui.load_saved_tokens(tfile)
                self.assertGreaterEqual(loaded, 3)
                self.assertEqual(os.environ.get("KAGGLE_API_TOKEN"), "KGAT_abc123")
                self.assertEqual(os.environ.get("GEMINI_API_KEY"), "gem-test")

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
            # Verify 3-tier logo icon renderer (PNG + SVG + monogram)
            self.assertIsNotNone(app._make_org_preview_icon("openai", "#10A37F"))
            self.assertIsNotNone(app._make_org_preview_icon("nonexistent-org", "#4285F4"))
            app._set_running(True)
            self.assertEqual(app.status_badge.cget("text"), "RUNNING")
            app._set_running(False)
            self.assertEqual(app.status_badge.cget("text"), "IDLE")
        finally:
            root.destroy()

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
            parse_progress_line,
            rasterize_svg_to_grid,
            render_monogram_grid,
        )

        prog = parse_progress_line("[7/17] downloading synhalees-07-figurative-sinhala -> slim CSV")
        self.assertIsNotNone(prog)
        assert prog is not None
        self.assertEqual(prog[:2], (7, 17))
        self.assertEqual(parse_eval_item_outcome("[12/225] 01_buddhist_culture/text/T012 PASS"), (12, 225, True))
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


if __name__ == "__main__":
    unittest.main()
