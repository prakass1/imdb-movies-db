"""
github_bot.schema_differ
-------------------------
SQL DDL parser and schema diff engine for PostgreSQL / relational schemas.
Parses CREATE TABLE and ALTER statements into structured AST representations,
compares DDL states, and categorizes schema modifications.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ChangeCategory(str, Enum):
    """Categorized schema change event types."""

    NEW_TABLE = "NEW_TABLE"
    DROP_TABLE = "DROP_TABLE"
    ADD_COLUMN = "ADD_COLUMN"
    DROP_COLUMN = "DROP_COLUMN"
    RENAME_COLUMN = "RENAME_COLUMN"
    TYPE_CHANGE = "TYPE_CHANGE"
    CONSTRAINT_CHANGE = "CONSTRAINT_CHANGE"


@dataclass
class ColumnSchema:
    """Structured representation of a table column definition."""

    name: str
    data_type: str
    nullable: bool = True
    primary_key: bool = False
    unique: bool = False
    default_value: str | None = None
    constraints: list[str] = field(default_factory=list)

    def normalized_type(self) -> str:
        """Return uppercase normalized data type without redundant whitespace."""
        return " ".join(self.data_type.upper().split())


@dataclass
class TableSchema:
    """Structured representation of a database table."""

    name: str
    columns: dict[str, ColumnSchema] = field(default_factory=dict)
    primary_keys: list[str] = field(default_factory=list)
    table_constraints: list[str] = field(default_factory=list)
    raw_ddl: str = ""

    def get_column(self, col_name: str) -> ColumnSchema | None:
        """Case-insensitive column lookup."""
        col_lower = col_name.lower()
        for name, col in self.columns.items():
            if name.lower() == col_lower:
                return col
        return None


@dataclass
class ColumnDiff:
    """Detailed diff for a specific column change."""

    change_category: ChangeCategory
    table_name: str
    column_name: str
    old_column_name: str | None = None
    old_type: str | None = None
    new_type: str | None = None
    old_constraints: list[str] = field(default_factory=list)
    new_constraints: list[str] = field(default_factory=list)
    details: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Convert diff to dictionary."""
        return {
            "change_category": self.change_category.value,
            "table_name": self.table_name,
            "column_name": self.column_name,
            "old_column_name": self.old_column_name,
            "old_type": self.old_type,
            "new_type": self.new_type,
            "old_constraints": self.old_constraints,
            "new_constraints": self.new_constraints,
            "details": self.details,
        }


@dataclass
class TableDiff:
    """Detailed diff for table-level changes and modified columns."""

    change_category: ChangeCategory
    table_name: str
    old_table: TableSchema | None = None
    new_table: TableSchema | None = None
    column_diffs: list[ColumnDiff] = field(default_factory=list)
    details: str = ""


@dataclass
class SchemaDiffResult:
    """Comprehensive comparison result between two schema states."""

    tables_added: list[TableDiff] = field(default_factory=list)
    tables_dropped: list[TableDiff] = field(default_factory=list)
    tables_modified: list[TableDiff] = field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        """Check if any schema modifications were detected."""
        return bool(self.tables_added or self.tables_dropped or self.tables_modified)

    @property
    def affected_table_names(self) -> list[str]:
        """List of all table names impacted by changes."""
        affected: set[str] = set()
        for t in self.tables_added:
            affected.add(t.table_name)
        for t in self.tables_dropped:
            affected.add(t.table_name)
        for t in self.tables_modified:
            affected.add(t.table_name)
        return sorted(affected)

    @property
    def all_column_diffs(self) -> list[ColumnDiff]:
        """Flat list of all column-level diffs across modified tables."""
        diffs: list[ColumnDiff] = []
        for t in self.tables_modified:
            diffs.extend(t.column_diffs)
        return diffs

    @property
    def summary_counts(self) -> dict[str, int]:
        """Count breakdown of changes by category."""
        counts: dict[str, int] = {
            ChangeCategory.NEW_TABLE.value: len(self.tables_added),
            ChangeCategory.DROP_TABLE.value: len(self.tables_dropped),
            ChangeCategory.ADD_COLUMN.value: 0,
            ChangeCategory.DROP_COLUMN.value: 0,
            ChangeCategory.RENAME_COLUMN.value: 0,
            ChangeCategory.TYPE_CHANGE.value: 0,
            ChangeCategory.CONSTRAINT_CHANGE.value: 0,
        }
        for col_diff in self.all_column_diffs:
            counts[col_diff.change_category.value] = counts.get(col_diff.change_category.value, 0) + 1
        return counts

    @property
    def is_breaking(self) -> bool:
        """Check if changes include breaking modifications (drops, renames, type changes)."""
        if self.tables_dropped:
            return True
        for col_diff in self.all_column_diffs:
            if col_diff.change_category in (
                ChangeCategory.DROP_COLUMN,
                ChangeCategory.RENAME_COLUMN,
                ChangeCategory.TYPE_CHANGE,
            ):
                return True
        return False


