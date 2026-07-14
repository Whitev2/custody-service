"""
Redis client for distributed locks and caching.

Based on backend implementation with:
- Sentinel support for dev/prod
- Connection pooling
- Retry logic with exponential backoff
"""
import asyncio
import socket

from redis import RedisError, ConnectionError, TimeoutError
from redis.asyncio import Redis, ConnectionPool
from redis.asyncio.sentinel import Sentinel, MasterNotFoundError

from app.config import cfg, log


# Global connection pool and client
_redis_pool: ConnectionPool | None = None
_redis_client: Redis | None = None


async def init_redis() -> None:
    """Initialize Redis with connection pool and retry logic."""
    global _redis_pool, _redis_client

    if _redis_client is not None:
        return

    max_retries = 3
    for attempt in range(max_retries):
        try:
            # For local - use standalone Redis
            log.info(f"Initializing Redis connection (attempt {attempt + 1}/{max_retries})")
            
            if cfg.redis.PASSWORD:
                url = f"redis://:{cfg.redis.PASSWORD}@{cfg.redis.HOST}:{cfg.redis.PORT}/{cfg.redis.DB}"
            else:
                url = f"redis://{cfg.redis.HOST}:{cfg.redis.PORT}/{cfg.redis.DB}"
            
            _redis_pool = ConnectionPool.from_url(
                url=url,
                decode_responses=True,
                socket_connect_timeout=10,
                socket_timeout=10,
                retry_on_timeout=True,
                health_check_interval=30,
                max_connections=50,
                retry_on_error=[ConnectionError, TimeoutError],
            )
            _redis_client = Redis(connection_pool=_redis_pool)

            # Test connection
            await _redis_client.ping()
            log.info(f"✅ Redis connected: {cfg.redis.HOST}:{cfg.redis.PORT}/{cfg.redis.DB}")
            return

        except (RedisError, MasterNotFoundError, socket.gaierror, OSError, TimeoutError, ConnectionError) as e:
            error_type = type(e).__name__
            log.warning(
                f"Redis connection error [{error_type}] (attempt {attempt + 1}/{max_retries}): {e}"
            )

            if attempt == max_retries - 1:
                log.error(f"❌ Redis unavailable after {max_retries} attempts")
                # Don't raise - Redis is optional for Custody
                return

            # Exponential backoff
            backoff = (2 ** attempt) + (0.1 * attempt)
            log.info(f"Retrying Redis connection in {backoff:.1f}s...")
            await asyncio.sleep(backoff)


async def close_redis() -> None:
    """Close Redis connection and pool."""
    global _redis_client, _redis_pool
    
    if _redis_client:
        await _redis_client.aclose()
        _redis_client = None
    if _redis_pool:
        await _redis_pool.aclose()
        _redis_pool = None
        log.info("Redis connection closed")


async def get_redis() -> Redis | None:
    """Get Redis client (initializes if needed)."""
    global _redis_client
    
    if _redis_client is None:
        await init_redis()
    
    return _redis_client


class DistributedLock:
    """
    Redis-based distributed lock.
    
    Usage:
        async with DistributedLock("my-task", ttl=60) as acquired:
            if acquired:
                # do work - only one pod will execute
            else:
                # lock held by another process
    
    Features:
        - Non-blocking (returns immediately if lock held)
        - Auto-release via TTL (if holder crashes)
        - Graceful fallback if Redis unavailable
    """

    def __init__(self, name: str, ttl: int = 300):
        """
        Args:
            name: Lock name (will be prefixed with "lock:")
            ttl: Lock TTL in seconds (auto-release if holder crashes)
        """
        self.name = f"lock:{name}"
        self.ttl = ttl
        self._redis: Redis | None = None
        self._acquired = False

    async def __aenter__(self) -> bool:
        """Try to acquire lock."""
        self._redis = await get_redis()

        if not self._redis:
            # Redis not available - allow execution (better than blocking)
            log.warning(f"⚠️ Redis unavailable, proceeding without lock: {self.name}")
            self._acquired = True
            return True

        try:
            # SET NX with TTL - atomic operation
            self._acquired = await self._redis.set(
                self.name,
                "1",
                nx=True,  # Only set if not exists
                ex=self.ttl,  # TTL in seconds
            )

            if self._acquired:
                log.debug(f"🔒 Lock acquired: {self.name}")
            else:
                log.debug(f"🔒 Lock held by another process: {self.name}")

            return bool(self._acquired)

        except Exception as e:
            log.error(f"❌ Lock error: {e}")
            # On error - allow execution
            self._acquired = True
            return True

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Release lock."""
        if self._acquired and self._redis:
            try:
                await self._redis.delete(self.name)
                log.debug(f"🔓 Lock released: {self.name}")
            except Exception as e:
                log.error(f"❌ Failed to release lock: {e}")

        self._acquired = False
