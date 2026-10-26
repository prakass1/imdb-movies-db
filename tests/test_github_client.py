"""
tests/test_github_client.py
---------------------------
Unit tests for GitHubConfig, GitHubClient, and Bob MCP configuration.
"""

import io
import json
import ssl
import urllib.error
from unittest.mock import MagicMock, patch

import pytest

from github_bot.config import GitHubConfig
from github_bot.client import (
    GitHubClient,
    GitHubError,
    AuthenticationError,
    ResourceNotFoundError,
    GitHubAPIError,
)


class TestGitHubConfig:
    """Tests for GitHubConfig loading, validation, and MCP export."""

    def test_default_config(self):
        config = GitHubConfig()
        assert config.github_token is None
        assert config.github_owner == ""
        assert config.movies_db_repo == "movies_db"
        assert config.movies_etl_repo == "movies_etl"
        assert config.api_base_url == "https://api.github.com"
        assert config.timeout_seconds == 10
        assert not config.is_authenticated()

    def test_from_env_defaults(self, monkeypatch):
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        monkeypatch.delenv("GITHUB_PERSONAL_ACCESS_TOKEN", raising=False)
        monkeypatch.delenv("GITHUB_OWNER", raising=False)
        monkeypatch.delenv("GITHUB_REPOSITORY_OWNER", raising=False)
        monkeypatch.delenv("MOVIES_DB_REPO", raising=False)
        monkeypatch.delenv("MOVIES_ETL_REPO", raising=False)
        monkeypatch.delenv("GITHUB_API_URL", raising=False)
        monkeypatch.delenv("GITHUB_TIMEOUT_SECONDS", raising=False)

        config = GitHubConfig.from_env()
        assert config.github_token is None
        assert config.github_owner == ""
        assert config.movies_db_repo == "movies_db"
        assert config.movies_etl_repo == "movies_etl"
        assert config.timeout_seconds == 10

    def test_from_env_custom(self, monkeypatch):
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_test123456789")
        monkeypatch.setenv("GITHUB_OWNER", "metflix-org")
        monkeypatch.setenv("MOVIES_DB_REPO", "custom_movies_db")
        monkeypatch.setenv("MOVIES_ETL_REPO", "custom_movies_etl")
        monkeypatch.setenv("GITHUB_API_URL", "https://api.github.com/")
        monkeypatch.setenv("GITHUB_TIMEOUT_SECONDS", "15")

        config = GitHubConfig.from_env()
        assert config.github_token == "ghp_test123456789"
        assert config.github_owner == "metflix-org"
        assert config.movies_db_repo == "custom_movies_db"
        assert config.movies_etl_repo == "custom_movies_etl"
        assert config.api_base_url == "https://api.github.com"
        assert config.timeout_seconds == 15
        assert config.is_authenticated()

    def test_from_env_personal_access_token_fallback(self, monkeypatch):
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        monkeypatch.delenv("GITHUB_OWNER", raising=False)
        monkeypatch.setenv("GITHUB_PERSONAL_ACCESS_TOKEN", "pat_test987654")
        monkeypatch.setenv("GITHUB_REPOSITORY_OWNER", "my-org")

        config = GitHubConfig.from_env()
        assert config.github_token == "pat_test987654"
        assert config.github_owner == "my-org"
        assert config.is_authenticated()

    def test_from_env_invalid_timeout(self, monkeypatch):
        monkeypatch.setenv("GITHUB_TIMEOUT_SECONDS", "invalid_num")
        config = GitHubConfig.from_env()
        assert config.timeout_seconds == 10

    def test_validate_missing_fields(self):
        config = GitHubConfig(github_token="", github_owner="", timeout_seconds=-1)
        issues = config.validate()
        assert len(issues) >= 3
        assert any("token" in issue for issue in issues)
        assert any("owner" in issue for issue in issues)
        assert any("timeout" in issue for issue in issues)

    def test_validate_complete(self):
        config = GitHubConfig(
            github_token="ghp_valid_token",
            github_owner="test-owner",
            movies_db_repo="movies_db",
            movies_etl_repo="movies_etl",
        )
        assert config.validate() == []

    def test_to_mcp_config(self):
        config = GitHubConfig()
        mcp_dict = config.to_mcp_config()
        assert "mcpServers" in mcp_dict
        assert "github" in mcp_dict["mcpServers"]
        gh_server = mcp_dict["mcpServers"]["github"]
        assert gh_server["command"] == "npx"
        assert "@modelcontextprotocol/server-github" in gh_server["args"]
        assert gh_server["env"]["GITHUB_PERSONAL_ACCESS_TOKEN"] == "${env:GITHUB_TOKEN}"


