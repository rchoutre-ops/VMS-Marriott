#!/usr/bin/env python3
"""Replit-ready web UI for Marriott automation workflows."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from flask import Flask, jsonify, render_template, request
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build


APP_DIR = Path(__file__).resolve().parent
DEFAULT_TARGET_SPREADSHEET_ID = "1gMYap7mOK17l7lOhPtBohGjJoC1FsPWfjyywXwFjLWk"
RAW_DATA_FOLDER_ID = "1nPq1cEdPRlE5irYyetqYi24uTySFHv0J"
ASSIGNMENT_FOLDER_ID = "1m-4NWsTQUQ51mJiQfMD-emhvm0qN1Fc_"
LOG_SPREADSHEET_ID = "1veHtzoByPQfD7CDynmxJOTiH2ZuksqkxUnmG96alwYE"
LATEST_LOGS_TAB = "Latest Logs"
FULL_LOG_HISTORY_TAB = "Full Log History"
SNAPSHOT_TIMEZONE = "Asia/Kolkata"
MAX_LOG_LINES = 1500
SCHEDULE_CONFIG_PATH = APP_DIR / "schedule_config.json"
SCHEDULER_POLL_SECONDS = 30
SCHEDULE_GRACE_SECONDS = 600


def load_local_env() -> None:
    """Load local .env values for the web app and workflow subprocesses.

    Replit secrets or already-exported shell values win over .env values.
    """
    candidates = []
    if os.environ.get("ENV_FILE"):
        candidates.append(Path(os.environ["ENV_FILE"]).expanduser())
    candidates.extend([APP_DIR / ".env", APP_DIR.parent / ".env"])

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

app = Flask(__name__)

RUN_LOCK = threading.Lock()
RUN_STATE: dict[str, Any] = {
    "running": False,
    "workflow": None,
    "status": "Ready",
    "started_at": None,
    "ended_at": None,
    "exit_code": None,
    "command": [],
    "logs": deque(maxlen=MAX_LOG_LINES),
    "process": None,
}
SCHEDULE_LOCK = threading.Lock()
LOG_SHEET_LOCK = threading.Lock()
SCHEDULER_STARTED = False
LOG_SESSION: dict[str, Any] = {
    "active": False,
    "run_id": None,
    "workflow": None,
    "started_at_ist": None,
    "rows": [],
    "sheet_error_reported": False,
}


def default_assignment_window() -> tuple[str, str]:
    """Return Marriott's N-1, N, N+1 Sat-Fri operating window."""
    today = datetime.now(ZoneInfo(SNAPSHOT_TIMEZONE)).date()
    days_since_saturday = (today.weekday() - 5) % 7
    current_saturday = today - timedelta(days=days_since_saturday)
    start = current_saturday - timedelta(days=7)
    end = current_saturday + timedelta(days=13)
    return start.isoformat(), end.isoformat()


def snapshot_name(workflow: str, start_date: str, end_date: str) -> str:
    """Build a clear Drive copy name for the selected workflow."""
    day = datetime.now(ZoneInfo(SNAPSHOT_TIMEZONE)).strftime("%A")
    start_mmdd = datetime.strptime(start_date, "%Y-%m-%d").strftime("%m/%d")
    end_mmdd = datetime.strptime(end_date, "%Y-%m-%d").strftime("%m/%d")
    prefix = "Raw Data" if workflow == "data" else "Assignments"
    return f"{prefix} - {day} Marriott({start_mmdd} - {end_mmdd})"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def ist_now() -> datetime:
    return datetime.now(ZoneInfo(SNAPSHOT_TIMEZONE))


def human_ist_timestamp(value: datetime | None = None) -> str:
    value = value or ist_now()
    return value.strftime("%A, %d %b %Y %I:%M:%S %p IST")


def compact_ist_timestamp(value: datetime | None = None) -> str:
    value = value or ist_now()
    return value.strftime("%Y%m%d-%H%M%S")


def log_sheet_service() -> Any:
    credentials = Credentials.from_service_account_file(
        str(credential_path()),
        scopes=["https://www.googleapis.com/auth/spreadsheets"],
    )
    return build("sheets", "v4", credentials=credentials, cache_discovery=False)


