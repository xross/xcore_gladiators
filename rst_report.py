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

    def detail(self, title: str) -> None:
        self.lines += [title, "^" * len(title), ""]

    def para(self, text: str) -> None:
        self.lines += [text.strip(), ""]

    def bullets(self, items: list[str]) -> None:
        for item in items:
            self.lines.append("- " + item.strip())
        self.lines.append("")

    def table(self, headers: list[str], rows: list[list[object]], widths: list[int] | None = None) -> None:
        if widths is None:
            narrow = {"Rank", "Trial", "Trials", "Passes", "Est. credits", "Est. USD", "Rating (0-5)"}
            wide = {"Reviewer's takeaway", "Warnings", "Reason chosen", "Skills loaded", "Execution evidence"}
            widths = [6 if h in narrow else 35 if h in wide else 18 for h in headers]
        if len(widths) != len(headers) or any(w <= 0 for w in widths):
            raise ValueError("Invalid RST column widths")
        self.lines += [".. list-table::", "   :header-rows: 1", "   :widths: " + " ".join(map(str, widths)), ""]
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


def _language(root: Path, entry: dict) -> str:
    """Classify submitted firmware sources, not libraries, prose or host tests."""
    source = root / "runs" / entry["run_id"] / "source" / "src"
    if not source.is_dir() or source.is_symlink():
        return "Not recorded"
    kinds = {".xc": "XC", ".c": "C", ".cc": "C++", ".cpp": "C++", ".cxx": "C++", ".S": "Assembly"}
    languages = {kinds[p.suffix] for p in source.rglob("*")
                 if p.is_file() and not p.is_symlink() and p.suffix in kinds and
                 p.resolve().is_relative_to(source.resolve())}
    return " + ".join(sorted(languages)) if languages else "Not recorded"


def _skill_usage(root: Path, entry: dict) -> dict:
    """Recorded exposure, not an assumption that enabled skills were consumed."""
    if entry.get("condition") not in ("native", "native_xtc", "native_compare_xtc"):
        return {"label": "Disabled", "missing": False, "c_guidance": False}
    calls = entry.get("skill_calls")
    loads = sum(isinstance(c, dict) and c.get("status") == "completed" and
                "skill" in str(c.get("tool", "")).lower() and
                not str(c.get("tool", "")).lower().endswith("list_skills") for c in calls or [])
    reads = set()
    event_loads = set()
    c_guidance = False
    events_file = root / "runs" / entry["run_id"] / "events.jsonl"
    if events_file.is_file():
        with events_file.open(encoding="utf-8", errors="replace") as stream:
            for line in stream:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                part = event.get("part") if isinstance(event, dict) else None
                state = part.get("state") if isinstance(part, dict) else None
                if not isinstance(state, dict) or state.get("status") != "completed":
                    continue
                tool = str(part.get("tool", "")).lower()
                inputs = state.get("input")
                inputs = inputs if isinstance(inputs, dict) else {}
                output = str(state.get("output", ""))
                consulted = False
                if tool == "skill":
                    event_loads.add(part.get("callID") or inputs.get("id") or inputs.get("name") or str(len(event_loads)))
                    consulted = True
                elif tool == "read":
                    path = str(inputs.get("path") or inputs.get("filePath") or "")
                    if re.search(r"(?:^|/)skills/[^/]+/SKILL\.md$", path) and output and "Permission denied" not in output:
                        reads.add(path)
                        consulted = True
                elif tool in ("shell", "bash"):
                    command = str(inputs.get("command", ""))
                    paths = re.findall(r"[^\s\"']*skills/[^/\s\"']+/SKILL\.md", command)
                    if (paths and re.search(r"\b(?:cat|head|sed)\b", command) and
                            re.search(r"(?m)^#|^name:", output) and "Permission denied" not in output):
                        reads.update(paths)
                        consulted = True
                if consulted:
                    plain = re.sub(r"\s+", " ", output.replace("*", "").replace("`", ""))
                    c_guidance |= "write new code in c using lib_xcore, not xc" in plain.lower()
    loads = max(loads, len(event_loads))
    labels = ([f"{loads} skill load{'s' if loads != 1 else ''}"] if loads else [])
    if reads:
        labels.append(f"{len(reads)} skill-file read{'s' if len(reads) != 1 else ''}")
    known = calls is not None or events_file.is_file()
    return {"label": "; ".join(labels) if labels else "None recorded" if known else "Not recorded",
            "missing": known and not labels, "c_guidance": c_guidance}


