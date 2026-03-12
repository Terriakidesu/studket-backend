param(
    [switch]$Listings,
    [switch]$Conversations,
    [switch]$Messages,
    [switch]$Notifications,
    [switch]$Transactions,
    [switch]$All,
    [switch]$Force
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
Set-Location $repoRoot

function Resolve-PythonPath {
    $venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
    if (Test-Path $venvPython) {
        return $venvPython
    }

    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonCommand) {
        return $pythonCommand.Source
    }

    throw "Python was not found. Expected .venv\Scripts\python.exe or a global python command."
}

function Read-YesNo {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Prompt
    )

    while ($true) {
        $answer = Read-Host "$Prompt [y/n]"
        switch ($answer.Trim().ToLowerInvariant()) {
            "y" { return $true }
            "yes" { return $true }
            "n" { return $false }
            "no" { return $false }
            default { Write-Host "Enter y or n." -ForegroundColor Yellow }
        }
    }
}

function Get-Selection {
    $selection = [ordered]@{
        listings = $false
        conversations = $false
        messages = $false
        notifications = $false
        transactions = $false
    }

    if ($All) {
        $selection.listings = $true
        $selection.conversations = $true
        $selection.messages = $true
        $selection.notifications = $true
        $selection.transactions = $true
        return $selection
    }

    $selection.listings = $Listings.IsPresent
    $selection.conversations = $Conversations.IsPresent
    $selection.messages = $Messages.IsPresent
    $selection.notifications = $Notifications.IsPresent
    $selection.transactions = $Transactions.IsPresent

    if ($selection.listings -or $selection.conversations -or $selection.messages -or $selection.notifications -or $selection.transactions) {
        return $selection
    }

    Write-Host "Choose what to reset." -ForegroundColor Cyan
    Write-Host "Resetting listings also removes listing media, tags, inventory, inquiries, reports, listing-linked transactions, and uploaded listing media files."
    Write-Host "Resetting conversations also removes conversation-linked messages, listing inquiries, and conversation reports."
    Write-Host "Resetting notifications removes user notification rows."
    Write-Host "Resetting transactions also removes reviews and transaction QR rows."
    $selection.listings = Read-YesNo "Reset listings?"
    $selection.conversations = Read-YesNo "Reset conversations?"
    $selection.messages = Read-YesNo "Reset messages?"
    $selection.notifications = Read-YesNo "Reset notifications?"
    $selection.transactions = Read-YesNo "Reset transactions?"
    return $selection
}

$selection = Get-Selection
$selectedKeys = @($selection.Keys | Where-Object { $selection[$_] })

if ($selectedKeys.Count -eq 0) {
    Write-Host "Nothing selected. No changes made." -ForegroundColor Yellow
    exit 0
}

Write-Host "Selected reset targets: $($selectedKeys -join ', ')" -ForegroundColor Cyan

if (-not $Force) {
    $confirmed = Read-YesNo "Proceed with deleting data from the selected tables?"
    if (-not $confirmed) {
        Write-Host "Cancelled." -ForegroundColor Yellow
        exit 0
    }
}

$pythonPath = Resolve-PythonPath
$selectionJson = @{
    listings = [bool]$selection.listings
    conversations = [bool]$selection.conversations
    messages = [bool]$selection.messages
    notifications = [bool]$selection.notifications
    transactions = [bool]$selection.transactions
} | ConvertTo-Json -Compress

$env:RESET_SELECTION_JSON = $selectionJson

$pythonScript = @'
import json
import os
import sys
from pathlib import Path

from sqlalchemy import text

from app.db.session import engine

selection = json.loads(os.environ["RESET_SELECTION_JSON"])

delete_groups = {
    "listings": [
        "review_for_listing_transactions",
        "transaction_qr_for_listing_transactions",
        "listing_inquiry",
        "listing_media",
        "listing_tags",
        "listing_inventory",
        "listing_report",
        "looking_for_report",
        "transaction_for_listings",
        "listing",
    ],
    "conversations": ["conversation_report", "listing_inquiry", "message", "conversation"],
    "messages": ["message"],
    "notifications": ["notification"],
    "transactions": ["review", "transaction_qr", "transaction"],
}

ordered_tables = []
seen = set()
for key in ("listings", "conversations", "messages", "notifications", "transactions"):
    if not selection.get(key):
        continue
    for table in delete_groups[key]:
        if table not in seen:
            ordered_tables.append(table)
            seen.add(table)

