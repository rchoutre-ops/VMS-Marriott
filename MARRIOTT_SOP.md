# Marriott Direct Assignment — How It Works

> Summarized from: *Marriott SOP - Current Understanding - Sankalp.pdf*  
> Last updated with WK22 analysis (Jun 5, 2026)

---

## The Big Picture

Instawork supplies workers (pros) to Marriott properties. Marriott uses a system called **Simplify VMS** to track every worker as a formal assignment. **If a shift isn't recorded as an assignment in Simplify, Marriott won't pay us for it.**

The problem: Instawork's system (Mode) knows which pro worked which shift. Simplify knows which assignments currently exist. This pipeline bridges the two — every day it matches Mode shift rows against Simplify assignments and figures out what action is needed.

---

## The Two Data Sources

### Mode (Instawork side)
- **What it is:** Instawork's internal reporting tool. Contains a query called `VMS - Marriott` that exports one row per pro-shift.
- **Key fields per row:** property site, pro name/ID, shift date, position, department, bill rate.
- **Known issue:** The Mode report wrapper often fails (Redshift query timeouts) but the Marriott sub-query usually still succeeds. The pipeline specifically checks sub-query status — not the report-level status — to get the CSV.

### Simplify VMS (Marriott client side)
Three reports are downloaded from `marriott.simplifyvmsapp.com`:
1. **Active Assignments Details - Vendor** → current open/closed/cancelled assignments
2. **Candidate Details** → all known Marriott candidate records (MAR-CD-XXXXXX IDs)
3. **Job Status Report** → all job postings and whether they're still open for assignment (MAR-JB-XXXXXX IDs)

---

## The Date Window

Marriott operates **Saturday–Friday weeks**. The pipeline always works with **3 consecutive weeks: Past 2 + Past 1 + Current**. No future weeks, because those shifts haven't happened yet.

**Example (run on Wednesday May 20, 2026):**
```
Anchor Saturday = May 16
Window = May 2 (anchor - 14 days) → May 22 (anchor + 6 days)
Sheet title = "Wednesday Marriott(05/02 - 05/22)"
```

---

## The Google Sheet Structure

The pipeline populates a Google Sheet with two kinds of tabs:

### Source Tabs (auto-populated by pipeline)
| Tab | What's in it |
|-----|-------------|
| `raw data` | Unmodified Mode export — 21 columns, audit copy only |
| `Mode` | Normalized version of raw data + decision columns W–AM |
| `Open & Closed` | All Simplify assignments (Open, Closed, Cancelled) |
| `Open Active` | Only Open assignments, with two helper join-key columns (Con1, Con 2) |
| `candidate details` | Simplify candidate records (active only by default) |
| `job status` | Simplify job postings (Sourcing status, not yet expired) |

### Output Tabs (auto-populated except where noted)
| Tab | What's in it |
|-----|-------------|
| `upload` | Rows ready to push into Simplify as new assignments *(ops fills this once they pick a Job ID)* |
| `job request` | Shifts that need a new job posting created in Simplify first |
| `can upload` | Brand-new workers who need a Candidate ID created in Simplify first |
| `can output` | Same as can upload + blank columns for the returned Candidate ID *(ops fills after import)* |
| `Output` | Post-import tracking: Submission ID, status, Shift ID lookup *(ops fills after upload)* |
| `Sheet8` | Sensitive source data for new candidates: name, email, SSN, DOB *(ops pastes manually)* |

---

## The Mode Tab: Column Layout

The Mode tab is the engine. Columns A–V are data, W–AM are formulas.

### Data columns (A–V)
| Col | Field | Notes |
|-----|-------|-------|
| A | `fieldglass_site_name` | Property code, `fg_` prefix stripped (e.g. `73R61`) |
| B | `name` | Hotel display name |
| C | `location` | Property name (before the `;` in raw data) |
| **D** | `Department_Code` | **Extracted from after the `;`** — this is the critical join key |
| E–H | position, shift_name, first_name, last_name | |
| **I** | `date_of_shift_start` | The shift date — used in all date-range checks |
| **J** | `partner_rate` | Instawork's bill rate to Marriott |
| K–Q | pro_rate, ot/dt rates, email, full_name, mark_up, state_code | |
| **R** | `worker_id` | Instawork pro ID — the main worker join key |
| S–V | first_shift_timestamp, dob_mmdd, shift_id, shiftgroup_id | |

