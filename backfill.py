#!/usr/bin/env python3
"""Backfill: run Data Workflow + Assignments for each missed day.

Downloads Mode/Simplify once fresh (for the first day), then reuses
the cached files for every subsequent day to avoid redundant downloads.
"""
from __future__ import annotations

import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
CREDS = str(APP_DIR / "service_account_key.json")

RAW_HISTORY_SPREADSHEET_ID = "1whZ27g2ir6OP-ncmW9kA6R43CWMuFPsiK-IFLQoqi-8"
DEFAULT_TARGET_SPREADSHEET_ID = "1gMYap7mOK17l7lOhPtBohGjJoC1FsPWfjyywXwFjLWk"
RAW_DATA_FOLDER_ID = "1nPq1cEdPRlE5irYyetqYi24uTySFHv0J"
ASSIGNMENT_FOLDER_ID = "1m-4NWsTQUQ51mJiQfMD-emhvm0qN1Fc_"


def window_for(d: date) -> tuple[str, str]:
    days_since_saturday = (d.weekday() - 5) % 7
    current_sat = d - timedelta(days=days_since_saturday)
    win_start = current_sat - timedelta(days=7)
    win_end = current_sat + timedelta(days=13)
    return win_start.isoformat(), win_end.isoformat()


def snap(prefix: str, d: date, win_start: str, win_end: str) -> str:
    from datetime import datetime
    s = datetime.strptime(win_start, "%Y-%m-%d").strftime("%m/%d")
    e = datetime.strptime(win_end, "%Y-%m-%d").strftime("%m/%d")
    return f"{prefix} - {d.strftime('%A')} Marriott({s} - {e})"


def run(cmd: list[str], label: str) -> int:
    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"{'='*60}")
    print("CMD:", " ".join(cmd), "\n")
    result = subprocess.run(cmd, cwd=str(APP_DIR))
    if result.returncode != 0:
        print(f"\n[WARN] {label} exited with code {result.returncode}")
    return result.returncode


MISSED_DAYS = [date(2026, 5, 27) + timedelta(days=i) for i in range(7)]  # May 27 – Jun 2

for idx, day in enumerate(MISSED_DAYS):
    win_start, win_end = window_for(day)
    first = idx == 0
    skip_dl = ["--skip-downloads"] if not first else ["--force-fresh-mode-download", "--mode-max-retries", "5"]

    raw_snap = snap("Raw Data", day, win_start, win_end)
    asgn_snap = snap("Assignments", day, win_start, win_end)

    print(f"\n\n{'#'*60}")
    print(f"  Day {idx+1}/7: {day} ({day.strftime('%A')})")
    print(f"  Window: {win_start} → {win_end}")
    print(f"{'#'*60}")

    # --- Data Workflow ---
    data_cmd = [
        sys.executable, str(APP_DIR / "data_workflow.py"),
        "--workdir", str(APP_DIR),
        "--target-spreadsheet-id", RAW_HISTORY_SPREADSHEET_ID,
        "--google-credentials", CREDS,
        "--start-date", win_start,
        "--end-date", win_end,
        "--shared-drive-id", RAW_DATA_FOLDER_ID,
        "--snapshot-name", raw_snap,
        "--no-assignment-logic",
        "--append-raw-tabs",
        *skip_dl,
    ]
    rc = run(data_cmd, f"Data Workflow – {day}")
    if rc != 0:
        print(f"[ERROR] Data workflow failed for {day}. Skipping assignments for this day.")
        continue

    # --- Assignments (always reuse downloads) ---
    asgn_cmd = [
        sys.executable, str(APP_DIR / "assignments.py"),
        "--workdir", str(APP_DIR),
        "--target-spreadsheet-id", DEFAULT_TARGET_SPREADSHEET_ID,
        "--google-credentials", CREDS,
        "--start-date", win_start,
        "--end-date", win_end,
        "--shared-drive-id", ASSIGNMENT_FOLDER_ID,
        "--snapshot-name", asgn_snap,
        "--skip-downloads",
    ]
    run(asgn_cmd, f"Assignments – {day}")

print("\n\nBackfill complete.")
