"""
github_bot.staging_adapter
--------------------------
Sub-Task 4: Staging & New Table Adapter — Draft Merge Request Generator.

Responsibilities:
  1. Python Table Definition Adapter  – Analyzes and rewrites Python-based landing
     table definitions (e.g. `movies_etl/db/ldg/titles.py`) to reflect column
     renames and drops without injecting unneeded op/CDC artifacts.

  2. CTE/CTAS Query Adapter          – Rewrites existing staging SQL files to reflect
     column renames and drops with explicit named projections.

  3. Repo Path Resolver              – Discovers existing Python and SQL files across
     the `movies_etl` directory structure (`movies_etl/db/ldg/`, `movies_etl/db/stg/`).

  4. Pull Request Dispatcher         – Uses GitHubClient to:
       • Determine changed / new file paths for movies_etl.
       • Create a feature branch (schema-sync/<change-type>-<commit_sha>).
       • Commit the modified / new Python and SQL files to that branch.
       • Open a Draft Pull Request on movies_etl with full traceability.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from github_bot.client import GitHubClient
from github_bot.config import GitHubConfig
from github_bot.schema_differ import (
    ChangeCategory,
    ColumnDiff,
    ColumnSchema,
    SchemaDiffResult,
    TableDiff,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants — movies_etl file conventions
# ---------------------------------------------------------------------------

_LANDING_PY_BASE_PATH = "movies_etl/db/ldg"
_STAGING_SQL_BASE_PATH = "movies_etl/db/stg"

# Branch name prefix for all staging-sync PRs
_BRANCH_PREFIX = "schema-sync"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class AdaptedFile:
    """A Python or SQL file that has been created or modified in movies_etl."""

    path: str
    content: str
    is_new: bool = False
    description: str = ""


@dataclass
class StagingAdapterResult:
    """Result returned by :class:`StagingPRDispatcher.dispatch`."""

    branch_name: str
    pr_number: int | None = None
    pr_url: str | None = None
    pr_title: str = ""
    pr_body: str = ""
    adapted_files: list[AdaptedFile] = field(default_factory=list)
    dry_run: bool = False


# ---------------------------------------------------------------------------
# Helper: SQL to Python type mapping & table name slugification
# ---------------------------------------------------------------------------


def _sql_to_python_type(sql_type: str) -> str:
    """Map SQL DDL types to Python typing annotations."""
    normalized = sql_type.upper().strip()
    if any(t in normalized for t in ("INT", "BIGINT", "SMALLINT", "SERIAL")):
        return "Optional[int]"
    if any(t in normalized for t in ("DOUBLE", "FLOAT", "REAL", "NUMERIC", "DECIMAL")):
        return "Optional[float]"
    if any(t in normalized for t in ("BOOL", "BOOLEAN")):
        return "Optional[bool]"
    if any(t in normalized for t in ("TIMESTAMP", "DATE", "TIME")):
        return "Optional[str]"
    return "Optional[str]"


def _table_to_short_name(table_name: str) -> str:
    """
    Map table names to short module names used in movies_etl:
      titles_top_100 -> titles
      movie_reviews  -> movie_reviews
    """
    clean = table_name.lower().strip()
    if "titles" in clean:
        return "titles"
    return re.sub(r"[^a-z0-9_]+", "_", clean).strip("_")


def _pascal_case(name: str) -> str:
    """Convert snake_case or lowercase table name to PascalCase class name."""
    parts = re.split(r"[^a-zA-Z0-9]+", name)
    return "".join(p.capitalize() for p in parts if p)


# ---------------------------------------------------------------------------
# 1. Python Landing Table Definition Adapter
# ---------------------------------------------------------------------------


class PythonTableDefinitionAdapter:
    """
    Inspects, modifies, and scaffolds Python-based landing table definitions in
    `movies_etl/db/ldg/<table_name>.py`.
    """

    def adapt_python_file(self, existing_code: str, table_diff: TableDiff) -> str:
        """
        Update an existing Python table definition file in movies_etl by applying
        column drops and renames.
        """
        result = existing_code
        for cd in table_diff.column_diffs:
            if cd.change_category == ChangeCategory.DROP_COLUMN:
                col = cd.column_name.lower()
                # Remove attribute definition lines like:
                #    poster_link: Optional[str] = None
                #    poster_link = Column(...)
                #    "poster_link",
                attr_pattern = re.compile(
                    rf"^\s*(?:{re.escape(col)}\s*[:=]|['\"]{re.escape(col)}['\"],?).*$\n?",
                    flags=re.MULTILINE | re.IGNORECASE,
                )
                result = attr_pattern.sub("", result)
            elif cd.change_category == ChangeCategory.RENAME_COLUMN:
                old = (cd.old_column_name or cd.column_name).lower()
                new = cd.column_name.lower()
                # Replace attribute name or string literal
                rename_pattern = re.compile(rf"\b{re.escape(old)}\b", flags=re.IGNORECASE)
                result = rename_pattern.sub(new, result)

        return result

    def scaffold_python_file(self, table_name: str, columns: list[ColumnSchema]) -> str:
        """
        Generate a clean Python landing table definition file for a new table.
        Contains only actual source columns — no artificial audit / op columns.
        """
        class_name = _pascal_case(_table_to_short_name(table_name))
        short_name = _table_to_short_name(table_name)

        col_lines: list[str] = []
        for col in columns:
            col_name = col.name.lower()
            py_type = _sql_to_python_type(col.data_type)
            col_lines.append(f"    {col_name}: {py_type} = None")

        fields_block = "\n".join(col_lines) if col_lines else "    pass"

        return (
            f'"""\n'
            f"movies_etl.db.ldg.{short_name}\n"
            f"----------------------------\n"
            f"Landing table definition for `{table_name}` in movies_etl.\n"
            f"Auto-generated by movies_db schema-sync bot.\n"
            f'"""\n\n'
            f"from dataclasses import dataclass\n"
            f"from typing import Optional\n\n\n"
            f"@dataclass\n"
            f"class {class_name}LandingTable:\n"
            f'    """Landing table schema definition for {table_name}."""\n\n'
            f"    __tablename__ = \"{table_name}\"\n\n"
            f"{fields_block}\n"
        )