class SQLDDLParser:
    """Parser for SQL DDL scripts into structured TableSchema objects."""

    @staticmethod
    def strip_comments(sql: str) -> str:
        """Remove SQL line comments (-- ...) and block comments (/* ... */)."""
        # Remove block comments
        sql = re.sub(r"/\*.*?\*/", "", sql, flags=re.DOTALL)
        # Remove line comments
        sql = re.sub(r"--.*?$", "", sql, flags=re.MULTILINE)
        return sql

    @classmethod
    def parse_ddl(cls, sql_text: str) -> dict[str, TableSchema]:
        """
        Parse DDL statements from SQL text.
        Supports CREATE TABLE [IF NOT EXISTS] and basic ALTER TABLE statements.
        """
        clean_sql = cls.strip_comments(sql_text)
        tables: dict[str, TableSchema] = {}

        # Split into statements by semicolon, accounting for quoted strings
        statements = cls._split_statements(clean_sql)

        for stmt in statements:
            stmt = stmt.strip()
            if not stmt:
                continue

            # Match CREATE TABLE
            create_match = re.match(
                r"^CREATE\s+TABLE(?:\s+IF\s+NOT\s+EXISTS)?\s+([a-zA-Z0-9_\.\"]+)\s*\((.*)\)\s*$",
                stmt,
                re.IGNORECASE | re.DOTALL,
            )
            if create_match:
                table_name_raw = create_match.group(1).strip().strip('"')
                # If schema-qualified, keep clean name
                table_name = table_name_raw.split(".")[-1].strip('"')
                body = create_match.group(2).strip()

                table_schema = cls._parse_table_body(table_name, body, stmt)
                tables[table_name] = table_schema
                continue

            # Match ALTER TABLE ...
            alter_match = re.match(
                r"^ALTER\s+TABLE\s+(?:ONLY\s+)?([a-zA-Z0-9_\.\"]+)\s+(.+)$",
                stmt,
                re.IGNORECASE | re.DOTALL,
            )
            if alter_match:
                table_name_raw = alter_match.group(1).strip().strip('"')
                table_name = table_name_raw.split(".")[-1].strip('"')
                alter_action = alter_match.group(2).strip()
                if table_name in tables:
                    cls._apply_alter_action(tables[table_name], alter_action)

        return tables

    @classmethod
    def _split_statements(cls, sql: str) -> list[str]:
        """Split SQL string by semicolon, respecting single/double quotes."""
        statements: list[str] = []
        current: list[str] = []
        in_single_quote = False
        in_double_quote = False
        parens_depth = 0

        i = 0
        n = len(sql)
        while i < n:
            char = sql[i]
            if char == "'" and not in_double_quote:
                in_single_quote = not in_single_quote
                current.append(char)
            elif char == '"' and not in_single_quote:
                in_double_quote = not in_double_quote
                current.append(char)
            elif char == "(" and not in_single_quote and not in_double_quote:
                parens_depth += 1
                current.append(char)
            elif char == ")" and not in_single_quote and not in_double_quote:
                if parens_depth > 0:
                    parens_depth -= 1
                current.append(char)
            elif char == ";" and not in_single_quote and not in_double_quote and parens_depth == 0:
                stmt = "".join(current).strip()
                if stmt:
                    statements.append(stmt)
                current = []
            else:
                current.append(char)
            i += 1

        if current:
            stmt = "".join(current).strip()
            if stmt:
                statements.append(stmt)

        return statements

    @classmethod
    def _parse_table_body(cls, table_name: str, body: str, raw_ddl: str) -> TableSchema:
        """Parse column and constraint definitions inside CREATE TABLE (...) body."""
        columns: dict[str, ColumnSchema] = {}
        primary_keys: list[str] = []
        table_constraints: list[str] = []

        # Split column / constraint items by comma outside parentheses
        items = cls._split_top_level_commas(body)

        for item in items:
            item = item.strip()
            if not item:
                continue

            # Check if this item is a table constraint
            upper_item = item.upper()

            # PRIMARY KEY (col1, col2)
            pk_match = re.match(r"^PRIMARY\s+KEY\s*\((.*?)\)", item, re.IGNORECASE)
            if pk_match:
                pks = [c.strip().strip('"') for c in pk_match.group(1).split(",")]
                primary_keys.extend(pks)
                table_constraints.append(item)
                continue

            # CONSTRAINT name PRIMARY KEY (...)
            named_pk_match = re.match(
                r"^CONSTRAINT\s+([a-zA-Z0-9_\"]+)\s+PRIMARY\s+KEY\s*\((.*?)\)",
                item,
                re.IGNORECASE,
            )
            if named_pk_match:
                pks = [c.strip().strip('"') for c in named_pk_match.group(2).split(",")]
                primary_keys.extend(pks)
                table_constraints.append(item)
                continue

            # Other constraints: FOREIGN KEY, UNIQUE, CHECK, CONSTRAINT ...
            if (
                upper_item.startswith("CONSTRAINT ")
                or upper_item.startswith("FOREIGN KEY")
                or upper_item.startswith("UNIQUE")
                or upper_item.startswith("CHECK")
            ):
                table_constraints.append(item)
                continue

            # Parse regular column definition: <col_name> <data_type> [constraints...]
            col = cls._parse_column_def(item)
            if col:
                columns[col.name] = col
                if col.primary_key and col.name not in primary_keys:
                    primary_keys.append(col.name)

        return TableSchema(
            name=table_name,
            columns=columns,
            primary_keys=primary_keys,
            table_constraints=table_constraints,
            raw_ddl=raw_ddl,
        )

    @classmethod
    def _split_top_level_commas(cls, text: str) -> list[str]:
        """Split a comma-separated list respecting parentheses and quotes."""
        items: list[str] = []
        current: list[str] = []
        parens_depth = 0
        in_single_quote = False
        in_double_quote = False

        for char in text:
            if char == "'" and not in_double_quote:
                in_single_quote = not in_single_quote
                current.append(char)
            elif char == '"' and not in_single_quote:
                in_double_quote = not in_double_quote
                current.append(char)
            elif char == "(" and not in_single_quote and not in_double_quote:
                parens_depth += 1
                current.append(char)
            elif char == ")" and not in_single_quote and not in_double_quote:
                if parens_depth > 0:
                    parens_depth -= 1
                current.append(char)
            elif char == "," and not in_single_quote and not in_double_quote and parens_depth == 0:
                items.append("".join(current).strip())
                current = []
            else:
                current.append(char)

        if current:
            items.append("".join(current).strip())

        return items

    @classmethod
    def _parse_column_def(cls, text: str) -> ColumnSchema | None:
        """Parse single column definition e.g. 'IMDB_Rating DOUBLE PRECISION NOT NULL'."""
        tokens = text.strip().split()
        if not tokens:
            return None

        col_name = tokens[0].strip('"')
        rest = " ".join(tokens[1:]).strip()

        # Multi-word SQL types: DOUBLE PRECISION, CHARACTER VARYING(N), TIMESTAMP WITH TIME ZONE, etc.
        data_type_tokens: list[str] = []
        constraint_tokens: list[str] = []

        # Common multi-word type prefixes
        tokens_rest = rest.split()
        i = 0
        n = len(tokens_rest)

        # Collect type (including parens like VARCHAR(255) or NUMERIC(10, 2))
        type_parts: list[str] = []
        open_parens = 0

        while i < n:
            tok = tokens_rest[i]
            upper_tok = tok.upper()

            # Stop conditions for data type: constraints keywords
            if open_parens == 0 and upper_tok in (
                "PRIMARY",
                "NOT",
                "NULL",
                "UNIQUE",
                "DEFAULT",
                "CHECK",
                "REFERENCES",
                "CONSTRAINT",
                "AUTO_INCREMENT",
                "GENERATED",
            ):
                break

            type_parts.append(tok)
            open_parens += tok.count("(") - tok.count(")")
            i += 1

        data_type = " ".join(type_parts).strip()
        constraint_part = " ".join(tokens_rest[i:]).strip()

        nullable = True
        primary_key = False
        unique = False
        default_val: str | None = None
        constraints_list: list[str] = []

        if constraint_part:
            upper_c = constraint_part.upper()
            if "NOT NULL" in upper_c:
                nullable = False
                constraints_list.append("NOT NULL")
            if "PRIMARY KEY" in upper_c:
                primary_key = True
                nullable = False
                constraints_list.append("PRIMARY KEY")
            if "UNIQUE" in upper_c:
                unique = True
                constraints_list.append("UNIQUE")

            def_match = re.search(r"DEFAULT\s+([^,]+)", constraint_part, re.IGNORECASE)
            if def_match:
                default_val = def_match.group(1).strip()
                constraints_list.append(f"DEFAULT {default_val}")

        return ColumnSchema(
            name=col_name,
            data_type=data_type,
            nullable=nullable,
            primary_key=primary_key,
            unique=unique,
            default_value=default_val,
            constraints=constraints_list,
        )

    @classmethod
    def _apply_alter_action(cls, table: TableSchema, action: str) -> None:
        """Apply ALTER TABLE action on table schema."""
        action_clean = action.strip()
        upper_act = action_clean.upper()

        # ADD COLUMN col TYPE ...
        add_match = re.match(
            r"^ADD(?:\s+COLUMN)?\s+([a-zA-Z0-9_\"]+)\s+(.+)$",
            action_clean,
            re.IGNORECASE,
        )
        if add_match:
            col_def_str = f"{add_match.group(1)} {add_match.group(2)}"
            col = cls._parse_column_def(col_def_str)
            if col:
                table.columns[col.name] = col
            return

        # DROP COLUMN col
        drop_match = re.match(
            r"^DROP(?:\s+COLUMN)?(?:\s+IF\s+EXISTS)?\s+([a-zA-Z0-9_\"]+)",
            action_clean,
            re.IGNORECASE,
        )
        if drop_match:
            col_name = drop_match.group(1).strip('"')
            table.columns.pop(col_name, None)
            return

        # RENAME COLUMN old_name TO new_name
        rename_match = re.match(
            r"^RENAME(?:\s+COLUMN)?\s+([a-zA-Z0-9_\"]+)\s+TO\s+([a-zA-Z0-9_\"]+)",
            action_clean,
            re.IGNORECASE,
        )
        if rename_match:
            old_name = rename_match.group(1).strip('"')
            new_name = rename_match.group(2).strip('"')
            if old_name in table.columns:
                col = table.columns.pop(old_name)
                col.name = new_name
                table.columns[new_name] = col


