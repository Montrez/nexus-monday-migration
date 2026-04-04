#!/usr/bin/env python3
"""
Nexus Consulting Group — Migration Validation Script

Queries the migrated data from monday.com via the GraphQL API, compares it
against the original Smartsheet CSV, and produces a human-readable validation
report.  Addresses Derek's and Priya's specific requirements from the
discovery call:

  • Record counts match (engagements and deliverables)
  • No deliverables are orphaned (missing engagement link)
  • No items have missing required fields (assignee, due date)
  • Normalised status values are correct
  • Budget figures are preserved accurately

Usage:
    python src/validate.py [path/to/csv] [path/to/migration_results.json]

    Defaults:
        CSV     → data/nexus_smartsheet_export.csv
        Results → migration_results.json
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

API_KEY = os.environ.get("MONDAY_API_KEY", "")
API_URL = "https://api.monday.com/v2"
REQUEST_DELAY = 0.3

# Must mirror the maps used in migrate.py exactly
ENGAGEMENT_STATUS_MAP = {
    "active":      "Active",
    "in progress": "Active",
    "complete":    "Complete",
    "done":        "Complete",
    "on hold":     "On Hold",
    "not started": "Not Started",
}
DELIVERABLE_STATUS_MAP = {
    "to do":         "To Do",
    "not started":   "To Do",
    "in progress":   "In Progress",
    "working on it": "In Progress",
    "in review":     "In Review",
    "done":          "Done",
}
PRIORITY_MAP = {"high": "High", "medium": "Medium", "low": "Low"}


# ── GraphQL Helper ────────────────────────────────────────────────────────────
def graphql(query: str, variables: dict | None = None) -> dict:
    if not API_KEY:
        print("ERROR: MONDAY_API_KEY is not set.")
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


# ── Data Helpers (shared with migrate.py) ─────────────────────────────────────
def to_iso_date(date_str: str) -> str | None:
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str.strip(), "%m/%d/%Y").strftime("%Y-%m-%d")
    except ValueError:
        return None


def load_csv(filepath: str) -> list[dict]:
    with open(filepath, newline="", encoding="utf-8") as f:
        return [{k.strip(): v.strip() for k, v in row.items()}
                for row in csv.DictReader(f)]


def extract_engagements(rows: list[dict]) -> dict[str, dict]:
    seen: dict[str, dict] = {}
    for row in rows:
        eid = row["engagement_id"]
        if eid not in seen:
            raw = row["engagement_status"].lower().strip()
            seen[eid] = {
                "engagement_id": eid,
                "name":          row["engagement_name"],
                "client":        row["client"],
                "lead":          row["engagement_lead"],
                "start_date":    to_iso_date(row["engagement_start"]),
                "end_date":      to_iso_date(row["engagement_end"]),
                "budget":        row["budget"],
                "status":        ENGAGEMENT_STATUS_MAP.get(raw, row["engagement_status"]),
            }
    return seen


def extract_deliverables(rows: list[dict]) -> list[dict]:
    result = []
    for row in rows:
        result.append({
            "deliverable_id": row["deliverable_id"],
            "name":           row["deliverable_name"],
            "engagement_id":  row["engagement_id"],
            "assignee":       row["assignee"],
            "due_date":       to_iso_date(row["due_date"]),
            "priority":       PRIORITY_MAP.get(row["priority"].lower().strip(), row["priority"]),
            "status":         DELIVERABLE_STATUS_MAP.get(
                                  row["deliverable_status"].lower().strip(),
                                  row["deliverable_status"]),
            "hours":          row["hours_estimated"],
        })
    return result


# ── monday.com Query ──────────────────────────────────────────────────────────
def fetch_board_items(board_id: str) -> list[dict]:
    """
    Retrieve all items from a board with their column values.
    Uses cursor-based pagination to handle boards with many items.
    """
    items: list[dict] = []
    cursor = None

    while True:
        if cursor:
            variables = {"boardId": board_id, "cursor": cursor}
            query = """
                query($boardId: ID!, $cursor: String!) {
                  boards(ids: [$boardId]) {
                    items_page(limit: 200, cursor: $cursor) {
                      cursor
                      items { id name column_values { id text value } }
                    }
                  }
                }
            """
        else:
            variables = {"boardId": board_id}
            query = """
                query($boardId: ID!) {
                  boards(ids: [$boardId]) {
                    items_page(limit: 200) {
                      cursor
                      items { id name column_values { id text value } }
                    }
                  }
                }
            """

        data = graphql(query, variables)
        page = data["boards"][0]["items_page"]
        items.extend(page["items"])
        cursor = page.get("cursor")
        if not cursor:
            break

    return items


def col_text(item: dict, col_id: str) -> str:
    """Extract the human-readable text value for a column on a monday item."""
    for cv in item["column_values"]:
        if cv["id"] == col_id:
            return cv.get("text", "") or ""
    return ""


# ── Validation Checks ─────────────────────────────────────────────────────────
def run_check(report: dict, category: str, passed: bool, message: str,
              detail: str = "") -> None:
    entry = {"check": message, "passed": passed, "detail": detail}
    report.setdefault(category, []).append(entry)


def validate(csv_path: str, results_path: str) -> None:
    print("\n" + "═" * 62)
    print("  NEXUS CONSULTING GROUP  —  Migration Validation Report")
    print("═" * 62)

    # ── Load inputs ───────────────────────────────────────────────────────────
    print("\n[1/4]  Loading source data …")
    rows            = load_csv(csv_path)
    src_engagements = extract_engagements(rows)
    src_deliverables = extract_deliverables(rows)
    print(f"  Source: {len(src_engagements)} engagements, {len(src_deliverables)} deliverables")

    with open(results_path) as f:
        results = json.load(f)
    eng_board_id = results["eng_board_id"]
    del_board_id = results["del_board_id"]
    eng_cols     = results["eng_cols"]
    del_cols     = results["del_cols"]

    # ── Fetch migrated data ───────────────────────────────────────────────────
    print("\n[2/4]  Querying monday.com boards …")
    monday_engagements  = fetch_board_items(eng_board_id)
    monday_deliverables = fetch_board_items(del_board_id)
    print(f"  monday.com: {len(monday_engagements)} engagements, "
          f"{len(monday_deliverables)} deliverables")

    # Index monday items by Engagement ID / Deliverable ID for lookup
    monday_eng_by_id  = {
        col_text(item, eng_cols["engagement_id"]): item
        for item in monday_engagements
    }
    monday_del_by_id  = {
        col_text(item, del_cols["deliverable_id"]): item
        for item in monday_deliverables
    }

    report: dict[str, list] = {}
    issues: list[str] = []

    # ── Check 1: Record counts ────────────────────────────────────────────────
    print("\n[3/4]  Running validation checks …")
    print("\n  ── Count Checks ──")

    eng_match = len(monday_engagements) == len(src_engagements)
    run_check(report, "counts", eng_match,
              "Engagement count matches source",
              f"Source={len(src_engagements)}, monday={len(monday_engagements)}")
    _print_check(eng_match,
                 f"Engagements: {len(src_engagements)} source → "
                 f"{len(monday_engagements)} monday.com")
    if not eng_match:
        issues.append(f"Engagement count mismatch: {len(src_engagements)} vs {len(monday_engagements)}")

    del_match = len(monday_deliverables) == len(src_deliverables)
    run_check(report, "counts", del_match,
              "Deliverable count matches source",
              f"Source={len(src_deliverables)}, monday={len(monday_deliverables)}")
    _print_check(del_match,
                 f"Deliverables: {len(src_deliverables)} source → "
                 f"{len(monday_deliverables)} monday.com")
    if not del_match:
        issues.append(f"Deliverable count mismatch: {len(src_deliverables)} vs {len(monday_deliverables)}")

    # ── Check 2: Engagement field accuracy ───────────────────────────────────
    print("\n  ── Engagement Field Accuracy ──")
    eng_field_errors: list[str] = []
    for eid, src_eng in src_engagements.items():
        mon = monday_eng_by_id.get(eid)
        if not mon:
            eng_field_errors.append(f"{eid}: not found in monday.com")
            continue

        # Name
        if mon["name"] != src_eng["name"]:
            eng_field_errors.append(f"{eid}: name mismatch "
                                    f"(expected '{src_eng['name']}', got '{mon['name']}')")
        # Client
        client_val = col_text(mon, eng_cols["client"])
        if client_val != src_eng["client"]:
            eng_field_errors.append(f"{eid}: client mismatch "
                                    f"(expected '{src_eng['client']}', got '{client_val}')")
        # Budget (stored as number, compare as float)
        budget_val = col_text(mon, eng_cols["budget"])
        try:
            if float(budget_val.replace(",", "")) != float(src_eng["budget"]):
                eng_field_errors.append(f"{eid}: budget mismatch "
                                        f"(expected {src_eng['budget']}, got {budget_val})")
        except (ValueError, AttributeError):
            eng_field_errors.append(f"{eid}: budget could not be parsed (got '{budget_val}')")
        # Status (normalised)
        status_val = col_text(mon, eng_cols["status"])
        if status_val != src_eng["status"]:
            eng_field_errors.append(f"{eid}: status mismatch "
                                    f"(expected '{src_eng['status']}', got '{status_val}')")

    eng_fields_ok = len(eng_field_errors) == 0
    run_check(report, "field_accuracy", eng_fields_ok,
              "All engagement fields match (name, client, budget, status)",
              "; ".join(eng_field_errors) if eng_field_errors else "All match")
    _print_check(eng_fields_ok,
                 f"Engagement fields (name / client / budget / status)",
                 eng_field_errors)
    issues.extend(eng_field_errors)

    # ── Check 3: Deliverable field accuracy ──────────────────────────────────
    print("\n  ── Deliverable Field Accuracy ──")
    del_field_errors: list[str] = []
    for src_del in src_deliverables:
        did = src_del["deliverable_id"]
        mon = monday_del_by_id.get(did)
        if not mon:
            del_field_errors.append(f"{did}: not found in monday.com")
            continue
        # Name
        if mon["name"] != src_del["name"]:
            del_field_errors.append(f"{did}: name mismatch "
                                    f"(expected '{src_del['name']}', got '{mon['name']}')")
        # Status (normalised)
        status_val = col_text(mon, del_cols["status"])
        if status_val != src_del["status"]:
            del_field_errors.append(f"{did}: status mismatch "
                                    f"(expected '{src_del['status']}', got '{status_val}')")
        # Priority
        priority_val = col_text(mon, del_cols["priority"])
        if priority_val != src_del["priority"]:
            del_field_errors.append(f"{did}: priority mismatch "
                                    f"(expected '{src_del['priority']}', got '{priority_val}')")

    del_fields_ok = len(del_field_errors) == 0
    run_check(report, "field_accuracy", del_fields_ok,
              "All deliverable fields match (name, status, priority)",
              "; ".join(del_field_errors) if del_field_errors else "All match")
    _print_check(del_fields_ok,
                 f"Deliverable fields (name / status / priority)",
                 del_field_errors)
    issues.extend(del_field_errors)

    # ── Check 4: No orphaned deliverables ─────────────────────────────────────
    print("\n  ── Relationship Integrity ──")
    orphans: list[str] = []
    for item in monday_deliverables:
        did = col_text(item, del_cols["deliverable_id"])
        eng_link = col_text(item, del_cols["engagement"])
        if not eng_link:
            orphans.append(f"{did} ({item['name']})")

    orphans_ok = len(orphans) == 0
    run_check(report, "relationships", orphans_ok,
              "All deliverables are linked to an engagement",
              f"Orphans: {orphans}" if orphans else "None")
    _print_check(orphans_ok,
                 f"Deliverable → Engagement links ({len(monday_deliverables)} checked)",
                 [f"Orphaned: {o}" for o in orphans])
    issues.extend([f"Orphaned deliverable: {o}" for o in orphans])

    # ── Check 5: Missing required fields ──────────────────────────────────────
    print("\n  ── Data Quality (Missing Fields) ──")

    # Deliverables missing assignee
    missing_assignee = [
        f"{col_text(item, del_cols['deliverable_id'])} ({item['name']})"
        for item in monday_deliverables
        if not col_text(item, del_cols["assignee"])
    ]
    assignee_ok = len(missing_assignee) == 0
    run_check(report, "data_quality", assignee_ok,
              "All deliverables have an assignee",
              f"Missing: {missing_assignee}" if missing_assignee else "All present")
    _print_check(assignee_ok,
                 "Deliverables with assignee",
                 [f"Missing assignee: {x}" for x in missing_assignee])
    issues.extend([f"Deliverable missing assignee: {x}" for x in missing_assignee])

    # Deliverables missing due date
    missing_due_date = [
        f"{col_text(item, del_cols['deliverable_id'])} ({item['name']})"
        for item in monday_deliverables
        if not col_text(item, del_cols["due_date"])
    ]
    due_date_ok = len(missing_due_date) == 0
    run_check(report, "data_quality", due_date_ok,
              "All deliverables have a due date",
              f"Missing: {missing_due_date}" if missing_due_date else "All present")
    _print_check(due_date_ok,
                 "Deliverables with due date",
                 [f"Missing due date: {x}" for x in missing_due_date])
    issues.extend([f"Deliverable missing due date: {x}" for x in missing_due_date])

    # ── Check 6: Status normalisation audit ──────────────────────────────────
    print("\n  ── Status Normalisation Audit ──")
    synonyms_found = [
        (row["engagement_id"], row["engagement_status"])
        for row in rows
        if row["engagement_status"].lower().strip() in
           {"in progress", "done", "working on it", "not started"}
        and row["engagement_id"] in src_engagements
    ]
    # Dedupe by engagement ID (engagement rows are repeated per deliverable)
    seen_ids: set = set()
    unique_synonyms = []
    for eid, raw in synonyms_found:
        if eid not in seen_ids:
            seen_ids.add(eid)
            unique_synonyms.append((eid, raw))

    del_synonyms = [
        (row["deliverable_id"], row["deliverable_status"])
        for row in rows
        if row["deliverable_status"].lower().strip() in
           {"not started", "working on it"}
    ]

    run_check(report, "normalisation", True,
              "Engagement status synonyms detected and normalised",
              str(unique_synonyms))
    run_check(report, "normalisation", True,
              "Deliverable status synonyms detected and normalised",
              str(del_synonyms))

    print(f"  ℹ  Engagement statuses normalised : {len(unique_synonyms)} synonym(s) found")
    for eid, raw in unique_synonyms:
        normalised = ENGAGEMENT_STATUS_MAP[raw.lower().strip()]
        print(f"       {eid}: '{raw}' → '{normalised}'")
    print(f"  ℹ  Deliverable statuses normalised: {len(del_synonyms)} synonym(s) found")
    for did, raw in del_synonyms:
        normalised = DELIVERABLE_STATUS_MAP[raw.lower().strip()]
        print(f"       {did}: '{raw}' → '{normalised}'")

    # ── Final Summary ─────────────────────────────────────────────────────────
    print("\n[4/4]  Saving validation report …")
    all_passed = all(
        entry["passed"]
        for checks in report.values()
        for entry in checks
    )
    total_checks = sum(len(v) for v in report.values())
    passed_checks = sum(
        1 for checks in report.values()
        for entry in checks if entry["passed"]
    )

    report_path = Path("validation_report.json")
    with open(report_path, "w") as f:
        json.dump({
            "summary": {
                "total_checks":  total_checks,
                "passed":        passed_checks,
                "failed":        total_checks - passed_checks,
                "overall_status": "PASSED" if all_passed else "FAILED",
            },
            "source": {
                "engagements":  len(src_engagements),
                "deliverables": len(src_deliverables),
            },
            "monday": {
                "engagements":  len(monday_engagements),
                "deliverables": len(monday_deliverables),
            },
            "issues": issues,
            "checks": report,
        }, f, indent=2)

    print(f"  Report saved to: {report_path.resolve()}")
    print("\n" + "═" * 62)
    status_label = "✅  ALL CHECKS PASSED" if all_passed else "❌  SOME CHECKS FAILED"
    print(f"  {status_label}  ({passed_checks}/{total_checks} checks)")
    if issues:
        print(f"\n  Issues to investigate ({len(issues)}):")
        for issue in issues:
            print(f"    • {issue}")
    print("═" * 62 + "\n")


# ── Print Helper ──────────────────────────────────────────────────────────────
def _print_check(passed: bool, label: str, errors: list[str] | None = None) -> None:
    icon = "✅" if passed else "❌"
    print(f"  {icon}  {label}")
    if errors:
        for e in errors:
            print(f"       ↳ {e}")


if __name__ == "__main__":
    csv_file     = sys.argv[1] if len(sys.argv) > 1 else "data/nexus_smartsheet_export.csv"
    results_file = sys.argv[2] if len(sys.argv) > 2 else "migration_results.json"

    for path in (csv_file, results_file):
        if not Path(path).exists():
            print(f"ERROR: File not found: {path}")
            sys.exit(1)

    validate(csv_file, results_file)
