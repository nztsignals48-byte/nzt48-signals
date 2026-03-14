"""
K-09: Token Bucket API Rate Limiter.
50 req/s capacity, 10/s regen. Reserves 20% for emergency flatten.
>80%: increase timeout. >90%: emergency only. 100%: HALT.
"""
import time
import logging
import asyncio
from typing import Optional

logger = logging.getLogger(__name__)


class TokenBucketRateLimiter:
    """Token bucket rate limiter for IBKR API calls.

    Reserves 20% of capacity for EMERGENCY_FLATTEN orders at all times.
    """

    def __init__(self, capacity: int = 50, refill_rate: float = 10.0,
                 emergency_reserve_pct: float = 0.20):
        self._capacity = capacity
        self._tokens = float(capacity)
        self._refill_rate = refill_rate
        self._emergency_reserve = int(capacity * emergency_reserve_pct)
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(self._capacity, self._tokens + elapsed * self._refill_rate)
        self._last_refill = now

    @property
    def utilization_pct(self) -> float:
        self._refill()
        return (1.0 - self._tokens / self._capacity) * 100

    async def acquire(self, is_emergency: bool = False) -> bool:
        async with self._lock:
            self._refill()
            available = self._tokens
            if not is_emergency:
                available -= self._emergency_reserve
            if available < 1.0:
                if is_emergency and self._tokens >= 1.0:
                    self._tokens -= 1.0
                    logger.warning("RATE_LIMITER: emergency token consumed (%.1f remaining)", self._tokens)
                    return True
                logger.error("RATE_LIMITER: exhausted (%.1f tokens, emergency=%s)", self._tokens, is_emergency)
                return False
            self._tokens -= 1.0
            util = self.utilization_pct
            if util > 90:
                logger.warning("RATE_LIMITER: >90%% utilization — emergency flatten only")
            elif util > 80:
                logger.warning("RATE_LIMITER: >80%% utilization — increasing timeouts")
            return True

    def get_recommended_timeout_ms(self) -> int:
        util = self.utilization_pct
        if util > 80:
            return 3000
        return 800

    def should_halt(self) -> bool:
        self._refill()
        return self._tokens < 1.0

    def status(self) -> dict:
        self._refill()
        return {
            "tokens": round(self._tokens, 1),
            "capacity": self._capacity,
            "utilization_pct": round(self.utilization_pct, 1),
            "emergency_reserve": self._emergency_reserve,
        }
