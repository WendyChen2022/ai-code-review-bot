"""Centralised configuration — reads environment variables from .env.

All other modules import `settings` from here instead of calling
os.environ or load_dotenv themselves.  Values are read lazily (at access
time, not at import time) so tests can load a custom .env before the
first property access.
"""

import os

from dotenv import load_dotenv

# Override any existing shell env vars with values from .env so the app
# always uses the project's own credentials during local development.
load_dotenv(override=True)


def _require(key: str) -> str:
    """Return the value of a required environment variable.

    Raises RuntimeError with a clear message if the variable is missing or
    empty, so misconfiguration is caught immediately at startup rather than
    producing a cryptic error deep in a request handler.
    """
    val = os.environ.get(key, "")
    if not val:
        raise RuntimeError(f"Missing required environment variable: {key}")
    return val


class Settings:
    """Typed accessors for every environment variable the app needs.

    Using properties keeps reads lazy — the variable is only looked up
    when the property is accessed, not when the module is imported.
    """

    @property
    def anthropic_api_key(self) -> str:
        return _require("ANTHROPIC_API_KEY")

    @property
    def github_token(self) -> str:
        return _require("GITHUB_TOKEN")

    @property
    def github_repo(self) -> str:
        """Fallback repo (owner/repo) when the webhook payload omits it."""
        return os.environ.get("GITHUB_REPO", "")

    @property
    def github_webhook_secret(self) -> str:
        """Optional HMAC secret for verifying GitHub webhook payloads."""
        return os.environ.get("GITHUB_WEBHOOK_SECRET", "")

    @property
    def port(self) -> int:
        return int(os.environ.get("PORT", "8000"))


# Single shared instance — import this everywhere instead of the class.
settings = Settings()
