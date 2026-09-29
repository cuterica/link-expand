"""Tune actual transfers without separate speed tests or location services."""

from collections import deque
import threading
import time


class TransferMeter:
    def __init__(self):
        self.lock = threading.Lock()
        self.total = 0
        self.samples = deque([(time.monotonic(), 0)])

    def add(self, size):
        now = time.monotonic()
        with self.lock:
            self.total += size
            if now - self.samples[-1][0] >= .1:
                self.samples.append((now, self.total))
            while len(self.samples) > 2 and now - self.samples[1][0] > 5:
                self.samples.popleft()

    def speed(self):
        now = time.monotonic()
        with self.lock:
            if now-self.samples[-1][0]>=.1:self.samples.append((now,self.total))
            while len(self.samples)>2 and now-self.samples[1][0]>5:self.samples.popleft()
            elapsed = now - self.samples[0][0]
            return int((self.total - self.samples[0][1]) / elapsed) if elapsed > 0 else 0


class DownloadTuner:
    def __init__(self, maximum=16, automatic=True, speed_limit=0, now=None):
        self.maximum, self.automatic, self.speed_limit = maximum, automatic, speed_limit
        self.connections = min(4, maximum) if automatic else maximum
        self.range_bytes = 16 * 1024 * 1024 if automatic else 64 * 1024 * 1024
        self.last_time = time.monotonic() if now is None else now
        self.last_bytes = 0
        self.baseline = None
        self.cooldown = self.last_time + 6
        self.lock = threading.Lock()

    def backoff(self, now=None):
        if not self.automatic:
            return
        now = time.monotonic() if now is None else now
        with self.lock:
            self.connections = max(1, self.connections // 2)
            self.baseline = None
            self.cooldown = now + 20

    def tick(self, transferred, now=None):
        now = time.monotonic() if now is None else now
        with self.lock:
            elapsed = now - self.last_time
            if elapsed < 4:
                return
            rate = max(0, transferred - self.last_bytes) / elapsed
            self.last_time, self.last_bytes = now, transferred
            if self.automatic:
                target = rate / max(1, self.connections) * 12
                self.range_bytes = min(64 * 1024 * 1024, max(8 * 1024 * 1024, int(target)))
            if not self.automatic or now < self.cooldown:
                return
            if self.speed_limit and rate >= self.speed_limit * .9:
                return
            if self.baseline:
                previous_connections, previous_rate = self.baseline
                self.baseline = None
                if rate < previous_rate * 1.10:
                    self.connections = previous_connections
                    self.cooldown = now + 20
                    return
            if self.connections < self.maximum and rate > 0:
                self.baseline = self.connections, rate
                self.connections = min(self.maximum, self.connections * 2)
                self.cooldown = now + 4
