"""Verifies a captured report snapshot against the running stack.

No snapshot is committed: `test_flows.py` deletes and re-creates qa_flows on every run, so its
account and category ids (and therefore the payloads) differ from run to run. Capture and verify
around a change instead — `report_snapshot.py capture --user admin --dir <path outside the repo>`
before a migration, `verify` after — and point this test at that directory when you want it in a
pytest run. It skips when the directory is missing.

    <venv>/bin/pytest qa/e2e/test_report_snapshot.py -q -p no:cacheprovider
Set REPORT_SNAPSHOT_DIR / REPORT_SNAPSHOT_USER to verify another capture (e.g. admin's, kept outside the repo).
"""
import os
import pathlib

import pytest
import requests

import report_snapshot as rs

ROOT = pathlib.Path(__file__).resolve().parents[2]
SNAP_DIR = pathlib.Path(os.environ.get("REPORT_SNAPSHOT_DIR", ROOT / "qa" / "fixtures" / "report-snapshots" / "qa_flows"))
USER = os.environ.get("REPORT_SNAPSHOT_USER", "qa_flows")


def test_report_payloads_unchanged():
    if not (SNAP_DIR / "manifest.json").exists():
        pytest.skip(f"no snapshot at {SNAP_DIR}")
    name, pw = rs.USERS[USER]
    s = requests.Session()
    r = rs.login_with_retry(lambda: s.post(f"{rs.BASE}/api/auth/login", json={"username": name, "password": pw}, timeout=30))
    if r.status_code != 200:
        pytest.skip(f"{USER} cannot sign in ({r.status_code})")
    newest, _ = rs.data_months(s)
    if newest is None:
        pytest.skip(f"{USER} has no transactions yet")
    assert rs.verify(USER, SNAP_DIR), "report payloads changed; see the mismatch list above"
