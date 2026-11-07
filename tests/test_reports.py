"""Readable reporting without model calls or new correctness claims."""
from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import benchmark as b
import rst_report as report


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.conf = b.load_config(Path(b.__file__).parent / "benchmark.toml")
        self.conf["tasks"] = self.conf["tasks"][:1]
        self.task = self.conf["tasks"][0]["id"]

    def entry(self, condition, seconds=100, credits=10, model="sol", trial=1, task=None):
        task = task or self.task
        return {
            "run_id": f"{task}--{model}--{condition}--r{trial}", "task_id": task,
            "model_label": model, "model": self.conf["models"]["workers"][model],
            "condition": condition, "repeat": trial,
            "invocation": {"exit_code": 0, "timed_out": False, "elapsed_seconds": seconds},
            "validation": {"status": "unverified", "reason": "No independent verifier configured"},
            "usage": {"copilot_estimated_ai_credits": credits, "copilot_estimated_usd": credits / 100},
        }

    def review(self, ordered):
        aliases = [f"candidate_{i:02d}" for i in range(1, len(ordered) + 1)]
        return {
            "task_id": self.task, "status": "reviewed",
            "blind_mapping": {a: e["run_id"] for a, e in zip(aliases, ordered)},
            "review": {
                "ranked_ids": aliases,
                "candidates": [{"id": a, "summary": "Good design compared with candidate_01. "
                                "Long source-level detail belongs in the appendix."} for a in aliases],
                "review_notes": "RAW_REVIEW_NOTES " * 80,
            },
        }

    def render(self, entries, reviews=None, manifest=None):
        return report.build_report(self.root, self.conf, manifest, entries, reviews or [],
                                   lambda: "2026-11-07T00:00:00Z", b.model_profile).read_text()

    def write_events(self, entry, outputs, *, status="completed", tool="shell", prose=""):
        path = self.root / "runs" / entry["run_id"] / "events.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        events = [{"type": "text", "part": {"text": prose}}]
        events += [{"type": "tool_use", "part": {"type": "tool", "tool": tool,
                    "state": {"status": status, "output": out}}} for out in outputs]
        path.write_text("not json\n" + "\n".join(json.dumps(e) for e in events) + "\n{unfinished")

    def write_build_sim(self, entry):
        self.write_events(entry, ["[100%] Built target app\n",
                                 "xsim: error: C51188: maximum cycles reached (4000000)",
                                 "PASS: 76 UART frames decoded\nPASS: 12 square-wave half-periods"])

    def test_readable_ranked_table_and_original_audit_details(self):
        no = self.entry("no_skills", 570.81, 38.3096)
        skills = self.entry("native", 342.75, 33.9418)
        review = self.review([no, skills])
        text = self.render([skills, no], [review])
        main, appendix = text.split("Appendix - detailed evidence and reproducibility", 1)
        self.assertIn("Blind design ranking", main)
        self.assertIn("   * - Rank\n     - Model\n     - Condition", main)
        self.assertIn("   * - 1\n     - Sol\n     - No skills", main)
        self.assertNotIn("candidate_", main)
        self.assertNotIn(no["run_id"], main)
        self.assertNotIn("RAW_REVIEW_NOTES", main)
        self.assertIn("candidate_01", appendix)
        self.assertIn(no["run_id"], appendix)
        self.assertIn("RAW_REVIEW_NOTES", appendix)
        self.assertIn("Long source-level detail", appendix)
        self.assertEqual(1, text.count("Appendix - detailed evidence and reproducibility"))

    def test_effort_deltas_are_plain_language_not_correctness_claims(self):
        no = self.entry("no_skills", 570.81, 38.3096)
        skills = self.entry("native", 342.75, 33.9418)
        text = self.render([no, skills], [self.review([no, skills])])
        main = text.split("Appendix - detailed evidence", 1)[0]
        self.assertIn("skills took 5m 43s versus 9m 31s", main)
        self.assertIn("40% faster", main)
        self.assertIn("33.94 versus 38.31 AI Credits (11% lower)", main)
        self.assertIn("Blind design rank: 2 with skills, 1 without", main)
        self.assertIn("All submissions above are independently UNVERIFIED", main)
        self.assertIn("not proof that skills improved correctness", main)

    def test_low_cost_unbuilt_submission_is_not_named_fastest_or_cheapest(self):
        no = self.entry("no_skills", 2, 0.01, model="luna")
        warm = self.entry("no_skills_xtc", 253.77, 18.8805)
        self.write_events(no, ["make: xcc: No such file or directory\nmake: *** [build] Error 1"])
        self.write_build_sim(warm)
        text = self.render([no, warm])
        summary = text.split("Results at a glance", 1)[0]
        self.assertIn("Sol / No skills + XTC, 4m 14s", summary)
        self.assertIn("Sol / No skills + XTC, 18.88 AI Credits", summary)
        self.assertIn("Environment hurdle", summary)
        self.assertNotIn("Luna / No skills, 2s", summary)
        self.assertIn("Compiler not found in worker PATH", report._worker_evidence(self.root, no))

    def test_worker_evidence_ignores_prose_and_non_shell_or_unfinished_tools(self):
        entry = self.entry("native")
        outputs = ["[100%] Built target app\nC51188: maximum cycles reached\nPASS: UART frames"]
        for status, tool in [("running", "shell"), ("completed", "read")]:
            with self.subTest(status=status, tool=tool):
                self.write_events(entry, outputs, status=status, tool=tool, prose="Everything compiled and passed.")
                self.assertEqual("No build/waveform pass identified", report._worker_evidence(self.root, entry))
        self.write_events(entry, ["[100%] Built target app", "PASS: UART frames in Python model"])
        self.assertEqual("Build logged; no waveform pass logged", report._worker_evidence(self.root, entry))

    def test_pytest_waveform_output_is_supported_without_counting_assistant_claims(self):
        entry = self.entry("native")
        self.write_events(entry, ["Constraints checks PASSED.", "C51188: maximum cycles reached (6600000)",
                                 "Square: 21 edges\nUART: 125 complete frames\n4 passed in 22.50s"])
        self.assertEqual("Build + waveform checks logged", report._worker_evidence(self.root, entry))
        self.assertEqual("unverified", entry["validation"]["status"])

    def test_missing_telemetry_and_review_do_not_invent_cost_or_success(self):
        entry = self.entry("native")
        entry["usage"] = {}
        entry["invocation"].pop("elapsed_seconds")
        text = self.render([entry])
        main = text.split("Appendix - detailed evidence", 1)[0]
        self.assertIn("not recorded", main)
        self.assertIn("not ranked", main)
        self.assertIn("No blind review was run", main)
        self.assertNotIn("Lowest recorded worker time", main)
        self.assertNotIn("Lowest estimated worker usage", main)

    def test_error_and_timed_out_pairs_are_excluded(self):
        no = self.entry("no_skills")
        skills = self.entry("native", 1, 0.1)
        skills["invocation"]["timed_out"] = True
        text = self.render([no, skills])
        self.assertIn("INCOMPLETE EXPERIMENT", text)
        comparison = text.split("Did skills help?", 1)[1].split("Did preactivating XTC help?", 1)[0]
        self.assertIn("no completed matched pair", comparison)
        self.assertNotIn("faster", comparison)

    def test_repeats_use_only_matched_trials_and_expose_trial_column(self):
        entries = [self.entry("no_skills", 100, 10, trial=1), self.entry("native", 50, 5, trial=1),
                   self.entry("no_skills", 100, 10, trial=2), self.entry("native", 70, 7, trial=2),
                   self.entry("no_skills", 1, 0.1, trial=3)]
        text = self.render(entries)
        self.assertIn("Median across 2 paired trials: skills took 1m 00s versus 1m 40s", text)
        self.assertIn("40% faster", text)
        self.assertIn("1 incomplete/error pairs excluded", text)
        self.assertIn("     - Trial\n", text)

    def test_multiple_tasks_do_not_pool_environment_comparisons(self):
        entries = [self.entry("native", 100, task="task-a"), self.entry("no_skills_xtc", 50, task="task-a"),
                   self.entry("native", 100, task="task-b"), self.entry("no_skills_xtc", 200, task="task-b")]
        text = self.render(entries)
        section = text.split("Skills versus a prepared environment:", 1)[1].split("Blind design ranking", 1)[0]
        self.assertIn("Sol (task-a)", section)
        self.assertIn("50% faster", section)
        self.assertIn("Sol (task-b)", section)
        self.assertIn("100% slower", section)
        self.assertIn("not an isolated skills effect", section)

    def test_verified_outcomes_are_separate_from_worker_duration(self):
        no = self.entry("no_skills", 100)
        skills = self.entry("native", 60)
        no["validation"]["status"] = "failed"
        skills["validation"]["status"] = "passed"
        skills["time_to_verified_success_s"] = 75
        text = self.render([no, skills])
        self.assertIn("skills improved the pass outcome in 1", text)
        self.assertIn("Independent passes in 1 verified pairs: 1 with skills, 0 without", text)
        self.assertIn("skills took 1m 00s versus 1m 40s", text)
        self.assertIn("Verified finish seconds", text)
        self.assertIn("     - 75\n", text)

    def test_zero_and_unknown_baselines_are_not_divided_or_fabricated(self):
        no = self.entry("no_skills", 0, 0)
        skills = self.entry("native", 60, 1)
        text = self.render([no, skills])
        self.assertIn("percentage unavailable: zero baseline", text)
        self.assertEqual("0s", report._duration(0))
        self.assertEqual("1h 01m 01s", report._duration(3661))
        self.assertEqual("not recorded", report._duration(float("nan")))

    def test_unknown_ranking_mapping_is_not_guessed_from_run_id(self):
        entry = self.entry("native")
        review = self.review([entry])
        review["blind_mapping"] = {}
        text = self.render([entry], [review])
        main = text.split("Appendix - detailed evidence", 1)[0]
        self.assertIn("Unknown submission", main)
        self.assertNotIn("Best blind design rank", main)
        self.assertNotIn("candidate_01", main)


if __name__ == "__main__":
    unittest.main()
