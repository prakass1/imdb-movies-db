"""
github_bot package
------------------
GitHub MCP configuration, REST API client, and schema synchronization utilities.
"""

from github_bot.config import GitHubConfig
from github_bot.client import (
    GitHubClient,
    GitHubError,
    AuthenticationError,
    ResourceNotFoundError,
    GitHubAPIError,
)
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
from github_bot.changelog_generator import ChangelogGenerator

__all__ = [
    "GitHubConfig",
    "GitHubClient",
    "GitHubError",
    "AuthenticationError",
    "ResourceNotFoundError",
    "GitHubAPIError",
    "ChangeCategory",
    "ColumnDiff",
    "ColumnSchema",
    "SchemaDiffer",
    "SchemaDiffResult",
    "SQLDDLParser",
    "TableDiff",
    "TableSchema",
    "ChangelogGenerator",
]
