"""GitHub webhook route.

Responsible only for the HTTP layer:
  - Reading the raw request body and headers.
  - Delegating signature verification and payload parsing to webhook_service.
  - Dispatching the review pipeline as a FastAPI background task so GitHub
    receives an immediate 202 instead of waiting for Claude to respond.

No business logic or API client code lives here.
"""

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from app.core.logging import logger
from app.services.review_service import run_review_safe
from app.services.webhook_service import parse_pr_event, verify_signature

router = APIRouter()


@router.post("/webhook")
async def github_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_github_event: str = Header(default=""),
    x_hub_signature_256: str = Header(default=""),
) -> JSONResponse:
    """Receive a GitHub webhook event and queue an AI code review.

    GitHub must be configured to send `pull_request` events to this URL.
    Signature verification runs when GITHUB_WEBHOOK_SECRET is set in .env.
    The review itself runs in the background — GitHub gets an immediate 202.
    """
    # Log every inbound webhook so we have a full audit trail of what GitHub sent.
    logger.info("Webhook received — event: %s", x_github_event)

    body = await request.body()

    # Reject requests whose HMAC signature doesn't match our secret.
    if not verify_signature(body, x_hub_signature_256):
        logger.warning("Webhook rejected — invalid signature")
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    # Ignore everything except pull_request events.
    if x_github_event != "pull_request":
        logger.info("Ignoring event type: %s", x_github_event)
        return JSONResponse(
            {"message": f"Ignoring event type: {x_github_event}"}, status_code=200
        )

    payload = await request.json()

    try:
        pr_event = parse_pr_event(payload)
    except ValueError as exc:
        logger.error("Failed to parse webhook payload: %s", exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # None means a PR action we don't handle (e.g. "closed", "labeled").
    if pr_event is None:
        action = payload.get("action", "unknown")
        logger.info("Ignoring PR action: %s", action)
        return JSONResponse(
            {"message": f"Ignoring PR action: {action}"}, status_code=200
        )

    # Log the key fields so every queued review is traceable in the logs.
    logger.info(
        "Queuing review — PR #%d (%s) in %s",
        pr_event.pr_number,
        pr_event.action,
        pr_event.repo_full_name,
    )
    background_tasks.add_task(run_review_safe, pr_event)

    return JSONResponse(
        {
            "message": f"Review queued for PR #{pr_event.pr_number}",
            "pr_number": pr_event.pr_number,
            "action": pr_event.action,
        },
        status_code=202,
    )
