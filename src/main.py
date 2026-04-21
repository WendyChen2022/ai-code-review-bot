"""FastAPI webhook server for the AI code review bot."""

import os

import anthropic
import httpx
from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from src.review_bot import PREvent, receive_webhook, run_review, verify_webhook_signature

load_dotenv()

app = FastAPI(
    title="AI Code Review Bot",
    description="Automatically reviews GitHub pull requests using Claude.",
    version="0.1.0",
)


@app.get("/health")
async def health() -> dict:
    """Health check endpoint."""
    return {"status": "ok"}


@app.post("/webhook")
async def github_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_github_event: str = Header(default=""),
    x_hub_signature_256: str = Header(default=""),
) -> JSONResponse:
    """Receive GitHub webhook events and trigger AI code reviews.

    GitHub must be configured to send `pull_request` events to this endpoint.
    Signature verification is performed when GITHUB_WEBHOOK_SECRET is set.
    Reviews are processed in the background so GitHub receives an immediate 200.
    """
    body = await request.body()

    if not verify_webhook_signature(body, x_hub_signature_256):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    if x_github_event != "pull_request":
        return JSONResponse(
            {"message": f"Ignoring event type: {x_github_event}"}, status_code=200
        )

    payload = await request.json()

    try:
        pr_event: PREvent | None = receive_webhook(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if pr_event is None:
        action = payload.get("action", "unknown")
        return JSONResponse(
            {"message": f"Ignoring PR action: {action}"}, status_code=200
        )

    background_tasks.add_task(_review_with_error_handling, pr_event)

    return JSONResponse(
        {
            "message": f"Review queued for PR #{pr_event.pr_number}",
            "pr_number": pr_event.pr_number,
            "action": pr_event.action,
        },
        status_code=202,
    )


async def _review_with_error_handling(pr_event: PREvent) -> None:
    """Run the review pipeline, logging any errors so the background task doesn't crash silently."""
    try:
        await run_review(pr_event)
    except anthropic.AuthenticationError:
        print("[Error] Invalid ANTHROPIC_API_KEY — check your .env file.")
    except anthropic.RateLimitError as exc:
        print(f"[Error] Claude rate limit hit for PR #{pr_event.pr_number}: {exc}")
    except anthropic.APIStatusError as exc:
        print(f"[Error] Claude API error for PR #{pr_event.pr_number}: {exc.status_code} {exc.message}")
    except httpx.HTTPStatusError as exc:
        print(f"[Error] GitHub API error for PR #{pr_event.pr_number}: {exc.response.status_code} {exc.response.text}")
    except httpx.RequestError as exc:
        print(f"[Error] Network error for PR #{pr_event.pr_number}: {exc}")
    except Exception as exc:
        print(f"[Error] Unexpected error reviewing PR #{pr_event.pr_number}: {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("src.main:app", host="0.0.0.0", port=port, reload=True)
