# AI Code Review Bot

A GitHub pull request reviewer that uses the Anthropic Claude API to automatically analyze code diffs and post structured review comments. When a PR is opened or pushed to, the bot fetches the diff, sends it to Claude for analysis, and posts a formatted review back to the PR within seconds.

Built as a practical demonstration of integrating a production-style LLM API pipeline with a real developer workflow.

---

## Key Features

- **Automated PR reviews** — triggers on `opened` and `synchronize` webhook events
- **Structured feedback** — severity-tagged findings (`🔴 Critical`, `🟡 Warning`, `🟢 Suggestion`) with an overall recommendation
- **Prompt caching** — reuses the system prompt across reviews to reduce token costs by ~90%
- **Retry logic** — exponential backoff on transient Claude and GitHub API failures
- **Webhook signature verification** — HMAC-SHA256 validation to reject spoofed requests
- **Async architecture** — reviews run as background tasks so GitHub always receives an immediate `202`
- **Structured logging** — timestamped log lines at every key step
- **Tested** — pytest suite with mocked external APIs (no real API calls in tests)

---

## Architecture

```
GitHub
  │
  │  POST /webhook  (pull_request event)
  ▼
FastAPI Server  ──── verify HMAC signature
  │                  parse payload → PREvent
  │
  │  BackgroundTask (immediate 202 returned to GitHub)
  ▼
Review Pipeline
  │
  ├── GitHub API  ──── fetch unified diff (with retry)
  │
  ├── Claude API  ──── analyze diff, prompt-cached system prompt (with retry)
  │
  └── GitHub API  ──── post review comment to PR
```

