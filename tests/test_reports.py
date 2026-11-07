"""Strict, readable reporting: only independently checked implementations."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

import benchmark as b
import rst_report as report
from fixtures.fake_verifier import validation


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.conf = b.load_config(Path(b.__file__).parent / "benchmark.toml")
        self.task = self.conf["tasks"][0]["id"]

    def entry(self, condition, seconds=100, credits=10, model="sol", trial=1, task=None, passed=True):
        task = task or self.task
        return {"run_id": f"{task}--{model}--{condition}--r{trial}", "task_id": task,
                "model_label": model, "model": self.conf["models"]["workers"][model],
                "condition": condition, "repeat": trial, "evaluation_class": "implementation",
                "invocation": {"exit_code": 0, "timed_out": False, "elapsed_seconds": seconds},
                "validation": validation(passed),
                "usage": {"copilot_estimated_ai_credits": credits, "copilot_estimated_usd": credits / 100}}

    def review(self, ordered):
        aliases = [f"candidate_{i:02d}" for i in range(1, len(ordered) + 1)]
        return {"task_id": self.task, "status": "reviewed", "evaluation_class": "implementation",
                "blind_mapping": {a: e["run_id"] for a, e in zip(aliases, ordered)},
                "review": {"ranked_ids": aliases,
                           "candidates": [{"id": a, "summary": "Good design compared with candidate_01. "
                                           "Full source-level detail.", "ratings": {"architecture": 4},
                                           "risks": ["First risk.", "Second risk."]} for a in aliases],
                           "review_notes": "RAW_REVIEW_NOTES " * 80}}

    def render(self, entries, reviews=None, manifest=None):
        return report.build_report(self.root, self.conf, manifest, entries, reviews or [],
                                   lambda: "2026-11-07T00:00:00Z", b.model_profile).read_text()

    def test_readable_ranked_tables_and_audit_details(self):
        no = self.entry("no_skills_xtc", 570.81, 38.3096)
        skills = self.entry("native_xtc", 342.75, 33.9418)
        text = self.render([skills, no], [self.review([no, skills])])
        main, appendix = text.split("Appendix - detailed evidence and reproducibility", 1)
        scoreboard = main.split("Results at a glance", 1)[1].split("Did skills help?", 1)[0]
        self.assertLess(scoreboard.index("   * - 1\n"), scoreboard.index("   * - 2\n"))
        self.assertIn(":widths: 6 9 20 8 14 12 19", scoreboard)
        self.assertIn("Independent check", scoreboard)
        self.assertNotIn("candidate_", main)
        self.assertNotIn(no["run_id"], main)
        self.assertNotIn("RAW_REVIEW_NOTES", main)
        self.assertIn(no["run_id"], appendix)
        self.assertIn("RAW_REVIEW_NOTES", appendix)
        self.assertIn("Rank 1 — Sol / No skills + XTC", appendix)
        self.assertIn("Criterion\n     - Rating (0-5)", appendix)
        self.assertIn(":widths: 82 18", appendix)
        self.assertIn("- First risk.\n- Second risk.", appendix)
        self.assertNotIn("Ratings: ``{", text)
        self.assertNotIn("Are you not entertained?", text)
        self.assertNotIn("Did preactivating XTC help?", text)
        self.assertNotIn(":widths: auto", text)

    def test_effort_and_verified_outcomes_are_separate(self):
        no = self.entry("no_skills_xtc", 570.81, 38.3096)
        skills = self.entry("native_xtc", 342.75, 33.9418)
        text = self.render([no, skills], [self.review([no, skills])])
        self.assertIn("Skills-enabled run took 5m 43s versus 9m 31s", text)
        self.assertIn("40% faster", text)
        self.assertIn("33.94 versus 38.31 AI Credits (11% lower)", text)
        self.assertIn("XTC was preactivated for both", text)
        self.assertIn("No skills: passed; skills enabled: passed.", text)
        self.assertIn("Blind design rank: 2 with skills, 1 without", text)

    def test_fast_failed_submission_cannot_win_on_time_or_cost(self):
        failed = self.entry("no_skills_xtc", 2, 0.01, model="luna", passed=False)
        passed = self.entry("native_xtc", 253.77, 18.8805)
        text = self.render([failed, passed])
        summary = text.split("Results at a glance", 1)[0]
        self.assertIn("Sol / Skills + XTC, 4m 14s", summary)
        self.assertIn("Sol / Skills + XTC, 18.88 AI Credits", summary)
        self.assertNotIn("Luna / No skills + XTC, 2s", summary)

    def test_missing_or_unverified_results_are_rejected_without_rewriting_report(self):
        entry = self.entry("native_xtc")
        path = self.root / "REPORT.rst"
        path.write_text("Existing report")
        for bad in [{}, {"status": "unverified"}, {"status": "passed"},
                    {"status": "failed", "observations": {"passed": False}}]:
            entry["validation"] = bad
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.render([entry])
            self.assertEqual("Existing report", path.read_text())

    def test_mixed_verifier_versions_and_contradictory_pass_are_rejected(self):
        no = self.entry("no_skills_xtc")
        skills = self.entry("native_xtc")
        skills["validation"]["observations"]["protocol"]["verifier_sha256"] = "a" * 64
        with self.assertRaisesRegex(ValueError, "different verifier revisions"):
            self.render([no, skills])
        no["validation"]["status"] = "failed"
        with self.assertRaisesRegex(ValueError, "contradicts"):
            self.render([no])

    def test_no_worker_log_fallback_can_create_verification(self):
        entry = self.entry("native_xtc")
        entry["validation"] = {"status": "unverified"}
        logs = self.root / "runs" / entry["run_id"]
        logs.mkdir(parents=True)
        (logs / "events.jsonl").write_text('PASS: everything compiled and simulated successfully\n')
        with self.assertRaises(ValueError):
            self.render([entry])

    def test_missing_telemetry_and_review_do_not_invent_values(self):
        entry = self.entry("native_xtc")
        entry["usage"] = {}
        entry["invocation"].pop("elapsed_seconds")
        main = self.render([entry]).split("Appendix - detailed evidence", 1)[0]
        self.assertIn("not recorded", main)
        self.assertIn("not ranked", main)
        self.assertIn("No blind review was run", main)
        self.assertNotIn("Lowest worker time", main)
        self.assertNotIn("Lowest estimated worker usage", main)

    def test_error_pairs_are_excluded_and_visible(self):
        no = self.entry("no_skills_xtc")
        skills = self.entry("native_xtc", 1, 0.1)
        skills["invocation"]["timed_out"] = True
        skills["validation"] = {"status": "error", "reason": "Worker timed out"}
        text = self.render([no, skills])
        self.assertIn("INCOMPLETE EXPERIMENT", text)
        comparison = text.split("Did skills help?", 1)[1].split("Blind design ranking", 1)[0]
        self.assertIn("no completed matched pair", comparison)
        self.assertNotIn("% faster", comparison)

    def test_repeats_use_matched_medians_and_show_trial(self):
        entries = [self.entry("no_skills_xtc", 100, 10, trial=1), self.entry("native_xtc", 50, 5, trial=1),
                   self.entry("no_skills_xtc", 100, 10, trial=2), self.entry("native_xtc", 70, 7, trial=2),
                   self.entry("no_skills_xtc", 1, 0.1, trial=3)]
        text = self.render(entries)
        self.assertIn("Median across 2 paired trials: Skills-enabled run took 1m 00s versus 1m 40s", text)
        self.assertIn("1 incomplete/error pairs excluded", text)
        self.assertIn("     - Trial\n", text)

    def test_distinct_tasks_are_not_pooled(self):
        template = self.conf["tasks"][0]
        self.conf["tasks"] = [dict(template, id=name) for name in ("task-a", "task-b")]
        entries = [self.entry("native_xtc", 50, task="task-a"), self.entry("no_skills_xtc", 100, task="task-a"),
                   self.entry("native_xtc", 200, task="task-b"), self.entry("no_skills_xtc", 100, task="task-b")]
        text = self.render(entries)
        section = text.split("Did skills help?", 1)[1].split("Blind design ranking", 1)[0]
        self.assertIn("Sol (Task a)", section)
        self.assertIn("50% faster", section)
        self.assertIn("Sol (Task b)", section)
        self.assertIn("100% slower", section)

    def test_verified_finish_time_is_not_worker_time(self):
        no = self.entry("no_skills_xtc", 100, passed=False)
        skills = self.entry("native_xtc", 60)
        skills["time_to_verified_success_s"] = 75
        text = self.render([no, skills])
        self.assertIn("No skills: failed; skills enabled: passed.", text)
        self.assertIn("Skills-enabled run took 1m 00s versus 1m 40s", text)
        self.assertIn("Verified finish seconds", text)
        self.assertIn("     - 75\n", text)

    def test_zero_baseline_and_unknown_mapping(self):
        no = self.entry("no_skills_xtc", 0, 0)
        skills = self.entry("native_xtc", 60, 1)
        review = self.review([no, skills])
        review["blind_mapping"] = {}
        text = self.render([no, skills], [review])
        self.assertIn("percentage unavailable: zero baseline", text)
        self.assertIn("Unknown submission", text)
        self.assertEqual("0s", report._duration(0))
        self.assertEqual("1h 01m 01s", report._duration(3661))
        self.assertEqual("not recorded", report._duration(float("nan")))

    def sources(self, entry, *files):
        root = self.root / "runs" / entry["run_id"] / "source" / "src"
        root.mkdir(parents=True, exist_ok=True)
        for file in files:
            path = root / file
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("// archived submission\n")
        return root

    def test_language_percentages_are_per_condition_and_include_failures(self):
        entries = []
        for model in ("sol", "opus", "luna"):
            for condition in ("no_skills_xtc", "native_xtc"):
                c = model == "luna" and condition == "native_xtc"
                entry = self.entry(condition, model=model, passed=not c)
                entry["skill_calls"] = []
                if condition == "native_xtc" and model != "opus":
                    entry["skill_calls"] = [{"tool": "skill", "status": "completed", "skill_or_query": "xmos-test"}]
                self.sources(entry, "main.c" if c else "main.xc")
                entries.append(entry)
        before = copy.deepcopy(entries)
        text = self.render(entries)
        section = text.split("Language choices and skill consultation", 1)[1].split("Did skills help?", 1)[0]
        self.assertIn("   * - No skills + XTC\n     - 100% (3/3)\n     - 0% (0/3)", section)
        self.assertIn("   * - Skills + XTC\n     - 66.7% (2/3)\n     - 33.3% (1/3)", section)
        self.assertNotIn("   * - Overall", section)
        self.assertIn("Luna, trial 1", section)
        self.assertIn("XC to C; independent outcome passed to failed", section)
        self.assertIn("Skill-use flag — Opus / Skills + XTC", text)
        self.assertIn("None recorded — FLAG", section)
        self.assertEqual(before, entries, "Regeneration must not rewrite results or classifications into the records")

    def test_language_classifier_separates_mixed_unknown_and_host_tests(self):
        entry = self.entry("native_xtc")
        self.assertEqual("Not recorded", report._language(self.root, entry))
        src = self.sources(entry, "main.xc", "header.h")
        host = src.parent / "tests"
        host.mkdir()
        (host / "host.c").write_text("// host checker")
        self.assertEqual("XC", report._language(self.root, entry))
        (src / "helper.c").write_text("// compiled helper")
        self.assertEqual("C + XC", report._language(self.root, entry))
        text = self.render([entry])
        self.assertIn("C + XC", text)
        self.assertIn("100% (1/1)", text)

    def test_skill_usage_distinguishes_disabled_unknown_failed_and_successful_loads(self):
        entry = self.entry("native_xtc")
        self.assertEqual("Not recorded", report._skill_usage(self.root, entry)["label"])
        self.assertFalse(report._skill_usage(self.root, entry)["missing"])
        entry["skill_calls"] = [{"tool": "skill", "status": "error"}]
        self.assertTrue(report._skill_usage(self.root, entry)["missing"])
        entry["skill_calls"].append({"tool": "skill", "status": "completed"})
        self.assertEqual("1 skill load", report._skill_usage(self.root, entry)["label"])
        disabled = self.entry("no_skills_xtc")
        self.assertEqual("Disabled", report._skill_usage(self.root, disabled)["label"])
        self.assertFalse(report._skill_usage(self.root, disabled)["missing"])

    def test_direct_skill_reads_count_as_consultation(self):
        for tool, inputs in [("read", {"path": ".opencode/skills/xmos-test/SKILL.md"}),
                             ("shell", {"command": "cat .opencode/skills/xmos-test/SKILL.md"})]:
            entry = self.entry("native_xtc")
            entry["skill_calls"] = []
            path = self.root / "runs" / entry["run_id"] / "events.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)
            event = {"part": {"tool": tool, "state": {"status": "completed", "input": inputs,
                                                      "output": "# Skill: xmos-test\nUseful guidance"}}}
            path.write_text(json.dumps(event) + "\n")
            with self.subTest(tool=tool):
                usage = report._skill_usage(self.root, entry)
                self.assertEqual("1 skill-file read", usage["label"])
                self.assertFalse(usage["missing"])

    def test_loaded_c_preference_and_saved_runtime_exception_are_reported_without_regrading(self):
        entry = self.entry("native_xtc", passed=False)
        self.sources(entry, "main.c")
        root = self.root / "runs" / entry["run_id"]
        event = {"part": {"tool": "skill", "callID": "s1", "state": {"status": "completed",
                 "input": {"id": "xmos-c-programming"},
                 "output": "**Write new code in C using `lib_xcore`**, not XC, unless required."}}}
        (root / "events.jsonl").write_text(json.dumps(event) + "\n")
        output = root / "verification" / "verification-example"
        output.mkdir(parents=True)
        (output / "simulator.log").write_text("Unhandled exception: ILLEGAL_RESOURCE, data: 0x00010300\n")
        entry["validation"]["observations"]["metrics"]["evidence"] = ["verification-example/simulator.log"]
        text = self.render([entry])
        self.assertIn("Write new code in C using lib_xcore, not XC", text)
        self.assertIn("not that C or the skills caused", text)
        self.assertIn("Unhandled exception: ILLEGAL_RESOURCE", text)
        self.assertIn("1 skill load", text)

    def test_ranking_table_prefers_rationale_to_first_sentence_pass_metrics(self):
        entry = self.entry("native_xtc")
        review = self.review([entry])
        review["review"]["candidates"][0]["summary"] = (
            "PASSED the common verifier: 287 UART frames and zero jitter. "
            "Ranked first for explicit ownership and simple bounded arithmetic. "
            "Full resource details remain in the appendix.")
        text = self.render([entry], [review])
        ranking = text.split("Blind design ranking", 1)[1].split("What we can and cannot conclude", 1)[0]
        self.assertIn("Ranked first for explicit ownership and simple bounded arithmetic", ranking)
        self.assertNotIn("287 UART frames", ranking)
        self.assertIn("287 UART frames", text.split("Appendix - detailed evidence", 1)[1])


if __name__ == "__main__":
    unittest.main()