def _failure_detail(root: Path, entry: dict) -> str:
    validation = entry.get("validation", {})
    if validation.get("status") != "failed":
        return ""
    output = root / "runs" / entry["run_id"] / "verification"
    evidence = (validation.get("observations") or {}).get("metrics", {}).get("evidence", [])
    for name in evidence:
        if not isinstance(name, str) or not name.endswith("/simulator.log"):
            continue
        path = output / name
        if path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(output.resolve()):
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                if "Unhandled exception:" in line:
                    return shorten(line.strip(), width=160, placeholder="...")
    return shorten(str(validation.get("reason") or "Independent acceptance failed"), width=160, placeholder="...")


def _review_for_task(reviews: list[dict], task_id: str) -> dict | None:
    return next((review for review in reviews if review.get("task_id") == task_id), None)


def _paired(results: list[dict]) -> dict[tuple, dict[str, dict]]:
    pairs: dict[tuple, dict[str, dict]] = {}
    for entry in results:
        key = (str(entry.get("task_id")), str(entry.get("model_label")), entry.get("repeat"))
        pairs.setdefault(key, {})[str(entry.get("condition"))] = entry
    return pairs


CONDITIONS = {"native": "Skills", "native_xtc": "Skills + XTC", "native_compare_xtc": "Comparison skills + XTC", "no_skills": "No skills", "no_skills_xtc": "No skills + XTC"}


def _condition_label(entry: dict) -> str:
    return entry.get("condition_label") or CONDITIONS.get(entry.get("condition"), entry.get("condition", "Unknown submission"))


def _skill_conditions(pair: dict, comparing: bool = False) -> tuple[str, str]:
    return ("native_compare_xtc", "native_xtc") if comparing or "native_compare_xtc" in pair else ("native_xtc", "no_skills_xtc")


def _model(entry: dict) -> str:
    label = str(entry.get("model_label") or "Unknown model")
    return label.capitalize() if label.islower() else label


def _label(entry: dict) -> str:
    return f"{_model(entry)} / {_condition_label(entry)}"


def _task_title(task: str) -> str:
    known = {"specific": "specific request", "everyday": "everyday request", "vague": "vague request"}
    if task.startswith("uart-heartbeat-"):
        suffix = task.removeprefix("uart-heartbeat-")
        return "UART heartbeat — " + known.get(suffix, suffix.replace("-", " "))
    return task.replace("-", " ").replace("_", " ").capitalize()


def _value_text(value: object) -> str:
    if value is None:
        return "not measured"
    if isinstance(value, dict):
        return "; ".join(f"{key}: {_value_text(item)}" for key, item in value.items()) or "not recorded"
    if isinstance(value, list):
        return "; ".join(_value_text(item) for item in value) or "none recorded"
    return str(value)


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


def _validate_results(conf: dict, results: list[dict], reviews: list[dict]) -> None:
    from verification.uart_heartbeat import VERSION, validate_observations
    tasks = {t["id"]: t for t in conf["tasks"]}
    if any(t.get("verifier") != VERSION or t.get("evaluation_class") != "implementation" for t in tasks.values()):
        raise ValueError("Reports require supported independent implementation verification; rerun with the current protocol")
    fingerprints: dict[str, set[str]] = {}
    for entry in results:
        task_id = entry.get("task_id")
        if task_id not in tasks:
            raise ValueError("Result references a task without a configured independent verifier")
        validation = entry.get("validation", {})
        status = validation.get("status")
        if status == "error":
            continue
        if status not in ("passed", "failed"):
            raise ValueError("Reports require independent passed/failed results; unverified archives are not supported")
        observed = validation.get("observations")
        validate_observations(observed)
        if observed["passed"] != (status == "passed"):
            raise ValueError("Independent status contradicts its verification evidence")
        fingerprints.setdefault(task_id, set()).add(observed["protocol"]["verifier_sha256"])
    if any(len(values) > 1 for values in fingerprints.values()):
        raise ValueError("Cannot compare submissions checked by different verifier revisions")
    for review in reviews:
        if review.get("status") == "reviewed" and review.get("evaluation_class") != "implementation":
            raise ValueError("Reports require implementation-only review, not mixed legacy rankings")


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
    return re.sub(r"\bcandidates?_(\d+)\b", lambda m: _label(entries[mapping["candidate_" + m[1]]])
                  if mapping.get("candidate_" + m[1]) in entries else "another submission", text)