The server and the review pipeline are fully decoupled. GitHub never waits on Claude — it gets a `202 Accepted` the moment the request is validated, and the review is posted asynchronously.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Web framework | [FastAPI](https://fastapi.tiangolo.com/) |
| LLM | [Anthropic Claude](https://www.anthropic.com/) (`claude-sonnet-4-6`) via the official Python SDK |
| HTTP client | [httpx](https://www.python-httpx.org/) (async) |
| Dependency management | [uv](https://docs.astral.sh/uv/) |
| Testing | [pytest](https://pytest.org/) with `unittest.mock` |
| Runtime | Python 3.11+ |

---

## How It Works

1. **Webhook arrives** — GitHub sends a `pull_request` event to `POST /webhook`
2. **Signature check** — the server verifies the `X-Hub-Signature-256` header using HMAC-SHA256; invalid requests are rejected with `401`
3. **Payload parsed** — the action is checked (`opened` or `synchronize` only); others return `200` and are ignored
4. **Background task queued** — a `202` is returned to GitHub immediately; the review runs asynchronously
5. **Diff fetched** — the bot calls the GitHub API to download the PR's unified diff
6. **Claude reviews** — the diff is sent to Claude with a cached system prompt covering code quality, bugs, security, and performance
7. **Comment formatted** — Claude's response is wrapped with a header (PR title), severity summary, and a footer crediting the bot and pinning the commit SHA
8. **Comment posted** — the formatted review is posted to the PR via the GitHub Issues Comments API

---

## Review Criteria

Claude is instructed to review each diff across five areas:

| Area | What gets flagged |
|---|---|
| **Code Quality** | Style, readability, naming, DRY violations |
| **Bugs & Edge Cases** | Logic errors, unhandled nulls, race conditions |
| **Security** | Injection risks, secret exposure, missing input validation |
| **Performance** | Inefficient algorithms, redundant I/O, memory issues |
| **Suggestions** | Specific, explained improvements with clear priority |

---

## Reliability Features

### Structured Logging

Every key event is logged with a timestamp and level using Python's standard `logging` module. No `print()` statements.

```
12:01:00 [INFO]    Webhook received — event: pull_request
12:01:00 [INFO]    Queuing review — PR #42 (opened) in owner/repo
12:01:01 [INFO]    Reviewing PR #42: Add discount calculator
12:01:04 [INFO]    Claude tokens — input: 312, cache_read: 4821, cache_created: 0, output: 987
12:01:04 [INFO]    Posted review for PR #42 — Request Changes
```

### Retry with Exponential Backoff

Both the Claude API and GitHub API calls are wrapped with retry logic — no third-party libraries required.

- **Max retries:** 3 (4 total attempts)
- **Backoff delays:** 1 s → 2 s → 4 s
- **Retried:** rate limits (429), server errors (5xx), network timeouts
- **Not retried:** auth errors, malformed requests — these will never succeed on retry

```
12:01:01 [WARNING] Claude API — attempt 1/4 failed: rate_limit_error. Retrying in 1s…
12:01:02 [WARNING] Claude API — attempt 2/4 failed: rate_limit_error. Retrying in 2s…
12:01:04 [INFO]    Claude tokens — input: 312, cache_read: 4821 ...
```

### Webhook Signature Verification

When `GITHUB_WEBHOOK_SECRET` is set, every inbound request is verified using HMAC-SHA256 before any payload parsing occurs. Requests with missing or invalid signatures are rejected with `401`.

`hmac.compare_digest` is used instead of `==` to prevent timing attacks.

### Prompt Caching

The Claude system prompt (~450 tokens) is marked with `cache_control: {type: "ephemeral"}`. After the first review in a 5-minute window, the prompt is served from Anthropic's cache at roughly 10% of the normal input token cost.

```
cache_read: 4821   ← prompt served from cache (cheap)
cache_created: 0   ← no new cache write needed
```

### Tests

The pytest suite covers the core logic without making real API calls.

```
tests/test_review.py::test_parse_pr_event_returns_pr_event         PASSED
tests/test_review.py::test_parse_pr_event_ignores_unhandled_actions PASSED
tests/test_review.py::test_parse_pr_event_handles_synchronize       PASSED
tests/test_review.py::test_verify_signature_accepts_valid_signature  PASSED
tests/test_review.py::test_verify_signature_rejects_wrong_secret     PASSED
tests/test_review.py::test_verify_signature_allows_all_when_no_secret_configured PASSED
tests/test_review.py::test_generate_review_comment_sets_recommendation PASSED
tests/test_review.py::test_generate_review_comment_body_contains_pr_title PASSED
tests/test_review.py::test_generate_review_comment_body_contains_commit_sha PASSED
tests/test_review.py::test_generate_review_comment_summary_extracts_severity_lines PASSED
tests/test_review.py::test_analyze_diff_calls_claude_api            PASSED
tests/test_review.py::test_generate_review_comment_handles_empty_analysis PASSED
tests/test_review.py::test_verify_signature_handles_invalid_input   PASSED
```

---

## Project Structure

```
ai-code-review-bot/
├── app/
│   ├── main.py                    # FastAPI app, router registration
│   ├── api/routes/
│   │   ├── health.py              # GET /health
│   │   └── webhook.py             # POST /webhook
│   ├── clients/
│   │   ├── claude_client.py       # Anthropic API call + prompt caching
│   │   └── github_client.py       # Fetch diff, post comment
│   ├── core/
│   │   ├── config.py              # Environment variable access (Settings)
│   │   ├── logging.py             # Shared logger
│   │   └── retry.py               # Retry helpers (sync + async)
│   ├── models/
│   │   └── schemas.py             # PREvent, ReviewComment dataclasses
│   └── services/
│       ├── review_service.py      # Orchestration, comment formatting, error handling
│       └── webhook_service.py     # Payload parsing, signature verification
├── tests/
│   └── test_review.py             # pytest suite (all mocked)
├── .env.example
├── pyproject.toml
└── README.md
```

---

## Local Setup

### Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) — `pip install uv`
- An [Anthropic API key](https://console.anthropic.com)
- A GitHub Personal Access Token with `repo` scope

### 1. Install dependencies

```bash
uv sync
```

### 2. Configure environment

```bash
cp .env.example .env
```

| Variable | Required | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | Anthropic API key |
| `GITHUB_TOKEN` | Yes | GitHub PAT with `repo` scope |
| `GITHUB_REPO` | No | Fallback repo (`owner/repo`) if webhook omits it |
| `GITHUB_WEBHOOK_SECRET` | Recommended | HMAC secret for request verification |

### 3. Start the server

```bash
uv run uvicorn app.main:app --reload
```

The server runs on `http://localhost:8000`.

### 4. Expose to GitHub (local development)

GitHub webhooks require a public HTTPS URL. Use [ngrok](https://ngrok.com):

```bash
ngrok http 8000
```

### 5. Register the webhook on GitHub

1. Go to your repo → **Settings → Webhooks → Add webhook**
2. **Payload URL:** `https://<your-ngrok-url>/webhook`
3. **Content type:** `application/json`
4. **Secret:** value of `GITHUB_WEBHOOK_SECRET` in your `.env`
5. **Events:** select **Pull requests** only
6. Click **Add webhook**

Open or push to a pull request to trigger the first review.

---

## Running Tests

```bash
uv run pytest tests/ -v
```

All tests run without real API calls. The Claude client is mocked with `unittest.mock.patch`; webhook payloads use hardcoded dicts.

---

## Design Decisions

**Why async background tasks?**
GitHub expects a response within 10 seconds. Claude API calls can take 5–15 seconds depending on diff size. Running the review as a FastAPI `BackgroundTask` returns `202` immediately and processes the review after — decoupling response time from LLM latency.

**Why `max_retries=0` on the Anthropic client?**
The SDK retries automatically by default. Disabling it and handling retries explicitly means there's one consistent retry path with predictable logging instead of silent SDK retries mixing with our own.

**Why prompt caching?**
The system prompt is ~450 tokens and identical across every review. Caching it reduces input token costs by ~90% after the first request in each 5-minute window. For high-volume repos this adds up quickly.

**Why no third-party retry library?**
The requirements are simple: 3 retries, exponential backoff, a `should_retry` predicate. Adding `tenacity` or `stamina` would work, but a 50-line `retry.py` is easier to read, test, and debug.

**Why split into `clients/` and `services/`?**
`clients/` handles API mechanics (auth headers, HTTP calls, retries). `services/` handles business logic (orchestration, formatting, error handling). This separation makes each layer easy to mock in tests and easy to swap out independently — for example, swapping `claude_client.py` for a different LLM without touching review logic.

---

## Future Improvements

- **Post inline comments** — use the GitHub Pull Request Review API to attach comments to specific diff lines instead of a single PR-level comment
- **Configurable review depth** — let users choose between a quick scan and a thorough review via a PR label or comment command
- **Diff filtering** — skip auto-generated files (`package-lock.json`, migration files) and binary files before sending to Claude
- **Cost tracking** — log cumulative token usage per repo to make prompt caching savings visible over time
- **Webhook queue** — add a lightweight queue (e.g. Redis + background worker) to handle burst traffic without dropping reviews
- **Multi-repo support** — support a single deployment reviewing PRs across multiple repositories via a shared webhook endpoint

---

## License

MIT