def ensure_log_tabs(service: Any) -> None:
    spreadsheet = (
        service.spreadsheets()
        .get(spreadsheetId=LOG_SPREADSHEET_ID, fields="sheets(properties(title))")
        .execute()
    )
    existing = {sheet["properties"]["title"] for sheet in spreadsheet.get("sheets", [])}
    requests = [
        {"addSheet": {"properties": {"title": title}}}
        for title in (LATEST_LOGS_TAB, FULL_LOG_HISTORY_TAB)
        if title not in existing
    ]
    if requests:
        service.spreadsheets().batchUpdate(
            spreadsheetId=LOG_SPREADSHEET_ID,
            body={"requests": requests},
        ).execute()


def append_log_rows(tab_name: str, rows: list[list[str]]) -> None:
    if not rows:
        return
    service = log_sheet_service()
    ensure_log_tabs(service)
    service.spreadsheets().values().append(
        spreadsheetId=LOG_SPREADSHEET_ID,
        range=f"'{tab_name}'!A:E",
        valueInputOption="USER_ENTERED",
        insertDataOption="INSERT_ROWS",
        body={"values": rows},
    ).execute()


def clear_latest_log_sheet(run_id: str, workflow: str, started_at: str) -> None:
    service = log_sheet_service()
    ensure_log_tabs(service)
    service.spreadsheets().values().clear(
        spreadsheetId=LOG_SPREADSHEET_ID,
        range=f"'{LATEST_LOGS_TAB}'!A:E",
        body={},
    ).execute()
    service.spreadsheets().values().update(
        spreadsheetId=LOG_SPREADSHEET_ID,
        range=f"'{LATEST_LOGS_TAB}'!A1:E3",
        valueInputOption="USER_ENTERED",
        body={
            "values": [
                [f"Latest Marriott Automation Run: {workflow}", "", "", "", ""],
                [f"Run ID: {run_id}", f"Started: {started_at}", "", "", ""],
                ["Run ID", "Timestamp IST", "Workflow", "Event Type", "Message"],
            ]
        },
    ).execute()


def begin_sheet_logging(workflow: str, command_summary: str) -> None:
    started_at = human_ist_timestamp()
    run_id = f"{compact_ist_timestamp()}-{workflow.lower().replace(' ', '-')}"
    with LOG_SHEET_LOCK:
        LOG_SESSION.update(
            {
                "active": True,
                "run_id": run_id,
                "workflow": workflow,
                "started_at_ist": started_at,
                "rows": [],
                "sheet_error_reported": False,
            }
        )
    try:
        clear_latest_log_sheet(run_id, workflow, started_at)
        append_sheet_log("RUN", f"Run started. {command_summary}")
    except Exception as exc:  # noqa: BLE001 - logging must never block automation.
        with LOG_SHEET_LOCK:
            LOG_SESSION["sheet_error_reported"] = True
        print(f"Google log sheet initialization failed: {exc}")


def append_sheet_log(event_type: str, message: str) -> None:
    with LOG_SHEET_LOCK:
        if not LOG_SESSION.get("active"):
            return
        run_id = str(LOG_SESSION["run_id"])
        workflow = str(LOG_SESSION["workflow"])
        timestamp = human_ist_timestamp()

    rows = []
    for part in str(message).splitlines() or [""]:
        text = part.strip()
        if not text:
            continue
        rows.append([run_id, timestamp, workflow, event_type, text])
    if not rows:
        return

    with LOG_SHEET_LOCK:
        LOG_SESSION["rows"].extend(rows)

    try:
        append_log_rows(LATEST_LOGS_TAB, rows)
    except Exception as exc:  # noqa: BLE001 - logging must never block automation.
        with LOG_SHEET_LOCK:
            already_reported = bool(LOG_SESSION.get("sheet_error_reported"))
            LOG_SESSION["sheet_error_reported"] = True
        if not already_reported:
            print(f"Google Latest Logs append failed: {exc}")


