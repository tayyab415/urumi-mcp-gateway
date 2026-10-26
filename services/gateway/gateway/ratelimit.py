"""A rate limit per credential on /mcp, checked after the credential is verified.

Traefik limits /mcp too, but it can only count by the raw `Authorization`
header, so one API key written with an extra space between "Bearer" and the
key gets a fresh allowance there. The gateway knows which credential a
request really carries, so it keeps one allowance per credential, however the
header is spelled.

The shape is Traefik's: up to `burst` requests at once, refilled at `average`
per `period`. The counts live in memory, which is enough for the gateway's one
replica (docs/design.md, section 10). Several replicas would need a shared
store such as Redis.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable

_DURATION = re.compile(r"^(\d+(?:\.\d+)?)(ms|s|m|h)$")
_SECONDS = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0}
_MAX_TRACKED = 10_000


def parse_period(text: str) -> float:
    """Seconds in a duration written the way Traefik's values are, such as "1s" or "1m"."""
    match = _DURATION.match(text.strip())
    if match is None:
        raise ValueError(f"a rate-limit period looks like 1s, 500ms or 1m, not {text!r}")
    return float(match[1]) * _SECONDS[match[2]]


class RateLimiter:
    """One token bucket per credential: `burst` tokens, refilled at `average` per `period_seconds`."""

    def __init__(self, average: float, period_seconds: float, burst: int, clock: Callable[[], float] = time.monotonic):
        self.rate = average / period_seconds
        self.burst = max(1, burst)
        self.clock = clock
        self._buckets: dict[str, tuple[float, float]] = {}  # credential id -> (tokens left, when counted)

    def wait(self, credential_id: str) -> float:
        """0 if this request may go ahead, and it is counted. Otherwise, seconds until one may."""
        now = self.clock()
        tokens, then = self._buckets.get(credential_id, (float(self.burst), now))
        tokens = min(float(self.burst), tokens + (now - then) * self.rate)
        if tokens >= 1:
            self._buckets[credential_id] = (tokens - 1, now)
            self._forget_idle(now)
            return 0.0
        self._buckets[credential_id] = (tokens, now)
        return (1 - tokens) / self.rate

    def _forget_idle(self, now: float) -> None:
        # Only credentials that passed the check get a bucket, so there are few. This
        # only stops the table growing for ever; a bucket that has refilled holds nothing.
        if len(self._buckets) > _MAX_TRACKED:
            self._buckets = {k: (t, w) for k, (t, w) in self._buckets.items() if t + (now - w) * self.rate < self.burst}
