#!/usr/bin/env python3
"""Marriott Simplify assignment logic.

This module owns the decision formulas and downstream assignment-output tabs:
Upload, Job Request, CAN Upload, CAN Output, Output, Sheet8, and Summary.
"""

from __future__ import annotations

import math
from datetime import date, datetime
from typing import Any, Callable

import numpy as np
import pandas as pd
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build


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


def google_concat_value(value: Any) -> str:
    """Approximate Google Sheets CONCATENATE display values for helper keys."""
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


def _decision_formulas(row_number: int) -> list[str]:
    """Build the W..AM formula strings for one Mode row."""
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
            '"OK","Not OK"),"AID Not Found")'
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
        f'=TEXT(I{r},"mm/dd/yyyy")',
        f"=CONCATENATE(AB{r},AC{r},AL{r})",
    ]


def apply_mode_decision_formulas(args: Any, mode_row_count: int) -> None:
    """Write headers + formulas for Mode columns W..AM in Google Sheets."""
    if mode_row_count <= 0:
        print("Assignment formula step skipped: Mode has 0 rows, so W..AM formulas were not applied.")
        return
    print("Assignment formula step 1/1: applying Mode decision formulas to columns W..AM.")
    print("  Formula W Perfect Match: CONCATENATE(Department_Code, worker_id, partner_rate).")
    print("  Formula X Perfect AID: FILTER Open Active Assignment ID where Con1 matches and shift date is inside assignment start/end.")
    print("  Formula Y 2nd Best Match: CONCATENATE(fieldglass_site_name, worker_id, partner_rate).")
    print("  Formula Z 2nd Best AID: FILTER Open Active Assignment ID where Con 2 matches and shift date is inside assignment start/end.")
    print("  Formula AA validation_for_Department: compare Mode Department_Code with department on 2nd Best AID.")
    print("  Formula AB CAN ID: XLOOKUP worker_id against Open & Closed Vendor Tracking ID 1 -> Candidate ID.")
    print("  Formula AF Existing Jobs: XLOOKUP 2nd Best AID against Open & Closed Assignment ID -> Job ID.")
    print("  Formula AJ/AK/AL/AM: lookup selected Available Job dates, format shift date, and build upload concat.")
    credentials = Credentials.from_service_account_file(
        str(args.google_credentials),
        scopes=["https://www.googleapis.com/auth/spreadsheets"],
    )
    service = build("sheets", "v4", credentials=credentials, cache_discovery=False)

    service.spreadsheets().values().update(
        spreadsheetId=args.target_spreadsheet_id,
        range="'Mode'!W1:AM1",
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


# ── Department-code override table ──────────────────────────────────────────
# Each entry is a dict with:
#   site           — fieldglass_site_name to match exactly
#   match_col      — 'shift_name', 'dept_code', or 'any'
#   match_value    — case-insensitive substring (or None for 'any')
#   new_dept_code  — replacement Department_Code value
#   note           — shown in Reason column if row is flagged for review
#   needs_confirm  — True → provisional; row stays Should-be-reviewed=Yes even if AID found
_DEPT_OVERRIDES: list[dict] = [
    # ── 73R61 Half Moon Bay — Mode produces blank dept; derive from shift-name
    # cross-property evidence: Charlotte 73R75, Portland 73R94, NOLA 73R44
    {
        "site": "73R61", "match_col": "shift_name", "match_value": "Banquet Bartender",
        "new_dept_code": "73R61_0230_00:Banquets",
        "note": "Dept inferred from shift name (cross-property _0230_=Banquets pattern). Needs Marriott confirmation before first assignment.",
        "needs_confirm": True,
    },
    {
        "site": "73R61", "match_col": "shift_name", "match_value": "Banquet Server",
        "new_dept_code": "73R61_0230_00:Banquets",
        "note": "Dept inferred from shift name (cross-property _0230_=Banquets pattern). Needs Marriott confirmation before first assignment.",
        "needs_confirm": True,
    },
    {
        "site": "73R61", "match_col": "shift_name", "match_value": "Gala Chef",
        "new_dept_code": "73R61_0190_00:Kitchen",
        "note": "Dept inferred from shift name (same _0190_=Kitchen pattern as Charlotte 73R75 / Portland 73R94). Needs Marriott confirmation.",
        "needs_confirm": True,
    },
    {
        "site": "73R61", "match_col": "shift_name", "match_value": "Gala Steward",
        "new_dept_code": "73R61_0192_00:Kitchen Steward",
        "note": "Dept inferred from shift name (NOLA 73R44 maps Dishwasher to _0192_=Kitchen Steward). Needs Marriott confirmation.",
        "needs_confirm": True,
    },
    # ── 42SRG W Minneapolis — new VMS site, never in Simplify; best-guess
    {
        "site": "42SRG", "match_col": "shift_name", "match_value": "Wilber",
        "new_dept_code": "42SRG_0230_00:Banquets",
        "note": "Best-guess: deep-cleaning at VMS Marriott properties historically routes to Banquets (Detroit 337U7, Sacramento 29STY). Needs ops confirmation with W Minneapolis property contact.",
        "needs_confirm": True,
    },
    # ── 33711 VEA Newport Beach — only one Simplify dept; all rows map here
    {
        "site": "33711", "match_col": "any", "match_value": None,
        "new_dept_code": "33711_0230_00:Banquets",
        "note": "Confirmed: 33711_0230_00:Banquets is the only Simplify department for VEA Newport Beach.",
        "needs_confirm": False,
    },
    # ── 33806 Oakland Marriott — Mode has 'MClub', Simplify uses 'Club' (drop M-prefix)
    {
        "site": "33806", "match_col": "dept_code", "match_value": "MClub",
        "new_dept_code": "33806_0019_00:Club",
        "note": "Confirmed: Simplify uses 33806_0019_00:Club; Mode export incorrectly prepends M (MClub).",
        "needs_confirm": False,
    },
    # ── 21GB2 Gaylord Pacific — accent typo in Mode: Cafe → Café
    {
        "site": "21GB2", "match_col": "dept_code", "match_value": "PCH Cafe & Market",
        "new_dept_code": "21GB2_0270_00:PCH Café & Market",
        "note": "Confirmed: Simplify exact spelling is PCH Café & Market (with accent on é).",
        "needs_confirm": False,
    },
]

# Review flags — rows that need ops review WITHOUT changing their dept code.
# These cases are ambiguous and can't be auto-resolved from the data alone.
_DEPT_REVIEW_FLAGS: list[dict] = [
    # 73R44 NOLA — some shift names suggest Club Level (0013/0019) not Casual Rest 2 (0212)
    {
        "site": "73R44", "match_col": "shift_name", "match_value": "MB",
        "note": "Shift name suggests Club Level (M Bar). Mode assigned 73R44_0212_00:Casual Rest 2, but active AIDs exist under 73R44_0013_00/0019_00 (Club Level). Ops must confirm correct department.",
    },
    {
        "site": "73R44", "match_col": "shift_name", "match_value": "Davenport",
        "note": "Shift name suggests Club Level (Davenport restaurant). Mode assigned 73R44_0212_00:Casual Rest 2, but active Club Level AIDs exist. Ops must confirm correct department.",
    },
    # 21GB2 Gaylord Pacific — Busser in PCH Marketplace dept (0270) is likely wrong venue
    {
        "site": "21GB2", "match_col": "position_and_dept", "match_value": ("Busser", "0270"),
        "note": "Busser in PCH Marketplace (21GB2_0270) is likely misclassified. Should be 21GB2_0221_00:Oeste Bar + Terrace or 21GB2_0227_00:Shallow End Grill depending on venue. Ops must confirm.",
    },
]


def apply_dept_overrides(mode_df: pd.DataFrame) -> pd.DataFrame:
    """Apply confirmed/derived department-code overrides to Mode rows.

    Adds three metadata columns that flow through to output tabs:
    - dept_override_note  — explanation of what was changed (empty if no change)
    - dept_needs_confirm  — True when the override is provisional / not yet Marriott-confirmed
    - dept_review_flag    — set on rows flagged for review without a dept code change
    """
    df = mode_df.copy()
    df["dept_override_note"] = ""
    df["dept_needs_confirm"] = False
    df["dept_review_flag"] = ""

    site_col = "fieldglass_site_name"
    dept_col = "Department_Code"
    shift_col = "shift_name"
    pos_col = "position"

    for override in _DEPT_OVERRIDES:
        site = override["site"]
        site_mask = df[site_col].astype(str).str.strip() == site
        mc = override["match_col"]
        mv = override.get("match_value")

        if mc == "any":
            row_mask = site_mask
        elif mc == "shift_name":
            row_mask = site_mask & df[shift_col].astype(str).str.contains(mv, case=False, na=False)
        elif mc == "dept_code":
            row_mask = site_mask & df[dept_col].astype(str).str.contains(mv, case=False, na=False)
        else:
            continue

        n = row_mask.sum()
        if n == 0:
            continue
        old = df.loc[row_mask, dept_col].astype(str).unique()
        df.loc[row_mask, dept_col] = override["new_dept_code"]
        df.loc[row_mask, "dept_override_note"] = override["note"]
        df.loc[row_mask, "dept_needs_confirm"] = override["needs_confirm"]
        print(f"  Dept override [{site}] {mc}='{mv}' → {override['new_dept_code']} ({n} rows, was: {', '.join(old[:3])})")

    for flag in _DEPT_REVIEW_FLAGS:
        site = flag["site"]
        site_mask = df[site_col].astype(str).str.strip() == site
        mc = flag["match_col"]
        mv = flag.get("match_value")

        if mc == "shift_name":
            row_mask = site_mask & df[shift_col].astype(str).str.contains(mv, case=False, na=False)
        elif mc == "position_and_dept":
            pos_val, dept_val = mv
            p_col = pos_col if pos_col in df.columns else site_col
            row_mask = (
                site_mask
                & df[p_col].astype(str).str.contains(pos_val, case=False, na=False)
                & df[dept_col].astype(str).str.contains(dept_val, case=False, na=False)
            )
        else:
            continue

        n = row_mask.sum()
        if n == 0:
            continue
        df.loc[row_mask, "dept_review_flag"] = flag["note"]
        print(f"  Dept review flag [{site}] {mc}='{mv}' → {n} rows flagged for review")

    return df


import re as _re


def _extract_dept_numeric(s: str) -> str:
    """Extract the 4-digit numeric dept code from a dept string or OA Department Code.

    Handles two formats:
      - Mode / Simplify Dept Name:  '73R61_0230_00:...'  → '0230'
      - OA Department Code:         '73 73R61 0230'       → '0230'
    Returns '' if no 4-digit segment is found.
    """
    # Try underscore-delimited format first: e.g. 73R61_0230_00:...
    m = _re.search(r"_(\d{4})_", str(s))
    if m:
        return m.group(1)
    # Fall back to last whitespace-delimited token that is 4 digits: e.g. '73 73R61 0230'
    tokens = str(s).strip().split()
    if tokens:
        last = tokens[-1]
        if _re.fullmatch(r"\d{4}", last):
            return last
    return ""


def _dept_codes_match(mode_dept: str, oa_row: "pd.Series") -> bool:  # type: ignore[type-arg]
    """Return True when Mode and Open Active departments represent the same function.

    Checks three conditions in order (most strict → most lenient):
      1. Exact string match against OA Department Name.
      2. Exact string match against OA Department Code (normalized).
      3. Numeric dept segment (e.g. '0230') extracted from both sides matches.
         This catches display-name divergence like:
           Mode:  '73R61_0230_00:Banquets'
           OA:    '73R61_0230_00:Half Moon Bay,Banquets'
         where the 4-digit code (0230) is identical.
    """
    oa_dept_name = str(oa_row.get("Department Name", "")).strip()
    oa_dept_code = _normalize_join_value(oa_row.get("Department Code", ""))

    if mode_dept == oa_dept_name:
        return True
    if mode_dept == oa_dept_code:
        return True

    mode_num = _extract_dept_numeric(mode_dept)
    oa_num_from_name = _extract_dept_numeric(oa_dept_name)
    oa_num_from_code = _extract_dept_numeric(oa_dept_code)
    if mode_num and mode_num in (oa_num_from_name, oa_num_from_code):
        return True

    return False


def compute_local_decisions(
    mode_df: pd.DataFrame,
    open_active_df: pd.DataFrame,
    open_closed_df: pd.DataFrame,
) -> pd.DataFrame:
    """Compute AID/CAN/job decisions locally for deterministic output tabs."""
    print("Assignment decision engine: starting local decision computation.")
    print(f"  Input Mode rows: {len(mode_df)}")
    print(f"  Input Open Active rows: {len(open_active_df)}")
    print(f"  Input Open & Closed rows: {len(open_closed_df)}")
    print("  Rule order:")
    print("    1. Build Perfect Match = Department_Code + worker_id + partner_rate.")
    print("    2. Perfect AID match requires Open Active Con1 equal to Perfect Match and shift date inside Assignment Start/End Date.")
    print("    3. Build 2nd Best Match = fieldglass_site_name + worker_id + partner_rate.")
    print("    4. 2nd Best AID match requires Open Active Con 2 equal to 2nd Best Match and shift date inside Assignment Start/End Date.")
    print("    5. validation_for_Department compares Mode department with the matched 2nd Best AID department.")
    print("    6. CAN ID lookup uses Open & Closed Vendor Tracking ID 1 -> Candidate ID.")
    print("    7. Existing Jobs lookup uses 2nd Best AID -> Open & Closed Job ID.")
    df = mode_df.copy()
    print("  Applying department code overrides and review flags.")
    df = apply_dept_overrides(df)

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
    for worker, candidate in zip(oc_vti1, oc_candidate_id):
        if not worker or worker == "nan":
            continue
        if candidate and candidate != "nan" and worker not in can_by_worker:
            can_by_worker[worker] = candidate

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
                if _dept_codes_match(dept_code, oa.iloc[hit]):
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

    print("Assignment decision engine: local decision columns computed.")
    print(f"  Perfect AID rows: {(df['Perfect AID'].astype(str) != '0').sum()}")
    print(
        "  2nd Best AID rows: "
        f"{((df['Perfect AID'].astype(str) == '0') & (df['2nd Best AID'].astype(str) != '0')).sum()}"
    )
    print(f"  Missing AID rows: {((df['Perfect AID'].astype(str) == '0') & (df['2nd Best AID'].astype(str) == '0')).sum()}")
    print(f"  CAN ID present rows: {((df['CAN ID'].astype(str) != '0') & (df['CAN ID'].astype(str) != '')).sum()}")
    print(f"  CAN ID missing rows: {(df['CAN ID'].astype(str).isin(['0', '', 'nan'])).sum()}")
    return df


# ── Discrepancy classification ───────────────────────────────────────────────

def _prep_oc_lookup(open_closed_df: pd.DataFrame) -> pd.DataFrame:
    """Attach normalised lookup columns to Open & Closed for discrepancy checks."""
    oc = open_closed_df.copy()
    oc["_start_dt"] = pd.to_datetime(oc["Assignment Start Date"], errors="coerce")
    oc["_end_dt"] = pd.to_datetime(oc["Assignment End Date"], errors="coerce")
    oc["_worker"] = oc["Vendor Tracking ID 1"].map(google_concat_value)
    oc["_rate"] = oc["Client ST Bill Rate"].map(google_concat_value)
    oc["_dept"] = oc["Department Name"].fillna("").astype(str).str.strip()
    oc["_site"] = oc["Work Location"].fillna("").astype(str).str.split(" - ", n=1).str[0].map(google_concat_value)
    oc["_status"] = oc["Assignment Status"].fillna("").astype(str).str.strip()
    return oc


def _classify_missing_row(row: pd.Series, oc: pd.DataFrame) -> dict:
    """Return a discrepancy dict for one missing-AID Mode row."""
    worker = google_concat_value(row.get("worker_id"))
    site = google_concat_value(row.get("fieldglass_site_name"))
    dept = google_concat_value(row.get("Department_Code") or row.get("Department Code", ""))
    shift_dt = pd.to_datetime(row.get("date_of_shift_start"), errors="coerce")
    empty = {"discrepancy_type": "no_simplify_assignment", "review_aid": "", "review_actual_rate": "", "review_actual_site": "", "review_actual_dept": ""}
    if pd.isna(shift_dt) or not worker:
        return empty
    worker_rows = oc[oc["_worker"] == worker]
    covering = worker_rows[(worker_rows["_start_dt"] <= shift_dt) & (worker_rows["_end_dt"] >= shift_dt)]
    open_cov = covering[covering["_status"] == "Open"]
    if covering.empty:
        return empty
    if open_cov.empty:
        r = covering.iloc[0]
        return {"discrepancy_type": "assignment_closed_cancelled", "review_aid": str(r.get("Assignment ID", "")), "review_actual_rate": r["_rate"], "review_actual_site": r["_site"], "review_actual_dept": r["_dept"]}
    same_site = open_cov[open_cov["_site"] == site]
    same_site_dept = same_site[same_site["_dept"] == dept]
    if not same_site_dept.empty:
        r = same_site_dept.iloc[0]
        return {"discrepancy_type": "same_site_dept_diff_rate", "review_aid": str(r.get("Assignment ID", "")), "review_actual_rate": r["_rate"], "review_actual_site": r["_site"], "review_actual_dept": r["_dept"]}
    if not same_site.empty:
        r = same_site.iloc[0]
        return {"discrepancy_type": "same_site_diff_dept_or_rate", "review_aid": str(r.get("Assignment ID", "")), "review_actual_rate": r["_rate"], "review_actual_site": r["_site"], "review_actual_dept": r["_dept"]}
    r = open_cov.iloc[0]
    return {"discrepancy_type": "worker_open_at_other_site", "review_aid": str(r.get("Assignment ID", "")), "review_actual_rate": r["_rate"], "review_actual_site": r["_site"], "review_actual_dept": r["_dept"]}


def _add_discrepancy_cols(decisions_df: pd.DataFrame, open_closed_df: pd.DataFrame) -> pd.DataFrame:
    """Classify every missing-AID row and attach discrepancy columns to decisions."""
    oc = _prep_oc_lookup(open_closed_df)
    df = decisions_df.copy()
    missing_mask = (df["Perfect AID"].astype(str) == "0") & (df["2nd Best AID"].astype(str) == "0")
    for col in ("discrepancy_type", "review_aid", "review_actual_rate", "review_actual_site", "review_actual_dept"):
        df[col] = ""
    for idx in df.index[missing_mask]:
        result = _classify_missing_row(df.loc[idx], oc)
        for col, val in result.items():
            df.at[idx, col] = val
    return df


def _reason_and_review(row: pd.Series) -> tuple[str, str]:
    """Return (human-readable reason, 'Yes'/'No' should-be-reviewed) for a missing-AID row."""
    # Ops-flagged ambiguous dept — always review, no code change applied
    dept_review_flag = str(row.get("dept_review_flag", ""))
    if dept_review_flag:
        return (dept_review_flag, "Yes")

    dept_override_note = str(row.get("dept_override_note", ""))
    dept_needs_confirm = bool(row.get("dept_needs_confirm", False))

    disc = str(row.get("discrepancy_type", ""))
    aid = str(row.get("review_aid", ""))
    actual_rate = str(row.get("review_actual_rate", ""))
    actual_site = str(row.get("review_actual_site", ""))
    actual_dept = str(row.get("review_actual_dept", ""))
    mode_rate = str(row.get("partner_rate", ""))
    mode_dept = str(row.get("Department_Code", "") or "")
    mode_site = str(row.get("fieldglass_site_name", ""))

    if disc == "same_site_dept_diff_rate":
        reason = (
            f"Rate mismatch — open assignment {aid} exists at same site/dept. "
            f"Simplify rate: {actual_rate} vs Mode rate: {mode_rate}. "
            "Review/amend rate before creating a new job."
        )
        review = "Yes"
    elif disc == "same_site_diff_dept_or_rate":
        reason = (
            f"Open assignment {aid} at same site but different dept/rate. "
            f"Simplify dept: {actual_dept} | Mode dept: {mode_dept}. "
            "Review before creating a new job."
        )
        review = "Yes"
    elif disc == "worker_open_at_other_site":
        reason = (
            f"Worker has open assignment at a different Marriott site {actual_site} "
            f"(AID: {aid}, rate: {actual_rate}). "
            "Confirm this is a separate new-site assignment."
        )
        review = "Yes"
    elif disc == "assignment_closed_cancelled":
        reason = (
            f"Prior assignment {aid} is Closed/Cancelled "
            f"(site: {actual_site}, rate: {actual_rate}). "
            "Check if rate/dept changed or assignment needs reopening."
        )
        review = "Yes"
    elif not mode_dept.strip():
        reason = (
            f"Blank department code — site {mode_site} has no dept in Mode export. "
            "Add department mapping before posting job."
        )
        review = "Yes"
    else:
        reason = "No matching assignment found in Simplify. Genuine new job or candidate required."
        review = "No"

    # Provisional dept override — always escalate to review and append the note
    if dept_needs_confirm:
        suffix = f" | Provisional dept code: {dept_override_note}" if dept_override_note else ""
        return (reason + suffix, "Yes")

    return (reason, review)


# ── Amend-review tab ─────────────────────────────────────────────────────────

def build_amend_tab(decisions_df: pd.DataFrame, open_closed_df: pd.DataFrame) -> pd.DataFrame:
    """Informational tab: rows with 2nd Best AID but dept display-name mismatch.

    NOTE: These rows are ALSO included in the Job Request tab (ops standard —
    verified across WK18–WK21: ops never uses a separate Amend Review tab).
    This tab is kept as a diagnostic reference only so the existing-assignment
    context is visible during review.
    """
    print("Assignment output build step: evaluating Amend Review rows (diagnostic only — rows also in Job Request).")
    print("  Rule: Perfect AID = 0 AND 2nd Best AID != 0 AND validation_for_Department = Not OK.")
    mask = (
        (decisions_df["Perfect AID"].astype(str) == "0")
        & (decisions_df["2nd Best AID"].astype(str) != "0")
        & (decisions_df["validation_for_Department"].astype(str) == "Not OK")
    )
    amend = decisions_df.loc[mask].copy()
    print(f"  Amend Review rows: {len(amend)}")
    cols = [
        "Property ID", "Property Name", "Shift", "Pro Name", "CAN ID",
        "Mode Department", "Simplify Department", "2nd Best AID", "Existing Job ID",
        "partner_rate", "Reason", "Should be reviewed",
    ]
    if amend.empty:
        return pd.DataFrame(columns=cols)
    oc_dept_by_aid = open_closed_df.set_index("Assignment ID")["Department Name"].to_dict()
    shift_dates = pd.to_datetime(amend["date_of_shift_start"], errors="coerce")
    dept_series = amend["Department_Code"] if "Department_Code" in amend.columns else pd.Series([""] * len(amend), index=amend.index)
    simplify_dept = amend["2nd Best AID"].map(lambda a: str(oc_dept_by_aid.get(str(a), "")))
    mode_dept = pd.Series(dept_series).astype(str)
    reason = (
        "[DIAGNOSTIC — row is also in Job Request tab] "
        "Existing assignment found at same site/worker/rate but dept display name differs. "
        "Mode dept: " + mode_dept.values + " | Simplify dept: " + simplify_dept.values
        + ". Dept numeric code matches — this is a display-name divergence, not a true dept mismatch. "
        "Post a new job request for the current period (ops standard)."
    )
    return pd.DataFrame(
        {
            "Property ID": amend["fieldglass_site_name"].astype(str).values,
            "Property Name": amend["location"].astype(str).values,
            "Shift": shift_dates.dt.strftime("%m/%d/%Y").fillna("").values,
            "Pro Name": (amend["first_name"].astype(str) + " " + amend["last_name"].astype(str)).str.strip().values,
            "CAN ID": amend["CAN ID"].astype(str).values,
            "Mode Department": mode_dept.values,
            "Simplify Department": simplify_dept.values,
            "partner_rate": amend["partner_rate"].astype(str).values,
            "2nd Best AID": amend["2nd Best AID"].astype(str).values,
            "Existing Job ID": amend["Existing Jobs"].astype(str).values,
            "Reason": reason,
            "Should be reviewed": "Yes",
        }
    ).reset_index(drop=True)


# ── Red-highlighting helper ──────────────────────────────────────────────────

def _highlight_review_rows(
    service: Any,
    spreadsheet_id: str,
    sheet_id: int,
    df: pd.DataFrame,
    review_col: str = "Should be reviewed",
) -> None:
    """Paint light-red background on every data row where review_col == 'Yes'."""
    if review_col not in df.columns:
        return
    light_red = {"red": 1.0, "green": 0.8, "blue": 0.8}
    requests = []
    for row_idx, val in enumerate(df[review_col]):
        if str(val).strip() == "Yes":
            sheet_row = row_idx + 1  # 0-indexed; row 0 is the header
            requests.append(
                {
                    "repeatCell": {
                        "range": {
                            "sheetId": sheet_id,
                            "startRowIndex": sheet_row,
                            "endRowIndex": sheet_row + 1,
                        },
                        "cell": {"userEnteredFormat": {"backgroundColor": light_red}},
                        "fields": "userEnteredFormat.backgroundColor",
                    }
                }
            )
    if not requests:
        return
    service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id, body={"requests": requests}
    ).execute()
    print(f"  Highlighted {len(requests)} review rows in sheet id={sheet_id}.")


