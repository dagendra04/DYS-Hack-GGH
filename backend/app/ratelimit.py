import time
from collections import defaultdict, deque


class SlidingLimiter:
    def __init__(self):
        self.hits = defaultdict(deque)

    def allow(self, key: str, limit: int, window: float = 60.0) -> bool:
        now = time.monotonic()
        q = self.hits[key]
        while q and now - q[0] > window:
            q.popleft()
        if len(q) >= limit:
            return False
        q.append(now)
        return True


limiter = SlidingLimiter()
