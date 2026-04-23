"""Health check route.

A lightweight endpoint that lets load balancers, uptime monitors, and
deployment scripts verify the server is running and accepting requests.
"""

from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
async def health() -> dict:
    """Return a simple status payload confirming the server is alive."""
    return {"status": "ok"}
