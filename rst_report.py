"""Management-facing RST report; granular reproducibility data stays in the appendix.

This module formats immutable experiment metadata, trusted verifier results,
agent telemetry and blinded model reviews. It never calls a model or creates
verification evidence.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Callable


def cell(value: object) -> str:
    """Single RST table cell, safe for model-generated text."""
    if value is None:
        return "n/a"
    value = re.sub(r"\s+", " ", str(value)).strip().replace("\\", "\\\\")
    for ch in ("*", "`", "|"):
        value = value.replace(ch, "\\" + ch)
    return value or "n/a"


def literal(value: object) -> str:
    return f"``{str(value).replace('`', "'").replace(chr(10), ' ')}``"


def as_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True)


class Rst:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def title(self, title: str) -> None:
        self.lines += [title, "=" * len(title), ""]

    def section(self, title: str) -> None:
        self.lines += [title, "-" * len(title), ""]

    def subsection(self, title: str) -> None:
        self.lines += [title, "~" * len(title), ""]

    def para(self, text: str) -> None:
        self.lines += [text.strip(), ""]

    def bullets(self, items: list[str]) -> None:
        for item in items:
            self.lines.append("- " + item.strip())
        self.lines.append("")

    def table(self, headers: list[str], rows: list[list[object]]) -> None:
        self.lines += [".. list-table::", "   :header-rows: 1", "   :widths: auto", ""]
        if not rows:
            rows = [["n/a"] + ["no data"] * (len(headers) - 1)]
        for row in [headers, *rows]:
            if len(row) != len(headers):
                raise ValueError("Mismatched RST table row")
            for index, value in enumerate(row):
                self.lines.append(("   * - " if index == 0 else "     - ") + cell(value))
        self.lines.append("")

    def code(self, content: str, language: str = "text") -> None:
        self.lines += [f".. code-block:: {language}", ""]
        for line in content.splitlines() or ["(no content)"]:
            self.lines.append("   " + line)
        self.lines.append("")

    def render(self) -> str:
        return "\n".join(self.lines).rstrip() + "\n"


def _condition_status(entry: dict | None) -> str:
    if entry is None:
        return "missing"
    if entry.get("error") or entry.get("requested_model_mismatch"):
        return "error"
    return entry.get("validation", {}).get("status", "unknown")


def _skill_names(entry: dict) -> str:
    names = []
    for call in entry.get("skill_calls", []):
        tool = str(call.get("tool", "")).lower()
        if "skill" in tool and not tool.endswith("list_skills"):
            names.append(str(call.get("skill_or_query") or call.get("tool")))
    return ", ".join(dict.fromkeys(names)) or "none observed"


def _review_for_task(reviews: list[dict], task_id: str) -> dict | None:
    return next((review for review in reviews if review.get("task_id") == task_id), None)


def _paired(results: list[dict]) -> dict[tuple, dict[str, dict]]:
    pairs: dict[tuple, dict[str, dict]] = {}
    for entry in results:
        key = (str(entry.get("task_id")), str(entry.get("model_label")), entry.get("repeat"))
        pairs.setdefault(key, {})[str(entry.get("condition"))] = entry
    return pairs


def build_report(root: Path, conf: dict, manifest: dict | None, results: list[dict],
                 reviews: list[dict], now_iso: Callable[[], str],
                 model_profile: Callable[[dict, str], dict]) -> Path:
    experiment = conf["experiment"]
    planned = manifest.get("run_order", []) if manifest else []
    started = manifest.get("started_utc", "unrecorded") if manifest else "unrecorded"
    missing = max(len(planned) - len(results), 0)
    errors = [e for e in results if e.get("error") or e.get("requested_model_mismatch") or
              e.get("validation", {}).get("status") == "error"]
    incomplete = bool(errors or missing)
    passed = sum(_condition_status(e) == "passed" for e in results)
    failed = sum(_condition_status(e) == "failed" for e in results)
    unverified = sum(_condition_status(e) == "unverified" for e in results)
    pairs = _paired(results)
    complete_pairs = [(key, pair) for key, pair in pairs.items()
                      if "native" in pair and "no_skills" in pair and
                      all(_condition_status(e) in ("passed", "failed") for e in pair.values()
                          if e.get("condition") in ("native", "no_skills"))]
    native_improved = sum(_condition_status(pair["native"]) == "passed" and
                          _condition_status(pair["no_skills"]) == "failed"
                          for _, pair in complete_pairs)
    native_worsened = sum(_condition_status(pair["native"]) == "failed" and
                          _condition_status(pair["no_skills"]) == "passed"
                          for _, pair in complete_pairs)
    native_unchanged = len(complete_pairs) - native_improved - native_worsened
    worker_cost = sum((e.get("usage", {}).get("copilot_estimated_usd") or 0) for e in results)
    reviewer_cost = sum((e.get("usage", {}).get("copilot_estimated_usd") or 0) for e in reviews)
    total_cost = worker_cost + reviewer_cost

    r = Rst()
    r.title("Gladiators - AI Coding-Agent Benchmark")
    r.para("*Are you not entertained?*")
    r.para(f"**Experiment:** {cell(experiment.get('name', root.name))} | "
           f"**Started (UTC):** {cell(started)} | **Report:** {now_iso()}")

    # Results first: nothing in the executive portion requires reading methodology.
    r.section("Executive summary")
    if incomplete:
        r.para("**INCOMPLETE EXPERIMENT — NO VALID SKILLS-VERSUS-NO-SKILLS CONCLUSION.** "
               "Some candidates failed to run or are missing. Correct the errors and repeat the paired trial.")
    elif not complete_pairs:
        r.para("**No independently verified paired comparison is available.** "
               "Do not draw conclusions about skills effectiveness from these results.")
    else:
        r.para(f"Across {len(complete_pairs)} independently verified native/no-skills pairs, "
               f"skills improved the pass outcome in {native_improved}, worsened it in "
               f"{native_worsened}, and made no difference to pass/fail in {native_unchanged}. "
               "These are descriptive results, not statistical evidence.")
    r.para(f"**Trials:** {len(results)} recorded" + (f"/{len(planned)} scheduled" if planned else "") +
           f"; **verified passes:** {passed}; **verification failures:** {failed}; "
           f"**unverified:** {unverified}; **run errors:** {len(errors)}. "
           f"**Estimated Copilot usage:** ${total_cost:.4f} "
           f"(workers ${worker_cost:.4f}; referee ${reviewer_cost:.4f}).")
    if errors:
        r.subsection("Errors requiring attention")
        r.table(["Run", "Condition", "Error"], [
            [e.get("run_id"), e.get("condition"), e.get("error") or
             e.get("validation", {}).get("reason") or "Served model mismatch"]
            for e in errors])

    r.section("Results - scoreboard")
    r.table(["Task", "Model", "Skills", "Verified", "Time (s)", "Est. cost (USD)"], [
        [e.get("task_id"), e.get("model_label"), e.get("condition"),
         _condition_status(e), e.get("time_to_verified_success_s") or
         e.get("invocation", {}).get("elapsed_seconds"),
         e.get("usage", {}).get("copilot_estimated_usd")]
        for e in results])
    r.para("**Verified** means an independent validator passed. The time shown is "
           "post-run verified time for passes; otherwise it is worker elapsed time. "
           "Costs are usage estimates, not billed charges.")

    r.section("The Duel - skills versus no skills")
    if incomplete:
        r.para("**Comparison invalid for this experiment:** one or more candidates did "
               "not complete; the table below identifies missing or erroneous conditions.")
    rows = []
    for (task, model, trial), pair in sorted(pairs.items()):
        if "native" not in conf["conditions"] and "no_skills" not in conf["conditions"]:
            break
        no = pair.get("no_skills")
        native = pair.get("native")
        if not no and not native:
            continue
        usable = (_condition_status(no) in ("passed", "failed") and
                  _condition_status(native) in ("passed", "failed"))
        cost_delta = None
        time_delta = None
        if usable:
            a = native.get("usage", {}).get("copilot_estimated_usd")
            b = no.get("usage", {}).get("copilot_estimated_usd")
            if isinstance(a, (int, float)) and isinstance(b, (int, float)):
                cost_delta = round(a - b, 5)
            a = native.get("time_to_verified_success_s")
            b = no.get("time_to_verified_success_s")
            if isinstance(a, (int, float)) and isinstance(b, (int, float)):
                time_delta = round(a - b, 2)
        rows.append([task, model, trial, _condition_status(no), _condition_status(native),
                     time_delta, cost_delta])
    r.table(["Task", "Model", "Trial", "No skills", "Native", "Time delta (s)",
             "Cost delta (USD)"], rows)
    r.para("Deltas are native minus no-skills (negative is quicker/cheaper). "
           "Time deltas are reported only where both implementations independently passed.")

    if "no_skills_xtc" in conf["conditions"]:
        r.section("XTC environment control - avoiding the first hurdle")
        r.para("This control tests whether an agent's apparent lack of progress comes from "
               "finding and activating XTC rather than solving the engineering problem. "
               "Neither control receives XMOS skills; only no_skills_xtc starts "
               "with XTC preactivated. All results remain unverified unless an "
               "independent verifier passes.")
        env_rows = []
        for (task, model, trial), pair in sorted(pairs.items()):
            no = pair.get("no_skills")
            warm = pair.get("no_skills_xtc")
            if no is None and warm is None:
                continue
            def duration(entry):
                return entry.get("invocation", {}).get("elapsed_seconds") if entry else None
            def price(entry):
                return entry.get("usage", {}).get("copilot_estimated_usd") if entry else None
            elapsed_delta = None
            cost_delta = None
            if (no and warm and
                not no.get("error") and not warm.get("error")):
                if isinstance(duration(no), (float, int)) and isinstance(duration(warm), (float, int)):
                    elapsed_delta = round(duration(warm) - duration(no), 2)
                if isinstance(price(no), (float, int)) and isinstance(price(warm), (float, int)):
                    cost_delta = round(price(warm) - price(no), 5)
            env_rows.append([task, model, trial, _condition_status(no), _condition_status(warm),
                             elapsed_delta, cost_delta])
        r.table(["Task", "Model", "Trial", "No skills", "No skills + XTC",
                 "Elapsed delta (s)", "Cost delta (USD)"], env_rows)
        r.para("Deltas are preactivated XTC minus ordinary no-skills (negative is "
               "faster/cheaper). They compare completed runs; they do NOT establish "
               "correctness or skill effectiveness without independent verification.")

    r.section("Referee's verdict")
    if not reviews:
        r.para("No blind review was run.")
    else:
        for review in reviews:
            task = str(review.get("task_id"))
            status = review.get("status", "unknown")
            if status != "reviewed" or not isinstance(review.get("review"), dict):
                r.para(f"**{cell(task)}:** review unavailable ({cell(status)}).")
                continue
            result = review["review"]
            r.para(f"**{cell(task)}:** {cell(result.get('review_notes', 'No summary available.'))}")
            mapping = review.get("blind_mapping", {})
            ordered = [f"{alias} ({mapping.get(alias, 'unknown run')})" for alias in result.get("ranked_ids", [])]
            r.para("**Blind ranking:** " + cell(" > ".join(ordered) if ordered else "not provided") +
                   ". Rankings reflect subjective design quality, not independent correctness.")

    r.section("Method at a glance")
    conditions = ", ".join(conf["conditions"].keys())
    r.para("Each model received the same problem and clean starter in separate OpenCode CLI sessions. "
           "Models were accessed **through GitHub Copilot**; worker variants were "
           f"{cell(conditions)}. A separate model reviewed anonymised implementations "
           "after the worker runs and independent build/simulation/hardware verification. "
           "The preactivated-XTC control (when selected) has no skills; only its "
           "toolchain environment differs from the no-skills baseline. "
           "Time, token use, skills invoked and estimated Copilot costs were recorded. "
           "Single trials should not be treated as statistically significant.")
    role_rows = []
    for alias, model in conf["models"]["workers"].items():
        profile = model_profile(conf, model)
        role_rows.append([f"Worker: {alias}", profile.get("display_name", model),
                          profile.get("developer"), profile.get("published_category")])
    reviewer = conf["models"]["reviewer"]
    profile = model_profile(conf, reviewer)
    role_rows.append(["Referee", profile.get("display_name", reviewer),
                      profile.get("developer"), profile.get("published_category")])
    r.table(["Role", "Model", "Developer", "Published tier"], role_rows)
    r.para("Full protocol, task rationale, controls and model provenance are in "
           "``docs/EXPERIMENT_DESIGN.md`` and ``docs/REPORTING.md``; "
           "this run's frozen settings are in ``experiment.json``.")

    r.section("Appendix - detailed evidence and reproducibility")
    r.subsection("Task selection")
    r.table(["Task", "Detail level", "Reason chosen", "Verifier"], [
        [t.get("id"), t.get("specificity"), t.get("rationale"),
         "configured" if t.get("verify_commands") else "not configured"]
        for t in conf["tasks"]])

    r.subsection("Per-run tokens and skill retrieval")
    token_rows = []
    for e in results:
        tokens = e.get("usage", {}).get("tokens") or {}
        token_rows.append([e.get("run_id"), _skill_names(e),
                           tokens.get("input"), tokens.get("cache_read"),
                           tokens.get("cache_write"), tokens.get("output"),
                           tokens.get("reasoning"),
                           e.get("usage", {}).get("copilot_estimated_ai_credits")])
    r.table(["Run", "Skills loaded", "Input", "Cache read", "Cache write", "Output",
             "Reasoning (subset)", "Est. credits"], token_rows)
    r.para("Reasoning tokens, when reported, are a subset of output tokens, not an extra billable sum.")

    r.subsection("Model selection and usage provenance")
    r.para("All model IDs are requested through GitHub Copilot in OpenCode. "
           "Provider-published tiers and expected strengths are descriptive, not measured quality.")
    for role, model in [(f"Worker {alias}", model) for alias, model in conf["models"]["workers"].items()] + [
        ("Referee", reviewer)]:
        profile = model_profile(conf, model)
        r.para(f"**{cell(role)}**: {literal(model)}; developer {cell(profile.get('developer'))}; "
               f"tier {cell(profile.get('published_category'))}; "
               f"expected strengths: {cell(profile.get('quality_expectation'))}; "
               f"why selected: {cell(profile.get('selection_rationale'))}. "
               f"Source: {literal(profile.get('source', 'unrecorded'))}.")
    r.table(["Run", "Requested model", "Observed model(s)", "Token data source", "Warnings"], [
        [e.get("run_id"), e.get("model"),
         ", ".join(e.get("observed_models") or []) or "not verified",
         e.get("usage", {}).get("usage_source"), "; ".join(e.get("warnings") or []) or "none"]
        for e in results])

    r.subsection("Independent verifier observations")
    for e in results:
        v = e.get("validation", {})
        obs = v.get("observations") if isinstance(v, dict) else None
        metrics = obs.get("metrics", {}) if isinstance(obs, dict) else {}
        r.para(f"**{literal(e.get('run_id', '?'))}**: {cell(v.get('status'))}. "
               f"Threads/tile: {literal(as_json(metrics.get('threads_per_tile')))}; "
               f"libraries: {literal(as_json(metrics.get('libraries')))}; "
               f"RAM/tile: {literal(as_json(metrics.get('ram_bytes_per_tile')))}; "
               f"evidence: {literal(as_json(metrics.get('evidence')))}.")
        if e.get("design_excerpt_self_reported"):
            r.para("Worker's own description (unverified):")
            r.code(e["design_excerpt_self_reported"])

    r.subsection("Blind review detail")
    for review in reviews:
        data = review.get("review")
        if not isinstance(data, dict):
            continue
        mapping = review.get("blind_mapping", {})
        for candidate in data.get("candidates", []):
            if not isinstance(candidate, dict):
                continue
            alias = candidate.get("id", "unknown")
            r.para(f"**{cell(review.get('task_id'))} - {cell(alias)} -> "
                   f"{literal(mapping.get(alias, 'unknown'))}**")
            r.bullets([
                "Summary: " + cell(candidate.get("summary", "not assessed")),
                "Ratings: " + literal(as_json(candidate.get("ratings", {}))),
                "Major decisions: " + cell(", ".join(candidate.get("major_decisions") or [])),
                "Risks: " + cell(", ".join(candidate.get("risks") or [])),
            ])

    r.subsection("Repeatability and verified finish times")
    repeats: dict[tuple, list[dict]] = {}
    for e in results:
        repeats.setdefault((e.get("task_id"), e.get("model_label"), e.get("condition")), []).append(e)
    r.table(["Task", "Model", "Condition", "Passes", "Trials"], [
        [task, model, condition, sum(_condition_status(e) == "passed" for e in entries), len(entries)]
        for (task, model, condition), entries in sorted(repeats.items())])
    finishers = [e for e in results if _condition_status(e) == "passed" and
                 isinstance(e.get("time_to_verified_success_s"), (int, float))]
    finishers.sort(key=lambda e: e["time_to_verified_success_s"])
    r.para("**Travelator - independently verified finish times** (worker plus verifier):")
    r.table(["Run", "Time (s)"], [[e.get("run_id"), e.get("time_to_verified_success_s")]
                                  for e in finishers])

    r.subsection("Provenance, limitations and next steps")
    environment = manifest.get("environment", {}) if manifest else {}
    r.bullets([
        "Full task prompts and SHA-256 hashes, run order, configuration snapshot and "
        "environment details are in ``experiment.json``; worker transcripts, logs and "
        "source snapshots live under ``runs/``. "
        f"Configuration SHA-256: {cell(manifest.get('configuration_sha256', 'unrecorded') if manifest else 'unrecorded')}.",
        f"OpenCode: {cell(environment.get('opencode_version') or 'not verified')}; "
        f"XTC in original shell: {cell(environment.get('compiler_version') or 'not activated')}; "
        f"XTC in warm control: {cell(environment.get('xtc_preactivated_version') or 'not configured')}; "
        f"xskills commit: {cell(environment.get('xskills_revision') or 'unrecorded')}.",
        "Run directories are separated, but filesystem isolation is not enforced by the OS. "
        "Use containers/VMs for a rigorous isolation boundary.",
        "Estimated GitHub Copilot costs are not account invoices; billed totals may differ "
        "due to allowances, cache effects or rate changes.",
        "For future MCP trials, add a separate MCP condition and rerun the identical "
        "tasks and verifiers with pinned skill and server revisions.",
    ])
    r.para("*Gladiators, you will go on my second whistle.*")
    path = root / "REPORT.rst"
    path.write_text(r.render(), encoding="utf-8")
    return path