def highlight_review_tabs(args: Any, output_tabs: dict[str, pd.DataFrame]) -> None:
    """Apply red highlighting to all 'Should be reviewed = Yes' rows across output tabs."""
    review_tabs = [t for t in ("job request", "can upload", "amend review", "provisional match") if t in output_tabs]
    if not review_tabs:
        return
    credentials = Credentials.from_service_account_file(
        str(args.google_credentials),
        scopes=["https://www.googleapis.com/auth/spreadsheets"],
    )
    service = build("sheets", "v4", credentials=credentials, cache_discovery=False)
    spreadsheet = (
        service.spreadsheets()
        .get(spreadsheetId=args.target_spreadsheet_id, fields="sheets(properties(sheetId,title))")
        .execute()
    )
    sheet_id_by_title = {s["properties"]["title"]: s["properties"]["sheetId"] for s in spreadsheet.get("sheets", [])}
    print("Applying review-row highlighting:")
    for tab_name in review_tabs:
        df = output_tabs[tab_name]
        if tab_name not in sheet_id_by_title:
            print(f"  Tab '{tab_name}' not found in spreadsheet — skipping highlight.")
            continue
        _highlight_review_rows(service, args.target_spreadsheet_id, sheet_id_by_title[tab_name], df)


# ── Provisional-match tab ────────────────────────────────────────────────────

