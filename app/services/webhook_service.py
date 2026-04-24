"""Webhook parsing and signature verification.

Responsible for two things:
  1. Verifying that an incoming request genuinely came from GitHub
     (HMAC-SHA256 signature check).
  2. Parsing a raw webhook payload dict into a typed PREvent, or
     returning None for events we intentionally ignore.

No HTTP framework types leak into this module — it works with plain
dicts and bytes so it stays easy to unit-test without spinning up FastAPI.
"""

import hashlib
import hmac

from app.core.config import settings
from app.models.schemas import PREvent

# Actions we want to trigger a review for.  "opened" fires when a PR is
# first created; "synchronize" fires on every subsequent push.
_HANDLED_ACTIONS = {"opened", "synchronize"}


def verify_signature(body: bytes, signature_header: str) -> bool:
    """Return True if the GitHub webhook signature is valid.

    If no GITHUB_WEBHOOK_SECRET is configured the check is skipped and
    every request is accepted — useful for local development but not for
    production.
    """
    # Treat missing inputs as an invalid signature regardless of secret config.
    if body is None or signature_header is None:
        return False

    secret = settings.github_webhook_secret
    if not secret:
        return True

    if not signature_header or not signature_header.startswith("sha256="):
        return False

    expected = "sha256=" + hmac.new(
        secret.encode(),
        body,
        hashlib.sha256,
    ).hexdigest()

    # compare_digest prevents timing attacks by avoiding short-circuit
    # evaluation on the first differing byte.
    return hmac.compare_digest(expected, signature_header)


def parse_pr_event(payload: dict) -> PREvent | None:
    """Parse a GitHub pull_request webhook payload into a PREvent.

    Returns None for actions we don't handle (e.g. "closed", "labeled")
    so callers can skip them cleanly without raising exceptions.
    Raises ValueError if a required field is missing from the payload.
    """
    action = payload.get("action", "")
    if action not in _HANDLED_ACTIONS:
        return None

    pr = payload.get("pull_request", {})
    repo = payload.get("repository", {})

    pr_number = pr.get("number")
    if not pr_number:
        raise ValueError("Webhook payload missing pull_request.number")

    return PREvent(
        action=action,
        pr_number=pr_number,
        pr_title=pr.get("title", ""),
        # Fall back to the configured default repo if GitHub omits it.
        repo_full_name=repo.get("full_name", settings.github_repo),
        diff_url=pr.get("diff_url", ""),
        head_sha=pr.get("head", {}).get("sha", ""),
    )
