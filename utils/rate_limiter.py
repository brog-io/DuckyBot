import time
from collections import defaultdict


class RateLimiter:
    def __init__(self, rate: int, per: float):
        self.rate = rate
        self.per = per
        self.tokens = defaultdict(lambda: self.rate)
        self.last_update = defaultdict(float)

    def check(self, key: str) -> tuple[bool, float]:
        now = time.time()
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
