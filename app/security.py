import hashlib
import hmac
import time

from fastapi import Header, HTTPException, Request, status
from redis.exceptions import RedisError

from app.config import get_settings
from app.jobs import redis_client


def api_keys_match(provided: str, expected: str) -> bool:
    return hmac.compare_digest(provided.encode(), expected.encode())


async def require_api_key(request: Request, x_api_key: str = Header(default="")) -> str:
    settings = get_settings()
    if not x_api_key or not any(api_keys_match(x_api_key, key) for key in settings.accepted_api_keys):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")
    request.state.api_key_hash = hashlib.sha256(x_api_key.encode()).hexdigest()[:16]
    return x_api_key


async def enforce_rate_limit(request: Request) -> None:
    settings = get_settings()
    forwarded = request.headers.get("x-forwarded-for", "").split(",", maxsplit=1)[0].strip()
    if settings.trust_proxy_headers and forwarded:
        identity = f"ip:{forwarded}"
    else:
        identity = f"key:{getattr(request.state, 'api_key_hash', 'unknown')}"
    bucket = int(time.time() // settings.rate_limit_window_seconds)
    key = f"obaol-ocr:rate:{identity}:{bucket}"
    try:
        connection = redis_client(settings)
        count = connection.incr(key)
        if count == 1:
            connection.expire(key, settings.rate_limit_window_seconds + 1)
    except RedisError as exc:
        if settings.redis_required:
            raise HTTPException(503, "Rate limiter is unavailable") from exc
        return
    if count > settings.rate_limit_requests:
        raise HTTPException(status_code=429, detail="Rate limit exceeded")
