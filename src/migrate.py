#!/usr/bin/env python3
"""
Nexus Consulting Group — Smartsheet to monday.com Migration Script

Reads the raw Smartsheet CSV export, performs all necessary data
transformations (deduplication, status normalization), creates the
board structure in monday.com, and imports all engagements and
deliverables via the GraphQL API.

Usage:
    python src/migrate.py [path/to/csv]   (defaults to data/nexus_smartsheet_export.csv)

Prerequisites:
    Set MONDAY_API_KEY in a .env file or as an environment variable.
"""

import csv
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

# ── Configuration ────────────────────────────────────────────────────────────
API_KEY = os.environ.get("MONDAY_API_KEY", "")
API_URL = "https://api.monday.com/v2"
REQUEST_DELAY = 0.3   # seconds between mutations — stays well under rate limits

# ── Status Normalisation Maps ────────────────────────────────────────────────
# Discovery call noted multiple synonymous values in the source data.
# We collapse them into canonical labels that will appear in monday.com.

ENGAGEMENT_STATUS_MAP = {
    "active":      "Active",
    "in progress": "Active",     # synonym identified in discovery call
    "complete":    "Complete",
    "done":        "Complete",   # synonym identified in discovery call
    "on hold":     "On Hold",
    "not started": "Not Started",
}

DELIVERABLE_STATUS_MAP = {
    "to do":         "To Do",
    "not started":   "To Do",         # synonym identified in discovery call
    "in progress":   "In Progress",
    "working on it": "In Progress",   # synonym identified in discovery call
    "in review":     "In Review",
    "done":          "Done",
}

PRIORITY_MAP = {
    "high":   "High",
    "medium": "Medium",
    "low":    "Low",
}

# Status label index assignments for monday.com status ("color") columns.
# Indices 1-4 map to our canonical labels. Index 0 is reserved.
ENGAGEMENT_STATUS_LABELS = {"1": "Active", "2": "On Hold", "3": "Not Started", "4": "Complete"}
DELIVERABLE_STATUS_LABELS = {"1": "To Do", "2": "In Progress", "3": "In Review", "4": "Done"}
PRIORITY_LABELS            = {"1": "High",  "2": "Medium",     "3": "Low"}


# ── GraphQL Helper ────────────────────────────────────────────────────────────
def graphql(query: str, variables: dict | None = None) -> dict:
    """Execute a GraphQL request against the monday.com v2 API."""
    if not API_KEY:
        print("ERROR: MONDAY_API_KEY is not set. Add it to your .env file.")
        sys.exit(1)

    headers = {
        "Authorization": API_KEY,
        "Content-Type": "application/json",
        "API-Version": "2023-10",
    }
    payload: dict = {"query": query}
    if variables:
        payload["variables"] = variables

    time.sleep(REQUEST_DELAY)
    response = requests.post(API_URL, json=payload, headers=headers)
    response.raise_for_status()

    data = response.json()
    if "errors" in data:
        raise RuntimeError(f"GraphQL error:\n{json.dumps(data['errors'], indent=2)}")
    return data["data"]


# ── Date Helper ───────────────────────────────────────────────────────────────
def to_iso_date(date_str: str) -> str | None:
    """Convert MM/DD/YYYY → YYYY-MM-DD (monday.com date format)."""
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str.strip(), "%m/%d/%Y").strftime("%Y-%m-%d")
    except ValueError:
        return None


# ── Data Loading & Transformation ─────────────────────────────────────────────
def load_csv(filepath: str) -> list[dict]:
    with open(filepath, newline="", encoding="utf-8") as f:
        return [{k.strip(): v.strip() for k, v in row.items()}
                for row in csv.DictReader(f)]


def extract_engagements(rows: list[dict]) -> dict[str, dict]:
    """
    Deduplicate engagement rows (one row per deliverable in the source) and
    normalise inconsistent status values found in the discovery call.
    Returns an ordered dict keyed by engagement_id.
    """
    seen: dict[str, dict] = {}
    for row in rows:
        eid = row["engagement_id"]
        if eid not in seen:
            raw = row["engagement_status"].lower().strip()
            seen[eid] = {
                "engagement_id":   eid,
                "name":            row["engagement_name"],
                "client":          row["client"],
                "lead":            row["engagement_lead"],
                "start_date":      to_iso_date(row["engagement_start"]),
                "end_date":        to_iso_date(row["engagement_end"]),
                "budget":          row["budget"],
                "status":          ENGAGEMENT_STATUS_MAP.get(raw, row["engagement_status"]),
                "_raw_status":     row["engagement_status"],   # kept for validation
            }
    return seen


