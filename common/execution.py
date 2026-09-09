"""HumanEval test execution for Stage 0 scoring.

Mechanism, described precisely:
    HumanEval reference tests (the dataset's `test` field, which defines
    `check(candidate)`) are executed against the frozen candidate in a
    fresh Python subprocess, with a 15-second per-task timeout:

        program = candidate_code + test_code + "check(<entry_point>)"
                  + print('PASS')

    baseline_correct = (returncode == 0 and stdout ends with "PASS").

This is NOT the upstream human-eval package (which is not installed here);
it is the reference-test semantics run locally. There is deliberately:
  - NO SAFE_HEADER import preamble (the old harness silently repaired
    missing imports — a correctness-measurement confound);
  - NO custom AST policy / module blocklist (the old ast_guard rejected
    correct candidates on a security policy);
  - NO model-visible test output during Stage 0 (results are only stored).

A subprocess TIMEOUT is an incorrect solution (test_status="timeout",
baseline_correct=false), not an infrastructure exclusion, and is never
retried. Only true local infrastructure errors (failure to create the
temp file or spawn the subprocess) raise TestInfraError and are retried
by the caller (the identical test is re-run).
"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Tuple


class TestInfraError(RuntimeError):
    """Local test-runner infrastructure failure (spawn/IO), retryable."""


# ── Subprocess environment sanitization ───────────────────────────────
# Model-generated code must NEVER inherit the parent process's API
# credentials or unrelated secrets (the parent holds OPENAI_API_KEY).
# The child gets a small allowlisted environment only: no *_API_KEY, no
# *_TOKEN, no AWS/SSH/cloud credentials, nothing secret-bearing.
_ENV_ALLOWLIST = ("PATH", "LANG", "LC_ALL", "TMPDIR")


def sanitized_env() -> dict:
    env = {k: os.environ[k] for k in _ENV_ALLOWLIST if k in os.environ}
    # Deterministic hash randomization for reproducible execution.
    env["PYTHONHASHSEED"] = "0"
    return env


def build_program(candidate_code: str, test_code: str, entry_point: str) -> str:
    return (
        candidate_code
        + "\n\n"
        + test_code
        + "\n\ncheck("
        + entry_point
        + ")\nprint('PASS')\n"
    )


def test_python_provenance() -> dict:
    """Execution-environment provenance stored in each scored record."""
    return {
        "test_python_executable": sys.executable,
        "test_python_version": platform.python_version(),
    }


def run_tests_once(
    candidate_code: str,
    test_code: str,
    entry_point: str,
    timeout_s: float,
) -> Tuple[str, str, bool, float]:
    """Run the reference tests once against the frozen candidate.

    Returns (test_status, test_details, baseline_correct, duration_s).
    test_status in {"pass", "fail_assertion", "fail_error", "timeout"}.
    Raises TestInfraError for local infrastructure failures only.
    """
    program = build_program(candidate_code, test_code, entry_point)
    tmpdir = None
    try:
        tmpdir = tempfile.mkdtemp(prefix="hev2_test_")
        path = os.path.join(tmpdir, "program.py")
        with open(path, "w", encoding="utf-8") as f:
            f.write(program)
    except OSError as exc:
        if tmpdir:
            shutil.rmtree(tmpdir, ignore_errors=True)
        raise TestInfraError(f"temp file creation failed: {exc}") from exc

    t0 = time.time()
    try:
        proc = subprocess.run(
            [sys.executable, path],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            cwd=tmpdir,
            stdin=subprocess.DEVNULL,
            env=sanitized_env(),
        )
    except subprocess.TimeoutExpired:
        return "timeout", f"exceeded {timeout_s}s", False, round(time.time() - t0, 2)
    except OSError as exc:
        raise TestInfraError(f"subprocess spawn failed: {exc}") from exc
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    duration = round(time.time() - t0, 2)
    stdout = proc.stdout or ""
    stderr = (proc.stderr or "").strip()

    if proc.returncode == 0 and stdout.strip().endswith("PASS"):
        return "pass", "pass", True, duration

    if "AssertionError" in stderr:
        last = stderr.splitlines()[-1][:200] if stderr.splitlines() else ""
        return "fail_assertion", f"assertion failed: {last}", False, duration

    details = stderr[-300:] if stderr else f"exit code {proc.returncode}"
    return "fail_error", details, False, duration
