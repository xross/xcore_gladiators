"""Explicit, compatibility-checked reuse of independently verified worker trials.

Original archives and reviewer verdicts are never edited. Reused trials are not
fresh repetitions; combined rankings must come from a new blind review.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

from verification.uart_heartbeat import protocol, validate_observations


def skill_fingerprint(directory: Path) -> dict:
    md = hashlib.sha256()
    tree = hashlib.sha256()
    count = 0
    for skill in sorted(directory.iterdir()):
        if not skill.is_dir() or skill.is_symlink() or not (skill / "SKILL.md").is_file():
            continue
        path = skill / "SKILL.md"
        md.update(str(path.relative_to(directory)).encode())
        md.update(b"\x00")
        md.update(path.read_bytes())
        count += 1
        for path in sorted(skill.rglob("*")):
            if path.is_symlink():
                raise ValueError(f"Skill resources must not contain symlinks: {path}")
            if (not path.is_file() or any(p in (".git", "__pycache__") for p in path.relative_to(skill).parts)
                    or path.name == ".DS_Store" or path.suffix in (".pyc", ".swp", ".swo")):
                continue
            tree.update(str(path.relative_to(directory)).encode())
            tree.update(b"\x00")
            tree.update(path.read_bytes())
            tree.update(b"\x00")
    return {"skills_sha256": md.hexdigest(), "skills_tree_sha256": tree.hexdigest(), "skills_count": count}


def trial_id(row: tuple) -> str:
    task, label, _, condition, repeat = row
    return f"{task['id']}--{label}--{condition}--r{repeat}"


def reusable_trials(source: Path, matrix: list[tuple], conf: dict, config_path: Path,
                    environment: dict, expected_prompts: dict[str, str]) -> dict[str, dict]:
    """Reject stale/incompatible baselines before spending any model calls."""
    source = source.expanduser().resolve()
    manifest = json.loads((source / "experiment.json").read_text(encoding="utf-8"))
    old_conf = manifest.get("report_config") or {}
    old_env = manifest.get("environment") or {}
    results = {}
    for row in matrix:
        task, label, model, condition, repeat = row
        run_id = trial_id(row)
        path = source / "runs" / run_id / "result.json"
        if not path.is_file():
            continue
        entry = json.loads(path.read_text(encoding="utf-8"))
        if entry.get("reused_from"):
            raise ValueError(f"Reuse the original source archive, not a reused copy: {run_id}")
        errors = []
        expected = {"run_id": run_id, "task_id": task["id"], "model_label": label, "model": model,
                    "condition": condition, "repeat": repeat, "evaluation_class": task["evaluation_class"],
                    "task_revision": task["revision"], "xtc_preconfigured": True}
        for key, value in expected.items():
            if entry.get(key) != value:
                errors.append(key)
        prompt_path = Path(task["prompt"]).expanduser()
        prompt_path = prompt_path if prompt_path.is_absolute() else config_path.parent / prompt_path
        if entry.get("prompt_sha256") != hashlib.sha256(prompt_path.read_bytes()).hexdigest():
            errors.append("task prompt")
        worker_prompt = path.parent / "worker_prompt.txt"
        if not worker_prompt.is_file() or worker_prompt.read_text(encoding="utf-8") != expected_prompts[run_id]:
            errors.append("worker instructions")
        if (entry.get("error") or entry.get("requested_model_mismatch") or
                entry.get("invocation", {}).get("exit_code") != 0 or entry.get("invocation", {}).get("timed_out")):
            errors.append("worker completion/model identity")
        validation = entry.get("validation", {})
        observed = validation.get("observations")
        validate_observations(observed)
        if validation.get("status") not in ("passed", "failed") or observed["passed"] != (validation.get("status") == "passed"):
            errors.append("independent verification")
        if observed["protocol"] != protocol() or old_env.get("implementation_verifier") != protocol():
            errors.append("verifier protocol/fingerprint")
        for key in ("platform", "python_version", "opencode_version", "xtc_preactivated_version", "xtc_activation_script_sha256"):
            if not environment.get(key) or old_env.get(key) != environment[key]:
                errors.append(key)
        for key in ("worker_timeout_seconds", "verifier_timeout_seconds", "seed"):
            if old_conf.get("experiment", {}).get(key) != conf["experiment"].get(key):
                errors.append(key)
        if old_conf.get("pricing", {}).get(model) != conf.get("pricing", {}).get(model):
            errors.append("pricing assumptions")
        notes = []
        if conf["conditions"][condition]["type"] == "native":
            current = environment["skill_sources"][condition]
            old = old_env.get("skill_sources", {}).get(condition) or old_env
            if old.get("skills_sha256") != current["skills_sha256"]:
                errors.append("skill content")
            if old.get("skills_tree_sha256"):
                if old["skills_tree_sha256"] != current["skills_tree_sha256"]:
                    errors.append("skill resources")
            else:
                if not current.get("xskills_revision") or old_env.get("xskills_revision") != current["xskills_revision"]:
                    errors.append("skill revision")
                notes.append("Source archive fingerprints SKILL.md files and git revision only; auxiliary skill resources cannot be retrospectively attested.")
        starter = Path(task["starter"]).expanduser()
        starter = starter if starter.is_absolute() else config_path.parent / starter
        old_inputs = manifest.get("task_inputs", {}).get(task["id"], {})
        if old_inputs.get("starter_sha256"):
            if old_inputs["starter_sha256"] != tree_digest(starter):
                errors.append("starter project")
        elif any(p.is_file() and (p.name != ".keep" or p.stat().st_size) for p in starter.rglob("*")):
            errors.append("source archive cannot attest this nonempty starter")
        if not (path.parent / "source" / "src").is_dir():
            errors.append("archived implementation source")
        # Ensure the review snapshot still matches the source actually compiled
        # by the trusted verifier, rather than an edited post-run snapshot.
        builds = list((path.parent / "verification").glob("verification-*/src"))
        if len(builds) != 1 or tree_digest(builds[0]) != tree_digest(path.parent / "source" / "src"):
            errors.append("archived source differs from verified build inputs")
        receipts = list((path.parent / "verification").glob("verification-*/result.json"))
        if len(receipts) != 1 or json.loads(receipts[0].read_text(encoding="utf-8")) != observed:
            errors.append("archived verifier receipt missing or differs from recorded observations")
        for item in observed.get("metrics", {}).get("evidence", []):
            evidence = path.parent / "verification" / item
            if not evidence.resolve().is_relative_to((path.parent / "verification").resolve()) or not evidence.is_file():
                errors.append("missing or invalid archived verifier evidence")
                break
        if errors:
            raise ValueError(f"Cannot reuse {run_id}: incompatible " + ", ".join(errors) + ". No model calls were started.")
        results[run_id] = {"directory": path.parent, "entry": entry,
                           "experiment_id": manifest.get("experiment_id", source.name),
                           "result_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                           "notes": notes, "tree_sha256": tree_digest(path.parent)}
    if not results:
        raise ValueError("No compatible matching trials found in --reuse-results; no model calls were started")
    return results


def tree_digest(root: Path) -> str:
    if not root.is_dir() or root.is_symlink():
        raise ValueError(f"Cannot attest a missing or symlinked directory: {root}")
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"Cannot attest an archive containing symlinks: {path}")
        if path.is_file():
            digest.update(str(path.relative_to(root)).encode())
            digest.update(b"\x00")
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            digest.update(b"\x00")
    return digest.hexdigest()


def copy_trial(reuse: dict, root: Path, condition_label: str | None) -> dict:
    original = reuse["directory"]
    if tree_digest(original) != reuse["tree_sha256"]:
        raise ValueError("Source archive changed after reuse preflight; refusing to copy")
    entry = dict(reuse["entry"])
    dest = root / "runs" / entry["run_id"]
    shutil.copytree(original, dest)
    if condition_label:
        entry["condition_label"] = condition_label
    entry["reused_from"] = {"experiment_id": reuse["experiment_id"], "run_id": entry["run_id"],
                            "result_sha256": reuse["result_sha256"], "archive_tree_sha256": reuse["tree_sha256"],
                            "compatibility_notes": reuse["notes"]}
    (dest / "result.json").write_text(json.dumps(entry, indent=2) + "\n", encoding="utf-8")
    return entry
