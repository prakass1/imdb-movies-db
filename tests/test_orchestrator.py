"""
tests/test_orchestrator.py
--------------------------
End-to-end integration tests for the SyncOrchestrator and sync_bot CLI.

Covers:
  - Full orchestration flow in dry-run mode (Scenario 1 & 2).
  - simulate_scenario_1 / simulate_scenario_2 built-in shortcuts.
  - run_from_files with an explicit prior SQL file.
  - run_from_files with a git base_ref (mocked subprocess).
  - CLI argument parsing and exit codes via sync_bot.main().
  - No-change path (empty diff).
  - Error resilience: landing dispatch failure does not abort staging step.
"""

from __future__ import annotations

import os
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from github_bot.client import GitHubClient
from github_bot.config import GitHubConfig
from github_bot.orchestrator import OrchestratorResult, SyncOrchestrator, _BASE_DDL, _SCENARIO_1_DDL, _SCENARIO_2_DDL, _SCENARIO_1_RENAME_HINTS
from github_bot.schema_differ import SchemaDiffResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_COMMIT_SHA_1 = "aabb1122ccdd3344"
_COMMIT_SHA_2 = "eeff5566aabb7788"


def _make_orchestrator(tmp_path, dry_run: bool = True) -> SyncOrchestrator:
    """Create a SyncOrchestrator with dry_run=True and a temp changelogs dir."""
    config = GitHubConfig(
        github_token="test-token",
        github_owner="test-owner",
        movies_db_repo="movies_db",
        movies_etl_repo="movies_etl",
    )
    return SyncOrchestrator(
        config=config,
        dry_run=dry_run,
        changelogs_dir=str(tmp_path / "changelogs"),
        owner="test-owner",
    )


# ---------------------------------------------------------------------------
# Scenario 1 — column rename & drop
# ---------------------------------------------------------------------------


class TestScenario1DryRun:
    def test_run_returns_result(self, tmp_path) -> None:
        orch = _make_orchestrator(tmp_path)
        result = orch.run(
            prior_sql=_BASE_DDL,
            target_sql=_SCENARIO_1_DDL,
            commit_sha=_COMMIT_SHA_1,
            rename_hints=_SCENARIO_1_RENAME_HINTS,
        )
        assert isinstance(result, OrchestratorResult)
        assert result.commit_sha == _COMMIT_SHA_1

    def test_diff_detects_breaking_changes(self, tmp_path) -> None:
        orch = _make_orchestrator(tmp_path)
        result = orch.run(
            prior_sql=_BASE_DDL,
            target_sql=_SCENARIO_1_DDL,
            commit_sha=_COMMIT_SHA_1,
            rename_hints=_SCENARIO_1_RENAME_HINTS,
        )
        assert result.diff_result.has_changes
        assert result.diff_result.is_breaking

    def test_changelog_written(self, tmp_path) -> None:
        orch = _make_orchestrator(tmp_path)
        result = orch.run(
            prior_sql=_BASE_DDL,
            target_sql=_SCENARIO_1_DDL,
            commit_sha=_COMMIT_SHA_1,
            rename_hints=_SCENARIO_1_RENAME_HINTS,
        )
        assert result.changelog_path is not None
        assert os.path.exists(result.changelog_path)
        content = result.changelog_content or ""
        assert "vote_count" in content
        assert "Poster_Link" in content
        assert "BREAKING" in content

    def test_landing_issue_simulated(self, tmp_path) -> None:
        orch = _make_orchestrator(tmp_path)
        result = orch.run(
            prior_sql=_BASE_DDL,
            target_sql=_SCENARIO_1_DDL,
            commit_sha=_COMMIT_SHA_1,
            rename_hints=_SCENARIO_1_RENAME_HINTS,
        )
        assert len(result.landing_issues) == 1
        issue = result.landing_issues[0]
        assert issue.dry_run is True
        assert "titles_top_100" in issue.title
        assert issue.issue_number is not None

    def test_staging_pr_simulated(self, tmp_path) -> None:
        orch = _make_orchestrator(tmp_path)
        result = orch.run(
            prior_sql=_BASE_DDL,
            target_sql=_SCENARIO_1_DDL,
            commit_sha=_COMMIT_SHA_1,
            rename_hints=_SCENARIO_1_RENAME_HINTS,
        )
        assert len(result.staging_prs) >= 1
        pr = result.staging_prs[0]
        assert pr.dry_run is True
        assert "titles-top-100" in pr.branch_name or "schema-sync" in pr.branch_name

    def test_no_errors(self, tmp_path) -> None:
        orch = _make_orchestrator(tmp_path)
        result = orch.run(
            prior_sql=_BASE_DDL,
            target_sql=_SCENARIO_1_DDL,
            commit_sha=_COMMIT_SHA_1,
            rename_hints=_SCENARIO_1_RENAME_HINTS,
        )
        assert result.success
        assert result.errors == []

    def test_simulate_scenario_1_shortcut(self, tmp_path) -> None:
        """simulate_scenario_1() is equivalent to a manual Scenario 1 run."""
        orch = _make_orchestrator(tmp_path)
        result = orch.simulate_scenario_1(commit_sha=_COMMIT_SHA_1)
        assert result.diff_result.has_changes
        assert result.diff_result.is_breaking
        assert result.changelog_path is not None
        assert len(result.landing_issues) >= 1
        assert len(result.staging_prs) >= 1