class TestGitHubClientInitAndHeaders:
    """Tests for headers and owner resolution."""

    def test_headers_with_and_without_token(self):
        cfg_no_token = GitHubConfig(github_token=None)
        client_no_token = GitHubClient(cfg_no_token)
        headers = client_no_token._get_headers()
        assert "Authorization" not in headers
        assert headers["Accept"] == "application/vnd.github+json"

        cfg_with_token = GitHubConfig(
            github_token="secret_token",
            extra_headers={"X-Custom": "test"},
        )
        client_with_token = GitHubClient(cfg_with_token)
        headers2 = client_with_token._get_headers()
        assert headers2["Authorization"] == "Bearer secret_token"
        assert headers2["X-Custom"] == "test"

    def test_resolve_owner(self):
        client = GitHubClient(GitHubConfig(github_owner="default-owner"))
        assert client._resolve_owner() == "default-owner"
        assert client._resolve_owner("custom-owner") == "custom-owner"

        client_no_owner = GitHubClient(GitHubConfig(github_owner=""))
        with pytest.raises(GitHubError, match="GitHub owner/organization must be specified"):
            client_no_owner._resolve_owner()


class TestGitHubClientDryRun:
    """Tests for GitHubClient dry run simulation mode."""

    @pytest.fixture
    def dry_run_client(self):
        config = GitHubConfig(
            github_token="test-token",
            github_owner="metflix-org",
            movies_db_repo="movies_db",
            movies_etl_repo="movies_etl",
        )
        return GitHubClient(config=config, dry_run=True)

    def test_get_authenticated_user_dry_run(self, dry_run_client):
        user = dry_run_client.get_authenticated_user()
        assert user["login"] == "metflix-org"
        assert dry_run_client.verify_auth() is True

    def test_verify_auth_no_token(self):
        client = GitHubClient(GitHubConfig(github_token=None), dry_run=True)
        assert client.verify_auth() is False

    def test_get_repository_and_access(self, dry_run_client):
        repo = dry_run_client.get_repository("movies_etl")
        assert repo["name"] == "movies_etl"
        assert repo["full_name"] == "metflix-org/movies_etl"
        assert dry_run_client.verify_repo_access("movies_etl") is True

    def test_create_and_get_issue_dry_run(self, dry_run_client):
        issue = dry_run_client.create_issue(
            repo="movies_etl",
            title="Landing Schema Migration Required",
            body="Step 1: DROP table titles_top_100",
            labels=["schema-sync", "landing-zone"],
        )
        assert issue["number"] == 101
        assert issue["title"] == "Landing Schema Migration Required"
        assert len(issue["labels"]) == 2

        fetched = dry_run_client.get_issue(repo="movies_etl", issue_number=101)
        assert fetched["number"] == 101

    def test_git_refs_and_branches_dry_run(self, dry_run_client):
        branch = dry_run_client.get_branch(repo="movies_etl", branch="main")
        assert branch["name"] == "main"
        assert "commit" in branch

        ref = dry_run_client.get_ref(repo="movies_etl", ref="refs/heads/main")
        assert ref["ref"] == "refs/heads/main"

        new_branch = dry_run_client.create_branch(
            repo="movies_etl",
            branch_name="schema-sync/titles-top-100-abc1234",
            from_sha="mocksha1234567890abcdef",
        )
        assert new_branch["ref"] == "refs/heads/schema-sync/titles-top-100-abc1234"

    def test_file_and_pull_request_dry_run(self, dry_run_client):
        file_resp = dry_run_client.create_or_update_file(
            repo="movies_etl",
            path="staging/titles_top_100.sql",
            message="Update staging query for vote_count",
            content="SELECT vote_count FROM titles_top_100;",
            branch="schema-sync/titles-top-100-abc1234",
        )
        assert file_resp["content"]["path"] == "staging/titles_top_100.sql"

        pr = dry_run_client.create_pull_request(
            repo="movies_etl",
            title="[Staging Sync] Adapt CTAS queries",
            body="Adapts columns for movies_db#abc1234",
            head="schema-sync/titles-top-100-abc1234",
            base="main",
            draft=True,
        )
        assert pr["number"] == 202
        assert pr["draft"] is True
        assert pr["head"]["ref"] == "schema-sync/titles-top-100-abc1234"

        fetched_pr = dry_run_client.get_pull_request(repo="movies_etl", pr_number=202)
        assert fetched_pr["number"] == 202


