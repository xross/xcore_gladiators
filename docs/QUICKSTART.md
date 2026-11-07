# Gladiators: quickstart

**First whistle:** set the challenge and prepare clean arenas.
**Second whistle:** the gladiators begin their independent OpenCode sessions.
The referee reviews anonymous finished solutions; trusted verification runs first if configured.
John Anderson's second-whistle line is from the British TV show *Gladiators*;
"Are you not entertained?" is from the film *Gladiator*. Neither quote belongs
in worker prompts.

This repository is a benchmark harness, not a supplied solution to the UART
problem. Independent verification is **optional** for initial exploration;
without it, results are marked `unverified` even though the blind review runs.

## Prerequisites

- Python 3.11 or later (stdlib only for the runner).
- OpenCode **2** CLI configured to access the **GitHub Copilot** provider.
- XMOS XTC Tools and xsim in the environment, plus any per-task Python test
  dependencies (in a virtual environment) once the real verifier is provided.
- An accessible, pinned checkout of `xmos/xskills/skills` for native runs.
- Ideally, a clean OpenCode home/profile without unrelated global skills,
  custom agents, plugins or `AGENTS.md` instructions.

## Five commands / edits

1. Sign in to GitHub Copilot through OpenCode, then verify the exact models:

   ```sh
   opencode models
   ```

2. Edit `benchmark.toml`. Confirm `[models.workers]` and
   `[models].reviewer`; the reviewer must have a different model ID.
   Check out a pinned `xskills` revision and pass its `skills/` directory
   on every native-skills plan/run command with `--skills-dir /path/to/xskills/skills --xtc-env /Applications/XMOS_XTC_15.3.1/SetEnv.sh`.
   Do **not** put the path in TOML. Invalid paths abort before model calls.
   Set `experiment.clean_home` when you have a dedicated isolated OpenCode
   profile. Verify its Copilot login separately. Do not copy credentials into
   this repository or into results.

3. Inspect the three user-facing requests under `tasks/uart-heartbeat/`.
   They describe the *same problem* at specific, everyday and vague levels.
   The worker does not receive implementation hints (language, libraries,
   CMake, thread allocation or test framework).

4. **Optional:** Once a known-good verifier exists, set the task
   `verify_commands` in `benchmark.toml`. Until then, leave them empty.
   See `tasks/uart-heartbeat/VERIFIER.md`.

5. Plan and test the harness without spending Copilot credits:

   ```sh
   python3 benchmark.py plan --task uart-heartbeat-specific --skills-dir /path/to/xskills/skills --xtc-env /Applications/XMOS_XTC_15.3.1/SetEnv.sh
   python3 -m unittest discover -s tests -v
   ```

   Then run just the six specific-prompt trials:

   ```sh
   python3 benchmark.py run --task uart-heartbeat-specific --skills-dir /path/to/xskills/skills --xtc-env /Applications/XMOS_XTC_15.3.1/SetEnv.sh
   ```

   The runner prints `[current/total | % complete]` when each worker starts
   and finishes. While it is running, a line is printed every 20 seconds with
   elapsed time, OpenCode event count, tool-call count, and latest event type.
   Separate stage updates cover the independent verifier and blind reviewer.
   For more frequent console updates, use `--progress-every 5`, or set
   `experiment.progress_interval_seconds = 5` in `benchmark.toml`.
   `--progress-every 0` turns off only the periodic updates. Full worker
   output stays in `events.jsonl` and `stderr.log`, not in the progress lines.

   For a smaller end-to-end trial **including a separate blind reviewer**:

   ```sh
   python3 benchmark.py plan --smoke --skills-dir /path/to/xskills/skills --xtc-env /Applications/XMOS_XTC_15.3.1/SetEnv.sh
   python3 benchmark.py run --smoke --skills-dir /path/to/xskills/skills --xtc-env /Applications/XMOS_XTC_15.3.1/SetEnv.sh --progress-every 5
   ```

   Smoke mode picks one prompt, two worker models (`experiment.smoke_models`
   in `benchmark.toml`, default `luna` and `sol`), and runs each model under all three conditions (no skills, native skills,
   and no skills with XTC preactivated): six worker sessions. The independent
   high-quality referee then reviews all six anonymised solutions. Model/provider calls
   **can incur real GitHub Copilot costs**. With no verifier, all six
   correctness outcomes are **unverified**, but the blind review still runs.
   When configured, the verifier checks each candidate. Use
   `--require-verification` to fail before paid calls when no verifier exists.
   `--smoke` requires both skill conditions and
   is not compatible with `--model`, `--condition`, or `--no-review`.
   Failed workers show their errors in the console and report. The reviewer
   is skipped when a worker fails; such a comparison is marked incomplete.

