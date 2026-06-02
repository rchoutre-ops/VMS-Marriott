#!/usr/bin/env python3
"""Standalone daily runner – invoked by Replit cron.

Runs Data Workflow then Assignments for today's operating window,
exactly mirroring what the Flask scheduler does.
"""
from __future__ import annotations

import os
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

APP_DIR = Path(__file__).resolve().parent
SNAPSHOT_TIMEZONE = "Asia/Kolkata"

RAW_HISTORY_SPREADSHEET_ID = "1whZ27g2ir6OP-ncmW9kA6R43CWMuFPsiK-IFLQoqi-8"
DEFAULT_TARGET_SPREADSHEET_ID = "1gMYap7mOK17l7lOhPtBohGjJoC1FsPWfjyywXwFjLWk"
RAW_DATA_FOLDER_ID = "1nPq1cEdPRlE5irYyetqYi24uTySFHv0J"
ASSIGNMENT_FOLDER_ID = "1m-4NWsTQUQ51mJiQfMD-emhvm0qN1Fc_"


def load_env() -> None:
    """Load .env if present (Replit secrets win over .env values)."""
    candidates = [APP_DIR / ".env", APP_DIR.parent / ".env"]
    env_path = next((p for p in candidates if p.exists()), None)
    if env_path is None:
        return
    for raw in env_path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


def credential_path() -> Path:
    import json

    json_secret = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if json_secret:
        key_path = Path("/tmp/marriott_service_account_key.json")
        if not key_path.exists() or key_path.read_text() != json_secret:
            json.loads(json_secret)
            key_path.write_text(json_secret)
        return key_path
    return APP_DIR / "service_account_key.json"


def default_window() -> tuple[str, str]:
    today = datetime.now(ZoneInfo(SNAPSHOT_TIMEZONE)).date()
    days_since_saturday = (today.weekday() - 5) % 7
    current_sat = today - timedelta(days=days_since_saturday)
    start = current_sat - timedelta(days=7)
    end = current_sat + timedelta(days=13)
    return start.isoformat(), end.isoformat()


def snapshot_name(prefix: str, start: str, end: str) -> str:
    ist_now = datetime.now(ZoneInfo(SNAPSHOT_TIMEZONE))
    day = ist_now.strftime("%A")
    s = datetime.strptime(start, "%Y-%m-%d").strftime("%m/%d")
    e = datetime.strptime(end, "%Y-%m-%d").strftime("%m/%d")
    return f"{prefix} - {day} Marriott({s} - {e})"


def run(cmd: list[str], label: str) -> int:
    print(f"\n{'='*60}\n  {label}\n{'='*60}")
    print("CMD:", " ".join(cmd), "\n")
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    result = subprocess.run(cmd, cwd=str(APP_DIR), env=env)
    print(f"\n[{label}] exit code: {result.returncode}")
    return result.returncode


def main() -> None:
    load_env()
    creds = str(credential_path())
    start, end = default_window()
    raw_snap = snapshot_name("Raw Data", start, end)
    asgn_snap = snapshot_name("Assignments", start, end)

    ist_now = datetime.now(ZoneInfo(SNAPSHOT_TIMEZONE))
    print(f"Daily Marriott run – {ist_now.strftime('%A, %d %b %Y %I:%M %p IST')}")
    print(f"Window: {start} → {end}\n")

    # Step 1: Data Workflow (fresh download, append to raw history)
    data_cmd = [
        sys.executable, str(APP_DIR / "data_workflow.py"),
        "--workdir", str(APP_DIR),
        "--target-spreadsheet-id", RAW_HISTORY_SPREADSHEET_ID,
        "--google-credentials", creds,
        "--start-date", start,
        "--end-date", end,
        "--shared-drive-id", RAW_DATA_FOLDER_ID,
        "--snapshot-name", raw_snap,
        "--no-assignment-logic",
        "--append-raw-tabs",
        "--force-fresh-mode-download",
        "--mode-max-retries", "5",
    ]
    rc = run(data_cmd, "Data Workflow")
    if rc != 0:
        print(f"\n[ERROR] Data Workflow failed (exit {rc}). Skipping Assignments.")
        sys.exit(rc)

    # Step 2: Assignments (reuse downloads from step 1)
    asgn_cmd = [
        sys.executable, str(APP_DIR / "assignments.py"),
        "--workdir", str(APP_DIR),
        "--target-spreadsheet-id", DEFAULT_TARGET_SPREADSHEET_ID,
        "--google-credentials", creds,
        "--start-date", start,
        "--end-date", end,
        "--shared-drive-id", ASSIGNMENT_FOLDER_ID,
        "--snapshot-name", asgn_snap,
        "--skip-downloads",
    ]
    rc = run(asgn_cmd, "Assignments")
    sys.exit(rc)


if __name__ == "__main__":
    main()