def extract_deliverables(rows: list[dict]) -> list[dict]:
    """Normalise deliverable rows — status, priority, and date formats."""
    result = []
    for row in rows:
        raw_status   = row["deliverable_status"].lower().strip()
        raw_priority = row["priority"].lower().strip()
        result.append({
            "deliverable_id":  row["deliverable_id"],
            "name":            row["deliverable_name"],
            "engagement_id":   row["engagement_id"],
            "assignee":        row["assignee"],
            "due_date":        to_iso_date(row["due_date"]),
            "priority":        PRIORITY_MAP.get(raw_priority, row["priority"]),
            "status":          DELIVERABLE_STATUS_MAP.get(raw_status, row["deliverable_status"]),
            "hours":           row["hours_estimated"],
            "_raw_status":     row["deliverable_status"],   # kept for validation
        })
    return result


# ── Board & Column Helpers ────────────────────────────────────────────────────
def create_board(name: str) -> str:
    print(f"  Creating board: '{name}' …")
    result = graphql(
        """
        mutation($name: String!) {
          create_board(board_name: $name, board_kind: public) { id name }
        }
        """,
        {"name": name},
    )
    board_id = result["create_board"]["id"]
    print(f"  ✓ '{name}' created  (ID: {board_id})")
    return board_id


def create_column(board_id: str, title: str, col_type: str,
                  defaults: dict | None = None) -> str:
    variables: dict = {"boardId": board_id, "title": title, "colType": col_type}
    if defaults:
        variables["defaults"] = json.dumps(defaults)

    result = graphql(
        """
        mutation($boardId: ID!, $title: String!, $colType: ColumnType!, $defaults: JSON) {
          create_column(board_id: $boardId, title: $title,
                        column_type: $colType, defaults: $defaults) { id title }
        }
        """,
        variables,
    )
    col_id = result["create_column"]["id"]
    print(f"    + {title:30s}  [{col_type}]  id={col_id}")
    return col_id


def set_status_labels(board_id: str, col_id: str, labels: dict) -> None:
    """Replace the status labels on a 'color' (status) column."""
    graphql(
        """
        mutation($boardId: ID!, $colId: String!, $value: String!) {
          change_column_metadata(board_id: $boardId, column_id: $colId,
                                 column_property: labels, value: $value) { id }
        }
        """,
        {"boardId": board_id, "colId": col_id, "value": json.dumps(labels)},
    )
    print(f"    ✓ Labels set on column {col_id}: {list(labels.values())}")


def create_item(board_id: str, name: str, col_values: dict) -> str:
    result = graphql(
        """
        mutation($boardId: ID!, $name: String!, $vals: JSON!) {
          create_item(board_id: $boardId, item_name: $name,
                      column_values: $vals) { id name }
        }
        """,
        {"boardId": board_id, "name": name, "vals": json.dumps(col_values)},
    )
    return result["create_item"]["id"]


