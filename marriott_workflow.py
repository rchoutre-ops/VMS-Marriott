#!/usr/bin/env python3
"""Marriott data workflow - one-button pipeline.

This is the single entry point for the Marriott raw-data refresh.
Running this file end-to-end:

    1. Logs into Mode and downloads the latest VMS - Marriott export
       (or reuses the existing ZIP with --skip-downloads).
    2. Logs into Simplify VMS and downloads:
         - Active Assignments Details - Vendor
         - Candidate Details
         - Job Status Report
    3. Transforms each source into the seven raw tabs Marriott ops needs.
    4. Uploads everything to the target Google Sheet.
    5. Snapshots a dated copy into the Marriott shared drive named like
       "Tuesday Marriott(05/09 - 05/29)" using the IST day-of-week.

Run:

    export MODE_API_KEY_ID="..."
    export MODE_API_KEY_SECRET="..."
    export SIMPLIFY_EMAIL="..."
    export SIMPLIFY_PASSWORD="..."
    python3 marriott_workflow.py

Target stage documented by this script:

    https://docs.google.com/spreadsheets/d/1gMYap7mOK17l7lOhPtBohGjJoC1FsPWfjyywXwFjLWk/edit

What this creates/updates (lowercased tab names match today's ops workbook):

    Input/source tabs:
      - raw data            (Mode export, unmodified, 21 cols)
      - Mode                (raw data + normalized + W..AM decision columns)
      - Open & Closed       (Simplify Active Assignments Details - Vendor)
      - Open Active         (Status = Open subset, with Con1 / Con 2 helper)
      - candidate details   (Simplify Candidate Details)
      - job status          (Simplify Job Status Report)

    Assignment output tabs:
      - upload              (rows ready to push to Simplify; manual Avail Jobs)
      - job request         (rows needing a new job posting)
      - can upload          (pros needing a new Candidate ID)
      - can output          (can upload + returned Candidate ID / Remarks)
      - Output              (post-import tracking - 17 cols)
      - Sheet8              (candidate creation source - id/name/email/ssn/...)
      - Summary             (operator scratch)

Sources:

    1. Mode report:
       https://app.mode.com/instawork/reports/9b580f8ef3ca

       The current stage used successful Mode report run:
       5742a194efc9

       The Marriott raw data file inside the Mode ZIP is the `VMS - Marriott`
       query export, whose token appears in the filename as:
       4e4a50423645

    2. Simplify All Reports:
       https://marriott.simplifyvmsapp.com/Report/EmbeddedReports/embeddedIndex

       Reports used:
       - Active Assignments Details - Vendor
       - Candidate Details
       - Job Status Report

Credential handling:

    Do not hardcode credentials in this file. Export them before running:

        export MODE_API_KEY_ID="..."
        export MODE_API_KEY_SECRET="..."
        export SIMPLIFY_EMAIL="..."
        export SIMPLIFY_PASSWORD="..."

    The Google service account key path defaults to:

        ./service_account_key.json

Exact filters used for the current stage:

    - Mode covers a Saturday to Friday operating week. The default date range
      is 2026-05-09 through 2026-05-29, which spans 3 consecutive weeks:
        Past 1:  Sat 2026-05-09 to Fri 2026-05-15
        Current: Sat 2026-05-16 to Fri 2026-05-22
        Future 1: Sat 2026-05-23 to Fri 2026-05-29
    - Weekends are kept (Marriott shifts run on weekends too).
    - `Raw Data` and `Mode` contain the same Mode data filtered to this range.
      For comparison, in the final reference workbook, Raw Data has a slightly
      wider range than Mode (Raw Data: 2026-05-02 to 2026-05-29; Mode:
      2026-05-09 to 2026-05-29). This script keeps them equal because our
      local Mode export only covers 2026-05-09 to 2026-05-29.
    - `Open & Closed` keeps Simplify assignment rows with statuses:
      Open, Closed, Cancelled.
    - `Open Active` is derived from `Open & Closed` where status is Open.
      It adds helper keys:
        Concat1 = Department Name + Vendor Tracking ID 1 + Client ST Bill Rate
        Concat2 = Work Location ID + Vendor Tracking ID 1 + Client ST Bill Rate
    - `CAN Details` keeps rows with Candidate Ref ID. By default only active
      candidates (Is Active? = Yes) are kept to mirror the final workbook.
    - `Jobs` keeps rows with Job ID. By default only Sourcing jobs whose Job
      End Date is on or after the run date (jobs still open for assignment)
      are kept. Pass --keep-all-jobs to skip that filter.

AID (Assignment ID) matching logic in the final Mode tab:

    The final Mode tab uses two AID lookups against `Open Active`:

    Perfect Match (col W) = CONCATENATE(Department Code, worker_id, partner_rate)
    Perfect AID (col X)   = open assignment ID where
                              Open Active Concat1 = Perfect Match AND
                              shift date is within Assignment Start/End Date.
                              Returns 0 when nothing matches.

    2nd Best Match (col Y) = CONCATENATE(fieldglass_site_name, worker_id, partner_rate)
    2nd Best AID  (col Z)  = open assignment ID where
                              Open Active Concat2 = 2nd Best Match AND
                              shift date is within Assignment Start/End Date.
                              Returns 0 when nothing matches.

    Interpretation:

      Perfect AID != 0
        The pro already has an open assignment at the exact department/rate.
        Action: do nothing. They are correctly assigned.

      Perfect AID = 0 AND 2nd Best AID != 0
        The pro has an open assignment at the same property/rate but the
        department on file does not match the Mode department.
        validation_for_Department (col AA) compares Mode Department Code
        against the department on the 2nd Best AID and returns OK,
        Not OK, or AID Not Found.
        Action: typically reassign / fix the existing assignment rather
        than create a new one. Existing Jobs (col AF) is the job ID
        associated with the 2nd Best AID.

      Perfect AID = 0 AND 2nd Best AID = 0
        This is a direct-assignment candidate. To create a new assignment:
          - CAN ID (col AB) must exist. It is looked up from
            Open & Closed by worker_id / Vendor Tracking ID 1, OR
            created via the Sheet9 -> CAN Upload -> CAN Output flow when
            the worker is new (no prior Marriott assignment).
          - Available Jobs (col AC) must hold a valid Job ID from the
            Sourcing Jobs tab.

        Sub-cases when both AIDs are 0:
          - CAN ID present and Available Jobs present -> row goes to the
            `Upload` tab for Simplify import.
              Start Date (col AJ) = Jobs.Job Start Date for that Job ID.
              End Date   (col AK) = Jobs.Job End Date for that Job ID.
              Shift Start Date (col AL) = TEXT(date_of_shift_start,
                                              "mm/dd/yyyy").
          - CAN ID present but no suitable Available Jobs -> row goes to
            the `Job Request` tab so a new job posting can be created.
          - CAN ID missing -> create a candidate via Sheet9 / CAN Upload,
            then place the returned Candidate ID back into Mode and
            continue.

      Notes:
        - Column AB (CAN ID), col X (Perfect AID), and col AC (Available
          Jobs) can be manually overridden in the final workbook. Most
          cells are formula-driven, but a small number are typed in.

Important Simplify note:

    The whole-workbook Simplify export returned an XLSX with "No Data".
    The useful files came from the Sigma page export endpoint:

        https://api-v3reporting.simplifyvmsapp.com/api/sigma/workbook/{report_id}/csv_post?email=...

    During the original run, page 1 exported successfully for each report.
    Page 2 returned HTTP 500 and was skipped.

Reproduce the current stage:

    python3 marriott_workflow.py

Reuse existing downloads without logging into Mode/Simplify:

    python3 marriott_workflow.py --skip-downloads

Change target sheet or date range:

    python3 marriott_workflow.py \\
      --target-spreadsheet-id 1gMYap7mOK17l7lOhPtBohGjJoC1FsPWfjyywXwFjLWk \\
      --start-date 2026-05-09 \\
      --end-date 2026-05-29

Restrict to Monday-Friday only (legacy behaviour):

    python3 marriott_workflow.py --weekdays-only
"""

from __future__ import annotations

import argparse
import math
import os
import re
import time
import zipfile
from datetime import date, datetime
from io import BytesIO
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests
from bs4 import BeautifulSoup
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from requests.auth import HTTPBasicAuth


