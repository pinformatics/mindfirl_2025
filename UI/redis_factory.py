"""Redis connection factory used by the Flask application."""

import os

import redis


def _env_or_default(name, default=None):
    """Return env value or fallback default when value is missing/blank."""
    value = os.environ.get(name)
    if value is None:
        return default

    normalized = value.strip()
    if normalized == "":
        return default

    return normalized


def create_redis_client():
    """Create a Redis client from environment variables."""
    redis_url = _env_or_default("REDIS_URL")
    if redis_url:
        return redis.Redis.from_url(redis_url, decode_responses=True)

    redis_port = int(_env_or_default("REDIS_PORT", "6379"))
    redis_use_tls = _env_or_default("REDIS_USE_TLS", "false").lower() == "true" or redis_port == 6380

    return redis.Redis(
        host=_env_or_default("REDIS_HOST", "localhost"),
        port=redis_port,
        username=_env_or_default("REDIS_USERNAME", None),
        password=_env_or_default("REDIS_PASSWORD", None),
        ssl=redis_use_tls,
        decode_responses=True,
    )
