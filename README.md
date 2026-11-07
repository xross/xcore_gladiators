# Gladiators

**Are you not entertained?** — *Gladiator* (2000 film)

*"Gladiators, you will go on my second whistle."* — John Anderson,
referee on the British TV show *Gladiators*.

**Gladiators** is an XMOS coding-agent benchmarking arena: multiple models face
identical engineering challenges in separate workspaces, their results are
verified independently, and a different high-quality model acts as referee.
Skills effectiveness is the **first experiment**, not a limit on future trials.
We can later compare prompts, model providers, MCP implementations, coding
agents, and toolchains using the same framework.

## Enter the arena

- **Gladiators:** independent implementing models, each receiving the same
  challenge and a clean starting project.
- **Arena:** an isolated workspace and OpenCode session for each trial.
- **First whistle:** the harness sets the challenge and prepares clean arenas.
- **Second whistle:** the gladiators begin their independent implementation runs;
  none can see another's implementation or previous attempts.
- **The Wall:** a trusted build/simulation/hardware verifier checks whether a
  solution actually works, independently of the model's claims.
- **Duel:** paired trials compare conditions (initially native skills versus
  no skills) on the same problem, model and repetition.
- **Hang Tough:** repeated, isolated trials test whether results are robust
  rather than a lucky single run.
- **The Eliminator / Travelator:** a solution reaches the finish only once it
  passes independent verification; the Travelator measures elapsed time to
  that verified finish, not just when the worker stops typing.
- **Referee:** after the runs and objective verification finish, an independent
  reviewing model receives anonymous solutions for blind design review.
- **Scoreboard:** an RST report pairs objective results, subjective assessments,
  token/time/cost measurements and model provenance.

The event names and quotes are presentation only, **not** additional instructions
in any worker prompt or substitutes for measured outcomes. The test problems
must remain neutral and identical across competing conditions. The Travelator
is the famous final obstacle in the TV show's Eliminator.

## First contest: XMOS skills

**Initial purpose:** Test whether providing reviewed XMOS development skills improves an
AI coding agent's ability to produce *correct, efficient and maintainable*
xcore software, and whether the benefit varies by model capability.

This is a **repeatable experiment harness**, not a report claiming that an
experiment has already run. It does **not** contain solved XMOS applications.

**Read first:** [Full research methodology](docs/EXPERIMENT_DESIGN.md),
[practical quickstart](docs/QUICKSTART.md),
[report contents and model provenance](docs/REPORTING.md), and
[implemented vs still missing](docs/STATUS.md). The methodology explains the
purpose, chosen problem, hypotheses, prompt specificity, success metrics,
reviewer independence, contamination controls, and how the identical tasks
will evaluate later MCP skill-delivery implementations. The generated
`REPORT.rst` intentionally contains only a concise methodology summary; full
replication details remain in `docs/EXPERIMENT_DESIGN.md` and
`docs/REPORTING.md`.

## Experimental design

The pilot uses OpenCode with GitHub Copilot as the model provider. There are
three independent implementers in `benchmark.toml` (GPT-6.1 Sol, Claude Opus
5.5, GPT-6 Luna), and a separate, high-capability reviewer (GPT-6 Astra).
**The reviewer is required to be a different exact model from every worker.**
These are editable defaults, not hard-coded in the runner. The separate
`[model_profiles."..."]` sections in the **same `benchmark.toml`** document
model developers, published capability tier (Powerful / Lightweight),
intended strengths, selection rationale and source/date. GitHub Copilot is the access/billing
provider for **all** models; OpenAI or Anthropic is the underlying developer.
Published tiers are not benchmark scores. Run `opencode models` to confirm
availability and exact IDs for your Copilot subscription.

Each task is repeated for every configured worker and condition:

| Condition | What the worker receives |
|---|---|
| `no_skills` | Unmodified starter; native skills disabled, XTC installed but not activated by Gladiators |
| `native` | Same starter plus pinned `xskills/skills`; XTC not preactivated, so the agent must discover setup guidance |
| `no_skills_xtc` | No skills, but XTC activated in OpenCode's inherited environment before the worker starts |
| `mcp` (later) | Same starter with an XMOS MCP server; native skill tool denied |

