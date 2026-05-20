# Marriott Raw Import Runbook

Target sheet:

https://docs.google.com/spreadsheets/d/1gMYap7mOK17l7lOhPtBohGjJoC1FsPWfjyywXwFjLWk/edit

This document explains how the sheet was brought to the current raw-data stage.

## Inputs

### Google Sheets

- Service account key: `service_account_key.json`
- Required scope: `https://www.googleapis.com/auth/spreadsheets`
- The service account must have edit access to the target spreadsheet.

### Mode

- Workspace: `instawork`
- Report URL: `https://app.mode.com/instawork/reports/9b580f8ef3ca`
- Report token: `9b580f8ef3ca`
- Query used for Marriott Mode raw data: `VMS - Marriott`
- Query token in export filename: `4e4a50423645`
- Successful report run used: `5742a194efc9`
- Run parameters observed:
  - `enterprise_VMS_name = enterprise_vmsmarriott`
  - `start_date = 2026-05-09`
  - `end_date = 2026-05-29`

Do not store Mode credentials in code. Use environment variables:

```sh
export MODE_API_KEY_ID="..."
export MODE_API_KEY_SECRET="..."
```

### Simplify

- Login URL: `https://marriott.simplifyvmsapp.com/site/login`
- Reports page: `https://marriott.simplifyvmsapp.com/Report/EmbeddedReports/embeddedIndex`

Do not store Simplify credentials in code. Use environment variables:

```sh
export SIMPLIFY_EMAIL="..."
export SIMPLIFY_PASSWORD="..."
```

Reports downloaded from Simplify:

- `Active Assignments Details - Vendor`
- `Candidate Details`
- `Job Status Report`

## Local Files Created

Mode export:

- `downloads/mode_raw/mode_marriott_run_5742a194efc9.zip`
- Extracted folder: `downloads/mode_raw/mode_marriott_run_5742a194efc9/`
- Marriott raw CSV:
  - `becky_leilani_vms_report-vms_-_marriott-4e4a50423645-2026-05-18-04-25-38.csv`

Simplify page exports:

- `downloads/simplify_raw/pages/Active_Assignments_Details_-_Vendor__page_1_XKOngcsFeU.xlsx`
- `downloads/simplify_raw/pages/Candidate_Details__page_1_XKOngcsFeU.xlsx`
- `downloads/simplify_raw/pages/Job_Status_Report__page_1_XKOngcsFeU.xlsx`

Note: the whole-workbook Simplify export returned `No Data`, so the useful files came from the Sigma page export endpoint. Page 2 for these workbooks returned a server `500`; page 1 contained the data used for this sheet.

## Target Tabs Created

### `Raw Import Summary`

Small audit tab showing:

- target tab name
- row count
- source/filter
- local export file used

### `Raw Data`

Source:

- Mode `VMS - Marriott` CSV

Rows kept:

- weekdays only
- `2026-05-11` through `2026-05-29`

Columns kept:

- `fieldglass_site_name`
- `name`
- `location`
- `position`
- `shift_name`
- `first_name`
- `last_name`
- `date_of_shift_start`
- `partner_rate`
- `pro_rate`
- `ot_pay_rate`
- `dt_pay_rate`
- `email`
- `full_name`
- `mark_up`
- `state_code`
- `worker_id`
- `first_shift_timestamp`
- `dob_mmdd`
- `shift_id`
- `shiftgroup_id`

Uploaded count:

- `362` data rows
- `21` columns

### `Mode`

Same contents as `Raw Data`.

This was added because the downstream workbook logic expects the Mode data to be visible under a `Mode` tab name.

Uploaded count:

- `362` data rows
- `21` columns

### `Open & Closed`

Source:

- Simplify `Active Assignments Details - Vendor`

Rows kept:

- assignment statuses `Open`, `Closed`, and `Cancelled`
- footer/disclaimer rows removed

Uploaded count:

- `14,274` data rows
- `57` columns

### `Open Active`

Source:

- derived from `Open & Closed`

Rows kept:

- `Assignment Status = Open`

Additional helper columns added at the front:

- `Concat1 = Department Name + Vendor Tracking ID 1 + Client ST Bill Rate`
- `Concat2 = Work Location ID + Vendor Tracking ID 1 + Client ST Bill Rate`

Other transformation:

- `Work Location` is split into:
  - `Work Location ID`
  - `Locations`
- original `Business Unit` is removed to match the final workbook shape.

Uploaded count:

- `1,275` data rows
- `59` columns

### `CAN Details`

Source:

- Simplify `Candidate Details`

Rows kept:

- rows with `Candidate Ref ID`

Uploaded count:

- `6,993` data rows
- `8` columns

### `Jobs`

Source:

- Simplify `Job Status Report`

Rows kept:

- rows with `Job ID`

Uploaded count:

- `494` data rows
- `17` columns

## How The Data Was Uploaded

The import used the Google Sheets API with `valueInputOption=RAW`.

The process was:

1. Download Mode report export ZIP from:

```text
https://modeanalytics.com/api/instawork/reports/9b580f8ef3ca/runs/5742a194efc9/results/content.csv
```

2. Extract the ZIP and select the `VMS - Marriott` CSV.

3. Log into Simplify as vendor user.

4. On `All Reports`, find the three required reports:

- `Active Assignments Details - Vendor`
- `Candidate Details`
- `Job Status Report`

5. Open each workbook page, read its report ID and page ID, then call the Sigma export endpoint:

```text
https://api-v3reporting.simplifyvmsapp.com/api/sigma/workbook/{report_id}/csv_post?email=...
```

6. Download the returned S3 `download_url`.

7. Read the CSV/XLSX files with pandas, apply the row filters/transforms above, then update the target spreadsheet tabs.

8. Copy `Raw Data` into a separate `Mode` tab.

## Verification

After upload, the target sheet had:

- `Raw Import Summary`: `5` data rows
- `Raw Data`: `362` data rows
- `Mode`: `362` data rows
- `Open & Closed`: `14,274` data rows
- `Open Active`: `1,275` data rows
- `CAN Details`: `6,993` data rows
- `Jobs`: `494` data rows

The default blank `Sheet1` was left unchanged.
