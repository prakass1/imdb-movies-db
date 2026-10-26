"""
github_bot.landing_dispatcher
------------------------------
Generates and publishes actionable GitHub Issues on `movies_etl` for the
Data Engineering team to execute Landing Zone migrations when schema changes
are detected in `movies_db`.

Two scenarios are handled:
  Scenario 1 – Modified tables (column renames, drops, type changes, dropped
                tables): full Part-1 pipeline stop / DROP / CDC reload flow.
  Scenario 2 – New tables: CDC stream initialization and initial Full Load.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from github_bot.client import GitHubClient
from github_bot.config import GitHubConfig
from github_bot.schema_differ import ChangeCategory, SchemaDiffResult, TableDiff

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Labels applied to every Landing Issue
# ---------------------------------------------------------------------------
_BASE_LABELS: list[str] = ["landing-action", "schema-migration", "p1-high"]
_BREAKING_LABEL = "breaking-change"


@dataclass
class LandingIssueResult:
    """Return value from :meth:`LandingZoneDispatcher.dispatch`."""

    title: str
    body: str
    labels: list[str]
    issue_number: int | None = None
    issue_url: str | None = None
    dry_run: bool = False


@dataclass
class DispatchContext:
    """Metadata threaded through every issue to ensure full traceability."""

    commit_sha: str
    source_repo: str = "movies_db"
    target_repo: str = "movies_etl"
    mr_reference: str | None = None
    changelog_path: str | None = None
    owner: str | None = None

    @property
    def short_sha(self) -> str:
        return self.commit_sha[:8] if len(self.commit_sha) >= 8 else self.commit_sha

    def commit_url(self) -> str:
        owner = self.owner or "unknown-owner"
        return f"https://github.com/{owner}/{self.source_repo}/commit/{self.commit_sha}"


class LandingIssueFormatter:
    """Builds formatted GitHub Issue titles and bodies from :class:`SchemaDiffResult`."""

    # ------------------------------------------------------------------
    # Public entry points
    # ------------------------------------------------------------------

    def format_modified_tables_issue(
        self,
        diff_result: SchemaDiffResult,
        ctx: DispatchContext,
    ) -> tuple[str, str, list[str]]:
        """
        Return ``(title, body, labels)`` for tables that were modified or
        dropped (Scenario 1: rename / drop).
        """
        affected = self._collect_modified_and_dropped(diff_result)
        table_list = ", ".join(t.table_name for t in affected) if affected else "multiple tables"

        title = (
            f"[Landing Action Required] Schema Migration: {table_list} "
            f"modified in {ctx.source_repo}#{ctx.short_sha}"
        )
        labels = list(_BASE_LABELS)
        if diff_result.is_breaking:
            labels.append(_BREAKING_LABEL)

        body = self._build_modified_body(affected, diff_result, ctx)
        return title, body, labels

    def format_new_tables_issue(
        self,
        diff_result: SchemaDiffResult,
        ctx: DispatchContext,
    ) -> tuple[str, str, list[str]]:
        """
        Return ``(title, body, labels)`` for newly introduced tables
        (Scenario 2: new table onboarding).
        """
        new_tables = diff_result.tables_added
        table_list = ", ".join(t.table_name for t in new_tables) if new_tables else "new tables"

        title = (
            f"[Landing Action Required] New Table Onboarding: {table_list} "
            f"from {ctx.source_repo}#{ctx.short_sha}"
        )
        labels = list(_BASE_LABELS)
        body = self._build_new_tables_body(new_tables, diff_result, ctx)
        return title, body, labels

    def format_combined_issue(
        self,
        diff_result: SchemaDiffResult,
        ctx: DispatchContext,
    ) -> tuple[str, str, list[str]]:
        """
        Return a single combined issue when a release contains both new tables
        and modified/dropped tables.
        """
        title = (
            f"[Landing Action Required] Schema Release: "
            f"{ctx.source_repo}#{ctx.short_sha}"
        )
        labels = list(_BASE_LABELS)
        if diff_result.is_breaking:
            labels.append(_BREAKING_LABEL)

        sections: list[str] = []
        modified_and_dropped = self._collect_modified_and_dropped(diff_result)
        if modified_and_dropped:
            sections.append(self._build_modified_body(modified_and_dropped, diff_result, ctx))
        if diff_result.tables_added:
            sections.append(self._build_new_tables_body(diff_result.tables_added, diff_result, ctx))

        body = "\n\n---\n\n".join(sections)
        return title, body, labels

    # ------------------------------------------------------------------
    # Body builders
    # ------------------------------------------------------------------

    def _build_modified_body(
        self,
        affected: list[TableDiff],
        diff_result: SchemaDiffResult,
        ctx: DispatchContext,
    ) -> str:
        table_names = [t.table_name for t in affected]
        drop_statements = "\n".join(f"  DROP TABLE IF EXISTS {n};" for n in table_names)
        reload_list = "\n".join(f"  - `{n}`" for n in table_names)
        column_change_detail = self._format_column_changes(affected)

        return f"""## Part-1 Landing Zone: Mandatory Operational Steps

