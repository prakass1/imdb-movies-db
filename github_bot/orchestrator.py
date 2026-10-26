"""
github_bot.orchestrator
-----------------------
Master orchestrator that ties together:
  1. DDL change detection (SchemaDiffer)
  2. Changelog generation (ChangelogGenerator)
  3. Landing Zone issue creation (LandingZoneDispatcher)
  4. Staging CTAS/CTE adaptation and Draft PR creation (StagingPRDispatcher)

Can be invoked programmatically or via the ``sync_bot.py`` CLI entry point.
"""

from __future__ import annotations

import datetime
import logging
import os
import subprocess
from dataclasses import dataclass, field
from typing import Any

from github_bot.changelog_generator import ChangelogGenerator
from github_bot.client import GitHubClient
from github_bot.config import GitHubConfig
from github_bot.landing_dispatcher import DispatchContext, LandingIssueResult, LandingZoneDispatcher
from github_bot.schema_differ import SchemaDiffer, SchemaDiffResult
from github_bot.staging_adapter import StagingAdapterResult, StagingPRDispatcher

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Built-in scenario DDL constants (mirrors test_changelog_generator.py)
# ---------------------------------------------------------------------------

_BASE_DDL = """
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

_SCENARIO_1_DDL = """
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

_SCENARIO_2_DDL = """
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

_SCENARIO_1_RENAME_HINTS: dict[str, dict[str, str]] = {
    "titles_top_100": {"No_of_Votes": "vote_count"},
}


# ---------------------------------------------------------------------------
# Orchestration result dataclass
# ---------------------------------------------------------------------------


@dataclass
class OrchestratorResult:
    """Aggregated outcome from a single orchestrator run."""

    commit_sha: str
    diff_result: SchemaDiffResult
    changelog_path: str | None = None
    changelog_content: str | None = None
    landing_issues: list[LandingIssueResult] = field(default_factory=list)
    staging_prs: list[StagingAdapterResult] = field(default_factory=list)
    dry_run: bool = False
    errors: list[str] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return len(self.errors) == 0

    def summary(self) -> str:
        """Return a human-readable one-paragraph summary."""
        lines = [
            f"Orchestrator Run Summary (commit: {self.commit_sha[:8]})",
            f"  Dry-run: {self.dry_run}",
            f"  Schema changes detected: {self.diff_result.has_changes}",
            f"  Breaking changes: {self.diff_result.is_breaking}",
            f"  Changelog: {self.changelog_path or 'N/A'}",
            f"  Landing issues created: {len(self.landing_issues)}",
            f"  Staging PRs opened: {len(self.staging_prs)}",
        ]
        if self.errors:
            lines.append(f"  Errors ({len(self.errors)}):")
            for err in self.errors:
                lines.append(f"    - {err}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main Orchestrator class
# ---------------------------------------------------------------------------


class SyncOrchestrator:
    """
    End-to-end orchestrator for cross-repo schema synchronization.

    Typical usage::

        orchestrator = SyncOrchestrator(dry_run=True)
        result = orchestrator.run(
            prior_sql="... old DDL ...",
            target_sql="... new DDL ...",
            commit_sha="abc123",
        )
    """

    def __init__(
        self,
        config: GitHubConfig | None = None,
        dry_run: bool = False,
        changelogs_dir: str = "changelogs",
        owner: str | None = None,
        source_repo: str = "movies_db",
        target_repo: str = "movies_etl",
        verbose: bool = False,
    ) -> None:
        self._config = config or GitHubConfig.from_env()
        self._dry_run = dry_run
        self._changelogs_dir = changelogs_dir
        self._owner = owner or self._config.github_owner or None
        self._source_repo = source_repo
        self._target_repo = target_repo

        log_level = logging.DEBUG if verbose else logging.INFO
        logging.basicConfig(
            level=log_level,
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        )

        self._client = GitHubClient(config=self._config, dry_run=dry_run)
        self._changelog_gen = ChangelogGenerator(output_dir=changelogs_dir)
        self._landing_dispatcher = LandingZoneDispatcher(
            client=self._client, config=self._config, dry_run=dry_run
        )
        self._staging_dispatcher = StagingPRDispatcher(
            client=self._client, config=self._config, dry_run=dry_run
        )

    # ------------------------------------------------------------------
    # Public entry points
    # ------------------------------------------------------------------

    def run(
        self,
        prior_sql: str,
        target_sql: str,
        commit_sha: str,
        mr_reference: str | None = None,
        rename_hints: dict[str, dict[str, str]] | None = None,
        timestamp: datetime.datetime | str | None = None,
        author: str | None = None,
        existing_staging_files: dict[str, str] | None = None,
    ) -> OrchestratorResult:
        """
        Execute the full sync workflow for a given DDL diff.

        Returns an :class:`OrchestratorResult` with all generated artefacts.
        """
        logger.info(
            "Starting SyncOrchestrator run (commit=%s, dry_run=%s)", commit_sha[:8], self._dry_run
        )

        result = OrchestratorResult(commit_sha=commit_sha, diff_result=SchemaDiffResult(), dry_run=self._dry_run)

        # --- Step 1: Detect schema changes ---
        try:
            diff = SchemaDiffer.diff_ddl_strings(prior_sql, target_sql, rename_hints=rename_hints)
            result.diff_result = diff
            logger.info(
                "Schema diff complete. has_changes=%s, is_breaking=%s, tables_affected=%d",
                diff.has_changes,
                diff.is_breaking,
                len(diff.affected_table_names),
            )
        except Exception as exc:  # noqa: BLE001
            msg = f"Schema diff failed: {exc}"
            logger.error(msg)
            result.errors.append(msg)
            return result

        if not diff.has_changes:
            logger.info("No schema changes detected. Nothing to do.")
            return result

        # --- Step 2: Generate changelog ---
        try:
            changelog_path, changelog_content = self._changelog_gen.write_changelog(
                diff_result=diff,
                commit_sha=commit_sha,
                mr_reference=mr_reference,
                timestamp=timestamp,
                author=author,
                source_repo=self._source_repo,
                target_repo=self._target_repo,
            )
            result.changelog_path = changelog_path
            result.changelog_content = changelog_content
            logger.info("Changelog written to: %s", changelog_path)
        except Exception as exc:  # noqa: BLE001
            msg = f"Changelog generation failed: {exc}"
            logger.error(msg)
            result.errors.append(msg)

        # --- Step 3: Landing Zone issue creation ---
        ctx = DispatchContext(
            commit_sha=commit_sha,
            source_repo=self._source_repo,
            target_repo=self._target_repo,
            mr_reference=mr_reference,
            changelog_path=result.changelog_path,
            owner=self._owner,
        )
        try:
            landing_results = self._landing_dispatcher.dispatch(diff, ctx)
            result.landing_issues = landing_results
            for lr in landing_results:
                logger.info(
                    "Landing issue %s: #%s (%s)",
                    "simulated" if self._dry_run else "created",
                    lr.issue_number,
                    lr.title[:60],
                )
        except Exception as exc:  # noqa: BLE001
            msg = f"Landing issue dispatch failed: {exc}"
            logger.error(msg)
            result.errors.append(msg)

        # --- Step 4: Staging PR creation ---
        landing_issue_number: int | None = None
        if result.landing_issues:
            landing_issue_number = result.landing_issues[0].issue_number

        try:
            staging_results = self._staging_dispatcher.dispatch(
                diff_result=diff,
                commit_sha=commit_sha,
                source_repo=self._source_repo,
                target_repo=self._target_repo,
                owner=self._owner,
                existing_staging_files=existing_staging_files,
                landing_issue_number=landing_issue_number,
            )
            result.staging_prs = staging_results
            for sr in staging_results:
                logger.info(
                    "Staging PR %s: #%s branch=%s (%s)",
                    "simulated" if self._dry_run else "opened",
                    sr.pr_number,
                    sr.branch_name,
                    sr.pr_title[:60],
                )
        except Exception as exc:  # noqa: BLE001
            msg = f"Staging PR dispatch failed: {exc}"
            logger.error(msg)
            result.errors.append(msg)

        logger.info("Run complete. %s", "Success." if result.success else f"{len(result.errors)} error(s).")
        return result

    def run_from_files(
        self,
        target_sql_path: str,
        prior_sql_path: str | None = None,
        base_ref: str | None = None,
        commit_sha: str | None = None,
        mr_reference: str | None = None,
        rename_hints: dict[str, dict[str, str]] | None = None,
    ) -> OrchestratorResult:
        """
        High-level entry point when working with files and git refs.

        If *prior_sql_path* is given, it is read directly.
        If *base_ref* is given (e.g. ``HEAD~1``), the prior DDL is retrieved
        from git using ``git show <base_ref>:<target_sql_path>``.
        Falls back to an empty string (treats all tables as new) when neither
        is provided.
        """
        # Read current DDL
        with open(target_sql_path, encoding="utf-8") as f:
            target_sql = f.read()

        # Resolve prior DDL
        if prior_sql_path:
            with open(prior_sql_path, encoding="utf-8") as f:
                prior_sql = f.read()
        elif base_ref:
            prior_sql = self._git_show(base_ref, target_sql_path)
        else:
            logger.warning("No prior SQL reference provided; treating all tables as new.")
            prior_sql = ""

        # Derive commit SHA
        resolved_sha = commit_sha or self._git_current_sha() or "unknown"

        return self.run(
            prior_sql=prior_sql,
            target_sql=target_sql,
            commit_sha=resolved_sha,
            mr_reference=mr_reference,
            rename_hints=rename_hints,
        )

    # ------------------------------------------------------------------
    # Simulation helpers
    # ------------------------------------------------------------------

    def simulate_scenario_1(self, commit_sha: str = "scenario1abc") -> OrchestratorResult:
        """
        Run the built-in Scenario 1 simulation:
        Rename ``No_of_Votes`` -> ``vote_count`` and drop ``Poster_Link``
        in ``titles_top_100``.
        """
        logger.info("Running built-in Scenario 1 simulation.")
        self._ensure_simulation_owner()
        return self.run(
            prior_sql=_BASE_DDL,
            target_sql=_SCENARIO_1_DDL,
            commit_sha=commit_sha,
            rename_hints=_SCENARIO_1_RENAME_HINTS,
            author="Scenario-1 Simulation",
        )

    def simulate_scenario_2(self, commit_sha: str = "scenario2def") -> OrchestratorResult:
        """
        Run the built-in Scenario 2 simulation:
        Add new table ``movie_reviews`` to the schema.
        """
        logger.info("Running built-in Scenario 2 simulation.")
        self._ensure_simulation_owner()
        return self.run(
            prior_sql=_BASE_DDL,
            target_sql=_SCENARIO_2_DDL,
            commit_sha=commit_sha,
            author="Scenario-2 Simulation",
        )

    def _ensure_simulation_owner(self) -> None:
        """Set a placeholder owner when running simulations without real config."""
        if not self._owner:
            self._owner = "simulation-owner"
            self._config.github_owner = "simulation-owner"
            # Rebuild dispatchers so _resolve_owner picks up the new owner
            self._client = GitHubClient(config=self._config, dry_run=self._dry_run)
            self._landing_dispatcher = LandingZoneDispatcher(
                client=self._client, config=self._config, dry_run=self._dry_run
            )
            self._staging_dispatcher = StagingPRDispatcher(
                client=self._client, config=self._config, dry_run=self._dry_run
            )

    # ------------------------------------------------------------------
    # Git helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _git_show(ref: str, path: str) -> str:
        """Return file content at *ref* from git history, or empty string on failure."""
        try:
            result = subprocess.run(
                ["git", "show", f"{ref}:{path}"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                return result.stdout
            logger.warning("git show %s:%s returned non-zero exit. Defaulting to empty DDL.", ref, path)
            return ""
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to run git show: %s. Defaulting to empty DDL.", exc)
            return ""

    @staticmethod
    def _git_current_sha() -> str | None:
        """Return the current HEAD commit SHA, or None if unavailable."""
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except Exception:  # noqa: BLE001
            pass
        return None
