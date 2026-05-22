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
        return
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


def compute_local_decisions(
    mode_df: pd.DataFrame,
    open_active_df: pd.DataFrame,
    open_closed_df: pd.DataFrame,
) -> pd.DataFrame:
    """Compute AID/CAN/job decisions locally for deterministic output tabs."""
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
    """Rows ready for Simplify direct assignment once Available Jobs is chosen."""
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
    """Mode rows that need a brand new Simplify job posting."""
    mask = (
        (decisions_df["Perfect AID"].astype(str) == "0")
        & (decisions_df["2nd Best AID"].astype(str) == "0")
        & (decisions_df["CAN ID"].astype(str) != "0")
        & (decisions_df["CAN ID"].astype(str) != "")
    )
    selected = decisions_df.loc[mask].copy()
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
    ]
    if selected.empty:
        return pd.DataFrame(columns=columns)

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
    """Mode rows representing pros without a Marriott Candidate ID."""
    mask = (
        (decisions_df["Perfect AID"].astype(str) == "0")
        & (decisions_df["2nd Best AID"].astype(str) == "0")
        & (decisions_df["CAN ID"].astype(str).isin(["0", "", "nan"]))
    )
    selected = decisions_df.loc[mask].copy()
    columns = [
        "First Name",
        "Middle Name",
        "Last Name",
        "Date Of Birth(MM/DD)",
        "State/National ID (Last 3 Digits)",
        "Email Address",
    ]
    if selected.empty:
        return pd.DataFrame(columns=columns)

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
    """CAN Upload columns plus returned Candidate ID and Remarks."""
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
    return pd.DataFrame(columns=["id", "name", "email", "ssn", "bank_account_type", "date_of_birth"])


def build_summary_template() -> pd.DataFrame:
    """Empty `Summary` tab for operator scratch / QA notes."""
    return pd.DataFrame(columns=["Summary"])


def build_assignment_tabs(tabs: dict[str, pd.DataFrame]) -> tuple[dict[str, pd.DataFrame], pd.DataFrame]:
    """Build assignment output tabs from prepared raw workflow tabs."""
    decisions = compute_local_decisions(
        tabs["Mode"],
        tabs["Open Active"],
        tabs["Open & Closed"],
    )
    can_upload_df = build_can_upload(decisions)
    output_tabs = {
        "upload": build_upload(decisions),
        "job request": build_job_request(decisions, tabs["Open Active"]),
        "can upload": can_upload_df,
        "can output": build_can_output(can_upload_df),
        "Output": build_output_template(),
        "Sheet8": build_sheet8_template(),
        "Summary": build_summary_template(),
    }
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
    if apply_formulas:
        apply_mode_decision_formulas(args, mode_row_count=len(tabs["Mode"]))
    output_tabs, decisions = build_assignment_tabs(tabs)
    print_assignment_distribution(decisions, output_tabs)
    upload_tabs(args, output_tabs)
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
