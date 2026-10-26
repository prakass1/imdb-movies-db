"""
tests/test_changelog_generator.py
---------------------------------
Unit tests for markdown changelog generation and file outputs.
"""

import os
from github_bot.changelog_generator import ChangelogGenerator
from github_bot.schema_differ import SchemaDiffer


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


class TestChangelogGenerator:
    """Tests for ChangelogGenerator."""

    def test_filename_generation(self) -> None:
        generator = ChangelogGenerator(output_dir="changelogs")
        filename = generator.generate_filename(
            commit_sha="a1b2c3d4e5f6",
            timestamp="20261025-180000",
        )
        assert filename == "changelog-20261025-180000-a1b2c3d4.md"

    def test_scenario_1_changelog_content(self) -> None:
        """Verify changelog markdown output for Scenario 1 (Rename & Drop)."""
        diff = SchemaDiffer.diff_ddl_strings(
            BASE_MOVIES_SQL,
            SCENARIO_1_SQL,
            rename_hints={"titles_top_100": {"No_of_Votes": "vote_count"}},
        )

        generator = ChangelogGenerator()
        md = generator.generate_markdown(
            diff_result=diff,
            commit_sha="7a8b9c0d1e2f",
            mr_reference="https://github.com/org/movies_etl/pull/42",
            timestamp="20261025-183000",
            author="Data Engineering Bot",
        )

        # Check Metadata
        assert "7a8b9c0d" in md
        assert "Commit SHA**: `7a8b9c0d1e2f`" in md
        assert "https://github.com/org/movies_etl/pull/42" in md
        assert "BREAKING CHANGES DETECTED" in md

        # Check Categorized table modifications
        assert "titles_top_100" in md
        assert "vote_count" in md
        assert "RENAME_COLUMN" in md
        assert "Poster_Link" in md
        assert "DROP_COLUMN" in md

        # Check Downstream Landing impact
        assert "Table Drop & CDC Reload Needed" in md
        assert "Active pipelines must be stopped" in md

        # Check Downstream Staging impact
        assert "Update CTE Column Projections" in md
        assert "Remove references to dropped column(s)" in md

        # Check Action checklist
        assert "Pipeline Interruption Check" in md
        assert "DROP Affected Landing Tables" in md
        assert "CDC Reload & Backfill" in md
        assert "Staging Merge Request" in md

    def test_scenario_2_changelog_content(self) -> None:
        """Verify changelog markdown output for Scenario 2 (New Table)."""
        diff = SchemaDiffer.diff_ddl_strings(BASE_MOVIES_SQL, SCENARIO_2_SQL)

        generator = ChangelogGenerator()
        md = generator.generate_markdown(
            diff_result=diff,
            commit_sha="f1e2d3c4b5a6",
            mr_reference="#101",
            timestamp="20261025-184500",
        )

        # Check Metadata
        assert "f1e2d3c4" in md
        assert "NON-BREAKING ADDITIONS" in md

        # Check Categorized table modifications
        assert "New Tables" in md
        assert "movie_reviews" in md
        assert "review_id" in md
        assert "reviewer_name" in md

        # Check Downstream Landing & Staging impact
        assert "New CDC Stream Required" in md
        assert "Scaffold Staging Model" in md

        # Check Action checklist
        assert "Provision Landing Table" in md
        assert "Full Load Initialization" in md

    def test_write_changelog_file(self, tmp_path) -> None:
        """Verify writing changelog to disk."""
        diff = SchemaDiffer.diff_ddl_strings(
            BASE_MOVIES_SQL,
            SCENARIO_1_SQL,
            rename_hints={"titles_top_100": {"No_of_Votes": "vote_count"}},
        )

        out_dir = tmp_path / "changelogs"
        generator = ChangelogGenerator(output_dir=str(out_dir))

        filepath, content = generator.write_changelog(
            diff_result=diff,
            commit_sha="3c4d5e6f7a8b",
            timestamp="20261025-190000",
        )

        assert os.path.exists(filepath)
        assert filepath.endswith("changelog-20261025-190000-3c4d5e6f.md")

        with open(filepath, "r", encoding="utf-8") as f:
            disk_content = f.read()

        assert disk_content == content
        assert "titles_top_100" in disk_content
