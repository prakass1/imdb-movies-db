"""
tests/test_staging_adapter.py
------------------------------
Comprehensive unit tests for Sub-Task 4: Staging & Python Landing Table Adapter.

Covers:
  - PythonTableDefinitionAdapter: Python landing table modification (drops, renames),
    scaffolding for new tables.
  - CTEQueryAdapter: column renames, column drops, explicit CTE generation.
  - StagingPRFormatter: PR title / body content for column-change and
    new-table scenarios, traceability fields, issue cross-links.
  - StagingPRDispatcher: full dry-run dispatch for column changes,
    new tables, matching existing paths, GitHubClient call sequence.
"""

from __future__ import annotations

from unittest.mock import MagicMock, call, patch

import pytest

from github_bot.client import GitHubClient
from github_bot.config import GitHubConfig
from github_bot.schema_differ import (
    ChangeCategory,
    ColumnDiff,
    ColumnSchema,
    SchemaDiffResult,
    TableDiff,
    TableSchema,
)
from github_bot.staging_adapter import (
    AdaptedFile,
    CTEQueryAdapter,
    PythonTableDefinitionAdapter,
    StagingAdapterResult,
    StagingPRDispatcher,
    StagingPRFormatter,
    _LANDING_PY_BASE_PATH,
    _STAGING_SQL_BASE_PATH,
)


# ===========================================================================
# Shared test data helpers
# ===========================================================================

COMMIT_SHA = "abc1234567890def1234567890abcdef12345678"
SHORT_SHA = "abc12345"
OWNER = "test-owner"
SOURCE_REPO = "movies_db"
TARGET_REPO = "movies_etl"


def _rename_diff(table: str = "titles_top_100", old: str = "No_of_Votes", new: str = "vote_count") -> ColumnDiff:
    return ColumnDiff(
        change_category=ChangeCategory.RENAME_COLUMN,
        table_name=table,
        column_name=new,
        old_column_name=old,
        old_type="BIGINT",
        new_type="BIGINT",
        details=f"Renamed {old} -> {new}",
    )


def _drop_diff(table: str = "titles_top_100", col: str = "Poster_Link") -> ColumnDiff:
    return ColumnDiff(
        change_category=ChangeCategory.DROP_COLUMN,
        table_name=table,
        column_name=col,
        old_type="TEXT",
        details=f"Dropped column {col}",
    )


def _modified_diff_result() -> SchemaDiffResult:
    td = TableDiff(
        change_category=ChangeCategory.RENAME_COLUMN,
        table_name="titles_top_100",
        column_diffs=[
            _rename_diff("titles_top_100", "No_of_Votes", "vote_count"),
            _drop_diff("titles_top_100", "Poster_Link"),
        ],
    )
    return SchemaDiffResult(tables_modified=[td])


def _new_table_diff_result() -> SchemaDiffResult:
    schema = TableSchema(
        name="movie_reviews",
        columns={
            "review_id": ColumnSchema("review_id", "INTEGER", nullable=False, primary_key=True),
            "movie_id": ColumnSchema("movie_id", "INTEGER", nullable=False),
            "reviewer_name": ColumnSchema("reviewer_name", "VARCHAR(255)"),
            "rating": ColumnSchema("rating", "NUMERIC(3,1)"),
            "review_text": ColumnSchema("review_text", "TEXT"),
            "review_date": ColumnSchema("review_date", "DATE"),
        },
        primary_keys=["review_id"],
    )
    td = TableDiff(
        change_category=ChangeCategory.NEW_TABLE,
        table_name="movie_reviews",
        new_table=schema,
    )
    return SchemaDiffResult(tables_added=[td])


