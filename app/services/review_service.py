"""Review orchestration and comment formatting.

Responsible for:
  - Formatting Claude's raw analysis text into a structured ReviewComment
    (generate_review_comment).
  - Orchestrating the full review pipeline: fetch diff → analyse → format
    → post (run_review).
  - Wrapping run_review in error handling so background task failures are
    logged instead of silently swallowed (run_review_safe).

This is the glue layer between the two clients (claude_client, github_client)
and the route handlers.  It contains business logic but no HTTP or API
knowledge.
"""

import anthropic
import httpx

from app.clients.claude_client import analyze_diff
from app.clients.github_client import fetch_pr_diff, post_pr_comment
from app.core.logging import logger
from app.models.schemas import PREvent, ReviewComment


def generate_review_comment(analysis: str, pr_event: PREvent) -> ReviewComment:
    """Format Claude's raw markdown analysis into a ReviewComment.

    Extracts a short summary of the most severe findings for logging, then
    builds the full GitHub comment body with a header, the analysis, and a
    footer crediting the bot and pinning the reviewed commit SHA.
    """
    # Determine overall recommendation from Claude's closing line.
    recommendation = "Comment"
    lower = analysis.lower()
    if "request changes" in lower:
        recommendation = "Request Changes"
    elif "approve" in lower:
        recommendation = "Approve"

    # Pull the first few severity-tagged or heading lines for the summary.
    summary_lines: list[str] = []
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


async def run_review(pr_event: PREvent) -> ReviewComment:
    """Orchestrate the full review pipeline for one PR event.

    Steps:
      1. Fetch the unified diff from GitHub.
      2. Send it to Claude for analysis.
      3. Format the analysis into a ReviewComment.
      4. Post the comment back to the PR.

    Returns the ReviewComment so callers can inspect it (e.g. in tests).
    """
    logger.info("Reviewing PR #%d: %s", pr_event.pr_number, pr_event.pr_title)

    diff = await fetch_pr_diff(pr_event.diff_url)
    if not diff.strip():
        logger.info("PR #%d has an empty diff — skipping.", pr_event.pr_number)
        return ReviewComment(
            summary="No changes to review.",
            body="No diff content found for this pull request.",
            recommendation="Comment",
        )

    analysis = analyze_diff(diff)
    review = generate_review_comment(analysis, pr_event)

    await post_pr_comment(pr_event.repo_full_name, pr_event.pr_number, review)
    logger.info(
        "Posted review for PR #%d — %s", pr_event.pr_number, review.recommendation
    )
    return review


async def run_review_safe(pr_event: PREvent) -> None:
    """Run the review pipeline and log any errors instead of raising them.

    Used as the FastAPI background task so a failed review doesn't crash
    the worker process or produce an unhandled exception in the logs.
    Each exception type gets a specific message to make debugging easier.
    """
    try:
        await run_review(pr_event)
    except anthropic.AuthenticationError:
        logger.error("Invalid ANTHROPIC_API_KEY — check your .env file.")
    except anthropic.RateLimitError as exc:
        logger.error("Claude rate limit hit for PR #%d: %s", pr_event.pr_number, exc)
    except anthropic.APIStatusError as exc:
        logger.error(
            "Claude API error for PR #%d: %s %s",
            pr_event.pr_number,
            exc.status_code,
            exc.message,
        )
    except httpx.HTTPStatusError as exc:
        logger.error(
            "GitHub API error for PR #%d: %s %s",
            pr_event.pr_number,
            exc.response.status_code,
            exc.response.text,
        )
    except httpx.RequestError as exc:
        logger.error("Network error for PR #%d: %s", pr_event.pr_number, exc)
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "Unexpected error reviewing PR #%d: %s: %s",
            pr_event.pr_number,
            type(exc).__name__,
            exc,
        )