> **Priority**: P1-HIGH · Coordinate with the pipeline team before executing.

### Step 1 — Stop All Active Pipelines (before Full Load)

Halt any currently running ingestion pipelines for the affected tables **before** triggering the Full Load (FL).  
Do **not** proceed to the DROP step until all CDC and batch jobs have fully stopped.

Affected tables:
{reload_list}

### Step 2 — DROP Affected Tables

Execute the following statements against the Landing Zone database:

```sql
{drop_statements}
```

> Verify each table has been dropped before continuing.

### Step 3 — Reload CDC Data Events

Reinitialize the Change Data Capture (CDC) stream and trigger a Fresh Full Load for each affected table:

{reload_list}

CDC reload ensures all downstream Staging models are rebuilt from a clean baseline.

---

## Schema Change Details
{column_change_detail}

---

## Traceability

| Field | Value |
|---|---|
| Source Repository | `{ctx.source_repo}` |
| Commit SHA | [`{ctx.short_sha}`]({ctx.commit_url()}) |
{self._mr_row(ctx)}{self._changelog_row(ctx)}
| Target Repository | `{ctx.target_repo}` |
"""

    def _build_new_tables_body(
        self,
        new_tables: list[TableDiff],
        diff_result: SchemaDiffResult,
        ctx: DispatchContext,
    ) -> str:
        table_names = [t.table_name for t in new_tables]
        table_list = "\n".join(f"  - `{n}`" for n in table_names)
        schema_detail = self._format_new_table_schemas(new_tables)

        return f"""## Part-2 Landing Zone: New Table Onboarding

> **Priority**: P1-HIGH · Coordinate with the pipeline team before executing.

### Step 1 — Initialize Landing CDC Stream

Create and enable a new CDC stream for each new table in the Landing Zone:

{table_list}

Verify CDC connectivity and replication lag before proceeding to the initial load.

### Step 2 — Trigger Initial Full Load (FL)

Initiate an initial Full Load for each new table to seed the Landing Zone with source data:

{table_list}

Monitor load progress and validate row counts against source before marking complete.

---

## New Table Schemas
{schema_detail}

---

## Traceability

