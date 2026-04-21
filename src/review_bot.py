"""Core AI code review logic: parse webhooks, call Claude API, post GitHub comments."""

import hashlib
import hmac
import os
from dataclasses import dataclass

import anthropic
import httpx
from dotenv import load_dotenv

load_dotenv(override=True)

# Read at call time so tests can load dotenv before importing this module
def _get_env(key: str, required: bool = True) -> str:
    val = os.environ.get(key, "")
    if required and not val:
        raise RuntimeError(f"Missing required environment variable: {key}")
    return val

# Cached across all requests — only written to cache once per ~5 min TTL
REVIEW_SYSTEM_PROMPT = """You are an expert code reviewer performing a thorough pull request review.

Analyze the provided git diff and give a structured, actionable code review covering:

**Code Quality & Best Practices**
- Adherence to language idioms and conventions
- Readability, naming, and structure
- DRY principles and unnecessary duplication

**Potential Bugs & Edge Cases**
- Logic errors or off-by-one mistakes
- Unhandled edge cases or null/empty inputs
- Race conditions or concurrency issues

**Security Vulnerabilities**
- Injection risks (SQL, command, XSS)
- Insecure data handling or exposure of secrets
- Authentication and authorization gaps
- Input validation issues

**Performance**
- Inefficient algorithms or data structures
- Unnecessary database or network calls
- Memory leaks or excessive allocations

**Actionable Suggestions**
- Be specific: reference the exact function or line when possible
- Distinguish blocking issues from optional improvements
- Explain *why* each suggestion matters, not just what to change

Format your response as a clear markdown review. Use "🔴 Critical", "🟡 Warning", or "🟢 Suggestion" prefixes to indicate severity. End with a brief overall assessment and a recommendation: Approve, Request Changes, or Comment.
"""


@dataclass
class PREvent:
    action: str
    pr_number: int
    pr_title: str
    repo_full_name: str
    diff_url: str
    head_sha: str


@dataclass
class ReviewComment:
    summary: str
    body: str
    recommendation: str


def receive_webhook(payload: dict) -> PREvent | None:
    """Parse a GitHub pull_request webhook payload into a PREvent.

    Returns None if the event action is not one we handle (opened/synchronize).
    """
    action = payload.get("action", "")
    if action not in ("opened", "synchronize"):
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
        repo_full_name=repo.get("full_name", _get_env("GITHUB_REPO", required=False)),
        diff_url=pr.get("diff_url", ""),
        head_sha=pr.get("head", {}).get("sha", ""),
    )


def verify_webhook_signature(body: bytes, signature_header: str) -> bool:
    """Verify the GitHub webhook HMAC-SHA256 signature.

    Returns True if the signature is valid or if no webhook secret is configured.
    """
    if not _get_env("GITHUB_WEBHOOK_SECRET", required=False):
        return True

    if not signature_header.startswith("sha256="):
        return False

    expected = "sha256=" + hmac.new(
        _get_env("GITHUB_WEBHOOK_SECRET", required=False).encode(),
        body,
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(expected, signature_header)


async def fetch_pr_diff(diff_url: str) -> str:
    """Fetch the unified diff for a pull request from GitHub."""
    headers = {
        "Authorization": f"Bearer {_get_env('GITHUB_TOKEN')}",
        "Accept": "application/vnd.github.v3.diff",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = client.get(diff_url, headers=headers, follow_redirects=True)
        response.raise_for_status()
        return response.text


def analyze_code_diff(diff: str) -> str:
    """Send a PR diff to Claude API for code review, using prompt caching on the system prompt.

    The system prompt is marked with cache_control so it's only tokenized once
    per 5-minute TTL window, cutting costs significantly on repeated reviews.

    Returns the raw markdown review text from Claude.
    """
    client = anthropic.Anthropic(api_key=_get_env("ANTHROPIC_API_KEY"))

    # Truncate extremely large diffs to stay within context limits
    max_diff_chars = 80_000
    if len(diff) > max_diff_chars:
        diff = diff[:max_diff_chars] + "\n\n[...diff truncated at 80,000 characters...]"

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        system=[
            {
                "type": "text",
                "text": REVIEW_SYSTEM_PROMPT,
                # Cache the system prompt — it's large and identical across all reviews
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[
            {
                "role": "user",
                "content": f"Please review the following pull request diff:\n\n```diff\n{diff}\n```",
            }
        ],
    )

    cache_read = response.usage.cache_read_input_tokens or 0
    cache_created = response.usage.cache_creation_input_tokens or 0
    print(
        f"[Claude] tokens — input: {response.usage.input_tokens}, "
        f"cache_read: {cache_read}, cache_created: {cache_created}, "
        f"output: {response.usage.output_tokens}"
    )

    return next(block.text for block in response.content if block.type == "text")


def generate_review_comment(analysis: str, pr_event: PREvent) -> ReviewComment:
    """Format Claude's raw analysis into a structured GitHub PR comment.

    Extracts the recommendation line and wraps the body in a collapsible header
    so long reviews don't overwhelm the PR timeline.
    """
    recommendation = "Comment"
    lower = analysis.lower()
    if "request changes" in lower:
        recommendation = "Request Changes"
    elif "approve" in lower:
        recommendation = "Approve"

    summary_lines = []
    for line in analysis.splitlines():
        stripped = line.strip()
        if stripped.startswith(("🔴", "🟡", "🟢", "#")):
            summary_lines.append(stripped)
        if len(summary_lines) >= 5:
            break
    summary = "\n".join(summary_lines) if summary_lines else analysis[:200]

    body = (
        f"## 🤖 AI Code Review — {pr_event.pr_title}\n\n"
        f"{analysis}\n\n"
        f"---\n"
        f"*Reviewed by [ai-code-review-bot](https://github.com/anthropics/anthropic-sdk-python) "
        f"using Claude `claude-sonnet-4-6` · commit `{pr_event.head_sha[:7]}`*"
    )

    return ReviewComment(summary=summary, body=body, recommendation=recommendation)


async def post_github_comment(
    repo_full_name: str,
    pr_number: int,
    comment: ReviewComment,
) -> dict:
    """Post the review comment to a GitHub pull request via the REST API.

    Uses the Issues Comments endpoint so the review appears in the PR timeline.
    Returns the created comment object from GitHub.
    """
    url = f"https://api.github.com/repos/{repo_full_name}/issues/{pr_number}/comments"
    headers = {
        "Authorization": f"Bearer {_get_env('GITHUB_TOKEN')}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(url, headers=headers, json={"body": comment.body})
        response.raise_for_status()
        return response.json()


async def run_review(pr_event: PREvent) -> ReviewComment:
    """Orchestrate the full review pipeline for a single PR event.

    Fetches the diff, calls Claude, formats the comment, and posts it to GitHub.
    """
    print(f"[Bot] Reviewing PR #{pr_event.pr_number}: {pr_event.pr_title}")

    diff = await fetch_pr_diff(pr_event.diff_url)
    if not diff.strip():
        print(f"[Bot] PR #{pr_event.pr_number} has an empty diff — skipping.")
        return ReviewComment(
            summary="No changes to review.",
            body="No diff content found for this pull request.",
            recommendation="Comment",
        )

    analysis = analyze_code_diff(diff)
    review = generate_review_comment(analysis, pr_event)

    await post_github_comment(pr_event.repo_full_name, pr_event.pr_number, review)
    print(f"[Bot] Posted review for PR #{pr_event.pr_number} — {review.recommendation}")
    return review
