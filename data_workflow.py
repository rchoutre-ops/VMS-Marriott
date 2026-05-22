#!/usr/bin/env python3
"""Marriott raw-data workflow entrypoint.

Run this file for the end-to-end raw data refresh: Mode/Simplify source files
are downloaded or reused, the raw tabs are prepared, and those source tabs are
uploaded to the Google Sheet. Assignment outputs are optional and delegated to
`assignments.py`.
"""

from __future__ import annotations

from assignments import run_assignment_logic
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
    print("Prepared raw workflow tabs:")
    for title, df in tabs.items():
        print(f"- {title}: {len(df)} data rows x {len(df.columns)} columns")

    if args.no_upload:
        return

    upload_tabs(args, tabs)

    if not args.no_assignment_logic:
        run_assignment_logic(args, tabs, upload_tabs)

    if args.no_snapshot:
        return

    snapshot_name = args.snapshot_name or build_snapshot_name(args.start_date, args.end_date)
    snapshot_to_shared_drive(args, snapshot_name)


if __name__ == "__main__":
    main()
