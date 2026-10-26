# Implementation Plan: GitHub Bot for Cross-Repo Schema Synchronization (`movies_db` -> `movies_etl`) & Metflix Solution Brief

## Overview

Build an automated schema synchronization bot using Bob Shell and GitHub MCP, alongside an executive-ready Solution Brief document showcasing Bob's AI Architect capabilities for client presentations (Metflix).

When database changes (DDL modifications, column renames, new tables, or dropped columns) are committed or merged in `movies_db`, the bot:
1. Detects schema changes and writes structured changelogs with commit and MR references under the `changelogs/` folder (and execution summaries under `knowledge_brain/`).
2. Uses GitHub MCP to interact with the GitHub Web UI (issues and merge requests / pull requests).
3. Generates actionable Landing instructions (pipeline stop, DROP tables, CDC reload) as GitHub Issues on `movies_etl`.
4. Generates proposed Merge Requests on `movies_etl` adapting CTAS/CTE staging queries for renamed/removed columns and scaffolding new tables according to team coding standards.
5. Simulates end-to-end scenarios (column rename/drop and new table addition) to validate both Issue and MR generation.
6. Delivers an executive-ready Architecture Brief highlighting Bob's autonomous agent capabilities, MCP interoperability, and enterprise ROI.

---

## Simulated Scenarios

### Scenario 1: Column Rename & Drop in Existing Table (`titles_top_100`)
- **Trigger Change**: Column `No_of_Votes` renamed to `vote_count` and column `Poster_Link` dropped in `titles_top_100`.
- **Expected Bot Output**:
  - **Changelog**: Generated under `changelogs/changelog-<timestamp>-<commit_sha>.md` documenting the column rename and drop with commit and MR references.
  - **Landing Issue on `movies_etl`**:
    - Title: `[Landing Action Required] Schema Migration: titles_top_100 modified in movies_db#<commit_sha>`
    - Instructions: (1) Stop active pipelines prior to Full Load (FL); (2) Drop table `titles_top_100`; (3) Reload CDC data events for `titles_top_100`.
  - **Staging Draft Merge Request on `movies_etl`**:
    - Branch: `schema-sync/titles-top-100-<commit_sha>`
    - Title: `[Staging Sync] Adapt CTAS/CTE queries for titles_top_100 column changes`
    - Modified Files: Staging CTE queries updated to alias/select `vote_count` and remove references to `Poster_Link`.

### Scenario 2: New Table Addition (`movie_reviews`)
- **Trigger Change**: New table `movie_reviews` (columns: `review_id`, `movie_id`, `reviewer_name`, `rating`, `review_text`, `review_date`) added to `db/movies.sql`.
- **Expected Bot Output**:
  - **Changelog**: Generated under `changelogs/changelog-<timestamp>-<commit_sha>.md` documenting the new table schema and commit reference.
  - **Landing Issue on `movies_etl`**:
    - Title: `[Landing Action Required] New Table Onboarding: movie_reviews from movies_db#<commit_sha>`
    - Instructions: (1) Initialize landing CDC stream; (2) Trigger initial Full Load.
  - **Staging Draft Merge Request on `movies_etl`**:
    - Branch: `schema-sync/new-table-movie-reviews-<commit_sha>`
    - Title: `[Staging Sync] Scaffold staging model and CTAS for movie_reviews`
    - Created Files: Staging model and CTE query following team SQL coding standards.

---

## Sub-Tasks

### Sub-Task 1: GitHub MCP Configuration & Environment Setup
- **Intent**: Configure GitHub MCP with necessary tokens and permissions so Bob Shell can interact with GitHub repositories, issues, branches, and pull requests via the GitHub Web UI.
- **Expected Outcomes**:
  - MCP configuration verified with GitHub integration capabilities.
  - Token and repository target configuration established for `movies_db` and `movies_etl`.
- **Todo List**:
  - [x] Define MCP configuration for GitHub in Bob's MCP settings or environment.
  - [x] Verify GitHub authentication and repository access permissions for both `movies_db` and `movies_etl`.
  - [x] Test connectivity and verification tools for reading repos, creating issues, and opening PRs.
- **Relevant Context**: `.bob/` configuration, GitHub MCP server configuration.
- **Status**: `[x] completed`

---

### Sub-Task 2: Schema Change Detection & `changelogs/` Generator
- **Intent**: Build a schema diff analyzer in `movies_db` that detects DDL changes (new tables, column additions, renames, drops, and type modifications) in `db/movies.sql` against git history or base commits, and writes structured changelogs to `changelogs/changelog-<timestamp>-<commit_sha>.md`.
- **Expected Outcomes**:
  - Schema diff engine detecting specific change categories (New Table, Column Rename, Column Drop, Column Add, Type Change).
  - Automated changelog file creation under `changelogs/` referencing commit SHA and MR/PR metadata.
  - Pytest unit tests verifying schema change parsing.
- **Todo List**:
  - [x] Implement SQL schema parser/differ comparing current `db/movies.sql` against prior commit/tag.
  - [x] Categorize changes into actionable schema event types (table level and column level).
  - [x] Generate markdown changelog in `changelogs/changelog-<timestamp>-<commit_sha>.md` with commit reference and affected tables/columns.
  - [x] Add unit tests with pytest covering schema change detection scenarios.
- **Relevant Context**: [`db/movies.sql`](db/movies.sql), `changelogs/`.
- **Status**: `[x] completed`

---

