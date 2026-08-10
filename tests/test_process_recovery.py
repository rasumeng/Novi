"""Milestone 5 Phase 6A — genuine process-boundary recovery.

Runs TWO genuinely separate Python interpreter processes:

    Process A (tests/recovery_driver.py --phase a)
        fresh stores → real Job → durable checkpoint (step 0 done) → exit
        WITHOUT finishing the Job (crash model)
        |
        v  disk
    Process B (tests/recovery_driver.py --phase b)
        fresh interpreter → same stores → load Task/Job/Checkpoint → resolve
        continuation → reopen a NEW attempt → resume → prove remaining steps
        execute (Step 1 is never skipped) and ExecutionHistory stays correct

This is NOT manager-A → manager-B inside one process: each phase is its own
Python process with its own module state. The test fails under the old
off-by-one (resolved next_step would be checkpoint.step + 1).
"""

import json
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DRIVER = PROJECT_ROOT / "tests" / "recovery_driver.py"


def _run_phase(phase: str, tmp: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(DRIVER), "--phase", phase, "--dir", str(tmp)],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        timeout=120,
    )


def _fail_detail(proc: subprocess.CompletedProcess) -> str:
    return (f"exit={proc.returncode}\nstdout:\n{proc.stdout}\n\n"
            f"stderr:\n{proc.stderr}")


def test_recovery_across_real_process_boundary(tmp_path):
    job_files = tmp_path / "jobs"
    assert not job_files.exists()                     # clean starting point

    # ── Process A: interrupted execution ─────────────────────────────────
    a = _run_phase("a", tmp_path)
    assert a.returncode == 0, "Process A failed:\n" + _fail_detail(a)

    report_a = json.loads((tmp_path / "report-a.json").read_text("utf-8"))
    original_id = report_a["job_id"]

    # Checkpoint must be durable on disk before Process B ever starts.
    job_file = job_files / f"{original_id}.json"
    cp_file = job_files / f"{original_id}.checkpoint.json"
    assert job_file.exists(), "original Job must be persisted on disk"
    assert cp_file.exists(), "checkpoint must be durably on disk"

    # ── Process B: fresh interpreter recovers ────────────────────────────
    b = _run_phase("b", tmp_path)
    assert b.returncode == 0, "Process B failed:\n" + _fail_detail(b)

    report = json.loads((tmp_path / "report.json").read_text("utf-8"))
    assert report["original_job_id"] == original_id
    assert report["resumed_job_id"] != original_id     # new attempt
    assert report["checkpoint_step"] == 1
    assert report["next_step"] == report["checkpoint_step"]  # no +1
    assert report["model_calls"] == 2                  # steps 1+2 only
    assert report["started_indexes"] == [1, 2]         # Step 1 never skipped
    assert report["history"] == [original_id, report["resumed_job_id"]]

    # Resumed attempt persisted as its own Job; original still recorded.
    resumed_file = job_files / f"{report['resumed_job_id']}.json"
    assert resumed_file.exists()
    assert job_file.exists()