def build_provisional_match_tab(decisions_df: pd.DataFrame) -> pd.DataFrame:
    """Perfect AID rows matched only because of a provisional department-code override.

    These rows found an AID and will be auto-processed, but ops should verify the
    dept code with the property contact before the first assignment is submitted.
    """
    print("Assignment output build step: evaluating Provisional Match rows.")
    print("  Rule: Perfect AID != 0 AND dept_needs_confirm = True.")
    has_col = "dept_needs_confirm" in decisions_df.columns
    if not has_col:
        return pd.DataFrame(columns=["Property ID", "Property Name", "Shift", "Pro Name", "CAN ID", "Perfect AID", "Dept Code Used", "Confirmation Note", "Should be reviewed"])
    confirm_mask = decisions_df["dept_needs_confirm"].astype(bool)
    perfect_mask = decisions_df["Perfect AID"].astype(str) != "0"
    prov = decisions_df.loc[confirm_mask & perfect_mask].copy()
    print(f"  Provisional Match rows: {len(prov)}")
    cols = ["Property ID", "Property Name", "Shift", "Pro Name", "CAN ID", "Perfect AID", "Dept Code Used", "Confirmation Note", "Should be reviewed"]
    if prov.empty:
        return pd.DataFrame(columns=cols)
    shift_dates = pd.to_datetime(prov["date_of_shift_start"], errors="coerce")
    dept_col = "Department_Code" if "Department_Code" in prov.columns else "Department Code"
    return pd.DataFrame(
        {
            "Property ID": prov["fieldglass_site_name"].astype(str).values,
            "Property Name": prov["location"].astype(str).values,
            "Shift": shift_dates.dt.strftime("%m/%d/%Y").fillna("").values,
            "Pro Name": (prov["first_name"].astype(str) + " " + prov["last_name"].astype(str)).str.strip().values,
            "CAN ID": prov["CAN ID"].astype(str).values,
            "Perfect AID": prov["Perfect AID"].astype(str).values,
            "Dept Code Used": prov[dept_col].astype(str).values,
            "Confirmation Note": prov["dept_override_note"].astype(str).values,
            "Should be reviewed": "Yes",
        }
    ).reset_index(drop=True)