# ---------------------------------------------------------------------------
# Scenario 2 — new table
# ---------------------------------------------------------------------------


class TestScenario2DryRun:
    def test_diff_detects_new_table(self, tmp_path) -> None:
        orch = _make_orchestrator(tmp_path)
        result = orch.run(
            prior_sql=_BASE_DDL,
            target_sql=_SCENARIO_2_DDL,
            commit_sha=_COMMIT_SHA_2,
        )
        assert result.diff_result.has_changes
        assert not result.diff_result.is_breaking
        assert any(t.table_name == "movie_reviews" for t in result.diff_result.tables_added)

    def test_changelog_mentions_new_table(self, tmp_path) -> None:
        orch = _make_orchestrator(tmp_path)
        result = orch.run(
            prior_sql=_BASE_DDL,
            target_sql=_SCENARIO_2_DDL,
            commit_sha=_COMMIT_SHA_2,
        )
        content = result.changelog_content or ""
        assert "movie_reviews" in content
        assert "New Tables" in content

    def test_landing_issue_new_table(self, tmp_path) -> None:
        orch = _make_orchestrator(tmp_path)
        result = orch.run(
            prior_sql=_BASE_DDL,
            target_sql=_SCENARIO_2_DDL,
            commit_sha=_COMMIT_SHA_2,
        )
        assert len(result.landing_issues) >= 1
        titles = [i.title for i in result.landing_issues]
        assert any("movie_reviews" in t for t in titles)

    def test_staging_pr_new_table_branch(self, tmp_path) -> None:
        orch = _make_orchestrator(tmp_path)
        result = orch.run(
            prior_sql=_BASE_DDL,
            target_sql=_SCENARIO_2_DDL,
            commit_sha=_COMMIT_SHA_2,
        )
        assert len(result.staging_prs) >= 1
        branch_names = [pr.branch_name for pr in result.staging_prs]
        assert any("movie-reviews" in b or "new-table" in b for b in branch_names)

    def test_simulate_scenario_2_shortcut(self, tmp_path) -> None:
        orch = _make_orchestrator(tmp_path)
        result = orch.simulate_scenario_2(commit_sha=_COMMIT_SHA_2)
        assert result.diff_result.has_changes
        assert any(t.table_name == "movie_reviews" for t in result.diff_result.tables_added)
        assert result.changelog_path is not None
        assert result.success


# ---------------------------------------------------------------------------
# No-change path
# ---------------------------------------------------------------------------


