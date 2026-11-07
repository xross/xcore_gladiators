# Gladiators

*"Gladiators, you will go on my second whistle."* — John Anderson.

Gladiators compares independent coding agents on xcore implementation quality.
Workers run in separate OpenCode sessions through GitHub Copilot. Every
submission is checked by the same **mandatory harness-owned verifier**, then
a different model reviews the implementations anonymously.

## Current contest

Implement concurrent UART and square-wave outputs on XU316. The task publishes
a fixed target, pins, clocks, source-build interface and acceptance criteria.
The verifier performs a fresh build, allows 1 ms startup, and checks 25 ms of
simulated concurrent output. It validates UART data/framing/timing, square-wave
timing, continuity, output direction, and simulator/build failure handling.

This is **implementation-only**: candidates do not need to author test frameworks.
Their test count, framework sophistication and chosen simulation duration are
not scored. Workers receive the common checker for feedback; final grading uses
a separate trusted copy and fresh artifacts outside the worker workspace.

| Condition | Worker environment |
|---|---|
| `no_skills_xtc` | XTC preactivated; native skill loading denied |
| `native_xtc` | Identical preactivated XTC plus the supplied XMOS skills |
| `native_compare_xtc` (optional) | Same XTC plus a second supplied skills folder |

Toolchain setup is controlled, not a separate quality score. Language,
architecture, libraries and timing implementation remain engineering choices
within the published source/target contract.

## Run

Requires Python 3.11+, OpenCode 2 with GitHub Copilot authentication, XTC Tools
**15.3.1**, and a pinned `xskills/skills` checkout. Python runner/verifier use
only the standard library. Check model IDs with `opencode models`.

```sh
python3 benchmark.py plan \
  --skills-dir ~/Desktop/xskills/skills \
  --xtc-env /Applications/XMOS_XTC_15.3.1/SetEnv.sh

# All three configured workers x both conditions: six worker trials + referee.
python3 benchmark.py run \
  --skills-dir ~/Desktop/xskills/skills \
  --xtc-env /Applications/XMOS_XTC_15.3.1/SetEnv.sh
```

Models, rates, budgets, repetitions and tasks are in `benchmark.toml`. Defaults
are Sol, Opus and Luna with Astra as the separate reviewer, one repeat each.
`--smoke` runs only Luna and Sol (four trials); it still uses mandatory
verification and the independent reviewer. Run commands incur model usage costs.

There is **no unchecked-results mode**. Missing/unsupported verification fails
before model calls; completed submissions receive independent pass/fail results.
Worker/tool failures are execution errors, never passes. Old unchecked pilot
results are not supported by current scoring/reporting. Files are not deleted.

### Compare two skills folders, reusing verified baselines

After editing the copied skills to remove C-promoting guidance:

```sh
python3 benchmark.py plan \
  --skills-dir ~/Desktop/xskills/skills \
  --compare-skills-dir ~/Desktop/xskills_xc/skills \
  --reuse-results bench-results/20261107-021802-e7dfa6 \
  --xtc-env /Applications/XMOS_XTC_15.3.1/SetEnv.sh
```

Replace `plan` with `run` when ready. With compatible inputs this reuses the six
previous current-skills/no-skills trials, runs only three comparison-skills
workers, and obtains a **new blind review of all nine submissions**. Neither
the original archive nor either supplied skills folder is modified. Identical
skills folders are allowed for planning but rejected for a paid run.

Reuse checks prompts, models, budgets, verifier, environment, skills and archived
source/evidence before any model calls. Old archives that fingerprinted only
`SKILL.md` and git revision carry an explicit auxiliary-resource attestation
warning. Reports distinguish historical from newly incurred usage; reused
observations are not counted again as independent trials in trends/comparisons.

## Results

Each experiment gets a unique directory under `bench-results/`, containing:

- `REPORT.rst`: rank-ordered results, independent outcomes, time/estimated usage,
  readable blind rankings and an evidence appendix.
- `experiment.json`: frozen settings, prompts, run order, protocol fingerprint,
  toolchain and skill provenance.
- `runs/`: transcripts, source snapshots and fresh independent verification
  artifacts (build/resource logs, XE, simulator log, VCD and result receipt).
- `reviews/`: blinded implementation review and the identity mapping.

```sh
python3 benchmark.py history
python3 benchmark.py trend --task uart-heartbeat-specific
python3 benchmark.py compare --task uart-heartbeat-specific
python3 benchmark.py report --results bench-results/<run-id>
```

Report regeneration uses recorded independent evidence and frozen settings;
it does not call models, rerun verification or reconstruct passes from worker
claims. Actual billing can differ from estimated credits/USD.

## Local tests (no paid calls)

```sh
python3 -m unittest discover -s tests -v

GLADIATORS_XTC_ENV=/Applications/XMOS_XTC_15.3.1/SetEnv.sh \
  python3 -m unittest discover -s tests -p test_uniform_verifier.py -v
```

The opt-in XTC suite compiles a reference and deliberately faulty applications.
These fixtures are for checker validation only and are never given to workers.

## Validity and next steps

- Use three or more repeated matched trials before drawing strong conclusions.
- Blind design preference, independent correctness and time/cost are separate outcomes.
- Host-process workspaces are **not OS sandboxes**. Use containers/VMs and a clean
  OpenCode profile for stronger isolation and protection of trusted tools.
- A finite simulator pass is not physical timing, wiring or flash-boot validation.
- Later simulation-test-framework skills will be evaluated in a separate class
  against known-good/faulty applications, not folded into implementation ranking.

See [quickstart](docs/QUICKSTART.md), [experiment design](docs/EXPERIMENT_DESIGN.md),
[reporting](docs/REPORTING.md), [status](docs/STATUS.md) and the
[verifier contract](tasks/uart-heartbeat/VERIFIER.md).
