# VMS Marriott Pipeline

Automated pipeline for the Marriott VMS (Vendor Management System) weekly staffing workflow. Downloads shift data from Mode and Simplify, runs the assignment eligibility engine, and produces action queues for the ops team directly in Google Sheets.

---

## What It Does

1. **Data Workflow** — Pulls the latest shift data from Mode Analytics and Simplify VMS, and writes raw source tabs to a Google Sheet.
2. **Assignment Engine** — Runs the eligibility decision logic on every shift and produces sorted action queues (`Final Assignment`, `Job Request`, `CAN Upload`, etc.) that ops works from in Simplify.
3. **Dashboard UI** — A local Flask web app to trigger runs, monitor live logs, and configure the daily schedule.

---

## Docs

| File | What it covers |
|---|---|
| [`ASSIGNMENT_LOGIC.md`](ASSIGNMENT_LOGIC.md) | Plain-English explanation of the eligibility engine, decision tree, and all output tabs |
| [`MARRIOTT_SOP.md`](MARRIOTT_SOP.md) | Business context, data sources, sheets structure, manual touchpoints, known issues |
| [`CODEBASE_OVERVIEW.md`](CODEBASE_OVERVIEW.md) | Architecture, file structure, entry points, credential setup |

---

## Quick Start

### 1. Clone and set up

```bash
git clone https://github.com/rchoutre-ops/VMS-Marriott.git
cd VMS-Marriott
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Set up credentials

Copy `.env.example` to `.env` and fill in all values (see [Credentials](#credentials) below):

```bash
cp .env.example .env   # then open and fill in
```

Place your Google Service Account key file in the project root as `service_account_key.json`.

### 3. Start the dashboard

```bash
.venv/bin/python app.py
```

Open [http://127.0.0.1:5001](http://127.0.0.1:5001) in your browser. Sign in with your `@instawork.com` or `@dr.instawork.com` Google account.

---

## Credentials

All credentials go in `.env`:

| Variable | Where to get it |
|---|---|
| `SIMPLIFY_EMAIL` | Your Simplify VMS login email |
| `SIMPLIFY_PASSWORD` | Your Simplify VMS password |
| `MODE_API_KEY_ID` | Mode Analytics → Account → API Tokens |
| `MODE_API_KEY_SECRET` | Same as above |
| `GOOGLE_CLIENT_ID` | Google Cloud Console → OAuth 2.0 credentials |
| `GOOGLE_CLIENT_SECRET` | Same as above |
| `FLASK_SECRET_KEY` | Any random string (run `python3 -c "import secrets; print(secrets.token_hex(32))"`) |

**Google Service Account** (`service_account_key.json`): Download from Google Cloud Console → Service Accounts → Keys. Share your target Google Sheet and the Shared Drive folder with the service account email.

---

## Project Structure

```
VMS-Marriott/
├── app.py                  # Flask web dashboard — routes, scheduler, OAuth
├── data_workflow.py        # CLI entry point for the Data Workflow step
├── assignments.py          # Assignment eligibility engine + output tab builders
├── marriott_workflow.py    # Core pipeline: Mode/Simplify download, tab building, Sheets upload
├── requirements.txt        # Python dependencies
├── templates/              # Flask HTML templates (base, index, login, schedule)
├── static/                 # CSS and JS for the dashboard
├── downloads/              # Local cache for Mode CSV and Simplify XLSX files
├── ASSIGNMENT_LOGIC.md     # How the eligibility engine works
├── MARRIOTT_SOP.md         # Business SOP and operational reference
└── CODEBASE_OVERVIEW.md    # Developer architecture guide
```

---

## Running the Pipeline

### From the Dashboard (recommended)

1. Open [http://127.0.0.1:5001](http://127.0.0.1:5001)
2. Set the date range and target Google Sheet ID in the **Run Controls** sidebar
3. Click **Run Data Workflow** → downloads Mode + Simplify data and writes raw tabs
4. Click **Run Assignments** → runs the eligibility engine and writes action queue tabs

**Options:**
- **Skip Drive snapshot** — enabled by default for testing; disable for production runs
- **Reuse existing downloads** — skips re-downloading from Mode/Simplify and uses cached files
- **Dry run** — builds all data but doesn't write to Sheets

### From the CLI

```bash
# Full pipeline (download + assignments)
.venv/bin/python assignments.py \
  --workdir . \
  --target-spreadsheet-id YOUR_SHEET_ID \
  --google-credentials service_account_key.json \
  --start-date 2026-05-30 \
  --end-date 2026-06-19

# Data only (no assignment logic)
.venv/bin/python data_workflow.py \
  --workdir . \
  --target-spreadsheet-id YOUR_SHEET_ID \
  --google-credentials service_account_key.json \
  --start-date 2026-05-30 \
  --end-date 2026-06-19 \
  --no-snapshot

# Reuse cached downloads (skip Mode + Simplify)
.venv/bin/python assignments.py [options] --skip-downloads
```

---

## Output Tabs (Google Sheet)

After a full run, the target Google Sheet will contain:

**Raw data tabs** (from Data Workflow):

| Tab | Source |
|---|---|
| `raw data` | Mode CSV — shift list |
| `Mode` | Mode CSV — with decision formula columns added |
| `Open & Closed` | Simplify — full assignment history |
| `Open Active` | Simplify — currently open assignments |
| `candidate details` | Simplify — candidate records |
| `job status` | Simplify — job postings |

**Action queue tabs** (from Assignments):

| Tab | What it is |
|---|---|
| `Final Assignment` | Every shift with its Action (ASSIGNED / JOB REQUEST / CAN UPLOAD / REVIEW) |
| `job request` | Shifts needing a new Simplify job posting |
| `can upload` | Workers needing a Candidate record created in Simplify |
| `can output` | CAN Upload + blank Candidate ID for post-import tracking |
| `upload` | Direct assignments ready for Simplify import |
| `amend review` | Diagnostic: dept name mismatch rows (also in Job Request) |
| `provisional match` | Shifts matched via provisional dept code — needs verification |

---

## Notes

- **Mode Redshift timeouts** — Mode's underlying Redshift query occasionally times out (WLM `ExecuteOverGMins`). When this happens the pipeline will look for the most recent successful run from the past few hours and reuse it. If none exists, use `--skip-downloads` with cached data.
- **Simplify page 2 always returns HTTP 500** — this is a known Simplify server issue. Page 1 contains all needed data; page 2 is skipped automatically.
- **Python 3.9 / LibreSSL warnings** — harmless warnings from Google auth libraries. No action needed.
