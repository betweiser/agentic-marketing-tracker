#!/usr/bin/env python3
"""
Agentic AI Marketing Tracker — syncs Metabase card 11715 into a Google Sheet.

What it does, every time it runs:
 1. Reads Month / Year / Start Date / End Date from the "Dashboard" tab (cells B1:B4).
 2. Turns those into a from_date / to_date range.
 3. Calls the Metabase question (card 11715) with that date range.
 4. Writes the result table back into the sheet, formatted, replacing old data.

Needs these environment variables (set as GitHub Secrets, injected by the workflow):
  METABASE_API_KEY          - your Metabase API key
  METABASE_BASE_URL         - e.g. https://metabase-lierhfgoeiwhr.newtonschool.co
  METABASE_CARD_ID          - 11715
  GOOGLE_CREDENTIALS_FILE   - path to the service-account json (written by the workflow)
  SHEET_ID                  - the Google Sheet's ID (from its URL)
  SHEET_TAB_NAME            - default "Dashboard"
"""

import os
import sys
import calendar
import datetime as dt

import requests
import gspread
from google.oauth2.service_account import Credentials

MB_BASE_URL = os.environ["METABASE_BASE_URL"].rstrip("/")
MB_API_KEY = os.environ["METABASE_API_KEY"]
MB_CARD_ID = os.environ["METABASE_CARD_ID"]
GOOGLE_CREDENTIALS_FILE = os.environ["GOOGLE_CREDENTIALS_FILE"]
SHEET_ID = os.environ["SHEET_ID"]
SHEET_TAB_NAME = os.environ.get("SHEET_TAB_NAME", "Dashboard")

HEADER_ROW = 6          # row the column headers live on
DATA_START_ROW = 7      # first data row

MONTHS = {name.lower(): num for num, name in enumerate(calendar.month_name) if name}


def get_worksheet():
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_file(GOOGLE_CREDENTIALS_FILE, scopes=scopes)
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SHEET_ID)
    return sh.worksheet(SHEET_TAB_NAME)


def read_filters(ws):
    month_raw = ws.acell("B1").value
    year_raw = ws.acell("B2").value
    start_raw = ws.acell("B3").value
    end_raw = ws.acell("B4").value

    if not all([month_raw, year_raw, start_raw, end_raw]):
        sys.exit("ERROR: Month/Year/Start Date/End Date cells (B1:B4) are not all filled in.")

    month_num = MONTHS.get(month_raw.strip().lower())
    if not month_num:
        sys.exit(f"ERROR: '{month_raw}' is not a recognized month name (e.g. September).")

    year = int(year_raw)
    start_day = int(start_raw)
    end_day = int(end_raw)

    from_date = dt.date(year, month_num, start_day)
    to_date = dt.date(year, month_num, end_day)
    return from_date, to_date


def run_metabase_query(from_date, to_date):
    # We already know the query's parameter names from the SQL itself
    # ({{from_date}} / {{to_date}}), so no need to introspect the card
    # (that requires permissions your API key may not have).
    parameters = [
        {
            "type": "date/single",
            "target": ["variable", ["template-tag", "from_date"]],
            "value": from_date.isoformat(),
        },
        {
            "type": "date/single",
            "target": ["variable", ["template-tag", "to_date"]],
            "value": to_date.isoformat(),
        },
    ]

    r = requests.post(
        f"{MB_BASE_URL}/api/card/{MB_CARD_ID}/query",
        headers={"x-api-key": MB_API_KEY, "Content-Type": "application/json"},
        json={"parameters": parameters},
        timeout=180,
    )
    r.raise_for_status()
    payload = r.json()

    data = payload["data"]
    cols = [c["display_name"] or c["name"] for c in data["cols"]]
    rows = data["rows"]
    return cols, rows


def write_to_sheet(ws, from_date, to_date, cols, rows):
    # drop the internal sort_order column if present, and any trailing empty col
    drop_idx = [i for i, c in enumerate(cols) if c.strip().lower() == "sort_order"]

    def clean_row(row):
        return [v for i, v in enumerate(row) if i not in drop_idx]

    clean_cols = clean_row(cols)
    clean_rows = [clean_row(r) for r in rows]

    n_cols = len(clean_cols)
    n_rows = len(clean_rows)

    # clear previous header + data block, keep the 4 filter rows untouched
    ws.batch_clear([f"A{HEADER_ROW}:Z{DATA_START_ROW + 500}"])

    values = [clean_cols] + clean_rows
    end_col_letter = gspread.utils.rowcol_to_a1(1, n_cols).rstrip("1")
    rng = f"A{HEADER_ROW}:{end_col_letter}{HEADER_ROW + n_rows}"
    ws.update(rng, values, value_input_option="USER_ENTERED")

    # last row is the TOTAL row (query already appends it) -> bold it
    total_row_num = HEADER_ROW + n_rows  # header row + all rows incl TOTAL

    fmt_requests = {
        "requests": []
    }

    sheet_id = ws.id

    def add_fmt(row_start, row_end, col_start, col_end, fmt, fields):
        fmt_requests["requests"].append({
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": row_start - 1,
                    "endRowIndex": row_end,
                    "startColumnIndex": col_start - 1,
                    "endColumnIndex": col_end,
                },
                "cell": {"userEnteredFormat": fmt},
                "fields": fields,
            }
        })

    # header row styling
    add_fmt(
        HEADER_ROW, HEADER_ROW, 1, n_cols,
        {
            "backgroundColor": {"red": 0.11, "green": 0.15, "blue": 0.25},
            "textFormat": {"bold": True, "foregroundColor": {"red": 1, "green": 1, "blue": 1}},
            "horizontalAlignment": "CENTER",
            "wrapStrategy": "WRAP",
        },
        "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment,wrapStrategy)",
    )

    # TOTAL row styling
    add_fmt(
        total_row_num, total_row_num, 1, n_cols,
        {
            "backgroundColor": {"red": 0.93, "green": 0.95, "blue": 1.0},
            "textFormat": {"bold": True},
        },
        "userEnteredFormat(backgroundColor,textFormat)",
    )

    # filter label cells styling (A1:A4)
    fmt_requests["requests"].append({
        "repeatCell": {
            "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 4,
                      "startColumnIndex": 0, "endColumnIndex": 1},
            "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}},
            "fields": "userEnteredFormat(textFormat)",
        }
    })

    # freeze the filter block + header row
    fmt_requests["requests"].append({
        "updateSheetProperties": {
            "properties": {"sheetId": sheet_id, "gridProperties": {"frozenRowCount": HEADER_ROW}},
            "fields": "gridProperties.frozenRowCount",
        }
    })

    # auto-resize columns
    fmt_requests["requests"].append({
        "autoResizeDimensions": {
            "dimensions": {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": 0, "endIndex": n_cols}
        }
    })

    ws.spreadsheet.batch_update(fmt_requests)

    # note when this last ran, next to the filters, for peace of mind
    ws.update_acell("D1", f"Last synced: {dt.datetime.utcnow().isoformat(timespec='seconds')} UTC "
                          f"(range used: {from_date} to {to_date})")


def main():
    ws = get_worksheet()
    from_date, to_date = read_filters(ws)
    print(f"Running Metabase card {MB_CARD_ID} for {from_date} -> {to_date} ...")
    cols, rows = run_metabase_query(from_date, to_date)
    print(f"Got {len(rows)} rows back. Writing to sheet...")
    write_to_sheet(ws, from_date, to_date, cols, rows)
    print("Done.")


if __name__ == "__main__":
    main()