# ---------------------------------------------------------------------------
# 2. CTE / CTAS Query Adapter
# ---------------------------------------------------------------------------


class CTEQueryAdapter:
    """
    Rewrites CTE / CTAS staging SQL to accommodate column renames and drops
    with explicit column lists.
    """

    def adapt(self, sql: str, column_diffs: list[ColumnDiff]) -> str:
        result = sql
        for diff in column_diffs:
            if diff.change_category == ChangeCategory.RENAME_COLUMN:
                result = self._apply_rename(result, diff)
            elif diff.change_category == ChangeCategory.DROP_COLUMN:
                result = self._apply_drop(result, diff)
        return result

    def _apply_rename(self, sql: str, diff: ColumnDiff) -> str:
        old = diff.old_column_name or diff.column_name
        new = diff.column_name
        if not old or old == new:
            return sql

        pattern = re.compile(
            r"(?i)(?P<prefix>(?:\w+\.)?)(?P<col>\b" + re.escape(old) + r"\b)"
            r"(?P<suffix>(?:\s+[Aa][Ss]\s+\w+)?)"
        )

        def _repl(m: re.Match) -> str:
            prefix = m.group("prefix")
            suffix = m.group("suffix")
            has_alias = bool(suffix.strip())
            if has_alias:
                return f"{prefix}{new}{suffix}"
            return f"{prefix}{new} AS {old}"

        return pattern.sub(_repl, sql)

    def _apply_drop(self, sql: str, diff: ColumnDiff) -> str:
        col = diff.column_name
        col_pattern = re.compile(
            r"(?i)^\s*(?:\w+\.)?(?:\b" + re.escape(col) + r"\b)(?:\s+[Aa][Ss]\s+\w+)?\s*,?\s*$"
        )
        lines = sql.splitlines(keepends=True)
        filtered: list[str] = []
        for line in lines:
            if col_pattern.match(line):
                continue
            filtered.append(line)

        cleaned = "".join(filtered)
        cleaned = re.sub(r",(\s*\n\s*(?:FROM|WHERE|\)))", r"\1", cleaned, flags=re.IGNORECASE)
        return cleaned

    def build_staging_cte(self, table_name: str, columns: list[ColumnSchema]) -> str:
        """Build an explicit CTE: source → renamed → final SELECT (no SELECT *)."""
        col_names = [c.name.lower() for c in columns]
        short_name = _table_to_short_name(table_name)

        source_select = ",\n        ".join(col_names) if col_names else "1"
        source_cte = (
            f"source AS (\n"
            f"    SELECT\n"
            f"        {source_select}\n"
            f"    FROM ldg.{table_name}\n"
            f")"
        )

        renamed_lines = [f"        {c}" for c in col_names]
        renamed_body = ",\n".join(renamed_lines) if renamed_lines else "        1"
        renamed_cte = (
            f"renamed AS (\n"
            f"    SELECT\n"
            f"{renamed_body}\n"
            f"    FROM source\n"
            f")"
        )

        final_cols = ",\n    ".join(col_names) if col_names else "*"
        final_select = (
            f"SELECT\n"
            f"    {final_cols}\n"
            f"FROM renamed"
        )

        return (
            f"-- Staging model for: {short_name}\n"
            f"-- Auto-generated by movies_db schema-sync bot\n"
            f"-- DO NOT EDIT MANUALLY — regenerate via sync_bot.py\n\n"
            f"WITH\n"
            f"{source_cte},\n\n"
            f"{renamed_cte}\n\n"
            f"{final_select};\n"
        )