class SchemaDiffer:
    """Schema differ comparing two DDL schemas or SQL states."""

    @classmethod
    def diff_ddl_strings(
        cls,
        old_sql: str,
        new_sql: str,
        rename_hints: dict[str, dict[str, str]] | None = None,
    ) -> SchemaDiffResult:
        """Compare two SQL DDL strings and compute schema diff."""
        old_tables = SQLDDLParser.parse_ddl(old_sql)
        new_tables = SQLDDLParser.parse_ddl(new_sql)
        return cls.diff_schemas(old_tables, new_tables, rename_hints=rename_hints)

    @classmethod
    def diff_ddl_files(
        cls,
        old_file_path: str,
        new_file_path: str,
        rename_hints: dict[str, dict[str, str]] | None = None,
    ) -> SchemaDiffResult:
        """Compare two DDL files from disk."""
        old_sql = ""
        if os.path.exists(old_file_path):
            with open(old_file_path, "r", encoding="utf-8") as f:
                old_sql = f.read()

        new_sql = ""
        if os.path.exists(new_file_path):
            with open(new_file_path, "r", encoding="utf-8") as f:
                new_sql = f.read()

        return cls.diff_ddl_strings(old_sql, new_sql, rename_hints=rename_hints)

    @classmethod
    def diff_git_ref(
        cls,
        file_path: str = "db/movies.sql",
        base_ref: str = "HEAD~1",
        cwd: str = ".",
        rename_hints: dict[str, dict[str, str]] | None = None,
    ) -> SchemaDiffResult:
        """
        Compare current working copy of a DDL file against a git commit/ref (e.g., HEAD~1 or origin/main).
        Falls back to empty prior schema if file or ref does not exist.
        """
        old_sql = ""
        try:
            cmd = ["git", "show", f"{base_ref}:{file_path}"]
            res = subprocess.run(
                cmd,
                cwd=cwd,
                capture_output=True,
                text=True,
                check=False,
            )
            if res.returncode == 0:
                old_sql = res.stdout
        except Exception:
            old_sql = ""

        full_new_path = os.path.join(cwd, file_path) if not os.path.isabs(file_path) else file_path
        new_sql = ""
        if os.path.exists(full_new_path):
            with open(full_new_path, "r", encoding="utf-8") as f:
                new_sql = f.read()

        return cls.diff_ddl_strings(old_sql, new_sql, rename_hints=rename_hints)

    @classmethod
    def diff_schemas(
        cls,
        old_tables: dict[str, TableSchema],
        new_tables: dict[str, TableSchema],
        rename_hints: dict[str, dict[str, str]] | None = None,
    ) -> SchemaDiffResult:
        """
        Compare two sets of parsed TableSchema objects.
        Detects NEW_TABLE, DROP_TABLE, ADD_COLUMN, DROP_COLUMN, RENAME_COLUMN, TYPE_CHANGE, and CONSTRAINT_CHANGE.
        """
        rename_hints = rename_hints or {}
        tables_added: list[TableDiff] = []
        tables_dropped: list[TableDiff] = []
        tables_modified: list[TableDiff] = []

        old_names = set(old_tables.keys())
        new_names = set(new_tables.keys())

        # Tables added
        for tbl_name in sorted(new_names - old_names):
            tbl = new_tables[tbl_name]
            col_diffs = [
                ColumnDiff(
                    change_category=ChangeCategory.ADD_COLUMN,
                    table_name=tbl_name,
                    column_name=col.name,
                    new_type=col.data_type,
                    new_constraints=col.constraints,
                    details=f"Column '{col.name}' ({col.data_type}) created with new table '{tbl_name}'",
                )
                for col in tbl.columns.values()
            ]
            tables_added.append(
                TableDiff(
                    change_category=ChangeCategory.NEW_TABLE,
                    table_name=tbl_name,
                    new_table=tbl,
                    column_diffs=col_diffs,
                    details=f"New table '{tbl_name}' added with {len(tbl.columns)} columns",
                )
            )

        # Tables dropped
        for tbl_name in sorted(old_names - new_names):
            tbl = old_tables[tbl_name]
            col_diffs = [
                ColumnDiff(
                    change_category=ChangeCategory.DROP_COLUMN,
                    table_name=tbl_name,
                    column_name=col.name,
                    old_type=col.data_type,
                    old_constraints=col.constraints,
                    details=f"Column '{col.name}' dropped with deleted table '{tbl_name}'",
                )
                for col in tbl.columns.values()
            ]
            tables_dropped.append(
                TableDiff(
                    change_category=ChangeCategory.DROP_TABLE,
                    table_name=tbl_name,
                    old_table=tbl,
                    column_diffs=col_diffs,
                    details=f"Table '{tbl_name}' dropped",
                )
            )

        # Common tables - compare columns
        for tbl_name in sorted(old_names & new_names):
            old_table = old_tables[tbl_name]
            new_table = new_tables[tbl_name]

            tbl_hints = rename_hints.get(tbl_name, {})
            column_diffs = cls._diff_table_columns(tbl_name, old_table, new_table, tbl_hints)

            if column_diffs:
                tables_modified.append(
                    TableDiff(
                        change_category=ChangeCategory.ADD_COLUMN
                        if all(c.change_category == ChangeCategory.ADD_COLUMN for c in column_diffs)
                        else ChangeCategory.DROP_COLUMN
                        if all(c.change_category == ChangeCategory.DROP_COLUMN for c in column_diffs)
                        else ChangeCategory.TYPE_CHANGE,
                        table_name=tbl_name,
                        old_table=old_table,
                        new_table=new_table,
                        column_diffs=column_diffs,
                        details=f"Table '{tbl_name}' schema modified ({len(column_diffs)} column change(s))",
                    )
                )

        return SchemaDiffResult(
            tables_added=tables_added,
            tables_dropped=tables_dropped,
            tables_modified=tables_modified,
        )

    @classmethod
    def _diff_table_columns(
        cls,
        table_name: str,
        old_table: TableSchema,
        new_table: TableSchema,
        rename_hints: dict[str, str],
    ) -> list[ColumnDiff]:
        """Compare columns of two table versions and categorize column differences."""
        diffs: list[ColumnDiff] = []

        old_cols = dict(old_table.columns)
        new_cols = dict(new_table.columns)

        matched_old: set[str] = set()
        matched_new: set[str] = set()

        # 1. Process explicit rename hints first (old_col -> new_col)
        for old_name, new_name in rename_hints.items():
            if old_name in old_cols and new_name in new_cols:
                old_c = old_cols[old_name]
                new_c = new_cols[new_name]
                matched_old.add(old_name)
                matched_new.add(new_name)

                type_changed = old_c.normalized_type() != new_c.normalized_type()
                details = f"Renamed column '{old_name}' -> '{new_name}'"
                if type_changed:
                    details += f" with type change ({old_c.data_type} -> {new_c.data_type})"

                diffs.append(
                    ColumnDiff(
                        change_category=ChangeCategory.RENAME_COLUMN,
                        table_name=table_name,
                        column_name=new_name,
                        old_column_name=old_name,
                        old_type=old_c.data_type,
                        new_type=new_c.data_type,
                        old_constraints=old_c.constraints,
                        new_constraints=new_c.constraints,
                        details=details,
                    )
                )

        # 2. Match exact column names
        for col_name, old_c in old_cols.items():
            if col_name in matched_old:
                continue

            if col_name in new_cols and col_name not in matched_new:
                new_c = new_cols[col_name]
                matched_old.add(col_name)
                matched_new.add(col_name)

                # Check data type changes
                if old_c.normalized_type() != new_c.normalized_type():
                    diffs.append(
                        ColumnDiff(
                            change_category=ChangeCategory.TYPE_CHANGE,
                            table_name=table_name,
                            column_name=col_name,
                            old_column_name=col_name,
                            old_type=old_c.data_type,
                            new_type=new_c.data_type,
                            old_constraints=old_c.constraints,
                            new_constraints=new_c.constraints,
                            details=f"Data type altered for '{col_name}': {old_c.data_type} -> {new_c.data_type}",
                        )
                    )
                # Check constraints changes
                elif set(old_c.constraints) != set(new_c.constraints):
                    diffs.append(
                        ColumnDiff(
                            change_category=ChangeCategory.CONSTRAINT_CHANGE,
                            table_name=table_name,
                            column_name=col_name,
                            old_column_name=col_name,
                            old_type=old_c.data_type,
                            new_type=new_c.data_type,
                            old_constraints=old_c.constraints,
                            new_constraints=new_c.constraints,
                            details=f"Constraints updated for '{col_name}': {old_c.constraints} -> {new_c.constraints}",
                        )
                    )

        # 3. Check for heuristic rename detection between unmatched old and new columns
        unmatched_old = [name for name in old_cols if name not in matched_old]
        unmatched_new = [name for name in new_cols if name not in matched_new]

        # Heuristic: If exactly 1 unmatched old and 1 unmatched new with matching normalized data type
        if len(unmatched_old) == 1 and len(unmatched_new) == 1:
            old_name = unmatched_old[0]
            new_name = unmatched_new[0]
            old_c = old_cols[old_name]
            new_c = new_cols[new_name]

            if old_c.normalized_type() == new_c.normalized_type():
                matched_old.add(old_name)
                matched_new.add(new_name)
                diffs.append(
                    ColumnDiff(
                        change_category=ChangeCategory.RENAME_COLUMN,
                        table_name=table_name,
                        column_name=new_name,
                        old_column_name=old_name,
                        old_type=old_c.data_type,
                        new_type=new_c.data_type,
                        old_constraints=old_c.constraints,
                        new_constraints=new_c.constraints,
                        details=f"Heuristically detected rename: '{old_name}' -> '{new_name}' ({new_c.data_type})",
                    )
                )

        # 4. Remaining unmatched old columns are DROP_COLUMN
        for old_name in old_cols:
            if old_name not in matched_old:
                old_c = old_cols[old_name]
                diffs.append(
                    ColumnDiff(
                        change_category=ChangeCategory.DROP_COLUMN,
                        table_name=table_name,
                        column_name=old_name,
                        old_column_name=old_name,
                        old_type=old_c.data_type,
                        old_constraints=old_c.constraints,
                        details=f"Dropped column '{old_name}' ({old_c.data_type})",
                    )
                )

        # 5. Remaining unmatched new columns are ADD_COLUMN
        for new_name in new_cols:
            if new_name not in matched_new:
                new_c = new_cols[new_name]
                diffs.append(
                    ColumnDiff(
                        change_category=ChangeCategory.ADD_COLUMN,
                        table_name=table_name,
                        column_name=new_name,
                        new_type=new_c.data_type,
                        new_constraints=new_c.constraints,
                        details=f"Added new column '{new_name}' ({new_c.data_type})",
                    )
                )

        return diffs
