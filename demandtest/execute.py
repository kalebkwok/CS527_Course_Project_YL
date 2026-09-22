"""S4 pass 5–6 — write, compile and run one test class (SPEC §2.8.5–2.8.6), plus the static
`target_hit` check of §2.8.5 (0.2.2): does the final source call the focal method and show
oracle evidence for every oracle need? A passing test that does neither is not a success
(TestTailor's coverage-accuracy analog, Zhou et al. FSE 2026)."""
from __future__ import annotations

import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .demand import Task
from .proximal import ORACLE_EVIDENCE

DEFAULT_TEST_ROOT = "src/test/java"
DEFAULT_TIMEOUT_S = 900
_AT_FRAME_LIMIT = 5
_JAVAC_BLOCK_LINES = 8


@dataclass
class Verdict:
    compiled: int
    passed: int
    wall_ms: int
    diagnostics: str = ""
    n_asserts: int = 0
    test_path: Optional[str] = None
    raw_tail: str = ""
    notes: str = ""


def class_name(src: str, fallback: str = "GeneratedTest") -> str:
    m = re.search(r"\b(?:class|interface|enum|record)\s+(\w+)", src)
    return m.group(1) if m else fallback


def package_of(task: Task) -> str:
    return task.focal_class.rsplit(".", 1)[0] if "." in task.focal_class else ""


def count_asserts(src: str) -> int:
    n = len(re.findall(
        r"\bassert(?:Equals|NotEquals|True|False|Null|NotNull|NotSame|Same|Throws|ArrayEquals|That|"
        r"IterableEquals|Between|All|InstanceOf|DoesNotThrow)\s*\(", src))
    n += len(re.findall(r"(?m)^\s*assert\s", src))  # the java `assert` keyword
    return n


def target_hit(src: str, task: Task, demands) -> int:
    """1 iff `src` calls the focal method and every oracle need has evidence (§2.8.5). Static, no execution."""
    name = re.escape(task.focal_method)
    if not (re.search(rf"\.{name}\s*\(", src) or re.search(rf"::\s*{name}\b", src)
            or (task.focal_method == "<init>")):
        return 0
    oracles = [n for n in demands if n.kind == "oracle"]
    if not oracles:
        return int(count_asserts(src) > 0)
    return int(all(ORACLE_EVIDENCE.get(n.detail) is not None and ORACLE_EVIDENCE[n.detail].search(src)
                   for n in oracles))


def write_test(repo: str, task: Task, src: str, test_root: str = DEFAULT_TEST_ROOT) -> Path:
    """Place the file under the focal package path (also grants package-private access)."""
    pkg_path = package_of(task).replace(".", "/")
    directory = Path(repo) / test_root / pkg_path
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{class_name(src)}.java"
    path.write_text(src, encoding="utf-8")
    return path


def remove_test(repo: str, test_path) -> None:
    if not test_path:
        return
    try:
        Path(test_path).unlink()
    except FileNotFoundError:
        pass


def maven_command(cls: str, module: Optional[str] = None) -> list[str]:
    cmd = ["mvn", "-q", "-B", "-o"]
    if module:
        cmd += ["-pl", module, "-am"]
    cmd += [f"-Dtest={cls}", "-DfailIfNoTests=false", "-Dsurefire.failIfNoSpecifiedTests=false", "test"]
    return cmd


def parse_verdict(log: str, exit_code: int, timed_out: bool = False) -> tuple[bool, bool, str]:
    """Verdict rules of §2.8.5 plus diagnostics trimming of §2.8.6."""
    compiled = (not timed_out) and ("COMPILATION ERROR" not in log) and ("cannot find symbol" not in log)
    if timed_out:
        passed = False
        diagnostics = f"timeout: {log.strip().splitlines()[-1] if log.strip() else 'no output'}"
        return compiled, passed, diagnostics
    run_line = re.search(r"Tests run: (\d+), Failures: (\d+), Errors: (\d+)", log)
    if exit_code == 0:
        passed = not (run_line and (int(run_line.group(2)) > 0 or int(run_line.group(3)) > 0))
    else:
        passed = False
    diagnostics = "" if passed else _trim_failure(log)
    return compiled, passed, diagnostics


def _trim_failure(log: str) -> str:
    """First javac error block (8 lines) or first failure line + <=5 `at ...` frames (§2.8.6)."""
    lines = log.splitlines()
    for i, ln in enumerate(lines):
        if "COMPILATION ERROR" in ln:
            return "\n".join(lines[i:i + _JAVAC_BLOCK_LINES]).strip()
    for i, ln in enumerate(lines):
        if re.search(r"\.java:\[\d+[,\d]*\]", ln):
            return "\n".join(lines[i:i + _JAVAC_BLOCK_LINES]).strip()
    for i, ln in enumerate(lines):
        if "<<< FAILURE!" in ln or "<<< ERROR!" in ln or re.search(r"\w+Test\.\w+:\d+", ln):
            block = [ln.strip()]
            for nxt in lines[i + 1:i + 3]:  # the exception message line, when present
                text = nxt.strip()
                if text and not text.startswith(("at ", "[")):
                    block.append(text)
                    break
            block += [l.strip() for l in lines[i + 1:] if l.strip().startswith("at ")][:_AT_FRAME_LIMIT]
            return "\n".join(block)
    tail = [l for l in lines if l.strip()][-12:]
    return "\n".join(tail)


def compile_and_run(repo: str, task: Task, src: str, test_root: str = DEFAULT_TEST_ROOT,
                    timeout_s: int = DEFAULT_TIMEOUT_S, module: Optional[str] = None,
                    keep: bool = False) -> Verdict:
    test_path = write_test(repo, task, src, test_root)
    cls = class_name(src)
    cmd = maven_command(cls, module)
    env = os.environ.copy()
    env["MAVEN_OPTS"] = "-Xmx2g"
    started = time.monotonic()
    timed_out = False
    try:
        proc = subprocess.run(cmd, cwd=repo, env=env, capture_output=True, text=True, timeout=timeout_s)
        log = (proc.stdout or "") + (proc.stderr or "")
        exit_code = proc.returncode
    except subprocess.TimeoutExpired as e:
        out = e.stdout or ""
        err = e.stderr or ""
        if isinstance(out, bytes):
            out = out.decode("utf-8", errors="replace")
        if isinstance(err, bytes):
            err = err.decode("utf-8", errors="replace")
        log = out + err
        exit_code = -1
        timed_out = True
    except FileNotFoundError as e:
        wall_ms = int((time.monotonic() - started) * 1000)
        return Verdict(0, 0, wall_ms, f"maven not available: {e}", count_asserts(src), str(test_path), "", "no-maven")
    wall_ms = int((time.monotonic() - started) * 1000)
    compiled, passed, diagnostics = parse_verdict(log, exit_code, timed_out)
    if not keep:
        remove_test(repo, test_path)
    return Verdict(int(compiled), int(passed), wall_ms, diagnostics, count_asserts(src), str(test_path), log[-4000:])
