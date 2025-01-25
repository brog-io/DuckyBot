import time
from collections import defaultdict


class RateLimiter:
    def __init__(self, rate: int, per: float, cleanup_interval: float = 3600):
        self.rate = rate
        self.per = per
        self.tokens = defaultdict(lambda: self.rate)
        self.last_update = defaultdict(float)
        self.cleanup_interval = cleanup_interval
        self.last_cleanup = time.time()

    def check(self, key: str) -> tuple[bool, float]:
        now = time.time()

        # Periodically cleanup old entries
        if now - self.last_cleanup > self.cleanup_interval:
            self._cleanup(now)
            self.last_cleanup = now

        last_update = self.last_update[key]
        time_passed = now - last_update

        self.tokens[key] = min(
            self.rate, self.tokens[key] + (time_passed * self.rate / self.per)
        )
        self.last_update[key] = now

        if self.tokens[key] >= 1:
            self.tokens[key] -= 1
            return True, 0

        return False, (1 - self.tokens[key]) * (self.per / self.rate)

    def _cleanup(self, now: float) -> None:
        # Remove entries that haven't been accessed in more than 2 periods
        stale_time = now - (self.per * 2)
        stale_keys = [k for k, v in self.last_update.items() if v < stale_time]
        for k in stale_keys:
            del self.tokens[k]
            del self.last_update[k]
