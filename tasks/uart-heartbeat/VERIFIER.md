# Independent validation for uart-heartbeat

There are three user-facing prompt variants in this folder. Never present
the numerical acceptance criteria from the specific variant to workers
solving the everyday or vague variants. In particular, a vague-scenario agent
can make its own justifiable choices about baud, framing and heartbeat rate.
The verifier must assess the chosen values and observed timing, rather than
secretly imposing 115200 baud, 8N1 or 1 kHz on an underspecified request.

This is a **candidate task prompt**, not a finished timing verifier. Do not count it as
passing merely because an agent says it ran xsim or wrote a test. The human benchmark
maintainer needs to supply a *known-working* hidden verifier before running a
meaningful scored experiment. Suggested approach:

1. Choose one XU316 XN model and explicit port mapping, shared by all candidates.
2. Provide a separate Pyxsim/test_support pytest driver that samples TX and heartbeat.
3. For **specific**: check 8N1 framing and each bit duration at 115200 baud, including start/stop bits; for **everyday/vague**: check the declared, reasonable serial configuration.
4. For **specific/everyday**: check heartbeat transitions continue at 500 us intervals; for **vague**: check the declared, reasonable period,
   within a declared tolerance and for a declared simulated duration.
5. Reject premature termination, skipped frames, stuck outputs and hangs.
6. Build each candidate with XTC 15.3.1 in the same environment and capture compiler
   memory/resource reports. Audit logical-core counts; do NOT infer them just by
   counting source-level functions or `par` constructs.

Set `verify_commands` in benchmark.toml to invoke those deterministic checks.
The harness runs them *after* OpenCode returns and records exit status/output.

Optional `benchmark-verification.json` written by a trusted verifier:

```json
{
  "passed": true,
  "tests_passed": 5,
  "tests_total": 5,
  "metrics": {
    "threads_per_tile": {"0": 3, "1": 0},
    "ram_bytes_per_tile": {"0": 48128, "1": 0},
    "libraries": ["lib_xcore"],
    "evidence": ["compiler -report output", "Pyxsim UART waveform test"]
  }
}
```

These numbers are SAMPLE SCHEMA VALUES, not measurements. A verifier's JSON is
never produced by the worker, and its data overrides the worker's self-report.
For stronger integrity, prefer storing verifier output outside the worker workspace
and passing it to the harness using your own validator adapter.
