"""
tests/test_landing_dispatcher.py
---------------------------------
Unit tests for LandingIssueFormatter and LandingZoneDispatcher covering:
  - Issue title and body generation for modified/dropped tables (Scenario 1).
  - Issue title and body generation for new tables (Scenario 2).
  - Mandatory operational steps present in every generated issue body.
  - Issue publishing via GitHubClient in both real (mocked) and dry-run modes.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from github_bot.client import GitHubClient
from github_bot.config import GitHubConfig
from github_bot.landing_dispatcher import (
    DispatchContext,
    LandingIssueFormatter,
    LandingIssueResult,
    LandingZoneDispatcher,
)
from github_bot.schema_differ import (
    ChangeCategory,
    ColumnDiff,
    ColumnSchema,
    SchemaDiffResult,
    TableDiff,
    TableSchema,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

COMMIT_SHA = "abc1234567890def"
SHORT_SHA = "abc12345"


def _ctx(**kwargs) -> DispatchContext:
    defaults = dict(
        commit_sha=COMMIT_SHA,
        source_repo="movies_db",
        target_repo="movies_etl",
        mr_reference=None,
        changelog_path=None,
        owner="test-owner",
    )
    defaults.update(kwargs)
    return DispatchContext(**defaults)


def _modified_diff() -> SchemaDiffResult:
    """Scenario 1: titles_top_100 with a rename and a drop column."""
    col_rename = ColumnDiff(
        change_category=ChangeCategory.RENAME_COLUMN,
        table_name="titles_top_100",
        column_name="vote_count",
        old_column_name="No_of_Votes",
    )
    col_drop = ColumnDiff(
        change_category=ChangeCategory.DROP_COLUMN,
        table_name="titles_top_100",
        column_name="Poster_Link",
    )
    table_diff = TableDiff(
        change_category=ChangeCategory.RENAME_COLUMN,
        table_name="titles_top_100",
        column_diffs=[col_rename, col_drop],
    )
    return SchemaDiffResult(tables_modified=[table_diff])


def _new_table_diff() -> SchemaDiffResult:
    """Scenario 2: movie_reviews added as a new table."""
    schema = TableSchema(
        name="movie_reviews",
        columns={
            "review_id": ColumnSchema("review_id", "INTEGER", nullable=False, primary_key=True),
            "movie_id": ColumnSchema("movie_id", "INTEGER", nullable=False),
            "reviewer_name": ColumnSchema("reviewer_name", "VARCHAR(255)"),
            "rating": ColumnSchema("rating", "DECIMAL(3,1)"),
            "review_text": ColumnSchema("review_text", "TEXT"),
            "review_date": ColumnSchema("review_date", "DATE"),
        },
    )
    table_diff = TableDiff(
        change_category=ChangeCategory.NEW_TABLE,
        table_name="movie_reviews",
        new_table=schema,
    )
    return SchemaDiffResult(tables_added=[table_diff])


def _combined_diff() -> SchemaDiffResult:
    """Both a modified table and a new table in a single release."""
    modified = _modified_diff()
    new = _new_table_diff()
    return SchemaDiffResult(
        tables_added=new.tables_added,
        tables_modified=modified.tables_modified,
    )


def _empty_diff() -> SchemaDiffResult:
    return SchemaDiffResult()


# ---------------------------------------------------------------------------
# LandingIssueFormatter — Scenario 1 (Modified/Dropped Tables)
# ---------------------------------------------------------------------------


class TestFormatterModifiedTables:
    """Issue formatting for Scenario 1: rename and drop in existing table."""

    def setup_method(self):
        self.formatter = LandingIssueFormatter()
        self.diff = _modified_diff()
        self.ctx = _ctx()

    def test_title_contains_table_name_and_commit(self):
        title, _, _ = self.formatter.format_modified_tables_issue(self.diff, self.ctx)
        assert "titles_top_100" in title
        assert SHORT_SHA in title
        assert "[Landing Action Required]" in title
        assert "Schema Migration" in title

    def test_title_includes_source_repo(self):
        title, _, _ = self.formatter.format_modified_tables_issue(self.diff, self.ctx)
        assert "movies_db" in title

    def test_breaking_label_added_when_breaking(self):
        _, _, labels = self.formatter.format_modified_tables_issue(self.diff, self.ctx)
        assert "breaking-change" in labels

    def test_base_labels_always_present(self):
        _, _, labels = self.formatter.format_modified_tables_issue(self.diff, self.ctx)
        for lbl in ("landing-action", "schema-migration", "p1-high"):
            assert lbl in labels

    # -- Mandatory operational steps present --

    def test_step1_stop_pipeline_present(self):
        _, body, _ = self.formatter.format_modified_tables_issue(self.diff, self.ctx)
        assert "Stop" in body
        assert "pipeline" in body.lower() or "Pipeline" in body

    def test_step2_drop_table_present(self):
        _, body, _ = self.formatter.format_modified_tables_issue(self.diff, self.ctx)
        assert "DROP TABLE" in body
        assert "titles_top_100" in body

    def test_step3_reload_cdc_present(self):
        _, body, _ = self.formatter.format_modified_tables_issue(self.diff, self.ctx)
        assert "CDC" in body
        assert "Reload" in body or "reload" in body.lower()

    # -- Column changes in body --

    def test_rename_column_documented(self):
        _, body, _ = self.formatter.format_modified_tables_issue(self.diff, self.ctx)
        assert "No_of_Votes" in body
        assert "vote_count" in body

    def test_drop_column_documented(self):
        _, body, _ = self.formatter.format_modified_tables_issue(self.diff, self.ctx)
        assert "Poster_Link" in body

    # -- Traceability --

    def test_commit_sha_in_body(self):
        _, body, _ = self.formatter.format_modified_tables_issue(self.diff, self.ctx)
        assert SHORT_SHA in body

    def test_mr_reference_when_provided(self):
        ctx = _ctx(mr_reference="movies_db/pull/42")
        _, body, _ = self.formatter.format_modified_tables_issue(self.diff, ctx)
        assert "movies_db/pull/42" in body

    def test_changelog_path_when_provided(self):
        ctx = _ctx(changelog_path="changelogs/changelog-20240101-abc12345.md")
        _, body, _ = self.formatter.format_modified_tables_issue(self.diff, ctx)
        assert "changelogs/changelog-20240101-abc12345.md" in body

    def test_target_repo_in_body(self):
        _, body, _ = self.formatter.format_modified_tables_issue(self.diff, self.ctx)
        assert "movies_etl" in body


# ---------------------------------------------------------------------------
# LandingIssueFormatter — Scenario 2 (New Tables)
# ---------------------------------------------------------------------------


class TestFormatterNewTables:
    """Issue formatting for Scenario 2: new table onboarding."""

    def setup_method(self):
        self.formatter = LandingIssueFormatter()
        self.diff = _new_table_diff()
        self.ctx = _ctx()

    def test_title_contains_new_table_name_and_commit(self):
        title, _, _ = self.formatter.format_new_tables_issue(self.diff, self.ctx)
        assert "movie_reviews" in title
        assert SHORT_SHA in title
        assert "[Landing Action Required]" in title
        assert "New Table Onboarding" in title

    def test_base_labels_present(self):
        _, _, labels = self.formatter.format_new_tables_issue(self.diff, self.ctx)
        for lbl in ("landing-action", "schema-migration", "p1-high"):
            assert lbl in labels

    def test_no_breaking_change_label_for_new_table(self):
        _, _, labels = self.formatter.format_new_tables_issue(self.diff, self.ctx)
        assert "breaking-change" not in labels

    # -- Mandatory operational steps (Part-2) --

    def test_step1_initialize_cdc_stream(self):
        _, body, _ = self.formatter.format_new_tables_issue(self.diff, self.ctx)
        assert "CDC" in body
        assert "Initialize" in body or "stream" in body.lower()

    def test_step2_trigger_initial_full_load(self):
        _, body, _ = self.formatter.format_new_tables_issue(self.diff, self.ctx)
        assert "Full Load" in body or "initial" in body.lower()

    # -- Schema detail in body --

    def test_new_table_name_in_body(self):
        _, body, _ = self.formatter.format_new_tables_issue(self.diff, self.ctx)
        assert "movie_reviews" in body

    def test_column_names_in_schema_section(self):
        _, body, _ = self.formatter.format_new_tables_issue(self.diff, self.ctx)
        for col in ("review_id", "movie_id", "reviewer_name", "rating", "review_text", "review_date"):
            assert col in body

    def test_commit_sha_traceability(self):
        _, body, _ = self.formatter.format_new_tables_issue(self.diff, self.ctx)
        assert SHORT_SHA in body


# ---------------------------------------------------------------------------
# LandingIssueFormatter — Combined release
# ---------------------------------------------------------------------------


class TestFormatterCombined:
    def setup_method(self):
        self.formatter = LandingIssueFormatter()
        self.diff = _combined_diff()
        self.ctx = _ctx()

    def test_combined_title(self):
        title, _, _ = self.formatter.format_combined_issue(self.diff, self.ctx)
        assert "[Landing Action Required]" in title
        assert SHORT_SHA in title

    def test_combined_body_has_both_parts(self):
        _, body, _ = self.formatter.format_combined_issue(self.diff, self.ctx)
        # Part-1 content
        assert "DROP TABLE" in body
        # Part-2 content
        assert "New Table Onboarding" in body or "Initialize" in body

    def test_breaking_label_from_modified_table(self):
        _, _, labels = self.formatter.format_combined_issue(self.diff, self.ctx)
        assert "breaking-change" in labels


# ---------------------------------------------------------------------------
# LandingZoneDispatcher — Dispatch logic
# ---------------------------------------------------------------------------


class TestDispatcherRouting:
    """Verify dispatch() selects the correct formatter path."""

    def _make_dispatcher(self, dry_run=True) -> LandingZoneDispatcher:
        config = GitHubConfig(
            github_token="tok",
            github_owner="test-owner",
            movies_etl_repo="movies_etl",
        )
        return LandingZoneDispatcher(config=config, dry_run=dry_run)

    def test_no_results_for_empty_diff(self):
        dispatcher = self._make_dispatcher()
        results = dispatcher.dispatch(_empty_diff(), _ctx())
        assert results == []

    def test_one_result_for_modified_tables(self):
        dispatcher = self._make_dispatcher()
        results = dispatcher.dispatch(_modified_diff(), _ctx())
        assert len(results) == 1
        assert "Schema Migration" in results[0].title

    def test_one_result_for_new_table(self):
        dispatcher = self._make_dispatcher()
        results = dispatcher.dispatch(_new_table_diff(), _ctx())
        assert len(results) == 1
        assert "New Table Onboarding" in results[0].title

    def test_combined_issues_produce_single_result(self):
        dispatcher = self._make_dispatcher()
        results = dispatcher.dispatch(_combined_diff(), _ctx())
        assert len(results) == 1

    def test_dry_run_flag_propagated(self):
        dispatcher = self._make_dispatcher(dry_run=True)
        results = dispatcher.dispatch(_modified_diff(), _ctx())
        assert results[0].dry_run is True


# ---------------------------------------------------------------------------
# LandingZoneDispatcher — Issue publishing via GitHubClient (real & dry-run)
# ---------------------------------------------------------------------------


class TestDispatcherPublishing:
    """Verify GitHubClient.create_issue is called with correct arguments."""

    def _mock_client(self, issue_response: dict | None = None) -> GitHubClient:
        client = MagicMock(spec=GitHubClient)
        client.dry_run = False
        client.create_issue.return_value = issue_response or {
            "number": 55,
            "title": "mock-title",
            "body": "mock-body",
            "html_url": "https://github.com/test-owner/movies_etl/issues/55",
            "state": "open",
        }
        return client

    def test_create_issue_called_once_for_modified(self):
        client = self._mock_client()
        dispatcher = LandingZoneDispatcher(client=client, dry_run=False)
        results = dispatcher.dispatch(_modified_diff(), _ctx())

        client.create_issue.assert_called_once()
        call_kwargs = client.create_issue.call_args
        assert call_kwargs.kwargs["repo"] == "movies_etl" or call_kwargs.args[0] == "movies_etl"

    def test_result_captures_issue_number_and_url(self):
        client = self._mock_client()
        dispatcher = LandingZoneDispatcher(client=client, dry_run=False)
        results = dispatcher.dispatch(_modified_diff(), _ctx())

        assert results[0].issue_number == 55
        assert "issues/55" in results[0].issue_url

    def test_labels_passed_to_create_issue(self):
        client = self._mock_client()
        dispatcher = LandingZoneDispatcher(client=client, dry_run=False)
        dispatcher.dispatch(_modified_diff(), _ctx())

        call_kwargs = client.create_issue.call_args.kwargs
        labels = call_kwargs.get("labels", [])
        assert "landing-action" in labels
        assert "p1-high" in labels

    def test_dry_run_uses_client_dry_run_path(self):
        config = GitHubConfig(
            github_token="tok",
            github_owner="test-owner",
            movies_etl_repo="movies_etl",
        )
        dispatcher = LandingZoneDispatcher(config=config, dry_run=True)
        results = dispatcher.dispatch(_modified_diff(), _ctx())

        # Should complete without network errors
        assert len(results) == 1
        assert results[0].dry_run is True
        assert results[0].issue_number == 101  # GitHubClient dry-run stub value
        assert results[0].issue_url is not None

    def test_owner_from_context_used_when_provided(self):
        client = self._mock_client()
        dispatcher = LandingZoneDispatcher(client=client, dry_run=False)
        ctx = _ctx(owner="custom-owner")
        dispatcher.dispatch(_modified_diff(), ctx)

        call_kwargs = client.create_issue.call_args.kwargs
        assert call_kwargs.get("owner") == "custom-owner"

    def test_no_create_issue_call_for_empty_diff(self):
        client = self._mock_client()
        dispatcher = LandingZoneDispatcher(client=client, dry_run=False)
        dispatcher.dispatch(_empty_diff(), _ctx())

        client.create_issue.assert_not_called()


# ---------------------------------------------------------------------------
# DispatchContext helpers
# ---------------------------------------------------------------------------


class TestDispatchContext:
    def test_short_sha_truncation(self):
        ctx = DispatchContext(commit_sha="abc1234567890def")
        assert ctx.short_sha == "abc12345"

    def test_short_sha_when_shorter_than_8(self):
        ctx = DispatchContext(commit_sha="abc")
        assert ctx.short_sha == "abc"

    def test_commit_url_construction(self):
        ctx = _ctx()
        url = ctx.commit_url()
        assert "test-owner" in url
        assert "movies_db" in url
        assert COMMIT_SHA in url
