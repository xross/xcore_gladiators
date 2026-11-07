# Gladiators: experiment design and methodology

**"Are you not entertained?"** (from the film *Gladiator*). The competition
is fun; the scoring must be reproducible. Gladiators is a general AI
coding-agent evaluation framework. The first contest studies XMOS development
skills, but the tasks and model matrix can also test MCP skill delivery,
agent/tool choices and other workflows.

**"Gladiators, you will go on my second whistle."** That's referee John
Anderson's catchphrase from the British TV show *Gladiators*, rather than the
film. We borrow it to describe the benchmark's stages: the *first whistle*
sets the challenge and prepares independent arenas; the *second whistle*
starts the competing implementer trials. After all workers finish and the
trusted verifier runs, an independent reviewing model judges the anonymous
implementations. There is **no shared model context or source code between
contenders**. These phrases appear only in project documentation and the
report, never in the prompts sent to workers.

## Who's who in the arena

| Theme | Experimental meaning |
|---|---|
| Gladiator | Worker/implementer model |
| Arena | Fresh isolated project and OpenCode session |
| Challenge | User prompt and accompanying neutral starting project |
| First whistle | Set challenge and prepare independent arenas |
| Second whistle | Start independent implementation trials |
| The Wall | Independent build, xsim or hardware verification gate |
| Duel | Compare the same model, task and repeat with different conditions |
| Hang Tough | Multiple fresh repetitions to assess consistency, not a single lucky run |
| The Eliminator | Overall evidence-based comparison, not subjective votes alone |
| Travelator | Final obstacle of the Eliminator; time to independently verified success, including post-run verification |
| Referee | Strong model, different from every implementing model; reviews after verification |
| Scoreboard | Independent verification, review, timings, resource and cost results |

The Gladiators event names are labels for reporting and explaining the
methodology; they do not appear in the worker's task prompts. The Travelator
is a recognisable element of the British show's *Eliminator*, not a separate
engineering problem. Only a verified solution can have a recorded finish time;
that metric is not a substitute for correctness, resource use or code quality.

**Status:** Experimental design and a tested *orchestration harness*; not yet a
validated xcore benchmarking result. No real OpenCode/Copilot/XTC trial has been
run in this package. The UART/heartbeat task still requires a trusted xsim
verifier before scientific conclusions are possible.

## Reporting policy and replication record

`REPORT.rst` is intended for management. It contains a short methodology
summary, model profiles and all results and caveats, **not** a copy of this
full experimental procedure. This document is the canonical methodology.
`docs/REPORTING.md` specifies reporting, provenance and cost-accounting
requirements; the actual frozen run parameters are in `experiment.json`.
The README explains how to launch the harness, not how a particular trial was
run. Changes to this protocol must also be reflected in the implementation
and test suite.

### Reproducible trial procedure

1. Choose the task IDs and exact prompt files; record why each task was chosen.
   Specific, everyday and vague prompts form separate task IDs, even when they
   test one problem family. Grade each against its own explicit requirements.
2. Freeze the skills catalogue revision, OpenCode/XTC versions, worker and
   reviewer model IDs, Copilot rate table, source/date of published model
   profiles and the TOML configuration digest before a paid run.
3. Use Python to create a trial matrix of tasks x models x conditions x repeats.
   In this initial version, the order is deterministic (task, model, condition,
   repeat), not randomised. Hardware access is serial; sequential worker runs
   also reduce contention for wall-time comparisons.
4. Create an identical clean starter per task in a fresh project directory and
   independent OpenCode session for every trial. Disable native skills in the
   no-skills condition; copy pinned XMOS skills into the native condition. The
   future MCP condition uses the same problem and denies native skill loading.
5. Launch the configured model through the OpenCode GitHub Copilot adapter.
   Ensure a worker does not see sibling workspaces, prior solutions, hidden
   verification or the eventual blind-review identities. A common OS account
   with multiple directories is not a security isolation boundary; use
   separate containers/VMs with restrictive mounts for rigorous studies.
6. Capture the worker source, tool-call traces, OpenCode session exports,
   requested/observed model ID, elapsed runtime, skill-loading calls and
   input/output/cache/reasoning token statistics when available. Distinguish
   events that actually happened from a worker's self-report.
