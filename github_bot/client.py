"""
github_bot.client
-----------------
Foundational client and abstraction layer for GitHub REST / MCP operations.
"""

from __future__ import annotations

import base64
import json
import logging
import ssl
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from github_bot.config import GitHubConfig

logger = logging.getLogger(__name__)


class GitHubError(Exception):
    """Base exception for GitHub client errors."""


class AuthenticationError(GitHubError):
    """Raised when GitHub credentials are invalid or missing."""


class ResourceNotFoundError(GitHubError):
    """Raised when a requested repository, issue, PR, or branch is not found."""


class GitHubAPIError(GitHubError):
    """Raised when GitHub API returns an unexpected error status code."""

    def __init__(self, message: str, status_code: int | None = None, response_body: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body


class GitHubClient:
    """Client for interacting with GitHub repositories, issues, branches, and PRs."""

    def __init__(self, config: GitHubConfig | None = None, dry_run: bool = False):
        self.config = config or GitHubConfig.from_env()
        self.dry_run = dry_run
        self._ssl_context = ssl.create_default_context()

    def _get_headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "Bob-GitHub-Bot/1.0",
        }
        if self.config.github_token:
            headers["Authorization"] = f"Bearer {self.config.github_token.strip()}"
        if self.config.extra_headers:
            headers.update(self.config.extra_headers)
        return headers

    def _resolve_owner(self, owner: str | None = None) -> str:
        resolved = owner or self.config.github_owner
        if not resolved:
            raise GitHubError("GitHub owner/organization must be specified.")
        return resolved

    def _request(
        self,
        method: str,
        endpoint: str,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
    ) -> Any:
        """Execute an HTTPS request to the GitHub API."""
        url = f"{self.config.api_base_url.rstrip('/')}/{endpoint.lstrip('/')}"
        if params:
            query_string = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
            if query_string:
                url = f"{url}?{query_string}"

        req_body: bytes | None = None
        headers = self._get_headers()
        if data is not None:
            req_body = json.dumps(data).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"

        request = urllib.request.Request(url=url, data=req_body, headers=headers, method=method.upper())

        try:
            with urllib.request.urlopen(
                request,
                context=self._ssl_context,
                timeout=self.config.timeout_seconds,
            ) as response:
                response_bytes = response.read()
                if not response_bytes:
                    return {}
                return json.loads(response_bytes.decode("utf-8"))
        except urllib.error.HTTPError as exc:
            status = exc.code
            err_content = exc.read().decode("utf-8", errors="replace")
            try:
                err_json = json.loads(err_content)
                err_msg = err_json.get("message", err_content)
            except Exception:
                err_msg = err_content

            logger.error("GitHub API HTTP %d error on %s %s: %s", status, method, endpoint, err_msg)

            if status in (401, 403):
                raise AuthenticationError(f"GitHub authentication / permission error (HTTP {status}): {err_msg}") from exc
            if status == 404:
                raise ResourceNotFoundError(f"GitHub resource not found (HTTP 404): {err_msg}") from exc
            raise GitHubAPIError(f"GitHub API error (HTTP {status}): {err_msg}", status_code=status, response_body=err_content) from exc
        except urllib.error.URLError as exc:
            logger.error("Network connection error to %s: %s", url, exc.reason)
            raise GitHubError(f"Failed to connect to GitHub API: {exc.reason}") from exc

    # -----------------------------------------------------------------------
    # Authentication & Repository Inspection
    # -----------------------------------------------------------------------

    def get_authenticated_user(self) -> dict[str, Any]:
        """Fetch the authenticated user's profile."""
        if self.dry_run:
            return {"login": self.config.github_owner or "mock-user", "id": 12345}
        return self._request("GET", "user")

    def verify_auth(self) -> bool:
        """Verify that the provided token and credentials are valid."""
        if not self.config.is_authenticated():
            return False
        if self.dry_run:
            return True
        try:
            self.get_authenticated_user()
            return True
        except (AuthenticationError, GitHubError):
            return False

    def get_repository(self, repo: str, owner: str | None = None) -> dict[str, Any]:
        """Fetch details of a specific repository."""
        resolved_owner = self._resolve_owner(owner)
        if self.dry_run:
            return {
                "name": repo,
                "full_name": f"{resolved_owner}/{repo}",
                "owner": {"login": resolved_owner},
                "default_branch": "main",
            }
        return self._request("GET", f"repos/{resolved_owner}/{repo}")

    def verify_repo_access(self, repo: str, owner: str | None = None) -> bool:
        """Check if the client can read the specified repository."""
        if self.dry_run:
            return True
        try:
            self.get_repository(repo, owner)
            return True
        except (ResourceNotFoundError, AuthenticationError, GitHubError):
            return False

    # -----------------------------------------------------------------------
    # Issue Management
    # -----------------------------------------------------------------------

    def create_issue(
        self,
        repo: str,
        title: str,
        body: str,
        labels: list[str] | None = None,
        owner: str | None = None,
    ) -> dict[str, Any]:
        """Create a new issue on the specified repository."""
        resolved_owner = self._resolve_owner(owner)
        payload: dict[str, Any] = {"title": title, "body": body}
        if labels:
            payload["labels"] = labels

        if self.dry_run:
            logger.info("[DRY RUN] create_issue on %s/%s: %s", resolved_owner, repo, title)
            return {
                "number": 101,
                "title": title,
                "body": body,
                "labels": [{"name": l} for l in (labels or [])],
                "html_url": f"https://github.com/{resolved_owner}/{repo}/issues/101",
                "state": "open",
            }

        return self._request("POST", f"repos/{resolved_owner}/{repo}/issues", data=payload)

    def get_issue(self, repo: str, issue_number: int, owner: str | None = None) -> dict[str, Any]:
        """Fetch an existing issue by number."""
        resolved_owner = self._resolve_owner(owner)
        if self.dry_run:
            return {
                "number": issue_number,
                "title": f"Mock Issue #{issue_number}",
                "body": "Mock body content",
                "html_url": f"https://github.com/{resolved_owner}/{repo}/issues/{issue_number}",
                "state": "open",
            }
        return self._request("GET", f"repos/{resolved_owner}/{repo}/issues/{issue_number}")

    def list_issues(
        self,
        repo: str,
        state: str = "open",
        labels: list[str] | None = None,
        owner: str | None = None,
    ) -> list[dict[str, Any]]:
        """List issues for a repository."""
        resolved_owner = self._resolve_owner(owner)
        params: dict[str, Any] = {"state": state}
        if labels:
            params["labels"] = ",".join(labels)

        if self.dry_run:
            return []
        return self._request("GET", f"repos/{resolved_owner}/{repo}/issues", params=params)

    # -----------------------------------------------------------------------
    # Git References & Branches
    # -----------------------------------------------------------------------

    def get_ref(self, repo: str, ref: str, owner: str | None = None) -> dict[str, Any]:
        """Get a git reference (e.g. heads/main)."""
        resolved_owner = self._resolve_owner(owner)
        clean_ref = ref.lstrip("/")
        if clean_ref.startswith("refs/"):
            clean_ref = clean_ref[len("refs/") :]

        if self.dry_run:
            return {
                "ref": f"refs/{clean_ref}",
                "object": {"sha": "mocksha1234567890abcdef", "type": "commit"},
            }
        return self._request("GET", f"repos/{resolved_owner}/{repo}/git/ref/{clean_ref}")

    def get_branch(self, repo: str, branch: str = "main", owner: str | None = None) -> dict[str, Any]:
        """Get details and latest commit SHA for a branch."""
        resolved_owner = self._resolve_owner(owner)
        if self.dry_run:
            return {
                "name": branch,
                "commit": {"sha": "mocksha1234567890abcdef"},
            }
        return self._request("GET", f"repos/{resolved_owner}/{repo}/branches/{branch}")

    def create_branch(
        self,
        repo: str,
        branch_name: str,
        from_sha: str,
        owner: str | None = None,
    ) -> dict[str, Any]:
        """Create a new branch from a commit SHA."""
        resolved_owner = self._resolve_owner(owner)
        payload = {
            "ref": f"refs/heads/{branch_name}",
            "sha": from_sha,
        }
        if self.dry_run:
            logger.info("[DRY RUN] create_branch %s on %s/%s from %s", branch_name, resolved_owner, repo, from_sha)
            return {
                "ref": f"refs/heads/{branch_name}",
                "object": {"sha": from_sha, "type": "commit"},
            }
        return self._request("POST", f"repos/{resolved_owner}/{repo}/git/refs", data=payload)

    # -----------------------------------------------------------------------
    # File Content Management
    # -----------------------------------------------------------------------

    def create_or_update_file(
        self,
        repo: str,
        path: str,
        message: str,
        content: str,
        branch: str,
        sha: str | None = None,
        owner: str | None = None,
    ) -> dict[str, Any]:
        """Create or update a file in the repository branch."""
        resolved_owner = self._resolve_owner(owner)
        encoded_content = base64.b64encode(content.encode("utf-8")).decode("utf-8")
        payload: dict[str, Any] = {
            "message": message,
            "content": encoded_content,
            "branch": branch,
        }
        if sha:
            payload["sha"] = sha

        if self.dry_run:
            logger.info("[DRY RUN] create_or_update_file %s on %s/%s branch %s", path, resolved_owner, repo, branch)
            return {
                "content": {"name": path.split("/")[-1], "path": path, "sha": "mockfilesha12345"},
                "commit": {"sha": "mockcommitsha67890", "message": message},
            }

        return self._request("PUT", f"repos/{resolved_owner}/{repo}/contents/{path.lstrip('/')}", data=payload)

    # -----------------------------------------------------------------------
    # Pull Request / Merge Request Management
    # -----------------------------------------------------------------------

    def create_pull_request(
        self,
        repo: str,
        title: str,
        body: str,
        head: str,
        base: str = "main",
        draft: bool = False,
        owner: str | None = None,
    ) -> dict[str, Any]:
        """Create a new Pull Request / Merge Request."""
        resolved_owner = self._resolve_owner(owner)
        payload = {
            "title": title,
            "body": body,
            "head": head,
            "base": base,
            "draft": draft,
        }

        if self.dry_run:
            logger.info("[DRY RUN] create_pull_request on %s/%s: %s (head=%s, base=%s, draft=%s)", resolved_owner, repo, title, head, base, draft)
            return {
                "number": 202,
                "title": title,
                "body": body,
                "html_url": f"https://github.com/{resolved_owner}/{repo}/pull/202",
                "state": "open",
                "draft": draft,
                "head": {"ref": head},
                "base": {"ref": base},
            }

        return self._request("POST", f"repos/{resolved_owner}/{repo}/pulls", data=payload)

    def get_pull_request(self, repo: str, pr_number: int, owner: str | None = None) -> dict[str, Any]:
        """Fetch details of an existing Pull Request."""
        resolved_owner = self._resolve_owner(owner)
        if self.dry_run:
            return {
                "number": pr_number,
                "title": f"Mock PR #{pr_number}",
                "body": "Mock PR body content",
                "html_url": f"https://github.com/{resolved_owner}/{repo}/pull/{pr_number}",
                "state": "open",
            }
        return self._request("GET", f"repos/{resolved_owner}/{repo}/pulls/{pr_number}")