# ── Upload tab (Simplify direct-assignment rows) ─────────────────────────────

def build_upload(decisions_df: pd.DataFrame) -> pd.DataFrame:
    """Rows ready for Simplify direct assignment once Available Jobs is chosen."""
    print("Assignment output build step: upload tab template created.")
    print("  Rule: rows require Perfect AID = 0, 2nd Best AID = 0, CAN ID present, and manually selected Available Jobs.")
    print("  Current implementation keeps upload empty until Available Jobs is chosen in Mode.")
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
        ]
    )


def build_job_request(
    decisions_df: pd.DataFrame,
    open_active_df: pd.DataFrame,
) -> pd.DataFrame:
    """Mode rows that need a new Simplify job posting (Case C3 + Case B-amend).

    Includes two cases:
      1. No existing assignment (Perfect AID = 0 AND 2nd Best AID = 0) AND CAN ID exists.
         → Worker is in Simplify but has no open job posting for this shift. Case C3.
      2. 2nd Best AID exists but dept code is genuinely different (validation = 'Not OK').
         → Worker has assignment for wrong dept; new job posting needed. Case B-amend.

    EXCLUDED (corrected WK22 analysis):
      - 2nd Best AID + validation = 'OK' → SKIP. Con1 failed only due to display-name
        suffix difference; the existing AID already covers the worker correctly.

    One row per shift — ops tracks each shift date individually in the JR pipeline.
    """
    print("Assignment output build step: evaluating Job Request rows.")
    print("  Rule: [No AID + CAN ID] OR [2nd Best AID + dept NOT OK]. One row per shift.")
    no_aid_mask = (
        (decisions_df["Perfect AID"].astype(str) == "0")
        & (decisions_df["2nd Best AID"].astype(str) == "0")
        & (decisions_df["CAN ID"].astype(str) != "0")
        & (decisions_df["CAN ID"].astype(str) != "")
    )
    # Only include 2nd Best AID rows where dept code is genuinely different (Not OK).
    # When validation = "OK" it means Con1 failed only due to display-name suffix
    # divergence (e.g. "Banquets" vs "Half Moon Bay,Banquets") but the 4-digit dept
    # code is identical — the existing AID already covers this worker correctly → SKIP.
    # Confirmed WK22: 73R61 workers with "OK" validation have zero ops JR rows because
    # their WK21-approved AIDs (end date 2027-01-31) still cover them.
    second_aid_mask = (
        (decisions_df["Perfect AID"].astype(str) == "0")
        & (~decisions_df["2nd Best AID"].astype(str).isin(["0", "", "nan"]))
        & (decisions_df["CAN ID"].astype(str) != "0")
        & (decisions_df["CAN ID"].astype(str) != "")
        & (decisions_df["validation_for_Department"].astype(str) == "Not OK")
    )
    mask = no_aid_mask | second_aid_mask
    selected = decisions_df.loc[mask].copy()
    selected["_has_second_aid"] = second_aid_mask[mask].values
    print(f"  Job Request candidate rows: {len(selected)} ({no_aid_mask.sum()} no-AID + {second_aid_mask.sum()} existing-AID)")
    columns = [
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
        "Existing AID",
        "Should be reviewed",
    ]
    if selected.empty:
        return pd.DataFrame(columns=columns)

    shift_dates = pd.to_datetime(selected["date_of_shift_start"], errors="coerce")
    department_series = selected["Department_Code"] if "Department_Code" in selected.columns else selected.get("Department Code", "")

    reason_review = selected.apply(_reason_and_review, axis=1, result_type="expand")
    reason_review.columns = ["_reason", "_review"]

    # For rows that have a 2nd Best AID (existing assignment with dept mismatch),
    # override the reason and status to make the existing-assignment context visible.
    has_second = selected["_has_second_aid"].astype(bool)
    second_aid_col = selected["2nd Best AID"].astype(str)
    simplify_dept_col: list[str] = []

    if has_second.any():
        try:
            oa_dept_by_aid = open_active_df.set_index("Assignment ID")["Department Name"].to_dict()
        except Exception:
            oa_dept_by_aid = {}
        for idx, row in selected.iterrows():
            if has_second.loc[idx]:
                aid = str(row.get("2nd Best AID", ""))
                oa_dept = oa_dept_by_aid.get(aid, "")
                mode_dept = str(row.get("Department_Code", "") or row.get("Department Code", ""))
                validation = str(row.get("validation_for_Department", ""))
                simplify_dept_col.append(oa_dept)
                if validation == "OK":
                    reason_review.at[idx, "_reason"] = (
                        f"Existing Simplify assignment {aid} found and dept code matches "
                        f"(Mode: '{mode_dept}' | Simplify: '{oa_dept}'). "
                        "Create new job posting for this period — ops policy for this site "
                        "requires fresh job requests each week rather than extending existing assignments."
                    )
                else:
                    reason_review.at[idx, "_reason"] = (
                        f"Existing Simplify assignment {aid} found at same site/worker/rate "
                        f"but dept name differs — Mode: '{mode_dept}' | Simplify: '{oa_dept}'. "
                        "Create new job posting for this period."
                    )
                reason_review.at[idx, "_review"] = "Yes"
            else:
                simplify_dept_col.append("")
    else:
        simplify_dept_col = [""] * len(selected)

    # Status column: clearly marks rows needing ops action before they can be uploaded.
    # "Existing AID → New JR" rows need a new job posting created first;
    # once that job is approved and a Job ID (MAR-JB-xxxxx) exists, the row
    # moves to the Upload tab. Plain no-AID rows are straightforward new jobs.
    status_values = [
        (
            f"EXISTING AID: {str(row['2nd Best AID'])} — "
            + ("Dept code matches, post new job" if str(row.get("validation_for_Department","")) == "OK"
               else "Dept name differs, post new job")
        ) if has_second.loc[idx] else ""
        for idx, row in selected.iterrows()
    ]

    existing_aid_values = [
        str(row["2nd Best AID"]) if has_second.loc[idx] else str(row.get("review_aid", ""))
        for idx, row in selected.iterrows()
    ]

    # Build per-row pre-dedup frame
    pre_dedup = pd.DataFrame(
        {
            "Property ID": selected["fieldglass_site_name"].astype(str).values,
            "Property Name": selected["location"].astype(str).values,
            "location": selected["location"].astype(str).values,
            "_can_id": selected["CAN ID"].astype(str).values,
            "_worker_id": selected["worker_id"].astype(str).values,
            "Shift": shift_dates.dt.strftime("%m/%d/%Y").fillna("").values,
            "_shift_dt": shift_dates.values,
            "partner_rate": selected["partner_rate"].astype(str).values,
            "pro_rate": selected["pro_rate"].astype(str).values,
            "Pro Name": (selected["first_name"].astype(str) + " " + selected["last_name"].astype(str)).str.strip().values,
            "Position": selected["position"].astype(str).values,
            "shift name": selected["shift_name"].astype(str).values,
            "Department": pd.Series(department_series).astype(str).values,
            "Mark up": selected["mark_up"].astype(str).values,
            "State": selected["state_code"].astype(str).values,
            "_reason": reason_review["_reason"].values,
            "_review": reason_review["_review"].values,
            "Status": status_values,
            "Existing AID": existing_aid_values,
        }
    ).reset_index(drop=True)

    # One JR row per shift (no dedup). Ops creates one row per shift date per
    # worker so each individual shift can be tracked through the approval pipeline.
    # Confirmed WK22: MAR-CD-235799 appears 6× in ops 337V2 JR — one per shift date.
    pre_dedup["_shift_dt_safe"] = pd.to_datetime(pre_dedup["_shift_dt"], errors="coerce")
    pre_dedup = pre_dedup.sort_values(["Property ID", "_shift_dt_safe"]).reset_index(drop=True)
    print(f"  Job Request rows (one per shift): {len(pre_dedup)}")

    all_shifts_notes = [""] * len(pre_dedup)

    return pd.DataFrame(
        {
            "Property ID": pre_dedup["Property ID"].values,
            "Property Name": pre_dedup["Property Name"].values,
            "location": pre_dedup["location"].values,
            "CAN id": pre_dedup["_can_id"].values,
            "Shift": pre_dedup["Shift"].values,
            "partner_rate": pre_dedup["partner_rate"].values,
            "pro_rate": pre_dedup["pro_rate"].values,
            "Pro Name": pre_dedup["Pro Name"].values,
            "Position": pre_dedup["Position"].values,
            "shift name": pre_dedup["shift name"].values,
            "Department": pre_dedup["Department"].values,
            "Mark up": pre_dedup["Mark up"].values,
            "State": pre_dedup["State"].values,
            "Reason for new Job(New Rate, Closed Assignment, Job Expired, open assignments but at different rates)": pre_dedup["_reason"].values,
            "Notes:": all_shifts_notes,
            "Becky Notes": "",
            "Comments": "",
            "Status": pre_dedup["Status"].values,
            "Existing AID": pre_dedup["Existing AID"].values,
            "Should be reviewed": pre_dedup["_review"].values,
        }
    ).reset_index(drop=True)


