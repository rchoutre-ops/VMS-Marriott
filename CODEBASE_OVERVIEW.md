# VMS Marriott — Codebase Overview

## What This System Does

This is an **automation pipeline** that bridges two external systems — **Mode Analytics** (Instawork's reporting platform) and **Simplify VMS** (Marriott's vendor management system) — and pushes processed data into a **Google Sheet** used by Marriott operations staff.

Every day the pipeline:
1. Downloads fresh shift data from Mode and assignment/candidate/job data from Simplify.
2. Transforms the raw data into structured tabs in a Google Sheet.
3. Runs an assignment decision engine to determine what action each shift row needs.
4. Builds output tabs (Upload, Job Request, CAN Upload) that ops staff use to create assignments in Simplify.
5. Saves a dated snapshot copy in a Marriott shared Google Drive folder.

---

## Repository Structure

```
.
├── app.py                  # Flask web UI — the main entry point for Replit
├── marriott_workflow.py    # Core pipeline: data download, transform, upload to Sheets
├── assignments.py          # Assignment decision engine and output tab builders
├── data_workflow.py        # Thin entrypoint: runs raw-data workflow (invoked by app.py)
├── run_daily.py            # Standalone daily runner (for Replit cron)
├── backfill.py             # One-off script to replay missed days
├── requirements.txt        # Python dependencies
├── templates/
│   ├── index.html          # Web UI: manual run page
│   └── schedule.html       # Web UI: schedule configuration page
└── static/
    ├── app.js              # Frontend JS: manual workflow controls + live log tail
    ├── schedule.js         # Frontend JS: schedule page controls
    └── styles.css          # Shared styles
```

---

## Entry Points

### 1. `app.py` — Flask Web Application (Primary)

The main way to run the system on Replit. It starts a Flask server that serves a web UI and exposes API endpoints to trigger workflows manually or on a schedule.

**Key constants defined here:**
- `DEFAULT_TARGET_SPREADSHEET_ID` — the active Marriott assignments Google Sheet
- `RAW_HISTORY_SPREADSHEET_ID` — a separate sheet that accumulates historical raw data
- `RAW_DATA_FOLDER_ID` / `ASSIGNMENT_FOLDER_ID` — Google Drive folders for snapshots
- `LOG_SPREADSHEET_ID` — a Google Sheet used for run audit logs

**Scheduler:** On startup, a background thread (`scheduler_loop`) polls every 30 seconds. If the configured daily time is reached and the run hasn't fired yet today, it automatically triggers a two-step chain: Data Workflow → Assignments.

**API endpoints:**

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/` | Serve the manual run UI |
| `GET` | `/schedule` | Serve the schedule configuration UI |
| `POST` | `/run/<workflow>` | Trigger `data` or `assignments` workflow manually |
| `POST` | `/stop` | Kill the running workflow process |
| `GET` | `/status` | Poll run state, logs, schedule info (used by frontend) |
| `GET` | `/api/schedule` | Get schedule config as JSON |
| `POST` | `/api/schedule` | Update schedule config |
| `POST` | `/api/schedule/run-now` | Trigger the full scheduled chain immediately |
| `GET` | `/health` | Health check |

**Google Sheet logging:** Every run session is logged to a dedicated Google Sheet (`LOG_SPREADSHEET_ID`) in two tabs:
- `Latest Logs` — cleared and overwritten on each run (easy to monitor live)
- `Full Log History` — append-only; permanent audit trail for all runs

---

### 2. `run_daily.py` — Standalone Cron Runner

Used by Replit's built-in cron job. Mirrors exactly what the Flask scheduler does: runs Data Workflow, then Assignments, for today's operating window. Useful as a fallback if the Flask app isn't running.

### 3. `backfill.py` — Missed Day Replay

A one-off script with a hardcoded list of missed dates (`MISSED_DAYS`). For each day it computes the correct operating window and runs both workflows. Downloads once fresh on the first day, then reuses cached files for subsequent days.

---

## Core Pipeline: `marriott_workflow.py`

This file contains all the logic for downloading data and building the Google Sheet tabs.

### Data Sources

**Mode Analytics**
- Report: `https://app.mode.com/instawork/reports/9b580f8ef3ca`
- Contains the `VMS - Marriott` query which exports shift-level data (who worked where, when, at what rate).
- Downloaded via the Mode REST API using `MODE_API_KEY_ID` and `MODE_API_KEY_SECRET`.
- Strategy: scan recent report runs for a successful VMS - Marriott query run matching the requested date range. If none found, trigger a fresh run. Retry up to `mode_max_retries` times.