DEFAULT_TARGET_SPREADSHEET_ID = "1gMYap7mOK17l7lOhPtBohGjJoC1FsPWfjyywXwFjLWk"
MARRIOTT_SHARED_DRIVE_ID = "0AFPXMexSsMIlUk9PVA"
SNAPSHOT_TIMEZONE = "Asia/Kolkata"
MODE_HOST = "https://modeanalytics.com"
MODE_WORKSPACE = "instawork"
MODE_REPORT_TOKEN = "9b580f8ef3ca"
MODE_RUN_TOKEN = "5742a194efc9"
MODE_MARRIOTT_QUERY_TOKEN = "4e4a50423645"
SIMPLIFY_BASE_URL = "https://marriott.simplifyvmsapp.com"
SIMPLIFY_REPORTS = (
    "Active Assignments Details - Vendor",
    "Candidate Details",
    "Job Status Report",
)


def load_local_env() -> None:
    """Load local .env values for CLI runs without overriding exported env vars."""
    candidates = []
    if os.environ.get("ENV_FILE"):
        candidates.append(Path(os.environ["ENV_FILE"]).expanduser())
    candidates.extend([Path(__file__).resolve().parent / ".env", Path(__file__).resolve().parent.parent / ".env"])

    env_path = next((path for path in candidates if path.exists()), None)
    if env_path is None:
        return

    for raw_line in env_path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


load_local_env()


def required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("_")


def column_name(column_index: int) -> str:
    name = ""
    column_index += 1
    while column_index:
        column_index, remainder = divmod(column_index - 1, 26)
        name = chr(65 + remainder) + name
    return name


def google_concat_value(value: Any) -> str:
    """Approximate Google Sheets CONCATENATE display values for helper keys.

    Google Sheets stores numeric strings as numbers under USER_ENTERED, so
    "24.50" becomes 24.5 and CONCATENATE renders it as "24.5". We mirror that
    so local pre-computation produces the same join keys.
    """
    if value is None or pd.isna(value):
        return ""
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (np.integer, int)):
        return str(int(value))
    if isinstance(value, (np.floating, float)):
        if math.isnan(float(value)):
            return ""
        if float(value).is_integer():
            return str(int(value))
        return f"{float(value):.10g}"
    if isinstance(value, str):
        s = value.strip()
        if s == "":
            return ""
        try:
            num = float(s)
        except ValueError:
            return s
        if math.isnan(num):
            return ""
        if num.is_integer():
            return str(int(num))
        return f"{num:.10g}"
    return str(value)


def clean_value(value: Any) -> Any:
    if value is None or pd.isna(value):
        return ""
    if isinstance(value, pd.Timestamp):
        if value.time() == datetime.min.time():
            return value.strftime("%Y-%m-%d")
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, datetime):
        if value.time() == datetime.min.time():
            return value.strftime("%Y-%m-%d")
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, np.generic):
        return value.item()
    return value


def df_to_values(df: pd.DataFrame) -> list[list[Any]]:
    headers = [str(column) for column in df.columns]
    rows = [[clean_value(value) for value in row] for row in df.itertuples(index=False, name=None)]
    return [headers] + rows


def execute_google_request(request: Any, description: str, attempts: int = 6) -> Any:
    """Execute a Google API request with backoff for short quota bursts."""
    for attempt in range(1, attempts + 1):
        try:
            return request.execute()
        except HttpError as exc:
            status = getattr(exc.resp, "status", None)
            if status not in {429, 500, 503} or attempt == attempts:
                raise
            wait_seconds = min(60, 5 * attempt)
            print(f"{description} hit Google API HTTP {status}; retrying in {wait_seconds}s ({attempt}/{attempts})")
            time.sleep(wait_seconds)
    raise RuntimeError(f"Google API request did not complete: {description}")


def _mode_auth() -> HTTPBasicAuth:
    return HTTPBasicAuth(required_env("MODE_API_KEY_ID"), required_env("MODE_API_KEY_SECRET"))


def _mode_request(method: str, url: str, *, attempts: int = 4, **kwargs: Any) -> requests.Response:
    """Call Mode with small retries for transient network/DNS failures."""
    import time

    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            response = requests.request(method, url, **kwargs)
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            last_error = exc
            if attempt == attempts:
                raise
            wait_seconds = min(5 * attempt, 20)
            print(
                f"Mode API {method.upper()} failed on attempt {attempt}/{attempts}: {exc}. "
                f"Retrying in {wait_seconds}s..."
            )
            time.sleep(wait_seconds)

    raise RuntimeError(f"Mode API request failed unexpectedly: {last_error}")


def _find_marriott_query_run(
    args: argparse.Namespace,
    auth: HTTPBasicAuth,
    pages: int = 6,
) -> tuple[str, str, str] | None:
    """Walk recent report runs and find the latest one where the VMS - Marriott
    query (token = args.mode_query_token) succeeded with our requested date range.

    Returns (run_token, query_run_token, completed_at_iso) or None.

    Important: Mode marks the whole report run as 'failed' if ANY sub-query fails
    (e.g. Main Chart hitting Redshift NestedLoopOver50K). The Marriott sub-query
    can still be 'succeeded' inside that 'failed' report run. We don't care about
    the report-level state; we only need our query to have finished.
    """
    host = MODE_HOST
    ws = MODE_WORKSPACE
    report = args.mode_report_token
    start = args.start_date
    end = args.end_date

    for page in range(1, pages + 1):
        resp = _mode_request(
            "get",
            f"{host}/api/{ws}/reports/{report}/runs",
            params={"page": page},
            auth=auth,
            timeout=30,
        )
        runs = resp.json().get("_embedded", {}).get("report_runs", [])
        if not runs:
            return None
        for run in runs:
            params = run.get("parameters") or {}
            ev = params.get("enterprise_VMS_name")
            if isinstance(ev, list):
                ev = ev[0] if ev else None
            if ev != "enterprise_vmsmarriott":
                continue
            if params.get("start_date") != start or params.get("end_date") != end:
                continue
            run_token = run["token"]
            qr = _mode_request(
                "get",
                f"{host}/api/{ws}/reports/{report}/runs/{run_token}/query_runs",
                auth=auth,
                timeout=30,
            )
            for q in qr.json().get("_embedded", {}).get("query_runs", []):
                if q.get("query_token") == args.mode_query_token and q.get("state") == "succeeded":
                    return run_token, q["token"], run.get("completed_at") or ""
    return None


def _trigger_marriott_run(args: argparse.Namespace, auth: HTTPBasicAuth, max_wait_seconds: int = 900) -> str | None:
    """Trigger a fresh report run for our date range. Returns the new run_token
    when the run terminates (succeeded OR failed) so the caller can re-scan for
    a successful VMS - Marriott query_run inside it.
    """
    import time

    host = MODE_HOST
    ws = MODE_WORKSPACE
    report = args.mode_report_token
    body = {
        "parameters": {
            "enterprise_VMS_name": ["enterprise_vmsmarriott"],
            "start_date": args.start_date,
            "end_date": args.end_date,
            "company": "",
        }
    }
    resp = requests.post(
        f"{host}/api/{ws}/reports/{report}/runs", auth=auth, json=body, timeout=60
    )
    if not resp.ok:
        print(f"Mode run trigger failed: HTTP {resp.status_code} {resp.text[:200]}")
        return None
    run_token = resp.json()["token"]
    print(f"Triggered new Mode run: {run_token}")

    deadline = time.time() + max_wait_seconds
    while time.time() < deadline:
        time.sleep(15)
        sr = _mode_request(
            "get",
            f"{host}/api/{ws}/reports/{report}/runs/{run_token}",
            auth=auth,
            timeout=30,
        ).json()
        state = sr.get("state")
        print(f"  poll {run_token} state={state}")
        if state in ("succeeded", "failed", "cancelled"):
            return run_token
    print(f"Mode run {run_token} did not finish within {max_wait_seconds}s")
    return run_token