def build_can_upload(decisions_df: pd.DataFrame) -> pd.DataFrame:
    """Mode rows representing pros without a Marriott Candidate ID.

    Workers with ambiguous situations (cross-property open assignment, blank
    department) get Should be reviewed = Yes so ops can investigate before
    creating a candidate record in Simplify.
    """
    print("Assignment output build step: evaluating CAN Upload rows.")
    print("  Rule: Perfect AID = 0 AND 2nd Best AID = 0 AND CAN ID missing/0/nan.")
    mask = (
        (decisions_df["Perfect AID"].astype(str) == "0")
        & (decisions_df["2nd Best AID"].astype(str) == "0")
        & (decisions_df["CAN ID"].astype(str).isin(["0", "", "nan"]))
    )
    selected = decisions_df.loc[mask].copy()
    print(f"  CAN Upload candidate shift rows before worker de-dupe: {len(selected)}")
    columns = [
        "First Name",
        "Middle Name",
        "Last Name",
        "Date Of Birth(MM/DD)",
        "State/National ID (Last 3 Digits)",
        "Email Address",
        "Site",
        "Dept",
        "Shift Dates",
        "Reason",
        "Should be reviewed",
    ]
    if selected.empty:
        return pd.DataFrame(columns=columns)

    # Compute reason/review per shift row before deduplication
    reason_review = selected.apply(_reason_and_review, axis=1, result_type="expand")
    reason_review.columns = ["_reason", "_review"]
    selected = selected.copy()
    selected["_reason"] = reason_review["_reason"].values
    selected["_review"] = reason_review["_review"].values

    # Aggregate per-worker context before dedup
    dept_col = "Department_Code" if "Department_Code" in selected.columns else "Department Code"
    worker_context = (
        selected.groupby("worker_id")
        .agg(
            _shift_dates=("date_of_shift_start", lambda s: ", ".join(sorted(set(s.astype(str))))),
            _site=("fieldglass_site_name", "first"),
            _dept=(dept_col, "first"),
            _reason=("_reason", "first"),
            _review=("_review", lambda s: "Yes" if "Yes" in s.values else "No"),
        )
        .reset_index()
    )
    selected = selected.drop_duplicates(subset=["worker_id"], keep="first").reset_index(drop=True)
    print(f"  CAN Upload rows after worker_id de-dupe: {len(selected)}")

    ctx = worker_context.set_index("worker_id")
    worker_ids = selected["worker_id"].astype(str)

    dob_mmdd = (
        selected["dob_mmdd"]
        .astype(str)
        .replace({"nan": "", "None": "", "NaN": ""})
        .str.replace(r"\.0$", "", regex=True)
    )
    dob_mmdd = dob_mmdd.where(dob_mmdd.str.match(r"\d{1,2}/\d{1,2}", na=False), "")

    return pd.DataFrame(
        {
            "First Name": selected["first_name"].astype(str).values,
            "Middle Name": "",
            "Last Name": selected["last_name"].astype(str).values,
            "Date Of Birth(MM/DD)": dob_mmdd.values,
            "State/National ID (Last 3 Digits)": "",  # OPS MUST FILL: last 3 digits of national/state ID
            # Email format note: Simplify requires the Instawork platform alias
            # ({FirstName}{last-3-SSN}@instawork.com), e.g. 'Leidy853@instawork.com'.
            # We populate the personal email as reference; ops must replace with the
            # @instawork.com alias before importing to Simplify.
            "Email Address": selected["email"].astype(str).values,
            "Site": [ctx.loc[w, "_site"] if w in ctx.index else "" for w in worker_ids],
            "Dept": [ctx.loc[w, "_dept"] if w in ctx.index else "" for w in worker_ids],
            "Shift Dates": [ctx.loc[w, "_shift_dates"] if w in ctx.index else "" for w in worker_ids],
            "Reason": [
                (ctx.loc[w, "_reason"] if w in ctx.index else "")
                + " | ACTION REQUIRED: Replace Email Address with {FirstName}{SSN-last-3}@instawork.com format before importing to Simplify."
                for w in worker_ids
            ],
            "Should be reviewed": [ctx.loc[w, "_review"] if w in ctx.index else "No" for w in worker_ids],
        }
    ).reset_index(drop=True)