**Simplify VMS** (`https://marriott.simplifyvmsapp.com`)
- Three reports are downloaded via a multi-step browser-like session:
  1. **Active Assignments Details - Vendor** — all open/closed/cancelled assignments
  2. **Candidate Details** — all known candidate records
  3. **Job Status Report** — all job postings and their status
- Login uses `SIMPLIFY_EMAIL` and `SIMPLIFY_PASSWORD` with CSRF token handling.
- Export is done via the Sigma reporting API (`api-v3reporting.simplifyvmsapp.com`).

### Date Window

Marriott uses **Saturday–Friday operating weeks**. The system always works with a 3-week window: the previous week (N-1), the current week (N), and next week (N+1). This window is computed automatically from today's IST date.

```
Example (today = Wednesday in IST):
  Previous Saturday (anchor - 7 days) → Next Friday (anchor + 13 days)
```

### Tab Building (`build_tabs`)

Takes the downloaded files and produces 6 source tabs as pandas DataFrames:

| Tab Name | Source | Description |
|----------|--------|-------------|
| `raw data` | Mode CSV | Unmodified 21-column Mode export for audit |
| `Mode` | raw data | Normalized: `fg_` prefix stripped, `location` split into location + `Department_Code` |
| `Open & Closed` | Simplify Assignments | Rows with status Open, Closed, or Cancelled |
| `Open Active` | Open & Closed | Only Open rows; adds `Con1` and `Con 2` helper join keys |
| `candidate details` | Simplify Candidates | Active candidates (Is Active? = Yes by default) |
| `job status` | Simplify Job Status | Sourcing jobs with Job End Date ≥ window start |

### `Open Active` join keys (critical for matching logic)

```
Con1  = Department Name + Vendor Tracking ID 1 + Client ST Bill Rate
Con 2 = Work Location ID + Vendor Tracking ID 1 + Client ST Bill Rate
```

These are pre-computed as Python strings mimicking how Google Sheets `CONCATENATE` would render numeric values.

### Uploading to Google Sheets (`upload_tabs` / `append_tabs`)

- `upload_tabs`: Clears and replaces each tab. Used for the active assignments sheet.
- `append_tabs`: Reads existing rows, deduplicates by `shift_id`/`shiftgroup_id` (or other stable keys), and only appends new rows. Used for the raw history sheet.

Both functions handle tab creation, resize, chunked writes (2000 rows per API call), and retry on quota errors.

### Drive Snapshots (`snapshot_to_shared_drive`)

After uploading, a dated copy of the spreadsheet is created in the configured shared Google Drive folder:
- Raw data snapshots → `RAW_DATA_FOLDER_ID`
- Assignment snapshots → `ASSIGNMENT_FOLDER_ID`

Name format: `"Raw Data - Tuesday Marriott(05/09 - 05/29)"`

---

## Assignment Decision Engine: `assignments.py`

This is the intelligence layer. After raw tabs are in the sheet, this module figures out what action each shift row needs.

### Department Code Override Table

Before running decisions, `apply_dept_overrides` patches known data quality issues in the Mode export:

| Site | Issue | Fix |
|------|-------|-----|
| `73R61` (Half Moon Bay) | Mode has blank dept | Inferred from shift name using cross-property pattern |
| `42SRG` (W Minneapolis) | New VMS site, no dept | Best-guess Banquets, flagged for confirmation |
| `33711` (VEA Newport Beach) | Only one Simplify dept | Auto-mapped to `33711_0230_00:Banquets` |
| `33806` (Oakland) | Mode prepends "M" to dept name | Stripped to match Simplify (`MClub` → `Club`) |
| `21GB2` (Gaylord Pacific) | Accent typo | `PCH Cafe` → `PCH Café` |

Additionally, `_DEPT_REVIEW_FLAGS` marks rows that are ambiguous and need ops review without changing the dept code (e.g., NOLA Club Level rows that Mode classifies as Casual Rest).

### Local Decision Computation (`compute_local_decisions`)

For every row in the Mode tab, the engine computes:

| Column | Formula Logic |
|--------|--------------|
| `Perfect Match` | `Department_Code + worker_id + partner_rate` |
| `Perfect AID` | Find an Open Active assignment where `Con1 = Perfect Match` AND shift date is within the assignment's start/end date |
| `2nd Best Match` | `fieldglass_site_name + worker_id + partner_rate` |
| `2nd Best AID` | Find an Open Active assignment where `Con 2 = 2nd Best Match` AND shift date is within start/end |
| `validation_for_Department` | Compare Mode's dept code with the department on the 2nd Best AID (`OK` / `Not OK` / `AID Not Found`) |
| `CAN ID` | XLOOKUP `worker_id` → `Vendor Tracking ID 1` in Open & Closed → `Candidate ID` |
| `Existing Jobs` | XLOOKUP `2nd Best AID` → `Assignment ID` in Open & Closed → `Job ID` |

These computed values are both written to the Google Sheet as live formulas (cols W–AM) AND computed locally in Python to build the output tabs deterministically.

### Assignment Decision Tree

```
Perfect AID ≠ 0
  └─→ DO NOTHING — worker already has an open assignment for this dept/rate/date.
      (Provisional dept override → also goes into "provisional match" review tab)

Perfect AID = 0, 2nd Best AID ≠ 0, validation = OK
  └─→ SKip — dept numeric code matches even if display name differs.
      (No new job needed; existing assignment covers the worker.)

Perfect AID = 0, 2nd Best AID ≠ 0, validation = Not OK
  └─→ JOB REQUEST — worker has an open assignment but at the wrong dept.
      Ops must create a new job posting for the correct dept.

Perfect AID = 0, 2nd Best AID = 0, CAN ID present
  └─→ JOB REQUEST — worker is in Simplify but has no open assignment.
      Ops must create a new job posting. (Available Jobs chosen manually.)

Perfect AID = 0, 2nd Best AID = 0, CAN ID missing
  └─→ CAN UPLOAD — worker has never been in Simplify.
      Ops must create a Candidate record first.
```

### Output Tabs Built

| Tab | Description |
|-----|-------------|
| `upload` | Rows ready for Simplify direct assignment (empty until ops manually selects Available Jobs in Mode col AC) |
| `job request` | Shifts needing a new job posting (no AID + CAN ID present, or wrong dept) |
| `can upload` | New workers needing a Candidate ID (deduplicated by `worker_id`) |
| `can output` | CAN Upload + blank Candidate ID and Remarks columns (ops fills after Simplify import) |
| `amend review` | Diagnostic reference: 2nd Best AID rows with dept display-name mismatch |
| `provisional match` | Perfect AID found but via a provisional dept override; needs Marriott confirmation |
| `Output` | Post-import tracking template (17 columns; filled by ops after upload) |
| `Sheet8` | Sensitive candidate creation source data (id, name, email, ssn, dob) |
| `Summary` | Operator scratch / QA notes |

**Red highlighting:** Rows where `Should be reviewed = Yes` are painted with a light-red background in the `job request`, `can upload`, `amend review`, and `provisional match` tabs.

---

## Credentials & Environment Variables