class TestNoChanges:
    def test_identical_ddl_returns_no_artefacts(self, tmp_path) -> None:
        orch = _make_orchestrator(tmp_path)
        result = orch.run(
            prior_sql=_BASE_DDL,
            target_sql=_BASE_DDL,
            commit_sha="identical123",
        )
        assert not result.diff_result.has_changes
        assert result.changelog_path is None
        assert result.landing_issues == []
        assert result.staging_prs == []
        assert result.success

    def test_empty_prior_sql_treats_all_as_new(self, tmp_path) -> None:
        orch = _make_orchestrator(tmp_path)
        result = orch.run(
            prior_sql="",
            target_sql=_BASE_DDL,
            commit_sha="init001",
        )
        assert result.diff_result.has_changes
        assert not result.diff_result.is_breaking


# ---------------------------------------------------------------------------
# run_from_files — explicit prior SQL file
# ---------------------------------------------------------------------------


class TestRunFromFiles:
    def test_explicit_prior_sql_file(self, tmp_path) -> None:
        prior_file = tmp_path / "prior.sql"
        prior_file.write_text(_BASE_DDL, encoding="utf-8")

        target_file = tmp_path / "movies.sql"
        target_file.write_text(_SCENARIO_1_DDL, encoding="utf-8")

        orch = _make_orchestrator(tmp_path)
        result = orch.run_from_files(
            target_sql_path=str(target_file),
            prior_sql_path=str(prior_file),
            commit_sha=_COMMIT_SHA_1,
            rename_hints=_SCENARIO_1_RENAME_HINTS,
        )
        assert result.diff_result.has_changes
        assert result.success

    def test_base_ref_git_show_called(self, tmp_path) -> None:
        """Verify _git_show is called when base_ref is provided."""
        target_file = tmp_path / "movies.sql"
        target_file.write_text(_SCENARIO_1_DDL, encoding="utf-8")

        orch = _make_orchestrator(tmp_path)
        with patch.object(
            SyncOrchestrator,
            "_git_show",
            return_value=_BASE_DDL,
        ) as mock_show:
            result = orch.run_from_files(
                target_sql_path=str(target_file),
                base_ref="HEAD~1",
                commit_sha=_COMMIT_SHA_1,
                rename_hints=_SCENARIO_1_RENAME_HINTS,
            )
        mock_show.assert_called_once_with("HEAD~1", str(target_file))
        assert result.diff_result.has_changes

    def test_neither_prior_nor_base_ref_warns(self, tmp_path, caplog) -> None:
        """When no prior source given, all tables are treated as new."""
        target_file = tmp_path / "movies.sql"
        target_file.write_text(_BASE_DDL, encoding="utf-8")

        orch = _make_orchestrator(tmp_path)
        import logging
        with caplog.at_level(logging.WARNING):
            result = orch.run_from_files(
                target_sql_path=str(target_file),
                commit_sha="fallback001",
            )
        assert "No prior SQL reference" in caplog.text
        assert result.diff_result.has_changes


# ---------------------------------------------------------------------------
# Git helper unit tests
# ---------------------------------------------------------------------------


class TestGitHelpers:
    def test_git_show_returns_stdout_on_success(self, tmp_path) -> None:
        fake_ddl = "CREATE TABLE foo (id INT);"
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = fake_ddl

        with patch("subprocess.run", return_value=mock_result):
            out = SyncOrchestrator._git_show("HEAD~1", "db/movies.sql")
        assert out == fake_ddl

    def test_git_show_returns_empty_on_failure(self) -> None:
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""

        with patch("subprocess.run", return_value=mock_result):
            out = SyncOrchestrator._git_show("HEAD~1", "db/movies.sql")
        assert out == ""

    def test_git_show_handles_exception(self) -> None:
        with patch("subprocess.run", side_effect=OSError("git not found")):
            out = SyncOrchestrator._git_show("HEAD~1", "db/movies.sql")
        assert out == ""

    def test_git_current_sha_returns_sha(self) -> None:
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "abc1234567890abcdef\n"

        with patch("subprocess.run", return_value=mock_result):
            sha = SyncOrchestrator._git_current_sha()
        assert sha == "abc1234567890abcdef"

    def test_git_current_sha_returns_none_on_failure(self) -> None:
        with patch("subprocess.run", side_effect=OSError("no git")):
            sha = SyncOrchestrator._git_current_sha()
        assert sha is None