# ---------------------------------------------------------------------------
# 3. Pull Request Body Formatter
# ---------------------------------------------------------------------------


class StagingPRFormatter:
    """Builds the Draft PR title and markdown body for staging sync PRs."""

    def format_column_change_pr(
        self,
        diff_result: SchemaDiffResult,
        adapted_files: list[AdaptedFile],
        commit_sha: str,
        source_repo: str,
        issue_number: int | None = None,
        owner: str | None = None,
    ) -> tuple[str, str]:
        short_sha = commit_sha[:8]
        tables = ", ".join(t.table_name for t in diff_result.tables_modified)

        title = f"[Staging Sync] Adapt landing definitions and staging CTEs for {tables}"

        file_list = "\n".join(f"- `{f.path}` ({f.description})" for f in adapted_files)
        change_detail = self._format_column_diffs(diff_result)
        issue_ref = self._issue_ref(issue_number, owner, "movies_etl") if issue_number else "_None_"
        commit_url = f"https://github.com/{owner}/{source_repo}/commit/{commit_sha}" if owner else commit_sha

        body = (
            f"## Staging & Landing Sync — Column Changes\n\n"
            f"This **Draft PR** was automatically generated by the `movies_db` schema-sync bot "
            f"in response to schema changes detected in commit "
            f"[`{short_sha}`]({commit_url}).\n\n"
            f"### Modified Files\n\n"
            f"{file_list}\n\n"
            f"### Schema Change Summary\n\n"
            f"{change_detail}\n\n"
            f"### Traceability\n\n"
            f"| Field | Value |\n"
            f"|---|---|\n"
            f"| Source Repo | `{source_repo}` |\n"
            f"| Commit SHA | [`{short_sha}`]({commit_url}) |\n"
            f"| Landing Issue | {issue_ref} |\n\n"
            f"---\n"
            f"> ⚠️ Review all Python landing table definitions and staging SQL queries carefully before merging.\n"
        )
        return title, body

    def format_new_table_pr(
        self,
        diff_result: SchemaDiffResult,
        adapted_files: list[AdaptedFile],
        commit_sha: str,
        source_repo: str,
        issue_number: int | None = None,
        owner: str | None = None,
    ) -> tuple[str, str]:
        short_sha = commit_sha[:8]
        tables = ", ".join(t.table_name for t in diff_result.tables_added)

        title = f"[Staging Sync] Scaffold landing definition and staging CTE for {tables}"

        file_list = "\n".join(
            f"- `{f.path}` ({'new' if f.is_new else 'modified'}) — {f.description}"
            for f in adapted_files
        )
        issue_ref = self._issue_ref(issue_number, owner, "movies_etl") if issue_number else "_None_"
        commit_url = f"https://github.com/{owner}/{source_repo}/commit/{commit_sha}" if owner else commit_sha

        body = (
            f"## Staging Sync — New Table Scaffolding\n\n"
            f"This **Draft PR** was automatically generated by the `movies_db` schema-sync bot "
            f"for new tables detected in commit "
            f"[`{short_sha}`]({commit_url}).\n\n"
            f"### Created Files\n\n"
            f"{file_list}\n\n"
            f"### Traceability\n\n"
            f"| Field | Value |\n"
            f"|---|---|\n"
            f"| Source Repo | `{source_repo}` |\n"
            f"| Commit SHA | [`{short_sha}`]({commit_url}) |\n"
            f"| Landing Issue | {issue_ref} |\n\n"
            f"---\n"
            f"> 📋 Review scaffolded landing definitions and staging SQL before merging.\n"
        )
        return title, body

    @staticmethod
    def _format_column_diffs(diff_result: SchemaDiffResult) -> str:
        lines: list[str] = []
        for td in diff_result.tables_modified:
            lines.append(f"**`{td.table_name}`**")
            for cd in td.column_diffs:
                if cd.change_category == ChangeCategory.RENAME_COLUMN:
                    lines.append(f"- Renamed: `{cd.old_column_name}` → `{cd.column_name}`")
                elif cd.change_category == ChangeCategory.DROP_COLUMN:
                    lines.append(f"- Dropped: `{cd.column_name}`")
                elif cd.change_category == ChangeCategory.ADD_COLUMN:
                    lines.append(f"- Added: `{cd.column_name}` ({cd.new_type})")
                elif cd.change_category == ChangeCategory.TYPE_CHANGE:
                    lines.append(f"- Type change: `{cd.column_name}` `{cd.old_type}` → `{cd.new_type}`")
        return "\n".join(lines) if lines else "_No column-level changes._"

    @staticmethod
    def _issue_ref(issue_number: int, owner: str | None, repo: str) -> str:
        if owner:
            return f"[#{issue_number}](https://github.com/{owner}/{repo}/issues/{issue_number})"
        return f"#{issue_number}"


