# Gladiators: implementation status

As of 2026-11-06.

| Feature | Status |
|---|---|
| Project identity, themed documentation and Gladiators-branded RST report | Implemented |
| Repeated task/model/condition matrix | Implemented |
| `--smoke`: two configured workers, two skill conditions, reviewer, one problem | Implemented; fake-process tests only |
| Persistent per-run archive and SQLite history/trend/compare commands | Implemented; fake-process tests only |
| Live CLI progress: trial count, per-stage heartbeats, event/tool activity, verifier and referee | Implemented; fake-process tests only |
| All worker/reviewer model IDs editable in TOML | Implemented |
| Reviewer required to be a different exact ID | Implemented |
| Three prompt detail levels | Implemented |
| Individual OpenCode runs and copied native skills | Implemented; needs real-client verification |
| Required `--skills-dir` CLI option and preflight validation before model calls | Implemented; local tests |
| Worker errors and incomplete comparisons flagged in console and RST; partial reviews skipped | Implemented; local tests |
| MCP condition via `mcp.servers` remote URL | Configured; not yet tested with a live MCP server |
| Tool-call, token, time and estimated Copilot cost parsing | Implemented; needs live export verification |
| Final report in reStructuredText (`REPORT.rst`) with concise methodology summary and OpenCode -> GitHub Copilot access route | Implemented; structurally tested |
| Model profiles: developer, published category, parameter disclosure, expected strengths and source | Implemented; editable in `benchmark.toml` |
| Frozen per-run reporting metadata and config digest | Implemented; see `experiment.json` |
| Blind independent model review | Implemented with a fake CLI end-to-end test |
| Independent verifier interface and unverified guard | Implemented |
| Known-good XU316 xsim UART/heartbeat waveform oracle | **Not implemented** |
| Verified thread, library, memory extraction | Schema supported; **not measured yet** |
| OS-level per-worker container/VM isolation | **Not implemented**; recommended for rigorous tests |
| Live OpenCode/GitHub Copilot trials | **Not executed** |
| Real Copilot billing reconciliation | **Not implemented** |

**Next steps:** implement the hidden Pyxsim verification for the first task,
confirm model identifiers and CLI export format with your installed OpenCode,
run a single exploratory pilot, and then execute repeated paired trials in
separate sandboxed environments. Until then, this is a useful **experimental
harness prototype**, not proof that skills improve outcomes.

## Workspace isolation fix (2026-11-06)

A live OpenCode event log showed a worker reading the Gladiators repository
root and writing directly into `tasks/uart-heartbeat/starter/`, rather than
its independent candidate directory. OpenCode can use a stale inherited `PWD`
even when Python launches it with `subprocess.Popen(cwd=...)`.

The harness now resets `PWD` to the actual work directory for each OpenCode
launch, session export, and verifier invocation. Worker workspaces are placed
in the system temporary directory instead of inside `bench-results/`. A
regression test checks `PWD == os.getcwd()` with an intentionally stale parent
`PWD`, and the fake OpenCode CLI validates the same invariant.

This prevents that known accidental project-root mismatch, but does not
constitute OS-level sandboxing. The exact exception behind a historical
`validation.status = error` remains unavailable without its `result.json`.

## Skills input and incomplete experiment handling

The native skill directory is supplied per invocation with `--skills-dir PATH`,
never in `benchmark.toml`. For any planned native-skills trial, missing or
invalid skills cause the command to fail *before* opening a results archive or
calling GitHub Copilot. Worker errors are recorded in `result.json` and logs,
printed in the console, and shown prominently in `REPORT.rst`. The referee is
not invoked on an incomplete set of candidates; failed experiments are
archived and return a nonzero exit status rather than claiming an evaluation.

## Optional verification and report layout (2026-11-06)

- `--smoke` runs both skill conditions for two models and the blind referee
  even without an independent verifier; outcomes are clearly `unverified`.
- A configured smoke verifier must still produce an explicit
  `benchmark-verification.json` outcome. Missing evidence is a failed check.
- `--require-verification` restores a strict preflight gate when wanted.
  The legacy `--allow-unverified` switch is accepted but unnecessary.
- The initial UART/Pyxsim behavioural verifier has **not** been implemented.
  Do not treat an untrusted worker-generated test or merely starting `xsim` as a pass.
- Management `REPORT.rst` begins with results, skills impact and referee
  conclusions. Method at a glance follows; detailed metrics and model profiles
  are in an appendix. Model size is not shown.
- Native skills paths are supplied only with the required `--skills-dir` flag.
