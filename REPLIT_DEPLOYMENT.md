# Marriott Automation Replit Deployment

## Local Run

```bash
pip install -r requirements.txt
python app.py
```

Open `http://localhost:5001`.

The local app loads `.env` from this folder or the parent folder. To use a different location:

```bash
ENV_FILE=/path/to/.env python app.py
```

## Replit Secrets

Add these secrets in Replit:

- `GOOGLE_SERVICE_ACCOUNT_JSON`: full Google service account JSON
- `MODE_API_KEY_ID`
- `MODE_API_KEY_SECRET`
- `SIMPLIFY_EMAIL`
- `SIMPLIFY_PASSWORD`
- `SIMPLIFY_EXPORT_EMAIL` optional

The app materializes `GOOGLE_SERVICE_ACCOUNT_JSON` to `/tmp/marriott_service_account_key.json` at runtime, so the key file does not need to be committed.

## Replit Run

The `.replit` file runs:

```bash
python app.py
```

The app binds to `0.0.0.0` and uses Replit's `PORT` environment variable when present.

## Workflow Buttons

- `Run Data Workflow`: executes `data_workflow.py` with assignment logic disabled and always triggers a fresh Mode run plus fresh Simplify downloads.
- `Run Assignments`: executes `assignments.py` and always triggers a fresh Mode run plus fresh Simplify downloads.

## Daily Schedule

Open `/schedule` to change the daily schedule. The default is enabled at `08:30` in `Asia/Kolkata`.

At the scheduled time, the app runs:

1. Data Workflow
2. Assignments, only after Data Workflow completes successfully

The schedule is stored in `schedule_config.json` on the Replit filesystem. The Replit deployment must be running for the in-app scheduler to fire.

Drive saves:

- Data Workflow copies the populated workbook to `1nPq1cEdPRlE5irYyetqYi24uTySFHv0J`.
- Assignments copies the populated workbook to `1m-4NWsTQUQ51mJiQfMD-emhvm0qN1Fc_`.

Use `Dry run only` to validate data preparation and assignment counts without updating Google Sheets.
