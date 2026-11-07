"""UART/heartbeat implementation-v2: fixed build and simulation acceptance test.

Stdlib only. Also copied into worker projects as benchmark_check.py for local
feedback. Final verification always uses THIS harness copy and fresh artifacts
outside the worker workspace, never candidate scripts, binaries or result JSON.
"""
from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import time
from fractions import Fraction
from pathlib import Path

VERSION = "uart-heartbeat-implementation-v2"
TARGET = "XK-EVK-XU316"
SYSTEM_HZ = 600_000_000
REFERENCE_HZ = 100_000_000
STARTUP_CYCLES = 600_000              # 1 ms; same allowance for every submission
OBSERVATION_CYCLES = 15_000_000       # 25 ms of steady-state observation
TOTAL_CYCLES = STARTUP_CYCLES + OBSERVATION_CYCLES
BIT_CYCLES = Fraction(SYSTEM_HZ, 115_200)
HALF_CYCLES = SYSTEM_HZ // 2_000
UART = "tile[0]_XS1_PORT_1A"
SQUARE = "tile[0]_XS1_PORT_1D"
SOURCE_SUFFIXES = {".c", ".xc", ".cpp", ".cc", ".cxx", ".S"}


def protocol() -> dict:
    return {"id": VERSION, "target": TARGET, "system_hz": SYSTEM_HZ,
            "reference_hz": REFERENCE_HZ, "startup_cycles": STARTUP_CYCLES,
            "observation_cycles": OBSERVATION_CYCLES, "total_cycles": TOTAL_CYCLES,
            "uart_signal": UART, "square_signal": SQUARE,
            "uart_baud": 115_200, "uart_frame": "8N1", "uart_pattern": [0x55, 0xA5],
            "uart_frame_tolerance_percent": 0.5, "uart_edge_interval_tolerance_percent": 1,
            "square_tolerance_reference_ticks": 1,
            "verifier_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}


def validate_observations(observed: dict) -> None:
    """Fail closed on missing/malformed verifier receipts; never infer a pass."""
    if not isinstance(observed, dict) or not isinstance(observed.get("passed"), bool):
        raise ValueError("Independent verifier must supply an explicit boolean passed result")
    receipt = observed.get("protocol")
    if (not isinstance(receipt, dict) or receipt.get("id") != VERSION or
            not isinstance(receipt.get("verifier_sha256"), str) or
            not re.fullmatch(r"[0-9a-f]{64}", receipt["verifier_sha256"])):
        raise ValueError("Independent verifier protocol/fingerprint is missing or unsupported")
    if not isinstance(observed.get("metrics"), dict):
        raise ValueError("Independent verifier measurements are missing")
    if observed["passed"]:
        stages = {s.get("stage"): s for s in observed.get("steps", []) if isinstance(s, dict)}
        if (stages.get("build", {}).get("exit_code") != 0 or
                stages.get("simulation", {}).get("exit_code") != 1 or
                any(stages.get(name, {}).get("timed_out", True) for name in ("build", "simulation")) or
                observed["metrics"].get("observation_ms") != 25 or
                observed["metrics"].get("startup_allowance_ms") != 1):
            raise ValueError("A verifier pass requires fresh build, bounded simulation and fixed-window evidence")
    elif not observed.get("reason"):
        raise ValueError("Independent verification failure must include a reason")


def tool_environment(env: dict) -> dict:
    # Do not let a candidate-supplied Python module/config or compiler search path
    # replace the verifier/toolchain. XTC's activated vendor paths are retained.
    clean = dict(env)
    for key in ("PYTHONPATH", "PYTHONHOME", "CPATH", "C_INCLUDE_PATH", "CPLUS_INCLUDE_PATH",
                "LIBRARY_PATH", "LD_PRELOAD", "DYLD_INSERT_LIBRARIES"):
        clean.pop(key, None)
    return clean


def run_process(command: list[str], cwd: Path, env: dict, timeout: float, log: Path) -> dict:
    started = time.monotonic()
    child_env = dict(env, PWD=str(cwd.resolve()))
    with log.open("w", encoding="utf-8") as stream:
        process = subprocess.Popen(command, cwd=cwd, env=child_env, stdout=stream,
                                   stderr=subprocess.STDOUT, start_new_session=os.name != "nt")
        timed_out = False
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            if os.name != "nt":
                os.killpg(process.pid, signal.SIGTERM)
            else:
                process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                if os.name != "nt":
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
                process.wait()
    return {"exit_code": process.returncode, "timed_out": timed_out,
            "elapsed_seconds": round(time.monotonic() - started, 3)}


def validate_toolchain(env: dict) -> dict:
    paths = {name: shutil.which(name, path=env.get("PATH", "")) for name in ("xcc", "xsim")}
    if not all(paths.values()):
        raise ValueError("XTC must be activated: xcc and xsim are required")
    version = subprocess.run([paths["xcc"], "--version"], env=env, capture_output=True,
                             text=True, timeout=10)
    text = version.stdout + version.stderr
    if version.returncode or not re.search(r"XTC version:\s*15\.3\.1\b", text):
        raise ValueError("implementation-v2 requires XTC Tools 15.3.1 (fixed simulator/target contract)")
    return {**paths, "version": text.strip()}


def submission(workspace: Path, build: Path) -> tuple[list[str], list[str]]:
    """Copy source text only. No Makefile, candidate compiler flags or prebuilt code."""
    workspace = workspace.resolve()
    src = workspace / "src"
    if not src.is_dir() or src.is_symlink():
        raise ValueError("Submit application source/header files under src/")
    files = list(src.rglob("*"))
    if len(files) > 500:
        raise ValueError("Submission exceeds 500 source-tree entries")
    sources = []
    size = 0
    for path in sorted(files):
        if path.is_symlink() or not path.resolve().is_relative_to(src.resolve()):
            raise ValueError("Submission source tree must not contain symlinks")
        if not path.is_file():
            continue
        if path.suffix not in SOURCE_SUFFIXES | {".h", ".hpp", ".inc"}:
            continue
        size += path.stat().st_size
        if size > 4_000_000:
            raise ValueError("Submission exceeds 4 MB of source/header text")
        dest = build / "src" / path.relative_to(src)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, dest)
        if path.suffix in SOURCE_SUFFIXES:
            sources.append(str(dest))
    if not sources:
        raise ValueError("No C, C++, XC or assembly sources found under src/")
    manifest = workspace / "submission.json"
    libraries = []
    if manifest.exists():
        if manifest.is_symlink():
            raise ValueError("submission.json must not be a symlink")
        data = json.loads(manifest.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or set(data) - {"libraries"}:
            raise ValueError("submission.json supports only an optional libraries list")
        libraries = data.get("libraries", [])
        if (not isinstance(libraries, list) or len(libraries) > 20 or
                any(not isinstance(lib, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_+-]*", lib)
                    for lib in libraries)):
            raise ValueError("libraries must name installed XTC libraries, without paths or flags")
    return sources, ["-l" + lib for lib in libraries]


def read_trace(path: Path) -> dict[str, list[tuple[int, int | None]]]:
    names = {UART, SQUARE, UART + "_direction", SQUARE + "_direction"}
    symbols = {}
    events = {name: [] for name in names}
    timestamp = 0
    quantum = None
    scale_text = ""
    in_scale = False
    with path.open(encoding="utf-8") as stream:
        for raw in stream:
            line = raw.strip()
            if line.startswith("$timescale"):
                in_scale = True
                scale_text = line.removeprefix("$timescale")
            elif in_scale:
                scale_text += " " + line
            if in_scale:
                if "$end" in scale_text:
                    match = re.fullmatch(r"\s*(\d+)\s*ps\s*\$end\s*", scale_text)
                    if not match or int(match[1]) not in (1, 1666):
                        raise ValueError("Unsupported VCD timescale for pinned XTC 15.3.1/600 MHz target")
                    quantum = 1666 // int(match[1])
                    in_scale = False
                continue
            if line.startswith("$var"):
                fields = line.split()
                if len(fields) >= 6 and fields[4] in names:
                    if fields[2] != "1" or fields[3] in symbols or fields[4] in symbols.values():
                        raise ValueError("Ambiguous or non-scalar VCD output signal")
                    symbols[fields[3]] = fields[4]
            elif line.startswith("#"):
                if quantum is None:
                    raise ValueError("VCD is missing its timescale")
                raw_time = int(line[1:])
                if raw_time % quantum or raw_time // quantum < timestamp:
                    raise ValueError("Invalid or out-of-order VCD timestamp")
                timestamp = raw_time // quantum
            elif line and line[0] in "01xz" and line[1:] in symbols:
                name = symbols[line[1:]]
                value = int(line[0]) if line[0] in "01" else None
                if not events[name] or events[name][-1][1] != value:
                    events[name].append((timestamp, value))
    if not all(events.values()):
        raise ValueError("Missing UART/square port or direction trace")
    return events


def value_at(events: list[tuple[int, int | None]], timestamp: int) -> int | None:
    index = bisect.bisect_right([t for t, _ in events], timestamp) - 1
    return events[index][1] if index >= 0 else None


def check_trace(events: dict[str, list[tuple[int, int | None]]]) -> dict:
    start, end = STARTUP_CYCLES, TOTAL_CYCLES
    for name in (UART, SQUARE):
        if value_at(events[name + "_direction"], start) != 1:
            raise ValueError(f"{name} is not an output after the startup allowance")
        if any(value != 1 for t, value in events[name + "_direction"] if start <= t <= end):
            raise ValueError(f"{name} changed output direction during observation")
        if value_at(events[name], start) is None or any(value is None for t, value in events[name] if start <= t <= end):
            raise ValueError(f"Unknown level on {name} during observation")

    square = [(t, v) for t, v in events[SQUARE] if start <= t <= end]
    if len(square) < 49 or square[0][0] - start > HALF_CYCLES + 6 or end - square[-1][0] > HALF_CYCLES + 6:
        raise ValueError("Square output missing, stopped, or observation too short")
    square_error = 0
    for i, (timestamp, value) in enumerate(square):
        phase_error = abs(timestamp - square[0][0] - i * HALF_CYCLES)
        interval_error = abs(timestamp - square[i - 1][0] - HALF_CYCLES) if i else 0
        square_error = max(square_error, phase_error, interval_error)
        if phase_error > 6 or interval_error > 6 or value != (square[0][1] ^ (i % 2)):
            raise ValueError("Square-wave timing/jitter exceeds one 100 MHz tick (10 ns)")

    uart = [(t, v) for t, v in events[UART] if start <= t <= end]
    # Allow alignment to the first complete frame after startup, never search for
    # a convenient passing subsequence later in the capture. All subsequent edges,
    # including the final partial frame, must match the requested stream.
    anchors = [i for i, (t, v) in enumerate(uart) if v == 0 and t <= start + 10 * BIT_CYCLES]
    best = None
    for anchor in anchors:
        for first_byte in (0x55, 0xA5):
            cursor, frames, byte = anchor, 0, first_byte
            max_error = Fraction(0)
            try:
                while cursor < len(uart):
                    origin = uart[cursor][0]
                    bits = [0] + [(byte >> bit) & 1 for bit in range(8)] + [1]
                    previous = 1
                    expected = []
                    for bit, level in enumerate(bits):
                        if level != previous:
                            expected.append((bit, level))
                        previous = level
                    prior_bit, prior_time = 0, origin
                    for bit, level in expected:
                        ideal = origin + bit * BIT_CYCLES
                        if ideal > end:
                            if cursor != len(uart):
                                raise ValueError("Extra UART transitions at observation end")
                            break
                        if cursor >= len(uart):
                            raise ValueError("UART stopped before observation end")
                        timestamp, actual = uart[cursor]
                        # 0.5% frame-rate allowance plus 1% of one bit for local
                        # edge latency. Do not require one particular divider algorithm.
                        if actual != level or abs(timestamp - ideal) > BIT_CYCLES * Fraction(1, 100) + bit * BIT_CYCLES * Fraction(5, 1000):
                            raise ValueError("UART data/framing/edge timing mismatch")
                        if bit > prior_bit:
                            error = abs(Fraction(timestamp - prior_time, bit - prior_bit) - BIT_CYCLES) / BIT_CYCLES
                            max_error = max(max_error, error)
                            if error > Fraction(1, 100):
                                raise ValueError("UART bit interval differs by more than 1%")
                        prior_bit, prior_time = bit, timestamp
                        cursor += 1
                    frame_end = origin + 10 * BIT_CYCLES
                    if frame_end > end:
                        break
                    frames += 1
                    if cursor >= len(uart):
                        if end - frame_end > BIT_CYCLES:
                            raise ValueError("UART missing next frame / stopped output")
                        break
                    if abs(uart[cursor][0] - frame_end) > 10 * BIT_CYCLES * Fraction(5, 1000):
                        raise ValueError("UART frame gap or baud error exceeds 0.5%")
                    byte ^= 0xF0
                if frames < 280 or cursor != len(uart):
                    raise ValueError("Too few complete UART frames or extra transitions")
                best = {"uart_frames": frames, "uart_transitions_checked": len(uart) - anchor,
                        "uart_max_bit_interval_error_percent": round(float(max_error * 100), 6)}
                break
            except ValueError:
                continue
        if best is not None:
            break
    if best is None:
        raise ValueError("UART does not continuously alternate 0x55/0xA5 at 115200 baud, 8N1")
    return {**best, "square_edges": len(square), "square_half_periods": len(square) - 1,
            "square_max_error_reference_ticks": square_error / 6,
            "observation_ms": 25, "startup_allowance_ms": 1}


def resource_report(text: str) -> dict:
    threads, ram = {}, {}
    for tile, section in re.findall(r"Constraint check for tile\[(\d+)\]:(.*?)(?=Constraint check for tile|\Z)", text, re.S):
        cores = re.search(r"Cores available:\s*\d+,\s*used:\s*(\d+)", section)
        memory = re.search(r"Memory available:\s*\d+,\s*used:\s*(\d+)", section)
        if cores:
            threads[tile] = int(cores[1])
        if memory:
            ram[tile] = int(memory[1])
    return {"threads_per_tile": threads or None, "ram_bytes_per_tile": ram or None}


def verify(workspace: Path, output: Path, env: dict, timeout: float = 180,
           progress=None) -> dict:
    started = time.monotonic()
    output.mkdir(parents=True, exist_ok=True)
    artifact = Path(tempfile.mkdtemp(prefix="verification-", dir=output))
    result = {"passed": False, "protocol": protocol(), "metrics": {}, "steps": []}
    env = tool_environment(env)
    deadline = started + timeout
    try:
        tools = validate_toolchain(env)
        result["toolchain_version"] = tools["version"]
        sources, libraries = submission(workspace, artifact)
        xe = artifact / "application.xe"
        vcd = artifact / "outputs.vcd"
        command = [tools["xcc"], f"-target={TARGET}", "-O2", "-g", "-Wall", "-report",
                   "-I", str(artifact / "src"), *sources, *libraries, "-o", str(xe)]
        if progress:
            progress("  [verifier] fresh harness-owned XTC build")
        built = run_process(command, artifact, env, max(0.01, deadline - time.monotonic()), artifact / "build.log")
        result["steps"].append({"stage": "build", **built})
        if built["timed_out"] or built["exit_code"] or not xe.is_file():
            raise ValueError("Fresh application build failed or timed out; see build.log")
        result["metrics"].update(resource_report((artifact / "build.log").read_text()))
        if progress:
            progress("  [verifier] fixed simulation: 1 ms startup + 25 ms observation")
        command = [tools["xsim"], "--max-cycles", str(TOTAL_CYCLES), "--vcd-tracing",
                   f"-o {vcd} -tile tile[0] -ports", str(xe)]
        simulated = run_process(command, artifact, env, max(0.01, deadline - time.monotonic()), artifact / "simulator.log")
        result["steps"].append({"stage": "simulation", **simulated})
        diagnostic = (artifact / "simulator.log").read_text().strip()
        expected = f"xsim: error: C51188: maximum cycles reached ({TOTAL_CYCLES})"
        if simulated["timed_out"] or simulated["exit_code"] != 1 or diagnostic != expected:
            raise ValueError("Unexpected simulator exit, timeout or diagnostic; see simulator.log")
        if not vcd.is_file() or not vcd.stat().st_size:
            raise ValueError("Simulator did not produce a fresh waveform")
        result["metrics"].update(check_trace(read_trace(vcd)))
        result["passed"] = True
    except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
        result["reason"] = str(exc)
    result["elapsed_seconds"] = round(time.monotonic() - started, 3)
    result["metrics"]["evidence"] = [str(p.relative_to(output)) for p in artifact.iterdir() if p.is_file()]
    (artifact / "result.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, default=Path(".benchmark-check"))
    parser.add_argument("--timeout", type=float, default=180)
    args = parser.parse_args()
    result = verify(args.workspace.resolve(), args.output.resolve(), os.environ.copy(), args.timeout, print)
    print(json.dumps(result, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