def _ranking_reason(candidate: dict, review: dict, entries: dict[str, dict]) -> str:
    explicit = candidate.get("ranking_reason")
    if isinstance(explicit, str) and explicit.strip():
        return shorten(_review_text(explicit, review, entries), width=300, placeholder="...")
    summary = str(candidate.get("summary") or "")
    sentences = re.split(r"(?<=[.!?])\s+", summary)
    reasons = [s for s in sentences if s.strip() and not re.match(r"^(?:PASSED|FAILED)\b", s)]
    ranked = next((s for s in reasons if re.search(r"\branked?\b|\bbecause\b|\badvantage\b|\boutweigh", s, re.I)), None)
    text = ranked or (reasons[0] if reasons else "No separate ranking rationale supplied; see the full review.")
    return shorten(_review_text(text, review, entries), width=300, placeholder="...")


def _change(value: float, baseline: float, positive: str, negative: str) -> str:
    if baseline <= 0:
        return "percentage unavailable: zero baseline"
    delta = (value - baseline) / baseline * 100
    if abs(delta) < 0.5:
        return "about the same"
    return f"{abs(delta):.0f}% {positive if delta < 0 else negative}"


def _comparisons(r: Rst, pairs: dict, ranks: dict[str, int], languages: dict, skill_usage: dict, failures: dict,
                 prefer_comparison: bool = False) -> None:
    """Compare matched trials, using medians rather than cherry-picking repeats."""
    groups: dict[tuple[str, str], list[dict]] = {}
    for (task, model, _), pair in pairs.items():
        if any(c in pair for c in ("native_xtc", "no_skills_xtc", "native_compare_xtc")):
            groups.setdefault((task, model), []).append(pair)
    if not groups:
        r.para("No matching conditions were recorded.")
        return
    tasks = {task for task, _ in groups}
    for (task, model), trials in sorted(groups.items()):
        condition, baseline = _skill_conditions({c: e for pair in trials for c, e in pair.items()}, prefer_comparison)
        title = _model({"model_label": model}) + (f" ({_task_title(task)})" if len(tasks) > 1 else "")
        valid = [p for p in trials if _completed(p.get(condition)) and _completed(p.get(baseline))]
        if not valid:
            r.bullets([f"**{cell(title)}:** no completed matched pair; missing or errored runs cannot be compared."])
            continue
        comparing = condition == "native_compare_xtc"
        subject = "comparison skills" if comparing else "skills"
        statements = []
        if len(valid) == 1:
            enabled, baseline_entry = valid[0][condition], valid[0][baseline]
            def outcome(e):
                language = languages[e["run_id"]]
                return _condition_status(e) + (f" ({language})" if language != "Not recorded" else "")
            statements.append((f"Current skills: {outcome(baseline_entry)}; comparison skills: {outcome(enabled)}.") if comparing else
                              f"No skills: {outcome(baseline_entry)}; skills enabled: {outcome(enabled)}.")
            usage = skill_usage[enabled["run_id"]]
            if usage["missing"]:
                statements.append("FLAG: no skill consultation recorded in the enabled run; "
                                  "this does not demonstrate an effect of reading skill content.")
            elif usage["label"] != "Not recorded":
                statements.append(f"Recorded consultation: {usage['label']}.")
            if failures[enabled["run_id"]]:
                statements.append(f"Failure: {failures[enabled['run_id']]}.")
        times = [(p[condition].get("invocation", {}).get("elapsed_seconds"),
                  p[baseline].get("invocation", {}).get("elapsed_seconds")) for p in valid]
        times = [(a, b) for a, b in times if _number(a) and _number(b) and a >= 0 and b >= 0]
        if times:
            a, b = median(a for a, _ in times), median(b for _, b in times)
            scope = f"Median across {len(times)} paired trials: " if len(trials) > 1 else ""
            comparison_text = "with current skills" if comparing else "without skills"
            statements.append(f"{scope}{'Comparison-skills' if comparing else 'Skills-enabled'} run took {_duration(a)} "
                              f"versus {_duration(b)} {comparison_text} ({_change(a, b, 'faster', 'slower')}).")
        costs = [(_credits(p[condition]), _credits(p[baseline])) for p in valid]
        costs = [(a, b) for a, b in costs if a is not None and b is not None]
        if costs:
            a, b = median(a for a, _ in costs), median(b for _, b in costs)
            scope = f"Median across {len(costs)} paired trials: " if len(trials) > 1 else ""
            statements.append(f"{scope}Estimated usage was {a:.2f} versus {b:.2f} AI Credits "
                              f"({_change(a, b, 'lower', 'higher')}).")
        ranked = [(ranks[p[condition]["run_id"]], ranks[p[baseline]["run_id"]])
                  for p in valid if p[condition].get("run_id") in ranks and p[baseline].get("run_id") in ranks]
        if len(ranked) == 1:
            a, b = ranked[0]
            statements.append(f"Blind design rank: {a} with {subject}, {b} " + ("with current skills." if comparing else "without."))
        elif ranked:
            preferred = sum(a < b for a, b in ranked)
            statements.append(f"Blind review preferred {subject} in {preferred}/{len(ranked)} ranked pairs.")
        verified = [p for p in valid if all(_condition_status(p[c]) in ("passed", "failed")
                                          for c in (condition, baseline))]
        if verified and len(valid) > 1:
            statements.append(f"Independent passes in {len(verified)} verified pairs: "
                              f"{sum(_condition_status(p[condition]) == 'passed' for p in verified)} "
                               f"with {subject}, {sum(_condition_status(p[baseline]) == 'passed' for p in verified)} " +
                               ("with current skills." if comparing else "without."))
        if len(valid) < len(trials):
            statements.append(f"{len(trials) - len(valid)} incomplete/error pairs excluded.")
        r.subsection(title)
        r.bullets([cell(s) for s in statements or ["Timing and usage were not recorded."]])