7. Run independent, known-good build, xsim or hardware verifiers after worker
   completion. Only passing trusted checks produce verified correctness and a
   time-to-verified-finish (worker runtime plus post-run verifier time). Extract
   memory per tile, libraries and hardware threads only from trusted evidence;
   missing metrics are unknown, not zero. With no verifier the status is
   `unverified` and cannot be treated as passed.
8. Only after verification, provide anonymised candidate source and checks to
   a different, high-capability reviewer model. Blind scoring must not include
   worker model/condition labels, elapsed time or model pricing. The reviewer's
   ratings are qualitative and do not override trusted verification.
9. Reconcile each session's token source and estimate GitHub Copilot AI Credits
   and USD using the *frozen* per-model rate assumptions. These estimates do
   not imply actual billed charges; rate tiers, account allowances and missing
   usage events require separate billing reconciliation. Reviewer costs are
   included in total experiment cost.
10. Publish `REPORT.rst` with the executive outcome, at-a-glance methodology,
    selected tasks, short model profiles, verified results, blind design review,
    costs and limitations. Keep `experiment.json`, logs, prompts and verifier
    evidence with the run. Increase repeats, control run order and verify the
    environment before drawing strong conclusions.

## The question

When an AI coding agent attempts a realistic xcore software task, does having
access to `xskills` improve its ability to deliver a correct, well-designed,
resource-efficient solution? Does that benefit depend on model quality, how
specific the user's prompt is, or how the skills are delivered?

This is **not** a demonstration designed to make skills look good. We want a
repeatable way to discover both useful and useless skills, quantify trade-offs,
and decide whether an MCP skill delivery system is worth deploying.

## End-to-end architecture

```text
benchmark.toml (task prompts + models + conditions + pricing)
    |
    v
Python benchmark.py orchestrator
    |
    +-- fresh workspace A -> OpenCode / worker model -> candidate A
    +-- fresh workspace B -> OpenCode / worker model -> candidate B
    +-- fresh workspace C -> OpenCode / worker model -> candidate C
    |        (no shared context, no other candidate code in prompt)
    |
    +-- trusted verifier -> build / xsim / measured resource data
    |
    +-- anonymize sources + verifier outcomes
    |
    v
Different high-quality reviewing model (blind to worker identity)
    |
    v
REPORT.rst + raw JSON + logs + per-run snapshots
```

The harness, rather than the LLM reviewer, controls trial allocation, model
selection, timing, isolation instructions, hidden verification, recordkeeping
and the final identity mapping. A reviewing model is used only for the aspects
of software quality that cannot be completely assessed by deterministic tests.

## Hypotheses

- H1: Compared with no skills, native `xskills` increase the rate of
  independently verified correct solutions.
- H2: Skills improve XMOS-specific design choices (threads, timers, ports,
  supported libraries) even when the final program compiles in both groups.
- H3: Smaller/weaker models may benefit more from specialist skills; this is a
  hypothesis, not an assumption.
- H4: Any gain may come at a cost in tokens and elapsed time. We should measure
  whether skills are worth that cost.
- H5 (later): MCP-delivered skills can approach native skills in correctness and
  discovery without requiring local skill installation.

## Why we do not want the agent failing at the first hurdle

In an early no-skills run, a model wrote source code but could not build it:
`xcc` and `xsim` were not available, and CMake could not locate the XMOS
build-system files because XTC had not been activated. That is useful data
about onboarding and environment discovery, but it stops the agent before
we can learn much about the **design quality** of its intended solution.

We therefore measure two different questions rather than conflating them:

1. **Unassisted setup:** With skills absent and XTC not activated, can the
   agent locate, activate and correctly use an installed toolchain? Do native
   XMOS skills help it get past this initial obstacle?
2. **Engineering with tools ready:** With skills still absent but XTC already
   activated, can the agent build, test and improve a correct implementation?
   Does the original no-skills failure disappear simply through environment
   preparation, or do design limitations persist?

This **does not** mean hiding installation issues. Environment setup is a
real part of customer experience, so cold trials remain important. But
preactivation gives us a baseline that does not punish a model for an
avoidable shell setup problem, making the design comparisons more revealing.

The cold conditions and the XTC-preactivated condition have the **same task,
model, starter project, runtime limit and access to public documentation**.
The third condition receives *no* skills or supplementary guidance; only
its exported environment changes. The harness validates that the parent
shell is cold and checks the warm toolchain before charging for model calls.
The independent verifier, when enabled, is supplied an equal toolchain
environment for every condition. Keep this distinction in the report.