### Decision columns (W–AM) — auto-written by pipeline as Google Sheets formulas
| Col | Name | Formula logic |
|-----|------|--------------|
| W | Perfect Match | `Department_Code + worker_id + partner_rate` |
| X | Perfect AID | Find Open Active assignment where Con1 = W AND shift date is inside assignment window |
| Y | 2nd Best Match | `fieldglass_site_name + worker_id + partner_rate` |
| Z | 2nd Best AID | Find Open Active assignment where Con 2 = Y AND shift date is inside assignment window |
| AA | validation_for_Department | Does Mode's dept code match the dept on the 2nd Best AID? (`OK` / `Not OK` / `AID Not Found`) |
| AB | CAN ID | Look up worker_id in Simplify Open & Closed to get their Candidate ID (0 = new worker) |
| AC | **Available Jobs** | **MANUAL** — ops picks the matching Job ID from `job status` |
| AD | state | MANUAL |
| AE | Assigned By | MANUAL |
| AF | Existing Jobs | Job ID tied to the 2nd Best AID |
| AG–AI | Comments, City tax, State tax | MANUAL |
| AJ | Start Date | Pulled from `job status` once AC is filled |
| AK | End Date | Pulled from `job status` once AC is filled |
| AL | Shift Start Date | Shift date formatted as `mm/dd/yyyy` |
| AM | Concat | `CAN ID + Job ID + Shift Date` — lookup key used by the Output tab |

---

## The Decision Tree — What Happens to Each Row

Every Mode row lands on exactly one outcome. Read top to bottom; first match wins.

```
┌─────────────────────────────────────────────────────────────────┐
│ Perfect AID (col X) ≠ 0?                                        │
│   YES → CASE A: Worker is already correctly assigned.           │
│          Do nothing.  (~78% of rows)                            │
└────────────────────────────────┬────────────────────────────────┘
                                 │ NO
                                 ▼
┌─────────────────────────────────────────────────────────────────┐
│ 2nd Best AID (col Z) ≠ 0?                                       │
│                                                                  │
│   YES + validation = "OK"                                        │
│       → CASE B-SKIP: Same assignment, just different dept        │
│         display name. 4-digit dept code is identical.            │
│         Do nothing. Worker is covered.                           │
│                                                                  │
│   YES + validation = "Not OK"                                    │
│       → CASE B-AMEND: Worker has an assignment but in the        │
│         wrong department. Goes to JOB REQUEST tab.               │
│         Reason: "Existing AID [X] in wrong dept — new job        │
│         needed for [correct dept]"                               │
└────────────────────────────────┬────────────────────────────────┘
                                 │ NO (both AIDs = 0)
                                 ▼
┌─────────────────────────────────────────────────────────────────┐
│ CAN ID (col AB) = 0?                                            │
│   YES → CASE C1: Brand new worker, not in Simplify yet.         │
│          Goes to CAN UPLOAD. Ops creates Candidate ID.          │
│          Then returns to C2 or C3 next run.                     │
└────────────────────────────────┬────────────────────────────────┘
                                 │ NO (CAN ID exists)
                                 ▼
┌─────────────────────────────────────────────────────────────────┐
│ Matching Sourcing job exists in job status?                      │
│   YES → CASE C2: Ready to assign. Ops picks the Job ID in       │
│          Mode!AC, then cuts the row into the UPLOAD tab.        │
│                                                                  │
│   NO  → CASE C3: No matching job posting exists yet.            │
│          Goes to JOB REQUEST tab. Ops asks Becky (Marriott PM)  │
│          to create the job. Returns to C2 after approval.       │
└─────────────────────────────────────────────────────────────────┘
```

---

## The Worker Lifecycle (Multi-Week View)

A brand-new worker progresses through the system over several weeks:

```
Week 1:  CAN Upload  →  Simplify creates MAR-CD-XXXXXX (Candidate ID)
Week 2:  Job Request →  Hotel creates  MAR-JB-XXXXXX  (Job ID)
Week 3:  Upload      →  Simplify creates MAR-AM-XXXXXX (Assignment ID)
Week 4+: SKIP (Case A/B-skip) — worker is fully covered, no action needed
```

This is why the same workers appear in JR sheets multiple days in a row — their job request is pending Becky's approval, so they re-surface on each daily run until the AID exists.

---

## What Ops Does Manually

The pipeline automates ~95% of the work. The remaining manual steps:

| Step | Where | What ops does |
|------|-------|--------------|
| Pick Available Jobs | Mode col AC | For each C2 row, find the matching Job ID in `job status` (same property + dept + position + rate) |
| Fill state, Assigned By | Mode cols AD, AE | Two-letter state code, operator name |
| Cut rows to Upload | `upload` tab | Once AC is filled, paste the row in the upload format |
| Sheet8 | `Sheet8` tab | Paste new pro source data (name, SSN, DOB) from internal Instawork tooling |
| CAN Output | `can output` tab | After importing `can upload` to Simplify, paste returned Candidate IDs in col G |
| Output | `Output` tab | After upload to Simplify, paste Submission ID (MAR-SB-XXXXXX) and status |

---

## Typical Daily Numbers (WK22 reference)

| Metric | Count |
|--------|-------|
| Total Mode rows | ~882 |
| Case A — already assigned (skip) | ~685 (78%) |
| Case B — 2nd Best AID (skip or amend) | ~168 (19%) |
| Case C1 — CAN Upload (new candidates) | ~5–41 |
| Case C3 — Job Request (new job needed) | ~20–49 |
| Case C2 — Upload-ready (waiting on AC) | ~4 |

---

## Known Issues & Open Questions

| # | Severity | Issue |
|---|----------|-------|
| 1 | High | **Stale Simplify data** — workers registered in Simplify after our download appear as CAN Upload instead of JR. Fix: run Simplify download as close to processing time as possible. |
| 2 | Medium | **Available Jobs (col AC) has no formula** — matching rule is property + dept + position + rate, but requires human judgment for edge cases. Could we automate this? |
| 3 | Medium | **Encoding drift** — Mode says "PCH Cafe & Market" (plain e), Simplify has "PCH Café & Market" (accented). Handled by override table but needs Marriott confirmation. |
| 4 | Low | **Mode!AD state** could be auto-derived from Open & Closed via XLOOKUP on worker_id — currently manual. |
| 5 | Low | **Sheet8 SSN handling** — source for new-candidate SSNs requires manual paste from internal Instawork tooling. Compliance review needed before automating. |
| 6 | Low | **Bill rate revisions** — when Marriott revises an AID's rate, a new AID is created. Need to confirm whether Open Active always shows the latest revision only. |

---

## How to Run

```bash
# Full run (downloads fresh data, updates sheet, creates snapshot)
python3 marriott_workflow.py

# Reuse cached downloads (skip Mode + Simplify login)
python3 marriott_workflow.py --skip-downloads

# Raw data only (no assignment logic)
python3 marriott_workflow.py --no-assignment-logic

# Dry run (build everything, don't write to Google Sheets)
python3 marriott_workflow.py --no-upload
```

Or use the **web dashboard** at `http://127.0.0.1:5001` to trigger runs with a button.

---

## Quick Links

| Resource | Link |
|----------|------|
| Active assignments sheet | [Google Sheet](https://docs.google.com/spreadsheets/d/1gMYap7mOK17l7lOhPtBohGjJoC1FsPWfjyywXwFjLWk/edit) |
| Snapshots shared drive | [Google Drive](https://drive.google.com/drive/u/0/folders/0AFPXMexSsMIlUk9PVA) |
| Mode report | [Mode Analytics](https://app.mode.com/instawork/reports/9b580f8ef3ca) |
| Simplify reports | [Simplify VMS](https://marriott.simplifyvmsapp.com/Report/EmbeddedReports/embeddedIndex) |
