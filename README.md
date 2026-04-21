# AI Code Review Bot

An automated GitHub pull request reviewer powered by [Claude](https://claude.ai) (Anthropic). When a PR is opened or updated, the bot fetches the diff, sends it to Claude for analysis, and posts a structured review comment directly on the PR.

## What it does

- Listens for GitHub `pull_request` webhook events (`opened`, `synchronize`)
- Fetches the PR's unified diff via the GitHub API
- Sends the diff to Claude (`claude-sonnet-4-6`) for a thorough code review
- Posts the review as a comment on the PR, with severity indicators and an overall recommendation

### Review criteria

Claude reviews each PR for:

| Area | What's checked |
|------|---------------|
| **Code Quality** | Idioms, readability, naming, DRY violations |
| **Bugs & Edge Cases** | Logic errors, unhandled nulls, race conditions |
| **Security** | Injection risks, secret exposure, input validation |
| **Performance** | Inefficient algorithms, unnecessary I/O |
| **Actionable Suggestions** | Severity-tagged, specific, and explained |

Comments use `🔴 Critical`, `🟡 Warning`, and `🟢 Suggestion` labels so reviewers can triage quickly.

## Setup

### 1. Install dependencies

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
```

### 2. Configure environment

```bash
cp .env.example .env
```

Edit `.env` and fill in:

| Variable | Description |
|----------|-------------|
| `ANTHROPIC_API_KEY` | Your Anthropic API key from [console.anthropic.com](https://console.anthropic.com) |
| `GITHUB_TOKEN` | GitHub Personal Access Token with `repo` and `pull_requests` scopes |
| `GITHUB_REPO` | Default repo in `owner/repo` format (used as fallback) |
| `GITHUB_WEBHOOK_SECRET` | (Optional) Secret for verifying GitHub webhook signatures |

### 3. Start the server

```bash
uv run uvicorn src.main:app --reload --port 8000
```

The server listens on `http://localhost:8000`.

### 4. Expose to GitHub

GitHub webhooks require a public URL. Use [ngrok](https://ngrok.com) for local development:

```bash
ngrok http 8000
```

Copy the `https://` forwarding URL (e.g. `https://abc123.ngrok.io`).

### 5. Configure the GitHub webhook

1. Go to your repository → **Settings → Webhooks → Add webhook**
2. Set **Payload URL** to `https://your-url/webhook`
3. Set **Content type** to `application/json`
4. Set **Secret** to the value of `GITHUB_WEBHOOK_SECRET` in your `.env`
5. Select **Individual events** → check **Pull requests**
6. Click **Add webhook**

Open a pull request to trigger the first review.

## API endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Health check |
| `POST` | `/webhook` | GitHub webhook receiver |

## Project structure

```
ai-code-review-bot/
├── src/
│   ├── __init__.py
│   ├── main.py         # FastAPI app and webhook handler
│   └── review_bot.py   # Claude API calls, GitHub API calls, review logic
├── .env.example        # Environment variable template
├── pyproject.toml      # Dependencies (managed with uv)
└── README.md
```

## Prompt caching

The Claude system prompt is marked with `cache_control: {type: "ephemeral"}`. On the first review it's written to Anthropic's cache; subsequent reviews within the 5-minute TTL window read it at ~10% of the normal input cost. This significantly reduces per-review cost when the bot is active.

You can verify caching is working by checking the server logs:

```
[Claude] tokens — input: 312, cache_read: 4821, cache_created: 0, output: 1203
```

A non-zero `cache_read` value means the system prompt was served from cache.

## Security notes

- **Webhook signature verification** is enabled automatically when `GITHUB_WEBHOOK_SECRET` is set. Always use this in production.
- The bot never stores PR diffs or review content.
- Large diffs are truncated at 80,000 characters to stay within context limits.

## License

MIT
