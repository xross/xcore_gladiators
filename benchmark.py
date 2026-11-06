#!/usr/bin/env python3
"""Gladiators: reproducible OpenCode coding-agent benchmark; Python 3.11+, stdlib only.

Experimental limitation: each run has an independent workspace and session, but
plain host processes are NOT OS sandboxes. See README for stronger isolation.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import random
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import tomllib
import uuid
from pathlib import Path
from typing import Callable

BASE = Path(__file__).resolve().parent
SKIP_NAMES = {".git", ".opencode", ".claude", ".agents", ".venv", "__pycache__", "node_modules", "build", "bin", ".cache", ".pytest_cache"}
SKIP_SNAPSHOT_FILES = {"opencode.json", "opencode.jsonc", "benchmark-verification.json", "REVIEW_INSTRUCTIONS.md", "AGENTS.md", "CLAUDE.md"}
SECRET_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".token", ".credentials"}
MAX_REVIEW_FILES = 120
MAX_REVIEW_BYTES = 1_600_000
SESSION_RE = re.compile(r"\bses_[a-zA-Z0-9_-]+\b")


def load_config(path: Path) -> dict:
    with path.open("rb") as f:
        conf = tomllib.load(f)
    if "skills_source" in conf.get("experiment", {}):
        raise ValueError("experiment.skills_source is no longer supported; remove it from TOML "
                         "and pass --skills-dir /path/to/xskills/skills on the command line")
    workers = conf["models"]["workers"]
    reviewer = conf["models"]["reviewer"]
    if not workers:
        raise ValueError("Set at least one model under [models.workers]")
    if reviewer.lower() in {m.lower() for m in workers.values()}:
        raise ValueError("Reviewer model must differ from EVERY worker model")
    all_ids = [t["id"] for t in conf["tasks"]]
    if len(all_ids) != len(set(all_ids)):
        raise ValueError("Task IDs must be unique")
    for model in [reviewer, *workers.values()]:
        if not model.startswith("github-copilot/"):
            raise ValueError(f"This experiment requires GitHub Copilot models: {model}")
    return conf


def relpath(path: str, config_path: Path) -> Path:
    p = Path(path).expanduser()
    return (p if p.is_absolute() else config_path.parent / p).resolve()


def validate_skills_directory(value: Path | None, matrix: list[tuple]) -> Path | None:
    """Preflight native skill source BEFORE any Copilot call or experiment creation.

    The CLI argument is deliberately never read from benchmark.toml. Relative
    paths are resolved against the caller's current directory, not the TOML.
    """
    requires_native = any(row[3] == "native" for row in matrix)
    if value is None:
        if requires_native:
            raise ValueError("Native-skills trials require --skills-dir PATH "
                             "(point to the xskills/skills directory). "
                             "No model calls were started.")
        return None
    path = value.expanduser().resolve()
    if not path.is_dir():
        raise ValueError(f"--skills-dir does not exist or is not a directory: {path}. "
                         "No model calls were started.")
    if not requires_native:
        return path
    valid = [p for p in path.iterdir() if p.is_dir() and not p.is_symlink()
             and (p / "SKILL.md").is_file() and not (p / "SKILL.md").is_symlink()]
    if not valid:
        raise ValueError(f"No skills found in --skills-dir {path}; expected "
                         "subdirectories containing SKILL.md. "
                         "Pass the skills/ directory, not the xskills repository root. "
                         "No model calls were started.")
    for skill in valid:
        try:
            content = (skill / "SKILL.md").read_text(encoding="utf-8")
        except (UnicodeError, OSError) as exc:
            raise ValueError(f"Unreadable skill {skill / 'SKILL.md'}: {exc}") from exc
        if not content.startswith("---\n"):
            raise ValueError(f"Invalid skill {skill / 'SKILL.md'}: expected YAML frontmatter")
    return path


def preflight_inputs(matrix: list[tuple], config_path: Path) -> None:
    """Catch missing task inputs before starting any expensive model calls."""
    for task in {row[0]["id"]: row[0] for row in matrix}.values():
        prompt = relpath(task["prompt"], config_path)
        starter = relpath(task["starter"], config_path)
        if not prompt.is_file():
            raise ValueError(f"Missing task prompt: {prompt}. No model calls were started.")
        if not starter.is_dir():
            raise ValueError(f"Missing task starter directory: {starter}. No model calls were started.")
        commands = task.get("verify_commands", [])
        if not isinstance(commands, list) or any(not isinstance(cmd, str) or not cmd.strip()
                                                 for cmd in commands):
            raise ValueError(f"Invalid verify_commands for {task['id']}: expected non-empty "
                             "shell commands. No model calls were started.")


def config_environment(conf: dict, config_path: Path) -> dict:
    env = os.environ.copy()
    home = conf["experiment"].get("clean_home", "").strip()
    if home:
        hp = relpath(home, config_path)
        if not hp.is_dir():
            raise ValueError(f"clean_home does not exist: {hp}")
        env["HOME"] = str(hp)
        env["XDG_CONFIG_HOME"] = str(hp / ".config")
        env["XDG_DATA_HOME"] = str(hp / ".local" / "share")
    env.pop("OPENCODE_CONFIG", None)
    env.pop("OPENCODE_CONFIG_CONTENT", None)
    env.pop("OPENCODE_CONFIG_DIR", None)
    return env


def compact_json(obj: object) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False)


def write_json(path: Path, obj: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(compact_json(obj) + "\n", encoding="utf-8")


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def elapsed_display(seconds: float) -> str:
    """Human-readable wall time for progress messages (not simulated time)."""
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


class JsonlActivity:
    """Read only NEW OpenCode event lines to report activity during a long run.

    We intentionally never display tool arguments, model responses or source
    text on stdout: those could contain sensitive user/repository information.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.offset = 0
        self.pending = b""
        self.events = 0
        self.tools: set[str] = set()
        self.last = "waiting for OpenCode events"

    def update(self) -> dict:
        if not self.path.exists():
            return self.summary()
        with self.path.open("rb") as file:
            file.seek(self.offset)
            while chunk := file.read(256 * 1024):
                self.offset += len(chunk)
                parts = (self.pending + chunk).split(b"\n")
                self.pending = parts.pop()
                for raw in parts:
                    try:
                        event = json.loads(raw)
                    except (ValueError, UnicodeDecodeError):
                        continue
                    if not isinstance(event, dict):
                        continue
                    self.events += 1
                    event_type = str(event.get("type") or "event")
                    part = event.get("part")
                    if not isinstance(part, dict):
                        props = event.get("properties")
                        part = props.get("part") if isinstance(props, dict) else None
                    if isinstance(part, dict) and part.get("type") == "tool":
                        tool = str(part.get("tool") or part.get("name") or "tool")
                        call_id = str(part.get("callID") or part.get("id") or f"{self.events}")
                        self.tools.add(call_id)
                        self.last = f"tool: {tool[:48]}"
                    elif event_type in ("step_start", "step_finish", "error", "tool_use"):
                        self.last = event_type.replace("_", " ")
        # Keep incomplete JSON lines for the next update; never grow without a bound.
        if len(self.pending) > 2_000_000:
            self.pending = b""
        return self.summary()

    def summary(self) -> dict:
        return {"events": self.events, "tools": len(self.tools), "latest": self.last}


