"""
Redis-backed rate limiter for FastAPI routes.

Strategy: sliding window approximation via Redis INCR + EXPIRE.
  Key:   ratelimit:{scope}:{identifier}
  TTL:   window_seconds
  Limit: max_requests per window

If Redis is unreachable, the check fails open (request allowed).
This is intentional — rate limiting is a protection layer, not a critical gate.
"""
import logging

import redis.asyncio as aioredis
from fastapi import HTTPException, Request

from app.core.settings import settings

logger = logging.getLogger(__name__)

_redis: aioredis.Redis | None = None


def _get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    return _redis


def _client_ip(request: Request) -> str:
    """Extract real client IP, honoring X-Forwarded-For for proxy/ALB setups."""
    forwarded_for = request.headers.get("x-forwarded-for", "")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"


async def check_rate_limit(
    request: Request,
    scope: str = "leads",
    max_requests: int = 3,
    window_seconds: int = 3600,
) -> None:
    """
    Raise HTTP 429 if the client IP has exceeded max_requests within window_seconds.
    Call this at the top of any route handler that needs rate limiting.
    """
    ip = _client_ip(request)
    key = f"ratelimit:{scope}:{ip}"

    try:
        r = _get_redis()
        count = await r.incr(key)
        if count == 1:
            await r.expire(key, window_seconds)
        if count > max_requests:
            logger.warning("rate_limit: %s exceeded %d requests for %s", ip, max_requests, scope)
            raise HTTPException(
                status_code=429,
                detail="Too many submissions. Please try again later.",
            )
    except HTTPException:
        raise
    except Exception as exc:
        # Redis unavailable — fail open so the service keeps running
        logger.error("rate_limit: Redis check failed (allowing request): %s", exc)
