from __future__ import annotations

import copy
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import benchmark as b
from verification import uart_heartbeat as v
from fixtures.fake_verifier import observations


def synthetic_trace(*, bit_ticks=None, second_byte=0xA5, square_ticks=50_000, phase=0):
    events = {v.UART: [(0, 1)], v.SQUARE: [(0, 0)],
              v.UART + "_direction": [(0, 1)], v.SQUARE + "_direction": [(0, 1)]}
    tick, fraction, level, frame = 10_000 + phase, 0, 1, 0
    while tick * 6 <= v.TOTAL_CYCLES:
        byte = (0x55, second_byte)[frame % 2]
        for bit in [0] + [(byte >> i) & 1 for i in range(8)] + [1]:
            if bit != level and tick * 6 <= v.TOTAL_CYCLES:
                events[v.UART].append((tick * 6, bit))
                level = bit
            if bit_ticks is None:
                fraction += 1
                tick += 868 + (fraction // 18)
                fraction %= 18
            else:
                tick += bit_ticks
        frame += 1
    level = 0
    for tick in range(square_ticks + phase, v.TOTAL_CYCLES // 6 + 1, square_ticks):
        level ^= 1
        events[v.SQUARE].append((tick * 6, level))
    return events


def write_vcd(path, events, quantum=1):
    names = list(events)
    text = [f"$timescale {1666 // quantum} ps $end"]
    text += [f"$var wire 1 s{i} {name} $end" for i, name in enumerate(names)]
    points = sorted((tick * quantum, f"{value if value is not None else 'x'}s{i}")
                    for i, name in enumerate(names) for tick, value in events[name])
    for tick, value in points:
        text += [f"#{tick}", value]
    path.write_text("\n".join(text) + "\n")


class WaveformTests(unittest.TestCase):
    def test_valid_fractional_and_rounded_uart_schedules(self):
        for bit_ticks, phase in [(None, 0), (868, 1234), (869, 713)]:
            with self.subTest(bit_ticks=bit_ticks, phase=phase):
                metrics = v.check_trace(synthetic_trace(bit_ticks=bit_ticks, phase=phase))
                self.assertGreaterEqual(metrics["uart_frames"], 280)
                self.assertEqual(25, metrics["observation_ms"])

    def test_bad_baud_pattern_and_square_rate_are_rejected(self):
        for kwargs in [{"bit_ticks": 800}, {"bit_ticks": 900}, {"second_byte": 0xA4},
                       {"square_ticks": 49_990}, {"square_ticks": 50_010}]:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                v.check_trace(synthetic_trace(**kwargs))

    def test_glitch_jitter_stuck_and_truncated_waveforms_are_rejected(self):
        for fault in ("glitch", "jitter", "missing-edge", "uart-stop", "square-stop", "unknown", "direction", "tail"):
            events = synthetic_trace()
            if fault == "glitch":
                index = next(i for i, (t, _) in enumerate(events[v.UART]) if t > 2_000_000)
                t, value = events[v.UART][index]
                events[v.UART][index + 1:index + 1] = [(t + 100, value ^ 1), (t + 200, value)]
            elif fault == "jitter":
                t, value = events[v.SQUARE][10]
                events[v.SQUARE][10] = (t + 12, value)
            elif fault == "missing-edge":
                del events[v.SQUARE][10]
            elif fault == "uart-stop":
                events[v.UART] = [(t, x) for t, x in events[v.UART] if t < 9_000_000]
            elif fault == "square-stop":
                events[v.SQUARE] = [(t, x) for t, x in events[v.SQUARE] if t < 9_000_000]
            elif fault == "unknown":
                t, _ = events[v.UART][200]
                events[v.UART][200] = (t, None)
            elif fault == "direction":
                events[v.UART + "_direction"].append((2_000_000, 0))
            else:
                for name in events:
                    events[name] = [(t, x) for t, x in events[name] if t < 12_000_000]
            with self.subTest(fault=fault), self.assertRaises(ValueError):
                v.check_trace(events)

    def test_uart_corruption_cannot_be_hidden_in_long_passing_subsequence(self):
        events = synthetic_trace()
        for index in (200, 1500, len(events[v.UART]) - 3):
            broken = copy.deepcopy(events)
            del broken[v.UART][index]
            with self.subTest(index=index), self.assertRaises(ValueError):
                v.check_trace(broken)

    def test_parser_supports_pinned_cycle_and_picosecond_representations(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "trace.vcd"
            for quantum in (1, 1666):
                events = synthetic_trace()
                write_vcd(p, events, quantum)
                self.assertEqual(events, v.read_trace(p))
            p.write_text(p.read_text().replace("$timescale 1 ps", "$timescale 2 ns"))
            with self.assertRaises(ValueError):
                v.read_trace(p)


class VerifierTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.worker = self.root / "worker"
        (self.worker / "src").mkdir(parents=True)
        (self.worker / "src" / "main.xc").write_text("int main(void) { return 0; }")
        self.output = self.root / "trusted"

    def fake_process(self, command, cwd, env, timeout, log):
        if command[0] == "xcc":
            Path(command[-1]).write_text("fresh XE")
            log.write_text("Constraint check for tile[0]:\n  Cores available: 8, used: 2\n"
                           "  Memory available: 524288, used: 1600\nConstraints checks PASSED.\n")
            return {"exit_code": 0, "timed_out": False}
        self.assertEqual(str(v.TOTAL_CYCLES), command[command.index("--max-cycles") + 1])
        path = Path(command[command.index("--vcd-tracing") + 1].split(" -tile")[0][3:])
        write_vcd(path, synthetic_trace())
        log.write_text(f"xsim: error: C51188: maximum cycles reached ({v.TOTAL_CYCLES})\n")
        return {"exit_code": 1, "timed_out": False}

    def verify(self, effect=None):
        with patch.object(v, "validate_toolchain", return_value={"xcc": "xcc", "xsim": "xsim", "version": "test"}), \
                patch.object(v, "run_process", side_effect=effect or self.fake_process):
            return v.verify(self.worker, self.output, {}, timeout=10)

    def test_fresh_build_ignores_worker_scripts_binaries_and_forged_result(self):
        (self.worker / "Makefile").write_text("all:\n\tfalse\n")
        (self.worker / "application.xe").write_text("stale")
        (self.worker / "benchmark-verification.json").write_text('{"passed": true}')
        result = self.verify()
        self.assertTrue(result["passed"], result.get("reason"))
        self.assertEqual({"0": 2}, result["metrics"]["threads_per_tile"])
        self.assertEqual({"0": 1600}, result["metrics"]["ram_bytes_per_tile"])
        self.assertEqual(v.VERSION, result["protocol"]["id"])
        self.assertEqual(64, len(result["protocol"]["verifier_sha256"]))
        self.assertTrue(list(self.output.glob("verification-*/src/main.xc")))

    def test_simulator_failures_missing_output_and_stale_capture_are_rejected(self):
        for mode in ("clean-exit", "other-error", "extra-diagnostic", "timeout", "missing-waveform"):
            def run(command, cwd, env, timeout, log):
                result = self.fake_process(command, cwd, env, timeout, log)
                if command[0] == "xsim":
                    if mode == "clean-exit":
                        result["exit_code"] = 0
                    elif mode == "other-error":
                        log.write_text("xsim: error: invalid image")
                    elif mode == "extra-diagnostic":
                        log.write_text(log.read_text() + "Unexpected runtime error\n")
                    elif mode == "timeout":
                        result["timed_out"] = True
                    else:
                        (cwd / "outputs.vcd").unlink()
                return result
            with self.subTest(mode=mode):
                self.assertFalse(self.verify(run)["passed"])

    def test_build_failure_cannot_reuse_previous_success(self):
        self.assertTrue(self.verify()["passed"])
        def fail(command, cwd, env, timeout, log):
            log.write_text("compiler error")
            return {"exit_code": 1, "timed_out": False}
        self.assertFalse(self.verify(fail)["passed"])

    def test_source_symlinks_and_compiler_flag_injection_are_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            build = Path(td)
            (self.worker / "src" / "bad.xc").symlink_to(self.worker / "src" / "main.xc")
            with self.assertRaisesRegex(ValueError, "symlink"):
                v.submission(self.worker, build)
            (self.worker / "src" / "bad.xc").unlink()
            for data in [{"libraries": ["xcore -target=wrong"]}, {"flags": ["-target=wrong"]}]:
                (self.worker / "submission.json").write_text(json.dumps(data))
                with self.assertRaises(ValueError):
                    v.submission(self.worker, build)

    def test_implementation_review_and_primary_plan_exclude_framework_scoring(self):
        conf = b.load_config(Path(b.__file__).parent / "benchmark.toml")
        matrix = b.plan(conf, smoke=True)
        self.assertEqual(4, len(matrix))
        self.assertEqual({"native_xtc", "no_skills_xtc"}, {row[3] for row in matrix})
        task = matrix[0][0]
        guidelines = b.review_guidelines(task)
        self.assertIn("IMPLEMENTATION-ONLY", guidelines)
        self.assertNotIn("testing_quality", guidelines)
        with self.assertRaises(ValueError):
            b.review_guidelines({})
        self.assertIsNone(b.require_verifier(task))
        frozen = b.report_config_snapshot(conf, {task["id"]})
        self.assertEqual(v.VERSION, frozen["tasks"][0]["verifier"])
        self.assertEqual("implementation", frozen["tasks"][0]["evaluation_class"])

    def test_builtin_verification_does_not_read_candidate_results(self):
        task = b.load_config(Path(b.__file__).parent / "benchmark.toml")["tasks"][0]
        result = observations(False)
        result["reason"] = "fault found"
        with patch.object(v, "verify", return_value=result):
            result = b.verify_candidate(task, self.worker, {}, self.output, 10)
        self.assertEqual("failed", result["status"])
        self.assertEqual("fault found", result["reason"])


@unittest.skipUnless(os.environ.get("GLADIATORS_XTC_ENV"), "set GLADIATORS_XTC_ENV for real XTC integration tests")
class LiveXtcTests(unittest.TestCase):
    def test_reference_and_faulty_firmware_under_same_protocol(self):
        env = b.activated_xtc_environment(dict(os.environ), Path(os.environ["GLADIATORS_XTC_ENV"]))
        fixture = Path(__file__).parent / "fixtures" / "uart_reference.xc"
        source = fixture.read_text()
        for name, prefix, expected in [("reference", "", True),
                                       ("bad-byte", "#define SECOND_BYTE 0xA4\n", False),
                                       ("bad-square", "#define SQUARE_INCREMENT 49000\n", False)]:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                (root / "worker" / "src").mkdir(parents=True)
                (root / "worker" / "src" / "main.xc").write_text(prefix + source)
                result = v.verify(root / "worker", root / "trusted", env, 180)
                self.assertEqual(expected, result["passed"], result.get("reason"))


if __name__ == "__main__":
    unittest.main()