# ---------------------------------------------------------------------------
# Error resilience
# ---------------------------------------------------------------------------


class TestErrorResilience:
    def test_landing_dispatch_failure_recorded_in_errors(self, tmp_path) -> None:
        """If landing dispatch raises, error is captured and staging still runs."""
        orch = _make_orchestrator(tmp_path)
        with patch.object(
            orch._landing_dispatcher,
            "dispatch",
            side_effect=RuntimeError("simulated network error"),
        ):
            result = orch.run(
                prior_sql=_BASE_DDL,
                target_sql=_SCENARIO_1_DDL,
                commit_sha=_COMMIT_SHA_1,
                rename_hints=_SCENARIO_1_RENAME_HINTS,
            )
        assert not result.success
        assert any("Landing issue dispatch failed" in e for e in result.errors)
        # Staging should still have been attempted
        assert len(result.staging_prs) >= 1

    def test_staging_dispatch_failure_recorded(self, tmp_path) -> None:
        orch = _make_orchestrator(tmp_path)
        with patch.object(
            orch._staging_dispatcher,
            "dispatch",
            side_effect=RuntimeError("simulated staging error"),
        ):
            result = orch.run(
                prior_sql=_BASE_DDL,
                target_sql=_SCENARIO_2_DDL,
                commit_sha=_COMMIT_SHA_2,
            )
        assert not result.success
        assert any("Staging PR dispatch failed" in e for e in result.errors)

    def test_diff_failure_returns_early(self, tmp_path) -> None:
        orch = _make_orchestrator(tmp_path)
        with patch.object(
            orch._changelog_gen,
            "write_changelog",
            side_effect=OSError("disk full"),
        ):
            result = orch.run(
                prior_sql=_BASE_DDL,
                target_sql=_SCENARIO_1_DDL,
                commit_sha=_COMMIT_SHA_1,
                rename_hints=_SCENARIO_1_RENAME_HINTS,
            )
        # Diff succeeded (has_changes) but changelog failed
        assert result.diff_result.has_changes
        assert any("Changelog generation failed" in e for e in result.errors)


# ---------------------------------------------------------------------------
# OrchestratorResult dataclass
# ---------------------------------------------------------------------------


class TestOrchestratorResult:
    def test_success_true_when_no_errors(self, tmp_path) -> None:
        orch = _make_orchestrator(tmp_path)
        result = orch.simulate_scenario_1(commit_sha="abc")
        assert result.success

    def test_success_false_when_errors(self) -> None:
        result = OrchestratorResult(
            commit_sha="xyz",
            diff_result=SchemaDiffResult(),
            errors=["something went wrong"],
        )
        assert not result.success

    def test_summary_contains_commit_sha(self, tmp_path) -> None:
        orch = _make_orchestrator(tmp_path)
        result = orch.simulate_scenario_1(commit_sha="deadbeef1234")
        summary = result.summary()
        assert "deadbeef" in summary

    def test_summary_mentions_dry_run(self, tmp_path) -> None:
        orch = _make_orchestrator(tmp_path, dry_run=True)
        result = orch.simulate_scenario_2(commit_sha="cafebabe5678")
        summary = result.summary()
        assert "True" in summary


# ---------------------------------------------------------------------------
# CLI — sync_bot.main()
# ---------------------------------------------------------------------------


