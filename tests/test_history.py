"""Checks for the cheap smoke matrix and persistent longitudinal index."""
from __future__ import annotations

import copy
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import benchmark as b
import results_store as store


def fake_xtc_install(root: Path) -> Path:
    """A fake local XTC activation script for tests; no real compiler needed."""
    tools = root / "fake-xtc-bin"
    tools.mkdir(parents=True, exist_ok=True)
    for name in ("xcc", "xsim"):
        prog = tools / name
        prog.write_text("#!/bin/sh\necho 'fake XTC version 15.3.1'\n", encoding="utf-8")
        prog.chmod(0o755)
    script = root / "SetEnv.sh"
    script.write_text(f'export PATH="{tools}:$PATH"\n', encoding="utf-8")
    return script


class HistoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg = Path(b.__file__).parent / "benchmark.toml"
        cls.conf = b.load_config(cls.cfg)

    def test_smoke_selects_two_models_three_conditions_with_referee(self):
        matrix = b.plan(self.conf, smoke=True)
        self.assertEqual(6, len(matrix))
        self.assertEqual({"uart-heartbeat-specific"}, {m[0]["id"] for m in matrix})
        self.assertEqual({"luna", "sol"}, {m[1] for m in matrix})
        self.assertEqual({("luna", "native"), ("luna", "no_skills"),
                          ("luna", "no_skills_xtc"), ("sol", "native"),
                          ("sol", "no_skills"), ("sol", "no_skills_xtc")},
                         {(m[1],m[3]) for m in matrix})
        self.assertEqual({1}, {m[4] for m in matrix})
        selected = b.config_for_selected_matrix(self.conf, matrix, True)
        self.assertEqual(["sol", "luna"], list(selected["models"]["workers"]))
        self.assertTrue(selected["experiment"]["review_enabled"])
        self.assertEqual(1, selected["experiment"]["repeats"])
        self.assertEqual(3, len(selected["conditions"]))
        with self.assertRaisesRegex(ValueError, "two models"):
            b.plan(self.conf, model_filter="sol", smoke=True)
        with self.assertRaisesRegex(ValueError, "two distinct"):
            modified = copy.deepcopy(self.conf)
            modified["experiment"]["smoke_models"] = ["luna", "luna"]
            b.plan(modified, smoke=True)

    def test_filtered_plan_selects_single_trial(self):
        matrix = b.plan(self.conf, "uart-heartbeat-everyday", "opus", "native", 1)
        self.assertEqual(1, len(matrix))
        self.assertEqual(("opus", "native", 1), (matrix[0][1], matrix[0][3], matrix[0][4]))
        with self.assertRaisesRegex(ValueError, "Unknown worker"):
            b.plan(self.conf, model_filter="unconfigured")
        with self.assertRaisesRegex(ValueError, "Unknown task"):
            b.plan(self.conf, task_filter="not-a-task")
        with self.assertRaisesRegex(ValueError, "positive"):
            b.plan(self.conf, repeats=0)

    def test_skills_directory_preflight_fails_before_any_worker_calls(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config = copy.deepcopy(self.conf)
            config["experiment"]["opencode_command"] = str(root / "not-even-installed")
            results = root / "results"
            with patch.object(b, "load_config", return_value=config), patch.object(sys, "argv", [
                "benchmark.py", "run", "--smoke", "--results-root", str(results)]):
                with self.assertRaisesRegex(ValueError, "require --skills-dir"):
                    b.main()
            self.assertFalse(results.exists(), "Must fail before creating archive or starting Copilot")
            empty = root / "empty-skills"
            empty.mkdir()
            with patch.object(b, "load_config", return_value=config), patch.object(sys, "argv", [
                "benchmark.py", "run", "--smoke", "--results-root", str(results),
                "--skills-dir", str(empty)]):
                with self.assertRaisesRegex(ValueError, "No skills found"):
                    b.main()
            self.assertFalse(results.exists())

    def test_warm_control_requires_script_and_actually_activates_xtc(self):
        matrix = b.plan(self.conf, task_filter="uart-heartbeat-specific", model_filter="luna",
                        condition_filter="no_skills_xtc", repeats=1)
        with self.assertRaisesRegex(ValueError, "require --xtc-env"):
            b.validate_xtc_script(None, matrix)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            script = fake_xtc_install(root)
            self.assertEqual(script.resolve(), b.validate_xtc_script(script, matrix))
            base_env = dict(os.environ)
            base_env["PATH"] = "/usr/bin:/bin"
            activated = b.activated_xtc_environment(base_env, script, needs_xsim=True)
            self.assertEqual(str(root / "fake-xtc-bin"), activated["PATH"].split(":")[0])
            self.assertIn("fake-xtc-bin", activated["PATH"])
            b.ensure_cold_controls(base_env, b.plan(self.conf, task_filter="uart-heartbeat-specific"))
            with self.assertRaisesRegex(ValueError, "already active"):
                b.ensure_cold_controls(activated, b.plan(self.conf,
                                                         task_filter="uart-heartbeat-specific"))

    def test_xsim_version_accepts_known_exit_one_banner(self):
        banner = ("Build 50-9ec4d01, May-01-2025\n"
                  "Copyright (C) XMOS Limited 2008-2024. All Rights Reserved.")
        for redirect in ("", " >&2"):
            with self.subTest(redirect=redirect), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                script = fake_xtc_install(root)
                (root / "fake-xtc-bin" / "xsim").write_text(
                    f"#!/bin/sh\necho '{banner}'{redirect}\nexit 1\n", encoding="utf-8")
                activated = b.activated_xtc_environment(dict(os.environ), script)
                self.assertEqual(str(root / "fake-xtc-bin"), activated["PATH"].split(":")[0])

    def test_xtc_version_failures_include_output_and_remain_fatal(self):
        banner = ("Build 50-9ec4d01, May-01-2025\n"
                  "Copyright (C) XMOS Limited 2008-2024. All Rights Reserved.")
        cases = [
            ("xsim", "", "dyld: Library not loaded", 1),
            ("xsim", "unrecognized option --version", "", 1),
            ("xsim", banner, "simulator initialization failed", 1),
            ("xsim", banner, "", 2),
            ("xcc", banner, "", 1),
        ]
        for exe, stdout, stderr, status in cases:
            with self.subTest(exe=exe, stdout=stdout, stderr=stderr, status=status):
                with tempfile.TemporaryDirectory() as td:
                    root = Path(td)
                    script = fake_xtc_install(root)
                    (root / "fake-xtc-bin" / exe).write_text(
                        f"#!/bin/sh\necho '{stdout}'\necho '{stderr}' >&2\nexit {status}\n",
                        encoding="utf-8")
                    with self.assertRaises(ValueError) as caught:
                        b.activated_xtc_environment(dict(os.environ), script)
                    message = str(caught.exception)
                    self.assertIn(f"{exe} --version (exit {status})", message)
                    self.assertIn("No model calls were started", message)
                    if stdout:
                        self.assertIn(f"stdout: {stdout}", message)
                    if stderr:
                        self.assertIn(f"stderr: {stderr}", message)

    def test_xtc_compiler_only_preflight_does_not_require_xsim(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            script = fake_xtc_install(root)
            (root / "fake-xtc-bin" / "xsim").unlink()
            activated = b.activated_xtc_environment(dict(os.environ), script, needs_xsim=False)
            self.assertEqual(str(root / "fake-xtc-bin"), activated["PATH"].split(":")[0])

    def test_smoke_allows_missing_verifier_by_default(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            skill = root / "skills" / "example"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text("---\nname: example\ndescription: test\n---\nUse this.", encoding="utf-8")
            conf = copy.deepcopy(self.conf)
            conf["experiment"]["opencode_command"] = str(root / "nonexistent-opencode")
            conf["tasks"][0]["verify_commands"] = []
            results = root / "results"
            with patch.object(b, "load_config", return_value=conf), patch.object(sys, "argv", [
                "benchmark.py", "run", "--smoke", "--skills-dir", str(root / "skills"),
                "--xtc-env", str(fake_xtc_install(root)), "--results-root", str(results)]):
                # It reaches the OpenCode availability preflight, proving no verifier is required.
                with self.assertRaisesRegex(ValueError, "OpenCode executable not found"):
                    b.main()
            self.assertFalse(results.exists())

    def test_explicit_require_verification_still_fails_before_copilot_calls(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            skill = root / "skills" / "example"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text("---\nname: example\ndescription: test\n---\nUse this.", encoding="utf-8")
            conf = copy.deepcopy(self.conf)
            conf["tasks"][0]["verify_commands"] = []
            results = root / "results"
            with patch.object(b, "load_config", return_value=conf), patch.object(sys, "argv", [
                "benchmark.py", "run", "--smoke", "--require-verification",
                "--skills-dir", str(root / "skills"), "--xtc-env", str(fake_xtc_install(root)),
                "--results-root", str(results)]):
                with self.assertRaisesRegex(ValueError, "Independent verification required"):
                    b.main()
            self.assertFalse(results.exists())

    def test_smoke_verifier_requires_explicit_evidence_file(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            no_evidence = {"verify_commands": [f'{sys.executable} -c "print(123)"']}
            result = b.verify_candidate(no_evidence, root, os.environ.copy(), root, 5,
                                        require_verifier_output=True)
            self.assertEqual("failed", result["status"])
            self.assertIn("no benchmark-verification.json", result["reason"])

    def test_results_first_model_size_absent_and_evidence_appendix(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            task = self.conf["tasks"][0]
            entry = {"task_id": task["id"], "run_id": "one", "model_label": "sol",
                     "model": self.conf["models"]["workers"]["sol"], "condition": "no_skills",
                     "repeat": 1, "validation": {"status": "passed"},
                     "invocation": {"elapsed_seconds": 50}, "usage": {"copilot_estimated_usd": 1.2}}
            text = b.report(root, self.conf, [entry], []).read_text(encoding="utf-8")
            self.assertLess(text.index("Results - scoreboard"), text.index("Method at a glance"))
            self.assertLess(text.index("The Duel"), text.index("Appendix - detailed evidence"))
            self.assertNotIn("Size", text)
            self.assertNotIn("Not publicly disclosed", text)
            self.assertNotIn("parameter count", text.lower())
            self.assertIn("GitHub Copilot", text)
            self.assertIn("Independent verifier observations", text)

    def test_no_skills_only_plan_does_not_require_skills_dir(self):
        with patch.object(b, "load_config", return_value=self.conf), patch.object(sys, "argv", [
            "benchmark.py", "plan", "--condition", "no_skills", "--model", "sol"]):
            self.assertEqual(0, b.main())

    def test_legacy_toml_skill_setting_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "benchmark.toml"
            path.write_text("[experiment]\nskills_source = 'foo'\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "no longer supported"):
                b.load_config(path)

    def test_incomplete_worker_matrix_skips_review_and_marks_report(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            task = self.conf["tasks"][0]
            first = {"task_id": task["id"], "run_id": "sol-no", "model_label": "sol",
                     "condition": "no_skills", "repeat": 1,
                     "validation": {"status": "unverified"}, "usage": {}, "skill_calls": []}
            second = {"task_id": task["id"], "run_id": "sol-native", "model_label": "sol",
                      "condition": "native", "repeat": 1,
                      "validation": {"status": "error"},
                      "error": "ValueError: native skills unavailable", "usage": {}, "skill_calls": []}
            review = b.judge_task(self.conf, self.cfg, root, task, [first, second])
            self.assertEqual("incomplete_candidates", review["status"])
            self.assertFalse((root / "reviews").exists(), "Do not incur judge costs on partial matrix")
            b.write_json(root / "experiment.json", {
                "run_order": [{"worker": "sol", "condition": mode} for mode in ("no_skills", "native")],
                "started_utc": "2026-11-06T22:00:00Z"})
            report = b.report(root, self.conf, [first, second], [review]).read_text(encoding="utf-8")
            self.assertIn("INCOMPLETE EXPERIMENT", report)
            self.assertIn("native skills unavailable", report)
            self.assertIn("Comparison invalid", report)

    @staticmethod
    def create_experiment(parent: Path, folder: str, model: str, status: str, usd: float):
        root = parent / folder
        b.write_json(root / "experiment.json", {
            "experiment_id": folder,
            "started_utc": "2026-11-06T15:00:00Z",
            "configuration_sha256": "test-config",
            "environment": {"xskills_revision": "HEAD", "opencode_version": "fake-v2"},
            "report_config": {"experiment": {"name": "test", "review_enabled": False}},
            "run_options": {"smoke": True, "review_enabled": False},
            "run_order": [{"task_id": "uart-heartbeat-specific", "worker": "luna"}],
        })
        b.write_json(root / "runs" / "trial-1" / "result.json", {
            "run_id": "trial-1", "task_id": "uart-heartbeat-specific",
            "task_family": "uart-heartbeat", "prompt_sha256": "hash-of-prompt",
            "prompt_specificity": "specific", "model_label": "luna", "model": model,
            "condition": "native", "repeat": 1,
            "started_utc": "2026-11-06T15:00:01Z",
            "validation": {"status": status},
            "invocation": {"exit_code": 0, "elapsed_seconds": 35},
            "usage": {"tokens": {"input": 3000, "output": 1000, "cache_read": 500,
                                 "cache_write": 0, "reasoning": 200},
                      "copilot_estimated_usd": usd, "copilot_estimated_ai_credits": usd * 100},
            "skill_calls": [{"tool": "skill", "skill_or_query": "xmos-testing"}],
        })
        return root

    def test_database_indexes_multiple_experiments_idempotently(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            first = self.create_experiment(base, "first", "github-copilot/gpt-6-luna", "passed", 0.02)
            second = self.create_experiment(base, "second", "github-copilot/gpt-6-luna", "failed", 0.06)
            db = store.connect(base / "gladiators.sqlite3")
            try:
                for root in (first, second, second):
                    store.index_experiment(db, root)
                self.assertEqual(2, db.execute("SELECT COUNT(*) FROM experiments").fetchone()[0])
                self.assertEqual(2, db.execute("SELECT COUNT(*) FROM trials").fetchone()[0])
                self.assertIn("first", "\n".join(store.history(db)))
                self.assertIn("second", "\n".join(store.history(db)))
                self.assertIn("hash-of-pro", "\n".join(store.trend(db)))
                self.assertIn("1/2", "\n".join(store.compare(db)))
                self.assertEqual((2,2), store.reindex(db, base))
                self.assertEqual(2, db.execute("SELECT COUNT(*) FROM trials").fetchone()[0])
            finally:
                db.close()

    def test_smoke_without_verifier_still_runs_review_and_marks_unverified(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            fake = root / "fake-opencode"
            fake.write_bytes((Path(__file__).parent / "fixtures" / "fake_opencode.py").read_bytes())
            fake.chmod(0o755)
            skill = root / "skills" / "xmos-test"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text("---\nname: xmos-test\ndescription: test\n---\nTest", encoding="utf-8")
            conf = copy.deepcopy(self.conf)
            conf["experiment"]["opencode_command"] = str(fake)
            conf["experiment"]["clean_home"] = ""
            conf["tasks"][0]["verify_commands"] = []
            results = root / "results"
            with patch.object(b, "load_config", return_value=conf), patch.object(sys, "argv", [
                "benchmark.py", "run", "--smoke", "--progress-every", "0",
                "--results-root", str(results), "--skills-dir", str(root / "skills"),
                "--xtc-env", str(fake_xtc_install(root))]):
                self.assertEqual(0, b.main())
            db = store.connect(results / "gladiators.sqlite3")
            try:
                self.assertEqual(6, db.execute("SELECT COUNT(*) FROM trials").fetchone()[0])
                self.assertEqual({"unverified"}, {r[0] for r in db.execute("SELECT verification_status FROM trials")})
                self.assertEqual({"reviewed"}, {r[0] for r in db.execute("SELECT status FROM reviews")})
            finally:
                db.close()
            report = next(results.glob("*/REPORT.rst")).read_text(encoding="utf-8")
            self.assertIn("No independently verified paired comparison", report)
            self.assertIn("unverified", report.lower())

    def test_smoke_end_to_end_fake_opencode_creates_new_history(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            fake = base / "fake-opencode"
            fake.write_bytes((Path(__file__).parent / "fixtures" / "fake_opencode.py").read_bytes())
            fake.chmod(0o755)
            skill = base / "skills" / "xmos-test"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text("---\nname: xmos-test\ndescription: test\n---\nTest", encoding="utf-8")
            conf = copy.deepcopy(self.conf)
            conf["experiment"]["opencode_command"] = str(fake)
            conf["experiment"]["clean_home"] = ""
            trusted = Path(__file__).parent / "fixtures" / "fake_verifier.py"
            conf["tasks"][0]["verify_commands"] = [f'{sys.executable} "{trusted}"']
            results = base / "results"
            with patch.object(b, "load_config", return_value=conf), patch.object(sys, "argv", [
                "benchmark.py", "run", "--smoke", "--progress-every", "0",
                "--results-root", str(results), "--skills-dir", str(base / "skills"),
                "--xtc-env", str(fake_xtc_install(base))]):
                self.assertEqual(0, b.main())
            db = store.connect(results / "gladiators.sqlite3")
            try:
                self.assertEqual(1, db.execute("SELECT COUNT(*) FROM experiments").fetchone()[0])
                self.assertEqual(6, db.execute("SELECT COUNT(*) FROM trials").fetchone()[0])
                self.assertEqual(1, db.execute("SELECT COUNT(*) FROM reviews").fetchone()[0])
                self.assertEqual({"reviewed"}, {r[0] for r in db.execute("SELECT status FROM reviews")})
                self.assertEqual({"passed"}, {r[0] for r in db.execute("SELECT verification_status FROM trials")})
            finally:
                db.close()
            manifests = list(results.glob("*/experiment.json"))
            self.assertEqual(1, len(manifests))
            for manifest_path in manifests:
                m = json.loads(manifest_path.read_text(encoding="utf-8"))
                self.assertTrue(m["run_options"]["smoke"])
                self.assertTrue(m["run_options"]["review_enabled"])
                self.assertEqual(["sol", "luna"], list(m["report_config"]["models"]["workers"]))
                report = manifest_path.parent / "REPORT.rst"
                self.assertTrue(report.is_file())
                self.assertIn("GitHub Copilot", report.read_text(encoding="utf-8"))
                self.assertTrue((manifest_path.parent / "reviews" / "uart-heartbeat-specific" / "review-result.json").exists())


if __name__ == "__main__":
    unittest.main()
