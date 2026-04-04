# Assumptions & Design Decisions

## Board Structure

**Why two boards instead of one?**

The discovery call made it clear that Nexus had a fundamental data model problem: all engagement-level data was repeated on every deliverable row. Derek spent two hours manually deduplicating in Excel to pull a budget report.

The solution: separate **Engagements** and **Deliverables** boards, linked via a `board_relation` (connect boards) column. This gives Derek a clean engagement list with no duplicates, and Priya can drill into deliverables from any engagement.

## Status Normalisation

The following synonyms were found in the source data and collapsed into canonical values during migration:

### Engagement Status
| Raw Value (Smartsheet) | Normalised Value (monday.com) |
|---|---|
| `In Progress` | `Active` |
| `Active` | `Active` |
| `Complete` | `Complete` |
| `On Hold` | `On Hold` |
| `Not Started` | `Not Started` |

> `In Progress` and `Active` were confirmed as synonyms by Priya in the discovery call.

### Deliverable Status
| Raw Value (Smartsheet) | Normalised Value (monday.com) |
|---|---|
| `To Do` | `To Do` |
| `Not Started` | `To Do` |
| `In Progress` | `In Progress` |
| `Working on it` | `In Progress` |
| `In Review` | `In Review` |
| `Done` | `Done` |

> Derek and Priya both flagged these inconsistencies explicitly during the call.

## Column Type Choices

| Field | monday.com Column Type | Rationale |
|---|---|---|
| Engagement Lead | `text` | Team members don't have monday.com accounts yet; text avoids broken people links |
| Assignee | `text` | Same reason as above |
| Engagement Status | `color` (status) | Custom labels; visually distinct from Deliverable Status |
| Deliverable Status | `color` (status) | Separate label set from Engagement Status per Priya's request |
| Priority | `color` (status) | Allows color coding (red/yellow/green) for visual priority at a glance |
| Budget | `numbers` | Enables sum/aggregate formulas in dashboards |
| Hours Estimated | `numbers` | Same — supports workload reporting |
| Engagement (link) | `board_relation` | Native cross-board link; powers the Deliverables dashboard widget |

## What Was Intentionally Left Out

- **Engagement ID / Deliverable ID columns**: kept as reference/audit fields. Not surfaced prominently in the dashboard since end users care about names, not IDs.
- **Engagement Lead email**: not in the source data; left as a manual enrichment task for the Nexus team.

## Data Quality Notes from Source

- `DEL-005`: status was `Not Started` (normalised to `To Do`)
- `DEL-013`: status was `Not Started` (normalised to `To Do`)
- `DEL-003`, `DEL-012`: status was `Working on it` (normalised to `In Progress`)
- `ENG-001`, `ENG-003`: engagement status was `In Progress` (normalised to `Active`)

All 27 deliverables have an assignee and a due date in the source data. Validation confirms no orphaned records.