def copy_project(source: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    if not source.is_dir():
        raise ValueError(f"Missing starter directory: {source}")
    for child in source.iterdir():
        if child.name in SKIP_NAMES or child.name in SKIP_SNAPSHOT_FILES or child.is_symlink():
            continue
        if child.is_dir():
            shutil.copytree(child, dest / child.name, symlinks=False, ignore=shutil.ignore_patterns(*(SKIP_NAMES | SKIP_SNAPSHOT_FILES)))
        elif child.is_file():
            shutil.copy2(child, dest / child.name)


def prepare_project(workspace: Path, task: dict, config_path: Path, condition: str,
                    conf: dict, skills_dir: Path | None = None) -> tuple[Path, list[str]]:
    copy_project(relpath(task["starter"], config_path), workspace)
    warnings: list[str] = []
    if condition == "native":
        if skills_dir is None:
            raise ValueError("Native trial missing the required --skills-dir PATH")
        src = skills_dir.resolve()
        skills_dest = workspace / ".opencode" / "skills"
        skills_dest.mkdir(parents=True)
        for skill in sorted(src.iterdir()):
            if skill.is_dir() and (skill / "SKILL.md").is_file() and not skill.is_symlink():
                shutil.copytree(skill, skills_dest / skill.name, symlinks=False)
    elif condition != "no_skills" and conf["conditions"][condition]["type"] != "mcp":
        raise ValueError(f"Unsupported condition {condition}")

    # OpenCode V2 permissions; explicit denials are still enforced with --auto.
    perms = [
        {"action": "subagent", "resource": "*", "effect": "deny"},
        {"action": "external_directory", "resource": "*", "effect": "deny"},
        {"action": "question", "resource": "*", "effect": "deny"},
    ]
    if condition != "native":
        perms.append({"action": "skill", "resource": "*", "effect": "deny"})
    opencode_conf: dict = {"$schema": "https://opencode.ai/config.json", "permissions": perms}
    if conf["conditions"][condition]["type"] == "mcp":
        c = conf["conditions"][condition]
        # OpenCode 2 stores MCP servers under mcp.servers, not mcp.<name>.
        opencode_conf["mcp"] = {"servers": {c.get("server_name", "xcore-skills"): {
            "type": "remote", "url": c["url"], "enabled": True}}}
    write_json(workspace / "opencode.json", opencode_conf)
    if not conf["experiment"].get("clean_home"):
        warnings.append("No clean_home configured: global agent settings/skills or plugins could contaminate this run")
    warnings.append("Host-process execution is not a security sandbox; shell tools can bypass OpenCode path checks")
    return workspace / "opencode.json", warnings


def worker_prompt(task: dict, config_path: Path, condition: str) -> str:
    prompt = relpath(task["prompt"], config_path).read_text(encoding="utf-8")
    context = (
        "INDEPENDENT BENCHMARK WORKER - STRICT RULES\n"
        "You are one of several agents solving the SAME problem from the SAME starting point.\n"
        "You may inspect only your current project and normal installed toolchain, vendor libraries,\n"
        "and official PUBLIC documentation. Do not inspect parent or sibling benchmark directories,\n"
        "other agent runs, solutions, previous attempts, cached generated code, review output,\n"
        "benchmark verifier files, or any existing local implementation of this specific task.\n"
        "Do not clone or search for another agent's solution. Do NOT collaborate with other agents.\n"
        "Do not use subagents. Do not change your OpenCode configuration or benchmark conditions.\n"
        "You can use published XMOS libraries and examples as dependencies/references; do not\n"
        "copy an already completed solution of this benchmark task.\n"
        "Perform the entire implementation yourself in the current project.\n"
        "Record your actual build/test attempts. Never invent metrics or claim unrun tests passed.\n"
        "Write DESIGN.md with design decisions, threads per tile, libraries, RAM/flash if measured,\n"
        "time/timing tradeoffs, and verification evidence. Mark unmeasured values as unknown.\n"
    )
    if condition == "no_skills":
        context += "This is the NO-SKILLS control: do not load any skill or skill catalogue.\n"
    elif condition == "native":
        context += "Native XMOS agent skills are installed in this project. Consult relevant native skills.\n"
    else:
        context += "XMOS instructions are available through MCP. Consult relevant MCP tools.\n"
    return context + "\nTASK:\n" + prompt + "\nWhen finished, briefly summarize verified results and remaining risks.\n"


def wait_with_progress(proc: subprocess.Popen, command: object, timeout: float,
                       on_tick: Callable[[float], None] | None = None,
                       progress_interval: float = 20.0) -> dict:
    """Wait, emit periodic ticks, and stop the whole subprocess group on timeout."""
    start = time.monotonic()
    deadline = start + timeout
    timed_out = False
    if progress_interval <= 0:
        on_tick = None
    try:
        if on_tick is None:
            proc.wait(timeout=timeout)
        else:
            while proc.poll() is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(command, timeout)
                try:
                    proc.wait(timeout=min(progress_interval, remaining))
                except subprocess.TimeoutExpired:
                    if time.monotonic() >= deadline:
                        raise
                    on_tick(time.monotonic() - start)
    except subprocess.TimeoutExpired:
        timed_out = True
        if os.name != "nt":
            os.killpg(proc.pid, signal.SIGTERM)
        else:
            proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            if os.name != "nt":
                os.killpg(proc.pid, signal.SIGKILL)
            else:
                proc.kill()
            proc.wait()
    return {"exit_code": proc.returncode, "timed_out": timed_out,
            "elapsed_seconds": round(time.monotonic() - start, 2)}


def launch(command: list[str], cwd: Path, timeout: int, env: dict, log_path: Path, error_path: Path,
           on_tick: Callable[[float], None] | None = None,
           progress_interval: float = 20.0) -> dict:
    with log_path.open("wb") as out, error_path.open("wb") as err:
        # OpenCode may prefer PWD over the cwd supplied to Popen. Keep both in
        # sync to prevent agents writing into the parent Gladiators repository.
        child_env = dict(env)
        child_env["PWD"] = str(cwd.resolve())
        kwargs = {"cwd": str(cwd), "env": child_env, "stdout": out, "stderr": err}
        if os.name != "nt":
            kwargs["start_new_session"] = True
        proc = subprocess.Popen(command, **kwargs)
        return wait_with_progress(proc, command, timeout, on_tick, progress_interval)


def load_json_lines(path: Path) -> list[dict]:
    events = []
    if not path.exists():
        return events
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                events.append(event)
    return events


def find_session_id(events: list[dict]) -> str | None:
    for obj in events:
        sid = obj.get("sessionID") or obj.get("sessionId") or obj.get("session_id")
        if isinstance(sid, str) and SESSION_RE.fullmatch(sid):
            return sid
        if isinstance(obj.get("session"), dict):
            sid = obj["session"].get("id")
            if isinstance(sid, str) and SESSION_RE.fullmatch(sid):
                return sid
    return None


def export_session(opencode: str, session_id: str | None, cwd: Path, env: dict, dest: Path) -> bool:
    if not session_id:
        return False
    # V2 and V1 CLI export syntax differ. Try V2 first.
    for cmd in ([opencode, "session", "export", session_id], [opencode, "export", session_id]):
        try:
            child_env = dict(env)
            child_env["PWD"] = str(cwd.resolve())
            result = subprocess.run(cmd, cwd=cwd, env=child_env, capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if result.returncode != 0:
            continue
        try:
            blob = json.loads(result.stdout)
        except json.JSONDecodeError:
            continue
        write_json(dest, blob)
        return True
    return False


def token_values(obj: object) -> dict | None:
    if not isinstance(obj, dict):
        return None
    t = obj.get("tokens")
    if not isinstance(t, dict):
        return None
    cache = t.get("cache") if isinstance(t.get("cache"), dict) else {}
    # Reasoning is a subset/parallel category of generated tokens; do NOT add twice.
    return {"input": int(t.get("input") or 0), "output": int(t.get("output") or 0),
            "cache_read": int(cache.get("read") or t.get("cacheRead") or 0),
            "cache_write": int(cache.get("write") or t.get("cacheWrite") or 0),
            "reasoning": int(t.get("reasoning") or 0)}


def _messages(blob: object) -> list[dict]:
    if not isinstance(blob, dict):
        return []
    messages = blob.get("messages", [])
    return messages if isinstance(messages, list) else []


def gather_parts(messages: list[dict], events: list[dict]) -> list[dict]:
    parts = []
    for msg in messages:
        for p in msg.get("parts", []) if isinstance(msg, dict) else []:
            if isinstance(p, dict):
                parts.append(p)
        info = msg.get("info", {}) if isinstance(msg, dict) else {}
        for p in info.get("content", []) if isinstance(info.get("content"), list) else []:
            if isinstance(p, dict):
                parts.append(p)
    if not parts:
        for event in events:
            part = event.get("part")
            if not isinstance(part, dict):
                part = event.get("properties", {}).get("part") if isinstance(event.get("properties"), dict) else None
            if isinstance(part, dict):
                parts.append(part)
    return parts


def extract_skill_calls(parts: list[dict]) -> list[dict]:
    calls = []
    seen = set()
    for p in parts:
        if p.get("type") != "tool":
            continue
        tool = str(p.get("tool") or p.get("name") or "")
        state = p.get("state") if isinstance(p.get("state"), dict) else {}
        inputs = state.get("input") or p.get("input") or {}
        if not isinstance(inputs, dict):
            inputs = {}
        if not ("skill" in tool.lower() or "xcore" in tool.lower()):
            continue
        name = inputs.get("id") or inputs.get("name") or inputs.get("query")
        ident = (str(p.get("callID") or ""), tool, str(name))
        if ident in seen:
            continue
        seen.add(ident)
        calls.append({"tool": tool, "skill_or_query": name, "status": state.get("status")})
    return calls




def observed_models(export: object) -> list[str]:
    observed = set()
    for msg in _messages(export):
        info = msg.get("info", msg) if isinstance(msg, dict) else {}
        if not isinstance(info, dict):
            continue
        role = info.get("role") or info.get("type")
        if role not in (None, "assistant"):
            continue
        ref = info.get("model")
        if isinstance(ref, dict):
            pid = ref.get("providerID") or ref.get("provider")
            mid = ref.get("id") or ref.get("modelID")
        else:
            pid, mid = info.get("providerID"), info.get("modelID")
        if pid and mid:
            observed.add(f"{pid}/{mid}".lower())
    return sorted(observed)

def session_usage(export: object, events: list[dict], pricing: dict | None) -> dict:
    # Prefer authoritative per-session aggregates if present; per-step export may
    # miss session title/compaction usage. Never add both aggregates and steps.
    blob = export if isinstance(export, dict) else {}
    aggregate = blob.get("session") or blob.get("info") or {}
    if not isinstance(aggregate, dict):
        aggregate = {}
    tokens = token_values(aggregate)
    tracked = aggregate.get("cost") if isinstance(aggregate.get("cost"), (int, float)) else None
    source = "session aggregate" if tokens is not None else "unavailable"
    messages = _messages(blob)
    if tokens is None:
        totals = {k: 0 for k in ("input", "output", "cache_read", "cache_write", "reasoning")}
        steps = 0
        msg_cost = 0.0
        has_msg_cost = False
        for msg in messages:
            info = msg.get("info", msg) if isinstance(msg, dict) else {}
            if not isinstance(info, dict) or (info.get("role") not in ("assistant", None) and info.get("type") != "assistant"):
                continue
            tt = token_values(info)
            if tt:
                steps += 1
                for k in totals:
                    totals[k] += tt[k]
            if isinstance(info.get("cost"), (int, float)):
                has_msg_cost = True
                msg_cost += info["cost"]
        if steps:
            tokens = totals
            source = "sum of assistant steps (may exclude overhead)"
            if tracked is None and has_msg_cost:
                tracked = msg_cost
    if tokens is None:
        for p in gather_parts(messages, events):
            tt = token_values(p)
            if tt:
                tokens = tokens or {k: 0 for k in tt}
                for k in tt:
                    tokens[k] += tt[k]
        if tokens is not None:
            source = "event/part fallback (may be incomplete)"
    est = None
    if tokens is not None and pricing:
        est = round(sum(tokens[k] * float(pricing.get(k, 0)) for k in ("input", "output", "cache_read", "cache_write")) / 1_000_000, 6)
    return {"tokens": tokens, "usage_source": source, "opencode_estimate_usd": tracked,
            "copilot_estimated_usd": est,
            "copilot_estimated_ai_credits": round(est * 100, 4) if est is not None else None,
            "actual_additional_billed_usd": None,
            "pricing_assumption": "default context tier; aggregate-token approximation; included-plan allowance not deducted"}


def verify_candidate(task: dict, workspace: Path, env: dict, run_dir: Path, timeout: int,
                     progress: Callable[[str], None] | None = None,
                     progress_interval: float = 20.0,
                     require_verifier_output: bool = False) -> dict:
    commands = task.get("verify_commands", [])
    if not commands:
        return {"status": "unverified", "reason": "No independent verifier configured", "elapsed_seconds": 0}
    start = time.monotonic()
    log_path = run_dir / "validator.log"
    all_ok = True
    steps = []
    # A worker could have fabricated this filename. Remove it BEFORE running
    # trusted independent validation, so only the verifier may recreate it.
    results_file = workspace / "benchmark-verification.json"
    if results_file.exists():
        results_file.unlink()
    with log_path.open("w", encoding="utf-8") as log:
        for i, cmd in enumerate(commands, start=1):
            if progress:
                progress(f"  [verifier {i}/{len(commands)}] running independent check")
            log.write(f"\n$ {cmd}\n")
            log.flush()
            try:
                child_env = dict(env)
                child_env["PWD"] = str(workspace.resolve())
                kwargs = {"cwd": str(workspace), "env": child_env, "shell": True,
                          "stdout": log, "stderr": subprocess.STDOUT}
                if os.name != "nt":
                    kwargs["start_new_session"] = True
                proc = subprocess.Popen(cmd, **kwargs)
                result = wait_with_progress(
                    proc, cmd, timeout,
                    (lambda elapsed, step=i: progress(
                        f"  [verifier {step}/{len(commands)}] {elapsed_display(elapsed)} elapsed; still running"))
                    if progress else None, progress_interval)
                steps.append({"command": cmd, "exit_code": result["exit_code"],
                              "timeout": result["timed_out"]})
                if progress:
                    progress(f"  [verifier {i}/{len(commands)}] exit={result['exit_code']} "
                             f"after {elapsed_display(result['elapsed_seconds'])}")
                if result["exit_code"] != 0:
                    all_ok = False
                    break
            except OSError as exc:
                log.write(f"Verifier could not be started: {exc}\n")
                steps.append({"command": cmd, "start_error": str(exc)})
                all_ok = False
                break
    data = {"status": "passed" if all_ok else "failed", "elapsed_seconds": round(time.monotonic() - start, 2), "steps": steps}
    if results_file.is_file():
        try:
            result = json.loads(results_file.read_text(encoding="utf-8"))
            if not isinstance(result, dict) or not isinstance(result.get("passed"), bool):
                raise ValueError("verifier JSON must contain boolean passed")
            data["observations"] = result
            if result["passed"] is False:
                data["status"] = "failed"
        except (ValueError, TypeError) as exc:
            data["invalid_metrics_json"] = True
            data["reason"] = f"Invalid independent verifier output: {exc}"
            data["status"] = "failed"
    elif require_verifier_output:
        data["status"] = "failed"
        data["reason"] = ("Smoke requires independent verifier evidence: "
                          "no benchmark-verification.json was generated by the verifier")
    return data


def snapshot_project(src: Path, dest: Path, include_build: bool = False) -> dict:
    """Persist source, not tools/skills/large build products; copied paths aren't symlinks."""
    dest.mkdir(parents=True, exist_ok=True)
    files, size = 0, 0
    omitted = []
    for p in sorted(src.rglob("*")):
        if (any(q in SKIP_NAMES for q in p.relative_to(src).parts) or p.name in SKIP_SNAPSHOT_FILES
            or p.name.startswith(".env") or p.suffix.lower() in SECRET_SUFFIXES
            or p.is_symlink() or not p.is_file()):
            continue
        rel = p.relative_to(src)
        if p.stat().st_size > 512_000 or files >= MAX_REVIEW_FILES or size + p.stat().st_size > MAX_REVIEW_BYTES:
            omitted.append(str(rel))
            continue
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, target)
        files += 1
        size += p.stat().st_size
    return {"copied_files": files, "copied_bytes": size, "omitted": omitted[:30]}


def run_one(conf: dict, config_path: Path, result_root: Path, task: dict, model_label: str,
            model: str, condition: str, repeat: int, skills_dir: Path | None = None,
            progress: Callable[[str], None] | None = None,
            progress_interval: float = 20.0,
            require_verifier_output: bool = False) -> dict:
    run_id = f"{task['id']}--{model_label}--{condition}--r{repeat}"
    run_dir = result_root / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    env = config_environment(conf, config_path)
    exp = conf["experiment"]
    opencode = exp.get("opencode_command", "opencode")
    stamp = now_iso()
    # Keep candidate workspaces OUTSIDE the project/results tree. This avoids
    # OpenCode discovering neighbouring solutions when walking up the directory
    # hierarchy. Host-level sandboxing is still required for strict isolation.
    with tempfile.TemporaryDirectory(prefix="gladiators-candidate-") as tmp:
        workspace = Path(tmp)
        try:
            if progress:
                progress("  [setup] preparing clean worker workspace and skill condition")
            conf_path, warnings = prepare_project(workspace, task, config_path,
                                                  condition, conf, skills_dir=skills_dir)
            env["OPENCODE_CONFIG"] = str(conf_path)
            env["OPENCODE_CONFIG_CONTENT"] = compact_json(json.loads(conf_path.read_text(encoding="utf-8")))
            instructions = worker_prompt(task, config_path, condition)
            (run_dir / "worker_prompt.txt").write_text(instructions, encoding="utf-8")
            command = [opencode, "run", "--standalone", "--auto", "--agent", "build", "--model", model,
                       "--format", "json", instructions]
            activity = JsonlActivity(run_dir / "events.jsonl")
            if progress:
                progress(f"  [worker] started OpenCode via GitHub Copilot: {model}")
            def worker_tick(elapsed: float) -> None:
                state = activity.update()
                if progress:
                    progress(f"  [worker] {elapsed_display(elapsed)} elapsed | "
                             f"events={state['events']} | tool calls={state['tools']} | "
                             f"latest={state['latest']}")
            invocation = launch(command, workspace, int(exp.get("worker_timeout_seconds", 2700)), env,
                                run_dir / "events.jsonl", run_dir / "stderr.log",
                                on_tick=worker_tick if progress else None,
                                progress_interval=progress_interval)
            if progress:
                state = activity.update()
                progress(f"  [worker] finished in {elapsed_display(invocation['elapsed_seconds'])}; "
                         f"exit={invocation['exit_code']}; events={state['events']}; "
                         f"tool calls={state['tools']}" +
                         (" (TIMEOUT)" if invocation['timed_out'] else ""))
            events = load_json_lines(run_dir / "events.jsonl")
            session_id = find_session_id(events)
            export_path = run_dir / "session-export.json"
            if progress:
                progress("  [usage] exporting session, counting tokens and skill calls")
            has_export = export_session(opencode, session_id, workspace, env, export_path)
            exported = json.loads(export_path.read_text(encoding="utf-8")) if has_export else None
            usage = session_usage(exported, events, conf.get("pricing", {}).get(model))
            skills = extract_skill_calls(gather_parts(_messages(exported), events))
            observed = observed_models(exported)
            mismatch = bool(observed and model.lower() not in observed)
            if mismatch:
                warnings.append(f"MODEL MISMATCH: requested {model}, observed {observed}; exclude this run from valid comparison")
            elif not observed:
                warnings.append("Actual served model ID not independently verifiable from exported transcript")
            if condition == "no_skills" and skills:
                warnings.append("Skill-related tool calls observed in NO-SKILLS control; inspect for contamination")
            execution_error = None
            if invocation["timed_out"]:
                execution_error = (f"OpenCode worker timed out after "
                                   f"{invocation['elapsed_seconds']}s. See {run_dir / 'stderr.log'}")
            elif invocation["exit_code"] != 0:
                execution_error = (f"OpenCode worker exited with code {invocation['exit_code']}. "
                                   f"See {run_dir / 'stderr.log'} and {run_dir / 'events.jsonl'}")
            if execution_error:
                # Do not confuse a failed agent process with a completed but
                # unverified implementation. Preserve its recorded usage/logs.
                verifier = {"status": "error", "reason": execution_error, "elapsed_seconds": 0}
                if progress:
                    progress(f"  [error] {execution_error}")
            else:
                if progress:
                    progress("  [verify] checking independent correctness" if task.get("verify_commands")
                             else "  [verify] no trusted verifier configured; will mark UNVERIFIED")
                verifier = verify_candidate(task, workspace, env, run_dir,
                                            int(exp.get("verifier_timeout_seconds", 900)),
                                            progress=progress, progress_interval=progress_interval,
                                            require_verifier_output=require_verifier_output)
            if progress:
                usage_str = (f"{usage['copilot_estimated_ai_credits']:.3f} estimated Copilot AI Credits"
                             if usage.get("copilot_estimated_ai_credits") is not None
                             else "credits unavailable")
                progress(f"  [verify] {verifier['status'].upper()}; "
                         f"{len(skills)} skill-related calls; {usage_str}")
            design_file = workspace / "DESIGN.md"
            design_excerpt = design_file.read_text(encoding="utf-8", errors="replace")[:1200] if design_file.is_file() else None
            snapshot = snapshot_project(workspace, run_dir / "source")
            result = {"run_id": run_id, "task_id": task["id"], "kind": task["kind"],
                      "model_label": model_label, "model": model, "condition": condition,
                      "task_family": task.get("family", task["id"]),
                      "prompt_specificity": task.get("specificity", "unspecified"),
                      "prompt_sha256": hashlib.sha256(relpath(task["prompt"], config_path).read_bytes()).hexdigest(),
                      "repeat": repeat, "started_utc": stamp, "invocation": invocation,
                      "session_id": session_id, "session_exported": has_export,
                      "observed_models": observed, "requested_model_mismatch": mismatch,
                      "usage": usage, "skill_calls": skills,
                      "validation": verifier, "time_to_verified_success_s": (
                          round(invocation["elapsed_seconds"] + verifier["elapsed_seconds"], 2)
                          if invocation["exit_code"] == 0 and verifier["status"] == "passed" else None),
                      "design_excerpt_self_reported": design_excerpt, "snapshot": snapshot,
                      "warnings": warnings}
            if execution_error:
                result["error"] = execution_error
            write_json(run_dir / "result.json", result)
            return result
        except Exception as exc:
            import traceback
            (run_dir / "harness-error.log").write_text(traceback.format_exc(), encoding="utf-8")
            if progress:
                progress(f"  [error] {type(exc).__name__}: {exc} "
                         f"(details in {run_dir / 'harness-error.log'})")
            result = {"run_id": run_id, "task_id": task["id"], "model_label": model_label,
                      "model": model, "condition": condition, "repeat": repeat,
                      "task_family": task.get("family", task["id"]),
                      "prompt_specificity": task.get("specificity", "unspecified"),
                      "started_utc": stamp, "error": f"{type(exc).__name__}: {exc}",
                      "diagnostic_file": str(run_dir / "harness-error.log"),
                      "validation": {"status": "error"}, "usage": {}, "skill_calls": []}
            write_json(run_dir / "result.json", result)
            return result


def anonymize_candidates(results: list[dict], seed: int, task_id: str) -> list[dict]:
    candidates = [r for r in results if r["task_id"] == task_id and "error" not in r
                  and not r.get("requested_model_mismatch")]
    # A deterministic but nonsemantic shuffle prevents model/condition ordering bias.
    h = int(hashlib.sha256(f"{seed}/{task_id}".encode()).hexdigest()[:12], 16)
    rnd = random.Random(h)
    rnd.shuffle(candidates)
    return [{"alias": f"candidate_{i+1:02d}", "run": r} for i, r in enumerate(candidates)]


def judge_task(conf: dict, config_path: Path, root: Path, task: dict, results: list[dict],
               progress: Callable[[str], None] | None = None,
               progress_interval: float = 20.0) -> dict:
    task_results = [r for r in results if r["task_id"] == task["id"]]
    candidates = anonymize_candidates(results, int(conf["experiment"].get("seed", 1)), task["id"])
    if len(candidates) < len(task_results):
        if progress:
            progress(f"  [referee] skipped: {len(task_results) - len(candidates)} of "
                     f"{len(task_results)} worker runs failed or had a model mismatch")
        return {"task_id": task["id"], "status": "incomplete_candidates",
                "expected": len(task_results), "eligible": len(candidates),
                "reason": "Worker error or model mismatch; cannot compare the complete matrix"}
    if len(candidates) < 2:
        if progress:
            progress(f"  [referee] skipped: only {len(candidates)} eligible candidates")
        return {"task_id": task["id"], "status": "not_enough_candidates"}
    judge_root = root / "reviews" / task["id"]
    judge_root.mkdir(parents=True, exist_ok=True)
    judge_work = judge_root / "judge-work"
    judge_work.mkdir(exist_ok=True)
    for c in candidates:
        alias, r = c["alias"], c["run"]
        dest = judge_work / "candidates" / alias
        dest.mkdir(parents=True, exist_ok=True)
        snapshot_project(root / "runs" / r["run_id"] / "source", dest / "source")
        # Objective evidence, without worker model/condition/usage/timing.
        write_json(dest / "verification.json", r["validation"])
    (judge_work / "task.md").write_text(relpath(task["prompt"], config_path).read_text(encoding="utf-8"), encoding="utf-8")
    guidelines = ("You are an independent, expert XMOS/XCORE code reviewer. Examine the implementations "
                  "under candidates/ and the specification in task.md. Candidate labels are BLIND: "
                  "you are not told the model or whether any skills were available. Do not try to infer identities. "
                  "Assess only the source and trusted verifier results, not self-reported claims. "
                  "Do not execute unknown source. Do not edit candidate source. "
                  "Assign each candidate integer ratings 0-5 for architecture, real_time_correctness, "
                  "maintainability, testing_quality, resource_efficiency, and xmos_idioms. "
                  "A failing/unverified candidate must not be called verified. Explicitly discuss logical "
                  "core allocation, libraries, RAM if established, timing risks, major design decisions, "
                  "and why one candidate is better. Use null for unknown measurements. "
                  "Write a VALID JSON file review.json containing {\"candidates\": [{\"id\":\"candidate_01\", "
                  "\"ratings\": {\"architecture\":0, \"real_time_correctness\":0, \"maintainability\":0, "
                  "\"testing_quality\":0, \"resource_efficiency\":0, \"xmos_idioms\":0}, "
                  "\"threads_per_tile\":null, \"libraries\":[], \"ram_bytes_per_tile\":null, "
                  "\"major_decisions\":[], \"risks\":[], \"summary\":\"...\"}], "
                  "\"ranked_ids\":[\"candidate_01\"], \"review_notes\":\"...\"}. "
                  "Use ALL candidate IDs and rank them best first. Include specific evidence. "
                  "Do not include invented measurements. Write only review.json outside candidate trees.")
    (judge_work / "REVIEW_INSTRUCTIONS.md").write_text(guidelines, encoding="utf-8")
    judge_conf = {"$schema": "https://opencode.ai/config.json", "permissions": [
        {"action": "skill", "resource": "*", "effect": "deny"},
        {"action": "subagent", "resource": "*", "effect": "deny"},
        {"action": "external_directory", "resource": "*", "effect": "deny"},
        {"action": "question", "resource": "*", "effect": "deny"}]}
    write_json(judge_work / "opencode.json", judge_conf)
    env = config_environment(conf, config_path)
    env["OPENCODE_CONFIG"] = str(judge_work / "opencode.json")
    env["OPENCODE_CONFIG_CONTENT"] = compact_json(judge_conf)
    model = conf["models"]["reviewer"]
    command = [conf["experiment"].get("opencode_command", "opencode"), "run", "--standalone", "--auto",
               "--agent", "build", "--model", model, "--format", "json", guidelines]
    activity = JsonlActivity(judge_root / "events.jsonl")
    if progress:
        progress(f"  [referee] reviewing {len(candidates)} blinded implementations using {model}")
    def referee_tick(elapsed: float) -> None:
        state = activity.update()
        if progress:
            progress(f"  [referee] {elapsed_display(elapsed)} elapsed | "
                     f"events={state['events']} | tool calls={state['tools']} | "
                     f"latest={state['latest']}")
    invocation = launch(command, judge_work, int(conf["experiment"].get("reviewer_timeout_seconds", 1200)),
                        env, judge_root / "events.jsonl", judge_root / "stderr.log",
                        on_tick=referee_tick if progress else None,
                        progress_interval=progress_interval)
    if progress:
        progress(f"  [referee] finished in {elapsed_display(invocation['elapsed_seconds'])}, "
                 f"exit={invocation['exit_code']}" + (" (TIMEOUT)" if invocation['timed_out'] else ""))
    events = load_json_lines(judge_root / "events.jsonl")
    session_id = find_session_id(events)
    export_path = judge_root / "session-export.json"
    has_export = export_session(conf["experiment"].get("opencode_command", "opencode"), session_id,
                                judge_work, env, export_path)
    exported = json.loads(export_path.read_text(encoding="utf-8")) if has_export else None
    review_data = None
    review_file = judge_work / "review.json"
    if review_file.is_file():
        try:
            review_data = json.loads(review_file.read_text(encoding="utf-8"))
            if not isinstance(review_data, dict) or not isinstance(review_data.get("candidates"), list):
                review_data = None
        except ValueError:
            pass
    actual_judge_models = observed_models(exported)
    reviewer_model_match = model.lower() in actual_judge_models if actual_judge_models else None
    status = ("reviewer_model_mismatch" if reviewer_model_match is False else
              "reviewed" if review_data else "review_missing_or_invalid")
    result = {"task_id": task["id"], "status": status,
              "reviewer_model": model, "observed_models": actual_judge_models,
              "reviewer_model_match": reviewer_model_match,
              "invocation": invocation, "session_id": session_id,
              "usage": session_usage(exported, events, conf.get("pricing", {}).get(model)),
              "blind_mapping": {c["alias"]: c["run"]["run_id"] for c in candidates},
              "review": review_data}
    write_json(judge_root / "review-result.json", result)
    return result


def report_config_snapshot(conf: dict, selected_task_ids: set[str]) -> dict:
    """Retain nonsecret experimental settings for faithful later report regeneration.

    The source TOML can be edited after a run. Reports must use these values,
    not silently substitute a new choice of models/tasks/skill conditions.
    Never persist the Copilot auth profile, skill source path or MCP URLs here.
    """
    exp = conf["experiment"]
    safe_exp = {k: exp[k] for k in ("name", "seed", "repeats", "parallel_sim_runs",
                 "worker_timeout_seconds", "reviewer_timeout_seconds",
                 "verifier_timeout_seconds", "progress_interval_seconds",
                 "review_enabled", "pricing_checked", "pricing_source") if k in exp}
    safe_tasks = []
    for t in conf["tasks"]:
        if t["id"] not in selected_task_ids:
            continue
        safe_tasks.append({k: t[k] for k in ("id", "family", "specificity", "kind", "rationale", "prompt") if k in t}
                          | {"verifier_configured": bool(t.get("verify_commands"))})
    return {
        "experiment": safe_exp,
        "models": conf["models"],
        "model_profiles": conf.get("model_profiles", {}),
        "pricing": conf.get("pricing", {}),
        "conditions": {name: {k: v[k] for k in ("type", "server_name") if k in v}
                       for name, v in conf["conditions"].items()},
        "tasks": safe_tasks,
    }


def probe_cli_version(argv: list[str]) -> str | None:
    """Best-effort informational check; failure never breaks a benchmark run."""
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=8)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if done.returncode != 0:
        return None
    version_lines = (done.stdout or done.stderr or "").strip().splitlines()
    return version_lines[0][:180] if version_lines else None


