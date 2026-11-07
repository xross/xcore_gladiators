# UART/heartbeat implementation verification

## Current protocol: implementation-v2

`verification/uart_heartbeat.py` is the harness-owned, standard-library-only
verifier. `benchmark.toml` selects it with:

```toml
evaluation_class = "implementation"
revision = "implementation-v2"
verifier = "uart-heartbeat-implementation-v2"
conditions = ["no_skills_xtc", "native_xtc"]
```

Both conditions receive preactivated XTC. No candidate-authored checker,
Makefile, executable, waveform or `benchmark-verification.json` determines
correctness. The harness copies source/header files into a fresh directory,
builds them with its fixed compiler command, runs xsim and checks the trace.
The worker receives an identical convenience copy as `benchmark_check.py`;
modifying that copy cannot change the final verifier.

The public contract is in `prompt-implementation-v2.md`. It fixes:

- XTC Tools **15.3.1**, target **XK-EVK-XU316**, system/reference clocks
  **600/100 MHz**; UART on tile-0 **1A**, square wave on tile-0 **1D**.
- **1 ms startup + 25 ms observation**, 15,600,000 system cycles in total.
- At least 280 complete alternating `55 A5` 8N1 frames at 115200 baud,
  continuous frames, all transitions after initial frame alignment, and the
  final partial frame. Frame timing tolerance is 0.5%; bit-interval tolerance
  is 1%, allowing valid implementations with different scheduling methods.
- Square half-period and accumulated phase within one reference tick (10 ns)
  of the 500 us schedule. Both outputs must continue through observation end.
- Known output levels and enabled output directions throughout observation.
- Only the exact expected `C51188` cycle-limit diagnostic with exit status 1
  is accepted. Clean early exit, other errors, host timeout and absent waveforms fail.

The parser explicitly handles XTC 15.3.1's truncated 1666 ps representation of
a nominal 600 MHz cycle. It does not infer reference timing from the candidate's
waveform. A different target/tool release needs a new reviewed protocol.

## Build interface and artifacts

All firmware source and headers live under `src/`. The harness compiles all
C/C++/XC/assembly sources there with `-O2 -g -Wall -report`, the fixed target,
and the source include directory. Optional `submission.json` accepts only
`{"libraries": ["installed_xtc_library_name"]}`. No arbitrary compiler flags,
candidate build scripts, prebuilt objects, symlinks or external source paths
are accepted as submission inputs. Vendored source dependencies can live under
`src/`; normal installed XTC headers/libraries remain accessible.

Final artifacts live under each run's `verification/verification-*/` directory:
copied sources, fresh XE, build/resource log, simulator log, VCD and result JSON.
The result records the protocol constants, verifier source SHA-256, toolchain
version, checked metrics, compiler-reported core/RAM allocation and stage times.
The trusted `observations.passed` boolean sets pass/fail. Worker time and verifier
time remain separate; neither raw test counts nor agent-selected simulation
lengths affect implementation ranking.

Local feedback (no model calls):

```sh
# Activate XTC in this child shell; WORKSPACE must contain src/.
bash -c 'source /Applications/XMOS_XTC_15.3.1/SetEnv.sh && python3 -m verification.uart_heartbeat --workspace WORKSPACE --output CHECK_RESULTS'
```

Unit tests cover valid fractional/rounded UART schedules, phase variation,
wrong baud/data, glitches, jitter, missing/stopped outputs, unknown levels,
truncated traces, simulator error handling and stale-result avoidance.
Real toolchain validation is opt-in:

```sh
GLADIATORS_XTC_ENV=/Applications/XMOS_XTC_15.3.1/SetEnv.sh \
  python3 -m unittest discover -s tests -p test_uniform_verifier.py -v
```

The reference/fault fixtures are for verifier validation only and are never
copied to worker projects. No model calls are made by these tests.

## Separate evaluation classes

Implementation review excludes candidate-authored tests and does not score
`testing_quality`. The later `test_framework` class is **not implemented yet**;
it should evaluate generated frameworks against known-good and faulty firmware
using fault detection, false failures, reproducibility and runner failure handling.
Do not combine those scores with implementation quality.

## Mandatory verification

Every selected task must name this supported verifier and the implementation
evaluation class. Missing or unsupported verification aborts before model calls.
There is no optional-verification mode, candidate shell-command verifier, or
fallback to worker logs. A missing/malformed receipt is an execution error,
never a pass. Reports and the index reject results without independent evidence.

The old pilot is not supported by the current reporting/scoring paths. Existing
files are not deleted or silently regraded. Host-process isolation is not an OS
sandbox: use containers/VMs to protect the verifier and installed tools against
intentionally hostile workers.