For one selected prompt, `repeats = 1` means **9 worker runs** and one blind
reviewer. With all three prompt variants enabled, that is **27 worker runs**
and three independent blind reviews. Use `--task uart-heartbeat-specific`
for the initial six-run pilot to control cost. Increase to **3-5 repeats** before drawing strong statistical
conclusions; AI responses vary from run to run. Keep prompts, toolchains,
validation, budgets, and references identical across conditions. Record XTC
version, OS, Git commit of xskills, model variants and OpenCode version.

**Workspace safety:** OpenCode versions can interpret an inherited `PWD`
rather than the subprocess working directory; Gladiators sets both explicitly
and creates temporary candidate workspaces outside the repository. Previous
versions could write into the shared starter project. Review and restore
`tasks/*/starter/` after upgrading from one of those versions. Run the full
unit suite before comparing results. This is not a substitute for container or
VM isolation when stronger separation is required.

**Do not have a master LLM delegate workers within a shared session.**
The Python runner launches each OpenCode worker as a fresh independent session
and workspace. Only once all candidate runs have finished does the reviewer
receive source snapshots named `candidate_01`, `candidate_02`, etc. It does not
see the model, skill condition, execution time or cost when scoring designs.
The final report maps anonymous evaluations back to models and conditions.

### Why test a preactivated XTC environment?

A recent unskilled agent generated source but could not compile it because
`xcc`, `xsim` and the CMake toolchain environment were absent from its shell.
It then stopped at the **first hurdle**, before its architecture and timing
choices could be assessed. For a benchmark of AI *engineering ability* this is
an important confounder: a failure to locate the toolchain is not necessarily
a failure to solve the specified software problem.

Gladiators therefore includes `no_skills_xtc` as a controlled third condition.
In the original `no_skills` and `native` conditions, XTC remains installed but
is **not** activated by the harness; the worker can discover and activate it
itself. In `no_skills_xtc`, the harness sources the specified XTC SetEnv script
**before** launching OpenCode, and the model receives no extra instructions
or native skills. This isolates the practical benefit of preconfigured tools
from the benefit of XMOS-specific instructions.

`--xtc-env SCRIPT` is required when `no_skills_xtc` is selected. Use
`SetEnv.sh` on macOS or `SetEnv` on Linux; do not use interactive
`SetEnv.command`. It verifies `xcc --version` and, for simulator tasks,
`xsim --version` **before any Copilot request**. Launch Gladiators from an
ordinary shell with XTC *not* already activated; otherwise the cold controls
would inherit it and the benchmark refuses to run. The trusted verifier,
when configured, receives the same XTC environment for every condition.
XTC 15.3.1's `xsim` returns exit status 1 even when printing its version;
the preflight accepts that status only with its recognized version banner.
Other nonzero exits remain fatal and include stdout/stderr in the error.

The three conditions do not form a complete factorial design: testing
`native` + preactivated XTC later would clarify whether skills still help
when initial environment friction is removed. Without an independent verifier,
we can compare time/cost and code decisions but **not claim correctness**.

### Live progress while a contest runs

The script prints the run count and percent completed, the task/model/skills
condition currently running, and regular activity updates during long OpenCode
sessions. Each heartbeat shows **worker or reviewer elapsed wall time**, how
many JSON events and tool calls have been observed, and the latest event type.
It does **not** print model responses, source code, or tool arguments. Independent
verification prints its current command number and elapsed time; after each
trial the terminal shows verification status and estimated Copilot AI Credits
where those figures are available. Output is flushed immediately, so the same
progress appears in a CI log or a redirected terminal log.

Change the interval in `benchmark.toml` using
`experiment.progress_interval_seconds` (default: 20 seconds), or override it
for one run with `--progress-every 5`. `--progress-every 0` disables periodic
heartbeats but retains the stage start/finish messages. These updates measure
**host wall time**, not simulated cycles or percent progress within the model.
The percentage reflects completed worker trials only; the blind review runs
afterwards. The OpenCode event stream is still recorded in full in the result
folder for detailed inspection.