def _find_marriott_query_run_for_run(
    args: argparse.Namespace,
    auth: HTTPBasicAuth,
    run_token: str,
) -> tuple[str, str, str] | None:
    """Return the Marriott query_run for one specific Mode report run."""
    run_resp = _mode_request(
        "get",
        f"{MODE_HOST}/api/{MODE_WORKSPACE}/reports/{args.mode_report_token}/runs/{run_token}",
        auth=auth,
        timeout=30,
    )
    completed_at = run_resp.json().get("completed_at") or ""

    qr = _mode_request(
        "get",
        f"{MODE_HOST}/api/{MODE_WORKSPACE}/reports/{args.mode_report_token}/runs/{run_token}/query_runs",
        auth=auth,
        timeout=30,
    )
    for query_run in qr.json().get("_embedded", {}).get("query_runs", []):
        if query_run.get("query_token") == args.mode_query_token and query_run.get("state") == "succeeded":
            return run_token, query_run["token"], completed_at
    return None


def download_mode_export(args: argparse.Namespace) -> Path:
    """Download the latest Marriott CSV by reaching directly into a successful
    VMS - Marriott query_run, regardless of whether the parent report run is
    flagged as failed.

    Strategy:
      1. Scan recent report runs for our date range; pick the most recent one
         where the VMS - Marriott query_run is 'succeeded'.
      2. If none, trigger a fresh report run and rescan. We attempt up to
         args.mode_max_retries fresh triggers.
      3. Download the CSV from /query_runs/{token}/results/content.csv.
    """
    mode_dir = args.workdir / "downloads" / "mode_raw"
    mode_dir.mkdir(parents=True, exist_ok=True)
    auth = _mode_auth()

    force_fresh = getattr(args, "force_fresh_mode_download", False)
    found = None
    attempts = 0
    if force_fresh:
        print(
            f"Forcing a fresh Mode run for {args.start_date} to {args.end_date}; "
            "cached CSV files and prior matching report runs will not be reused."
        )
        while not found and attempts < args.mode_max_retries:
            attempts += 1
            run_token = _trigger_marriott_run(args, auth)
            if run_token:
                found = _find_marriott_query_run_for_run(args, auth, run_token)
            if not found and attempts < args.mode_max_retries:
                print(
                    "Fresh Mode run did not produce a succeeded VMS - Marriott query_run; "
                    f"retrying ({attempts + 1}/{args.mode_max_retries})"
                )
    else:
        found = _find_marriott_query_run(args, auth)
        while not found and attempts < args.mode_max_retries:
            attempts += 1
            print(
                f"No succeeded VMS - Marriott query_run for {args.start_date} to {args.end_date}; "
                f"triggering a fresh Mode run (attempt {attempts}/{args.mode_max_retries})"
            )
            _trigger_marriott_run(args, auth)
            found = _find_marriott_query_run(args, auth)
    if not found:
        if force_fresh:
            raise RuntimeError(
                "Could not obtain a fresh Mode CSV for the requested date range. "
                "The newly triggered Mode run did not produce a succeeded VMS - Marriott query_run. "
                "Check the Mode run for warehouse/query failure details, then retry."
            )
        raise RuntimeError(
            "Could not obtain a fresh Mode CSV for the requested date range. "
            "All recent report runs failed at the VMS - Marriott sub-query. "
            "Re-run later or run --skip-downloads to reuse the latest local CSV."
        )

    run_token, qr_token, completed_at = found
    out_dir = mode_dir / f"mode_marriott_run_{run_token}"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_csv = out_dir / f"vms_marriott_{run_token}_{qr_token}.csv"

    if force_fresh or not out_csv.exists():
        url = (
            f"{MODE_HOST}/api/{MODE_WORKSPACE}/reports/{args.mode_report_token}"
            f"/runs/{run_token}/query_runs/{qr_token}/results/content.csv"
        )
        resp = _mode_request("get", url, auth=auth, timeout=180)
        out_csv.write_bytes(resp.content)
        print(
            f"Downloaded fresh Mode CSV from run {run_token} query_run {qr_token} "
            f"({len(resp.content)} bytes, completed_at={completed_at})"
        )
    else:
        print(f"Reusing cached Mode CSV: {out_csv}")

    # Keep the current_csv pointer current so --skip-downloads finds it
    pointer = mode_dir / "current_marriott.csv"
    if pointer.exists() or pointer.is_symlink():
        pointer.unlink()
    try:
        pointer.symlink_to(out_csv.resolve())
    except OSError:
        pointer.write_bytes(out_csv.read_bytes())

    print(f"Selected Mode CSV: {out_csv}")
    return out_csv


