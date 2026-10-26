"""
github_bot.config
-----------------
Configuration models and environment loaders for GitHub integration and MCP settings.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any


@dataclass
class GitHubConfig:
    """GitHub integration and target repository configuration."""

    github_token: str | None = None
    github_owner: str = ""
    movies_db_repo: str = "movies_db"
    movies_etl_repo: str = "movies_etl"
    api_base_url: str = "https://api.github.com"
    timeout_seconds: int = 10
    extra_headers: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_env(cls) -> GitHubConfig:
        """Load GitHub configuration from environment variables."""
        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GITHUB_PERSONAL_ACCESS_TOKEN")
        owner = os.environ.get("GITHUB_OWNER", os.environ.get("GITHUB_REPOSITORY_OWNER", ""))
        movies_db = os.environ.get("MOVIES_DB_REPO", "movies_db")
        movies_etl = os.environ.get("MOVIES_ETL_REPO", "movies_etl")
        api_url = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")

        timeout_str = os.environ.get("GITHUB_TIMEOUT_SECONDS", "10")
        try:
            timeout_seconds = int(timeout_str)
        except ValueError:
            timeout_seconds = 10

        return cls(
            github_token=token,
            github_owner=owner,
            movies_db_repo=movies_db,
            movies_etl_repo=movies_etl,
            api_base_url=api_url,
            timeout_seconds=timeout_seconds,
        )

    def is_authenticated(self) -> bool:
        """Check if an authentication token is available."""
        return bool(self.github_token and self.github_token.strip())

    def validate(self) -> list[str]:
        """Validate configuration completeness and return a list of issue descriptions."""
        issues: list[str] = []
        if not self.is_authenticated():
            issues.append("Missing GitHub authentication token (GITHUB_TOKEN or GITHUB_PERSONAL_ACCESS_TOKEN).")
        if not self.github_owner:
            issues.append("Missing GitHub owner/organization (GITHUB_OWNER).")
        if not self.movies_db_repo:
            issues.append("Missing source repository name for movies_db (MOVIES_DB_REPO).")
        if not self.movies_etl_repo:
            issues.append("Missing target repository name for movies_etl (MOVIES_ETL_REPO).")
        if self.timeout_seconds <= 0:
            issues.append("timeout_seconds must be a positive integer.")
        return issues

    def to_mcp_config(self) -> dict[str, Any]:
        """Generate MCP server configuration mapping for .bob/mcp.json."""
        return {
            "mcpServers": {
                "github": {
                    "command": "npx",
                    "args": ["-y", "@modelcontextprotocol/server-github"],
                    "env": {
                        "GITHUB_PERSONAL_ACCESS_TOKEN": "${env:GITHUB_TOKEN}"
                    }
                }
            }
        }