def _mock_client(dry_run: bool = True) -> MagicMock:
    client = MagicMock(spec=GitHubClient)
    client.dry_run = dry_run
    client.get_branch.return_value = {
        "name": "main",
        "commit": {"sha": "base1234567890abcdef1234567890abcdef12"},
    }
    client.create_branch.return_value = {
        "ref": "refs/heads/schema-sync/test",
        "object": {"sha": "base1234567890abcdef1234567890abcdef12"},
    }
    client.create_or_update_file.return_value = {
        "content": {"name": "test.sql", "path": "test.sql", "sha": "file1234"},
        "commit": {"sha": "commit1234", "message": "chore: sync"},
    }
    client.create_pull_request.return_value = {
        "number": 202,
        "html_url": "https://github.com/test-owner/imdb-movies-etl/pull/202",
        "state": "open",
        "draft": True,
        "title": "Test PR",
    }
    return client


# ===========================================================================
# PythonTableDefinitionAdapter tests
# ===========================================================================


class TestPythonTableDefinitionAdapter:
    """Tests for Python landing table adaptation and scaffolding."""

    def setup_method(self):
        self.adapter = PythonTableDefinitionAdapter()

    def test_adapt_python_file_drops_column(self):
        existing_py = (
            "from dataclasses import dataclass\n"
            "from typing import Optional\n\n"
            "@dataclass\n"
            "class TitlesLandingTable:\n"
            "    movie_id: Optional[str] = None\n"
            "    poster_link: Optional[str] = None\n"
            "    series_title: Optional[str] = None\n"
        )
        diff = TableDiff(
            change_category=ChangeCategory.DROP_COLUMN,
            table_name="titles_top_100",
            column_diffs=[_drop_diff("titles_top_100", "poster_link")],
        )
        result = self.adapter.adapt_python_file(existing_py, diff)
        assert "poster_link" not in result
        assert "movie_id" in result
        assert "series_title" in result

    def test_adapt_python_file_renames_column(self):
        existing_py = (
            "@dataclass\n"
            "class TitlesLandingTable:\n"
            "    no_of_votes: Optional[int] = None\n"
        )
        diff = TableDiff(
            change_category=ChangeCategory.RENAME_COLUMN,
            table_name="titles_top_100",
            column_diffs=[_rename_diff("titles_top_100", "no_of_votes", "vote_count")],
        )
        result = self.adapter.adapt_python_file(existing_py, diff)
        assert "vote_count" in result
        assert "no_of_votes" not in result

    def test_scaffold_python_file_clean_fields(self):
        cols = [
            ColumnSchema("review_id", "INTEGER", nullable=False, primary_key=True),
            ColumnSchema("rating", "NUMERIC(3,1)"),
            ColumnSchema("reviewer_name", "VARCHAR(255)"),
        ]
        result = self.adapter.scaffold_python_file("movie_reviews", cols)
        assert "class MovieReviewsLandingTable:" in result
        assert "review_id: Optional[int] = None" in result
        assert "rating: Optional[float] = None" in result
        assert "reviewer_name: Optional[str] = None" in result
        # No artificial cdc or op columns
        assert "_cdc_op" not in result
        assert "_cdc_ts" not in result


# ===========================================================================
# CTEQueryAdapter tests
# ===========================================================================


class TestCTEQueryAdapter:
    """Tests for SQL CTE transformations."""

    def setup_method(self):
        self.adapter = CTEQueryAdapter()

    def test_rename_column_in_projection(self):
        sql = "SELECT movie_id, no_of_votes, rating FROM source"
        result = self.adapter.adapt(sql, [_rename_diff("titles", "no_of_votes", "vote_count")])
        assert "vote_count AS no_of_votes" in result

    def test_drop_column_removes_line(self):
        sql = "SELECT\n    movie_id,\n    poster_link,\n    rating\nFROM source"
        result = self.adapter.adapt(sql, [_drop_diff("titles", "poster_link")])
        assert "poster_link" not in result
        assert "movie_id" in result
        assert "rating" in result

    def test_build_staging_cte(self):
        cols = [
            ColumnSchema("movie_id", "VARCHAR(255)"),
            ColumnSchema("title", "VARCHAR(255)"),
        ]
        cte = self.adapter.build_staging_cte("titles", cols)
        assert "WITH" in cte
        assert "source AS (" in cte
        assert "renamed AS (" in cte
        assert "SELECT" in cte
        assert "movie_id" in cte
        assert "title" in cte
        assert "SELECT *" not in cte