| Variable | Required for |
|----------|-------------|
| `MODE_API_KEY_ID` | Mode API authentication |
| `MODE_API_KEY_SECRET` | Mode API authentication |
| `SIMPLIFY_EMAIL` | Simplify VMS login |
| `SIMPLIFY_PASSWORD` | Simplify VMS login |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | Google Sheets/Drive API (Replit secret). Falls back to `./service_account_key.json` locally |
| `SIMPLIFY_EXPORT_EMAIL` | Email for Simplify Sigma CSV export endpoint (default: `mcaplan@instawork.com`) |

Credentials can be set as environment variables, Replit secrets, or stored in a `.env` file in the project root.

---

## Google Sheets Architecture

| Sheet | ID | Purpose |
|-------|----|---------|
| Active Assignments Sheet | `1gMYap7mOK17l7lOhPtBohGjJoC1FsPWfjyywXwFjLWk` | The live ops workbook — cleared and rebuilt each run |
| Raw History Sheet | `1whZ27g2ir6OP-ncmW9kA6R43CWMuFPsiK-IFLQoqi-8` | Append-only historical raw data archive |
| Log Sheet | `1veHtzoByPQfD7CDynmxJOTiH2ZuksqkxUnmG96alwYE` | Run audit logs (Latest Logs + Full Log History) |

---

## Workflow Execution Modes

The pipeline can be run in several modes controlled by CLI flags (passed by `app.py`):

| Flag | Effect |
|------|--------|
| `--skip-downloads` | Reuse cached files in `downloads/` instead of re-downloading |
| `--force-fresh-mode-download` | Always trigger a new Mode report run, ignore cache |
| `--no-upload` | Build DataFrames and print counts only; don't write to Google Sheets |
| `--no-snapshot` | Skip the dated Drive snapshot |
| `--no-assignment-logic` | Skip decision engine and output tabs (raw data only) |
| `--append-raw-tabs` | Append new rows to raw history sheet instead of overwriting |
| `--keep-all-candidates` | Don't filter candidates to active-only |
| `--keep-all-jobs` | Don't filter jobs to Sourcing status with future end dates |

---

## Dependencies

```
Flask               — Web UI server
requests            — HTTP calls to Mode and Simplify APIs
beautifulsoup4      — HTML parsing for Simplify login CSRF tokens
pandas              — DataFrame processing
numpy               — Numeric type handling
openpyxl            — Reading Simplify XLSX exports
google-api-python-client — Google Sheets and Drive API
google-auth         — Google service account authentication
```

---

## Local File Layout (Runtime)

Downloads are cached locally and reused by `--skip-downloads`:

```
downloads/
  mode_raw/
    mode_marriott_run_{run_token}/
      vms_marriott_{run_token}_{qr_token}.csv
    current_marriott.csv          ← symlink to the latest CSV
  simplify_raw/
    pages/
      Active_Assignments_Details_-_Vendor__page_1_{id}.xlsx
      Candidate_Details__page_1_{id}.xlsx
      Job_Status_Report__page_1_{id}.xlsx
```

---

## Known Data Quality Issues Handled

1. **Mode `fg_` prefix** — `fieldglass_site_name` values like `fg_73R75` are stripped to `73R75`.
2. **Location semicolon split** — Mode's `location` field contains `"Property Name; Department_Code"`. The pipeline splits these into two separate columns.
3. **Department name divergence** — Simplify and Mode sometimes display the same department differently (e.g., `Banquets` vs `Half Moon Bay,Banquets`). The engine compares the 4-digit numeric segment (e.g., `0230`) to avoid false mismatches.
4. **Numeric concatenation** — Google Sheets `CONCATENATE` normalizes numbers (e.g., `"24.50"` → `"24.5"`). The Python `google_concat_value()` function mirrors this exactly so local join keys match the sheet formula results.
5. **Mode report partial failure** — Mode marks a whole report run as "failed" if any sub-query fails, but the Marriott sub-query may still have succeeded. The downloader inspects individual query_run states, not the top-level report state.
