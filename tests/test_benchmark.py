import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import benchmark as b


class BenchmarkTests(unittest.TestCase):
    def setUp(self):
        self.config_path = Path(__file__).resolve().parents[1] / "benchmark.toml"
        self.conf = b.load_config(self.config_path)

    def test_matrix_and_distinct_reviewer(self):
        matrix = b.plan(self.conf)
        self.assertEqual(18, len(matrix))
        self.assertEqual(3, len(self.conf["tasks"]))
        self.assertEqual({"specific", "everyday", "vague"},
                         {t["specificity"] for t in self.conf["tasks"]})
        self.assertNotIn(self.conf["models"]["reviewer"], self.conf["models"]["workers"].values())
        self.conf["models"]["reviewer"] = self.conf["models"]["workers"]["sol"]
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "config.toml"
            p.write_text('[models]\nreviewer="github-copilot/same"\n[models.workers]\na="github-copilot/same"\n[[tasks]]\nid="one"\n', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Reviewer model"):
                b.load_config(p)

    def test_user_prompts_are_non_prescriptive_and_distinct(self):
        prompts = {}
        for task in self.conf["tasks"]:
            user_text = b.relpath(task["prompt"], self.config_path).read_text(encoding="utf-8")
            prompts[task["specificity"]] = user_text
            self.assertIn("XU316", user_text)
            for forbidden in ("xcommon", "lib_xcore", "Pyxsim", "CMake", "hardware thread", "XC main"):
                self.assertNotIn(forbidden.lower(), user_text.lower())
        self.assertIn("115200", prompts["specific"])
        self.assertIn("115200", prompts["everyday"])
        self.assertNotIn("115200", prompts["vague"])
        self.assertLess(len(prompts["vague"]), len(prompts["specific"]))

    def test_each_variant_has_six_worker_conditions(self):
        from collections import Counter
        matrix = b.plan(self.conf)
        self.assertEqual({t["id"]: 6 for t in self.conf["tasks"]},
                         dict(Counter(t["id"] for t, *_ in matrix)))

    def test_pricing_caches_not_double_counts_reasoning(self):
        export = {"session": {"tokens": {"input": 1_000_000, "output": 100_000,
                    "reasoning": 75_000, "cache": {"read": 200_000, "write": 10_000}}, "cost": 0.23}}
        rates = self.conf["pricing"]["github-copilot/gpt-6.1-sol"]
        usage = b.session_usage(export, [], rates)
        expected = 2 + 1 + 0.02 + 0.025
        self.assertAlmostEqual(expected, usage["copilot_estimated_usd"], places=5)
        self.assertEqual(75_000, usage["tokens"]["reasoning"])
        self.assertIsNone(usage["actual_additional_billed_usd"])

    def test_skill_tool_calls(self):
        parts = [{"type": "tool", "tool": "skill", "callID": "c1", "state": {"status": "completed", "input": {"id": "xmos-testing"}}},
                 {"type": "tool", "tool": "xcore-skills_load_skill", "callID": "c2", "state": {"status": "completed", "input": {"name": "xmos-run-and-simulate"}}}]
        calls = b.extract_skill_calls(parts)
        self.assertEqual(2, len(calls))
        self.assertEqual("xmos-testing", calls[0]["skill_or_query"])

    def test_jsonl_activity_counts_incrementally_and_redacts_content(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "events.jsonl"
            tracker = b.JsonlActivity(path)
            self.assertEqual(0, tracker.update()["events"])
            with path.open("wb") as file:
                file.write(b'{"type":"step_start","secret":"do-not-print"}\n')
                file.write(b'{"type":"tool_use","part":{"type":"tool","tool":"bash","callID":"a1"}}')
            state = tracker.update()
            self.assertEqual(1, state["events"])
            self.assertEqual(0, state["tools"])
            with path.open("ab") as file:
                file.write(b'\n{"type":"tool_use","part":{"type":"tool","tool":"bash","callID":"a1"}}\n')
            state = tracker.update()
            self.assertEqual(3, state["events"])
            self.assertEqual(1, state["tools"])
            self.assertEqual(state, tracker.update())
            self.assertNotIn("do-not-print", repr(state))

    def test_launch_progress_heartbeats_and_wall_clock(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            events = root / "events.jsonl"
            ticks = []
            script = ("import json,time; "
                      "print(json.dumps({'type':'step_start','sessionID':'ses_test'}),flush=True); "
                      "time.sleep(0.23)")
            result = b.launch([sys.executable, "-c", script], root, 5, os.environ.copy(),
                              events, root / "stderr.log", on_tick=ticks.append,
                              progress_interval=0.05)
            self.assertEqual(0, result["exit_code"])
            self.assertFalse(result["timed_out"])
            self.assertGreaterEqual(len(ticks), 2)
            self.assertLess(ticks[0], ticks[-1])
            self.assertGreaterEqual(b.JsonlActivity(events).update()["events"], 1)
            self.assertEqual("00:01:01", b.elapsed_display(61))

    def test_launch_overrides_stale_pwd_for_isolated_workspace(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            workspace = root / "isolated-worker"
            workspace.mkdir()
            env = os.environ.copy()
            env["PWD"] = str(root)  # Emulate invoking benchmark from its repo.
            out = root / "worker-events.jsonl"
            script = ("import json,os; "
                      "print(json.dumps({'cwd':os.getcwd(),'PWD':os.environ.get('PWD')}))")
            result = b.launch([sys.executable, "-c", script], workspace, 5,
                              env, out, root / "worker-stderr.log")
            self.assertEqual(0, result["exit_code"])
            data = json.loads(out.read_text().strip())
            self.assertEqual(str(workspace.resolve()), data["cwd"])
            self.assertEqual(data["cwd"], data["PWD"])
            self.assertEqual(str(root), env["PWD"], "Caller environment must remain unchanged")

    def test_verifier_prints_progress_for_slow_commands(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            messages = []
            task = {"verify_commands": [f'{sys.executable} -c "import time;time.sleep(0.22)"']}
            result = b.verify_candidate(task, root, os.environ.copy(), root, 20,
                                        progress=messages.append, progress_interval=0.05)
            self.assertEqual("passed", result["status"])
            self.assertTrue(any("still running" in m for m in messages), messages)
            self.assertTrue(any("exit=0" in m for m in messages), messages)

    def test_mcp_condition_uses_v2_mcp_servers_config(self):
        with tempfile.TemporaryDirectory() as td:
            workspace = Path(td) / "worker"
            conf = b.load_config(self.config_path)
            conf["conditions"] = dict(conf["conditions"])
            conf["conditions"]["mcp"] = {
                "type": "mcp", "url": "http://127.0.0.1:9000/mcp",
                "server_name": "xcore-skills"
            }
            conf_path, _ = b.prepare_project(workspace, conf["tasks"][0], self.config_path, "mcp", conf)
            client = json.loads(conf_path.read_text())
            self.assertEqual("http://127.0.0.1:9000/mcp",
                client["mcp"]["servers"]["xcore-skills"]["url"])
            self.assertTrue(any(x["action"] == "skill" and x["effect"] == "deny"
                                for x in client["permissions"]))

    def test_anonymization_excludes_wrong_worker_model(self):
        good = {"task_id": "a", "run_id": "good", "requested_model_mismatch": False}
        bad = {"task_id": "a", "run_id": "bad", "requested_model_mismatch": True}
        candidates = b.anonymize_candidates([good, bad], 42, "a")
        self.assertEqual(["good"], [x["run"]["run_id"] for x in candidates])

    def test_snapshot_never_includes_native_skills_or_config(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "src"
            dst = Path(td) / "dst"
            (src / ".opencode" / "skills" / "x").mkdir(parents=True)
            (src / ".opencode" / "skills" / "x" / "SKILL.md").write_text("secret", encoding="utf-8")
            (src / "opencode.json").write_text('{"model":"secret"}', encoding="utf-8")
            (src / "main.c").write_text("int main(){}", encoding="utf-8")
            metadata = b.snapshot_project(src, dst)
            self.assertEqual(1, metadata["copied_files"])
            self.assertTrue((dst / "main.c").exists())
            self.assertFalse((dst / "opencode.json").exists())
            self.assertFalse((dst / ".opencode").exists())

    def test_verifier_overwrites_worker_forged_results(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            # A worker's claimed "pass" must be deleted before trusted validator.
            (d / "benchmark-verification.json").write_text('{"passed":true,"metrics":{"threads_per_tile":{"0":1}}}', encoding="utf-8")
            out = d / "result"
            out.mkdir()
            task = {"verify_commands": ["python3 -c 'import sys;sys.exit(1)'"]}
            res = b.verify_candidate(task, d, {}, out, 10)
            self.assertEqual("failed", res["status"])
            self.assertNotIn("observations", res)
            self.assertFalse((d / "benchmark-verification.json").exists())

    def test_unconfigured_verifier_is_never_pass(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            res = b.verify_candidate({"verify_commands": []}, d, {}, d, 10)
            self.assertEqual("unverified", res["status"])


    def test_report_includes_brief_methodology_and_frozen_model_profiles(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            task = self.conf["tasks"][0]
            snap = b.report_config_snapshot(self.conf, {task["id"]})
            b.write_json(root / "experiment.json", {
                "started_utc": "2026-11-06T15:00:00Z",
                "configuration_sha256": "abc123",
                "report_config": snap,
            })
            row = {"run_id": "example", "task_id": task["id"], "model_label": "sol",
                   "model": "github-copilot/gpt-6.1-sol", "condition": "no_skills", "repeat": 1,
                   "validation": {"status": "unverified"}, "usage": {}, "skill_calls": []}
            # Simulate config changes AFTER the run; report must retain frozen IDs.
            changed = b.load_config(self.config_path)
            changed["models"]["reviewer"] = "github-copilot/different-future-model"
            changed["tasks"][0]["rationale"] = "REWRITTEN_LATER"
            report = b.report(root, changed, [row], [])
            self.assertEqual("REPORT.rst", report.name)
            rst = report.read_text(encoding="utf-8")
            self.assertIn("Gladiators - AI Coding-Agent Benchmark", rst)
            self.assertIn("Are you not entertained?", rst)
            self.assertIn("Gladiators, you will go on my second whistle.", rst)
            self.assertIn("Results - scoreboard", rst)
            self.assertIn("The Duel - skills versus no skills", rst)
            self.assertIn("Travelator - independently verified finish times", rst)
            self.assertIn("No independently verified paired comparison", rst)
            self.assertIn("GitHub Copilot", rst)
            self.assertIn(".. list-table::", rst)
            self.assertIn("Independent verifier observations", rst)
            self.assertNotIn("```", rst)
            self.assertIn("Method at a glance\n------------------", rst)
            self.assertLess(rst.index("Results - scoreboard"), rst.index("Method at a glance"))
            self.assertIn("Appendix - detailed evidence and reproducibility", rst)
            self.assertIn("docs/EXPERIMENT_DESIGN.md", rst)
            self.assertIn("docs/REPORTING.md", rst)
            self.assertNotIn("Execution route and study factors", rst)
            self.assertNotIn("Copilot token pricing assumptions", rst)
            self.assertIn("Claude Opus 5.5", rst)
            self.assertIn("Anthropic", rst)
            self.assertIn("OpenAI", rst)
            self.assertIn("Lightweight", rst)
            self.assertNotIn("Not publicly disclosed", rst)
            self.assertNotIn("Size", rst)
            self.assertIn("GPT-6 Astra", rst)
            self.assertIn("experiment.json", rst)
            self.assertIn("UART", rst)
            self.assertNotIn("different-future-model", rst)
            self.assertNotIn("REWRITTEN_LATER", rst)

    def test_missing_model_profile_is_marked_unknown(self):
        profile = b.model_profile(self.conf, "github-copilot/other")
        self.assertEqual("Not documented", profile["developer"])
        self.assertIn("not", profile["quality_expectation"].lower())

    def test_fake_opencode_end_to_end_native_and_blind_review(self):
        # Fully local fake CLI: checks orchestration without Copilot credentials or XTC.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            fake = root / "fake-opencode"
            fake.write_bytes((Path(__file__).resolve().parent / "fixtures" / "fake_opencode.py").read_bytes())
            fake.chmod(0o755)
            src = root / "skills" / "xmos-test"
            src.mkdir(parents=True)
            (src / "SKILL.md").write_text('---\nname: xmos-test\ndescription: test\n---\nDo a test', encoding='utf-8')
            conf = b.load_config(self.config_path)
            conf['experiment'] = dict(conf['experiment'])
            conf['experiment']['opencode_command'] = str(fake)
            conf['experiment']['clean_home'] = ''
            task = dict(conf['tasks'][0])
            task["verify_commands"] = [f"{sys.executable} {Path(__file__).resolve().parent / 'fixtures' / 'fake_verifier.py'}"]
            results = []
            for cond in ('no_skills', 'native'):
                result = b.run_one(conf, self.config_path, root, task, 'sol', 'github-copilot/gpt-6.1-sol',
                                   cond, 1, skills_dir=root / 'skills')
                results.append(result)
                self.assertNotIn('error', result, result.get('error'))
                self.assertTrue(result['session_exported'])
                self.assertEqual('passed', result['validation']['status'])
                self.assertEqual(2, result['validation']['observations']['metrics']['threads_per_tile']['0'])
                self.assertGreater(result['usage']['copilot_estimated_usd'], 0)
                self.assertEqual(bool(result['skill_calls']), cond == 'native')
            review = b.judge_task(conf, self.config_path, root, task, results)
            self.assertEqual('reviewed', review['status'])
            self.assertEqual(2, len(review['blind_mapping']))
            judge_dir = root / 'reviews' / task['id'] / 'judge-work' / 'candidates'
            for candidate in judge_dir.iterdir():
                self.assertFalse((candidate / 'source' / 'opencode.json').exists())
                self.assertFalse((candidate / 'source' / '.opencode').exists())


if __name__ == "__main__":
    unittest.main()
