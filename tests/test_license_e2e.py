"""End-to-end licensing: run the real run.py binary the way a customer would.

Covers the whole commercial flow:
  trial (no key) → refusal when a license is required → paid key passes the
  startup gate → tampered and expired keys are refused.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
FAIL_MARKER = "could not validate a commercial license"


def _run_simon(env_extra: dict[str, str], timeout: int = 30) -> tuple[int, str]:
    """Start `run.py server` with licensing env overrides.

    Returns (exit_code, combined_output). Exit code -1 means "still running
    when the timeout hit" — i.e. it got PAST the license gate and into
    normal startup, which for the valid-key case is success.
    """
    env = dict(os.environ)
    env.update(env_extra)
    proc = subprocess.Popen(
        [sys.executable, "run.py", "server"],
        cwd=REPO, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    try:
        out, _ = proc.communicate(timeout=timeout)
        return proc.returncode, out
    except subprocess.TimeoutExpired:
        proc.kill()
        out, _ = proc.communicate()
        return -1, out


@pytest.fixture()
def pro_key() -> str:
    sys.path.insert(0, str(REPO / "tools"))
    from keygen import make_key  # vendor tool — tests only
    return make_key("pro", "customer@example.com", "2099-01-01")


def test_trial_runs_without_key() -> None:
    """Default personal use: no key, requirement off → starts fine."""
    code, out = _run_simon({"SIMON_REQUIRE_LICENSE": "false",
                            "SIMON_LICENSE_KEY": ""})
    assert FAIL_MARKER not in out


def test_required_but_no_key_refuses_to_start() -> None:
    code, out = _run_simon({"SIMON_REQUIRE_LICENSE": "true",
                            "SIMON_LICENSE_KEY": ""})
    assert code == 1
    assert FAIL_MARKER in out


def test_paid_key_passes_the_gate(pro_key: str) -> None:
    """The trial → paid upgrade: same binary, add a key, requirement on."""
    code, out = _run_simon({"SIMON_REQUIRE_LICENSE": "true",
                            "SIMON_LICENSE_KEY": pro_key})
    assert FAIL_MARKER not in out
    # Either still running (past the gate) or died later for an unrelated
    # reason (e.g. port busy) — never a licensing refusal.
    assert code != 1 or FAIL_MARKER not in out


def test_tampered_key_refused(pro_key: str) -> None:
    tampered = pro_key[:-4] + ("AAAA" if not pro_key.endswith("AAAA") else "BBBB")
    code, out = _run_simon({"SIMON_REQUIRE_LICENSE": "true",
                            "SIMON_LICENSE_KEY": tampered})
    assert code == 1
    assert FAIL_MARKER in out


def test_expired_key_refused() -> None:
    sys.path.insert(0, str(REPO / "tools"))
    from keygen import make_key
    expired = make_key("pro", "customer@example.com", "2020-01-01")
    code, out = _run_simon({"SIMON_REQUIRE_LICENSE": "true",
                            "SIMON_LICENSE_KEY": expired})
    assert code == 1
    assert FAIL_MARKER in out
