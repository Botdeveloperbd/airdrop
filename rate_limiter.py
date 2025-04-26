# rate_limiter.py
from datetime import datetime

class RateLimiter:
    def __init__(self):
        self.limits = {}

    def check_rate_limit(self, user_id, action):
        now = datetime.now().timestamp()
        key = f"{user_id}_{action}"
        if key in self.limits:
            last_time = self.limits[key]
            if now - last_time < 60:  # 1-minute cooldown
                return False
        self.limits[key] = now
        return True