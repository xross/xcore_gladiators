# Implementation status

## Implemented

- Independent OpenCode worker sessions and temporary projects via GitHub Copilot.
- Full matrix across all configured workers; reduced four-worker smoke matrix.
- Equal preactivated-XTC conditions with and without pinned native skills.
- Mandatory supported-verifier preflight; no optional/unchecked execution mode.
- Uniform source-only XTC build and fixed 1 ms + 25 ms xsim verification.
- Independent UART/square-wave checks, compiler resources and protocol receipts.
- Synthetic fault/error tests and opt-in real-XTC good/faulty firmware tests.
- Implementation-only blind review, omitting candidate test trees and testing scores.
- Rank-ordered readable RST reports, evidence appendix and frozen regeneration.
- SQLite progress/history index for independently checked outcomes and errors.
- Worker progress, process-group timeouts and preserved diagnostics.

## Not implemented / not established

- Separate simulation-test-framework-authoring benchmark and fault corpus.
- OS/container/VM isolation against intentionally hostile workers.
- Physical board, electrical, flash-boot or long-duration timing validation.
- Statistical evidence of skills effectiveness from single trials.
- Exact account billing or guaranteed actual served-model identity.

See `QUICKSTART.md` for commands and `../tasks/uart-heartbeat/VERIFIER.md` for the
public acceptance contract. Tests/fake model fixtures are not scored benchmark
runs. Real benchmark commands make paid model calls; local verifier tests do not.