def finish_sheet_logging(status: str, exit_code: int | None) -> None:
    finished_at = human_ist_timestamp()
    append_sheet_log("RUN", f"Run finished with status={status}, exit_code={exit_code}, finished_at={finished_at}.")
    with LOG_SHEET_LOCK:
        if not LOG_SESSION.get("active"):
            return
        run_id = str(LOG_SESSION["run_id"])
        workflow = str(LOG_SESSION["workflow"])
        started_at = str(LOG_SESSION["started_at_ist"])
        rows = list(LOG_SESSION["rows"])
        LOG_SESSION["active"] = False

    history_rows = [
        ["", "", "", "", ""],
        [f"========== {workflow} | {run_id} ==========", "", "", "", ""],
        [f"Started: {started_at}", f"Finished: {finished_at}", f"Status: {status}", f"Exit code: {exit_code}", ""],
        ["Run ID", "Timestamp IST", "Workflow", "Event Type", "Message"],
        *rows,
        [f"========== END {run_id} ==========", "", "", "", ""],
        ["", "", "", "", ""],
    ]
    try:
        append_log_rows(FULL_LOG_HISTORY_TAB, history_rows)
    except Exception as exc:  # noqa: BLE001 - logging must never block automation.
        print(f"Google Full Log History append failed: {exc}")


def default_schedule_config() -> dict[str, Any]:
    return {
        "enabled": True,
        "time": "08:30",
        "timezone": SNAPSHOT_TIMEZONE,
        "target_spreadsheet_id": DEFAULT_TARGET_SPREADSHEET_ID,
        "skip_snapshot": False,
        "dry_run": False,
        "keep_all_candidates": False,
        "keep_all_jobs": False,
        "last_started_for_date": None,
        "last_started_at": None,
        "last_finished_at": None,
        "last_status": None,
    }


def load_schedule_config() -> dict[str, Any]:
    config = default_schedule_config()
    if SCHEDULE_CONFIG_PATH.exists():
        try:
            saved = json.loads(SCHEDULE_CONFIG_PATH.read_text())
        except json.JSONDecodeError:
            saved = {}
        if isinstance(saved, dict):
            config.update(saved)
    return config


def save_schedule_config(config: dict[str, Any]) -> None:
    merged = default_schedule_config()
    merged.update(config)
    SCHEDULE_CONFIG_PATH.write_text(json.dumps(merged, indent=2, sort_keys=True) + "\n")


def today_scheduled_datetime(config: dict[str, Any], now: datetime | None = None) -> datetime:
    tz = ZoneInfo(config.get("timezone") or SNAPSHOT_TIMEZONE)
    now = now.astimezone(tz) if now else datetime.now(tz)
    hour, minute = [int(part) for part in str(config.get("time", "08:30")).split(":", 1)]
    return now.replace(hour=hour, minute=minute, second=0, microsecond=0)


def schedule_is_due(config: dict[str, Any], now: datetime | None = None) -> bool:
    if not config.get("enabled", True):
        return False
    tz = ZoneInfo(config.get("timezone") or SNAPSHOT_TIMEZONE)
    now = now.astimezone(tz) if now else datetime.now(tz)
    candidate = today_scheduled_datetime(config, now)
    within_grace = candidate <= now <= candidate + timedelta(seconds=SCHEDULE_GRACE_SECONDS)
    return within_grace and config.get("last_started_for_date") != candidate.date().isoformat()


def next_scheduled_run(config: dict[str, Any], now: datetime | None = None) -> datetime | None:
    if not config.get("enabled", True):
        return None
    tz = ZoneInfo(config.get("timezone") or SNAPSHOT_TIMEZONE)
    now = now.astimezone(tz) if now else datetime.now(tz)
    candidate = today_scheduled_datetime(config, now)
    if candidate <= now or config.get("last_started_for_date") == candidate.date().isoformat():
        candidate = candidate + timedelta(days=1)
    return candidate


def scheduled_form(config: dict[str, Any]) -> dict[str, Any]:
    start_date, end_date = default_assignment_window()
    return {
        "target_spreadsheet_id": config.get("target_spreadsheet_id") or DEFAULT_TARGET_SPREADSHEET_ID,
        "start_date": start_date,
        "end_date": end_date,
        "skip_snapshot": bool(config.get("skip_snapshot", False)),
        "dry_run": bool(config.get("dry_run", False)),
        "keep_all_candidates": bool(config.get("keep_all_candidates", False)),
        "keep_all_jobs": bool(config.get("keep_all_jobs", False)),
    }