def find_mode_csv(args: argparse.Namespace) -> Path:
    """Locate the most recent local Marriott CSV for --skip-downloads.

    Looks for the symlink written by download_mode_export first, then falls back
    to the newest vms_marriott_*.csv on disk, then to the legacy ZIP-extracted
    layout used by previous runs.
    """
    mode_dir = args.workdir / "downloads" / "mode_raw"
    pointer = mode_dir / "current_marriott.csv"
    if pointer.exists():
        target = pointer.resolve() if pointer.is_symlink() else pointer
        if target.exists():
            return target

    candidates = sorted(
        mode_dir.glob("mode_marriott_run_*/vms_marriott_*.csv"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if candidates:
        return candidates[0]

    legacy = sorted(
        mode_dir.glob(f"mode_marriott_run_*/*{args.mode_query_token}*.csv"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if legacy:
        return legacy[0]

    raise RuntimeError(f"No local Marriott CSV found under {mode_dir}")


def simplify_login(email: str, password: str) -> requests.Session:
    session = requests.Session()

    response = session.get(f"{SIMPLIFY_BASE_URL}/site/login", timeout=30)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    csrf = soup.find("input", {"name": "YII_CSRF_TOKEN"}).get("value")

    response = session.post(
        f"{SIMPLIFY_BASE_URL}/site/login",
        data={"YII_CSRF_TOKEN": csrf, "_uemail": email},
        timeout=30,
    )
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    csrf = soup.find("input", {"name": "YII_CSRF_TOKEN"}).get("value")
    role = soup.find("input", {"name": "_uloginas"}).get("value")

    response = session.post(
        f"{SIMPLIFY_BASE_URL}/site/login",
        data={
            "YII_CSRF_TOKEN": csrf,
            "_uemail": email,
            "_upassword": password,
            "_uloginas": role,
        },
        timeout=30,
        allow_redirects=True,
    )
    response.raise_for_status()
    if "/dashboard" not in response.url:
        raise RuntimeError(f"Unexpected Simplify login URL after login: {response.url}")

    return session


def list_simplify_reports(session: requests.Session) -> dict[str, str]:
    params = {
        "draw": "1",
        "start": "0",
        "length": "100",
        "search[value]": "",
        "search[regex]": "false",
        "order[0][column]": "0",
        "order[0][dir]": "asc",
    }
    for index in range(7):
        params.update(
            {
                f"columns[{index}][data]": str(index),
                f"columns[{index}][searchable]": "true",
                f"columns[{index}][orderable]": "true",
                f"columns[{index}][search][value]": "",
                f"columns[{index}][search][regex]": "false",
            }
        )

    response = session.get(
        f"{SIMPLIFY_BASE_URL}/Report/EmbeddedReports/embeddedIndexAjax1",
        params=params,
        headers={"X-Requested-With": "XMLHttpRequest"},
        timeout=30,
    )
    response.raise_for_status()

    reports: dict[str, str] = {}
    for row in response.json()["data"]:
        soup = BeautifulSoup(row[0], "html.parser")
        link = soup.find("a")
        if not link:
            continue
        reports[link.get_text(" ", strip=True)] = link.get("href")

    return reports


def extract_simplify_report_id(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    hidden = soup.find("input", {"id": "hiddenReportId"}) or soup.find("input", {"name": "report_id"})
    if hidden and hidden.get("value"):
        return hidden["value"]

    match = re.search(r"window\.reportConfig\s*=\s*\{.*?id:\s*\"([^\"]+)\"", html, flags=re.S)
    if match:
        return match.group(1)

    raise RuntimeError("Could not find Simplify report ID in workbook page")


def get_simplify_page_ids(session: requests.Session, report_id: str) -> list[str]:
    response = session.post(
        f"{SIMPLIFY_BASE_URL}/Report/EmbeddedReports/getReportPages",
        data={"id": report_id},
        headers={"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def download_simplify_reports(args: argparse.Namespace) -> dict[str, Path]:
    output_dir = args.workdir / "downloads" / "simplify_raw" / "pages"
    output_dir.mkdir(parents=True, exist_ok=True)

    session = simplify_login(required_env("SIMPLIFY_EMAIL"), required_env("SIMPLIFY_PASSWORD"))
    reports = list_simplify_reports(session)

    downloaded: dict[str, Path] = {}
    for report_name in SIMPLIFY_REPORTS:
        href = reports.get(report_name)
        if not href:
            raise RuntimeError(f"Could not find Simplify report: {report_name}")

        page = session.get(href, timeout=30)
        page.raise_for_status()
        report_id = extract_simplify_report_id(page.text)
        page_ids = get_simplify_page_ids(session, report_id)

        for page_index, page_id in enumerate(page_ids, start=1):
            body = {"pages": [{"page_id": page_id, "filters": {}}]}
            response = requests.post(
                (
                    "https://api-v3reporting.simplifyvmsapp.com/api/sigma/workbook/"
                    f"{report_id}/csv_post?email={args.simplify_export_email}"
                ),
                json=body,
                timeout=120,
            )

            if not response.ok:
                print(
                    f"Skipping {report_name} page {page_index} ({page_id}): "
                    f"HTTP {response.status_code}"
                )
                continue

            download_url = response.json()["download_url"]
            workbook = requests.get(download_url, timeout=120)
            workbook.raise_for_status()

            path = output_dir / f"{safe_name(report_name)}__page_{page_index}_{page_id}.xlsx"
            path.write_bytes(workbook.content)
            downloaded.setdefault(report_name, path)
            print(f"Downloaded {report_name} page {page_index}: {path}")

        if report_name not in downloaded:
            raise RuntimeError(f"No successful Simplify page export for {report_name}")

    return downloaded


def _latest_download(output_dir: Path, pattern: str) -> Path:
    candidates = sorted(output_dir.glob(pattern), key=lambda path: path.stat().st_mtime, reverse=True)
    if not candidates:
        raise RuntimeError(f"No Simplify download found for pattern {pattern} under {output_dir}")
    return candidates[0]


def find_existing_simplify_downloads(args: argparse.Namespace) -> dict[str, Path]:
    output_dir = args.workdir / "downloads" / "simplify_raw" / "pages"
    return {
        "Active Assignments Details - Vendor": _latest_download(
            output_dir, "Active_Assignments_Details_-_Vendor__page_1_*.xlsx"
        ),
        "Candidate Details": _latest_download(output_dir, "Candidate_Details__page_1_*.xlsx"),
        "Job Status Report": _latest_download(output_dir, "Job_Status_Report__page_1_*.xlsx"),
    }


def prepare_raw_data(
    csv_path: Path,
    start_date: pd.Timestamp,
    end_date: pd.Timestamp,
    weekdays_only: bool = False,
) -> pd.DataFrame:
    """Return the UNMODIFIED Mode VMS - Marriott columns for the raw data tab.

    21 columns in the exact order Marriott ops expects. No fg_ stripping, no
    location splitting. The raw data tab is the source-of-truth dump used for
    audit; the Mode tab is the normalized derivative.
    """
    df = pd.read_csv(csv_path, dtype=object)
    date_col = pd.to_datetime(df["date_of_shift_start"])
    mask = (date_col >= start_date) & (date_col <= end_date)
    if weekdays_only:
        mask &= date_col.dt.weekday < 5
    raw_columns = [
        "fieldglass_site_name",
        "name",
        "location",
        "position",
        "shift_name",
        "first_name",
        "last_name",
        "date_of_shift_start",
        "partner_rate",
        "pro_rate",
        "ot_pay_rate",
        "dt_pay_rate",
        "email",
        "full_name",
        "mark_up",
        "state_code",
        "worker_id",
        "first_shift_timestamp",
        "dob_mmdd",
        "shift_id",
        "shiftgroup_id",
    ]
    output = df.loc[mask, raw_columns].copy()
    output["date_of_shift_start"] = pd.to_datetime(output["date_of_shift_start"]).dt.strftime("%Y-%m-%d")
    return output.reset_index(drop=True)


def prepare_mode_view(raw_df: pd.DataFrame) -> pd.DataFrame:
    """Build the normalized Mode tab from raw data.

    A  fieldglass_site_name  (fg_ prefix stripped)
    B  name
    C  location              (portion BEFORE the ';')
    D  Department_Code       (portion AFTER the ';', trimmed). Note: column
                              name uses underscore to match the real assignment
                              workbook header convention.
    E..V                     position, shift_name, first_name, last_name,
                              date_of_shift_start (I), partner_rate (J),
                              pro_rate, ot_pay_rate, dt_pay_rate, email,
                              full_name, mark_up, state_code, worker_id (R),
                              first_shift_timestamp, dob_mmdd, shift_id,
                              shiftgroup_id.

    These positions are intentional. Perfect Match = D & R & J. 2nd Best
    Match = A & R & J. Shift date is at I. Same layout as the real ops
    workbook so the decision formulas (W..AM) drop in unchanged.
    """
    output = raw_df.copy()
    output["fieldglass_site_name"] = (
        output["fieldglass_site_name"].astype(str).str.replace(r"^fg_", "", regex=True).str.strip()
    )
    location_split = output["location"].fillna("").astype(str).str.split(";", n=1, expand=True)
    output["location"] = location_split[0].str.strip()
    department_code = (
        location_split[1].fillna("").str.strip() if location_split.shape[1] > 1 else pd.Series([""] * len(output))
    )
    output.insert(3, "Department_Code", department_code.values)
    return output.reset_index(drop=True)


def prepare_assignments(path: Path) -> pd.DataFrame:
    df = pd.read_excel(path, dtype=object)
    valid_statuses = {"Open", "Closed", "Cancelled"}
    return df[df["Assignment Status"].isin(valid_statuses)].reset_index(drop=True)


def prepare_open_active(assignments: pd.DataFrame) -> pd.DataFrame:
    """Build the Open Active tab in the exact layout the real ops workbook uses.

    Column letters that downstream Mode formulas hardcode:
      A  Con1                       (=CONCATENATE(J,BC,AB))
      B  Con 2  (with the space)    (=CONCATENATE(G,BC,AB))
      G  Work Location ID
      H  location
      I  (blank spacer column)      kept empty on purpose
      J  Department Name
      K  Department Code
      O  Assignment ID
      W  Assignment Start Date
      X  Assignment End Date
      AB Client ST Bill Rate
      BC Vendor Tracking ID 1
    """
    df = assignments[assignments["Assignment Status"] == "Open"].copy().reset_index(drop=True)

    work_location = df["Work Location"].fillna("").astype(str)
    split_location = work_location.str.split(" - ", n=1, expand=True)
    work_location_id = split_location[0]
    locations = split_location[1] if split_location.shape[1] > 1 else ""

    output = df.drop(columns=["Work Location", "Business Unit"], errors="ignore").copy()
    insert_at = list(output.columns).index("Department Name")
    output.insert(insert_at, "", "")
    output.insert(insert_at, "location", locations)
    output.insert(insert_at, "Work Location ID", work_location_id)

    con1 = [
        google_concat_value(row["Department Name"])
        + google_concat_value(row.get("Vendor Tracking ID 1"))
        + google_concat_value(row["Client ST Bill Rate"])
        for _, row in output.iterrows()
    ]
    con2 = [
        google_concat_value(row["Work Location ID"])
        + google_concat_value(row.get("Vendor Tracking ID 1"))
        + google_concat_value(row["Client ST Bill Rate"])
        for _, row in output.iterrows()
    ]
    output.insert(0, "Con 2", con2)
    output.insert(0, "Con1", con1)
    return output


def prepare_candidates(path: Path, active_only: bool = True) -> pd.DataFrame:
    df = pd.read_excel(path, dtype=object)
    df = df[df["Candidate Ref ID"].notna()]
    if active_only and "Is Active?" in df.columns:
        df = df[df["Is Active?"].astype(str).str.strip().str.lower() == "yes"]
    return df.reset_index(drop=True)


def prepare_jobs(path: Path, sourcing_only: bool = True, end_date_cutoff: pd.Timestamp | None = None) -> pd.DataFrame:
    df = pd.read_excel(path, dtype=object)
    df = df[df["Job ID"].notna()]
    if sourcing_only and "Job Status" in df.columns:
        df = df[df["Job Status"].astype(str).str.strip().str.lower() == "sourcing"]
    if end_date_cutoff is not None and "Job End Date" in df.columns:
        end = pd.to_datetime(df["Job End Date"], errors="coerce")
        df = df[end >= end_date_cutoff]
    return df.reset_index(drop=True)


def build_tabs(args: argparse.Namespace, mode_csv: Path, simplify_files: dict[str, Path]) -> dict[str, pd.DataFrame]:
    start_date = pd.Timestamp(args.start_date)
    end_date = pd.Timestamp(args.end_date)
    jobs_cutoff = pd.Timestamp(args.jobs_end_date_cutoff) if args.jobs_end_date_cutoff else None

    raw_data = prepare_raw_data(mode_csv, start_date, end_date, weekdays_only=args.weekdays_only)
    mode_view = prepare_mode_view(raw_data)
    assignments = prepare_assignments(simplify_files["Active Assignments Details - Vendor"])
    open_active = prepare_open_active(assignments)
    candidates = prepare_candidates(
        simplify_files["Candidate Details"],
        active_only=not args.keep_all_candidates,
    )
    jobs = prepare_jobs(
        simplify_files["Job Status Report"],
        sourcing_only=not args.keep_all_jobs,
        end_date_cutoff=None if args.keep_all_jobs else jobs_cutoff,
    )

    return {
        "raw data": raw_data,
        "Mode": mode_view,
        "Open & Closed": assignments,
        "Open Active": open_active,
        "candidate details": candidates,
        "job status": jobs,
    }


TAB_RENAME_MAP = {
    "Raw Data": "raw data",
    "Jobs": "job status",
    "CAN Details": "candidate details",
    "Upload": "upload",
    "Job Request": "job request",
    "CAN Upload": "can upload",
    "CAN Output": "can output",
}


def upload_tabs(args: argparse.Namespace, tabs: dict[str, pd.DataFrame]) -> None:
    credentials = Credentials.from_service_account_file(
        str(args.google_credentials),
        scopes=["https://www.googleapis.com/auth/spreadsheets"],
    )
    service = build("sheets", "v4", credentials=credentials, cache_discovery=False)
    spreadsheet = (
        service.spreadsheets()
        .get(
            spreadsheetId=args.target_spreadsheet_id,
            fields="sheets(properties(sheetId,title,gridProperties))",
        )
        .execute()
    )
    existing = {sheet["properties"]["title"]: sheet["properties"] for sheet in spreadsheet.get("sheets", [])}

    rename_requests = []
    for old_name, new_name in TAB_RENAME_MAP.items():
        if old_name in existing and new_name not in existing:
            rename_requests.append(
                {
                    "updateSheetProperties": {
                        "properties": {"sheetId": existing[old_name]["sheetId"], "title": new_name},
                        "fields": "title",
                    }
                }
            )
    if rename_requests:
        service.spreadsheets().batchUpdate(
            spreadsheetId=args.target_spreadsheet_id,
            body={"requests": rename_requests},
        ).execute()
        for old_name, new_name in TAB_RENAME_MAP.items():
            if old_name in existing and new_name not in existing:
                props = existing.pop(old_name)
                props["title"] = new_name
                existing[new_name] = props
                print(f"Renamed tab '{old_name}' -> '{new_name}'")

    for title, df in tabs.items():
        row_count = max(len(df) + 10, 100)
        col_count = max(len(df.columns) + 5, 26)

        if title not in existing:
            response = execute_google_request(
                service.spreadsheets().batchUpdate(
                    spreadsheetId=args.target_spreadsheet_id,
                    body={
                        "requests": [
                            {
                                "addSheet": {
                                    "properties": {
                                        "title": title,
                                        "gridProperties": {
                                            "rowCount": row_count,
                                            "columnCount": col_count,
                                        },
                                    }
                                }
                            }
                        ]
                    },
                ),
                f"add sheet {title}",
            )
            existing[title] = response["replies"][0]["addSheet"]["properties"]
        else:
            sheet_id = existing[title]["sheetId"]
            service.spreadsheets().batchUpdate(
                spreadsheetId=args.target_spreadsheet_id,
                body={
                    "requests": [
                        {
                            "updateSheetProperties": {
                                "properties": {
                                    "sheetId": sheet_id,
                                    "gridProperties": {
                                        "rowCount": row_count,
                                        "columnCount": col_count,
                                    },
                                },
                                "fields": "gridProperties(rowCount,columnCount)",
                            }
                        }
                    ]
                },
            ).execute()

        service.spreadsheets().values().clear(
            spreadsheetId=args.target_spreadsheet_id,
            range=f"'{title}'",
        ).execute()

        values = df_to_values(df)
        end_col = column_name(len(values[0]) - 1)
        chunk_size = 2000

        for start in range(0, len(values), chunk_size):
            chunk = values[start : start + chunk_size]
            start_row = start + 1
            end_row = start + len(chunk)
            update_range = f"'{title}'!A{start_row}:{end_col}{end_row}"
            service.spreadsheets().values().update(
                spreadsheetId=args.target_spreadsheet_id,
                range=update_range,
                valueInputOption="USER_ENTERED",
                body={"values": chunk},
            ).execute()

        print(f"Updated {title}: {len(df)} rows x {len(df.columns)} columns")


def _row_key(row: list[Any], width: int, headers: list[str] | None = None) -> tuple[str, ...]:
    if headers:
        normalized_headers = [str(header).strip().lower() for header in headers[:width]]
        shift_id_indexes = [
            index for index, header in enumerate(normalized_headers) if header in {"shift_id", "shiftgroup_id"}
        ]
        if shift_id_indexes and all(index < len(row) for index in shift_id_indexes):
            stable_values = tuple(str(clean_value(row[index])).strip() for index in shift_id_indexes)
            if all(stable_values):
                return ("shift-key", *stable_values)
        for stable_header in ("assignment id", "candidate id", "job posting id"):
            if stable_header in normalized_headers:
                index = normalized_headers.index(stable_header)
                if index < len(row):
                    stable_value = str(clean_value(row[index])).strip()
                    if stable_value:
                        return (stable_header, stable_value)
    padded = [clean_value(value) for value in row[:width]]
    padded.extend([""] * (width - len(padded)))
    return tuple(str(value).strip() for value in padded)


def append_tabs(args: argparse.Namespace, tabs: dict[str, pd.DataFrame]) -> None:
    """Append only rows that are not already present in each destination tab."""
    credentials = Credentials.from_service_account_file(
        str(args.google_credentials),
        scopes=["https://www.googleapis.com/auth/spreadsheets"],
    )
    service = build("sheets", "v4", credentials=credentials, cache_discovery=False)
    spreadsheet = (
        service.spreadsheets()
        .get(
            spreadsheetId=args.target_spreadsheet_id,
            fields="sheets(properties(sheetId,title,gridProperties))",
        )
        .execute()
    )
    existing = {sheet["properties"]["title"]: sheet["properties"] for sheet in spreadsheet.get("sheets", [])}

    for title, df in tabs.items():
        values = df_to_values(df)
        headers = values[0]
        width = len(headers)
        row_count = max(len(df) + 10, 100)
        col_count = max(width + 5, 26)

        if title not in existing:
            response = execute_google_request(
                service.spreadsheets().batchUpdate(
                    spreadsheetId=args.target_spreadsheet_id,
                    body={
                        "requests": [
                            {
                                "addSheet": {
                                    "properties": {
                                        "title": title,
                                        "gridProperties": {
                                            "rowCount": row_count,
                                            "columnCount": col_count,
                                        },
                                    }
                                }
                            }
                        ]
                    },
                ),
                f"add sheet {title}",
            )
            existing[title] = response["replies"][0]["addSheet"]["properties"]

        existing_values = execute_google_request(
            service.spreadsheets().values().get(spreadsheetId=args.target_spreadsheet_id, range=f"'{title}'"),
            f"read existing values for {title}",
        ).get("values", [])

        if not existing_values:
            end_col = column_name(width - 1)
            chunk_size = 2000
            for start in range(0, len(values), chunk_size):
                chunk = values[start : start + chunk_size]
                start_row = start + 1
                end_row = start_row + len(chunk) - 1
                update_range = f"'{title}'!A{start_row}:{end_col}{end_row}"
                execute_google_request(
                    service.spreadsheets().values().update(
                        spreadsheetId=args.target_spreadsheet_id,
                        range=update_range,
                        valueInputOption="USER_ENTERED",
                        body={"values": chunk},
                    ),
                    f"initialize {title} rows {start_row}-{end_row}",
                )
                time.sleep(1.1)
            print(f"Initialized {title}: {len(df)} rows x {len(df.columns)} columns")
            continue

        existing_headers = [str(value).strip() for value in existing_values[0][:width]]
        if existing_headers != headers:
            raise RuntimeError(
                f"Cannot append to '{title}' because existing headers do not match generated headers. "
                "Clear or rename the tab before retrying."
            )

        seen = {_row_key(row, width, headers) for row in existing_values[1:]}
        rows_to_append: list[list[Any]] = []
        for row in values[1:]:
            key = _row_key(row, width, headers)
            if key in seen:
                continue
            seen.add(key)
            rows_to_append.append(row)

        if rows_to_append:
            chunk_size = 2000
            for start in range(0, len(rows_to_append), chunk_size):
                chunk = rows_to_append[start : start + chunk_size]
                execute_google_request(
                    service.spreadsheets().values().append(
                        spreadsheetId=args.target_spreadsheet_id,
                        range=f"'{title}'!A:{column_name(width - 1)}",
                        valueInputOption="USER_ENTERED",
                        insertDataOption="INSERT_ROWS",
                        body={"values": chunk},
                    ),
                    f"append {title} chunk starting {start + 1}",
                )
                time.sleep(1.1)
        print(f"Appended {title}: {len(rows_to_append)} new rows ({len(df)} generated)")


MODE_DECISION_HEADERS = [
    "Perfect Match",
    "Perfect AID",
    "2nd Best Match",
    "2nd Best AID",
    "validation_for_Department",
    "CAN ID",
    "Available Jobs",
    "state",
    "Assigned By",
    "Existing Jobs",
    "Comments",
    "City tax",
    "state tax",
    "Start Date",
    "End Date",
    "Shift Start Date",
    "Concat",
]


def _decision_formulas(row_number: int) -> list[str]:
    """Build the W..AM formula strings for one Mode row.

    Column letters target the real ops workbook layout:
      Open Active:  Con1=A, Con 2=B, Department Name=J, Assignment ID=O,
                    Assignment Start Date=W, Assignment End Date=X
      Open & Closed: Vendor Tracking ID 1=AZ, Candidate ID=R, Assignment ID=L,
                    Job ID=AG
      job status (lowercase tab name): Job ID=B, Job Start Date=F,
                    Job End Date=G

    Manual columns (AC Available Jobs, AD state, AE Assigned By, AG Comments,
    AH City tax, AI state tax) are emitted blank so operators fill by hand.
    AM Concat = CONCATENATE(AB, AC, AL) so the Output tab's Shift ID XLOOKUP
    can find the matching Mode row once a row is uploaded.
    """
    r = row_number
    return [
        f"=CONCATENATE(D{r},R{r},J{r})",
        (
            "=ARRAY_CONSTRAIN("
            "ARRAYFORMULA("
            "IFERROR("
            "FILTER('Open Active'!O:O,"
            f"'Open Active'!A:A=W{r},"
            f"I{r}>='Open Active'!W:W,"
            f"I{r}<='Open Active'!X:X"
            "),0)),1,1)"
        ),
        f"=CONCATENATE(A{r},R{r},J{r})",
        (
            "=ARRAY_CONSTRAIN("
            "ARRAYFORMULA("
            "IFERROR("
            "FILTER('Open Active'!O:O,"
            f"'Open Active'!B:B=Y{r},"
            f"I{r}>='Open Active'!W:W,"
            f"I{r}<='Open Active'!X:X"
            "),0)),1,1)"
        ),
        (
            "=IFERROR(IF("
            f"D{r}=INDEX('Open Active'!J:J,MATCH(Z{r},'Open Active'!O:O,0)),"
            "\"OK\",\"Not OK\"),\"AID Not Found\")"
        ),
        f"=XLOOKUP(R{r},'Open & Closed'!AZ:AZ,'Open & Closed'!R:R,0)",
        "",
        "",
        "",
        f"=XLOOKUP(Z{r},'Open & Closed'!L:L,'Open & Closed'!AG:AG,0)",
        "",
        "",
        "",
        f"=XLOOKUP(AC{r},'job status'!B:B,'job status'!F:F,0)",
        f"=XLOOKUP(AC{r},'job status'!B:B,'job status'!G:G,0)",
        f"=TEXT(I{r},\"mm/dd/yyyy\")",
        f"=CONCATENATE(AB{r},AC{r},AL{r})",
    ]


def apply_mode_decision_formulas(args: argparse.Namespace, mode_row_count: int) -> None:
    """Write headers + formulas for Mode columns W..AM using USER_ENTERED so
    Google Sheets evaluates the lookups against the freshly uploaded raw tabs.
    """
    if mode_row_count <= 0:
        return
    credentials = Credentials.from_service_account_file(
        str(args.google_credentials),
        scopes=["https://www.googleapis.com/auth/spreadsheets"],
    )
    service = build("sheets", "v4", credentials=credentials, cache_discovery=False)

    header_range = "'Mode'!W1:AM1"
    service.spreadsheets().values().update(
        spreadsheetId=args.target_spreadsheet_id,
        range=header_range,
        valueInputOption="RAW",
        body={"values": [MODE_DECISION_HEADERS]},
    ).execute()

    values = [_decision_formulas(row_number) for row_number in range(2, mode_row_count + 2)]
    chunk_size = 500
    for start in range(0, len(values), chunk_size):
        chunk = values[start : start + chunk_size]
        start_row = 2 + start
        end_row = start_row + len(chunk) - 1
        service.spreadsheets().values().update(
            spreadsheetId=args.target_spreadsheet_id,
            range=f"'Mode'!W{start_row}:AM{end_row}",
            valueInputOption="USER_ENTERED",
            body={"values": chunk},
        ).execute()
    print(f"Applied Mode decision formulas to {mode_row_count} rows (W..AM)")


def _normalize_join_value(value: Any) -> str:
    """Stringify a value the way Google Sheets CONCATENATE would in our keys."""
    return google_concat_value(value)


def compute_local_decisions(
    mode_df: pd.DataFrame,
    open_active_df: pd.DataFrame,
    open_closed_df: pd.DataFrame,
) -> pd.DataFrame:
    """Compute Perfect AID, 2nd Best AID, CAN ID, Existing Jobs locally so we
    can build downstream output tabs deterministically (without relying on
    sheet formula evaluation timing).
    """
    df = mode_df.copy()

    oa = open_active_df.copy()
    oa_assignment_id = oa["Assignment ID"].astype(str)
    oa_concat1 = oa["Con1"].astype(str)
    oa_concat2 = oa["Con 2"].astype(str)
    oa_dept = oa["Department Name"].astype(str)
    oa_start = pd.to_datetime(oa["Assignment Start Date"], errors="coerce")
    oa_end = pd.to_datetime(oa["Assignment End Date"], errors="coerce")

    oc = open_closed_df.copy()
    oc_vti1 = oc["Vendor Tracking ID 1"].astype(str)
    oc_candidate_id = oc["Candidate ID"].astype(str)
    oc_assignment_id = oc["Assignment ID"].astype(str)
    oc_job_id = oc["Job ID"].astype(str)

    can_by_worker: dict[str, str] = {}
    for w, c in zip(oc_vti1, oc_candidate_id):
        if not w or w == "nan" or w == "":
            continue
        if c and c != "nan" and w not in can_by_worker:
            can_by_worker[w] = c

    job_by_aid: dict[str, str] = {}
    for aid, job in zip(oc_assignment_id, oc_job_id):
        if not aid or aid == "nan":
            continue
        if job and job != "nan" and aid not in job_by_aid:
            job_by_aid[aid] = job

    perfect_match: list[str] = []
    perfect_aid: list[str] = []
    second_match: list[str] = []
    second_aid: list[str] = []
    validation: list[str] = []
    can_id: list[str] = []
    existing_jobs: list[str] = []

    mode_dates = pd.to_datetime(df["date_of_shift_start"], errors="coerce")
    for index, row in df.iterrows():
        dept_code = _normalize_join_value(row.get("Department_Code") or row.get("Department Code"))
        site = _normalize_join_value(row.get("fieldglass_site_name"))
        worker = _normalize_join_value(row.get("worker_id"))
        rate = _normalize_join_value(row.get("partner_rate"))
        pm = dept_code + worker + rate
        sm = site + worker + rate
        perfect_match.append(pm)
        second_match.append(sm)

        shift_date = mode_dates.iloc[index]

        p_aid = "0"
        if pd.notna(shift_date) and pm:
            in_window = (oa_start <= shift_date) & (oa_end >= shift_date)
            match_mask = (oa_concat1 == pm) & in_window
            hits = oa_assignment_id[match_mask].tolist()
            if hits:
                p_aid = hits[0]
        perfect_aid.append(p_aid)

        s_aid = "0"
        if pd.notna(shift_date) and sm:
            in_window = (oa_start <= shift_date) & (oa_end >= shift_date)
            match_mask = (oa_concat2 == sm) & in_window
            hits_idx = oa_assignment_id[match_mask].index.tolist()
            if hits_idx:
                hit = hits_idx[0]
                s_aid = oa_assignment_id.iloc[hit]
                if dept_code == _normalize_join_value(oa.iloc[hit].get("Department Code")) or dept_code == oa_dept.iloc[hit]:
                    validation.append("OK")
                else:
                    validation.append("Not OK")
            else:
                validation.append("AID Not Found")
        else:
            validation.append("AID Not Found")
        second_aid.append(s_aid)

        can_id.append(can_by_worker.get(worker, "0") if worker else "0")
        existing_jobs.append(job_by_aid.get(s_aid, "0") if s_aid != "0" else "0")

    df["Perfect Match"] = perfect_match
    df["Perfect AID"] = perfect_aid
    df["2nd Best Match"] = second_match
    df["2nd Best AID"] = second_aid
    df["validation_for_Department"] = validation
    df["CAN ID"] = can_id
    df["Existing Jobs"] = existing_jobs

    return df


def build_upload(decisions_df: pd.DataFrame) -> pd.DataFrame:
    """Mode rows that are ready for direct assignment: Perfect AID = 0,
    2nd Best AID = 0, CAN ID present, AND an Available Jobs ID has been
    chosen. Available Jobs is a manual decision so this view is typically
    empty until ops populates Mode!AC.
    """
    cols = [
        "Candidate ID",
        "Job ID",
        "Available Start Date (MM/DD/YYYY)",
        "Available End Date (MM/DD/YYYY)",
        "Client Bill Rate",
        "Pay Rate",
        "City Tax",
        "State Tax",
        "Vendor Tracking ID 1",
        "Vendor Tracking ID 2",
        "Vendor Tracking ID 3",
    ]
    return pd.DataFrame(columns=cols)


def build_job_request(
    decisions_df: pd.DataFrame,
    open_active_df: pd.DataFrame,
) -> pd.DataFrame:
    """Mode rows that need a brand new job posting: Perfect AID = 0 AND
    2nd Best AID = 0 AND CAN ID present. The reference workbook has 11 such
    rows; ours will typically have more because we don't yet have AC chosen
    anywhere.
    """
    mask = (
        (decisions_df["Perfect AID"].astype(str) == "0")
        & (decisions_df["2nd Best AID"].astype(str) == "0")
        & (decisions_df["CAN ID"].astype(str) != "0")
        & (decisions_df["CAN ID"].astype(str) != "")
    )
    selected = decisions_df.loc[mask].copy()
    if selected.empty:
        return pd.DataFrame(
            columns=[
                "Property ID",
                "Property Name",
                "location",
                "CAN id",
                "Shift",
                "partner_rate",
                "pro_rate",
                "Pro Name",
                "Position",
                "shift name",
                "Department",
                "Mark up",
                "State",
                "Reason for new Job(New Rate, Closed Assignment, Job Expired, open assignments but at different rates)",
                "Notes:",
                "Becky Notes",
                "Comments",
                "Status",
            ]
        )

    shift_dates = pd.to_datetime(selected["date_of_shift_start"], errors="coerce")
    department_series = selected["Department_Code"] if "Department_Code" in selected.columns else selected.get("Department Code", "")
    return pd.DataFrame(
        {
            "Property ID": selected["fieldglass_site_name"].astype(str),
            "Property Name": selected["location"].astype(str),
            "location": selected["location"].astype(str),
            "CAN id": selected["CAN ID"].astype(str),
            "Shift": shift_dates.dt.strftime("%m/%d/%Y").fillna(""),
            "partner_rate": selected["partner_rate"].astype(str),
            "pro_rate": selected["pro_rate"].astype(str),
            "Pro Name": (selected["first_name"].astype(str) + " " + selected["last_name"].astype(str)).str.strip(),
            "Position": selected["position"].astype(str),
            "shift name": selected["shift_name"].astype(str),
            "Department": pd.Series(department_series).astype(str).values,
            "Mark up": selected["mark_up"].astype(str),
            "State": selected["state_code"].astype(str),
            "Reason for new Job(New Rate, Closed Assignment, Job Expired, open assignments but at different rates)": "",
            "Notes:": "",
            "Becky Notes": "",
            "Comments": "",
            "Status": "",
        }
    ).reset_index(drop=True)


def build_can_upload(decisions_df: pd.DataFrame) -> pd.DataFrame:
    """Mode rows representing pros without a Marriott Candidate ID:
    Perfect AID = 0 AND 2nd Best AID = 0 AND CAN ID = 0. We pre-fill what we
    can from Mode (First Name, Last Name, DOB MM/DD, Email). SSN last 3 and
    Middle Name are blank for ops to add manually (Sheet9 / Falcon flow).
    """
    mask = (
        (decisions_df["Perfect AID"].astype(str) == "0")
        & (decisions_df["2nd Best AID"].astype(str) == "0")
        & (decisions_df["CAN ID"].astype(str).isin(["0", "", "nan"]))
    )
    selected = decisions_df.loc[mask].copy()
    if selected.empty:
        return pd.DataFrame(
            columns=[
                "First Name",
                "Middle Name",
                "Last Name",
                "Date Of Birth(MM/DD)",
                "State/National ID (Last 3 Digits)",
                "Email Address",
            ]
        )

    selected = selected.drop_duplicates(subset=["worker_id"], keep="first")
    dob_mmdd = (
        selected["dob_mmdd"]
        .astype(str)
        .replace({"nan": "", "None": "", "NaN": ""})
        .str.replace(r"\.0$", "", regex=True)
    )
    dob_mmdd = dob_mmdd.where(dob_mmdd.str.match(r"\d{2}/\d{2}", na=False), "")
    return pd.DataFrame(
        {
            "First Name": selected["first_name"].astype(str),
            "Middle Name": "",
            "Last Name": selected["last_name"].astype(str),
            "Date Of Birth(MM/DD)": dob_mmdd,
            "State/National ID (Last 3 Digits)": "",
            "Email Address": selected["email"].astype(str),
        }
    ).reset_index(drop=True)


def build_can_output(can_upload_df: pd.DataFrame) -> pd.DataFrame:
    """can output template: CAN Upload columns + Candidate ID + Remarks.

    After ops imports `can upload` into Simplify, paste returned Candidate IDs
    into column G and "Data Imported Successfully" (or any error message) into
    column H. The matching row order is preserved.
    """
    cols = [
        "First Name",
        "Middle Name",
        "Last Name",
        "Date Of Birth(MM/DD)",
        "State/National ID (Last 3 Digits)",
        "Email Address",
        "Candidate ID",
        "Remarks",
    ]
    if can_upload_df.empty:
        return pd.DataFrame(columns=cols)
    output = can_upload_df.copy()
    output["Candidate ID"] = ""
    output["Remarks"] = ""
    return output[cols].reset_index(drop=True)


def build_output_template() -> pd.DataFrame:
    """Empty `Output` tab template.

    17 columns: the 11 upload columns + 6 post-import tracking columns.
    Column M is intentionally header-less because the real ops workbook
    leaves it unlabeled but uses it for the import status message.

    After Simplify ingests `upload`, ops fills in:
      L Remarks   - Simplify Submission ID (e.g. MAR-SB-241567)
      M (blank)   - status text ("Data Imported Successfully" / error)
      N State     - =VLOOKUP(B,Mode!AC:AD,2,0)
      O Assigned By - operator's name
      P Concat    - =CONCATENATE(A,B,C)
      Q Shift ID  - =XLOOKUP(P,Mode!AM:AM,Mode!U:U,0)
    """
    return pd.DataFrame(
        columns=[
            "Candidate ID",
            "Job ID",
            "Available Start Date (MM/DD/YYYY)",
            "Available End Date (MM/DD/YYYY)",
            "Client Bill Rate",
            "Pay Rate",
            "City Tax",
            "State Tax",
            "Vendor Tracking ID 1",
            "Vendor Tracking ID 2",
            "Vendor Tracking ID 3",
            "Remarks",
            "",
            "State",
            "Assigned By",
            "Concat",
            "Shift ID",
        ]
    )


def build_sheet8_template() -> pd.DataFrame:
    """Empty `Sheet8` tab - source data for candidate creation.

    Ops pastes one row per new pro:
      id (worker_id), name (full name), email (real pro email), ssn (full
      number), bank_account_type (typically last 3 of SSN as the password),
      date_of_birth (yyyy-mm-dd).

    The candidate upload format (`can upload` columns) is derived from
    Sheet8 by ops: First/Last from name, MM/DD from date_of_birth, last 3 of
    SSN, and a constructed email `{FirstName}{ssn_last3}@instawork.com`.
    """
    return pd.DataFrame(columns=["id", "name", "email", "ssn", "bank_account_type", "date_of_birth"])


def build_summary_template() -> pd.DataFrame:
    """Empty `Summary` tab - operator scratch / QA notes."""
    return pd.DataFrame(columns=["Summary"])


def default_assignment_window(today: date | None = None) -> tuple[str, str]:
    """Return (start_date, end_date) for today's Marriott operating window.

    Marriott uses Sat-Fri operating weeks. Ops keeps assignment readiness for
    N-1, N, and N+1: previous week, current week, and next week.

    For today (Wed in IST), the anchor Saturday is the most recent Saturday,
    and the window is [anchor - 7 days, anchor + 13 days] = [previous Sat,
    next Fri].
    """
    today = today or datetime.now(ZoneInfo(SNAPSHOT_TIMEZONE)).date()
    days_since_saturday = (today.weekday() - 5) % 7
    current_saturday = today - pd.Timedelta(days=days_since_saturday).to_pytimedelta()
    start = current_saturday - pd.Timedelta(days=7).to_pytimedelta()
    end = current_saturday + pd.Timedelta(days=13).to_pytimedelta()
    return start.isoformat(), end.isoformat()


def build_snapshot_name(start_date: str, end_date: str) -> str:
    """Format the snapshot file name using IST day-of-week.

    Example: build_snapshot_name("2026-05-09", "2026-05-29") run on a Tuesday
    in IST returns 'Tuesday Marriott(05/09 - 05/29)'.
    """
    ist_now = datetime.now(ZoneInfo(SNAPSHOT_TIMEZONE))
    day = ist_now.strftime("%A")
    start_mmdd = pd.Timestamp(start_date).strftime("%m/%d")
    end_mmdd = pd.Timestamp(end_date).strftime("%m/%d")
    return f"{day} Marriott({start_mmdd} - {end_mmdd})"


def snapshot_to_shared_drive(args: argparse.Namespace, snapshot_name: str) -> dict[str, Any]:
    """Copy the populated target spreadsheet into the Marriott shared drive."""
    credentials = Credentials.from_service_account_file(
        str(args.google_credentials),
        scopes=["https://www.googleapis.com/auth/drive"],
    )
    drive = build("drive", "v3", credentials=credentials, cache_discovery=False)
    new_file = (
        drive.files()
        .copy(
            fileId=args.target_spreadsheet_id,
            body={"name": snapshot_name, "parents": [args.shared_drive_id]},
            supportsAllDrives=True,
            fields="id,name,webViewLink,parents,driveId",
        )
        .execute()
    )
    link = new_file.get("webViewLink") or f"https://docs.google.com/spreadsheets/d/{new_file['id']}/edit"
    print(f"Snapshot created in shared drive: {new_file['name']} -> {link}")
    return new_file


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Populate Marriott raw tabs in Google Sheets.")
    parser.add_argument("--target-spreadsheet-id", default=DEFAULT_TARGET_SPREADSHEET_ID)
    parser.add_argument("--google-credentials", type=Path, default=Path("service_account_key.json"))
    parser.add_argument("--workdir", type=Path, default=Path("."))
    default_start, default_end = default_assignment_window()
    parser.add_argument(
        "--start-date",
        default=default_start,
        help="Mode/raw window start (default: previous Saturday in IST, N-1).",
    )
    parser.add_argument(
        "--end-date",
        default=default_end,
        help="Mode/raw window end (default: next Friday in IST, N+1).",
    )
    parser.add_argument(
        "--weekdays-only",
        action="store_true",
        help="Restrict Mode rows to Monday-Friday. Default keeps Sat-Fri operating week.",
    )
    parser.add_argument(
        "--keep-all-candidates",
        action="store_true",
        help="Keep all Candidate Details rows. Default keeps only Is Active? = Yes.",
    )
    parser.add_argument(
        "--keep-all-jobs",
        action="store_true",
        help="Keep all Job Status Report rows. Default keeps only Sourcing jobs whose Job End Date is >= cutoff.",
    )
    parser.add_argument(
        "--jobs-end-date-cutoff",
        default=default_start,
        help="Minimum Job End Date when filtering Sourcing jobs (default: window start).",
    )
    parser.add_argument("--mode-report-token", default=MODE_REPORT_TOKEN)
    parser.add_argument(
        "--mode-run-token",
        default=MODE_RUN_TOKEN,
        help="Legacy: only used by older ZIP-based flows. Ignored by the query-level downloader.",
    )
    parser.add_argument("--mode-query-token", default=MODE_MARRIOTT_QUERY_TOKEN)
    parser.add_argument(
        "--mode-max-retries",
        type=int,
        default=5,
        help="If no recent VMS - Marriott query_run succeeded, how many times to trigger a fresh Mode run.",
    )
    parser.add_argument(
        "--force-fresh-mode-download",
        action="store_true",
        help="Always trigger a new Mode report run and download its Marriott CSV, ignoring cached CSVs and prior runs.",
    )
    parser.add_argument("--simplify-export-email", default=os.environ.get("SIMPLIFY_EXPORT_EMAIL", "mcaplan@instawork.com"))
    parser.add_argument(
        "--skip-downloads",
        action="store_true",
        help="Reuse existing files in downloads/ instead of logging into Mode and Simplify.",
    )
    parser.add_argument(
        "--no-upload",
        action="store_true",
        help="Build data frames and print counts, but do not update Google Sheets.",
    )
    parser.add_argument(
        "--append-raw-tabs",
        action="store_true",
        help="Append only new rows to existing raw workflow tabs instead of clearing and replacing them.",
    )
    parser.add_argument(
        "--shared-drive-id",
        default=MARRIOTT_SHARED_DRIVE_ID,
        help="Shared Google Drive ID to drop the dated snapshot into.",
    )
    parser.add_argument(
        "--no-snapshot",
        action="store_true",
        help="Skip creating a dated snapshot copy in the shared drive.",
    )
    parser.add_argument(
        "--no-assignment-logic",
        action="store_true",
        help="Skip applying Mode decision formulas (W..AM) and building Upload / Job Request / CAN Upload / CAN Output tabs.",
    )
    parser.add_argument(
        "--snapshot-name",
        default=None,
        help="Override snapshot file name (default: '{Day} Marriott(MM/DD - MM/DD)' in IST).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    args.workdir = args.workdir.resolve()
    args.google_credentials = args.google_credentials.resolve()

    if args.skip_downloads:
        mode_csv = find_mode_csv(args)
        simplify_files = find_existing_simplify_downloads(args)
    else:
        mode_csv = download_mode_export(args)
        simplify_files = download_simplify_reports(args)

    tabs = build_tabs(args, mode_csv, simplify_files)
    print("Prepared tabs:")
    for title, df in tabs.items():
        print(f"- {title}: {len(df)} data rows x {len(df.columns)} columns")

    if args.no_upload:
        return

    if args.append_raw_tabs:
        append_tabs(args, tabs)
    else:
        upload_tabs(args, tabs)

    if not args.no_assignment_logic:
        from assignments import run_assignment_logic

        run_assignment_logic(args, tabs, upload_tabs)

    if args.no_snapshot:
        return

    snapshot_name = args.snapshot_name or build_snapshot_name(args.start_date, args.end_date)
    snapshot_to_shared_drive(args, snapshot_name)


if __name__ == "__main__":
    main()