The current three conditions are a useful first experiment, not a complete
2x2 factorial design. Future trials could add `native + XTC` to separate
skill effects from any interaction with preactivation.

## Experiment factors

| Factor | Initial settings | Config location |
|---|---|---|
| Implementing model | Sol / Opus / small cheap model | `[models.workers]` in `benchmark.toml` |
| Judge model | Different, high-capability model | `[models].reviewer` |
| Skill condition | `no_skills`, `native`, `no_skills_xtc` | `[conditions.*]` + `--xtc-env` |
| Later skill condition | `mcp` | `[conditions.mcp]` (enable when ready) |
| Problem wording | specific / everyday / vague | Three task entries + prompt files |
| Trial repetitions | 1 exploratory; 3-5+ for patterns | `[experiment].repeats` |
| Environment | XU316, pinned XTC, same starter | Task definitions and local machine |

Treat a *task ID* as an atomic, comparable prompt. A family can contain multiple
wordings, but comparisons must be **paired inside one task ID**, not pooled
between a highly specific prompt and a vague one. Every trial starts in a fresh
workspace and OpenCode session.

## Small end-to-end smoke experiment and historical archive

`python3 benchmark.py run --smoke` is a **shortened complete experimental
pipeline**, not a single-model connectivity check. By default it chooses the
specified UART/heartbeat task and runs `luna` and `sol` from
`experiment.smoke_models`, each in `no_skills`, `native` and `no_skills_xtc` conditions,
with one repetition. It then invokes the independently configured reviewer
on the six blinded code candidates and writes `REPORT.rst` and all raw data.
Smoke mode can run **without a verifier**: every candidate is labelled
`unverified`, but the separate blind reviewer still scores code quality.
No correctness pass/fail or verified skills benefit can be concluded from those
runs. When a trusted verifier is configured, it runs after every smoke worker
and must provide an explicit boolean pass/fail evidence file. Use
`--require-verification` to block an unverified trial before paid model calls.
This deliberately tests the skills comparison *and* the reviewer workflow.
The reviewer scores design, not independently measured correctness. A
single smoke trial is exploratory and does not establish that skills improve
correctness or performance. For defensible studies, repeat the full matrix
several times with a validated verifier.
This mode makes **seven separate Copilot agent invocations** (six workers plus
one reviewer) when all are eligible; model pricing and requests must be checked
before running.

Each invocation creates a new unique experiment directory preserving the exact
model IDs, skill mode, task prompt checksum, chosen condition, reviewer result,
token/cost estimates, OpenCode session data and independent verifier evidence.
The durable SQLite index under `bench-results/gladiators.sqlite3` tracks all
experiments for `history`, `trend`, and `compare`; it can be regenerated from
archived JSON with `benchmark.py index`. Longitudinal comparisons should
stratify by model ID, task/prompt revision, skill mode, toolchain, verifier,
and skills revision to avoid attributing environment drift to model changes.

## Model provenance and selection

All configured models (three implementers and one separate reviewer by
 default) are accessed **through GitHub Copilot from OpenCode**, not through
model-developer APIs. This matters for reproducibility, model availability,
latency, token/cache accounting and billing. GitHub Copilot is the access
provider; OpenAI and Anthropic are the underlying model developers.

The benchmark must distinguish three types of model information:

- **Publisher-described class/intended strengths:** GitHub's `Powerful` or
  `Lightweight` category and task recommendation. Useful for choosing a range
  of *a priori* expected capabilities, not proof of outcome quality.
- **Size/context:** A proprietary model's parameter count is unknown unless
  publicly disclosed. The effective context window through the exact
  OpenCode/Copilot route must be checked separately; pricing thresholds are
  not reliable proxies for model parameter count.
- **Measured effectiveness:** Only actual independently verified results and
  blind design reviews on the chosen xcore task establish how well the models
  perform *in this experiment*. Reviewer design scores are not a broad LLM
  benchmark.

The predeclared profile, model developer, selection rationale, publication
source and source-check date live in `[model_profiles."..."]` in
`benchmark.toml`. These are **editable** when models change. If a profile is
missing the report marks it unknown, rather than guessing its capabilities.
At trial creation the runner archives a sanitized snapshot of this catalogue
(and task descriptions, model matrix and prices) in `experiment.json` so a
report regeneration cannot silently rewrite history after models change.
For report details and source links see [REPORTING.md](REPORTING.md).

