#!/usr/bin/env python3
"""Fake CLI for HARNESS UNIT TESTS ONLY. Not an XMOS model or verifier."""
import json
import os
import pathlib
import sys

args = sys.argv[1:]
if pathlib.Path(os.environ.get("PWD", "/")) != pathlib.Path.cwd():
    print("WORKSPACE ERROR: PWD differs from process cwd", file=sys.stderr)
    sys.exit(5)
if args[:2] == ["session", "export"] or args[:1] == ["export"]:
    native = (pathlib.Path.cwd() / ".opencode" / "skills").exists()
    parts = [{"type": "tool", "tool": "skill", "callID": "x1", "state": {
        "status": "completed", "input": {"id": "xmos-test"}}}] if native else []
    print(json.dumps({
        "session": {"id": "ses_fake01", "tokens": {"input": 1000, "output": 100,
                                                  "cache": {"read": 0, "write": 0}}, "cost": 0.01},
        "messages": [{"info": {"role": "assistant"}, "parts": parts}]
    }))
elif args[:1] == ["run"]:
    w = pathlib.Path.cwd()
    if (w / "candidates").exists():
        names = sorted(p.name for p in (w / "candidates").iterdir())
        data = {"candidates": [
            {"id": n, "ratings": {"architecture": 3}, "summary": "fake",
             "major_decisions": [], "risks": []} for n in names],
            "ranked_ids": names, "review_notes": "fake test"}
        (w / "review.json").write_text(json.dumps(data))
    else:
        (w / "main.c").write_text("int main(void){return 0;}\n")
        (w / "DESIGN.md").write_text("# Demo\nUnknown threads and memory.")
    print(json.dumps({"type": "step_start", "sessionID": "ses_fake01", "part": {"type": "step-start"}}))
else:
    sys.exit(1)