def build_can_output(can_upload_df: pd.DataFrame) -> pd.DataFrame:
    """CAN Upload columns plus returned Candidate ID and Remarks."""
    print("Assignment output build step: creating CAN Output template.")
    print("  Rule: preserve CAN Upload row order and add Candidate ID + Remarks columns for Simplify import output.")
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
    """Empty `Output` tab template for post-import tracking."""
    print("Assignment output build step: creating Output template for post-import tracking.")
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
    """Empty `Sheet8` source tab for candidate creation."""
    print("Assignment output build step: creating Sheet8 template for sensitive candidate creation source data.")
    return pd.DataFrame(columns=["id", "name", "email", "ssn", "bank_account_type", "date_of_birth"])


def build_summary_template() -> pd.DataFrame:
    """Empty `Summary` tab for operator scratch / QA notes."""
    print("Assignment output build step: creating Summary scratch tab.")
    return pd.DataFrame(columns=["Summary"])


def build_assignment_tabs(tabs: dict[str, pd.DataFrame]) -> tuple[dict[str, pd.DataFrame], pd.DataFrame]:
    """Build assignment output tabs from prepared raw workflow tabs."""
    print("Assignment tab build sequence started.")
    print("  Step 1: compute local decisions from Mode, Open Active, and Open & Closed.")
    decisions = compute_local_decisions(
        tabs["Mode"],
        tabs["Open Active"],
        tabs["Open & Closed"],
    )
    print("  Step 1b: classify discrepancy type for every missing-AID row.")
    decisions = _add_discrepancy_cols(decisions, tabs["Open & Closed"])
    print("  Step 2: build CAN Upload from missing-CAN decision rows.")
    can_upload_df = build_can_upload(decisions)
    print("  Step 3: build output tabs in this order: upload, job request, can upload, can output, amend review, provisional match, Output, Sheet8, Summary.")
    output_tabs = {
        "upload": build_upload(decisions),
        "job request": build_job_request(decisions, tabs["Open Active"]),
        "can upload": can_upload_df,
        "can output": build_can_output(can_upload_df),
        "amend review": build_amend_tab(decisions, tabs["Open & Closed"]),
        "provisional match": build_provisional_match_tab(decisions),
        "Output": build_output_template(),
        "Sheet8": build_sheet8_template(),
        "Summary": build_summary_template(),
    }
    print("Assignment tab build sequence completed.")
    for title, df in output_tabs.items():
        print(f"  Output tab '{title}': {len(df)} rows x {len(df.columns)} columns")
    return output_tabs, decisions