## What the output means

Results appear under unique `bench-results/<timestamp>-<suffix>/` directories.
A SQLite database at `bench-results/gladiators.sqlite3` indexes every run;
it does not overwrite prior experiments. Use `python3 benchmark.py history`
to list runs, `python3 benchmark.py trend --task uart-heartbeat-specific`
for trends and `python3 benchmark.py compare --task uart-heartbeat-specific`
for paired comparisons. `python3 benchmark.py index` rebuilds the index from
preserved experiment JSON, reports and logs. `REPORT.rst` summarises each
model/condition, measured build/simulation success, worker wall time,
post-run verified time, token usage, skill calls, estimated AI Credits and USD,
and blind reviewer feedback. The underlying JSON events, exports, verifier
logs and snapshots are retained for investigation.

**`unverified` is not success.** Resource measurements appear only if the
trusted verifier supplies them. The report won't manufacture thread counts,
linked-library lists, memory figures, or evidence of correctness.

## Run all wording variants / add more problems

```sh
python3 benchmark.py plan --skills-dir /path/to/xskills/skills --xtc-env /Applications/XMOS_XTC_15.3.1/SetEnv.sh
python3 benchmark.py run --skills-dir /path/to/xskills/skills --xtc-env /Applications/XMOS_XTC_15.3.1/SetEnv.sh
```

The three prompt variants are separate test IDs, so the full initial matrix
has 18 implementer runs at `repeats=1`. Add new problems as additional
`[[tasks]]` entries; nothing in the runner is hardcoded to the UART problem.

Increase `experiment.repeats` to at least 3-5 after verifying the cost and
runtime on one small pilot. The current runner deliberately serialises all
workers; this also supports hardware tasks with one attached board.

## Later: Test an MCP skills server

Add this condition to `benchmark.toml`:

```toml
[conditions.mcp]
type = "mcp"
server_name = "xcore-skills"
url = "http://127.0.0.1:8000/mcp"
```

It can point to a local Streamable HTTP server or an HTTPS production service.
OpenCode receives it through `mcp.servers`, while its native `skill` tool is
denied for that condition. Keep the task prompts, skills source commit,
verifier and models the same for a fair comparison. You can swap MCP server
implementations by changing only the URL, then archive each server version.

This is a **comparison of skill delivery modes**, not a test of an MCP remote
compiler or simulator. Those operations could be benchmarked separately.

## Important safety/validity limitations

- Directory isolation plus instructions **does not prevent filesystem reads
  outside the workspace** by a model with shell access. Use separate containers
  or VMs and avoid mounting prior runs for a rigorous study.
- A verifier called after the worker finishes is not the earliest moment at
  which its solution became correct. The reported metric says *post-run
  verified time*, rather than *time of first correct revision*.
- Actual Copilot charges depend on the account's allowance, billing and
  context tier. Usage-based costs in the report are **estimates**.
- Any model not actually available through your Copilot provider should be
  replaced in `benchmark.toml` before launching paid trials.
- This harness was exercised with a **fake local OpenCode fixture** and unit
  tests, not a live Copilot account or XTC tools.

## Final report and methodology

Each run writes a `REPORT.rst` with a concise study summary, task selection rationale,
OpenCode-to-**GitHub Copilot** model access route, full worker/reviewer model
catalogue, published capability tiers, model descriptions, blind
review outcomes, independent verifier results, tokens, time and Copilot cost
estimates. Full reproducibility details live in `EXPERIMENT_DESIGN.md` and
`REPORTING.md`, not in every management-facing report.

Rebuild a report from archived results:

```sh
python3 benchmark.py report --results bench-results/<run-id>
```

Use [REPORTING.md](REPORTING.md) to interpret the model profiles and the
frozen reporting configuration recorded in `experiment.json`.

## XTC environment control

Run from a shell where XTC is not already activated. `--xtc-env` must point
to the *sourcable* activation script (macOS `SetEnv.sh`, Linux `SetEnv`).
Only `no_skills_xtc` workers inherit the activated environment. The other
workers can activate XTC themselves using their own knowledge or skills.
This avoids conflating an environment setup failure with an inability to
implement a correct design. See `docs/EXPERIMENT_DESIGN.md` for the rationale.

The warm trial preflight checks `xcc` (and `xsim` for simulation tasks) before
starting paid Copilot calls. A trusted verifier, if configured, has the same
XTC environment regardless of worker condition.
