# Two outputs on an XU316 — implementation-v2

Implement two outputs running concurrently:

- Tile 0, `XS1_PORT_1A` / X0D00: repeating `0x55, 0xA5` bytes, 115200 baud,
  8N1, LSB first, idle high, one stop bit and no extra inter-frame gaps.
- Tile 0, `XS1_PORT_1D` / X0D11: a 1 kHz, 50% square wave. Serial transmission
  must not disrupt its timing.

The fixed simulator target is `XK-EVK-XU316` under XTC Tools 15.3.1:
600 MHz system clock, 100 MHz reference clock. Do not change that target or its
clocks. Physical board testing is not requested. Choose the implementation
language, architecture and timing techniques yourself.

## Submission and common acceptance test

This task evaluates the **xcore implementation**, not test-framework authoring.
Place all application source and headers under `src/`. The harness compiles all
`.c`, `.xc`, `.cpp`, `.cc`, `.cxx` and `.S` files there, using `xcc`, the fixed
target, `-O2 -g -Wall -report`, and `src/` as an include directory. Include
dependencies as source under `src/`, or optionally list installed XTC link
libraries in `submission.json`, e.g. `{"libraries": ["xcore"]}`. Do not supply
prebuilt objects or rely on your own build scripts for acceptance.

Every submission receives the identical test:

- Up to 1 ms of startup, then 25 ms of concurrent steady-state observation.
- At least 280 complete alternating UART frames. Frame intervals must be within
  0.5% of nominal; transition intervals, divided by their expected bit count,
  must be within 1% of nominal bit time. Bit levels, framing, unexpected edges,
  continuous transmission and the final partial frame are checked. Alignment
  to the first complete frame may use up to one frame at observation start.
- Every observed square-wave half-period and accumulated phase must be within
  one reference tick (10 ns) of the 500 us schedule, with output continuing to
  the end of observation.
- Both pins must remain enabled as outputs with known levels during observation.
- Build failures, premature simulator exit, timeouts, missing/stale waveforms
  and unexpected simulator diagnostics fail acceptance.

The harness supplies `benchmark_check.py` for local feedback. With XTC activated:

```sh
python3 benchmark_check.py
```

Final grading uses a separate harness-owned checker, a fresh source build and
fresh waveform artifacts. Do not modify the supplied checker. You need not
author tests or a simulation framework: extra tests, their count and their
duration do not contribute to the implementation ranking.

Write `DESIGN.md` explaining timing, concurrency, resource ownership and tradeoffs.
Record actual local check results and mark unmeasured physical properties unknown.