## Initial problem: UART and heartbeat

**User-facing intent:** Produce serial data on one XU316 pin and a periodic
heartbeat on another; both must run simultaneously, and the user wants a way
to test this without hardware.

We selected this as the first problem because it is small enough for repeatable
trials, but exposes xcore-specific behavior that generic code generation may
get wrong. A program can compile while failing real-time waveform accuracy.
It naturally creates design tradeoffs about I/O, scheduling, concurrency,
resource use and testing. Crucially, it can be verified in xsim without
sharing an XTAG or a physical board.

Three **independent user prompts** vary in detail; none tells the agent how to
implement it, what language or build system to use, or which libraries to call:

1. **Specific:** XU316; repeating 0x55/0xA5; 115200 baud, 8N1; 1 kHz square
   wave; demonstrate that the outputs do not interfere.
2. **Everyday:** XU316; 115200-baud serial; 1 kHz heartbeat; reliable concurrent
   behavior; simulation requested. The agent decides framing and data.
3. **Vague:** XU316; serial data and a regular heartbeat on separate pins;
   simultaneous operation; demonstrate without hardware. The agent must
   choose and document sensible timing, framing and other assumptions.

### Fair grading across prompt specificity

The hidden verifier must test **what was actually requested**. The specific
prompt may be checked against 115200/8N1/1kHz; the vague prompt must not be
failed for choosing another reasonable baud or heartbeat frequency. Vague
prompts can instead be judged on coherent assumptions, observed behavior,
repeatable tests and clarity. The verifier cannot silently impose detailed
requirements that only appeared in the specific prompt.

**Current gap:** No known-good Pyxsim or hardware timing oracle is packaged.
The harness intentionally marks unconfigured validation as `unverified`,
while allowing the implementations and reviewer to run. Use
`--require-verification` when a strict correctness gate is needed.
See `tasks/uart-heartbeat/VERIFIER.md`.

## Worker and reviewer separation

- A Python orchestrator launches workers individually; workers **do not
  spawn or collaborate with subagents** and do not get any other candidate's
  code, transcripts or results in their prompt.
- Each worker gets the same task-specific clean starter project, permissions,
  model budget and a fresh session. Conditions differ only in the availability
  of skills and minimal condition-specific guidance.
- The worker is instructed not to inspect any earlier attempts, sibling
  workspaces, verifier implementation or local generated solutions. It can use
  its installed toolchain, published libraries and normal documentation.
- After a worker returns, a **separate trusted verifier** builds/runs tests.
  A worker's self-reported claims are not verification. Actual measurements
  (cores/threads, memory, linked libraries, timing) should come from known-good
  tool outputs and verifiers, or be explicitly marked *unknown*.
- A high-capability reviewer using a **different exact model ID** receives
  anonymized source snapshots and objective verifier results. It does not
  receive model labels, skill conditions, worker costs or elapsed times.
  Only the reporting phase unmasks the identities.
- Reviewer ratings cover architectural decisions, real-time correctness,
  maintainability, test quality, resource efficiency and XMOS conventions.
  These are **subjective** and secondary to objective test results.

> Isolation caveat: A separate directory is not a filesystem security boundary.
> This harness stops ordinary workspace crossover, but shell tools on one host
> may still read user-readable files. For a defensible trial, run each worker in
> a separately isolated VM/container with only its starting files and toolchain
> mounted. No other worker's solution may be mounted or accessible. The
> bundled harness does not automatically provide a container per worker.

## Measurements and interpretation

| Output | Collection | Interpretation |
|---|---|---|
| Correctness | Independent, hidden build/xsim/hardware verifier | Primary binary or per-test outcome; `unverified` is not a pass |
| Time | OpenCode worker elapsed + verifier time | Time to **post-run verified** solution, not earliest correct revision |
| Tokens | OpenCode per-session export/events | Input, cached read/write, output, plus usage provenance |
| Costs | Configured GitHub Copilot token rates | Estimated USD + AI Credits; reconcile billing for actual extras |
| Skills used | Observed skill tool calls | Actual retrieval evidence where transcripts exist, not model claims |
| Hardware threads | Compiler/linker/resource inspection or audit | Per tile; do not conflate source functions with logical cores |
| Libraries | Trusted linker/build evidence | Otherwise self-reported and explicitly qualified |
| Memory | XTC memory/map/report output | Per tile, not inferred from source length |
| Design decisions | `DESIGN.md` + anonymous reviewer assessment | Judge explains tradeoffs and risks, with uncertainty |