# ---------------------------------------------------------------------------
# 4. Pull Request Dispatcher
# ---------------------------------------------------------------------------


class StagingPRDispatcher:
    """
    Orchestrates branch creation, file commits, and Draft PR opening on
    ``movies_etl`` for Python landing table definitions and staging SQL models.
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
        self._py_adapter = PythonTableDefinitionAdapter()
        self._cte_adapter = CTEQueryAdapter()
        self._formatter = StagingPRFormatter()

    def dispatch(
        self,
        diff_result: SchemaDiffResult,
        commit_sha: str,
        source_repo: str = "movies_db",
        target_repo: str | None = None,
        owner: str | None = None,
        base_branch: str = "main",
        existing_staging_files: dict[str, str] | None = None,
        landing_issue_number: int | None = None,
    ) -> list[StagingAdapterResult]:
        if not diff_result.has_changes:
            logger.info("No schema changes detected; skipping Staging PR dispatch.")
            return []

        resolved_repo = target_repo or self._config.movies_etl_repo
        resolved_owner = owner or self._config.github_owner or None
        files_map = existing_staging_files or {}

        results: list[StagingAdapterResult] = []

        # --- Scenario 1: column renames / drops on existing tables ---
        if diff_result.tables_modified:
            result = self._handle_column_changes(
                diff_result=diff_result,
                commit_sha=commit_sha,
                source_repo=source_repo,
                target_repo=resolved_repo,
                owner=resolved_owner,
                base_branch=base_branch,
                files_map=files_map,
                issue_number=landing_issue_number,
            )
            results.append(result)

        # --- Scenario 2: new tables ---
        if diff_result.tables_added:
            result = self._handle_new_tables(
                diff_result=diff_result,
                commit_sha=commit_sha,
                source_repo=source_repo,
                target_repo=resolved_repo,
                owner=resolved_owner,
                base_branch=base_branch,
                issue_number=landing_issue_number,
            )
            results.append(result)

        return results

    def _find_matching_path(self, table_name: str, files_map: dict[str, str], extension: str) -> str | None:
        """Find an existing path in files_map matching the table name."""
        short_name = _table_to_short_name(table_name)
        full_name = table_name.lower()
        for path in files_map:
            if path.endswith(extension):
                path_lower = path.lower()
                if f"/{short_name}{extension}" in path_lower or f"/{full_name}{extension}" in path_lower:
                    return path
        return None

    def _handle_column_changes(
        self,
        diff_result: SchemaDiffResult,
        commit_sha: str,
        source_repo: str,
        target_repo: str,
        owner: str | None,
        base_branch: str,
        files_map: dict[str, str],
        issue_number: int | None,
    ) -> StagingAdapterResult:
        short_sha = commit_sha[:8]
        adapted_files: list[AdaptedFile] = []

        for table_diff in diff_result.tables_modified:
            table = table_diff.table_name
            short_name = _table_to_short_name(table)

            # 1. Adapt Python landing table definition (e.g. movies_etl/db/ldg/titles.py)
            py_path = self._find_matching_path(table, files_map, ".py") or f"{_LANDING_PY_BASE_PATH}/{short_name}.py"
            existing_py = files_map.get(py_path)
            if existing_py:
                updated_py = self._py_adapter.adapt_python_file(existing_py, table_diff)
            else:
                cols = list(table_diff.new_table.columns.values()) if table_diff.new_table else []
                updated_py = self._py_adapter.scaffold_python_file(table, cols)

            adapted_files.append(
                AdaptedFile(
                    path=py_path,
                    content=updated_py,
                    is_new=py_path not in files_map,
                    description=f"Updated Python landing definition for `{short_name}` (reflected dropped/renamed columns)",
                )
            )

            # 2. Adapt Staging SQL CTE query (e.g. movies_etl/db/stg/titles.sql)
            sql_path = self._find_matching_path(table, files_map, ".sql") or f"{_STAGING_SQL_BASE_PATH}/{short_name}.sql"
            existing_sql = files_map.get(sql_path)
            if existing_sql:
                updated_sql = self._cte_adapter.adapt(existing_sql, table_diff.column_diffs)
            else:
                cols = list(table_diff.new_table.columns.values()) if table_diff.new_table else []
                updated_sql = self._cte_adapter.build_staging_cte(table, cols)

            adapted_files.append(
                AdaptedFile(
                    path=sql_path,
                    content=updated_sql,
                    is_new=sql_path not in files_map,
                    description=f"Adapted staging CTE for `{short_name}` column changes",
                )
            )

        branch_name = f"{_BRANCH_PREFIX}/{self._slugify(diff_result.tables_modified[0].table_name)}-{short_sha}"
        title, body = self._formatter.format_column_change_pr(
            diff_result=diff_result,
            adapted_files=adapted_files,
            commit_sha=commit_sha,
            source_repo=source_repo,
            issue_number=issue_number,
            owner=owner,
        )

        return self._push_and_open_pr(
            adapted_files=adapted_files,
            branch_name=branch_name,
            base_branch=base_branch,
            target_repo=target_repo,
            owner=owner,
            title=title,
            body=body,
            commit_sha=commit_sha,
        )

    def _handle_new_tables(
        self,
        diff_result: SchemaDiffResult,
        commit_sha: str,
        source_repo: str,
        target_repo: str,
        owner: str | None,
        base_branch: str,
        issue_number: int | None,
    ) -> StagingAdapterResult:
        short_sha = commit_sha[:8]
        adapted_files: list[AdaptedFile] = []

        for table_diff in diff_result.tables_added:
            table = table_diff.table_name
            short_name = _table_to_short_name(table)
            columns = list(table_diff.new_table.columns.values()) if table_diff.new_table else []

            # 1. Scaffold Python landing definition
            py_code = self._py_adapter.scaffold_python_file(table, columns)
            adapted_files.append(
                AdaptedFile(
                    path=f"{_LANDING_PY_BASE_PATH}/{short_name}.py",
                    content=py_code,
                    is_new=True,
                    description=f"Scaffolded Python landing definition for `{short_name}`",
                )
            )

            # 2. Scaffold Staging SQL CTE
            sql_code = self._cte_adapter.build_staging_cte(table, columns)
            adapted_files.append(
                AdaptedFile(
                    path=f"{_STAGING_SQL_BASE_PATH}/{short_name}.sql",
                    content=sql_code,
                    is_new=True,
                    description=f"Scaffolded Staging CTE query for `{short_name}`",
                )
            )

        first_table = self._slugify(diff_result.tables_added[0].table_name)
        branch_name = f"{_BRANCH_PREFIX}/new-table-{first_table}-{short_sha}"
        title, body = self._formatter.format_new_table_pr(
            diff_result=diff_result,
            adapted_files=adapted_files,
            commit_sha=commit_sha,
            source_repo=source_repo,
            issue_number=issue_number,
            owner=owner,
        )

        return self._push_and_open_pr(
            adapted_files=adapted_files,
            branch_name=branch_name,
            base_branch=base_branch,
            target_repo=target_repo,
            owner=owner,
            title=title,
            body=body,
            commit_sha=commit_sha,
        )

    def _push_and_open_pr(
        self,
        adapted_files: list[AdaptedFile],
        branch_name: str,
        base_branch: str,
        target_repo: str,
        owner: str | None,
        title: str,
        body: str,
        commit_sha: str | None = None,
    ) -> StagingAdapterResult:
        logger.debug("Opening staging sync PR for commit %s on branch %s", commit_sha, branch_name)
        base_info = self._client.get_branch(repo=target_repo, branch=base_branch, owner=owner)
        base_sha = base_info["commit"]["sha"]

        self._client.create_branch(
            repo=target_repo,
            branch_name=branch_name,
            from_sha=base_sha,
            owner=owner,
        )
        logger.info("Created branch %s on %s/%s", branch_name, owner or "<owner>", target_repo)

        for adapted_file in adapted_files:
            self._client.create_or_update_file(
                repo=target_repo,
                path=adapted_file.path,
                message=f"chore(schema-sync): {adapted_file.description}",
                content=adapted_file.content,
                branch=branch_name,
                owner=owner,
            )
            logger.info("Committed %s to %s", adapted_file.path, branch_name)

        pr_response = self._client.create_pull_request(
            repo=target_repo,
            title=title,
            body=body,
            head=branch_name,
            base=base_branch,
            draft=True,
            owner=owner,
        )
        logger.info(
            "%s Draft PR #%s created: %s",
            "[DRY RUN]" if self._dry_run else "",
            pr_response.get("number"),
            pr_response.get("html_url"),
        )

        return StagingAdapterResult(
            branch_name=branch_name,
            pr_number=pr_response.get("number"),
            pr_url=pr_response.get("html_url"),
            pr_title=title,
            pr_body=body,
            adapted_files=adapted_files,
            dry_run=self._dry_run,
        )

    @staticmethod
    def _slugify(name: str) -> str:
        return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
