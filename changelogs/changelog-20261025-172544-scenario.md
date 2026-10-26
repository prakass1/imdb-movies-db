# Schema Migration Changelog: scenario

## Metadata
- **Commit SHA**: `scenario2def`
- **Source Repository**: `movies_db`
- **Target Sync Repository**: `movies_etl`
- **Merge Request Reference**: Pending Sync MR
- **Timestamp**: `2026-10-25 17:25:44 UTC`
- **Author / Trigger**: Scenario-2 Simulation
- **Migration Status**: **✅ NON-BREAKING ADDITIONS**

---

## Executive Summary
A total of **1** schema modification(s) across **1** table(s) were identified.

| Change Category | Count | Impact Level |
|:---|:---:|:---|
| **New Tables** (`NEW_TABLE`) | 1 | Info / Scaffolding Required |
| **Dropped Tables** (`DROP_TABLE`) | 0 | High (Pipeline Decommission) |
| **Renamed Columns** (`RENAME_COLUMN`) | 0 | High (CTAS / CTE Breaking) |
| **Dropped Columns** (`DROP_COLUMN`) | 0 | High (Projection Breaking) |
| **Type Changes** (`TYPE_CHANGE`) | 0 | Medium / High (Cast Mismatch) |
| **Added Columns** (`ADD_COLUMN`) | 0 | Low (Backward Compatible) |
| **Constraint Changes** (`CONSTRAINT_CHANGE`) | 0 | Medium |

---

## Categorized Modifications by Table

### 🆕 New Tables
#### Table: `movie_reviews`
_New table 'movie_reviews' added with 6 columns_

| Column Name | Data Type | Constraints | Description |
|:---|:---|:---|:---|
| `review_id` | `VARCHAR(64)` | PRIMARY KEY | Column 'review_id' (VARCHAR(64)) created with new table 'movie_reviews' |
| `movie_id` | `VARCHAR(255)` | NOT NULL | Column 'movie_id' (VARCHAR(255)) created with new table 'movie_reviews' |
| `reviewer_name` | `VARCHAR(255)` | None | Column 'reviewer_name' (VARCHAR(255)) created with new table 'movie_reviews' |
| `rating` | `DOUBLE PRECISION` | None | Column 'rating' (DOUBLE PRECISION) created with new table 'movie_reviews' |
| `review_text` | `TEXT` | None | Column 'review_text' (TEXT) created with new table 'movie_reviews' |
| `review_date` | `TIMESTAMP` | None | Column 'review_date' (TIMESTAMP) created with new table 'movie_reviews' |

---

## Downstream Impact Analysis

### 1. Landing Zone (`movies_etl` Ingestion)
- **New CDC Stream Required**: Table `movie_reviews` must be provisioned in Landing Zone with initial Full Load (FL).

### 2. Staging Layer (`movies_etl` Transformations & CTEs)
- **Scaffold Staging Model**: Create staging CTE/CTAS model for `movie_reviews` adhering to team SQL standards.

---

## Operational Action Checklist

- [ ] **Step 1: Pipeline Interruption Check** - Pause running Landing ingestion pipelines prior to applying changes.
- [ ] **Step 2: Provision Landing Table** - Initialize landing tables and CDC replication for new tables.
- [ ] **Step 3: Full Load Initialization** - Run initial bulk data load for newly onboarded tables.
- [ ] **Step 4: Staging Merge Request (MR)** - Review and merge staging query updates (CTE mappings & CTAS scaffolds).
- [ ] **Step 5: Pipeline Resumption & End-to-End Test** - Resume ingestion pipelines and execute data validation tests.