def study_environment(conf: dict, config_path: Path, skills_dir: Path | None = None) -> dict:
    skills_revision = (probe_cli_version(["git", "-C", str(skills_dir),
                                          "rev-parse", "HEAD"]) if skills_dir else None)
    # Hash content, not the install location. This allows comparisons across
    # machines and over time without exposing a personal filesystem path.
    skill_fingerprint = None
    skill_count = 0
    if skills_dir:
        digest = hashlib.sha256()
        for p in sorted(skills_dir.glob("*/SKILL.md")):
            digest.update(str(p.relative_to(skills_dir)).encode("utf-8"))
            digest.update(b"\x00")
            digest.update(p.read_bytes())
            skill_count += 1
        skill_fingerprint = digest.hexdigest()
    return {
        "python_version": sys.version.split()[0],
        "platform": sys.platform,
        "opencode_version": probe_cli_version([conf["experiment"].get("opencode_command", "opencode"), "--version"]),
        "compiler_version": probe_cli_version(["xcc", "--version"]),
        "xskills_revision": skills_revision,
        "skills_sha256": skill_fingerprint,
        "skills_count": skill_count,
    }


def config_for_report(root: Path, current_conf: dict) -> tuple[dict, dict | None]:
    """Prefer frozen experiment metadata over current editable configuration."""
    manifest_path = root / "experiment.json"
    if not manifest_path.is_file():
        return current_conf, None
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    snapshot = manifest.get("report_config")
    return (snapshot if isinstance(snapshot, dict) else current_conf), manifest


