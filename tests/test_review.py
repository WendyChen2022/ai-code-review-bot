"""Unit tests for the AI code review bot.

All tests use fake data or mocks — no real Claude or GitHub API calls are made.
Run with: uv run pytest tests/ -v
"""
import sys
from unittest.mock import MagicMock, patch


import hashlib
import hmac

from app.models.schemas import PREvent
from app.services.review_service import generate_review_comment
from app.services.webhook_service import parse_pr_event, verify_signature


# ---------------------------------------------------------------------------
# 1. Webhook payload parsing
# ---------------------------------------------------------------------------

SAMPLE_PAYLOAD = {
    "action": "opened",
    "pull_request": {
        "number": 42,
        "title": "Add discount calculator",
        "diff_url": "https://github.com/owner/repo/pull/42.diff",
        "head": {"sha": "abc1234567890"},
    },
    "repository": {
        "full_name": "owner/repo",
    },
}


def test_parse_pr_event_returns_pr_event():
    """A valid 'opened' payload should parse into a PREvent with correct fields."""
    event = parse_pr_event(SAMPLE_PAYLOAD)

    assert event is not None
    assert event.pr_number == 42
    assert event.pr_title == "Add discount calculator"
    assert event.repo_full_name == "owner/repo"
    assert event.action == "opened"
    assert event.head_sha == "abc1234567890"


def test_parse_pr_event_ignores_unhandled_actions():
    """Actions like 'closed' should return None so the bot skips them silently."""
    payload = {**SAMPLE_PAYLOAD, "action": "closed"}
    assert parse_pr_event(payload) is None


def test_parse_pr_event_handles_synchronize():
    """The 'synchronize' action (new push to existing PR) should also be parsed."""
    payload = {**SAMPLE_PAYLOAD, "action": "synchronize"}
    event = parse_pr_event(payload)
    assert event is not None
    assert event.action == "synchronize"


# ---------------------------------------------------------------------------
# 2. Webhook signature verification
# ---------------------------------------------------------------------------

def _make_signature(secret: str, body: bytes) -> str:
    """Helper: compute the HMAC-SHA256 signature GitHub would send."""
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def test_verify_signature_accepts_valid_signature():
    """A signature computed with the correct secret should be accepted."""
    body = b'{"action": "opened"}'
    secret = "test-secret"

    with patch("app.services.webhook_service.settings") as mock_settings:
        mock_settings.github_webhook_secret = secret
        sig = _make_signature(secret, body)
        assert verify_signature(body, sig) is True


def test_verify_signature_rejects_wrong_secret():
    """A signature computed with a different secret should be rejected."""
    body = b'{"action": "opened"}'

    with patch("app.services.webhook_service.settings") as mock_settings:
        mock_settings.github_webhook_secret = "correct-secret"
        sig = _make_signature("wrong-secret", body)
        assert verify_signature(body, sig) is False


def test_verify_signature_allows_all_when_no_secret_configured():
    """When no webhook secret is set, every request should be accepted (dev mode)."""
    with patch("app.services.webhook_service.settings") as mock_settings:
        mock_settings.github_webhook_secret = ""
        assert verify_signature(b"any body", "any signature") is True


# ---------------------------------------------------------------------------
# 3. Review comment generation from a mocked Claude response
# ---------------------------------------------------------------------------

FAKE_ANALYSIS = """\
## Review

🔴 Critical: Division by zero when discount is 0.
🟡 Warning: No input validation for negative values.
🟢 Suggestion: Add type hints and a docstring.

**Recommendation: Request Changes**
"""

SAMPLE_PR_EVENT = PREvent(
    action="opened",
    pr_number=42,
    pr_title="Add discount calculator",
    repo_full_name="owner/repo",
    diff_url="https://github.com/owner/repo/pull/42.diff",
    head_sha="abc1234",
)


def test_generate_review_comment_sets_recommendation():
    """'Request Changes' in the analysis text should set the recommendation field."""
    review = generate_review_comment(FAKE_ANALYSIS, SAMPLE_PR_EVENT)
    assert review.recommendation == "Request Changes"


def test_generate_review_comment_body_contains_pr_title():
    """The posted comment body should include the PR title in its header."""
    review = generate_review_comment(FAKE_ANALYSIS, SAMPLE_PR_EVENT)
    assert "Add discount calculator" in review.body


def test_generate_review_comment_body_contains_commit_sha():
    """The comment footer should include the short commit SHA for traceability."""
    review = generate_review_comment(FAKE_ANALYSIS, SAMPLE_PR_EVENT)
    assert "abc1234" in review.body


def test_generate_review_comment_summary_extracts_severity_lines():
    """The summary field should contain the severity-tagged lines from the analysis."""
    review = generate_review_comment(FAKE_ANALYSIS, SAMPLE_PR_EVENT)
    assert "🔴" in review.summary
    assert "🟡" in review.summary


def test_analyze_diff_calls_claude_api(monkeypatch):
    """analyze_diff should call the Anthropic client and return Claude's text response."""
    fake_block = MagicMock()
    fake_block.type = "text"
    fake_block.text = "Looks good!"

    fake_response = MagicMock()
    fake_response.content = [fake_block]
    fake_response.usage.input_tokens = 100
    fake_response.usage.cache_read_input_tokens = 0
    fake_response.usage.cache_creation_input_tokens = 0
    fake_response.usage.output_tokens = 10

    fake_client = MagicMock()
    fake_client.messages.create.return_value = fake_response

    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-test-key")

    with patch("app.clients.claude_client.anthropic.Anthropic", return_value=fake_client):
        from app.clients.claude_client import analyze_diff
        result = analyze_diff("+ some code change")

    assert result == "Looks good!"
    fake_client.messages.create.assert_called_once()

def test_generate_review_comment_handles_empty_analysis():
    result = generate_review_comment("", SAMPLE_PR_EVENT)
    assert result is not None

def test_verify_signature_handles_invalid_input():
    result = verify_signature(None, None)
    assert result is False