"""Anthropic Claude API client.

Responsible for the single outbound call to the Claude Messages API:
sending a PR diff and receiving a code review.

Prompt caching is applied to the system prompt so it is tokenised only
once per 5-minute TTL window.  The cache_creation_input_tokens /
cache_read_input_tokens fields in the response usage tell you whether a
cache hit occurred — check the logs to verify.

No business logic lives here — this module only knows how to format a
prompt and parse a response.
"""

import anthropic

from app.core.config import settings
from app.core.logging import logger
from app.core.retry import retry_sync

# The system prompt is the stable, expensive part of every review request.
# Marking it with cache_control keeps it cached across consecutive reviews,
# reducing input token costs by ~90 % after the first request in each
# 5-minute window.
_REVIEW_SYSTEM_PROMPT = """You are an expert code reviewer performing a thorough pull request review.

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

# Diffs larger than this are truncated before being sent to Claude to stay
# within the model's context window and avoid excessive token costs.
_MAX_DIFF_CHARS = 80_000


def _is_retryable_claude(exc: Exception) -> bool:
    """Return True for transient Claude API errors that are worth retrying.

    Rate limits and server errors are transient — a retry after a short wait
    usually succeeds.  Auth errors and bad requests are permanent — retrying
    would waste time and quota.
    """
    if isinstance(exc, anthropic.RateLimitError):
        return True
    if isinstance(exc, anthropic.APIConnectionError):
        return True
    if isinstance(exc, anthropic.APIStatusError):
        return exc.status_code >= 500  # 5xx server errors only; not 4xx
    return False


def analyze_diff(diff: str) -> str:
    """Send a PR diff to Claude and return the raw markdown review text.

    Creates a fresh Anthropic client per call so the API key is always read
    from the current environment (important for tests that swap credentials).

    max_retries=0 disables the SDK's built-in retry so our own retry_sync
    loop is the single source of retry logic (avoids double-retrying).
    """
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key, max_retries=0)

    if len(diff) > _MAX_DIFF_CHARS:
        diff = diff[:_MAX_DIFF_CHARS] + "\n\n[...diff truncated at 80,000 characters...]"

    def _call() -> anthropic.types.Message:
        return client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4096,
            system=[
                {
                    "type": "text",
                    "text": _REVIEW_SYSTEM_PROMPT,
                    # cache_control tells Claude to cache this block for 5 minutes.
                    # Subsequent requests with the same prefix skip re-tokenisation.
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"Please review the following pull request diff:\n\n"
                        f"```diff\n{diff}\n```"
                    ),
                }
            ],
        )

    response = retry_sync(_call, should_retry=_is_retryable_claude)

    cache_read = response.usage.cache_read_input_tokens or 0
    cache_created = response.usage.cache_creation_input_tokens or 0
    logger.info(
        "Claude tokens — input: %d, cache_read: %d, cache_created: %d, output: %d",
        response.usage.input_tokens,
        cache_read,
        cache_created,
        response.usage.output_tokens,
    )

    return next(block.text for block in response.content if block.type == "text")
