# Gladiators quickstart

The benchmark evaluates **xcore implementation**, not agent-authored test
frameworks. Every submission is freshly built and checked by the same mandatory
harness-owned simulator verifier.

## Prerequisites

- Python 3.11+; the runner/checker use only the standard library.
- OpenCode 2 configured for GitHub Copilot.
- XMOS XTC Tools **15.3.1**, including `xcc`, `xsim` and its activation script.
- A pinned `xskills/skills` directory.
- Prefer a dedicated OpenCode home without unrelated global skills/settings.

Check model availability with `opencode models`. Worker/reviewer model IDs,
prices, budgets and repeats are editable in `benchmark.toml`. Reviewer identity
must differ from every worker identity.

## Plan and run

```sh
python3 benchmark.py plan \
  --skills-dir ~/Desktop/xskills/skills \
  --xtc-env /Applications/XMOS_XTC_15.3.1/SetEnv.sh

python3 -m unittest discover -s tests -v

# Full configured matrix: Sol, Opus and Luna x two conditions = six workers.
python3 benchmark.py run \
  --skills-dir ~/Desktop/xskills/skills \
  --xtc-env /Applications/XMOS_XTC_15.3.1/SetEnv.sh
```

The two conditions are `no_skills_xtc` and `native_xtc`. Both inherit activated
XTC; only the latter receives native XMOS skills. `--smoke` reduces the matrix
to Luna and Sol (four workers), retaining the common verifier and blind reviewer.
Full mode uses all configured models and `experiment.repeats` (currently one).
Use at least three repeats for stronger conclusions after assessing cost.

**Run commands make paid model calls**, including a separate blind referee.
`plan`, unit tests, verifier integration tests and report regeneration do not.

Missing/unsupported verification is an unconditional preflight error. There
are no `--allow-unverified` or `--require-verification` switches: verification
cannot be opted out of. Workers can fail acceptance or encounter execution
errors, but cannot finish with an unchecked correctness outcome.

## Compare current and edited skills

Edit only the copied repository, for example `~/Desktop/xskills_xc`, to remove
the language-promotion guidance you want to test. Pass its **skills/** subfolder,
not the repository root. Both folders are copied into isolated worker workspaces;
the supplied originals are never edited by the runner.

```sh
# Read-only preflight: no model calls or new results archive.
python3 benchmark.py plan \
  --skills-dir ~/Desktop/xskills/skills \
  --compare-skills-dir ~/Desktop/xskills_xc/skills \
  --reuse-results bench-results/20261107-021802-e7dfa6 \
  --xtc-env /Applications/XMOS_XTC_15.3.1/SetEnv.sh

# After the skills differ and the plan is satisfactory:
python3 benchmark.py run \
  --skills-dir ~/Desktop/xskills/skills \
  --compare-skills-dir ~/Desktop/xskills_xc/skills \
  --reuse-results bench-results/20261107-021802-e7dfa6 \
  --xtc-env /Applications/XMOS_XTC_15.3.1/SetEnv.sh
```

The default comparison matrix has nine submissions: three models across
`no_skills_xtc`, `native_xtc` (current) and `native_compare_xtc` (comparison).
With the compatible six-trial archive above, the plan shows **six REUSE and
three RUN** entries. Only the three comparison workers and one fresh combined
reviewer incur new model usage. The existing failed submission remains failed;
reuse does not select only successful attempts or recycle old rankings.

Identical skill contents produce a planning warning and block `run`. Incompatible
matching results also block before model calls; omit `--reuse-results` to run
all nine workers afresh instead. Reuse requires review, so `--no-review` cannot
be combined with it. Old SKILL.md-only fingerprints generate a warning because
auxiliary resources cannot be retrospectively attested, even with matching git
revision. New archives fingerprint the complete relevant skill-resource tree.

Reuse copies evidence into a new archive with original experiment/run IDs and
hashes. Original times and token usage remain available, but are marked historical,
not newly incurred costs or fresh repetitions. Provider models may change behind
the same model ID; reuse is an exploratory comparison, not a concurrent rerun.

## Common acceptance test

See `tasks/uart-heartbeat/prompt-implementation-v2.md` for the public contract:
fixed target/pins/clocks, a source-only build interface, 1 ms startup allowance,
25 ms concurrent observation and identical UART/square-wave tolerances.
Workers get `benchmark_check.py` for feedback. Final grading uses the separate
harness copy and fresh artifacts, ignoring candidate tests/binaries/results.

The blind reviewer sees implementation source and independent verifier evidence,
not candidate-authored tests. Test-framework sophistication/count/length is not
part of implementation ranking. Framework-authoring skills will be a separate
future evaluation class, not a mixed score.

Real-XTC verifier validation without model calls:

```sh
GLADIATORS_XTC_ENV=/Applications/XMOS_XTC_15.3.1/SetEnv.sh \
  python3 -m unittest discover -s tests -p test_uniform_verifier.py -v
```

## Results and progress

The console reports the current worker, elapsed time and event activity.
Use `--progress-every 5` for more frequent updates. Results are in unique
`bench-results/<timestamp>-<suffix>/` directories:

- `REPORT.rst`: rank-ordered summary, independent outcomes, time and usage.
- `runs/*/verification/`: fresh sources/XE, resource log, simulator log, VCD,
  verifier receipt with protocol fingerprint and measurements.
- `runs/*/events.jsonl`, `stderr.log`, `source/`: worker audit trail.
- `reviews/`: anonymous implementation review and mapping.
- `experiment.json`: frozen configuration, protocol and skill/tool versions.

```sh
python3 benchmark.py report --results bench-results/<run-id>
python3 benchmark.py history
python3 benchmark.py trend --task uart-heartbeat-specific
python3 benchmark.py compare --task uart-heartbeat-specific
```

Only independently checked results and execution errors are supported. Old
unchecked pilot results are not migrated or regraded. Their files are not deleted.

## Limits

Host processes are not OS sandboxes. Use containers/VMs and restrict mounted
files for rigorous isolation. Reported credits/USD are estimates, not invoices.
Simulator pass is finite digital-model evidence, not physical board validation.