def print_assignment_distribution(decisions: pd.DataFrame, output_tabs: dict[str, pd.DataFrame]) -> None:
    """Print the assignment workload breakdown for the current run."""
    zeros = (decisions["Perfect AID"].astype(str) == "0") & (decisions["2nd Best AID"].astype(str) == "0")
    can_upload_df = output_tabs["can upload"]
    job_request_df = output_tabs["job request"]
    print("Assignment-logic distribution:")
    print(f"  Mode rows total:                 {len(decisions)}")
    print(f"  Perfect AID matched (do nothing): {(decisions['Perfect AID'].astype(str) != '0').sum()}")
    print(
        "  2nd Best AID matched (amend):     "
        f"{((decisions['Perfect AID'].astype(str) == '0') & (decisions['2nd Best AID'].astype(str) != '0')).sum()}"
    )
    print(f"  Need new CAN ID (can upload):     {len(can_upload_df)}")
    print(f"  Need new job posting (job req):   {len(job_request_df)}")
    print(f"  Ready for Upload (needs AC):      {zeros.sum() - len(can_upload_df) - len(job_request_df)}")


def run_assignment_logic(
    args: Any,
    tabs: dict[str, pd.DataFrame],
    upload_tabs: Callable[[Any, dict[str, pd.DataFrame]], None],
    *,
    apply_formulas: bool = True,
) -> dict[str, pd.DataFrame]:
    """Run assignment formulas, build output tabs, upload them, and return them."""
    print("Assignment workflow started.")
    if apply_formulas:
        apply_mode_decision_formulas(args, mode_row_count=len(tabs["Mode"]))
    else:
        print("Assignment formula step skipped by caller: apply_formulas=False.")
    output_tabs, decisions = build_assignment_tabs(tabs)
    print_assignment_distribution(decisions, output_tabs)
    print("Assignment workflow upload step: writing assignment output tabs to target Google Sheet.")
    upload_tabs(args, output_tabs)
    print("Assignment workflow review-highlight step: painting 'Should be reviewed = Yes' rows red.")
    highlight_review_tabs(args, output_tabs)
    print("Assignment workflow completed.")
    return output_tabs


def main() -> None:
    """Run the full Marriott workflow with assignment outputs enabled."""
    from marriott_workflow import (
        build_snapshot_name,
        build_tabs,
        download_mode_export,
        download_simplify_reports,
        find_existing_simplify_downloads,
        find_mode_csv,
        parse_args,
        snapshot_to_shared_drive,
        upload_tabs,
    )

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
        output_tabs, decisions = build_assignment_tabs(tabs)
        print_assignment_distribution(decisions, output_tabs)
        return

    upload_tabs(args, tabs)
    run_assignment_logic(args, tabs, upload_tabs)

    if not args.no_snapshot:
        snapshot_name = args.snapshot_name or build_snapshot_name(args.start_date, args.end_date)
        snapshot_to_shared_drive(args, snapshot_name)


if __name__ == "__main__":
    main()