class TestGitHubClientHTTPRequests:
    """Tests for HTTP request dispatching and error handling."""

    @pytest.fixture
    def client(self):
        config = GitHubConfig(
            github_token="ghp_test_token",
            github_owner="test-owner",
            api_base_url="https://api.github.com",
            timeout_seconds=5,
        )
        return GitHubClient(config=config, dry_run=False)

    def test_request_success_get(self, client):
        mock_response_data = {"id": 1, "login": "test-owner"}
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(mock_response_data).encode("utf-8")
        mock_response.__enter__.return_value = mock_response

        with patch("urllib.request.urlopen", return_value=mock_response) as mock_urlopen:
            result = client._request("GET", "user")
            assert result == mock_response_data
            mock_urlopen.assert_called_once()
            req = mock_urlopen.call_args[0][0]
            assert req.get_method() == "GET"
            assert req.full_url == "https://api.github.com/user"
            assert req.headers["Authorization"] == "Bearer ghp_test_token"

    def test_request_success_post_with_params(self, client):
        mock_response_data = {"number": 42, "title": "New Issue"}
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(mock_response_data).encode("utf-8")
        mock_response.__enter__.return_value = mock_response

        with patch("urllib.request.urlopen", return_value=mock_response) as mock_urlopen:
            result = client._request(
                "POST",
                "repos/test-owner/movies_etl/issues",
                data={"title": "New Issue"},
            )
            assert result["number"] == 42
            req = mock_urlopen.call_args[0][0]
            assert req.get_method() == "POST"
            assert req.data == b'{"title": "New Issue"}'

    def test_request_http_error_401(self, client):
        err_body = b'{"message": "Bad credentials"}'
        err = urllib.error.HTTPError(
            url="https://api.github.com/user",
            code=401,
            msg="Unauthorized",
            hdrs={},
            fp=io.BytesIO(err_body),
        )

        with patch("urllib.request.urlopen", side_effect=err):
            with pytest.raises(AuthenticationError, match="GitHub authentication / permission error"):
                client._request("GET", "user")

    def test_request_http_error_404(self, client):
        err_body = b'{"message": "Not Found"}'
        err = urllib.error.HTTPError(
            url="https://api.github.com/repos/test-owner/missing",
            code=404,
            msg="Not Found",
            hdrs={},
            fp=io.BytesIO(err_body),
        )

        with patch("urllib.request.urlopen", side_effect=err):
            with pytest.raises(ResourceNotFoundError, match="GitHub resource not found"):
                client._request("GET", "repos/test-owner/missing")

    def test_request_http_error_500(self, client):
        err_body = b'{"message": "Internal Error"}'
        err = urllib.error.HTTPError(
            url="https://api.github.com/repos",
            code=500,
            msg="Server Error",
            hdrs={},
            fp=io.BytesIO(err_body),
        )

        with patch("urllib.request.urlopen", side_effect=err) as _:
            with pytest.raises(GitHubAPIError, match="GitHub API error"):
                client._request("GET", "repos")

    def test_request_network_url_error(self, client):
        err = urllib.error.URLError("Connection refused")
        with patch("urllib.request.urlopen", side_effect=err):
            with pytest.raises(GitHubError, match="Failed to connect to GitHub API"):
                client._request("GET", "user")


class TestBobMCPConfigFile:
    """Verifies that the generated .bob/mcp.json file exists and is valid."""

    def test_mcp_json_structure(self):
        with open(".bob/mcp.json", "r", encoding="utf-8") as fh:
            data = json.load(fh)

        assert "mcpServers" in data
        assert "github" in data["mcpServers"]
        gh = data["mcpServers"]["github"]
        assert gh["command"] == "npx"
        assert "-y" in gh["args"]
        assert "@modelcontextprotocol/server-github" in gh["args"]
        assert gh["env"]["GITHUB_PERSONAL_ACCESS_TOKEN"] == "${env:GITHUB_TOKEN}"