| Field | Value |
|---|---|
| Source Repository | `{ctx.source_repo}` |
| Commit SHA | [`{ctx.short_sha}`]({ctx.commit_url()}) |
{self._mr_row(ctx)}{self._changelog_row(ctx)}
| Target Repository | `{ctx.target_repo}` |
"""

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _collect_modified_and_dropped(diff_result: SchemaDiffResult) -> list[TableDiff]:
        return list(diff_result.tables_modified) + list(diff_result.tables_dropped)

    @staticmethod
    def _format_column_changes(affected: list[TableDiff]) -> str:
        lines: list[str] = []
        for td in affected:
            category_label = "DROPPED" if td.change_category == ChangeCategory.DROP_TABLE else "MODIFIED"
            lines.append(f"\n### `{td.table_name}` ({category_label})")
            if td.column_diffs:
                for cd in td.column_diffs:
                    if cd.change_category == ChangeCategory.RENAME_COLUMN:
                        lines.append(f"- **Rename**: `{cd.old_column_name}` → `{cd.column_name}`")
                    elif cd.change_category == ChangeCategory.DROP_COLUMN:
                        lines.append(f"- **Drop column**: `{cd.column_name}`")
                    elif cd.change_category == ChangeCategory.ADD_COLUMN:
                        lines.append(f"- **Add column**: `{cd.column_name}` ({cd.new_type})")
                    elif cd.change_category == ChangeCategory.TYPE_CHANGE:
                        lines.append(f"- **Type change**: `{cd.column_name}` `{cd.old_type}` → `{cd.new_type}`")
                    else:
                        lines.append(f"- **{cd.change_category.value}**: `{cd.column_name}` — {cd.details}")
            elif td.details:
                lines.append(td.details)
        return "\n".join(lines) if lines else "_No column-level detail available._"

    @staticmethod
    def _format_new_table_schemas(new_tables: list[TableDiff]) -> str:
        lines: list[str] = []
        for td in new_tables:
            lines.append(f"\n### `{td.table_name}`")
            schema = td.new_table
            if schema and schema.columns:
                lines.append("| Column | Type | Nullable | PK |")
                lines.append("|---|---|---|---|")
                for col_name, col in schema.columns.items():
                    pk = "✓" if col.primary_key else ""
                    nullable = "✓" if col.nullable else ""
                    lines.append(f"| `{col_name}` | `{col.data_type}` | {nullable} | {pk} |")
            elif td.details:
                lines.append(td.details)
            else:
                lines.append("_Schema details not available._")
        return "\n".join(lines) if lines else "_No new table schema detail available._"

    @staticmethod
    def _mr_row(ctx: DispatchContext) -> str:
        if ctx.mr_reference:
            return f"| MR / PR Reference | {ctx.mr_reference} |\n"
        return ""

    @staticmethod
    def _changelog_row(ctx: DispatchContext) -> str:
        if ctx.changelog_path:
            return f"| Changelog | `{ctx.changelog_path}` |\n"
        return ""


class LandingZoneDispatcher:
    """
    Orchestrates issue formatting and publication to the `movies_etl` repository.

    Usage::

        dispatcher = LandingZoneDispatcher(client=GitHubClient(...))
        results = dispatcher.dispatch(diff_result, ctx)
    """

    def __init__(
        self,
        client: GitHubClient | None = None,
        config: GitHubConfig | None = None,
        dry_run: bool = False,
    ) -> None:
        self._config = config or GitHubConfig.from_env()
        self._client = client or GitHubClient(config=self._config, dry_run=dry_run)
        self._dry_run = dry_run
        self._formatter = LandingIssueFormatter()

    # ------------------------------------------------------------------
    # Main dispatch entry point
    # ------------------------------------------------------------------

    def dispatch(
        self,
        diff_result: SchemaDiffResult,
        ctx: DispatchContext,
    ) -> list[LandingIssueResult]:
        """
        Build and publish Landing Zone GitHub Issues based on *diff_result*.

        Returns a list of :class:`LandingIssueResult` — one per issue created.
        Issues are always published to *ctx.target_repo* (default: ``movies_etl``).
        """
        if not diff_result.has_changes:
            logger.info("No schema changes detected; skipping Landing Zone dispatch.")
            return []

        results: list[LandingIssueResult] = []

        has_modified = bool(diff_result.tables_modified or diff_result.tables_dropped)
        has_new = bool(diff_result.tables_added)

        if has_modified and has_new:
            # Single combined issue
            title, body, labels = self._formatter.format_combined_issue(diff_result, ctx)
            results.append(self._publish(ctx, title, body, labels))
        else:
            if has_modified:
                title, body, labels = self._formatter.format_modified_tables_issue(diff_result, ctx)
                results.append(self._publish(ctx, title, body, labels))
            if has_new:
                title, body, labels = self._formatter.format_new_tables_issue(diff_result, ctx)
                results.append(self._publish(ctx, title, body, labels))

        return results

    def _publish(
        self,
        ctx: DispatchContext,
        title: str,
        body: str,
        labels: list[str],
    ) -> LandingIssueResult:
        """Publish a single issue to GitHub (or simulate in dry-run mode)."""
        repo = ctx.target_repo or self._config.movies_etl_repo
        owner = ctx.owner or self._config.github_owner or None

        logger.info(
            "%s Landing Issue: %s → %s/%s",
            "[DRY RUN]" if self._dry_run else "Publishing",
            title,
            owner or "<owner>",
            repo,
        )

        response: dict[str, Any] = self._client.create_issue(
            repo=repo,
            title=title,
            body=body,
            labels=labels,
            owner=owner,
        )

        return LandingIssueResult(
            title=title,
            body=body,
            labels=labels,
            issue_number=response.get("number"),
            issue_url=response.get("html_url"),
            dry_run=self._dry_run,
        )
