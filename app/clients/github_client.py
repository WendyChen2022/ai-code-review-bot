"""GitHub REST API client.

Responsible for all outbound HTTP calls to api.github.com:
  - Fetching a pull request's unified diff
  - Posting a review comment on a pull request

No business logic lives here — this module only knows how to talk to
GitHub.  All callers pass in the data they want sent; this module adds
auth headers and handles HTTP-level errors.
"""

import httpx

from app.core.config import settings
from app.core.retry import retry_async
from app.models.schemas import ReviewComment

# GitHub API version to pin — keeps behaviour stable as GitHub ships changes.
_GITHUB_API_VERSION = "2022-11-28"


def _auth_headers() -> dict[str, str]:
    """Return standard auth headers shared by all GitHub API requests."""
    return {
        "Authorization": f"Bearer {settings.github_token}",
        "X-GitHub-Api-Version": _GITHUB_API_VERSION,
    }


def _is_retryable_github(exc: Exception) -> bool:
    """Return True for transient GitHub API errors that are worth retrying.

    Rate limits (429) and server errors (5xx) are transient.
    Network errors (timeouts, connection resets) are also retried.
    Other 4xx errors (401 bad token, 404 not found) are permanent.
    """
    if isinstance(exc, httpx.RequestError):
        # Covers timeouts, connection errors, and other network failures.
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code == 429 or exc.response.status_code >= 500
    return False


async def fetch_pr_diff(diff_url: str) -> str:
    """Fetch the unified diff for a pull request.

    GitHub redirects the diff URL, so follow_redirects is required.
    Returns the raw diff text (empty string if the PR has no changes).
    Retries up to 3 times on transient failures.
    """
    headers = {
        **_auth_headers(),
        "Accept": "application/vnd.github.v3.diff",
    }

    async def _call() -> str:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(diff_url, headers=headers, follow_redirects=True)
            response.raise_for_status()
            return response.text

    return await retry_async(_call, should_retry=_is_retryable_github)


async def post_pr_comment(
    repo_full_name: str,
    pr_number: int,
    comment: ReviewComment,
) -> dict:
    """Post a review comment to a GitHub pull request.

    Uses the Issues Comments endpoint so the comment appears in the PR
    timeline alongside other review activity.
    Returns the created comment object from the GitHub API.
    Retries up to 3 times on transient failures.
    """
    url = f"https://api.github.com/repos/{repo_full_name}/issues/{pr_number}/comments"
    headers = {
        **_auth_headers(),
        "Accept": "application/vnd.github+json",
    }

    async def _call() -> dict:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(url, headers=headers, json={"body": comment.body})
            response.raise_for_status()
            return response.json()

    return await retry_async(_call, should_retry=_is_retryable_github)