def model_profile(conf: dict, model: str) -> dict:
    profile = conf.get("model_profiles", {}).get(model, {})
    return {
        "display_name": profile.get("display_name") or model.removeprefix("github-copilot/"),
        "developer": profile.get("developer") or "Not documented",
        "published_category": profile.get("published_category") or "Not documented",
        "parameter_count": profile.get("parameter_count") or "Not publicly disclosed / unverified",
        "context_window": profile.get("context_window") or "Not verified for this OpenCode + Copilot route",
        "quality_expectation": profile.get("quality_expectation") or "Not predeclared; do not infer from results",
        "selection_rationale": profile.get("selection_rationale") or "No selection rationale recorded",
        "source": profile.get("source") or "Not provided",
        "source_checked": profile.get("source_checked") or "Not recorded",
    }


def report(root: Path, conf: dict, results: list[dict], reviews: list[dict]) -> Path:
    """Generate a reStructuredText report from frozen experiment metadata."""
    from rst_report import build_report
    frozen_conf, manifest = config_for_report(root, conf)
    return build_report(root, frozen_conf, manifest, results, reviews, now_iso, model_profile)


def plan(conf: dict, task_filter: str | None = None, model_filter: str | None = None,
         condition_filter: str | None = None, repeats: int | None = None,
         smoke: bool = False) -> list[tuple[dict, str, str, str, int]]:
    """Build an independent run matrix, optionally reduced for a cheap trial.

    --smoke chooses one task (preferring a specific prompt), exactly two
    configured worker models (experiment.smoke_models, or the two lowest-cost
    by recorded token-price assumptions), and both no_skills/native conditions,
    with one repeat and independent blind review.
    """
    tasks = conf["tasks"]
    workers = conf["models"]["workers"]
    conditions = conf["conditions"]
    if task_filter and task_filter not in {t["id"] for t in tasks}:
        raise ValueError(f"Unknown task '{task_filter}'. Available: {', '.join(t['id'] for t in tasks)}")
    if model_filter and model_filter not in workers and model_filter not in workers.values():
        raise ValueError(f"Unknown worker '{model_filter}'. Available labels: {', '.join(workers)}")
    if condition_filter and condition_filter not in conditions:
        raise ValueError(f"Unknown condition '{condition_filter}'. Available: {', '.join(conditions)}")
    if repeats is not None and repeats < 1:
        raise ValueError("--repeats must be a positive integer")
    if smoke and task_filter is None:
        task_filter = next((t["id"] for t in tasks if t.get("specificity") == "specific"), tasks[0]["id"])
    if smoke:
        if model_filter is not None or condition_filter is not None:
            raise ValueError("--smoke always compares two models in both conditions; "
                             "set experiment.smoke_models to choose models, or omit --smoke "
                             "to use --model/--condition")
        if repeats not in (None, 1):
            raise ValueError("--smoke requires one repeat; omit --repeats or set it to 1")
        if not {"no_skills", "native"}.issubset(conditions):
            raise ValueError("--smoke requires both no_skills and native conditions")
        configured = conf["experiment"].get("smoke_models")
        if configured is not None:
            if not isinstance(configured, list) or len(configured) != 2 or len(set(configured)) != 2:
                raise ValueError("experiment.smoke_models must list exactly two distinct worker aliases")
            if any(label not in workers for label in configured):
                raise ValueError("experiment.smoke_models contains an unknown worker alias")
            chosen_workers = configured
        else:
            if len(workers) < 2:
                raise ValueError("--smoke requires at least two worker models")
            prices = conf.get("pricing", {})
            # Favour low-cost worker calls: illustrative 3:1 input/output token mix.
            def estimated_weighted_rate(label: str) -> float:
                rates = prices.get(workers[label])
                return (3 * float(rates.get("input", 0)) + float(rates.get("output", 0))) if rates else float("inf")
            chosen_workers = sorted(workers, key=estimated_weighted_rate)[:2]
        chosen_conditions = ["no_skills", "native"]
        repeats_final = 1
    else:
        chosen_workers = [label for label, model in workers.items()
                          if not model_filter or model_filter in (label, model)]
        chosen_conditions = [condition_filter] if condition_filter else list(conditions)
        repeats_final = repeats if repeats is not None else int(conf["experiment"].get("repeats", 1))
    matrix = []
    for task in tasks:
        if task_filter and task["id"] != task_filter:
            continue
        for label in chosen_workers:
            model = workers[label]
            for condition in chosen_conditions:
                for trial in range(1, repeats_final + 1):
                    matrix.append((task, label, model, condition, trial))
    return matrix


