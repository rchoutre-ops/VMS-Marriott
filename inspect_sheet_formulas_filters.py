#!/usr/bin/env python3
"""Inspect a Google Sheet for formulas and applied filters.

The script reads every tab in the configured spreadsheet and writes a Markdown
report containing:
  - every cell with a formula
  - basic sheet filters
  - saved filter views

It expects a Google service account key in ./service_account_key.json by default.
Make sure the spreadsheet is shared with the service account email.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build


SPREADSHEET_ID = "1YF408L3VZkw-7M17rCnMrsLCZ0KuKGGKipSyqHtYPeU"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]


def column_name(column_index: int) -> str:
    """Convert a zero-based column index to A1 notation letters."""
    name = ""
    column_index += 1
    while column_index:
        column_index, remainder = divmod(column_index - 1, 26)
        name = chr(65 + remainder) + name
    return name


def quote_sheet_name(title: str) -> str:
    escaped_title = title.replace("'", "''")
    return f"'{escaped_title}'"


def cell_a1(sheet_title: str, row_index: int, column_index: int) -> str:
    return f"{quote_sheet_name(sheet_title)}!{column_name(column_index)}{row_index + 1}"


def grid_range_to_a1(grid_range: dict[str, Any] | None, sheet_title: str) -> str:
    if not grid_range:
        return f"{quote_sheet_name(sheet_title)}!<unspecified range>"

    start_row = grid_range.get("startRowIndex")
    end_row = grid_range.get("endRowIndex")
    start_col = grid_range.get("startColumnIndex")
    end_col = grid_range.get("endColumnIndex")

    if start_row is None and end_row is None and start_col is None and end_col is None:
        return quote_sheet_name(sheet_title)

    start = ""
    end = ""

    if start_col is not None:
        start += column_name(start_col)
    if start_row is not None:
        start += str(start_row + 1)

    if end_col is not None:
        end += column_name(end_col - 1)
    if end_row is not None:
        end += str(end_row)

    if start and end:
        return f"{quote_sheet_name(sheet_title)}!{start}:{end}"
    if start:
        return f"{quote_sheet_name(sheet_title)}!{start}:"
    if end:
        return f"{quote_sheet_name(sheet_title)}!:{end}"
    return quote_sheet_name(sheet_title)


def load_spreadsheet(credentials_path: Path, spreadsheet_id: str) -> dict[str, Any]:
    credentials = Credentials.from_service_account_file(
        str(credentials_path),
        scopes=SCOPES,
    )
    service = build("sheets", "v4", credentials=credentials, cache_discovery=False)

    fields = (
        "spreadsheetId,"
        "properties(title),"
        "sheets("
        "properties(sheetId,title,gridProperties(rowCount,columnCount)),"
        "basicFilter,"
        "filterViews,"
        "data(startRow,startColumn,rowData(values(userEnteredValue)))"
        ")"
    )

    return (
        service.spreadsheets()
        .get(
            spreadsheetId=spreadsheet_id,
            includeGridData=True,
            fields=fields,
        )
        .execute()
    )


def extract_formulas(sheet: dict[str, Any]) -> list[dict[str, str]]:
    sheet_title = sheet["properties"]["title"]
    formulas: list[dict[str, str]] = []

    for grid_data in sheet.get("data", []):
        start_row = grid_data.get("startRow", 0)
        start_col = grid_data.get("startColumn", 0)

        for row_offset, row in enumerate(grid_data.get("rowData", [])):
            for col_offset, cell in enumerate(row.get("values", [])):
                formula = cell.get("userEnteredValue", {}).get("formulaValue")
                if not formula:
                    continue

                row_index = start_row + row_offset
                col_index = start_col + col_offset
                formulas.append(
                    {
                        "cell": cell_a1(sheet_title, row_index, col_index),
                        "formula": formula,
                    }
                )

    return formulas


def format_json(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True)


def criteria_heading(column_key: str, sheet_title: str, filter_range: dict[str, Any] | None) -> str:
    try:
        column_index = int(column_key)
    except ValueError:
        return column_key

    if filter_range and "startColumnIndex" in filter_range:
        column_index += filter_range["startColumnIndex"]

    return f"{column_name(column_index)} ({column_key})"


def format_filter_specs(filter_specs: list[dict[str, Any]], sheet_title: str) -> list[str]:
    lines: list[str] = []

    for filter_spec in filter_specs:
        column_index = filter_spec.get("columnIndex")
        data_source_column = filter_spec.get("dataSourceColumnReference")

        if column_index is not None:
            heading = column_name(column_index)
        elif data_source_column:
            heading = format_json(data_source_column)
        else:
            heading = "<unspecified column>"

        lines.append(f"- Column `{heading}`:")
        lines.append("```json")
        lines.append(format_json(filter_spec))
        lines.append("```")

    return lines


def format_basic_filter(sheet: dict[str, Any]) -> list[str]:
    sheet_title = sheet["properties"]["title"]
    basic_filter = sheet.get("basicFilter")

    if not basic_filter:
        return ["No basic filter enabled."]

    lines = [f"Basic filter range: `{grid_range_to_a1(basic_filter.get('range'), sheet_title)}`"]

    sort_specs = basic_filter.get("sortSpecs", [])
    if sort_specs:
        lines.append("")
        lines.append("Sort specs:")
        lines.append("```json")
        lines.append(format_json(sort_specs))
        lines.append("```")

    filter_specs = basic_filter.get("filterSpecs", [])
    if filter_specs:
        lines.append("")
        lines.append("Filter specs:")
        lines.extend(format_filter_specs(filter_specs, sheet_title))

    criteria = basic_filter.get("criteria", {})
    if criteria:
        lines.append("")
        lines.append("Criteria:")
        for column_key, criterion in sorted(criteria.items(), key=lambda item: int(item[0])):
            heading = criteria_heading(column_key, sheet_title, basic_filter.get("range"))
            lines.append(f"- Column `{heading}`:")
            lines.append("```json")
            lines.append(format_json(criterion))
            lines.append("```")

    if not sort_specs and not filter_specs and not criteria:
        lines.append("No criteria or sort specs are set on the basic filter.")

    return lines


def format_filter_views(sheet: dict[str, Any]) -> list[str]:
    sheet_title = sheet["properties"]["title"]
    filter_views = sheet.get("filterViews", [])

    if not filter_views:
        return ["No filter views."]

    lines: list[str] = []
    for index, filter_view in enumerate(filter_views, start=1):
        title = filter_view.get("title") or f"Filter view {index}"
        lines.append(f"Filter view: `{title}`")
        lines.append(f"- Range: `{grid_range_to_a1(filter_view.get('range'), sheet_title)}`")

        sort_specs = filter_view.get("sortSpecs", [])
        if sort_specs:
            lines.append("- Sort specs:")
            lines.append("```json")
            lines.append(format_json(sort_specs))
            lines.append("```")

        filter_specs = filter_view.get("filterSpecs", [])
        if filter_specs:
            lines.append("- Filter specs:")
            lines.extend(format_filter_specs(filter_specs, sheet_title))

        criteria = filter_view.get("criteria", {})
        if criteria:
            lines.append("- Criteria:")
            for column_key, criterion in sorted(criteria.items(), key=lambda item: int(item[0])):
                heading = criteria_heading(column_key, sheet_title, filter_view.get("range"))
                lines.append(f"  - Column `{heading}`:")
                lines.append("```json")
                lines.append(format_json(criterion))
                lines.append("```")

        if not sort_specs and not filter_specs and not criteria:
            lines.append("- No criteria or sort specs are set.")

        lines.append("")

    return lines


def build_report(spreadsheet: dict[str, Any]) -> str:
    spreadsheet_title = spreadsheet.get("properties", {}).get("title", "<untitled>")
    sheet_count = len(spreadsheet.get("sheets", []))

    lines = [
        f"# Formula And Filter Report: {spreadsheet_title}",
        "",
        f"- Spreadsheet ID: `{spreadsheet.get('spreadsheetId')}`",
        f"- Tabs inspected: `{sheet_count}`",
        "",
    ]

    total_formulas = 0

    for sheet in spreadsheet.get("sheets", []):
        properties = sheet["properties"]
        title = properties["title"]
        formulas = extract_formulas(sheet)
        total_formulas += len(formulas)

        lines.extend(
            [
                f"## {title}",
                "",
                f"- Sheet ID: `{properties.get('sheetId')}`",
                f"- Grid size: `{properties.get('gridProperties', {}).get('rowCount', 0)}` rows x "
                f"`{properties.get('gridProperties', {}).get('columnCount', 0)}` columns",
                f"- Formula cells: `{len(formulas)}`",
                "",
                "### Formulas",
                "",
            ]
        )

        if formulas:
            for item in formulas:
                lines.append(f"- `{item['cell']}`: `{item['formula']}`")
        else:
            lines.append("No formulas found.")

        lines.extend(["", "### Basic Filter", ""])
        lines.extend(format_basic_filter(sheet))
        lines.extend(["", "### Filter Views", ""])
        lines.extend(format_filter_views(sheet))
        lines.append("")

    lines.insert(4, f"- Formula cells found: `{total_formulas}`")

    return "\n".join(lines).rstrip() + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Inspect all tabs in a Google Sheet for formulas and filter settings."
    )
    parser.add_argument(
        "--spreadsheet-id",
        default=SPREADSHEET_ID,
        help="Google Sheets spreadsheet ID to inspect.",
    )
    parser.add_argument(
        "--credentials",
        type=Path,
        default=Path("service_account_key.json"),
        help="Path to the Google service account JSON key.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("sheet_formula_filter_report.md"),
        help="Markdown report output path.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    spreadsheet = load_spreadsheet(args.credentials, args.spreadsheet_id)
    report = build_report(spreadsheet)
    args.output.write_text(report, encoding="utf-8")
    print(f"Wrote report to {args.output}")


if __name__ == "__main__":
    main()
