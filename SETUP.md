# Setup guide — Agentic AI Marketing Tracker auto-sync

Follow these steps in order. Each one is short.

## Step 1 — Set up the Google Sheet

Open your sheet: https://docs.google.com/spreadsheets/d/1Jbec-2z8B4yxN53uEgvPwm5pxIla72UZHtrXo0xjarw/edit

1. Rename the tab (bottom of the screen) to exactly: `Dashboard`
2. Set up these cells exactly like this:

   | Cell | Value |
   |------|-------|
   | A1 | `Month` |
   | B1 | `September` |
   | A2 | `Year` |
   | B2 | `2026` |
   | A3 | `Start Date` |
   | B3 | `1` |
   | A4 | `End Date` |
   | B4 | `17` |

   Leave row 5 blank. Row 6 onward is where the script will write the table automatically — don't type anything there.

3. Share the sheet with your service account's email:
   - Open your service account's `.json` key file (the one you already have).
   - Find the line that says `"client_email": "something@something.iam.gserviceaccount.com"`.
   - Click **Share** (top-right of the sheet) → paste that email → set it to **Editor** → Send/Share.

## Step 2 — Create the GitHub repo

1. Go to https://github.com/new
2. Repo name: `agentic-marketing-tracker` (or anything you like)
3. Set it to **Private**
4. Click **Create repository**
5. On your own computer (or ask me — see note at the bottom), upload the 4 files I built:
   - `sync_sheet.py`
   - `requirements.txt`
   - `.github/workflows/daily-sync.yml`
   - `SETUP.md` (this file)

   Easiest way if you're not comfortable with git commands: on the new repo's GitHub page, click **"uploading an existing file"** and drag all 4 files/folders in, then commit.

## Step 3 — Add your secrets to GitHub

In your new repo: **Settings → Secrets and variables → Actions → New repository secret**

Add these 3 secrets one at a time:

| Secret name | Value |
|---|---|
| `METABASE_API_KEY` | your Metabase API key |
| `SHEET_ID` | `1Jbec-2z8B4yxN53uEgvPwm5pxIla72UZHtrXo0xjarw` (the long ID from your sheet's URL) |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | the **entire contents** of your service account `.json` key file, pasted as-is |

That's it — no other setup needed. The Metabase base URL and card ID are already written into the workflow file.

## Step 4 — Run it

1. In your repo, click the **Actions** tab.
2. Click **"Sync Agentic AI Marketing Tracker"** on the left.
3. Click **"Run workflow"** (top-right) → **Run workflow** again to confirm.
4. Wait ~30–60 seconds, then refresh your Google Sheet — the table should appear, formatted, below your 4 filter cells.

After this first manual run works, it will **run automatically every day at 8:30 AM IST** — no action needed from you.

## How to use it day to day

- To see a different date range: just change the 4 cells (Month/Year/Start Date/End Date) in the sheet, then go to the Actions tab and click **Run workflow** to refresh instantly. Otherwise it'll pick up the new dates on the next scheduled daily run.
- Cell D1 will show "Last synced: ..." so you always know how fresh the data is.

## If something goes wrong

Go to the **Actions** tab → click the failed run (red X) → it'll show the exact error in plain text. Common ones:
- "Month/Year/Start Date/End Date cells are not all filled in" → check A1:B4 in the Dashboard tab.
- A Google permission error → the sheet wasn't shared with the service account email (Step 1.3).
- A Metabase 401/403 error → the API key secret is wrong or expired.
