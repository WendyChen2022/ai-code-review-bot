"""Shared data models used across the application.

These dataclasses act as the internal data contract between layers — the
webhook parser produces a PREvent; the review pipeline consumes it and
produces a ReviewComment.
"""

from dataclasses import dataclass


@dataclass
class PREvent:
    """Parsed representation of a GitHub pull_request webhook event."""

    action: str          # "opened" or "synchronize"
    pr_number: int
    pr_title: str
    repo_full_name: str  # "owner/repo"
    diff_url: str        # URL to fetch the unified diff
    head_sha: str        # Latest commit SHA on the PR branch


@dataclass
class ReviewComment:
    """Formatted review ready to be posted to GitHub."""

    summary: str         # Short extract of key findings (for internal logging)
    body: str            # Full markdown body for the GitHub comment
    recommendation: str  # "Approve", "Request Changes", or "Comment"