### Why the initial problem?

The initial problem is two concurrent XU316 outputs: a UART-style serial
transmitter and a periodic heartbeat, independently observable in simulation.
We deliberately do **not** tell the worker to use C, XC, `lib_xcore`,
xcommon-cmake, specific timing primitives, hardware threads or Pyxsim. The
agent must discover an appropriate xcore implementation itself. The problem
was selected because:

- It exercises **XMOS-specific timing, timers, ports, logical cores and concurrency**.
- A solution can compile yet fail **concurrent real-time timing**, exposing
  whether skills help with architectural correctness rather than boilerplate.
- Independent edge timing and framing checks can be performed in a simulator
  **without requiring exclusive access to a physical board**.
- It should expose meaningful design differences: direct bit-banging vs
  port-clock use, core allocation, direct C/lib_xcore vs other dependencies,
  memory and test strategies.
- Relevant existing xskills include `xmos-c-programming`,
  `xmos-real-time-programming`, `xmos-testing` and `xmos-run-and-simulate`.

### Why multiple prompt variants?

Real customers vary in how much detail they provide. Three task entries share
the **same underlying problem and clean starting workspace**, but expose different
amounts of user detail:

| Variant / task ID | User supplies | Agent must decide |
|---|---|---|
| `uart-heartbeat-specific` | XU316, two outputs, 115200 8N1, repeating bytes, 1 kHz, no board | How to implement and verify it |
| `uart-heartbeat-everyday` | XU316, 115200 serial, 1 kHz heartbeat, no board | Framing, data pattern, implementation and test method |
| `uart-heartbeat-vague` | XU316, simultaneous serial + regular heartbeat, no board | Appropriate rates, framing, data, implementation and test method |

These are **three separate benchmarks**, not three prompts to be combined into
one agent session. Within each variant, all workers and skill conditions get
exactly the same wording; the blind reviewer compares candidates **within
that variant only**. Do not pool raw success rates or rankings across variants
without reporting the differences in specification detail. The worker's
benchmark housekeeping instructions (independent workspace, recorded evidence
and a DESIGN.md) remain identical across all variants; these are not part of
the user-facing problem description.

**Important evaluation rule:** The independent verifier and review rubric
must judge only requirements present in the chosen prompt, plus a common
minimum of *observable serial-like output, periodic heartbeat, simultaneous
operation, and reproducible verification*. In particular, do **not** fail the
vague prompt for choosing a reasonable rate other than 115200 baud or a
heartbeat other than 1 kHz. Instead, record chosen values and score the
justification, correctness against those values, and how explicitly the
agent explained its assumptions. Provide separate verifier profiles if needed.

**Important:** The UART example is a benchmark *candidate*, not a complete
xsim validation fixture. Exact port mapping and an independently verified
Pyxsim testbench must be added before claims of correctness can be made.
See `tasks/uart-heartbeat/VERIFIER.md`. A verifier is **optional** for exploratory runs, including `--smoke`. Without
one, Gladiators records `unverified` rather than claiming correctness. Use
`--require-verification` to reject experiments lacking a trusted verifier.

Later tasks can evaluate I2S, SPI, I3C, software-defined interfaces,
DSP/vectorisation, DFU, xsim testbench authoring, or hardware tests. Avoid
multiple tasks which all test the same skill unless that is intentional.

## Setup

Requires: Python 3.11+, a compatible OpenCode 2 CLI, an authenticated
GitHub Copilot provider in OpenCode, and XTC Tools 15.3.1 with xsim and any
project dependencies needed for each task. The benchmark script has no pip
dependencies. Tools must be properly licensed/available in the execution
shell. See the OpenCode docs for `opencode run`, `opencode session export` and
Copilot authentication.

1. Verify OpenCode can access GitHub Copilot: start `opencode`, use `/connect`
   to sign in to GitHub Copilot, then run `opencode models`. Edit `benchmark.toml`
   so all four model IDs exist, or substitute alternatives. The reviewer must
   not equal any worker.
