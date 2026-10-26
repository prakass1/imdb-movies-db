#!/usr/bin/env python3
"""
sync_bot.py
-----------
Top-level CLI entry point for the GitHub Schema Sync Bot.

Ties together schema diff, changelog generation, Landing Zone issue creation,
and Staging Draft PR creation via the :class:`~github_bot.orchestrator.SyncOrchestrator`.

Usage examples
--------------
# Dry-run comparing HEAD~1 against current db/movies.sql
python sync_bot.py --base-ref HEAD~1 --dry-run

# Compare two explicit SQL files
python sync_bot.py --prior-sql old/movies.sql --target-sql db/movies.sql --dry-run

# Run built-in Scenario 1 simulation (rename + drop)
python sync_bot.py --simulate-scenario-1 --dry-run

# Run built-in Scenario 2 simulation (new table)
python sync_bot.py --simulate-scenario-2 --dry-run

# Real run with explicit commit SHA and MR reference
python sync_bot.py --base-ref HEAD~1 --commit-sha abc123def --mr-ref 42
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from dotenv import load_dotenv

from github_bot.config import GitHubConfig
from github_bot.orchestrator import SyncOrchestrator

load_dotenv()

logger = logging.getLogger("sync_bot")


_DEFAULT_TARGET_SQL = "db/movies.sql"
_DEFAULT_CHANGELOGS_DIR = "changelogs"


# ---------------------------------------------------------------------------
# CLI argument parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sync_bot",
        description="GitHub Schema Sync Bot — cross-repo DDL change orchestrator.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Usage examples")[0],
    )

    # --- Input sources ---
    input_group = parser.add_mutually_exclusive_group()
    input_group.add_argument(
        "--base-ref",
        metavar="GIT_REF",
        help="Git reference for the prior DDL state (e.g. HEAD~1, main, v1.2.3). "
             "The file at --target-sql is retrieved at this ref via `git show`.",
    )
    input_group.add_argument(
        "--prior-sql",
        metavar="FILE",
        help="Path to a SQL file containing the previous DDL state.",
    )

    parser.add_argument(
        "--target-sql",
        metavar="FILE",
        default=_DEFAULT_TARGET_SQL,
        help=f"Path to the current DDL SQL file (default: {_DEFAULT_TARGET_SQL}).",
    )

    # --- Run identity ---
    parser.add_argument(
        "--commit-sha",
        metavar="SHA",
        default=None,
        help="Commit SHA to stamp in changelogs and GitHub artefacts. "
             "Derived from `git rev-parse HEAD` when omitted.",
    )
    parser.add_argument(
        "--mr-ref",
        metavar="REF",
        default=None,
        help="Merge Request / Pull Request reference (e.g. #42 or a full URL).",
    )

    # --- Simulation shortcuts ---
    sim_group = parser.add_mutually_exclusive_group()
    sim_group.add_argument(
        "--simulate-scenario-1",
        action="store_true",
        help="Run built-in Scenario 1: Rename No_of_Votes → vote_count and drop Poster_Link.",
    )
    sim_group.add_argument(
        "--simulate-scenario-2",
        action="store_true",
        help="Run built-in Scenario 2: Add new table movie_reviews.",
    )

    # --- Behaviour flags ---
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate GitHub operations without making real network calls.",
    )
    parser.add_argument(
        "--owner",
        metavar="GITHUB_OWNER",
        default=None,
        help="GitHub owner/organisation. Falls back to GITHUB_OWNER env var.",
    )
    parser.add_argument(
        "--changelogs-dir",
        metavar="DIR",
        default=_DEFAULT_CHANGELOGS_DIR,
        help=f"Directory for generated changelog files (default: {_DEFAULT_CHANGELOGS_DIR}).",
    )

    # --- Verbosity ---
    verbosity_group = parser.add_mutually_exclusive_group()
    verbosity_group.add_argument(
        "--verbose",
        action="store_true",
        help="Enable DEBUG-level log output.",
    )
    verbosity_group.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress all output except errors.",
    )

    return parser


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """Entry point. Returns exit code (0 = success, 1 = error)."""
    parser = build_parser()
    args = parser.parse_args(argv)

    # --- Logging setup ---
    if args.quiet:
        log_level = logging.ERROR
    elif args.verbose:
        log_level = logging.DEBUG
    else:
        log_level = logging.INFO

    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stderr,
    )

    # --- Build config & orchestrator ---
    config = GitHubConfig.from_env()
    if args.owner:
        config.github_owner = args.owner

    orchestrator = SyncOrchestrator(
        config=config,
        dry_run=args.dry_run,
        source_repo=config.movies_db_repo,
        target_repo=config.movies_etl_repo,
        changelogs_dir=args.changelogs_dir,
        owner=args.owner or config.github_owner or None,
        verbose=args.verbose,
    )

    # --- Run ---
    if args.simulate_scenario_1:
        logger.info("▶ Running Scenario 1 simulation (rename + drop).")
        result = orchestrator.simulate_scenario_1()
    elif args.simulate_scenario_2:
        logger.info("▶ Running Scenario 2 simulation (new table).")
        result = orchestrator.simulate_scenario_2()
    elif args.base_ref or args.prior_sql:
        logger.info("▶ Running file-based diff.")
        result = orchestrator.run_from_files(
            target_sql_path=args.target_sql,
            prior_sql_path=args.prior_sql,
            base_ref=args.base_ref,
            commit_sha=args.commit_sha,
            mr_reference=args.mr_ref,
        )
    else:
        # Neither simulation nor explicit source provided — attempt git HEAD~1 compare
        logger.info("▶ No input specified; defaulting to HEAD~1 git comparison.")
        result = orchestrator.run_from_files(
            target_sql_path=args.target_sql,
            base_ref="HEAD~1",
            commit_sha=args.commit_sha,
            mr_reference=args.mr_ref,
        )

    # --- Print summary to stdout ---
    print(result.summary())

    if not result.success:
        print(f"\nRun completed with {len(result.errors)} error(s).", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
