# Gladiators: the referee's report

**Are you not entertained?** (film *Gladiator*). The final RST scoreboard
is an evidence-based comparison, not a popularity contest.

*"Gladiators, you will go on my second whistle."* (John Anderson, British TV
show *Gladiators*). This is the cue for the independent implementers to start;
the separate referee reviews their anonymous solutions only after the trials
and trusted verification are complete. It cannot see their model identities
or skills conditions when scoring.

The benchmark uses a handful of Gladiators event names to label measurable
stages: **The Wall** (trusted verification), **Duel** (paired condition
comparison), **Hang Tough** (repeatability), and **The Eliminator / Travelator**
(time to a verified finish). A worker that fails verification has not finished
the Travelator, regardless of elapsed time. Event names are never included
in worker prompts, and they do not change objective pass/fail criteria.

The benchmark writes `bench-results/<run-id>/REPORT.rst` as **reStructuredText (RST)**. The final report is a
**management-facing result summary**, not a full replication protocol. It should be understandable
to somebody who did not run OpenCode or read the worker transcripts. It uses proper RST headings, `list-table` directives and literal code blocks, so it can be rendered with Docutils or Sphinx (including in XMOS documentation workflows). No Markdown conversion step is needed.

The runner has no rendering dependency. To preview the report as HTML, install Docutils separately and run `rst2html REPORT.rst REPORT.html`, or include the RST in a Sphinx documentation build. Markdown is retained only for the developer README and methodology documentation.

## Report versus methodology documentation

`REPORT.rst` puts executive findings, the scoreboard, paired comparisons and the
referee's summary **first**. It gives management a concise methodology summary,
then an appendix with detailed evidence and reproducibility records:

1. Aim, selected problems and one-line rationales.
2. High-level experiment design: OpenCode via GitHub Copilot, models, conditions,
   independent runs, verifier, blind reviewer and the main limitations.
3. Short profiles for each worker and the reviewer (model, developer, Copilot
   access route and published tier); expected strengths and rationale are in the appendix.
4. Verified outcomes, design verdicts, run-by-run token/time/cost measurements,
   actual skills observed, verified resources, paired comparisons and the
   proposed follow-up with MCP-delivered skills.
5. Reproducibility pointers to the protocol and frozen experiment metadata.

**Do not paste the full protocol into every report.** The canonical, detailed
method is in `docs/EXPERIMENT_DESIGN.md`: test hypotheses, selection of tasks,
prompt-specificity controls, run ordering, isolation, skill conditions,
verification, blind judging, metric definitions, statistical limitations and
the later native-vs-MCP comparison. This document (`docs/REPORTING.md`)
records output requirements, model-provenance rules, pricing-accounting
interpretation and how report regeneration preserves provenance.

The run-specific frozen configuration and ordered trials live in
`bench-results/<run-id>/experiment.json`. Worker transcripts, original prompts,
verifier outputs and raw JSON evidence live in the same results tree. Those
artifacts—not the README—are the source of run-specific results. Report
regeneration reads the frozen metadata rather than silently substituting edits
made to `benchmark.toml` after the run.

### Minimum methodological points shown in REPORT.rst

- Access/provider: Python -> OpenCode CLI -> GitHub Copilot, not a direct model
  developer API; separate worker and reviewer sessions.
- Conditions: the same task in no-skills/native-skills conditions (and MCP later),
  identical prompt/starter within a task, condition-specific skill availability.
- Independent validation and blind review: verification determines correctness;
  reviewer assesses design without seeing models/conditions.
- Limits: fixed/non-randomised run order, current directory-level rather than
  OS-level isolation, single trials exploratory, estimates not actual billing.

The full documentation must be updated whenever the experiment's methodology
changes. The report should remain concise and readable without assuming that
management will open the documentation.

## Model size and quality: do not invent precision

GitHub Copilot publishes labels such as **Powerful** and **Lightweight** and
recommends models for different task types. Those are provider descriptions of
intended use and tier, **not published parameter counts, measured performance
on xcore tasks, or an objective ranking**. The initial proprietary models'
parameter counts are not publicly disclosed. Likewise, the effective context
window through a particular OpenCode/Copilot adapter must be verified on the
actual deployed client; do not infer it from a Copilot pricing threshold.

`benchmark.toml` contains the editable `[model_profiles."<model-id>"]`
entries. For a new model, supply at least:

```toml
[model_profiles."github-copilot/new-model-id"]
display_name = "New model"
developer = "Model developer (check the provider documentation)"
published_category = "Powerful / Lightweight / etc. (only if documented)"
parameter_count = "Not publicly disclosed"
context_window = "Not verified for OpenCode via GitHub Copilot"
quality_expectation = "What you expect this model to be good at — hypothesis only"
selection_rationale = "Why the experiment needs this model"
source = "https://official-source.example/"
source_checked = "YYYY-MM-DD"
```

**Changing a model ID does not automatically update its profile.** If a
profile is missing, the report explicitly marks fields as unknown rather than
assigning a developer or capability. Check `opencode models` before paid runs.

Default-model profile sources, checked 2026-11-06:

- [GitHub: supported models and developers](https://docs.github.com/en/copilot/reference/ai-models/supported-models)
- [GitHub: model comparisons and intended uses](https://docs.github.com/en/copilot/reference/ai-models/model-comparison)
- [GitHub: Copilot token-rate table](https://docs.github.com/en/copilot/reference/copilot-billing/models-and-pricing)

## A report stays faithful after configuration changes

At run creation, the runner stores a **sanitised report configuration** inside
`experiment.json`, along with a SHA-256 digest of the actual TOML file and a
full, ordered trial manifest. It records the task rationales, worker IDs,
reviewer ID, model profiles, prices and skill-condition types. It deliberately
**does not** copy authentication credentials, local home/profile paths, MCP
URLs or secrets into this snapshot.

The command below uses that snapshot rather than the current edited TOML when
regenerating reports:

```sh
python3 benchmark.py report --results bench-results/<run-id>
```

Older result directories without a report snapshot can still be rendered, but
then metadata may come from the current TOML file; treat those reports as less
reproducible. Task text is also captured in the per-trial worker prompts, which
can be audited separately without trusting any summarised description.

## Reviewer versus model-quality claim

The separate high-capability reviewer scores **implementations** on software
quality, not the generic quality of an LLM. Objective correctness comes from
independent build/simulator/hardware checks. Blind review prevents the
reviewer seeing worker identity and skills condition until results are mapped
back into the final report. It does not eliminate subjective scoring bias.

Do not claim that a model is "better" because its published tier is Powerful,
or that skills are effective because they were loaded. Compare verified paired
outcomes and publish uncertainty, then collect repeated trials.

## Repeated experiments and quick pilot

`--smoke` runs two configured worker models (each with/without native skills),
then invokes the blind reviewer, even without a verifier. Such trials are
reported as `unverified`, not passes. When `verify_commands` are configured,
they run for each candidate and must produce `benchmark-verification.json` with
an explicit boolean `passed`; missing output is a failed validation.
Use `--require-verification` for a strict preflight gate.
Smoke mode uses one repeat and remains exploratory. The four worker runs plus referee incur actual GitHub
Copilot usage when running against live models.

Reports live in unique, immutable experiment directories; the SQLite
`bench-results/gladiators.sqlite3` index permits cross-run trend/comparison
queries and is rebuildable using `benchmark.py index`. Never compare results
from different task/prompt revisions as if they were paired trials.
