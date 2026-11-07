"""Two skill sources and verified baseline reuse without duplicate observations."""
import copy
import hashlib
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import benchmark as b
import results_store as store
import rst_report
from experiment_reuse import copy_trial, reusable_trials, skill_fingerprint, tree_digest, trial_id
from fixtures.fake_verifier import observations, validation
from verification import uart_heartbeat as verifier


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.config_path = Path(b.__file__).parent / "benchmark.toml"
        self.base = b.load_config(self.config_path)
        self.conf = b.comparison_config(self.base)
        self.current = self.skills("current", "Write new code in C")
        self.comparison = self.skills("comparison", "Choose the appropriate language")
        self.env = {"implementation_verifier": verifier.protocol(), "platform": "test-platform",
                    "python_version": "test-python", "opencode_version": "test-opencode",
                    "xtc_preactivated_version": "test-xcc", "xtc_activation_script_sha256": "activation-hash",
                    "xskills_revision": "revision", **skill_fingerprint(self.current),
                    "skill_sources": {"native_xtc": skill_fingerprint(self.current) | {"xskills_revision": "revision"},
                                      "native_compare_xtc": skill_fingerprint(self.comparison) | {"xskills_revision": "revision"}}}
        self.matrix = b.plan(self.conf)
        self.prompts = {trial_id(row): b.worker_prompt(row[0], self.config_path, row[3]) for row in self.matrix}
        self.source = self.root / "original"
        old_env = copy.deepcopy(self.env)
        old_env["skill_sources"].pop("native_compare_xtc")
        manifest = {"experiment_id": "original", "environment": old_env,
                    "report_config": b.report_config_snapshot(self.base, {self.base["tasks"][0]["id"]}),
                    "task_inputs": {t["id"]: {"starter_sha256": tree_digest(b.relpath(t["starter"], self.config_path))}
                                    for t in self.base["tasks"]}, "run_order": [{}] * 6}
        b.write_json(self.source / "experiment.json", manifest)
        for row in b.plan(self.base):
            self.archive(row)

    def skills(self, folder, text):
        root = self.root / folder
        (root / "xmos-test" / "scripts").mkdir(parents=True)
        (root / "xmos-test" / "SKILL.md").write_text("---\nname: xmos-test\n---\n" + text)
        (root / "xmos-test" / "scripts" / "helper.py").write_text("# shared helper\n")
        return root

    def archive(self, row, root=None):
        task, label, model, condition, repeat = row
        root = root or self.source
        run = root / "runs" / trial_id(row)
        for folder in (run / "source" / "src", run / "verification" / "verification-test" / "src"):
            folder.mkdir(parents=True)
            (folder / "main.xc").write_text("// synthetic test-only implementation\n")
        (run / "worker_prompt.txt").write_text(self.prompts[trial_id(row)])
        entry = {"run_id": trial_id(row), "task_id": task["id"], "model_label": label, "model": model,
                 "condition": condition, "condition_label": self.conf["conditions"][condition].get("display_name"),
                 "repeat": repeat, "evaluation_class": task["evaluation_class"], "task_revision": task["revision"],
                 "xtc_preconfigured": True,
                 "prompt_sha256": hashlib.sha256(b.relpath(task["prompt"], self.config_path).read_bytes()).hexdigest(),
                 "invocation": {"exit_code": 0, "timed_out": False, "elapsed_seconds": 10},
                 "validation": validation(not (label == "luna" and condition == "native_xtc")),
                 "usage": {"copilot_estimated_usd": .01, "copilot_estimated_ai_credits": 1}, "skill_calls": []}
        b.write_json(run / "result.json", entry)
        b.write_json(run / "verification" / "verification-test" / "result.json", entry["validation"]["observations"])
        return entry

    def reuse(self):
        return reusable_trials(self.source, self.matrix, self.conf, self.config_path, self.env, self.prompts)

    def test_nine_conditions_and_independent_skill_installation(self):
        self.assertEqual(9, len(self.matrix))
        self.assertEqual(2, len(self.base["conditions"]))
        for condition, directory in [("native_xtc", self.current), ("native_compare_xtc", self.comparison)]:
            workspace = self.root / condition
            config, _ = b.prepare_project(workspace, self.conf["tasks"][0], self.config_path, condition, self.conf, directory)
            self.assertEqual((directory / "xmos-test" / "SKILL.md").read_bytes(),
                             (workspace / ".opencode" / "skills" / "xmos-test" / "SKILL.md").read_bytes())
            self.assertNotIn({"action": "skill", "resource": "*", "effect": "deny"}, json.loads(config.read_text())["permissions"])

    def test_identical_folders_fail_before_model_calls_but_can_be_planned(self):
        same = self.skills("same", "Write new code in C")
        argv = ["benchmark.py", "run", "--skills-dir", str(self.current), "--compare-skills-dir", str(same),
                "--results-root", str(self.root / "results")]
        with patch.object(b, "launch") as launch, patch("sys.argv", argv):
            with self.assertRaisesRegex(ValueError, "identical contents"):
                b.main()
            launch.assert_not_called()
        self.assertFalse((self.root / "results").exists())
        with patch.object(b, "launch") as launch, patch("sys.argv", [*argv[:1], "plan", *argv[2:],
                "--xtc-env", str(self.config_path)]), redirect_stdout(io.StringIO()) as output:
            self.assertEqual(0, b.main())
            self.assertIn("identical contents", output.getvalue())
            launch.assert_not_called()
        self.assertEqual(skill_fingerprint(self.current), skill_fingerprint(same))
        (same / "xmos-test" / "scripts" / "helper.py").write_text("# changed resource\n")
        self.assertEqual(skill_fingerprint(self.current)["skills_sha256"], skill_fingerprint(same)["skills_sha256"])
        self.assertNotEqual(skill_fingerprint(self.current)["skills_tree_sha256"], skill_fingerprint(same)["skills_tree_sha256"])

    def test_six_baselines_reused_including_original_failure_without_modifying_archive(self):
        before = tree_digest(self.source)
        reuse = self.reuse()
        self.assertEqual(6, len(reuse))
        self.assertEqual(3, len(self.matrix) - len(reuse))
        failed = next(value for value in reuse.values() if value["entry"]["validation"]["status"] == "failed")
        entry = copy_trial(failed, self.root / "combined", "Current skills + XTC")
        self.assertEqual("failed", entry["validation"]["status"])
        self.assertEqual("original", entry["reused_from"]["experiment_id"])
        self.assertEqual(before, tree_digest(self.source))

    def test_incompatible_metadata_is_rejected_not_silently_reused(self):
        manifest_path = self.source / "experiment.json"
        original = json.loads(manifest_path.read_text())
        mutations = [("opencode_version", "different"), ("xtc_activation_script_sha256", "different"),
                     ("python_version", "different"), ("platform", "different")]
        for key, value in mutations:
            changed = copy.deepcopy(original)
            changed["environment"][key] = value
            b.write_json(manifest_path, changed)
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "Cannot reuse"):
                self.reuse()
        b.write_json(manifest_path, original)
        for key in ("worker_timeout_seconds", "verifier_timeout_seconds", "seed"):
            changed = copy.deepcopy(original)
            changed["report_config"]["experiment"][key] += 1
            b.write_json(manifest_path, changed)
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "Cannot reuse"):
                self.reuse()
        b.write_json(manifest_path, original)
        changed = copy.deepcopy(original)
        changed["environment"]["skill_sources"]["native_xtc"]["skills_tree_sha256"] = "different"
        b.write_json(manifest_path, changed)
        with self.assertRaisesRegex(ValueError, "skill resources"):
            self.reuse()

    def test_tampered_sources_prompt_and_protocol_are_rejected(self):
        row = b.plan(self.base)[0]
        run = self.source / "runs" / trial_id(row)
        file = run / "source" / "src" / "main.xc"
        original = file.read_text()
        file.write_text("// changed after verification\n")
        with self.assertRaisesRegex(ValueError, "archived source"):
            self.reuse()
        file.write_text(original)
        prompt = run / "worker_prompt.txt"
        prompt.write_text("different instructions")
        with self.assertRaisesRegex(ValueError, "worker instructions"):
            self.reuse()
        prompt.write_text(self.prompts[trial_id(row)])
        result = json.loads((run / "result.json").read_text())
        result["validation"]["observations"]["protocol"]["verifier_sha256"] = "0" * 64
        b.write_json(run / "result.json", result)
        with self.assertRaisesRegex(ValueError, "verifier protocol"):
            self.reuse()

    def test_old_skill_md_only_fingerprint_scope_is_explicit(self):
        path = self.source / "experiment.json"
        manifest = json.loads(path.read_text())
        manifest["environment"].pop("skill_sources")
        manifest["environment"].pop("skills_tree_sha256")
        b.write_json(path, manifest)
        reuse = self.reuse()
        self.assertEqual(6, len(reuse))
        current = next(v for v in reuse.values() if v["entry"]["condition"] == "native_xtc")
        self.assertIn("cannot be retrospectively attested", current["notes"][0])

    def test_reuse_cannot_disable_fresh_review(self):
        with patch("sys.argv", ["benchmark.py", "run", "--reuse-results", str(self.source), "--no-review"]), \
                patch.object(b, "launch") as launch, redirect_stdout(io.StringIO()), patch("sys.stderr", new=io.StringIO()):
            with self.assertRaises(SystemExit):
                b.main()
            launch.assert_not_called()

    def test_missing_receipt_and_changed_archive_after_preflight_are_rejected(self):
        reuse = self.reuse()
        record = next(iter(reuse.values()))
        file = record["directory"] / "verification" / "verification-test" / "result.json"
        file.unlink()
        with self.assertRaisesRegex(ValueError, "archived verifier receipt"):
            self.reuse()
        with self.assertRaisesRegex(ValueError, "changed after reuse preflight"):
            copy_trial(record, self.root / "combined", None)

    def test_reused_copies_cannot_be_reused_transitively(self):
        record = next(iter(self.reuse().values()))
        copy_trial(record, self.root / "combined", None)
        copied = self.root / "combined" / "runs" / record["entry"]["run_id"] / "result.json"
        original = record["directory"] / "result.json"
        original.write_bytes(copied.read_bytes())
        with self.assertRaisesRegex(ValueError, "original source archive"):
            self.reuse()

    def test_missing_evidence_is_rejected(self):
        run = self.source / "runs" / trial_id(b.plan(self.base)[0])
        entry = json.loads((run / "result.json").read_text())
        entry["validation"]["observations"]["metrics"]["evidence"] = ["verification-test/missing.vcd"]
        b.write_json(run / "result.json", entry)
        b.write_json(run / "verification" / "verification-test" / "result.json", entry["validation"]["observations"])
        with self.assertRaisesRegex(ValueError, "missing or invalid archived verifier evidence"):
            self.reuse()

    def test_partial_comparison_does_not_fall_back_to_no_skills_pairing(self):
        entries = [value["entry"] for value in self.reuse().values()]
        manifest = {"run_order": [{}] * 9}
        path = rst_report.build_report(self.source, self.conf, manifest, entries, [], b.now_iso, b.model_profile)
        text = path.read_text()
        section = text.split("Did the comparison skills change results?", 1)[1].split("Blind design ranking", 1)[0]
        self.assertIn("no completed matched pair", section)
        self.assertNotIn("Skills-enabled run took", section)

    def test_index_does_not_count_reuse_as_new_trials_or_new_cost(self):
        combined = self.root / "combined"
        b.write_json(combined / "experiment.json", {"experiment_id": "combined", "environment": self.env,
                     "report_config": b.report_config_snapshot(self.conf, {self.base["tasks"][0]["id"]}),
                     "run_order": [{}] * 9})
        for record in self.reuse().values():
            copy_trial(record, combined, "Current skills + XTC")
        for row in self.matrix:
            if row[3] == "native_compare_xtc":
                self.archive(row, combined)
        db = store.connect(self.root / "results.sqlite3")
        try:
            store.index_experiment(db, self.source)
            store.index_experiment(db, combined)
            self.assertEqual(15, db.execute("SELECT COUNT(*) FROM trials").fetchone()[0])
            self.assertEqual(9, db.execute("SELECT COUNT(*) FROM trials WHERE is_reused=0").fetchone()[0])
            self.assertEqual(6, db.execute("SELECT COUNT(*) FROM trials WHERE is_reused=1").fetchone()[0])
            output = "\n".join(store.compare(db))
            self.assertIn("not independent repetitions", output)
            self.assertEqual(9, len(store.trend(db)) - 2)
            combined_history = next(line for line in store.history(db) if line.startswith("combined "))
            self.assertIn("$  0.0300", combined_history)
        finally:
            db.close()

    def test_cli_runs_only_three_new_workers_and_one_fresh_review(self):
        fake = self.root / "fake-opencode"
        fake.write_bytes((Path(__file__).parent / "fixtures" / "fake_opencode.py").read_bytes())
        fake.chmod(0o755)
        conf = copy.deepcopy(self.base)
        conf["experiment"]["opencode_command"] = str(fake)
        tools = self.root / "tools"
        tools.mkdir()
        for name in ("xcc", "xsim"):
            path = tools / name
            path.write_text("#!/bin/sh\necho 'XTC version: 15.3.1'\n")
            path.chmod(0o755)
        script = self.root / "SetEnv.sh"
        script.write_text(f'export PATH="{tools}:$PATH"\n')
        results = self.root / "results"
        argv = ["benchmark.py", "run", "--skills-dir", str(self.current), "--compare-skills-dir", str(self.comparison),
                "--reuse-results", str(self.source), "--xtc-env", str(script), "--results-root", str(results),
                "--progress-every", "0"]
        with patch.object(b, "load_config", return_value=conf), patch.object(b, "study_environment", return_value=self.env), \
                patch.object(verifier, "verify", side_effect=lambda *a, **kw: observations()) as check, \
                patch.object(b, "launch", wraps=b.launch) as launch, patch("sys.argv", argv), redirect_stdout(io.StringIO()):
            self.assertEqual(0, b.main())
            self.assertEqual(3, check.call_count)
            self.assertEqual(4, launch.call_count, "Three new workers plus one fresh combined reviewer")
        root = next(results.glob("*/experiment.json")).parent
        entries = [json.loads(p.read_text()) for p in root.glob("runs/*/result.json")]
        self.assertEqual(9, len(entries))
        self.assertEqual(6, sum(bool(e.get("reused_from")) for e in entries))
        text = (root / "REPORT.rst").read_text()
        self.assertIn("6 previous worker results and 3 new worker results", text)
        self.assertIn("estimated new usage: $", text.lower())
        self.assertIn("Current skills + XTC", text)
        self.assertIn("Comparison skills + XTC", text)
        self.assertIn("Did the comparison skills change results?", text)
        self.assertIn("Origin", text)
        self.assertIn("Original independent verification evidence is copied, not rerun", text)
        reviews = [json.loads(p.read_text()) for p in root.glob("reviews/*/review-result.json")]
        self.assertEqual(9, len(reviews[0]["blind_mapping"]))
        new_cost = sum(e["usage"].get("copilot_estimated_usd", 0) for e in entries if not e.get("reused_from"))
        new_cost += sum(e["usage"].get("copilot_estimated_usd", 0) for e in reviews)
        self.assertIn(f"estimated new usage: ${new_cost:.6f}", text)
        self.assertIn("Historical reused worker usage: $0.060000", text)
        changed_conf = copy.deepcopy(self.base)
        changed_conf["models"]["workers"] = {"other": "github-copilot/other"}
        b.report(root, changed_conf, entries, reviews)
        regenerated = (root / "REPORT.rst").read_text()
        self.assertIn("3 models, 3 conditions", regenerated)


if __name__ == "__main__":
    unittest.main()
