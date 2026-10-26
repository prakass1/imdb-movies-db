"""
github_bot.changelog_generator
------------------------------
Formats schema differences into structured markdown changelogs saved under
`changelogs/changelog-<timestamp>-<commit_sha>.md`.

Documents commit metadata, MR references, categorized schema changes, and
actionable impact analyses for downstream Landing and Staging layers.
"""

from __future__ import annotations

import datetime
import os
import re
from typing import Any

from github_bot.schema_differ import ChangeCategory, ColumnDiff, SchemaDiffResult, TableDiff


class ChangelogGenerator:
    """Changelog generator that turns SchemaDiffResult instances into markdown documents."""

    def __init__(self, output_dir: str = "changelogs") -> None:
        self.output_dir = output_dir

    @staticmethod
    def format_timestamp(ts: datetime.datetime | str | None = None) -> tuple[str, str]:
        """
        Return (file_timestamp, display_timestamp).
        file_timestamp: YYYYMMDD-HHMMSS or clean numeric string
        display_timestamp: ISO 8601 formatted string
        """
        if ts is None:
            now = datetime.datetime.now(datetime.timezone.utc)
            return now.strftime("%Y%m%d-%H%M%S"), now.strftime("%Y-%m-%d %H:%M:%S UTC")
        if isinstance(ts, datetime.datetime):
            return ts.strftime("%Y%m%d-%H%M%S"), ts.strftime("%Y-%m-%d %H:%M:%S UTC")
        # ts is string
        clean_ts = re.sub(r"[^0-9a-zA-Z_-]", "", ts)
        return clean_ts, str(ts)

    def generate_filename(self, commit_sha: str, timestamp: datetime.datetime | str | None = None) -> str:
        """Generate standardized changelog filename: changelog-<timestamp>-<commit_sha>.md."""
        file_ts, _ = self.format_timestamp(timestamp)
        clean_sha = commit_sha[:8] if len(commit_sha) >= 8 else commit_sha
        if not clean_sha:
            clean_sha = "initial"
        return f"changelog-{file_ts}-{clean_sha}.md"

    def generate_markdown(
        self,
        diff_result: SchemaDiffResult,
        commit_sha: str,
        mr_reference: str | None = None,
        timestamp: datetime.datetime | str | None = None,
        author: str | None = None,
        source_repo: str = "movies_db",
        target_repo: str = "movies_etl",
    ) -> str:
        """Generate structured markdown changelog content."""
        _, display_ts = self.format_timestamp(timestamp)
        short_sha = commit_sha[:8] if len(commit_sha) >= 8 else commit_sha
        mr_ref_display = mr_reference or "Pending Sync MR"
        breaking_status = "⚠️ BREAKING CHANGES DETECTED" if diff_result.is_breaking else "✅ NON-BREAKING ADDITIONS"

        summary_counts = diff_result.summary_counts
        total_changes = sum(summary_counts.values())

        lines: list[str] = [
            f"# Schema Migration Changelog: {short_sha}",
            "",
            "## Metadata",
            f"- **Commit SHA**: `{commit_sha}`",
            f"- **Source Repository**: `{source_repo}`",
            f"- **Target Sync Repository**: `{target_repo}`",
            f"- **Merge Request Reference**: {mr_ref_display}",
            f"- **Timestamp**: `{display_ts}`",
            f"- **Author / Trigger**: {author or 'Bob Schema Bot'}",
            f"- **Migration Status**: **{breaking_status}**",
            "",
            "---",
            "",
            "## Executive Summary",
            f"A total of **{total_changes}** schema modification(s) across **{len(diff_result.affected_table_names)}** table(s) were identified.",
            "",
            "| Change Category | Count | Impact Level |",
            "|:---|:---:|:---|",
            f"| **New Tables** (`NEW_TABLE`) | {summary_counts[ChangeCategory.NEW_TABLE.value]} | Info / Scaffolding Required |",
            f"| **Dropped Tables** (`DROP_TABLE`) | {summary_counts[ChangeCategory.DROP_TABLE.value]} | High (Pipeline Decommission) |",
            f"| **Renamed Columns** (`RENAME_COLUMN`) | {summary_counts[ChangeCategory.RENAME_COLUMN.value]} | High (CTAS / CTE Breaking) |",
            f"| **Dropped Columns** (`DROP_COLUMN`) | {summary_counts[ChangeCategory.DROP_COLUMN.value]} | High (Projection Breaking) |",
            f"| **Type Changes** (`TYPE_CHANGE`) | {summary_counts[ChangeCategory.TYPE_CHANGE.value]} | Medium / High (Cast Mismatch) |",
            f"| **Added Columns** (`ADD_COLUMN`) | {summary_counts[ChangeCategory.ADD_COLUMN.value]} | Low (Backward Compatible) |",
            f"| **Constraint Changes** (`CONSTRAINT_CHANGE`) | {summary_counts[ChangeCategory.CONSTRAINT_CHANGE.value]} | Medium |",
            "",
            "---",
            "",
            "## Categorized Modifications by Table",
            "",
        ]

        if not diff_result.has_changes:
            lines.append("No schema differences detected between compared states.\n")
        else:
            # 1. New Tables
            if diff_result.tables_added:
                lines.append("### 🆕 New Tables")
                for tbl_diff in diff_result.tables_added:
                    lines.append(f"#### Table: `{tbl_diff.table_name}`")
                    lines.append(f"_{tbl_diff.details}_")
                    lines.append("")
                    lines.append("| Column Name | Data Type | Constraints | Description |")
                    lines.append("|:---|:---|:---|:---|")
                    for col in tbl_diff.column_diffs:
                        const_str = ", ".join(col.new_constraints) if col.new_constraints else "None"
                        lines.append(f"| `{col.column_name}` | `{col.new_type}` | {const_str} | {col.details} |")
                    lines.append("")

            # 2. Dropped Tables
            if diff_result.tables_dropped:
                lines.append("### 🗑️ Dropped Tables")
                for tbl_diff in diff_result.tables_dropped:
                    lines.append(f"- **Table `{tbl_diff.table_name}`**: {tbl_diff.details}")
                lines.append("")

            # 3. Modified Tables
            if diff_result.tables_modified:
                lines.append("### 🔄 Modified Tables")
                for tbl_diff in diff_result.tables_modified:
                    lines.append(f"#### Table: `{tbl_diff.table_name}`")
                    lines.append("")
                    lines.append("| Column | Operation | Previous State | New State | Details |")
                    lines.append("|:---|:---|:---|:---|:---|")
                    for col in tbl_diff.column_diffs:
                        prev_state = f"`{col.old_type}`" if col.old_type else "-"
                        if col.old_column_name and col.old_column_name != col.column_name:
                            prev_state = f"`{col.old_column_name}` ({col.old_type or 'unknown'})"

                        new_state = f"`{col.new_type}`" if col.new_type else "-"
                        lines.append(
                            f"| `{col.column_name}` | `{col.change_category.value}` | {prev_state} | {new_state} | {col.details} |"
                        )
                    lines.append("")

        # Downstream Impact Analysis
        lines.extend([
            "---",
            "",
            "## Downstream Impact Analysis",
            "",
            "### 1. Landing Zone (`movies_etl` Ingestion)",
        ])

        landing_impacts = self._generate_landing_impact(diff_result)
        for impact in landing_impacts:
            lines.append(f"- {impact}")

        lines.extend([
            "",
            "### 2. Staging Layer (`movies_etl` Transformations & CTEs)",
        ])

        staging_impacts = self._generate_staging_impact(diff_result)
        for impact in staging_impacts:
            lines.append(f"- {impact}")

        lines.extend([
            "",
            "---",
            "",
            "## Operational Action Checklist",
            "",
        ])

        actions = self._generate_action_checklist(diff_result)
        for action in actions:
            lines.append(f"- [ ] {action}")

        lines.append("")
        return "\n".join(lines)

    def write_changelog(
        self,
        diff_result: SchemaDiffResult,
        commit_sha: str,
        mr_reference: str | None = None,
        timestamp: datetime.datetime | str | None = None,
        author: str | None = None,
        source_repo: str = "movies_db",
        target_repo: str = "movies_etl",
    ) -> tuple[str, str]:
        """
        Generate markdown and write to output_dir/changelog-<timestamp>-<commit_sha>.md.
        Returns (relative_file_path, markdown_content).
        """
        filename = self.generate_filename(commit_sha, timestamp)
        os.makedirs(self.output_dir, exist_ok=True)
        filepath = os.path.join(self.output_dir, filename)

        content = self.generate_markdown(
            diff_result=diff_result,
            commit_sha=commit_sha,
            mr_reference=mr_reference,
            timestamp=timestamp,
            author=author,
            source_repo=source_repo,
            target_repo=target_repo,
        )

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)

        return filepath, content

    @staticmethod
    def _generate_landing_impact(diff_result: SchemaDiffResult) -> list[str]:
        """Generate specific impact items for Landing Zone."""
        impacts: list[str] = []

        if not diff_result.has_changes:
            return ["No landing layer changes required. Schema matches target state."]

        for tbl in diff_result.tables_added:
            impacts.append(
                f"**New CDC Stream Required**: Table `{tbl.table_name}` must be provisioned in Landing Zone with initial Full Load (FL)."
            )

        for tbl in diff_result.tables_dropped:
            impacts.append(
                f"**Pipeline Decommission**: Cease CDC replication for table `{tbl.table_name}` and DROP landing replica."
            )

        for tbl in diff_result.tables_modified:
            dropped_or_renamed = [
                c
                for c in tbl.column_diffs
                if c.change_category in (ChangeCategory.DROP_COLUMN, ChangeCategory.RENAME_COLUMN)
            ]
            added_cols = [c for c in tbl.column_diffs if c.change_category == ChangeCategory.ADD_COLUMN]
            type_changes = [c for c in tbl.column_diffs if c.change_category == ChangeCategory.TYPE_CHANGE]

            if dropped_or_renamed:
                impacts.append(
                    f"**Table Drop & CDC Reload Needed**: Table `{tbl.table_name}` has dropped/renamed columns "
                    f"({', '.join(c.column_name for c in dropped_or_renamed)}). Active pipelines must be stopped prior to FL, "
                    f"landing table dropped, and CDC events reloaded."
                )
            if type_changes:
                impacts.append(
                    f"**Type Alignment Required**: Table `{tbl.table_name}` column data types altered "
                    f"({', '.join(f'{c.column_name} ({c.new_type})' for c in type_changes)}). Verify raw landing serde compatibility."
                )
            if added_cols and not dropped_or_renamed and not type_changes:
                impacts.append(
                    f"**Schema Evolution (Additive)**: Table `{tbl.table_name}` added column(s) "
                    f"({', '.join(c.column_name for c in added_cols)}). Ingestion supports nullable column extension."
                )

        return impacts

    @staticmethod
    def _generate_staging_impact(diff_result: SchemaDiffResult) -> list[str]:
        """Generate specific impact items for Staging Layer."""
        impacts: list[str] = []

        if not diff_result.has_changes:
            return ["No staging layer changes required."]

        for tbl in diff_result.tables_added:
            impacts.append(
                f"**Scaffold Staging Model**: Create staging CTE/CTAS model for `{tbl.table_name}` adhering to team SQL standards."
            )

        for tbl in diff_result.tables_dropped:
            impacts.append(
                f"**Remove Staging References**: Archive or remove staging models dependent on dropped table `{tbl.table_name}`."
            )

        for tbl in diff_result.tables_modified:
            renames = [c for c in tbl.column_diffs if c.change_category == ChangeCategory.RENAME_COLUMN]
            drops = [c for c in tbl.column_diffs if c.change_category == ChangeCategory.DROP_COLUMN]
            adds = [c for c in tbl.column_diffs if c.change_category == ChangeCategory.ADD_COLUMN]
            types = [c for c in tbl.column_diffs if c.change_category == ChangeCategory.TYPE_CHANGE]

            if renames:
                rename_desc = ", ".join(f"`{c.old_column_name}` -> `{c.column_name}`" for c in renames)
                impacts.append(
                    f"**Update CTE Column Projections**: Table `{tbl.table_name}` column renames ({rename_desc}) "
                    f"require updating CTE SELECT expressions and downstream alias mappings."
                )
            if drops:
                drop_desc = ", ".join(f"`{c.column_name}`" for c in drops)
                impacts.append(
                    f"**Remove Dropped Columns**: Remove references to dropped column(s) {drop_desc} from `{tbl.table_name}` staging models."
                )
            if types:
                type_desc = ", ".join(f"`{c.column_name}` -> `{c.new_type}`" for c in types)
                impacts.append(
                    f"**Adjust Casting/Formatting**: Adjust data type casts for {type_desc} in `{tbl.table_name}` staging transformations."
                )
            if adds:
                add_desc = ", ".join(f"`{c.column_name}`" for c in adds)
                impacts.append(
                    f"**Include New Projections**: Expose new column(s) {add_desc} in `{tbl.table_name}` staging CTEs as required."
                )

        return impacts

    @staticmethod
    def _generate_action_checklist(diff_result: SchemaDiffResult) -> list[str]:
        """Generate step-by-step operational checklist."""
        actions: list[str] = [
            "**Step 1: Pipeline Interruption Check** - Pause running Landing ingestion pipelines prior to applying changes.",
        ]

        if diff_result.tables_dropped or any(
            c.change_category in (ChangeCategory.DROP_COLUMN, ChangeCategory.RENAME_COLUMN)
            for c in diff_result.all_column_diffs
        ):
            actions.append(
                "**Step 2: DROP Affected Landing Tables** - Drop and recreate landing tables with breaking modifications."
            )
            actions.append(
                "**Step 3: CDC Reload & Backfill** - Reload CDC events and trigger Full Load (FL) for affected landing tables."
            )
        elif diff_result.tables_added:
            actions.append(
                "**Step 2: Provision Landing Table** - Initialize landing tables and CDC replication for new tables."
            )
            actions.append(
                "**Step 3: Full Load Initialization** - Run initial bulk data load for newly onboarded tables."
            )
        else:
            actions.append(
                "**Step 2: Schema Evolution Verification** - Validate landing zone receives updated schema definitions."
            )

        actions.append(
            "**Step 4: Staging Merge Request (MR)** - Review and merge staging query updates (CTE mappings & CTAS scaffolds)."
        )
        actions.append(
            "**Step 5: Pipeline Resumption & End-to-End Test** - Resume ingestion pipelines and execute data validation tests."
        )

        return actions
