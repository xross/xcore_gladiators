"""Management-facing RST report; granular reproducibility data stays in the appendix.

This module formats immutable experiment metadata, trusted verifier results,
agent telemetry and blinded model reviews. It never calls a model or creates
verification evidence.
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path
from statistics import median
from textwrap import shorten
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
    invocation = entry.get("invocation", {})
    if (entry.get("error") or entry.get("requested_model_mismatch") or
            invocation.get("timed_out") or invocation.get("exit_code", 0) != 0):
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


CONDITIONS = {"native": "Skills", "no_skills": "No skills", "no_skills_xtc": "No skills + XTC"}


def _model(entry: dict) -> str:
    label = str(entry.get("model_label") or "Unknown model")
    return label.capitalize() if label.islower() else label


def _label(entry: dict) -> str:
    return f"{_model(entry)} / {CONDITIONS.get(entry.get('condition'), entry.get('condition', 'Unknown'))}"


def _number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _duration(seconds: object) -> str:
    if not _number(seconds) or seconds < 0:
        return "not recorded"
    minutes, seconds = divmod(round(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {seconds:02d}s"
    return f"{minutes}m {seconds:02d}s" if minutes else f"{seconds}s"


def _credits(entry: dict) -> float | None:
    value = entry.get("usage", {}).get("copilot_estimated_ai_credits")
    return value if _number(value) and value >= 0 else None


def _completed(entry: dict | None) -> bool:
    return bool(entry and _condition_status(entry) not in ("error", "missing") and
                entry.get("invocation", {}).get("exit_code") == 0)


def _worker_evidence(root: Path, entry: dict) -> str:
    """Describe recorded tool output, never promote agent-run checks to verification.

    Only completed shell-tool results count, not assistant prose or DESIGN.md.
    Recognizers are deliberately conservative; unknown formats stay unclassified.
    """
    path = root / "runs" / str(entry.get("run_id", "")) / "events.jsonl"
    built = simulated = waveform = compiler_missing = False
    if path.is_file():
        with path.open(encoding="utf-8", errors="replace") as stream:
            for line in stream:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(event, dict):
                    continue
                part = event.get("part")
                if not isinstance(part, dict) or part.get("tool") not in ("shell", "bash"):
                    continue
                state = part.get("state")
                if not isinstance(state, dict) or state.get("status") != "completed":
                    continue
                output = str(state.get("output", ""))
                simulated |= bool(re.search(
                    r"C51188: maximum cycles reached|Expected bounded stop after \d+ cycles|"
                    r"Simulated \d+ cycles; expected cycle-limit termination", output))
                compiler_missing |= bool(re.search(
                    r"(?:xcc: (?:No such file or directory|command not found)|xcc: not found)", output))
                # A combined build/test command may include xsim's normal bounded stop.
                build_output = re.sub(r"(?m)^.*C51188: maximum cycles reached.*$", "", output)
                if not re.search(r"(?im)(?:\berror:|\*\*\*|exceeded timeout)", build_output):
                    built |= bool(re.search(r"Built target |Constraints checks PASSED\.", output))
                waveform |= bool(re.search(r"(?im)^PASS:.*(?:UART|frames|square|waveform)", output) or
                                 (re.search(r"\b\d+ passed\b", output) and
                                  re.search(r"(?m)^UART:.*\bframes\b", output) and
                                  re.search(r"(?m)^Square:", output)))
    if built and simulated and waveform:
        return "Build + waveform checks logged"
    if built:
        return "Build logged; no waveform pass logged"
    if compiler_missing:
        return "Compiler not found in worker PATH"
    return "No build/waveform pass identified"


def _ranking(review: dict | None, entries: dict[str, dict]) -> list[tuple[str, dict, dict]]:
    if not review or review.get("status") != "reviewed" or not isinstance(review.get("review"), dict):
        return []
    data = review["review"]
    candidates = {c.get("id"): c for c in data.get("candidates", []) if isinstance(c, dict)}
    mapping = review.get("blind_mapping", {})
    return [(alias, entries.get(mapping.get(alias), {}), candidates.get(alias, {}))
            for alias in data.get("ranked_ids", [])]


def _review_text(text: str, review: dict, entries: dict[str, dict]) -> str:
    """Resolve anonymous references for readers; preserve original prose in appendix."""
    mapping = review.get("blind_mapping", {})
    return re.sub(r"\bcandidate_\d+\b", lambda m: _label(entries[mapping[m[0]]])
                  if mapping.get(m[0]) in entries else "another submission", text)


def _change(value: float, baseline: float, positive: str, negative: str) -> str:
    if baseline <= 0:
        return "percentage unavailable: zero baseline"
    delta = (value - baseline) / baseline * 100
    if abs(delta) < 0.5:
        return "about the same"
    return f"{abs(delta):.0f}% {positive if delta < 0 else negative}"


def _comparisons(r: Rst, pairs: dict, ranks: dict[str, int], *, warm: bool = False) -> None:
    """Compare matched trials, using medians rather than cherry-picking repeats."""
    condition = "no_skills_xtc" if warm else "native"
    groups: dict[tuple[str, str], list[dict]] = {}
    for (task, model, _), pair in pairs.items():
        if condition in pair or "no_skills" in pair:
            groups.setdefault((task, model), []).append(pair)
    if not groups:
        r.para("No matching conditions were recorded.")
        return
    tasks = {task for task, _ in groups}
    for (task, model), trials in sorted(groups.items()):
        title = _model({"model_label": model}) + (f" ({task})" if len(tasks) > 1 else "")
        valid = [p for p in trials if _completed(p.get(condition)) and _completed(p.get("no_skills"))]
        if not valid:
            r.bullets([f"**{cell(title)}:** no completed matched pair; missing or errored runs cannot be compared."])
            continue
        subject = "preactivated XTC" if warm else "skills"
        statements = []
        times = [(p[condition].get("invocation", {}).get("elapsed_seconds"),
                  p["no_skills"].get("invocation", {}).get("elapsed_seconds")) for p in valid]
        times = [(a, b) for a, b in times if _number(a) and _number(b) and a >= 0 and b >= 0]
        if times:
            a, b = median(a for a, _ in times), median(b for _, b in times)
            scope = f"Median across {len(times)} paired trials: " if len(trials) > 1 else ""
            statements.append(f"{scope}{subject} took {_duration(a)} versus {_duration(b)} without "
                              f"{'preactivation' if warm else 'skills'} ({_change(a, b, 'faster', 'slower')}).")
        costs = [(_credits(p[condition]), _credits(p["no_skills"])) for p in valid]
        costs = [(a, b) for a, b in costs if a is not None and b is not None]
        if costs:
            a, b = median(a for a, _ in costs), median(b for _, b in costs)
            scope = f"Median across {len(costs)} paired trials: " if len(trials) > 1 else ""
            statements.append(f"{scope}estimated usage was {a:.2f} versus {b:.2f} AI Credits "
                              f"({_change(a, b, 'lower', 'higher')}).")
        ranked = [(ranks[p[condition]["run_id"]], ranks[p["no_skills"]["run_id"]])
                  for p in valid if p[condition].get("run_id") in ranks and p["no_skills"].get("run_id") in ranks]
        if len(ranked) == 1:
            a, b = ranked[0]
            statements.append(f"Blind design rank: {a} with {subject}, {b} without.")
        elif ranked:
            preferred = sum(a < b for a, b in ranked)
            statements.append(f"Blind review preferred {subject} in {preferred}/{len(ranked)} ranked pairs.")
        verified = [p for p in valid if all(_condition_status(p[c]) in ("passed", "failed")
                                          for c in (condition, "no_skills"))]
        if verified:
            statements.append(f"Independent passes in {len(verified)} verified pairs: "
                              f"{sum(_condition_status(p[condition]) == 'passed' for p in verified)} "
                              f"with {subject}, {sum(_condition_status(p['no_skills']) == 'passed' for p in verified)} without.")
        if len(valid) < len(trials):
            statements.append(f"{len(trials) - len(valid)} incomplete/error pairs excluded.")
        r.bullets([f"**{cell(title)}:** " + " ".join(statements or ["Timing and usage were not recorded."])])


def build_report(root: Path, conf: dict, manifest: dict | None, results: list[dict],
                 reviews: list[dict], now_iso: Callable[[], str],
                 model_profile: Callable[[dict, str], dict]) -> Path:
    experiment = conf["experiment"]
    planned = manifest.get("run_order", []) if manifest else []
    started = manifest.get("started_utc", "unrecorded") if manifest else "unrecorded"
    missing = max(len(planned) - len(results), 0)
    errors = [e for e in results if _condition_status(e) == "error"]
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
    entries = {e["run_id"]: e for e in results}
    evidence = {run_id: _worker_evidence(root, e) for run_id, e in entries.items()}
    ranks = {}
    for review in reviews:
        for rank, (_, entry, _) in enumerate(_ranking(review, entries), 1):
            if entry:
                ranks[entry["run_id"]] = rank

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
               "Correctness remains unproven; time, usage and blind design preferences can still be compared descriptively.")
    else:
        r.para(f"Across {len(complete_pairs)} independently verified native/no-skills pairs, "
               f"skills improved the pass outcome in {native_improved}, worsened it in "
               f"{native_worsened}, and made no difference to pass/fail in {native_unchanged}. "
               "These are descriptive results, not statistical evidence.")
    r.para(f"**{len(results)} of {len(planned)} scheduled workers recorded.**" if planned else
           f"**{len(results)} worker runs recorded.**")
    task_ids = list(dict.fromkeys(str(e.get("task_id")) for e in results))
    for task in task_ids:
        task_entries = [e for e in results if str(e.get("task_id")) == task]
        review = _review_for_task(reviews, task)
        ranking = _ranking(review, entries)
        findings = []
        prefix = f"{cell(task)}: " if len(task_ids) > 1 else ""
        if ranking and ranking[0][1]:
            findings.append(prefix + f"**Best blind design rank:** {cell(_label(ranking[0][1]))}. "
                            "This is the reviewer's source-and-test assessment, not a correctness pass.")
        eligible = [e for e in task_entries if _completed(e) and
                    (_condition_status(e) == "passed" or evidence[e["run_id"]] == "Build + waveform checks logged") and
                    _condition_status(e) != "failed"]
        timed = [e for e in eligible if _number(e.get("invocation", {}).get("elapsed_seconds")) and
                 e["invocation"]["elapsed_seconds"] >= 0]
        if timed:
            fastest = min(timed, key=lambda e: e["invocation"]["elapsed_seconds"])
            findings.append(prefix + f"**Lowest recorded worker time among submissions with build/check evidence:** "
                            f"{cell(_label(fastest))}, {_duration(fastest['invocation']['elapsed_seconds'])}.")
        priced = [e for e in eligible if _credits(e) is not None]
        if priced:
            cheapest = min(priced, key=lambda e: _credits(e))
            findings.append(prefix + f"**Lowest estimated worker usage among those submissions:** "
                            f"{cell(_label(cheapest))}, {_credits(cheapest):.2f} AI Credits.")
        no_compiler = [e for e in task_entries if evidence[e["run_id"]] == "Compiler not found in worker PATH"]
        for e in no_compiler:
            findings.append(prefix + f"**Environment hurdle:** {cell(_label(e))} logged a compiler-not-found "
                            "failure. Its short runtime is not evidence of a working implementation.")
        if findings:
            r.bullets(findings)
    r.para(f"**Independent correctness:** {passed} passed; {failed} failed; {unverified} unverified; "
           f"{len(errors)} run errors. **Estimated total usage:** ${total_cost:.4f}, including the reviewer. "
           "Worker and reviewer costs are separated in the appendix.")
    if errors:
        r.bullets([f"**Run error:** {cell(_label(e))}. {cell(e.get('error') or e.get('validation', {}).get('reason') or 'Worker failed or served model did not match.')}"
                   for e in errors])

    r.section("Results at a glance")
    r.para("Skills = native XMOS skills, XTC not preactivated. No skills = neither skills nor preactivation. "
           "No skills + XTC = toolchain preactivated, no skills. Cold workers can discover and activate XTC themselves.")
    for task in task_ids:
        r.subsection(task)
        task_entries = [e for e in results if str(e.get("task_id")) == task]
        multiple_trials = len({e.get("repeat") for e in task_entries}) > 1
        headers = ["Model", "Condition"] + (["Trial"] if multiple_trials else [])
        headers += ["Worker time", "Est. credits", "Worker log evidence", "Blind rank"]
        rows = []
        order = {"no_skills": 0, "native": 1, "no_skills_xtc": 2}
        for e in sorted(task_entries, key=lambda e: (str(e.get("model_label")), str(e.get("repeat")),
                                                    order.get(e.get("condition"), 3))):
            row = [_model(e), CONDITIONS.get(e.get("condition"), e.get("condition"))]
            if multiple_trials:
                row.append(e.get("repeat"))
            worker_evidence = {
                "Build + waveform checks logged": "Build + sim checks",
                "Build logged; no waveform pass logged": "Build only",
                "Compiler not found in worker PATH": "No compiler on PATH",
            }.get(evidence[e["run_id"]], "Not identified") if _completed(e) else "Worker error / incomplete"
            row += [_duration(e.get("invocation", {}).get("elapsed_seconds")),
                    f"{_credits(e):.2f}" if _credits(e) is not None else "not recorded",
                    worker_evidence, ranks.get(e["run_id"], "not ranked")]
            rows.append(row)
        r.table(headers, rows)
        statuses = {_condition_status(e) for e in task_entries}
        if statuses == {"unverified"}:
            r.para("**All submissions above are independently UNVERIFIED.** Logged checks were run by the "
                   "worker, not a trusted verifier.")
        else:
            r.bullets([f"Independent check for {cell(_label(e))}, trial {cell(e.get('repeat'))}: "
                       f"**{cell(_condition_status(e))}**." for e in task_entries])
    r.para("Times are worker wall time throughout, not time to verified success. Log evidence describes "
           "recognizable completed shell-tool output, not a new verification or a reading of worker claims. "
           "Unrecognized or missing logs are not proof that a build failed. Credits are usage estimates, not invoices.")

    r.section("Did skills help?")
    if incomplete:
        r.para("**Comparison invalid for the full experiment:** some runs are missing or errored. "
               "Only completed matched pairs are described below.")
    _comparisons(r, pairs, ranks)
    r.para("These are effort and design comparisons, not proof that skills improved correctness. "
           "A faster worker can simply have stopped before compiling; read the execution evidence alongside its time.")

    if "no_skills_xtc" in conf["conditions"]:
        r.section("Did preactivating XTC help?")
        _comparisons(r, pairs, ranks, warm=True)
        r.para("This compares no-skills workers with and without a prepared toolchain. "
               "It separates the environment hurdle from skill guidance, but does not by itself establish correctness.")
        warm_pairs = [p for p in pairs.values() if _completed(p.get("native")) and _completed(p.get("no_skills_xtc"))]
        if warm_pairs:
            r.para("**Skills versus a prepared environment:**")
            for task, model in sorted({(str(p["native"].get("task_id")), str(p["native"].get("model_label")))
                                       for p in warm_pairs}):
                matched = [p for p in warm_pairs if str(p["native"].get("model_label")) == model and
                           str(p["native"].get("task_id")) == task]
                timed = [p for p in matched if all(_number(p[c].get("invocation", {}).get("elapsed_seconds"))
                                                 and p[c]["invocation"]["elapsed_seconds"] >= 0
                                                 for c in ("native", "no_skills_xtc"))]
                if timed:
                    a = median(p["no_skills_xtc"]["invocation"]["elapsed_seconds"] for p in timed)
                    b = median(p["native"]["invocation"]["elapsed_seconds"] for p in timed)
                    label = _model({"model_label": model}) + (f" ({task})" if len(task_ids) > 1 else "")
                    scope = f"; paired medians across {len(timed)} trials" if len(timed) > 1 else ""
                    r.bullets([f"**{cell(label)}:** no skills + XTC took "
                               f"{_duration(a)} versus {_duration(b)} with skills "
                               f"({_change(a, b, 'faster', 'slower')}{scope})."])
            r.para("This is not an isolated skills effect: both skills and initial toolchain state change. "
                   "A skills + preactivated XTC condition is needed to separate them.")

    r.section("Blind design ranking")
    r.para("The reviewer assessed anonymised source and supplied tests without knowing the models or "
           "conditions. Lower rank is better; it is not an independent correctness score.")
    if not reviews:
        r.para("No blind review was run.")
    for review in reviews:
        r.subsection(str(review.get("task_id")))
        ranking = _ranking(review, entries)
        if not ranking:
            r.para(f"Ranking unavailable ({cell(review.get('status', 'unknown'))}).")
            continue
        multiple_trials = len({e.get("repeat") for _, e, _ in ranking if e}) > 1
        headers = ["Rank", "Model", "Condition"] + (["Trial"] if multiple_trials else []) + ["Reviewer's takeaway"]
        rows = []
        for rank, (_, e, candidate) in enumerate(ranking, 1):
            summary = _review_text(str(candidate.get("summary") or "No assessment supplied."), review, entries)
            summary = re.sub(r"^(?:First|Second|Third|Fourth|Fifth|Sixth|Last):\s*", "", summary)
            takeaway = shorten(re.split(r"(?<=[.!?])\s+", summary)[0], width=230, placeholder="...")
            row = [rank, _model(e), CONDITIONS.get(e.get("condition"), e.get("condition", "Unknown submission"))]
            if multiple_trials:
                row.append(e.get("repeat"))
            rows.append(row + [takeaway])
        r.table(headers, rows)
    r.para("Full reviewer reasoning, risks, scores and anonymous-to-run mappings are in the appendix.")

    r.section("What we can and cannot conclude")
    r.bullets([
        "Worker logs show what the agent attempted and recorded. Only an independent verifier can establish a correctness pass; "
        "a model review or successful-looking tool output cannot substitute for it.",
        "Time/cost savings and design preferences are different outcomes. More decoded frames or more tests alone do not establish a better design.",
        "Single trials are exploratory. Repeated matched trials and the same independent verifier are needed before attributing differences to skills.",
        "Without a skills + preactivated XTC condition, skills cannot be isolated from environment setup in the warm-versus-skills comparison.",
        "Estimated credits may use incomplete event telemetry and do not establish actual billed charges. "
        "Model identity, global settings and host isolation warnings are retained in the appendix.",
    ])
    r.para("**Next experiment:** add a skills + preactivated XTC condition, use identical independent build/waveform "
           "checks for every condition, and repeat each matched trial at least three times.")

    r.section("Appendix - detailed evidence and reproducibility")

    r.subsection("Method at a glance")
    conditions = ", ".join(conf["conditions"].keys())
    r.para("Each model received the same problem and clean starter in separate OpenCode CLI sessions. "
           "Models were accessed **through GitHub Copilot**; worker variants were "
           f"{cell(conditions)}. A separate model reviewed anonymised implementations "
            "after the worker runs and any configured independent verification. "
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

    r.subsection("Recorded times, costs and verification")
    r.para(f"Estimated worker usage: ${worker_cost:.6f}; reviewer usage: ${reviewer_cost:.6f}; "
           f"total: ${total_cost:.6f}. These are estimates, not billed charges.")
    r.table(["Run", "Independent check", "Worker seconds", "Verified finish seconds", "Est. USD"], [
        [e.get("run_id"), _condition_status(e), e.get("invocation", {}).get("elapsed_seconds"),
         e.get("time_to_verified_success_s"), e.get("usage", {}).get("copilot_estimated_usd")]
        for e in results])
    r.bullets([f"{literal(e.get('run_id'))}: {cell(e.get('error') or e.get('validation', {}).get('reason') or 'Worker failed or served model did not match.')}"
               for e in errors])
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
        r.para(f"**{cell(review.get('task_id'))} - full reviewer notes:**")
        r.para(cell(data.get("review_notes", "No summary supplied.")))
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