2. Check out a **specific revision** of `xmos/xskills`. Pass its `skills/`
   directory to Gladiators using the **required** `--skills-dir PATH` flag
   whenever the selected matrix includes native skills (including `--smoke`).
   There is deliberately **no skills path in `benchmark.toml`**. The path is
   validated before any worker launches or results directory is created.
3. **Use an isolated OpenCode home/profile.** This prevents global skills,
   `AGENTS.md`, custom agents and plugins contaminating the control. Configure
   `experiment.clean_home` to a dedicated directory containing your Copilot
   credentials but no global skills or agent instructions. This requires
   configuring Copilot authentication *in that profile*. OpenCode V2 supports
   `opencode auth export` / `opencode auth import` if you need to migrate
   credentials. **Never copy auth files into the benchmark, commit them, or
   put them in the reports.** Verify `opencode auth list` in the selected
   profile. The harness changes `HOME`, `XDG_CONFIG_HOME` and `XDG_DATA_HOME`.
4. **Optional:** Configure `tasks[].verify_commands` to invoke an independent
   XTC build and Pyxsim testbench. Until the verifier is ready, leave those
   commands empty: all workers and the referee still run, but correctness
   remains `unverified`. Hidden validator details are never in worker prompts.
5. Check the plan and then run the benchmark:

```sh
python3 benchmark.py plan --task uart-heartbeat-specific --skills-dir /path/to/xskills/skills --xtc-env /Applications/XMOS_XTC_15.3.1/SetEnv.sh
python3 -m unittest discover -s tests -v
python3 benchmark.py run --task uart-heartbeat-specific --skills-dir /path/to/xskills/skills --xtc-env /Applications/XMOS_XTC_15.3.1/SetEnv.sh
```

To see more frequent progress updates during the first live pilot:

```sh
python3 benchmark.py run --task uart-heartbeat-specific --skills-dir /path/to/xskills/skills --xtc-env /Applications/XMOS_XTC_15.3.1/SetEnv.sh --progress-every 5
```

For a **short but complete pilot**, use `--smoke`. It tests one problem
with two models (`experiment.smoke_models`, default Luna and Sol), **three conditions**
(no skills, native skills, no skills + preactivated XTC; 6 worker runs), then runs the **separate blind
reviewer** (one additional Copilot model call). This lets you verify the full
collection-and-reporting pipeline without the full model/task matrix:

```sh
python3 benchmark.py plan --smoke --skills-dir /path/to/xskills/skills --xtc-env /Applications/XMOS_XTC_15.3.1/SetEnv.sh
python3 benchmark.py run --smoke --skills-dir /path/to/xskills/skills --xtc-env /Applications/XMOS_XTC_15.3.1/SetEnv.sh --progress-every 5
```

Smoke mode **does not require a verifier**. If none is configured, the six
implementations are marked `unverified` and the separate blind reviewer still
assesses the code. The report does not turn those judgements into verified
correctness or valid skills-effectiveness pass/fail conclusions. If a trusted
verifier is configured, it runs after each worker; smoke verification requires
its explicit pass/fail evidence file. To make verification mandatory, supply
`--require-verification`. A passing verifier is meaningful only if its checks
are meaningful; this repository does not yet ship a UART/Pyxsim timing
validator. Live GitHub Copilot calls, including the reviewer, may incur costs. To choose different smoke models, edit `smoke_models` in
`benchmark.toml`. Smoke mode always uses exactly two worker models, one repeat,
and the two `native`/`no_skills` conditions; it deliberately does not accept
`--model`, `--condition` or `--no-review`. For isolated one-off debugging,
use those flags on a normal `run` invocation instead.

If the skills directory is missing, unreadable or contains no valid
`SKILL.md`, Gladiators fails *before charging any Copilot calls*, rather than
running only the no-skills half. Worker errors are printed immediately and
saved in `result.json` plus `harness-error.log` or `stderr.log`. A partial
experiment is flagged prominently in `REPORT.rst`, and the blind review is
skipped when a worker is missing or fails; such runs cannot support a paired
skills conclusion. A run with worker errors exits nonzero while preserving
all logs and historical results.

