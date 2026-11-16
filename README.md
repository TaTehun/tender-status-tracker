# Tender Status Tracker

A Python pipeline that logs into a TMS web portal, queries today's tender requests by region and carrier, exports the results to Excel, and emails a formatted report. No manual steps.

> **Visual overview:** [project-overview.html](project-overview.html)

---

## Overview

Each business day, load planners need to review the current tender status for two carriers across multiple regions. This pipeline runs three times per day (once per region), each triggered by Task Scheduler via a dedicated `.bat` file:

1. Logs in to the TMS portal via Selenium, retrying up to 3 times
2. Searches, per carrier, for today's tender requests filtered by carrier ID, status, and logistics group
3. Detects empty result sets before attempting export
4. Exports the result set as an Excel file, retrying up to 5 times on failure
5. Formats the file (column filter, auto-fit, filters) and sends one report per carrier by email with an embedded HTML summary table
6. Sends a separate alert email on failure or no-data, with a log excerpt attached on failure

---

## Project structure

```
tender-status-tracker/
├── main.py                      # Full automation pipeline entry point
├── main_example.py              # Portfolio version (no sensitive values)
├── email_pipeline_example.py    # Portfolio version of the email pipeline
├── run_east_coast.bat           # Task Scheduler trigger, East region
├── run_central.bat              # Task Scheduler trigger, Central region
├── run_west_coast.bat           # Task Scheduler trigger, West region
├── email_pipeline/
│   ├── email_setup.py           # All settings (paths, SMTP, recipients, subjects)
│   └── sender.py                # EmailSender class + load_sender() factory
├── utils/
│   ├── date_utils.py            # Date formatting utilities
│   ├── email_utils.py           # xlsx formatting + HTML email body builder
│   └── log_utils.py             # In-memory log buffering + failure log snippets
└── brd/                         # Business requirement documents
```

---

## How it works

### 1. Run selection
Each `.bat` file passes a run key as a command-line argument (`east_coast`, `central`, or `west_coast`). `main.py` reads this via `sys.argv` and looks up the corresponding logistics groups, label, region name, and CC list from the `RUNS` dict.

### 2. Login
Selenium launches Chrome with a custom download directory and `download.prompt_for_download` / `safebrowsing` prefs, plus an explicit CDP `Browser.setDownloadBehavior` call so headless-style downloads always land in the target folder. `webdriver-manager` resolves `chromedriver` automatically. The pipeline retries login up to 3 times before aborting the run.

### 3. Search per carrier
The pipeline navigates to the Tender Requests menu (re-expanding the nav tree only if it isn't already visible) and sets search conditions once per carrier:
- Carrier ID set to the target carrier for that pass
- Tender Request Status multi-selected via JavaScript (the select element is hidden)
- Logistics Group multi-selected via JavaScript, values vary per region
- Date range set to `TODAY` for both from and to

### 4. No-data detection
Before exporting, the pipeline checks for a "no records found" message with the Export button absent. If detected, it sends a no-data alert for that carrier and skips export, avoiding wasted retry attempts.

### 5. Export
The Export Data flow opens a popup window. The pipeline switches to the popup, submits, polls the download directory for the output file (up to 60 seconds), closes the popup, and returns to the main page. It retries up to 5 times per carrier.

### 6. Format and send
The exported `.xlsx` is filtered down to the report columns (via `format_xlsx`), auto-fit, and auto-filtered, then an HTML email is built with only the `Load ID` and `Tender Request Status` columns embedded as a table in the body. The formatted file is attached and sent via SMTP with BCC support. Each carrier's report is sent independently, so one carrier failing doesn't block the other.

### 7. Failure and no-data alerts
Each carrier has its own alert path, sent to a separate recipient list. A no-data result skips export and sends a lightweight notice. An export or login error flushes the day's buffered log to disk and embeds its WARNING/ERROR lines in the alert email body.

---

## File naming

Exported files are saved as `{Carrier}_Tender Status_{Region}_{mmddyyyy}.xlsx` (e.g. `ProviderA_Tender Status_East_05042026.xlsx`). If a file for the same carrier, region, and date already exists, it is overwritten. The pipeline keeps the most recent files per carrier and deletes anything beyond the configured limit on each run.

---

## Configuration

All settings are in `email_pipeline/email_setup.py`:

| Setting | Description |
|---------|-------------|
| `TMS_USER_ENV` / `TMS_PASS_ENV` | `.env` key names for TMS credentials |
| `ENV_PATH` | Path to the `.env` file |
| `DOWNLOAD_BASE` | Local folder where exports are saved |
| `LOG_BASE` | Local folder where daily run logs are written |
| `MAX_DATA_FOLDERS` | Max number of export files to retain, per carrier |
| `SMTP_SERVER` / `SMTP_PORT` | SMTP server settings |
| `SMTP_USER_ENV` / `SMTP_PASS_ENV` | `.env` key names for SMTP credentials |
| `NOTIFICATION_FROM` | Sender address |
| `EMAIL_TO` / `EMAIL_BCC` | Report recipients. CC is region specific, defined per-run in `RUNS` (main.py) |
| `EMAIL_SUBJECT` | Report subject template, formatted with region and date |
| `EMAIL_TO_FAILURE` / `EMAIL_CC_FAILURE` | Failure / no-data alert recipients |
| `EMAIL_SUBJECT_FAILURE` / `EMAIL_SUBJECT_NO_DATA` | Failure / no-data alert subject templates |

The second carrier's report/failure/no-data settings mirror the above with a `_PROVIDER_B`-style suffix.

Credentials are never hardcoded. They are stored in a `.env` file and loaded at runtime via `python-dotenv`.

---

## Requirements

```
selenium
webdriver-manager
pandas
openpyxl
python-dotenv
```

`webdriver-manager` resolves `chromedriver` automatically at runtime, so you never place the driver by hand.

---

## Running

Each region is triggered by its own bat file (intended for Task Scheduler):

```
run_east_coast.bat   →  python main.py east_coast
run_central.bat      →  python main.py central
run_west_coast.bat   →  python main.py west_coast
```

Or run directly:

```bash
python main.py east_coast
python main.py central
python main.py west_coast
```

---

## Changelog

### v1.4 · 2026-08-12 · Dual-carrier support and failure log alerts
- Second carrier added. Each carrier is searched, exported, and emailed independently per run
- `webdriver-manager` replaces the fixed `chromedriver.exe` path and resolves the driver automatically
- Login retried up to 3 times; nav tree expansion skipped if already visible
- No-data detection before export skips wasted retry attempts and sends a distinct alert
- Failure alerts now flush the buffered run log and embed WARNING/ERROR lines in the email body
- List Customization step removed. Report columns are now filtered from the downloaded xlsx instead

### v1.3 · 2026-05-06 · Multi-region dispatch
- `RUNS` dict + `sys.argv` key. Logistics groups resolved at runtime per region
- Single entry point replaces per-region scripts

### v1.2 · 2026-05-05 · Email pipeline hardening
- BCC support, failure alert email with separate recipient list
- File rotation. Oldest exports pruned each run

### v1.1 · 2026-05-04 · Project restructure
- Refactored into `email_pipeline/` + `utils/` module layout
- Added `project-overview.html`

### v1.0 · 2026-04-30 · Initial release
- Selenium TMS login, tender search, column customization, export
- SMTP report distribution
