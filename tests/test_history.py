"""Strict verification preflight, XTC activation and persistent results index."""
import copy
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import benchmark as b
import results_store as store
from fixtures.fake_verifier import observations, validation
from verification import uart_heartbeat as verifier


def fake_xtc_install(root):
    tools = root / "fake-xtc-bin"
    tools.mkdir(parents=True, exist_ok=True)
    for name in ("xcc", "xsim"):
        prog = tools / name
        prog.write_text("#!/bin/sh\necho 'XTC version: 15.3.1'\n")
        prog.chmod(0o755)
    script = root / "SetEnv.sh"
    script.write_text(f'export PATH="{tools}:$PATH"\n')
    return script


class HistoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg = Path(b.__file__).parent / "benchmark.toml"
        cls.conf = b.load_config(cls.cfg)

    def test_smoke_is_four_warm_trials_and_full_is_six(self):
        matrix = b.plan(self.conf, smoke=True)
        self.assertEqual(4, len(matrix))
        self.assertEqual({"native_xtc", "no_skills_xtc"}, {m[3] for m in matrix})
        self.assertEqual({"luna", "sol"}, {m[1] for m in matrix})
        self.assertEqual(6, len(b.plan(self.conf)))
        selected = b.config_for_selected_matrix(self.conf, matrix, True)
        self.assertEqual(["sol", "luna"], list(selected["models"]["workers"]))
        self.assertEqual(2, len(selected["conditions"]))
        for kwargs, message in [({"model_filter": "sol"}, "two models"), ({"repeats": 2}, "one repeat")]:
            with self.assertRaisesRegex(ValueError, message):
                b.plan(self.conf, smoke=True, **kwargs)
        modified = copy.deepcopy(self.conf)
        modified["experiment"]["smoke_models"] = ["luna", "luna"]
        with self.assertRaisesRegex(ValueError, "two distinct"):
            b.plan(modified, smoke=True)

    def test_filters_and_removed_legacy_tasks(self):
        matrix = b.plan(self.conf, "uart-heartbeat-specific", "opus", "native_xtc", 1)
        self.assertEqual(1, len(matrix))
        for kwargs, message in [({"task_filter": "uart-heartbeat-vague"}, "Unknown task"),
                                ({"model_filter": "unknown"}, "Unknown worker"), ({"repeats": 0}, "positive")]:
            with self.assertRaisesRegex(ValueError, message):
                b.plan(self.conf, **kwargs)

    def test_missing_verifier_fails_plan_and_full_run_before_any_model_calls(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            skill = root / "skills" / "example"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text("---\nname: example\n---\nTest")
            conf = copy.deepcopy(self.conf)
            conf["tasks"][0].pop("verifier")
            for command in ("plan", "run"):
                with patch.object(b, "load_config", return_value=conf), patch.object(b, "launch") as launch, \
                        patch.object(sys, "argv", ["benchmark.py", command, "--skills-dir", str(root / "skills"),
                         "--xtc-env", str(fake_xtc_install(root)), "--results-root", str(root / "results")]):
                    with self.assertRaisesRegex(ValueError, "requires a supported independent"):
                        b.main()
                    launch.assert_not_called()
                    self.assertFalse((root / "results").exists())

    def test_skills_directory_preflight(self):
        matrix = b.plan(self.conf, smoke=True)
        with self.assertRaisesRegex(ValueError, "require --skills-dir"):
            b.validate_skills_directory(None, matrix)
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaisesRegex(ValueError, "No skills found"):
                b.validate_skills_directory(Path(td), matrix)

    def test_warm_control_requires_and_activates_script(self):
        matrix = b.plan(self.conf)
        with self.assertRaisesRegex(ValueError, "require --xtc-env"):
            b.validate_xtc_script(None, matrix)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            script = fake_xtc_install(root)
            self.assertEqual(script.resolve(), b.validate_xtc_script(script, matrix))
            env = dict(os.environ, PATH="/usr/bin:/bin")
            activated = b.activated_xtc_environment(env, script)
            self.assertEqual(str(root / "fake-xtc-bin"), activated["PATH"].split(":")[0])
            self.assertEqual("/usr/bin:/bin", env["PATH"])

    def test_xsim_version_accepts_known_exit_one_banner(self):
        banner = "Build 50-9ec4d01, May-01-2025\nCopyright (C) XMOS Limited 2008-2024. All Rights Reserved."
        for redirect in ("", " >&2"):
            with self.subTest(redirect=redirect), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                script = fake_xtc_install(root)
                (root / "fake-xtc-bin" / "xsim").write_text(f"#!/bin/sh\necho '{banner}'{redirect}\nexit 1\n")
                self.assertIn("fake-xtc-bin", b.activated_xtc_environment(dict(os.environ), script)["PATH"])

    def test_real_xtc_failures_remain_fatal_and_include_output(self):
        banner = "Build 50-9ec4d01, May-01-2025\nCopyright (C) XMOS Limited 2008-2024. All Rights Reserved."
        for exe, out, err, code in [("xsim", "", "dyld: Library not loaded", 1),
                                    ("xsim", "unknown option", "", 1),
                                    ("xsim", banner, "runtime failure", 1),
                                    ("xsim", banner, "", 2), ("xcc", banner, "", 1)]:
            with self.subTest(exe=exe, err=err, code=code), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                script = fake_xtc_install(root)
                (root / "fake-xtc-bin" / exe).write_text(f"#!/bin/sh\necho '{out}'\necho '{err}' >&2\nexit {code}\n")
                with self.assertRaises(ValueError) as caught:
                    b.activated_xtc_environment(dict(os.environ), script)
                self.assertIn(f"{exe} --version (exit {code})", str(caught.exception))
                self.assertIn(err or out, str(caught.exception))

    def test_incomplete_worker_matrix_skips_review_and_reports_error(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            task = self.conf["tasks"][0]
            first = {"task_id": task["id"], "run_id": "sol-no", "model_label": "sol",
                     "condition": "no_skills_xtc", "repeat": 1, "validation": validation(), "usage": {}}
            second = {"task_id": task["id"], "run_id": "sol-native", "model_label": "sol",
                      "condition": "native_xtc", "repeat": 1, "validation": {"status": "error"},
                      "error": "native skills unavailable", "usage": {}}
            review = b.judge_task(self.conf, self.cfg, root, task, [first, second])
            self.assertEqual("incomplete_candidates", review["status"])
            self.assertFalse((root / "reviews").exists())
            text = b.report(root, self.conf, [first, second], [review]).read_text()
            self.assertIn("INCOMPLETE EXPERIMENT", text)
            self.assertIn("native skills unavailable", text)

    @staticmethod
    def create_experiment(parent, folder, passed, usd):
        root = parent / folder
        b.write_json(root / "experiment.json", {"experiment_id": folder, "started_utc": "2026-11-07T00:00:00Z",
                     "report_config": {"experiment": {"name": "test"}}, "run_order": [{}],
                     "run_options": {"verification_required": True}})
        b.write_json(root / "runs" / "trial-1" / "result.json", {"run_id": "trial-1", "task_id": "uart-heartbeat-specific",
                     "prompt_sha256": "hash-of-prompt", "model_label": "luna", "model": "github-copilot/gpt-6-luna",
                     "condition": "native_xtc", "repeat": 1, "validation": validation(passed),
                     "invocation": {"exit_code": 0, "elapsed_seconds": 35},
                     "usage": {"copilot_estimated_usd": usd}})
        return root

    def test_index_and_compare_are_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            first = self.create_experiment(base, "first", True, .02)
            second = self.create_experiment(base, "second", False, .06)
            db = store.connect(base / "results.sqlite3")
            try:
                for root in (first, second, second):
                    store.index_experiment(db, root)
                self.assertEqual(2, db.execute("SELECT COUNT(*) FROM trials").fetchone()[0])
                self.assertIn("first", "\n".join(store.history(db)))
                self.assertIn("hash-of-pro", "\n".join(store.trend(db)))
                self.assertIn("1/2", "\n".join(store.compare(db)))
                self.assertEqual((2, 2), store.reindex(db, base))
            finally:
                db.close()

    def test_full_fake_run_checks_all_six_workers_with_same_verifier(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            fake = root / "fake-opencode"
            fake.write_bytes((Path(__file__).parent / "fixtures" / "fake_opencode.py").read_bytes())
            fake.chmod(0o755)
            skill = root / "skills" / "example"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text("---\nname: example\n---\nTest")
            conf = copy.deepcopy(self.conf)
            conf["experiment"]["opencode_command"] = str(fake)
            results = root / "results"
            with patch.object(b, "load_config", return_value=conf), \
                    patch.object(verifier, "verify", side_effect=lambda *a, **kw: observations()) as check, \
                    patch.object(sys, "argv", ["benchmark.py", "run", "--progress-every", "0", "--results-root", str(results),
                     "--skills-dir", str(root / "skills"), "--xtc-env", str(fake_xtc_install(root))]):
                self.assertEqual(0, b.main())
                self.assertEqual(6, check.call_count)
            manifest_path = next(results.glob("*/experiment.json"))
            manifest = json.loads(manifest_path.read_text())
            self.assertFalse(manifest["run_options"]["smoke"])
            self.assertTrue(manifest["run_options"]["verification_required"])
            self.assertEqual(6, len(manifest["run_order"]))
            for file in manifest_path.parent.glob("runs/*/result.json"):
                result = json.loads(file.read_text())
                self.assertEqual("passed", result["validation"]["status"])
                self.assertTrue(result["xtc_preconfigured"])
            report = (manifest_path.parent / "REPORT.rst").read_text()
            self.assertIn("6 passed", report)
            self.assertNotIn("UNVERIFIED", report)


if __name__ == "__main__":
    unittest.main()