To rerender an existing report without contacting any models:

```sh
python3 benchmark.py report --results bench-results/YYYYMMDD-HHMMSS-XXXXXX
```

Every run creates a **new timestamped directory** under `bench-results/`,
including worker logs and session exports, source snapshots, verification logs,
anonymous review inputs and `REPORT.rst` (reStructuredText, generated by
`rst_report.py`). A persistent SQLite index at
`bench-results/gladiators.sqlite3` records experiment and trial metadata
for longitudinal comparisons; previous runs are never overwritten. Use:

```sh
python3 benchmark.py history
python3 benchmark.py trend --task uart-heartbeat-specific
python3 benchmark.py compare --task uart-heartbeat-specific
python3 benchmark.py index   # rebuild the SQLite index from archived results
```

The full per-run JSON and source archives remain the source of truth; the
SQLite file is a rebuildable search/comparison index. **Treat all transcripts as sensitive**, especially for internal
code; they may contain tool outputs. Review before sharing.

## Isolation and experimental fairness

**Critical worker instruction:** Do not inspect code produced by other agents,
previous benchmark runs, existing solutions or benchmark verifier material.
Workers may inspect only their own starter project, installed toolchain,
normal published XMOS libraries and official public documentation. They must
not collaborate or spawn subagents. These instructions are embedded identically
in every prompt (apart from the skill condition).

The runner creates a **fresh temporary workspace per run**, no shared worktree,
no worker-to-worker conversation, and no source solutions in the starter.
It excludes `AGENTS.md` and `CLAUDE.md` from starter copies, denies
OpenCode `subagent` and `external_directory` tool permissions, and
prevents native skill calls in the no-skills/MCP conditions.

**However, a local process with shell access is NOT an OS sandbox.** OpenCode
itself warns that shell permission path inference is best-effort. A worker can
still access other user-readable files through shell commands. This package
only provides **best-effort behavioral isolation**, not a security boundary.
For a defensible controlled experiment, launch each run in a **separate
container or VM** with only one worktree mounted, plus its toolchain and
credential mechanism, and no access to previous candidate sources. This can
be added as an outer execution wrapper without changing experiment config.
For hardware tests, mount only the intended XTAG/board and serialize runs.

The v1 runner executes all workers **sequentially** for fair wall-time
comparisons and to avoid Copilot rate-limit/CPU interference. Simulation tasks
could be parallelized later using separate sandboxes. Hardware tasks must
always serialize access to the one board, ideally with a physical lock or
centralized booking mechanism. Don't interpret wall-time differences when
running jobs in parallel on a shared CPU.

### Time to correct solution

A true "time of first correct revision" requires independent verification
at every checkpoint, which can itself change worker behavior and consume
substantial simulator time. V1 therefore reports **time to post-run verified
solution = worker elapsed time + independent verifier time**, and `null` for
failed/unverified trials. This is not the earliest time the implementation
became correct; name it accurately. A future version can add timestamped
verification checkpoints for a closer measurement.

## Measurements

| Field | Provenance and interpretation |
|---|---|
| Success/failure | Exit status of hidden independent build/xsim/hardware verifier; absent verifier = unverified |
| Skill usage | Native `skill` or MCP `load_skill` tool calls from exported OpenCode transcript; not self-reported |
| Tokens | OpenCode session export aggregate where available; otherwise assistant messages or JSON event fallback, clearly labelled |
| Usage cost | Estimated from **GitHub Copilot default-context per-million-token pricing** in `benchmark.toml`, using input, cached input, cache write and output counters |
| Actual billed cost | **Unavailable** without GitHub billing reconciliation and allowance information; not confused with estimated cost |
| Wall time | OpenCode worker elapsed seconds; external verification time separate |
| Hardware threads / tiles | Independent verifier must measure or audit the actual linked application, rather than count C functions; agent's `DESIGN.md` is only self-reported |
| Libraries | Independent linker/CMake/verified artifact extraction preferred; otherwise self-reported |
| Memory | XTC compiler/linker reports per tile, when supplied by verifier; never invent if not measured |
| Major design choices | Worker `DESIGN.md`, anonymized source, and independent blind reviewer analysis |
| Quality | Separate model scores architecture, real-time behavior, maintainability, test quality, resource efficiency, XMOS practices, each 0–5 |