def build_report(root: Path, conf: dict, manifest: dict | None, results: list[dict],
                 reviews: list[dict], now_iso: Callable[[], str],
                 model_profile: Callable[[dict, str], dict]) -> Path:
    experiment = conf["experiment"]
    _validate_results(conf, results, reviews)
    planned = manifest.get("run_order", []) if manifest else []
    started = manifest.get("started_utc", "unrecorded") if manifest else "unrecorded"
    missing = max(len(planned) - len(results), 0)
    errors = [e for e in results if _condition_status(e) == "error"]
    incomplete = bool(errors or missing)
    passed = sum(_condition_status(e) == "passed" for e in results)
    failed = sum(_condition_status(e) == "failed" for e in results)
    comparing_skills = "native_compare_xtc" in conf["conditions"]
    pairs = _paired(results)
    complete_pairs = [(key, pair) for key, pair in pairs.items()
                       if all(_condition_status(pair.get(c)) in ("passed", "failed") for c in _skill_conditions(pair, comparing_skills))]
    worker_cost = sum((e.get("usage", {}).get("copilot_estimated_usd") or 0) for e in results)
    reviewer_cost = sum((e.get("usage", {}).get("copilot_estimated_usd") or 0) for e in reviews)
    total_cost = worker_cost + reviewer_cost
    reused_entries = [e for e in results if e.get("reused_from")]
    reused_cost = sum(e.get("usage", {}).get("copilot_estimated_usd") or 0 for e in reused_entries)
    entries = {e["run_id"]: e for e in results}
    languages = {run_id: _language(root, e) for run_id, e in entries.items()}
    skill_usage = {run_id: _skill_usage(root, e) for run_id, e in entries.items()}
    failures = {run_id: _failure_detail(root, e) for run_id, e in entries.items()}
    ranks = {}
    for review in reviews:
        for rank, (_, entry, _) in enumerate(_ranking(review, entries), 1):
            if entry:
                ranks[entry["run_id"]] = rank

    r = Rst()
    r.title("Gladiators - AI Coding-Agent Benchmark")
    r.para(f"**Experiment:** {cell(experiment.get('name', root.name))} | "
           f"**Started (UTC):** {cell(started)} | **Report:** {now_iso()}")

    # Results first: nothing in the executive portion requires reading methodology.
    r.section("Executive summary")
    if incomplete:
        r.para(("**INCOMPLETE EXPERIMENT — NO VALID SKILLS-FOLDER COMPARISON.** " if comparing_skills else
                "**INCOMPLETE EXPERIMENT — NO VALID SKILLS-VERSUS-NO-SKILLS CONCLUSION.** ") +
               "Some candidates failed to run or are missing. Correct the errors and repeat the paired trial.")
    elif not complete_pairs:
        r.para("**No complete matched pass/fail pair is available yet.** "
               "Only independently checked submissions are reported; execution errors remain incomplete.")
    else:
        r.para(f"**{passed} of {len(results)} submissions passed the same independent acceptance test.** "
               f"{failed} failed; {len(errors)} had execution errors. "
               "The paired results below are observations, not evidence that skills caused the differences.")
    r.para(f"**{len(results)} of {len(planned)} scheduled workers recorded.**" if planned else
           f"**{len(results)} worker runs recorded.**")
    task_ids = list(dict.fromkeys(str(e.get("task_id")) for e in results))
    r.para(f"**Matrix:** {len(conf['tasks'])} task(s), {len(conf['models']['workers'])} models, "
           f"{len(conf['conditions'])} conditions; {experiment.get('repeats', 1)} repeat(s) per combination.")
    if reused_entries:
        r.para(f"**Reuse:** {len(reused_entries)} previous worker results and {len(results) - len(reused_entries)} "
               "new worker results. Reused results are not fresh repetitions; original time/usage values are retained. "
               "Original independent verification evidence is copied, not rerun. Old ranks are not reused; "
               "any combined ranking comes from a fresh blind review.")
    for task in task_ids:
        task_entries = [e for e in results if str(e.get("task_id")) == task]
        review = _review_for_task(reviews, task)
        ranking = _ranking(review, entries)
        findings = []
        prefix = f"{cell(_task_title(task))}: " if len(task_ids) > 1 else ""
        if ranking and ranking[0][1]:
            findings.append(prefix + f"**Best blind design rank:** {cell(_label(ranking[0][1]))}. "
                            "Design preference is separate from the independent correctness result.")
        eligible = [e for e in task_entries if _completed(e) and _condition_status(e) == "passed"]
        timed = [e for e in eligible if _number(e.get("invocation", {}).get("elapsed_seconds")) and
                 e["invocation"]["elapsed_seconds"] >= 0]
        if timed:
            fastest = min(timed, key=lambda e: e["invocation"]["elapsed_seconds"])
            findings.append(prefix + f"**Lowest worker time among independently passing submissions:** "
                            f"{cell(_label(fastest))}, {_duration(fastest['invocation']['elapsed_seconds'])}.")
        priced = [e for e in eligible if _credits(e) is not None]
        if priced:
            cheapest = min(priced, key=lambda e: _credits(e))
            findings.append(prefix + f"**Lowest estimated worker usage among those submissions:** "
                            f"{cell(_label(cheapest))}, {_credits(cheapest):.2f} AI Credits.")
        if findings:
            r.bullets(findings)
    flags = [f"**Skill-use flag — {cell(_label(e))}:** skills enabled, but no consultation recorded. "
             "Do not interpret this as demonstrated use of the skill content."
             for e in results if skill_usage[e["run_id"]]["missing"]]
    if flags:
        r.bullets(flags)
    failure_notes = [f"**Failed submission — {cell(_label(e))} ({cell(languages[e['run_id']])}):** "
                     + cell(failures[e["run_id"]]) for e in results if failures[e["run_id"]]]
    if failure_notes:
        r.bullets(failure_notes)
    r.para(f"**Independent correctness:** {passed} passed; {failed} failed; "
           f"{len(errors)} run errors. **Estimated {'new ' if reused_entries else 'total '}usage:** "
           f"${total_cost - reused_cost:.4f}, including the reviewer" +
           (f"; ${reused_cost:.4f} historical worker usage was reused, not incurred again. " if reused_entries else ". ") +
           "Worker and reviewer costs are separated in the appendix.")
    if errors:
        r.bullets([f"**Run error:** {cell(_label(e))}. {cell(e.get('error') or e.get('validation', {}).get('reason') or 'Worker failed or served model did not match.')}"
                   for e in errors])

    r.section("Results at a glance")
    r.para(("Current skills, comparison skills and the no-skills reference all start with the toolchain ready. "
            "The primary comparison is between the two skills folders. " if comparing_skills else
            "Skills + XTC and No skills + XTC both start with the toolchain ready; only skill availability differs. ") +
           ("Every submission was independently built and checked; reused submissions retain their original evidence: "
            if reused_entries else "Every submission is freshly built and checked by the same independent verifier: ") + "1 ms startup, "
           "then 25 ms of concurrent UART and square-wave observation.")
    for task in task_ids:
        r.subsection(_task_title(task))
        task_entries = [e for e in results if str(e.get("task_id")) == task]
        multiple_trials = len({e.get("repeat") for e in task_entries}) > 1
        headers = ["Rank", "Model", "Condition"] + (["Trial"] if multiple_trials else [])
        headers += ["Language", "Worker time", "Est. credits", "Independent check"] + (["Origin"] if reused_entries else [])
        rows = []
        order = {"no_skills": 0, "native": 1, "no_skills_xtc": 2, "native_xtc": 3}
        for e in sorted(task_entries, key=lambda e: (ranks.get(e["run_id"], float("inf")), str(e.get("model_label")), str(e.get("repeat")),
                                                     order.get(e.get("condition"), 3))):
            row = [ranks.get(e["run_id"], "not ranked"), _model(e), _condition_label(e)]
            if multiple_trials:
                row.append(e.get("repeat"))
            outcome = _condition_status(e)
            row += [languages[e["run_id"]], _duration(e.get("invocation", {}).get("elapsed_seconds")),
                    f"{_credits(e):.2f}" if _credits(e) is not None else "not recorded",
                     outcome.capitalize()]
            if reused_entries:
                row.append("Reused" if e.get("reused_from") else "New")
            rows.append(row)
        r.table(headers, rows, [6, 9, 20] + ([6] if multiple_trials else []) + [8, 14, 12, 19] + ([10] if reused_entries else []))
    r.para("Times are worker wall time throughout. Verifier duration and worker-plus-verifier finish time "
           "are recorded separately. Candidate-authored tests and claims do not determine pass/fail. "
           "Credits are usage estimates, not invoices.")

    r.section("Language choices and skill consultation")
    for task in task_ids:
        if len(task_ids) > 1:
            r.subsection(_task_title(task))
        task_entries = [e for e in results if str(e.get("task_id")) == task]
        categories = ["XC", "C"] + sorted({languages[e["run_id"]] for e in task_entries} - {"XC", "C"})
        rows = []
        conditions = sorted({e.get("condition") for e in task_entries},
                            key=lambda c: ({"no_skills_xtc": 0, "native_xtc": 1, "native_compare_xtc": 2}.get(c, 3), str(c)))
        for condition in conditions:
            group = [e for e in task_entries if e.get("condition") == condition]
            counts = [sum(languages[e["run_id"]] == language for e in group) for language in categories]
            def percentage(count):
                value = f"{100 * count / len(group):.1f}".removesuffix(".0")
                return f"{value}% ({count}/{len(group)})"
            rows.append([_condition_label(group[0])] + [percentage(n) for n in counts])
        r.table(["Condition", *categories], rows, [40] + [60 // len(categories)] * len(categories))
        r.para("Percentages count submissions within each condition, including failed submissions, not lines of code. "
               "Mixed-language and missing-source submissions are separate categories when present. "
               "Host tests and preinstalled libraries are excluded. There is no combined overall percentage.")
        enabled = [e for e in task_entries if e.get("condition") in ("native", "native_xtc", "native_compare_xtc")]
        multiple_trials = len({e.get("repeat") for e in enabled}) > 1
        r.table(["Skills-enabled model"] + (["Condition"] if comparing_skills else []) + (["Trial"] if multiple_trials else []) + ["Recorded consultation"], [
            [_model(e)] + ([_condition_label(e)] if comparing_skills else []) + ([e.get("repeat")] if multiple_trials else []) +
            [skill_usage[e["run_id"]]["label"] + (" — FLAG" if skill_usage[e["run_id"]]["missing"] else "")]
            for e in sorted(enabled, key=lambda e: (str(e.get("model_label")), str(e.get("repeat"))))],
            [30] + ([35] if comparing_skills else []) + ([10] if multiple_trials else []) + [70])
    r.para("Consultation is based on successful recorded skill loads and explicit skill-file reads. "
           "Enabled does not mean consulted; missing telemetry is shown as not recorded rather than assumed to be zero.")
    shifts = []
    for (task, model, trial), pair in sorted(pairs.items()):
        condition, baseline = _skill_conditions(pair, comparing_skills)
        enabled, no = pair.get(condition), pair.get(baseline)
        if not _completed(enabled) or not _completed(no):
            continue
        before, after = languages[no["run_id"]], languages[enabled["run_id"]]
        if before != after and "Not recorded" not in (before, after):
            title = _model(enabled) + (f" ({_task_title(task)})" if len(task_ids) > 1 else "")
            shifts.append(f"**{cell(title)}, trial {cell(trial)}:** {cell(before)} to {cell(after)}; "
                          f"independent outcome {_condition_status(no)} to {_condition_status(enabled)}.")
    if shifts:
        r.para("**Paired language shifts and correctness outcomes**")
        r.para("Direction: current skills to comparison skills." if comparing_skills else "Direction: no skills to skills enabled.")
        r.bullets(shifts)
    if any(value["c_guidance"] for value in skill_usage.values()):
        r.para('Recorded consulted skill content explicitly says: "Write new code in C using lib_xcore, not XC". '
               "This establishes prescriptive C guidance, not that C or the skills caused a particular failure.")
    r.para("Language choice, skill availability, actual consultation and correctness must be considered together. "
           "Repeated paired trials are needed to determine whether C-directed guidance systematically changes outcomes.")

    r.section("Did the comparison skills change results?" if comparing_skills else "Did skills help?")
    r.para("XTC was preactivated for both conditions. Skills means availability; actual consultation is reported separately.")
    if incomplete:
        r.para("**Comparison invalid for the full experiment:** some runs are missing or errored. "
               "Only completed matched pairs are described below.")
    _comparisons(r, pairs, ranks, languages, skill_usage, failures, comparing_skills)
    r.para("Compare independent pass outcomes before time or cost: a failed submission is not an efficient solution. "
           "A single matched pair does not establish a reliable causal skills effect.")

    r.section("Blind design ranking")
    r.para("The reviewer assessed anonymised implementation source and independent verifier results without knowing the models or "
           "conditions. Lower rank is better; it is not an independent correctness score.")
    if not reviews:
        r.para("No blind review was run.")
    for review in reviews:
        r.subsection(_task_title(str(review.get("task_id"))))
        r.para("**Scope: xcore implementation only.** Candidate test-framework authoring is not scored.")
        ranking = _ranking(review, entries)
        if not ranking:
            r.para(f"Ranking unavailable ({cell(review.get('status', 'unknown'))}).")
            continue
        multiple_trials = len({e.get("repeat") for _, e, _ in ranking if e}) > 1
        headers = ["Rank", "Model", "Condition"] + (["Trial"] if multiple_trials else []) + ["Reason for rank"]
        rows = []
        for rank, (_, e, candidate) in enumerate(ranking, 1):
            takeaway = _ranking_reason(candidate, review, entries)
            row = [rank, _model(e), _condition_label(e)]
            if multiple_trials:
                row.append(e.get("repeat"))
            rows.append(row + [takeaway])
        r.table(headers, rows, [6, 11, 22] + ([6] if multiple_trials else []) + [61])
    r.para("Full reviewer reasoning, risks, scores and anonymous-to-run mappings are in the appendix.")

    r.section("What we can and cannot conclude")
    r.bullets([
        "Correctness comes from the mandatory uniform verifier. All passing submissions meet the same observation window and acceptance criteria.",
        "Time/cost savings and blind implementation-design preferences are different outcomes; report them separately.",
        "Single trials are exploratory. Repeated matched trials and the same independent verifier are needed before attributing differences to skills.",
        "All compared conditions use preactivated XTC, so initial toolchain state is controlled. Test-framework authoring is not scored.",
        "Estimated credits may use incomplete event telemetry and do not establish actual billed charges. "
        "Model identity, global settings and host isolation warnings are retained in the appendix.",
    ])
    r.para("**Next step:** repeat each matched implementation trial at least three times. "
           "Evaluate simulation-test-framework skills separately using known-good and faulty applications.")

    r.section("Appendix - detailed evidence and reproducibility")

    ordered_entries = sorted(results, key=lambda e: (str(e.get("task_id")), ranks.get(e["run_id"], float("inf")),
                                                     str(e.get("model_label")), str(e.get("repeat")), str(e.get("condition"))))
    refs = {e["run_id"]: f"R{i:02d}" for i, e in enumerate(ordered_entries, 1)}
    r.subsection("Submission identifier key")
    r.para("Short references below keep tables readable. Full identifiers are retained here for log lookup.")
    for e in ordered_entries:
        r.bullets([f"**{refs[e['run_id']]} — {cell(_label(e))}, trial {cell(e.get('repeat'))}:** "
                   + literal(e["run_id"])])
        if e.get("reused_from"):
            source = e["reused_from"]
            r.bullets([f"Reused from {literal(source['experiment_id'])}; original result SHA-256: {literal(source['result_sha256'])}."] +
                      [cell(note) for note in source.get("compatibility_notes", [])])

    r.subsection("Method at a glance")
    conditions = ", ".join(conf["conditions"].keys())
    r.para("Each model received the same problem and clean starter in separate OpenCode CLI sessions. "
           "Models were accessed **through GitHub Copilot**; worker variants were "
           f"{cell(conditions)}. A separate model reviewed anonymised implementations "
            "after the worker runs and any configured independent verification. "
            "Preactivation makes tools available so workers can attempt the implementation; "
            "it is not an implementation-quality score. "
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
           f"submission total: ${total_cost:.6f}. These are estimates, not billed charges.")
    if reused_entries:
        r.para(f"Historical reused worker usage: ${reused_cost:.6f}; estimated new usage: ${total_cost - reused_cost:.6f}.")
    r.table(["Run", "Independent check", "Worker seconds", "Verified finish seconds", "Est. USD"], [
        [refs[e["run_id"]], _condition_status(e), e.get("invocation", {}).get("elapsed_seconds"),
         e.get("time_to_verified_success_s"), e.get("usage", {}).get("copilot_estimated_usd")]
        for e in results])
    r.bullets([f"{literal(e.get('run_id'))}: {cell(e.get('error') or e.get('validation', {}).get('reason') or 'Worker failed or served model did not match.')}"
               for e in errors])
    r.subsection("Task selection")
    r.table(["Task", "Detail level", "Reason chosen", "Verifier"], [
        [t.get("id"), t.get("specificity"), t.get("rationale"),
         "configured" if t.get("verifier_configured") or t.get("verifier") or t.get("verify_commands") else "not configured"]
        for t in conf["tasks"]])

    r.subsection("Per-run tokens and skill retrieval")
    token_rows = []
    for e in results:
        tokens = e.get("usage", {}).get("tokens") or {}
        token_rows.append([refs[e["run_id"]], _skill_names(e),
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
        [refs[e["run_id"]], e.get("model"),
         ", ".join(e.get("observed_models") or []) or "not verified",
         e.get("usage", {}).get("usage_source"), "; ".join(e.get("warnings") or []) or "none"]
        for e in results])

    r.subsection("Independent verifier observations")
    for e in ordered_entries:
        v = e.get("validation", {})
        obs = v.get("observations") if isinstance(v, dict) else None
        metrics = obs.get("metrics", {}) if isinstance(obs, dict) else {}
        r.detail(f"{refs[e['run_id']]} — {_label(e)}")
        r.para(f"**Independent status:** {cell(v.get('status'))}.")
        if v.get("reason"):
            r.para(cell(v["reason"]))
        if metrics:
            r.table(["Measurement", "Recorded value"], [
                [str(key).replace("_", " ").capitalize(), _value_text(value)]
                for key, value in metrics.items()], [30, 70])
        else:
            r.para("No independent resource or waveform measurements were recorded.")
        if e.get("design_excerpt_self_reported"):
            r.para("Worker's own design notes (not independent measurements):")
            r.code(e["design_excerpt_self_reported"])

    r.subsection("Blind review detail")
    for review in reviews:
        data = review.get("review")
        if not isinstance(data, dict):
            continue
        r.para(f"**{cell(_task_title(str(review.get('task_id'))))} — full reviewer notes:**")
        r.para(cell(data.get("review_notes", "No summary supplied.")))
        mapping = review.get("blind_mapping", {})
        task_ranks = {alias: rank for rank, (alias, _, _) in enumerate(_ranking(review, entries), 1)}
        for candidate in sorted(data.get("candidates", []), key=lambda c: task_ranks.get(c.get("id"), float("inf")) if isinstance(c, dict) else float("inf")):
            if not isinstance(candidate, dict):
                continue
            alias = candidate.get("id", "unknown")
            entry = entries.get(mapping.get(alias), {})
            rank = task_ranks.get(alias, "unranked")
            r.detail(f"Rank {rank} — {_label(entry)}")
            r.para(f"Submission: {cell(refs.get(mapping.get(alias), 'unknown'))}; "
                   f"blind label: {literal(alias)}; trial: {cell(entry.get('repeat'))}.")
            r.para(cell(_review_text(str(candidate.get("summary", "not assessed")), review, entries)))
            ratings = candidate.get("ratings")
            if isinstance(ratings, dict) and ratings:
                r.table(["Criterion", "Rating (0-5)"], [
                    [str(key).replace("_", " ").capitalize(), value] for key, value in ratings.items()], [82, 18])
            for heading, key in (("Major decisions", "major_decisions"), ("Risks", "risks")):
                r.para(f"**{heading}**")
                r.bullets([cell(_review_text(str(item), review, entries)) for item in candidate.get(key) or []])

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
    r.table(["Run", "Time (s)"], [[refs[e["run_id"]], e.get("time_to_verified_success_s")]
                                  for e in finishers])

    r.subsection("Provenance, limitations and next steps")
    environment = manifest.get("environment", {}) if manifest else {}
    if environment.get("skill_sources"):
        r.table(["Skill condition", "SKILL.md SHA-256", "Resource-tree SHA-256"], [
            [conf["conditions"].get(condition, {}).get("display_name") or CONDITIONS.get(condition, condition),
             source.get("skills_sha256"), source.get("skills_tree_sha256")]
            for condition, source in environment["skill_sources"].items() if condition in conf["conditions"]])
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