The estimated **worker cost and reviewer cost are separate**, so the cost of
running the evaluator isn't misattributed to implementing with/without skills.
GitHub Copilot includes allowances, and actual charges may differ from token
estimates due to billing/account terms or extended-context rates.

### Decision rules

Do not declare a skill effective because it was *loaded*: that is an adoption
metric, not a correctness metric. Compare paired pass rates first, then quality
scores, time, tokens and cost. Publish failures and null measurements. With
one run per condition, results are anecdotal; repeat and randomize trial order
if attempting statistical inference. Model availability and provider behavior
can change; archive the model IDs, actual observed IDs when available, OpenCode
version, XTC version, skill repository SHA, environment and task prompt hash.

## Extending beyond the first problem

Tasks are a list of `[[tasks]]` entries in `benchmark.toml`, each pointing to
its own prompt, starter project and verifier commands. New problem families
could include:

- **Software-defined SPI/I3C:** Exercises precise interface timing and xsim
  testbench authoring; samples edge-cases rather than a happy-path demo.
- **Microphone/audio pipeline:** Tests discovery and correct integration of
  existing XMOS libraries, not just writing a bespoke implementation.
- **DSP compute + real-time I/O:** Reveals vectorization, memory and thread
  placement tradeoffs; can expose architectural wins and failures.
- **Hardware peripheral integration:** Tests real pin/clock/physical behavior.
  With one connected board, these trials must be serialized and independently
  measured using an XTAG/logic analyser/other appropriate evidence.

Prefer problems with an **objective oracle** (build and functional checks,
waveform timing, numerical accuracy), known relevant skills, and enough
latitude to reveal important design choices. Avoid prompts that reveal the
exact implementation or tell the worker to use a skill.

## The planned MCP experiment

**Phase 1 (now):** Compare `no_skills` versus native local `SKILL.md` files
with identical tasks. This tells us if the content of `xskills` helps at all.

**Phase 2:** Add a local or remotely hosted XMOS MCP server serving the exact
same pinned skills. Run the same task IDs, implementer models, verification,
reviewer, budgets and hardware/toolchain, with `mcp` enabled and native skill
loading disabled. Compare against the phase-1 baseline.

We can compare multiple MCP delivery implementations without changing the
benchmark problems:

- **Conventional MCP tools:** Server exposes a catalogue/discovery tool and a
  `load_skill` equivalent that returns instructions. Evaluate actual discovery,
  selection and content retrieval in each agent.
- **Different discovery advertisements:** Minimal tool descriptions versus
  server instructions versus an abbreviated catalogue in instructions. Compare
  how consistently agents consult the tools and the context/token overhead.
  Client support and limits for MCP server instructions differ, so record
  whether each client actually injects them and whether they are truncated.
- **Native SEP-2640 (when client support exists):** Same skills made available
  using the standard extension. Check whether the client actually registers
  them and retrieves their contents; availability on the server alone is not
  evidence that the agent can use them.

Measure *discovery rate* and *load rate* separately from correctness; record
which skill was loaded, what prompted loading, and whether the instructions
were subsequently applied. In particular, the model deciding not to consult a
skill can be normal agent behavior in **both** native and MCP variants. The
empirical difference is the subject of this experiment, not an assumed fact.

**Goal:** A grounded decision on whether to deploy XMOS skills over MCP, which
advertising mechanism works across OpenCode/Claude/Copilot, and whether the
user experience can avoid installing a separate skills repository. The runner
currently executes via OpenCode with GitHub Copilot; testing other agent hosts
is a later adapter project, not a capability of this first harness.

## Risks / exclusions

- Unverified tasks, silent model substitution, hidden skill contamination or
  shared workspaces invalidate strong conclusions. Record them explicitly.
- Evaluator model bias can affect rankings. Consider a second human review or
  reviewer swap for high-stakes decisions.
- Model pricing, rate limits, API support and tool behavior change over time.
- Prompt variation changes specification difficulty; do not compare raw
  outcomes between different detail levels as if they were equivalent tasks.
- This first version is **not** an automatic hardware measurement rig and does
  not yet compute confidence intervals.

For practical commands and file layouts, see [`QUICKSTART.md`](QUICKSTART.md)
and [`../README.md`](../README.md).
