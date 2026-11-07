# Report format and interpretation

`REPORT.rst` is a management-facing result summary. It is rendered using
reStructuredText list tables, with narrow rank/score columns and more space for
conditions and explanatory text. The generator uses no model calls.

## Main report

1. **Executive summary:** independent pass/fail/error counts, highest blind
   design rank, fastest and lowest-usage independently passing submissions.
2. **Results at a glance:** ordered by blind rank, with model/condition, worker
   language, time, estimated credits and independent result. Unranked entries follow.
   The matrix size and repeats are explicit; report regeneration does not invent
   missing conditions or additional trials.
3. **Language choices and skill consultation:** C/XC percentages within each
   condition, with no combined overall row. Count all submissions, including
   failures, not lines of code. Classify firmware under the archived `source/src`
   tree; exclude host tests and installed libraries. Mixed languages and missing
   source are separate categories rather than double-counted or silently omitted.
   Flag enabled skills with no recorded consultation, while keeping unavailable
   telemetry distinct from zero usage. Successful skill loads and explicit file
   reads count; mere availability or a failed load does not. Show paired language
   changes alongside independent outcomes. Recorded prescriptive C guidance can
   be quoted, but is not proof that language or skills caused a failure.
4. **Did skills help?** Matched pass outcomes, elapsed-time and usage differences,
   and blind preferences. Repeated matched trials use medians, never selected
   best attempts or pooled unrelated tasks.
5. **Blind design ranking:** concise ranking reasons with readable model and
   condition labels, not the first sentence's repeated verifier counts. Select
   reasons from the saved reviewer summary; preserve the complete review in the
   appendix and leave the original ranking unchanged. Candidate tests are not scored.
6. **Limits/next steps:** finite simulation, single-trial uncertainty, estimated
   billing, isolation/model-provenance caveats and repeated-trial recommendations.

No separate toolchain-activation analysis is needed: both primary conditions
receive XTC so workers can attempt the implementation. No film quotation is
included. Raw run IDs and anonymous labels do not dominate headings.

## Strict correctness evidence

Every configured task must have a supported harness-owned verifier. Every
completed submission must have an explicit independent pass/fail receipt with
protocol ID, verifier fingerprint and measurements. Passing receipts require
the fresh build and fixed-window simulator evidence. Missing, malformed or
contradictory receipts are errors, never successful/unchecked results.

The report rejects unchecked archives and mixed implementation/testing rankings.
It does not reconstruct correctness from worker event logs, self-reported tests,
DESIGN.md, test counts, simulation lengths or reviewer opinions. Different
verifier fingerprints cannot be compared as one task's acceptance results.

Execution errors remain visible and mark the matrix incomplete. Failed
submissions cannot win speed/cost recommendations. Worker wall time, verifier
duration and worker-plus-verifier finish time are distinct metrics.

## Appendix

The appendix contains a short identifier key for log lookup, frozen methodology,
full IDs, token telemetry, skill retrievals, model profiles/provenance, exact
times/costs, independent resource/waveform measurements, reviewer reasoning,
rating tables and separate decision/risk bullets. Ratings are not JSON strings.

Independent core/RAM counts come from the compiler report, not source-level
function counts. Physical timing/flash use remains unknown unless measured.
Credits and USD are estimates; missing telemetry must remain unknown and model
reasoning tokens must not be billed twice.

## Provenance and regeneration

Reports use `experiment.json`'s frozen settings, not later edits to TOML.
Protocol constants, verifier SHA-256, XTC version and skill revision are recorded.
Do not pool different prompts, verifier revisions or conditions. Provider tiers
and expected strengths are descriptions, not measured model quality or known
parameter counts. An unavailable served-model identity stays explicitly unknown.

```sh
python3 benchmark.py report --results bench-results/<run-id>
```

The command regenerates presentation only, never evidence or rankings. Only
strictly verified experiments are supported. Historical unchecked files are
neither converted nor deleted. External Docutils/Sphinx/rst2pdf may render the
RST; they are not dependencies of the benchmark runner.

## Future framework-authoring skills

Evaluate simulation-framework generation in a distinct task class using
known-good and faulty implementations, fault detection, false failures and
runner reliability. Do not mix those scores into xcore implementation ranking.
