#!/usr/bin/env bash
# =============================================================================
# NZT-48 Trading System — Google Sheets API Setup Guide
# =============================================================================
# Run this script to see setup instructions, then run test_gsheets.py to verify.
# =============================================================================

set -euo pipefail

CREDS_TARGET="credentials/gsheets-service-account.json"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

print_header() {
    echo ""
    echo "============================================================"
    echo "  $1"
    echo "============================================================"
}

print_step() {
    echo ""
    echo "  STEP $1: $2"
    echo "  ----------------------------------------------------------"
}

print_header "NZT-48: Google Sheets API Setup"

echo ""
echo "  This guide sets up Google Sheets auto-logging for trade signals."
echo "  Estimated time: 10-15 minutes."
echo "  When complete, run:  python3 scripts/test_gsheets.py"

# ------------------------------------------------------------------ STEP 1
print_step "1" "Create a Google Cloud Project"
cat << 'STEP'

  a) Go to:  https://console.cloud.google.com/projectcreate
  b) Project name:  NZT48-Trading  (or any name you like)
  c) Click [Create] and wait ~30 seconds.
  d) Make sure the new project is selected in the top dropdown.

STEP

# ------------------------------------------------------------------ STEP 2
print_step "2" "Enable the Google Sheets API"
cat << 'STEP'

  a) Go to:  https://console.cloud.google.com/apis/library/sheets.googleapis.com
  b) Click [Enable].
  c) Also enable the Drive API:
     https://console.cloud.google.com/apis/library/drive.googleapis.com
  d) Click [Enable] there too.
  e) Both APIs must be enabled — gspread uses Drive to open spreadsheets by name.

STEP

# ------------------------------------------------------------------ STEP 3
print_step "3" "Create a Service Account"
cat << 'STEP'

  a) Go to:  https://console.cloud.google.com/iam-admin/serviceaccounts
  b) Click [+ Create Service Account].
  c) Name:        nzt48-sheets-writer
     Description: NZT-48 trading system sheets logger
  d) Click [Create and Continue].
  e) Role: select "Editor" (or "Google Sheets Editor" if you want minimal scope).
  f) Click [Continue] then [Done].

STEP

# ------------------------------------------------------------------ STEP 4
print_step "4" "Download the JSON Key File"
cat << 'STEP'

  a) In the Service Accounts list, click on "nzt48-sheets-writer".
  b) Go to the [Keys] tab.
  c) Click [Add Key] → [Create new key].
  d) Select [JSON] and click [Create].
  e) A file downloads automatically — something like:
       nzt48-trading-abc123-xxxxxxxxxxxx.json

STEP

# ------------------------------------------------------------------ STEP 5
print_step "5" "Place the Credentials File"
cat << 'STEP'

  Move the downloaded JSON file to:
STEP
echo ""
echo "    $REPO_ROOT/$CREDS_TARGET"
cat << 'STEP'

  Commands (adjust filename as needed):

    mkdir -p credentials
    mv ~/Downloads/nzt48-trading-*.json credentials/gsheets-service-account.json

  Then set the environment variable in your .env.production:

    GOOGLE_SHEETS_CREDS=credentials/gsheets-service-account.json

  IMPORTANT: credentials/ is in .gitignore — never commit this file.

STEP

# ------------------------------------------------------------------ STEP 6
print_step "6" "Share Your Spreadsheet with the Service Account"
cat << 'STEP'

  a) Open the JSON file you just saved.
  b) Find the "client_email" field — it looks like:
       nzt48-sheets-writer@nzt48-trading-xxxxxx.iam.gserviceaccount.com
  c) Open (or create) your Google Sheet:
       https://docs.google.com/spreadsheets/
  d) Click [Share] in the top-right corner.
  e) Paste the service account email address.
  f) Set permission to [Editor].
  g) Uncheck "Notify people" (it's a robot, not a person).
  h) Click [Share].

  Set the spreadsheet name in your .env.production:
    GOOGLE_SHEETS_SPREADSHEET_NAME=NZT-48 Trade Log

STEP

# ------------------------------------------------------------------ STEP 7
print_step "7" "Install Python Dependencies"
cat << 'STEP'

  pip install gspread google-auth

  Or if using Docker, add to requirements.txt:
    gspread>=6.0.0
    google-auth>=2.0.0

STEP

# ------------------------------------------------------------------ VERIFY
print_header "Verification"
echo ""
echo "  When all steps are done, run:"
echo ""
echo "    python3 $REPO_ROOT/scripts/test_gsheets.py"
echo ""
echo "  It will check imports, credentials file, and authentication."
echo ""

# ------------------------------------------------------------------ STATUS CHECK
print_header "Current Status Check"
echo ""

CREDS_FULL="$REPO_ROOT/$CREDS_TARGET"
if [ -f "$CREDS_FULL" ]; then
    echo "  [FOUND]   Credentials file: $CREDS_FULL"
else
    echo "  [MISSING] Credentials file: $CREDS_FULL"
    echo "            Follow Step 4 and 5 above."
fi

if python3 -c "import gspread" 2>/dev/null; then
    echo "  [OK]      gspread is installed"
else
    echo "  [MISSING] gspread not installed — run: pip install gspread google-auth"
fi

if python3 -c "from google.oauth2.service_account import Credentials" 2>/dev/null; then
    echo "  [OK]      google-auth is installed"
else
    echo "  [MISSING] google-auth not installed — run: pip install google-auth"
fi

echo ""
