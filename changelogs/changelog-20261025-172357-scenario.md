# Schema Migration Changelog: scenario

## Metadata
- **Commit SHA**: `scenario1abc`
- **Source Repository**: `movies_db`
- **Target Sync Repository**: `movies_etl`
- **Merge Request Reference**: Pending Sync MR
- **Timestamp**: `2026-10-25 17:23:57 UTC`
- **Author / Trigger**: Scenario-1 Simulation
- **Migration Status**: **⚠️ BREAKING CHANGES DETECTED**

---

## Executive Summary
A total of **2** schema modification(s) across **1** table(s) were identified.

| Change Category | Count | Impact Level |
|:---|:---:|:---|
| **New Tables** (`NEW_TABLE`) | 0 | Info / Scaffolding Required |
| **Dropped Tables** (`DROP_TABLE`) | 0 | High (Pipeline Decommission) |
| **Renamed Columns** (`RENAME_COLUMN`) | 1 | High (CTAS / CTE Breaking) |
| **Dropped Columns** (`DROP_COLUMN`) | 1 | High (Projection Breaking) |
| **Type Changes** (`TYPE_CHANGE`) | 0 | Medium / High (Cast Mismatch) |
| **Added Columns** (`ADD_COLUMN`) | 0 | Low (Backward Compatible) |
| **Constraint Changes** (`CONSTRAINT_CHANGE`) | 0 | Medium |

---

## Categorized Modifications by Table

### 🔄 Modified Tables
#### Table: `titles_top_100`

| Column | Operation | Previous State | New State | Details |
|:---|:---|:---|:---|:---|
| `vote_count` | `RENAME_COLUMN` | `No_of_Votes` (BIGINT) | `BIGINT` | Renamed column 'No_of_Votes' -> 'vote_count' |
| `Poster_Link` | `DROP_COLUMN` | `TEXT` | - | Dropped column 'Poster_Link' (TEXT) |

---

## Downstream Impact Analysis

### 1. Landing Zone (`movies_etl` Ingestion)
- **Table Drop & CDC Reload Needed**: Table `titles_top_100` has dropped/renamed columns (vote_count, Poster_Link). Active pipelines must be stopped prior to FL, landing table dropped, and CDC events reloaded.

### 2. Staging Layer (`movies_etl` Transformations & CTEs)
- **Update CTE Column Projections**: Table `titles_top_100` column renames (`No_of_Votes` -> `vote_count`) require updating CTE SELECT expressions and downstream alias mappings.
- **Remove Dropped Columns**: Remove references to dropped column(s) `Poster_Link` from `titles_top_100` staging models.

---

## Operational Action Checklist

- [ ] **Step 1: Pipeline Interruption Check** - Pause running Landing ingestion pipelines prior to applying changes.
- [ ] **Step 2: DROP Affected Landing Tables** - Drop and recreate landing tables with breaking modifications.
- [ ] **Step 3: CDC Reload & Backfill** - Reload CDC events and trigger Full Load (FL) for affected landing tables.
- [ ] **Step 4: Staging Merge Request (MR)** - Review and merge staging query updates (CTE mappings & CTAS scaffolds).
- [ ] **Step 5: Pipeline Resumption & End-to-End Test** - Resume ingestion pipelines and execute data validation tests.
