"""Application entry point.

Creates the FastAPI app and registers all route routers.
Keep this file thin — it wires things together but owns no logic itself.
"""

import uvicorn
from fastapi import FastAPI

from app.api.routes import health, webhook
from app.core.config import settings

app = FastAPI(
    title="AI Code Review Bot",
    description="Automatically reviews GitHub pull requests using Claude.",
    version="0.1.0",
)

app.include_router(health.router)
app.include_router(webhook.router)


if __name__ == "__main__":
    # Run directly with `python -m app.main` for quick local testing.
    # For production / hot-reload use: uv run uvicorn app.main:app --reload
    uvicorn.run("app.main:app", host="0.0.0.0", port=settings.port, reload=True)
