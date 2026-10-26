"""
tests/test_schema_differ.py
---------------------------
Unit tests for SQL DDL parser and schema differ engine.
"""

from unittest.mock import MagicMock, patch

import pytest

from github_bot.schema_differ import (
    ChangeCategory,
    ColumnDiff,
    ColumnSchema,
    SchemaDiffer,
    SchemaDiffResult,
    SQLDDLParser,
    TableDiff,
    TableSchema,
)


BASE_MOVIES_SQL = """
CREATE TABLE IF NOT EXISTS titles_top_100 (
    MovieId VARCHAR(255),
    Poster_Link TEXT,
    Series_Title VARCHAR(255),
    Released_Year BIGINT,
    Certificate VARCHAR(10),
    Runtime BIGINT,
    Genre VARCHAR(255),
    IMDB_Rating DOUBLE PRECISION,
    Overview TEXT,
    Meta_score BIGINT,
    Director VARCHAR(255),
    No_of_Votes BIGINT,
    Gross BIGINT
);
"""

SCENARIO_1_SQL = """
-- Scenario 1: Poster_Link dropped, No_of_Votes renamed to vote_count
CREATE TABLE IF NOT EXISTS titles_top_100 (
    MovieId VARCHAR(255),
    Series_Title VARCHAR(255),
    Released_Year BIGINT,
    Certificate VARCHAR(10),
    Runtime BIGINT,
    Genre VARCHAR(255),
    IMDB_Rating DOUBLE PRECISION,
    Overview TEXT,
    Meta_score BIGINT,
    Director VARCHAR(255),
    vote_count BIGINT,
    Gross BIGINT
);
"""

SCENARIO_2_SQL = """
CREATE TABLE IF NOT EXISTS titles_top_100 (
    MovieId VARCHAR(255),
    Poster_Link TEXT,
    Series_Title VARCHAR(255),
    Released_Year BIGINT,
    Certificate VARCHAR(10),
    Runtime BIGINT,
    Genre VARCHAR(255),
    IMDB_Rating DOUBLE PRECISION,
    Overview TEXT,
    Meta_score BIGINT,
    Director VARCHAR(255),
    No_of_Votes BIGINT,
    Gross BIGINT
);

CREATE TABLE IF NOT EXISTS movie_reviews (
    review_id VARCHAR(64) PRIMARY KEY,
    movie_id VARCHAR(255) NOT NULL,
    reviewer_name VARCHAR(255),
    rating DOUBLE PRECISION,
    review_text TEXT,
    review_date TIMESTAMP
);
"""