A reviewer assesses design quality but **does not overrule the objective
verifier**. Read any low-scoring/high-scoring claims before using the data in
planning. Report any attempted tool denial, task timeout, inconclusive test,
model swap, or incomplete session export.

The GitHub billing table may have higher rates for *extended context*.
This script uses default context-tier rates only; if any run uses an extended
context tier, update the estimator or reconcile with GitHub billing.
GitHub Copilot subscription allowances mean the *actual additional billed*
amount can be $0 even when the estimated AI Credits are positive.

## Adding a task

A task can be a new engineering problem or another wording of an existing
one. Set `family` to group related prompts and `specificity` to document how
much detail the user supplied. The runner compares model/skill conditions
within a task ID; do not treat different wordings as interchangeable.


Add a new item to `[[tasks]]` in `benchmark.toml`:

```toml
[[tasks]]
id = "spi-pyxsim"
family = "spi"
specificity = "everyday"
kind = "sim"
rationale = "Tests software-defined SPI timing and Pyxsim verification."
prompt = "tasks/spi-pyxsim/prompt.md"
starter = "tasks/spi-pyxsim/starter"
verify_commands = [
  "cmake -S . -B build -G Ninja",
  "cmake --build build",
  "python3 -m pytest -q tests/test_spi.py",
]
```

These commands are illustrative: use the **known-good commands of your
selected project**. Place independent hidden verification materials outside
the starter. The harness expects the commands to be runnable in the worker
worktree; adapt your test environment so the verifier imports its own test
fixtures without exposing their implementation to workers. The trusted
validator can optionally write `benchmark-verification.json` with
`threads_per_tile`, `ram_bytes_per_tile`, `libraries`, `evidence`, and test
counts. The runner deletes any pre-existing copy of this filename before
running the independent validator to prevent worker-forged measurements.

For `kind = "hardware"`, use the same definition, but the validator should
explicitly select the correct XTAG adapter and collect evidence. The pilot
runner is sequential; do not parallelize hardware cases using one board.

## Future MCP server testing

Once the native/no-skills baseline is established, enable the third condition:

```toml
[conditions.mcp]
type = "mcp"
url = "https://your-real-xmos-mcp-server.example/mcp"
server_name = "xcore-skills"
```

Keep the *same* task prompts, model IDs, worker tool access, verifier, sample
size, and xskills content revision. Record the deployed server commit,
`SKILL.md` release tag and skill discovery mechanism (`tools/list` with
`list_skills`/`load_skill`, server instructions, or SEP-2640). This allows:

- **No skills vs native skills:** Is XMOS guidance itself beneficial?
- **Native vs MCP-delivered skills:** Does remote discovery/delivery change
  actual usage, correctness, engineering quality, latency or cost?
- **Per-model differences:** Do weaker models get a greater benefit from skills?
- **Multi-task differences:** Are skills more valuable for timing-critical I/O
  than for routine build/project setup?

If testing SEP-2640, ensure the selected OpenCode version truly supports it;
otherwise its mere presence on the server does not constitute skill delivery.
Repeated runs and reporting an actual effective skill-load rate are much more
useful than a single demonstration prompt.

## References

- [OpenCode V2 CLI](https://dev.opencode.ai/v2/docs/cli/commands/)
- [OpenCode V2 native skills](https://dev.opencode.ai/v2/docs/skills/)
- [OpenCode V2 permissions and external directory limitations](https://dev.opencode.ai/v2/docs/permissions/)
- [OpenCode GitHub Copilot connection](https://dev.opencode.ai/v2/docs/providers/)
- [GitHub Copilot pricing](https://docs.github.com/en/copilot/reference/copilot-billing/models-and-pricing)
- [XMOS XTC Tools](https://www.xmos.com/documentation/XM-014363-PC/html/)
- [XMOS test_support](https://github.com/xmos/test_support)
