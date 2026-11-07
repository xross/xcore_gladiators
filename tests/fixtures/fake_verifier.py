"""TEST FIXTURE ONLY: synthetic verifier receipts, never actual measurements."""
from verification.uart_heartbeat import protocol


def observations(passed=True):
    return {"passed": passed, "reason": None if passed else "Synthetic waveform fault",
            "protocol": protocol(), "elapsed_seconds": 0.01,
            "metrics": {"threads_per_tile": {"0": 2}, "ram_bytes_per_tile": {"0": 1600},
                        "libraries": ["lib_xcore"], "observation_ms": 25, "startup_allowance_ms": 1},
            "steps": [{"stage": "build", "exit_code": 0, "timed_out": False},
                      {"stage": "simulation", "exit_code": 1, "timed_out": False}]}


def validation(passed=True):
    return {"status": "passed" if passed else "failed", "elapsed_seconds": 0.01,
            "observations": observations(passed)}