# ── Main Migration ────────────────────────────────────────────────────────────
def run_migration(csv_path: str) -> dict:
    print("\n" + "═" * 62)
    print("  NEXUS CONSULTING GROUP  —  Smartsheet → monday.com Migration")
    print("═" * 62)

    # ── 1. Load & transform source data ──────────────────────────────────────
    print("\n[1/5]  Loading & transforming source data …")
    rows          = load_csv(csv_path)
    engagements   = extract_engagements(rows)
    deliverables  = extract_deliverables(rows)

    normalised_eng = sum(
        1 for e in engagements.values()
        if e["_raw_status"] != e["status"]
    )
    normalised_del = sum(
        1 for d in deliverables
        if d["_raw_status"] != d["status"]
    )
    print(f"  ✓ {len(engagements)} unique engagements  "
          f"({normalised_eng} status values normalised)")
    print(f"  ✓ {len(deliverables)} deliverables  "
          f"({normalised_del} status values normalised)")

    # ── 2. Create Engagements board ───────────────────────────────────────────
    print("\n[2/5]  Creating Engagements board …")
    eng_board_id = create_board("Nexus Engagements")

    print("  Adding columns …")
    eng_cols = {
        "engagement_id": create_column(eng_board_id, "Engagement ID",     "text"),
        "client":        create_column(eng_board_id, "Client",             "text"),
        "lead":          create_column(eng_board_id, "Engagement Lead",    "text"),
        "start_date":    create_column(eng_board_id, "Start Date",         "date"),
        "end_date":      create_column(eng_board_id, "End Date",           "date"),
        "budget":        create_column(eng_board_id, "Budget ($)",         "numbers"),
        "status":        create_column(eng_board_id, "Engagement Status",  "color"),
    }
    set_status_labels(eng_board_id, eng_cols["status"], ENGAGEMENT_STATUS_LABELS)

    # ── 3. Create Deliverables board ──────────────────────────────────────────
    print("\n[3/5]  Creating Deliverables board …")
    del_board_id = create_board("Nexus Deliverables")

    print("  Adding columns …")
    del_cols = {
        "deliverable_id": create_column(del_board_id, "Deliverable ID",      "text"),
        "assignee":       create_column(del_board_id, "Assignee",             "text"),
        "due_date":       create_column(del_board_id, "Due Date",             "date"),
        "priority":       create_column(del_board_id, "Priority",             "color"),
        "status":         create_column(del_board_id, "Deliverable Status",   "color"),
        "hours":          create_column(del_board_id, "Hours Estimated",      "numbers"),
        "engagement":     create_column(del_board_id, "Engagement",           "board_relation",
                                        {"boardIds": [int(eng_board_id)]}),
    }
    set_status_labels(del_board_id, del_cols["priority"], PRIORITY_LABELS)
    set_status_labels(del_board_id, del_cols["status"],   DELIVERABLE_STATUS_LABELS)

    # ── 4. Import engagements ─────────────────────────────────────────────────
    print(f"\n[4/5]  Importing {len(engagements)} engagements …")
    engagement_item_ids: dict[str, str] = {}

    for i, (eid, eng) in enumerate(engagements.items(), 1):
        print(f"  [{i:02d}/{len(engagements)}]  {eid}  {eng['name']}")
        col_values: dict = {
            eng_cols["engagement_id"]: eng["engagement_id"],
            eng_cols["client"]:        eng["client"],
            eng_cols["lead"]:          eng["lead"],
            eng_cols["budget"]:        eng["budget"],
            eng_cols["status"]:        {"label": eng["status"]},
        }
        if eng["start_date"]:
            col_values[eng_cols["start_date"]] = {"date": eng["start_date"]}
        if eng["end_date"]:
            col_values[eng_cols["end_date"]] = {"date": eng["end_date"]}

        item_id = create_item(eng_board_id, eng["name"], col_values)
        engagement_item_ids[eid] = item_id
        print(f"         ✓ monday item ID {item_id}")

    # ── 5. Import deliverables ────────────────────────────────────────────────
    print(f"\n[5/5]  Importing {len(deliverables)} deliverables …")
    deliverable_item_ids: list[str] = []

    for i, deliv in enumerate(deliverables, 1):
        print(f"  [{i:02d}/{len(deliverables)}]  {deliv['deliverable_id']}  {deliv['name']}")
        col_values = {
            del_cols["deliverable_id"]: deliv["deliverable_id"],
            del_cols["assignee"]:       deliv["assignee"],
            del_cols["hours"]:          deliv["hours"],
            del_cols["priority"]:       {"label": deliv["priority"]},
            del_cols["status"]:         {"label": deliv["status"]},
        }
        if deliv["due_date"]:
            col_values[del_cols["due_date"]] = {"date": deliv["due_date"]}

        # Link back to parent engagement item
        parent_item_id = engagement_item_ids.get(deliv["engagement_id"])
        if parent_item_id:
            col_values[del_cols["engagement"]] = {"item_ids": [int(parent_item_id)]}

        item_id = create_item(del_board_id, deliv["name"], col_values)
        deliverable_item_ids.append(item_id)
        print(f"         ✓ monday item ID {item_id}")

    # ── Save results for the validation script ────────────────────────────────
    results = {
        "eng_board_id":            eng_board_id,
        "del_board_id":            del_board_id,
        "eng_cols":                eng_cols,
        "del_cols":                del_cols,
        "engagement_item_ids":     engagement_item_ids,
        "deliverable_item_ids":    deliverable_item_ids,
        "source_engagement_count": len(engagements),
        "source_deliverable_count": len(deliverables),
    }
    out_path = Path("migration_results.json")
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2)

    print("\n" + "═" * 62)
    print("  MIGRATION COMPLETE")
    print(f"  Engagements migrated : {len(engagements)}")
    print(f"  Deliverables migrated: {len(deliverables)}")
    print(f"  Results saved to     : {out_path.resolve()}")
    print("═" * 62 + "\n")
    return results


if __name__ == "__main__":
    csv_file = sys.argv[1] if len(sys.argv) > 1 else "data/nexus_smartsheet_export.csv"
    if not Path(csv_file).exists():
        print(f"ERROR: CSV file not found: {csv_file}")
        sys.exit(1)
    run_migration(csv_file)