# ===========================================================================
# StagingPRFormatter tests
# ===========================================================================


class TestStagingPRFormatter:
    """Tests for PR title and body generation."""

    def setup_method(self):
        self.formatter = StagingPRFormatter()

    def test_column_change_pr_title_and_body(self):
        diff = _modified_diff_result()
        files = [
            AdaptedFile("movies_etl/db/ldg/titles.py", "content", False, "Updated Python landing def"),
            AdaptedFile("movies_etl/db/stg/titles.sql", "content", False, "Adapted staging CTE"),
        ]
        title, body = self.formatter.format_column_change_pr(
            diff, files, COMMIT_SHA, SOURCE_REPO, 101, OWNER,
        )
        assert "titles_top_100" in title
        assert "movies_etl/db/ldg/titles.py" in body
        assert "movies_etl/db/stg/titles.sql" in body
        assert "#101" in body

    def test_new_table_pr_title_and_body(self):
        diff = _new_table_diff_result()
        files = [
            AdaptedFile("movies_etl/db/ldg/movie_reviews.py", "content", True, "New Python landing def"),
            AdaptedFile("movies_etl/db/stg/movie_reviews.sql", "content", True, "New staging CTE"),
        ]
        title, body = self.formatter.format_new_table_pr(
            diff, files, COMMIT_SHA, SOURCE_REPO, 102, OWNER,
        )
        assert "movie_reviews" in title
        assert "movies_etl/db/ldg/movie_reviews.py" in body
        assert "movies_etl/db/stg/movie_reviews.sql" in body


# ===========================================================================
# StagingPRDispatcher tests
# ===========================================================================


class TestStagingPRDispatcher:
    """Tests for StagingPRDispatcher orchestration."""

    def setup_method(self):
        self.client = _mock_client(dry_run=True)
        self.dispatcher = StagingPRDispatcher(client=self.client, dry_run=True)

    def test_dispatch_column_changes_dry_run(self):
        diff = _modified_diff_result()
        existing_files = {
            "movies_etl/db/ldg/titles.py": "@dataclass\nclass TitlesLandingTable:\n    poster_link: Optional[str] = None\n    no_of_votes: Optional[int] = None\n",
            "movies_etl/db/stg/titles.sql": "SELECT\n    poster_link,\n    no_of_votes\nFROM ldg.titles\n",
        }
        results = self.dispatcher.dispatch(
            diff, commit_sha=COMMIT_SHA, owner=OWNER, existing_staging_files=existing_files, landing_issue_number=42,
        )
        assert len(results) == 1
        res = results[0]
        assert "titles-top-100" in res.branch_name
        assert len(res.adapted_files) == 2
        paths = [f.path for f in res.adapted_files]
        assert "movies_etl/db/ldg/titles.py" in paths
        assert "movies_etl/db/stg/titles.sql" in paths

    def test_dispatch_new_table_dry_run(self):
        diff = _new_table_diff_result()
        results = self.dispatcher.dispatch(
            diff, commit_sha=COMMIT_SHA, owner=OWNER, landing_issue_number=43,
        )
        assert len(results) == 1
        res = results[0]
        assert "new-table-movie-reviews" in res.branch_name
        assert len(res.adapted_files) == 2
        paths = [f.path for f in res.adapted_files]
        assert "movies_etl/db/ldg/movie_reviews.py" in paths
        assert "movies_etl/db/stg/movie_reviews.sql" in paths

    def test_no_changes_early_exit(self):
        empty_diff = SchemaDiffResult()
        results = self.dispatcher.dispatch(empty_diff, commit_sha=COMMIT_SHA)
        assert results == []