def config_for_selected_matrix(conf: dict, matrix: list[tuple], review_enabled: bool) -> dict:
    """Freeze only the model and conditions actually evaluated in a partial run."""
    from copy import deepcopy
    chosen = deepcopy(conf)
    worker_labels = {row[1] for row in matrix}
    condition_names = {row[3] for row in matrix}
    task_ids = {row[0]["id"] for row in matrix}
    chosen["models"]["workers"] = {name: model for name, model in conf["models"]["workers"].items()
                                     if name in worker_labels}
    chosen["conditions"] = {name: value for name, value in conf["conditions"].items() if name in condition_names}
    chosen["tasks"] = [task for task in conf["tasks"] if task["id"] in task_ids]
    chosen["experiment"]["repeats"] = max(row[4] for row in matrix)
    chosen["experiment"]["review_enabled"] = review_enabled
    return chosen


def main() -> int:
    from results_store import connect as open_results_db, index_experiment, reindex, history, trend, compare

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["plan", "run", "report", "index", "history", "trend", "compare"])
    parser.add_argument("--config", type=Path, default=BASE / "benchmark.toml")
    parser.add_argument("--skills-dir", type=Path, metavar="PATH",
                        help="Required for plan/run when native skills are selected; "
                        "path to xskills/skills (never configured in TOML)")
    parser.add_argument("--task", help="Optional task ID filter")
    parser.add_argument("--model", help="Filter by worker alias (luna) or full OpenCode model ID")
    parser.add_argument("--condition", help="Filter by condition (native, no_skills, later mcp)")
    parser.add_argument("--repeats", type=int, help="Override repeats per model/condition")
    parser.add_argument("--smoke", action="store_true", help="Quick complete comparison: one task, two configured workers, "
                        "with and without native skills, one repeat, optional verifier and blind reviewer")
    parser.add_argument("--results", type=Path, help="Existing experiment directory for 'report'")
    parser.add_argument("--results-root", type=Path, default=BASE / "bench-results",
                        help="Persistent experiment archive directory (default: bench-results)")
    parser.add_argument("--database", type=Path,
                        help="SQLite historical index path (default: RESULTS_ROOT/gladiators.sqlite3)")
    parser.add_argument("--limit", type=int, default=40, help="Number of rows shown in history/trend")
    parser.add_argument("--no-review", action="store_true")
    parser.add_argument("--allow-unverified", action="store_true", help="Legacy compatibility flag; verification is now optional by default")
    parser.add_argument("--require-verification", action="store_true",
                        help="Fail before model calls if any selected task lacks an independent verifier")
    parser.add_argument("--progress-every", type=float, default=None,
                        metavar="SECONDS", help="Show activity during workers, verification and review "
                        "every N seconds (0 disables periodic updates; default from benchmark.toml)")
    args = parser.parse_args()
    if args.smoke and args.no_review:
        parser.error("--smoke includes the independent reviewer; omit --smoke to use --no-review")
    if args.require_verification and args.allow_unverified:
        parser.error("--require-verification conflicts with --allow-unverified")
    if args.limit < 1:
        parser.error("--limit must be positive")
    results_root = args.results_root.resolve()
    db_path = args.database.resolve() if args.database else results_root / "gladiators.sqlite3"
    if args.command in ("index", "history", "trend", "compare"):
        db = open_results_db(db_path)
        try:
            if args.command == "index":
                count, trials = reindex(db, results_root)
                print(f"Indexed {count} experiments, {trials} worker results in {db_path}")
            elif args.command == "history":
                print("\n".join(history(db, limit=args.limit)))
            elif args.command == "trend":
                print("\n".join(trend(db,args.task,args.model,args.condition,args.limit)))
            else:
                print("\n".join(compare(db,args.task,args.model,args.condition)))
        finally:
            db.close()
        return 0

    config_path = args.config.resolve()
    conf = load_config(config_path)
    if args.command == "report":
        if not args.results:
            parser.error("--results is required for report")
        root = args.results.resolve()
        runs = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((root / "runs").glob("*/result.json"))]
        reviews = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((root / "reviews").glob("*/review-result.json"))]
        print(report(root, conf, runs, reviews))
        db = open_results_db(db_path)
        try:
            counts = index_experiment(db, root)
            print(f"Results index: {db_path} ({counts[0]} trials)")
        finally:
            db.close()
        return 0
    matrix = plan(conf, args.task, args.model, args.condition, args.repeats, args.smoke)
    if not matrix:
        parser.error("No test cases match your selection")
    skills_dir = validate_skills_directory(args.skills_dir, matrix)
    preflight_inputs(matrix, config_path)
    if args.command == "plan":
        if args.smoke:
            print("SMOKE PLAN (four worker trials, optional verification, and blind referee)")
            if any(not task.get("verify_commands") for task, *_ in matrix):
                print("NOTE: no verifier configured; trial correctness will be UNVERIFIED, but review still runs.")
        for t, name, model, condition, trial in matrix:
            print(f"{t['id']:26} {name:8} {model:42} {condition:12} r{trial}")
        print(f"\n{len(matrix)} independent worker runs. Reviewer: "
              + conf["models"]["reviewer"])
        return 0
    exp = conf["experiment"]
    progress_interval = (args.progress_every if args.progress_every is not None else
                         float(exp.get("progress_interval_seconds", 20)))
    if progress_interval < 0:
        parser.error("--progress-every must be nonnegative")
    unconfigured = sorted({t["id"] for t, *_ in matrix if not t.get("verify_commands")})
    if args.require_verification and unconfigured:
        raise ValueError("Independent verification required for: " + ", ".join(unconfigured) +
                         ". Configure tasks[].verify_commands or omit --require-verification. "
                         "No model calls were started.")
    allow_unverified = not args.require_verification
    if unconfigured:
        print("WARNING: No independent verifier configured for " + ", ".join(unconfigured) +
              ". Correctness will be UNVERIFIED (not passed); the blind reviewer "
              "can still assess design quality.", flush=True)
    exe = exp.get("opencode_command", "opencode")
    if not Path(exe).is_file() and shutil.which(exe) is None:
        raise ValueError(f"OpenCode executable not found: {exe}")
    reviewer_enabled = bool(not args.no_review and (args.smoke or exp.get("review_enabled", True)))
    selected_conf = config_for_selected_matrix(conf, matrix, reviewer_enabled)
    root = (results_root / (dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6])).resolve()
    root.mkdir(parents=True)
    run_options = {"smoke": args.smoke, "review_enabled": reviewer_enabled,
                   "allow_unverified": allow_unverified,
                   "native_skills_directory_supplied": skills_dir is not None,
                   "model_filter": args.model, "condition_filter": args.condition,
                   "task_filter": args.task, "repeats_override": args.repeats}
    write_json(root / "experiment.json", {"experiment_id": root.name,
                                          "started_utc": now_iso(),
                                          "configuration_sha256": hashlib.sha256(config_path.read_bytes()).hexdigest(),
                                          "environment": study_environment(conf, config_path, skills_dir),
                                          "report_config": report_config_snapshot(selected_conf,
                                              {t[0]["id"] for t in matrix}),
                                          "run_options": run_options,
                                          "run_order": [{"task_id": t[0]["id"], "worker": t[1],
                                                         "model_id": t[2], "condition": t[3], "repeat": t[4]}
                                                        for t in matrix],
                                          "model_config": selected_conf["models"],
                                          "task_ids": [t[0]["id"] for t in matrix],
                                          "task_prompts": [{"id": t["id"], "family": t.get("family", t["id"]),
                                                            "specificity": t.get("specificity", "unspecified"),
                                                            "prompt_sha256": hashlib.sha256(relpath(t["prompt"], config_path).read_bytes()).hexdigest()}
                                                           for t in selected_conf["tasks"]],
                                          "conditions": list(selected_conf["conditions"]),
                                          "seed": exp.get("seed"),
                                          "note": "No auth secrets or local skill contents are included in this manifest."})
    db = open_results_db(db_path)
    try:
        index_experiment(db, root)
        all_results: list[dict] = []
        experiment_start = time.monotonic()
        print(f"Results: {root}", flush=True)
        print(f"Results database: {db_path}", flush=True)
        if args.smoke:
            print("SMOKE MODE: one problem; two worker models x two skills conditions; "
                  "one repeat; independent blind reviewer ENABLED. "
                  "Independent verification optional; unconfigured tasks remain UNVERIFIED. "
                  "Real Copilot usage, including referee, may incur charges.", flush=True)
        print(f"Gladiators: {len(matrix)} worker runs; {len({t[0]['id'] for t in matrix})} task(s); "
              f"referee {'enabled' if reviewer_enabled else 'disabled'}; "
              f"heartbeat every {progress_interval:g}s" if progress_interval else
              f"Gladiators: {len(matrix)} worker runs; periodic heartbeat disabled", flush=True)
        if int(exp.get("parallel_sim_runs", 1)) != 1:
            print("NOTE: v1 deliberately runs sequentially, despite parallel_sim_runs; "
                  "fairness and shared Copilot throttling outweigh throughput.", flush=True)
        for i, (task, label, model, cond, trial) in enumerate(matrix, start=1):
            print(f"[{i}/{len(matrix)} | {100*(i-1)/len(matrix):.0f}% complete] "
                  f"START {task['id']} | {label} | {cond} | trial {trial}", flush=True)
            result = run_one(conf, config_path, root, task, label, model, cond, trial,
                             skills_dir=skills_dir,
                             progress=lambda message: print(message, flush=True),
                             progress_interval=progress_interval,
                             require_verifier_output=bool(args.smoke and task.get("verify_commands")))
            all_results.append(result)
            index_experiment(db, root)  # Keep partial results queryable if interrupted.
            if result.get("error"):
                print(f"  ERROR: {result['error']}", flush=True)
            print(f"[{i}/{len(matrix)} | {100*i/len(matrix):.0f}% complete] "
                  f"DONE {result.get('validation', {}).get('status')} "
                  f"(worker {result.get('invocation', {}).get('elapsed_seconds', 'n/a')}s; "
                  f"total elapsed {elapsed_display(time.monotonic()-experiment_start)})", flush=True)
        reviews = []
        if reviewer_enabled:
            review_tasks = selected_conf["tasks"]
            for j, task in enumerate(review_tasks, start=1):
                print(f"[referee {j}/{len(review_tasks)}] START {task['id']}", flush=True)
                review = judge_task(conf, config_path, root, task, all_results,
                                    progress=lambda message: print(message, flush=True),
                                    progress_interval=progress_interval)
                reviews.append(review)
                index_experiment(db, root)
                print(f"[referee {j}/{len(review_tasks)}] DONE {review.get('status', 'unknown')}", flush=True)
        print("[report] creating management-facing REPORT.rst", flush=True)
        result_path = report(root, selected_conf, all_results, reviews)
        index_experiment(db, root)
        print(f"Complete after {elapsed_display(time.monotonic()-experiment_start)}. "
              f"Verified passes: {sum(r.get('validation', {}).get('status') == 'passed' for r in all_results)}"
              f"/{len(all_results)}. Report: {result_path}", flush=True)
        print(f"History: python3 benchmark.py history | "
              f"Trends: python3 benchmark.py trend --task {matrix[0][0]['id']}", flush=True)
        if any(r.get("error") or r.get("requested_model_mismatch") for r in all_results):
            print("ERROR: Experiment incomplete. One or more worker runs failed; "
                  "review the errors and REPORT.rst before comparing conditions.", file=sys.stderr)
            return 1
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, FileNotFoundError, KeyError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(2)