def local_downloads_available() -> bool:
    return (APP_DIR / "downloads" / "mode_raw").exists() and (APP_DIR / "downloads" / "simplify_raw").exists()


def credential_path() -> Path:
    """Return a Google key path, materializing Replit secret JSON when provided.

    Local runs can use ./service_account_key.json. In Replit, store the service
    account JSON in a secret named GOOGLE_SERVICE_ACCOUNT_JSON.
    """
    json_secret = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if json_secret:
        key_path = Path("/tmp/marriott_service_account_key.json")
        if not key_path.exists() or key_path.read_text() != json_secret:
            json.loads(json_secret)
            key_path.write_text(json_secret)
        return key_path
    return APP_DIR / "service_account_key.json"


def requirements_status() -> dict[str, bool]:
    load_local_env()
    return {
        "google_key": credential_path().exists(),
        "mode_credentials": bool(os.environ.get("MODE_API_KEY_ID") and os.environ.get("MODE_API_KEY_SECRET")),
        "simplify_credentials": bool(os.environ.get("SIMPLIFY_EMAIL") and os.environ.get("SIMPLIFY_PASSWORD")),
        "local_downloads": local_downloads_available(),
    }


def build_command(workflow: str, form: dict[str, Any], *, reuse_downloads: bool = False) -> list[str]:
    if workflow not in {"data", "assignments"}:
        raise ValueError("Unknown workflow")

    script = "data_workflow.py" if workflow == "data" else "assignments.py"
    command = [
        sys.executable,
        str(APP_DIR / script),
        "--workdir",
        str(APP_DIR),
        "--target-spreadsheet-id",
        form["target_spreadsheet_id"].strip(),
        "--google-credentials",
        str(credential_path()),
        "--start-date",
        form["start_date"].strip(),
        "--end-date",
        form["end_date"].strip(),
        "--shared-drive-id",
        RAW_DATA_FOLDER_ID if workflow == "data" else ASSIGNMENT_FOLDER_ID,
        "--snapshot-name",
        snapshot_name(workflow, form["start_date"].strip(), form["end_date"].strip()),
    ]

    if reuse_downloads:
        command.append("--skip-downloads")
    else:
        command.extend(["--force-fresh-mode-download", "--mode-max-retries", "5"])

    if workflow == "data":
        command.append("--no-assignment-logic")
    if form.get("skip_snapshot"):
        command.append("--no-snapshot")
    if form.get("dry_run"):
        command.append("--no-upload")
    if form.get("keep_all_candidates"):
        command.append("--keep-all-candidates")
    if form.get("keep_all_jobs"):
        command.append("--keep-all-jobs")
    return command


def append_log(line: str) -> None:
    with RUN_LOCK:
        RUN_STATE["logs"].append(line)
    append_sheet_log("LOG", line)