class TestSQLDDLParser:
    """Tests for SQLDDLParser."""

    def test_strip_comments(self) -> None:
        sql = """
        -- Line comment
        CREATE TABLE foo (
            /* Block comment */
            id INT -- inline comment
        );
        """
        stripped = SQLDDLParser.strip_comments(sql)
        assert "-- Line comment" not in stripped
        assert "/* Block comment */" not in stripped
        assert "inline comment" not in stripped
        assert "CREATE TABLE foo" in stripped
        assert "id INT" in stripped

    def test_parse_single_table(self) -> None:
        tables = SQLDDLParser.parse_ddl(BASE_MOVIES_SQL)
        assert "titles_top_100" in tables
        tbl = tables["titles_top_100"]
        assert len(tbl.columns) == 13
        assert tbl.columns["MovieId"].data_type == "VARCHAR(255)"
        assert tbl.columns["IMDB_Rating"].data_type == "DOUBLE PRECISION"
        assert tbl.columns["Poster_Link"].data_type == "TEXT"
        assert tbl.columns["No_of_Votes"].data_type == "BIGINT"

    def test_parse_multiple_tables_with_constraints(self) -> None:
        tables = SQLDDLParser.parse_ddl(SCENARIO_2_SQL)
        assert len(tables) == 2
        assert "titles_top_100" in tables
        assert "movie_reviews" in tables

        reviews = tables["movie_reviews"]
        assert len(reviews.columns) == 6
        assert reviews.columns["review_id"].primary_key is True
        assert reviews.columns["review_id"].nullable is False
        assert reviews.columns["movie_id"].nullable is False
        assert "review_id" in reviews.primary_keys

    def test_parse_table_level_primary_key(self) -> None:
        sql = """
        CREATE TABLE user_roles (
            user_id VARCHAR(64),
            role_id VARCHAR(64),
            assigned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user_id, role_id)
        );
        """
        tables = SQLDDLParser.parse_ddl(sql)
        assert "user_roles" in tables
        tbl = tables["user_roles"]
        assert tbl.primary_keys == ["user_id", "role_id"]
        assert tbl.columns["assigned_at"].default_value == "CURRENT_TIMESTAMP"

    def test_apply_alter_table_statements(self) -> None:
        sql = """
        CREATE TABLE items (
            item_id INT,
            name VARCHAR(50)
        );
        ALTER TABLE items ADD COLUMN price DECIMAL(10, 2);
        ALTER TABLE items RENAME COLUMN name TO item_name;
        ALTER TABLE items DROP COLUMN item_id;
        """
        tables = SQLDDLParser.parse_ddl(sql)
        assert "items" in tables
        tbl = tables["items"]
        assert "item_id" not in tbl.columns
        assert "item_name" in tbl.columns
        assert "price" in tbl.columns
        assert tbl.columns["price"].data_type == "DECIMAL(10, 2)"


