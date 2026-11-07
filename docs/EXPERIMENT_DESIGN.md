# Experiment design: xcore implementation

## Question

Do reviewed XMOS skills improve an agent's ability to implement correct,
efficient, maintainable xcore software, and does the effect vary by model?
This is distinct from the ability to author a simulation test framework.

## Controlled implementation comparison

Every configured worker solves the same task from the same clean starter in
an independent OpenCode session through GitHub Copilot. Both conditions have
XTC Tools 15.3.1 preactivated; `native_xtc` additionally receives pinned XMOS
skills, while `no_skills_xtc` denies native skill loading. The model receives
the same task contract and budgets. Toolchain discovery is not the scored task.

The primary UART/heartbeat contract fixes the target/pins/clocks and source
submission interface while leaving language, architecture and timing methods
open. It publishes the exact 1 ms startup / 25 ms observation protocol and
UART/square-wave tolerances. It is inappropriate to secretly impose these
numbers on a different, underspecified problem; another task needs its own
published contract and supported independent verifier.

The runner currently executes trials sequentially, in recorded configuration
order, avoiding shared simulator/host contention. The order is not randomised;
model latency, provider load and ordering effects remain possible confounders.
Use multiple repeats and counterbalanced conditions for stronger studies.

## Mandatory independent correctness

Missing or unsupported verification aborts before any model call. Every
completed submission is freshly built and simulated by the harness-owned
verifier outside the worker directory. Candidate tests, build scripts, old
binaries/waveforms and self-reported result files cannot determine acceptance.
The worker gets an identical convenience checker, not permission to alter the
final oracle. The checker is validated locally with correct/faulty firmware
and synthetic corruption/error-handling tests before paid trials.

Results are independent **passed**, **failed**, or **execution error**.
Missing/malformed evidence is not accepted. A successful receipt includes
fresh build/simulation stages, fixed-window measurements, protocol ID and
verifier source fingerprint. Compiler resource reports provide core and SRAM
allocation; physical timing, flash use and runtime stack peaks remain unknown.

The verifier checks the fixed compiled application's actual output, not a
separate arithmetic model or a candidate's assertion that xsim succeeded.
The exact contract/parser constraints are in `tasks/uart-heartbeat/VERIFIER.md`.

## Blinded implementation review

Only after workers and verification finish, a distinct reviewer receives
anonymous implementation sources and independent results. It does not receive
model/condition identities, usage or worker elapsed time. A seeded shuffle
assigns candidate aliases. Candidate-authored test trees are omitted.

Ratings concern architecture, real-time design, maintainability, resource
efficiency and xcore idioms. Test-framework sophistication/count/duration is
not scored. The reviewer may assess timing risks beyond the fixed observation
window but must not invent measurements or treat failed verification as passing.
Its ranking is a subjective design assessment, not a substitute for correctness.

## Measurements and reporting

- Primary: independent correctness under identical acceptance tests.
- Secondary: implementation-only blind design ranking and measured resources.
- Effort: worker wall time, tokens and estimated credits/USD.
- Verification time is separate; worker + final verifier duration is post-run
  time to verified finish, not the earliest moment the implementation was correct.

The report compares matched task/model/repetition pairs. Multiple matched
repeats use medians rather than the best attempt. Failed submissions cannot
win recommendations for fastest or cheapest working implementation. Single
trials are exploratory and cannot establish a causal skills effect.

Freeze prompt hashes, verifier ID/source hash, tool versions, models, rates,
skill revision/content hash, budgets and trial order in `experiment.json`.
Do not pool different prompts/protocols or infer effective parameter counts
from provider capability tiers. Usage estimates are not invoices; actual model
identity is marked unknown unless independently available in exported metadata.

## Integrity limits

Temporary project directories, explicit permissions and instructions prevent
ordinary session sharing, but are not an OS security boundary. Shell tools can
access other host paths, installed tools or global agent settings. For rigorous
isolation, use clean authenticated profiles and containers/VMs that expose only
the candidate project, approved toolchain and selected skills, keeping the
trusted verifier outside writable mounts. Do not mount prior solutions.

Old unchecked/mixed-score pilot experiments are not supported by current
scoring/reporting. They are not silently regraded or deleted.

## Later: framework-authoring skills

Create a separate task class that provides an application and asks the agent
to author simulation tests. Evaluate the generated framework against the same
known-good variants and hidden fault corpus, including errors, timeouts and
stale traces. Score fault detection, false failures, reproducibility and
runner reliability; report time/cost separately. Compare baseline skills with
baseline plus framework skills, under identical prepared environments and
budgets. Do not mix these scores into implementation ranking.