### Sub-Task 3: Landing Zone Action Dispatcher (GitHub Issue Generator)
- **Intent**: Generate high-priority operational GitHub Issues on `movies_etl` with clear execution instructions for the Data Engineering team to manage Landing Zone tables upon release.
- **Expected Outcomes**:
  - Formatted GitHub Issue created on `movies_etl` visible in the GitHub Web UI.
  - Issue contains standardized step-by-step landing instructions:
    1. Stop running pipelines prior to Full Load (FL).
    2. DROP affected tables identified in the release changelog.
    3. Reload CDC data events for affected tables.
- **Todo List**:
  - [x] Create standardized Landing Issue template incorporating Part-1 operational requirements.
  - [x] Build Bob Shell / MCP orchestration step to publish the issue to `movies_etl`.
  - [x] Verify issue formatting, labels, and traceability linking back to `movies_db` commit/MR.
- **Relevant Context**: GitHub MCP `create_issue` tool, `changelogs/` changelog.
- **Status**: `[x] completed`

---

### Sub-Task 4: Staging & New Table Adapter (Draft Merge Request Generator)
- **Intent**: Automate the creation of proposed Merge Requests / Pull Requests on `movies_etl` to update Staging CTAS/CTE queries and scaffold new Landing/Staging tables matching coding standards.
- **Expected Outcomes**:
  - Automated branch and PR created on `movies_etl` visible in GitHub Web UI.
  - For Column Changes/Drops: updates CTEs and CTAS staging SQL files to reflect new/renamed column mappings.
  - For New Tables: generates scaffolded ingestion and staging SQL matching team conventions.
- **Todo List**:
  - [x] Define transformation rules for adapting CTAS and CTE SQL statements when columns are renamed or dropped.
  - [x] Define scaffolding rules for newly added tables adhering to `movies_etl` coding standards.
  - [x] Implement Bob Shell / MCP workflow to create branch, commit modified SQL files, and open a Draft PR on `movies_etl`.
  - [x] Link the Draft PR to the corresponding Landing Issue and `movies_db` commit reference.
- **Relevant Context**: GitHub MCP `create_pull_request`, `movies_etl` staging queries and schemas.
- **Status**: `[x] completed`

---

### Sub-Task 5: End-to-End Orchestrator Script & Local Bob Shell Trigger
- **Intent**: Provide a single command / shell script to trigger the full sync workflow locally (or via git hook), coordinating changelog generation, issue creation, and PR generation with end-to-end scenario simulations.
- **Expected Outcomes**:
  - Unified CLI command (e.g. `python sync_bot.py` or Bob Shell script) that orchestrates Sub-Tasks 1 through 4.
  - Execution of simulated Scenario 1 (column rename/drop) and Scenario 2 (new table addition) demonstrating generated changelogs, Issues, and MRs.
  - Comprehensive pytest test suite validating the entire sync flow with mocked/dry-run GitHub MCP calls.
  - Execution summary recorded under `knowledge_brain/`.
- **Todo List**:
  - [x] Implement master orchestrator tying together schema detection, changelog recording, and GitHub MCP calls.
  - [x] Add dry-run / simulation mode for Scenario 1 and Scenario 2.
  - [x] Add test suite in `tests/` and verify with pytest.
  - [x] Record execution summary in `knowledge_brain/`.
- **Relevant Context**: [`movies_ingestion.py`](movies_ingestion.py), [`tests/`](tests/), [`github_bot/orchestrator.py`](github_bot/orchestrator.py), [`sync_bot.py`](sync_bot.py), [`scripts/bob_sync_trigger.sh`](scripts/bob_sync_trigger.sh).
- **Deliverables**:
  - `github_bot/orchestrator.py` — SyncOrchestrator class with `run()`, `run_from_files()`, `simulate_scenario_1()`, `simulate_scenario_2()`
  - `sync_bot.py` — CLI entry point with `--dry-run`, `--simulate-scenario-1`, `--simulate-scenario-2`, `--base-ref`, `--prior-sql`, `--commit-sha`, `--mr-ref`, `--verbose`, `--quiet`
  - `scripts/bob_sync_trigger.sh` — shell trigger script for Bob Shell / git hooks
  - `tests/test_orchestrator.py` — 37 integration tests (all passing)
  - `changelogs/changelog-*-a3f8c12e.md` — Scenario 1 sample changelog
  - `changelogs/changelog-*-b7e2d45f.md` — Scenario 2 sample changelog
- **Status**: `[x] completed`

---

### Sub-Task 6: AI Architect & Bob Capabilities Pitch Document (Metflix Presentation)
- **Intent**: Author a comprehensive, executive-facing Solution Brief and sales pitch document highlighting Bob's AI Architect capabilities and Model Context Protocol (MCP) strengths.
- **Expected Outcomes**:
  - High-impact pitch document created at `docs/bob-metflix-solution-brief.md`.
  - Clear breakdown of:
    - The Enterprise Problem: Schema drift, silent pipeline failures, and cross-team coordination bottlenecks in media streaming data architectures.
    - Bob's AI Architect Paradigm: Multi-repository context comprehension, AST/SQL schema reasoning, and automated code generation.
    - Model Context Protocol (MCP) in Action: Seamless tool orchestration across GitHub, databases, and CI/CD pipelines.
    - Quantifiable Business ROI: Reduction in pipeline downtime, elimination of manual MR drafting, and faster time-to-market for data products.
- **Todo List**:
  - [x] Structure the solution brief tailored for media/streaming data architectures (Metflix).
  - [x] Detail Bob's architectural differentiation (deterministic planning, mode specialization, MCP extensibility).
  - [x] Provide before/after workflow comparisons and operational metrics.
  - [x] Format with clear executive summaries and architectural visuals.
- **Relevant Context**: `docs/bob-metflix-solution-brief.md`.
- **Status**: `[x] completed`