class TestSchemaDiffer:
    """Tests for SchemaDiffer change detection and categorization."""

    def test_scenario_1_column_rename_and_drop(self) -> None:
        """Scenario 1: No_of_Votes renamed to vote_count, Poster_Link dropped."""
        diff = SchemaDiffer.diff_ddl_strings(
            BASE_MOVIES_SQL,
            SCENARIO_1_SQL,
            rename_hints={"titles_top_100": {"No_of_Votes": "vote_count"}},
        )

        assert diff.has_changes is True
        assert diff.is_breaking is True
        assert diff.affected_table_names == ["titles_top_100"]
        assert len(diff.tables_added) == 0
        assert len(diff.tables_dropped) == 0
        assert len(diff.tables_modified) == 1

        mod_table = diff.tables_modified[0]
        assert mod_table.table_name == "titles_top_100"

        categories = {c.change_category for c in mod_table.column_diffs}
        assert ChangeCategory.RENAME_COLUMN in categories
        assert ChangeCategory.DROP_COLUMN in categories

        rename_diff = next(c for c in mod_table.column_diffs if c.change_category == ChangeCategory.RENAME_COLUMN)
        assert rename_diff.old_column_name == "No_of_Votes"
        assert rename_diff.column_name == "vote_count"

        drop_diff = next(c for c in mod_table.column_diffs if c.change_category == ChangeCategory.DROP_COLUMN)
        assert drop_diff.column_name == "Poster_Link"

    def test_scenario_2_new_table_addition(self) -> None:
        """Scenario 2: New table movie_reviews added."""
        diff = SchemaDiffer.diff_ddl_strings(BASE_MOVIES_SQL, SCENARIO_2_SQL)

        assert diff.has_changes is True
        assert diff.is_breaking is False
        assert diff.affected_table_names == ["movie_reviews"]
        assert len(diff.tables_added) == 1
        assert len(diff.tables_dropped) == 0
        assert len(diff.tables_modified) == 0

        new_tbl = diff.tables_added[0]
        assert new_tbl.table_name == "movie_reviews"
        assert len(new_tbl.column_diffs) == 6
        assert all(c.change_category == ChangeCategory.ADD_COLUMN for c in new_tbl.column_diffs)

    def test_drop_table_detection(self) -> None:
        """Test dropping an entire table."""
        diff = SchemaDiffer.diff_ddl_strings(SCENARIO_2_SQL, BASE_MOVIES_SQL)
        assert diff.has_changes is True
        assert diff.is_breaking is True
        assert len(diff.tables_dropped) == 1
        assert diff.tables_dropped[0].table_name == "movie_reviews"

    def test_column_type_change_detection(self) -> None:
        """Test changing a column data type."""
        old_sql = "CREATE TABLE metrics (id INT, score BIGINT);"
        new_sql = "CREATE TABLE metrics (id INT, score DOUBLE PRECISION);"

        diff = SchemaDiffer.diff_ddl_strings(old_sql, new_sql)
        assert diff.has_changes is True
        assert diff.is_breaking is True
        assert len(diff.tables_modified) == 1
        col_diff = diff.tables_modified[0].column_diffs[0]
        assert col_diff.change_category == ChangeCategory.TYPE_CHANGE
        assert col_diff.column_name == "score"
        assert col_diff.old_type == "BIGINT"
        assert col_diff.new_type == "DOUBLE PRECISION"

    def test_column_addition_detection(self) -> None:
        """Test adding a column to an existing table."""
        old_sql = "CREATE TABLE metrics (id INT);"
        new_sql = "CREATE TABLE metrics (id INT, tags VARCHAR(100));"

        diff = SchemaDiffer.diff_ddl_strings(old_sql, new_sql)
        assert diff.has_changes is True
        assert diff.is_breaking is False
        assert len(diff.tables_modified) == 1
        col_diff = diff.tables_modified[0].column_diffs[0]
        assert col_diff.change_category == ChangeCategory.ADD_COLUMN
        assert col_diff.column_name == "tags"

    def test_heuristic_column_rename(self) -> None:
        """Test heuristic detection when single column is replaced with same type."""
        old_sql = "CREATE TABLE users (id INT, full_name VARCHAR(100));"
        new_sql = "CREATE TABLE users (id INT, display_name VARCHAR(100));"

        diff = SchemaDiffer.diff_ddl_strings(old_sql, new_sql)
        assert diff.has_changes is True
        assert len(diff.tables_modified) == 1
        col_diff = diff.tables_modified[0].column_diffs[0]
        assert col_diff.change_category == ChangeCategory.RENAME_COLUMN
        assert col_diff.old_column_name == "full_name"
        assert col_diff.column_name == "display_name"

    def test_no_changes(self) -> None:
        """Test identical SQL strings produce no diff."""
        diff = SchemaDiffer.diff_ddl_strings(BASE_MOVIES_SQL, BASE_MOVIES_SQL)
        assert diff.has_changes is False
        assert diff.is_breaking is False
        assert len(diff.affected_table_names) == 0

    def test_diff_ddl_files(self, tmp_path) -> None:
        """Test diffing DDL files stored on disk."""
        old_file = tmp_path / "old.sql"
        new_file = tmp_path / "new.sql"

        old_file.write_text(BASE_MOVIES_SQL, encoding="utf-8")
        new_file.write_text(SCENARIO_2_SQL, encoding="utf-8")

        diff = SchemaDiffer.diff_ddl_files(str(old_file), str(new_file))
        assert diff.has_changes is True
        assert "movie_reviews" in diff.affected_table_names

    def test_diff_git_ref(self, tmp_path) -> None:
        """Test diffing against a git ref using mock."""
        target_file = tmp_path / "movies.sql"
        target_file.write_text(SCENARIO_1_SQL, encoding="utf-8")

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=BASE_MOVIES_SQL)
            diff = SchemaDiffer.diff_git_ref(
                file_path=str(target_file),
                base_ref="HEAD~1",
                cwd=str(tmp_path),
                rename_hints={"titles_top_100": {"No_of_Votes": "vote_count"}},
            )
            assert diff.has_changes is True
            assert diff.is_breaking is True
            assert "titles_top_100" in diff.affected_table_names