def run_command(command: list[str], workflow_label: str) -> int:
    load_local_env()
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"

    process = subprocess.Popen(
        command,
        cwd=str(APP_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        env=env,
        start_new_session=True,
    )
    with RUN_LOCK:
        RUN_STATE["process"] = process

    assert process.stdout is not None
    for line in process.stdout:
        append_log(line)

    exit_code = process.wait()
    with RUN_LOCK:
        RUN_STATE["process"] = None
    return exit_code


def run_process(command: list[str], workflow_label: str) -> None:
    final_status = "Failed"
    final_exit_code = 1
    try:
        exit_code = run_command(command, workflow_label)
        final_status = "Completed" if exit_code == 0 else "Failed"
        final_exit_code = exit_code
        with RUN_LOCK:
            RUN_STATE["status"] = final_status
            RUN_STATE["exit_code"] = exit_code
    except Exception as exc:  # noqa: BLE001 - surface any workflow launch/runtime error.
        with RUN_LOCK:
            RUN_STATE["status"] = "Failed"
            RUN_STATE["exit_code"] = 1
        append_log(f"\nWorkflow launcher error: {exc}\n")
    finally:
        final_message = (
            f"\n{workflow_label} completed successfully.\n"
            if final_exit_code == 0
            else f"\n{workflow_label} failed with exit code {final_exit_code}.\n"
        )
        append_log(final_message)
        with RUN_LOCK:
            RUN_STATE["running"] = False
            RUN_STATE["ended_at"] = utc_now_iso()
            RUN_STATE["process"] = None
        finish_sheet_logging(final_status, final_exit_code)


def run_chained_process(steps: list[tuple[str, list[str]]], schedule_config: dict[str, Any] | None = None) -> None:
    final_status = "Completed"
    final_exit_code = 0
    try:
        for index, (label, command) in enumerate(steps, start=1):
            append_log(f"\nStarting step {index}/{len(steps)}: {label}\n")
            append_log("Command:\n  " + " ".join(command) + "\n\n")
            exit_code = run_command(command, label)
            if exit_code != 0:
                final_status = "Failed"
                final_exit_code = exit_code
                append_log(f"\n{label} failed with exit code {exit_code}. Stopping scheduled chain.\n")
                break
            append_log(f"\n{label} completed successfully.\n")
    except Exception as exc:  # noqa: BLE001 - scheduled runner should log any failure.
        final_status = "Failed"
        final_exit_code = 1
        append_log(f"\nScheduled workflow launcher error: {exc}\n")
    finally:
        finished_at = utc_now_iso()
        append_log(
            "\nScheduled daily run completed successfully.\n"
            if final_exit_code == 0
            else f"\nScheduled daily run failed with exit code {final_exit_code}.\n"
        )
        with RUN_LOCK:
            RUN_STATE["running"] = False
            RUN_STATE["status"] = final_status
            RUN_STATE["ended_at"] = finished_at
            RUN_STATE["exit_code"] = final_exit_code
            RUN_STATE["process"] = None
        finish_sheet_logging(final_status, final_exit_code)
        if schedule_config is not None:
            with SCHEDULE_LOCK:
                latest = load_schedule_config()
                latest["last_finished_at"] = finished_at
                latest["last_status"] = final_status
                save_schedule_config(latest)


def validate_form(form: dict[str, Any]) -> str | None:
    load_local_env()
    if not form.get("target_spreadsheet_id", "").strip():
        return "Target Google Sheet ID is required."
    for field in ("start_date", "end_date"):
        try:
            datetime.strptime(form.get(field, ""), "%Y-%m-%d")
        except ValueError:
            return "Start date and end date must use YYYY-MM-DD."
    if not credential_path().exists():
        return "Google credentials were not found. Add service_account_key.json locally or GOOGLE_SERVICE_ACCOUNT_JSON in Replit Secrets."
    status = requirements_status()
    if not status["mode_credentials"] or not status["simplify_credentials"]:
        return "Fresh downloads require MODE_API_KEY_ID, MODE_API_KEY_SECRET, SIMPLIFY_EMAIL, and SIMPLIFY_PASSWORD."
    return None


def validate_schedule_config(config: dict[str, Any]) -> str | None:
    try:
        datetime.strptime(str(config.get("time", "")), "%H:%M")
    except ValueError:
        return "Schedule time must use HH:MM."
    try:
        ZoneInfo(str(config.get("timezone") or SNAPSHOT_TIMEZONE))
    except Exception:
        return "Timezone must be a valid IANA timezone, for example Asia/Kolkata."
    if not str(config.get("target_spreadsheet_id", "")).strip():
        return "Target Google Sheet ID is required."
    return None


def build_scheduled_steps(config: dict[str, Any]) -> tuple[dict[str, Any], list[tuple[str, list[str]]]]:
    form = scheduled_form(config)
    steps = [
        ("Data Workflow", build_command("data", form)),
        ("Assignments", build_command("assignments", form, reuse_downloads=True)),
    ]
    return form, steps


def start_scheduled_run(config: dict[str, Any], trigger: str, *, consume_schedule_slot: bool) -> bool:
    form, steps = build_scheduled_steps(config)
    scheduled_date = today_scheduled_datetime(config).date().isoformat()
    started_at = utc_now_iso()
    with RUN_LOCK:
        if RUN_STATE["running"]:
            return False
        RUN_STATE.update(
            {
                "running": True,
                "workflow": "Scheduled Daily Run",
                "status": "Running",
                "started_at": started_at,
                "ended_at": None,
                "exit_code": None,
                "command": [command for _, command in steps],
                "logs": deque(maxlen=MAX_LOG_LINES),
                "process": None,
            }
        )

    command_summary = f"Trigger={trigger}; window={form['start_date']} to {form['end_date']}; sequence=Data Workflow -> Assignments"
    begin_sheet_logging("Scheduled Daily Run", command_summary)
    append_log(f"Starting Scheduled Daily Run ({trigger})\n")
    append_log(
        f"Window: {form['start_date']} to {form['end_date']}\n"
        "Sequence: Data Workflow -> Assignments\n\n"
    )

    with SCHEDULE_LOCK:
        latest = load_schedule_config()
        if consume_schedule_slot:
            latest["last_started_for_date"] = scheduled_date
        latest["last_started_at"] = started_at
        latest["last_finished_at"] = None
        latest["last_status"] = "Running"
        save_schedule_config(latest)

    thread = threading.Thread(target=run_chained_process, args=(steps, config), daemon=True)
    thread.start()
    return True


def scheduler_loop() -> None:
    while True:
        try:
            with SCHEDULE_LOCK:
                config = load_schedule_config()
            if schedule_is_due(config):
                form_error = validate_form(scheduled_form(config))
                if form_error:
                    now = utc_now_iso()
                    scheduled_date = today_scheduled_datetime(config).date().isoformat()
                    with SCHEDULE_LOCK:
                        latest = load_schedule_config()
                        latest["last_started_for_date"] = scheduled_date
                        latest["last_started_at"] = now
                        latest["last_finished_at"] = now
                        latest["last_status"] = "Failed"
                        save_schedule_config(latest)
                    append_log(f"\nScheduled run skipped because settings are invalid: {form_error}\n")
                else:
                    started = start_scheduled_run(config, "schedule", consume_schedule_slot=True)
                    if not started:
                        append_log("\nScheduled run is due, but another workflow is already running. Will try again shortly.\n")
        except Exception as exc:  # noqa: BLE001 - scheduler must keep running.
            append_log(f"\nScheduler error: {exc}\n")
        time.sleep(SCHEDULER_POLL_SECONDS)


def start_scheduler_once() -> None:
    global SCHEDULER_STARTED
    if SCHEDULER_STARTED:
        return
    SCHEDULER_STARTED = True
    thread = threading.Thread(target=scheduler_loop, daemon=True)
    thread.start()


@app.get("/")
def index() -> str:
    start_date, end_date = default_assignment_window()
    return render_template(
        "index.html",
        defaults={
            "target_spreadsheet_id": DEFAULT_TARGET_SPREADSHEET_ID,
            "start_date": start_date,
            "end_date": end_date,
            "skip_snapshot": False,
        },
        requirements=requirements_status(),
    )


@app.get("/schedule")
def schedule_page() -> str:
    config = load_schedule_config()
    next_run = next_scheduled_run(config)
    return render_template(
        "schedule.html",
        config=config,
        next_run=next_run.isoformat(timespec="minutes") if next_run else "Disabled",
        requirements=requirements_status(),
    )


@app.get("/api/schedule")
def schedule_api() -> Any:
    config = load_schedule_config()
    next_run = next_scheduled_run(config)
    return jsonify(
        {
            "config": config,
            "next_run": next_run.isoformat(timespec="minutes") if next_run else None,
            "due": schedule_is_due(config),
        }
    )


@app.post("/api/schedule")
def update_schedule_api() -> tuple[Any, int]:
    payload = request.get_json(force=True)
    config = load_schedule_config()
    editable = {
        "enabled": bool(payload.get("enabled")),
        "time": str(payload.get("time", "08:30")).strip(),
        "timezone": str(payload.get("timezone", SNAPSHOT_TIMEZONE)).strip(),
        "target_spreadsheet_id": str(payload.get("target_spreadsheet_id", DEFAULT_TARGET_SPREADSHEET_ID)).strip(),
        "skip_snapshot": bool(payload.get("skip_snapshot")),
        "dry_run": bool(payload.get("dry_run")),
        "keep_all_candidates": bool(payload.get("keep_all_candidates")),
        "keep_all_jobs": bool(payload.get("keep_all_jobs")),
    }
    error = validate_schedule_config(editable)
    if error:
        return jsonify({"ok": False, "error": error}), 400
    config.update(editable)
    with SCHEDULE_LOCK:
        save_schedule_config(config)
    next_run = next_scheduled_run(config)
    return jsonify({"ok": True, "next_run": next_run.isoformat(timespec="minutes") if next_run else None})


@app.post("/api/schedule/run-now")
def run_schedule_now_api() -> tuple[Any, int]:
    config = load_schedule_config()
    error = validate_schedule_config(config)
    if error:
        return jsonify({"ok": False, "error": error}), 400
    form = scheduled_form(config)
    form_error = validate_form(form)
    if form_error:
        return jsonify({"ok": False, "error": form_error}), 400
    if not start_scheduled_run(config, "manual schedule run", consume_schedule_slot=False):
        return jsonify({"ok": False, "error": "A workflow is already running."}), 409
    return jsonify({"ok": True})


@app.post("/run/<workflow>")
def run_workflow(workflow: str) -> tuple[Any, int]:
    form = request.get_json(force=True)
    label = "Data Workflow" if workflow == "data" else "Assignments"

    error = validate_form(form)
    if error:
        return jsonify({"ok": False, "error": error}), 400

    try:
        command = build_command(workflow, form)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    with RUN_LOCK:
        if RUN_STATE["running"]:
            return jsonify({"ok": False, "error": "A workflow is already running."}), 409

        RUN_STATE.update(
            {
                "running": True,
                "workflow": label,
                "status": "Running",
                "started_at": utc_now_iso(),
                "ended_at": None,
                "exit_code": None,
                "command": command,
                "logs": deque(maxlen=MAX_LOG_LINES),
                "process": None,
            }
        )
        RUN_STATE["logs"].append(f"Starting {label}\n")

    begin_sheet_logging(label, "Command: " + " ".join(command))
    append_log(f"Starting {label}\n")
    append_log("Command:\n  " + " ".join(command) + "\n\n")

    thread = threading.Thread(target=run_process, args=(command, label), daemon=True)
    thread.start()
    return jsonify({"ok": True})


@app.post("/stop")
def stop_workflow() -> tuple[Any, int]:
    with RUN_LOCK:
        process = RUN_STATE.get("process")
        if not RUN_STATE["running"] or process is None:
            return jsonify({"ok": False, "error": "No workflow is running."}), 400
        RUN_STATE["status"] = "Stopping"
    append_log("\nStop requested. Terminating workflow...\n")

    try:
        os.killpg(os.getpgid(process.pid), signal.SIGTERM)
    except Exception:
        process.terminate()
    return jsonify({"ok": True})


@app.get("/status")
def status() -> Any:
    schedule_config = load_schedule_config()
    next_run = next_scheduled_run(schedule_config)
    with RUN_LOCK:
        return jsonify(
            {
                "running": RUN_STATE["running"],
                "workflow": RUN_STATE["workflow"],
                "status": RUN_STATE["status"],
                "started_at": RUN_STATE["started_at"],
                "ended_at": RUN_STATE["ended_at"],
                "exit_code": RUN_STATE["exit_code"],
                "logs": list(RUN_STATE["logs"]),
                "requirements": requirements_status(),
                "schedule": {
                    "enabled": schedule_config.get("enabled", True),
                    "time": schedule_config.get("time", "08:30"),
                    "timezone": schedule_config.get("timezone", SNAPSHOT_TIMEZONE),
                    "next_run": next_run.isoformat(timespec="minutes") if next_run else None,
                    "last_status": schedule_config.get("last_status"),
                    "last_started_at": schedule_config.get("last_started_at"),
                    "last_finished_at": schedule_config.get("last_finished_at"),
                },
            }
        )


@app.get("/health")
def health() -> Any:
    return jsonify({"ok": True})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5001"))
    start_scheduler_once()
    app.run(host="0.0.0.0", port=port, debug=os.environ.get("FLASK_DEBUG") == "1", use_reloader=False)
