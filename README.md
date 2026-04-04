# Nexus Consulting Group — Smartsheet → monday.com Migration

> Demo project for the monday.com Technical Consultant interview assignment.
>
> Covers: requirements analysis · data migration · migration validation · custom dashboard (monday vibe)

---

## Overview

Nexus Consulting Group is migrating from a flat Smartsheet export (one row per deliverable, engagement data repeated) to a properly structured monday.com workspace with two connected boards.

This repo contains:

| File | Purpose |
|---|---|
| `data/nexus_smartsheet_export.csv` | Source data — raw Smartsheet CSV export |
| `src/migrate.py` | Migration script — parses, transforms, and loads data into monday.com |
| `src/validate.py` | Validation script — queries monday.com and produces a verification report |
| `docs/assumptions.md` | Design decisions and data transformation rationale |

---

## Quick Start

### 1. Prerequisites

- Python 3.11+
- A [monday.com trial account](https://monday.com/lang/en/sign-up/)
- Your monday.com API key ([Developer → API](https://developer.monday.com/))

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure credentials

```bash
cp .env.example .env
# Edit .env and paste your monday.com API key
```

Your `.env` should look like:

```
MONDAY_API_KEY=eyJhbGciOiJIUzI1NiJ9...
```

### 4. Run the migration

```bash
python src/migrate.py
```

This will:
- Create two boards: **Nexus Engagements** and **Nexus Deliverables**
- Configure custom status labels on both boards
- Import 6 engagements (deduplicated from 27 source rows)
- Import 27 deliverables, each linked to its parent engagement
- Save `migration_results.json` (required by the validation script)

### 5. Run the validation

```bash
python src/validate.py
```

This will:
- Query both boards from monday.com via the GraphQL API
- Compare counts, field values, and relationships against the source CSV
- Check for orphaned deliverables and missing required fields
- Audit status normalisation (synonym → canonical value)
- Print a summary report and save `validation_report.json`

---

## Board Design

### Nexus Engagements

| Column | Type | Notes |
|---|---|---|
| Name | Item name | Engagement name |
| Engagement ID | Text | ENG-001 … ENG-006 |
| Client | Text | |
| Engagement Lead | Text | Text (not People) — no monday users yet |
| Start Date | Date | |
| End Date | Date | |
| Budget ($) | Numbers | Supports sum/aggregate in dashboards |
| Engagement Status | Status | `Active` · `On Hold` · `Not Started` · `Complete` |

### Nexus Deliverables

| Column | Type | Notes |
|---|---|---|
| Name | Item name | Deliverable name |
| Deliverable ID | Text | DEL-001 … DEL-027 |
| Assignee | Text | |
| Due Date | Date | |
| Priority | Status | `High` · `Medium` · `Low` |
| Deliverable Status | Status | `To Do` · `In Progress` · `In Review` · `Done` |
| Hours Estimated | Numbers | |
| Engagement | Board Relation | Links to **Nexus Engagements** |

---

## Status Normalisation

The source data contained several inconsistent status values. These were normalised during migration:

**Engagement Status**
- `In Progress` → **Active** *(synonym confirmed in discovery call)*
- `Done` → **Complete**

**Deliverable Status**
- `Not Started` → **To Do**
- `Working on it` → **In Progress** *(synonym confirmed in discovery call)*

See `docs/assumptions.md` for the full decision log.

---

## AI-Assisted Development

This project was built with AI assistance throughout. Key examples:

- **Requirements extraction**: Used Claude to parse the discovery call transcript and identify the data model structure, status synonyms, and column type decisions
- **GraphQL API patterns**: Used Claude to draft the mutation structure for `create_board`, `create_column`, and `create_item` — then iterated based on API errors
- **Validation logic**: Prompted Claude with the specific concerns Derek and Priya raised (count checks, orphan checks, missing field checks) and asked it to generate a corresponding validation script
- **Edge case handling**: Asked Claude "what could go wrong in a CSV migration like this?" to identify additional validation checks (budget float comparison, date format conversion, pagination)

**Where AI was most effective**: drafting boilerplate API code and translating the discovery call requirements into specific technical checks.

**Where human judgment was needed**: deciding *which* status values were synonyms (required reading the transcript carefully), and choosing `text` over `people` columns for assignees (a business context decision AI couldn't make alone).

---

## Resources

- [monday.com API Reference](https://developer.monday.com/api-reference/)
- [monday.com API Playground](https://developer.monday.com/api-reference/docs/api-playground)
- [monday vibe](https://monday.com/vibe)
- [monday.com Academy](https://monday.com/academy)