if not ordered_tables:
    print("Nothing selected. No changes made.")
    sys.exit(0)

def quote_table(table_name: str) -> str:
    return '"transaction"' if table_name == "transaction" else table_name

def count_for_key(connection, table_name: str) -> int:
    if table_name == "review_for_listing_transactions":
        return connection.execute(
            text('SELECT COUNT(*) FROM review WHERE transaction_id IN (SELECT transaction_id FROM "transaction" WHERE listing_id IN (SELECT listing_id FROM listing))')
        ).scalar_one()
    if table_name == "transaction_qr_for_listing_transactions":
        return connection.execute(
            text('SELECT COUNT(*) FROM transaction_qr WHERE transaction_id IN (SELECT transaction_id FROM "transaction" WHERE listing_id IN (SELECT listing_id FROM listing))')
        ).scalar_one()
    if table_name == "transaction_for_listings":
        return connection.execute(
            text('SELECT COUNT(*) FROM "transaction" WHERE listing_id IN (SELECT listing_id FROM listing)')
        ).scalar_one()
    return connection.execute(text(f"SELECT COUNT(*) FROM {quote_table(table_name)}")).scalar_one()

def delete_for_key(connection, table_name: str) -> None:
    if table_name == "review_for_listing_transactions":
        connection.execute(
            text('DELETE FROM review WHERE transaction_id IN (SELECT transaction_id FROM "transaction" WHERE listing_id IN (SELECT listing_id FROM listing))')
        )
        return
    if table_name == "transaction_qr_for_listing_transactions":
        connection.execute(
            text('DELETE FROM transaction_qr WHERE transaction_id IN (SELECT transaction_id FROM "transaction" WHERE listing_id IN (SELECT listing_id FROM listing))')
        )
        return
    if table_name == "transaction_for_listings":
        connection.execute(text('DELETE FROM "transaction" WHERE listing_id IN (SELECT listing_id FROM listing)'))
        return
    connection.execute(text(f"DELETE FROM {quote_table(table_name)}"))

def display_name(table_name: str) -> str:
    aliases = {
        "review_for_listing_transactions": "review",
        "transaction_qr_for_listing_transactions": "transaction_qr",
        "transaction_for_listings": "transaction",
    }
    return aliases.get(table_name, table_name)

before_counts: dict[str, int] = {}
after_counts: dict[str, int] = {}
removed_files: list[str] = []
removed_dirs: list[str] = []
static_root = Path("app/static")
listing_media_root = static_root / "listing-media"
listing_media_paths: list[str] = []

with engine.begin() as connection:
    if selection.get("listings"):
        listing_media_paths = [
            row[0]
            for row in connection.execute(text("SELECT file_path FROM listing_media")).fetchall()
            if row[0]
        ]

    for table in ordered_tables:
        before_counts[table] = count_for_key(connection, table)

    for table in ordered_tables:
        delete_for_key(connection, table)

    for table in ordered_tables:
        after_counts[table] = count_for_key(connection, table)

if selection.get("listings"):
    for path_value in listing_media_paths:
        normalized = str(path_value).strip().replace("\\", "/")
        if not normalized.startswith("/static/"):
            continue
        local_path = static_root / Path(normalized[len("/static/"):])
        if local_path.exists() and local_path.is_file():
            local_path.unlink()
            removed_files.append(str(local_path))

    if listing_media_root.exists():
        for directory in sorted(listing_media_root.glob("*"), reverse=True):
            if not directory.is_dir():
                continue
            try:
                next(directory.iterdir())
            except StopIteration:
                directory.rmdir()
                removed_dirs.append(str(directory))

print("Database reset complete.")
for table in ordered_tables:
    removed = before_counts[table] - after_counts[table]
    print(f"{display_name(table)}: removed {removed}, remaining {after_counts[table]}")
if selection.get("listings"):
    print(f"listing_media_files_removed: {len(removed_files)}")
    for path in removed_files:
        print(path)
    print(f"listing_media_dirs_removed: {len(removed_dirs)}")
    for path in removed_dirs:
        print(path)
'@

try {
    $pythonScript | & $pythonPath -
}
finally {
    Remove-Item Env:\RESET_SELECTION_JSON -ErrorAction SilentlyContinue
}