class TestCLI:
    def test_simulate_scenario_1_exit_zero(self, tmp_path) -> None:
        """--simulate-scenario-1 --dry-run should exit 0."""
        with patch("sync_bot.SyncOrchestrator") as MockOrch:
            instance = MockOrch.return_value
            mock_result = MagicMock(spec=OrchestratorResult)
            mock_result.success = True
            mock_result.errors = []
            mock_result.summary.return_value = "OK"
            instance.simulate_scenario_1.return_value = mock_result

            from sync_bot import main
            exit_code = main(["--simulate-scenario-1", "--dry-run"])
        assert exit_code == 0

    def test_simulate_scenario_2_exit_zero(self, tmp_path) -> None:
        with patch("sync_bot.SyncOrchestrator") as MockOrch:
            instance = MockOrch.return_value
            mock_result = MagicMock(spec=OrchestratorResult)
            mock_result.success = True
            mock_result.errors = []
            mock_result.summary.return_value = "OK"
            instance.simulate_scenario_2.return_value = mock_result

            from sync_bot import main
            exit_code = main(["--simulate-scenario-2", "--dry-run"])
        assert exit_code == 0

    def test_exit_nonzero_on_error(self) -> None:
        with patch("sync_bot.SyncOrchestrator") as MockOrch:
            instance = MockOrch.return_value
            mock_result = MagicMock(spec=OrchestratorResult)
            mock_result.success = False
            mock_result.errors = ["Something broke"]
            mock_result.summary.return_value = "FAILED"
            instance.simulate_scenario_1.return_value = mock_result

            from sync_bot import main
            exit_code = main(["--simulate-scenario-1", "--dry-run"])
        assert exit_code == 1

    def test_prior_sql_and_target_sql_flags(self, tmp_path) -> None:
        prior_file = tmp_path / "prior.sql"
        prior_file.write_text(_BASE_DDL, encoding="utf-8")
        target_file = tmp_path / "target.sql"
        target_file.write_text(_SCENARIO_2_DDL, encoding="utf-8")

        with patch("sync_bot.SyncOrchestrator") as MockOrch:
            instance = MockOrch.return_value
            mock_result = MagicMock(spec=OrchestratorResult)
            mock_result.success = True
            mock_result.errors = []
            mock_result.summary.return_value = "OK"
            instance.run_from_files.return_value = mock_result

            from sync_bot import main
            exit_code = main([
                "--prior-sql", str(prior_file),
                "--target-sql", str(target_file),
                "--dry-run",
            ])
        assert exit_code == 0
        instance.run_from_files.assert_called_once()

    def test_base_ref_flag_triggers_run_from_files(self, tmp_path) -> None:
        target_file = tmp_path / "movies.sql"
        target_file.write_text(_SCENARIO_1_DDL, encoding="utf-8")

        with patch("sync_bot.SyncOrchestrator") as MockOrch:
            instance = MockOrch.return_value
            mock_result = MagicMock(spec=OrchestratorResult)
            mock_result.success = True
            mock_result.errors = []
            mock_result.summary.return_value = "OK"
            instance.run_from_files.return_value = mock_result

            from sync_bot import main
            exit_code = main([
                "--base-ref", "HEAD~1",
                "--target-sql", str(target_file),
                "--dry-run",
            ])
        assert exit_code == 0
        call_kwargs = instance.run_from_files.call_args
        assert call_kwargs.kwargs.get("base_ref") == "HEAD~1" or (
            len(call_kwargs.args) > 1 and "HEAD~1" in str(call_kwargs.args)
        )

    def test_commit_sha_and_mr_ref_flags(self, tmp_path) -> None:
        with patch("sync_bot.SyncOrchestrator") as MockOrch:
            instance = MockOrch.return_value
            mock_result = MagicMock(spec=OrchestratorResult)
            mock_result.success = True
            mock_result.errors = []
            mock_result.summary.return_value = "OK"
            instance.simulate_scenario_1.return_value = mock_result

            from sync_bot import main
            exit_code = main([
                "--simulate-scenario-1",
                "--commit-sha", "deadbeef1234",
                "--mr-ref", "#99",
                "--dry-run",
            ])
        assert exit_code == 0

    def test_mutually_exclusive_prior_and_base_ref(self) -> None:
        """--prior-sql and --base-ref are mutually exclusive."""
        from sync_bot import build_parser
        parser = build_parser()
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args(["--prior-sql", "a.sql", "--base-ref", "HEAD~1"])
        assert exc_info.value.code != 0

    def test_mutually_exclusive_simulate_flags(self) -> None:
        """--simulate-scenario-1 and --simulate-scenario-2 are mutually exclusive."""
        from sync_bot import build_parser
        parser = build_parser()
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args(["--simulate-scenario-1", "--simulate-scenario-2"])
        assert exc_info.value.code != 0
