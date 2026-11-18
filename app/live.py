"""Feed ao vivo: a API guarda eventos em memória e o runner de experimentos os publica via HTTP.

O runner roda em outro processo, então ele faz POST /events na API (se ela estiver no ar) e o frontend
lê com GET /events?after=<último id>. Se a API não estiver rodando, o runner segue normalmente sem o feed.
"""
import threading
import time
from collections import deque
from typing import Optional

import httpx


class EventStore:
    """Buffer circular de eventos com id crescente (thread-safe)."""

    def __init__(self, maxlen: int = 5000):
        self._lock = threading.Lock()
        self._events: deque = deque(maxlen=maxlen)
        self._next = 1
        self.session = int(time.time() * 1000)  # muda quando a API reinicia ou o feed é limpo

    def add(self, event: dict) -> dict:
        with self._lock:
            e = {**event, "id": self._next, "ts": time.time()}
            self._next += 1
            self._events.append(e)
            return e

    def since(self, after: int, limit: int = 500) -> list[dict]:
        with self._lock:
            return [e for e in self._events if e["id"] > after][:limit]

    @property
    def last_id(self) -> int:
        return self._next - 1

    def clear(self) -> None:
        with self._lock:
            self._events.clear()
            self._next = 1
            self.session = int(time.time() * 1000)


class LivePublisher:
    """Cliente usado pelo runner. Nunca derruba a coleta: falhas apenas desligam o feed."""

    MAX_FAILURES = 5

    def __init__(self, url: Optional[str]):
        self.url = (url or "").rstrip("/")
        self.enabled = False
        self.failures = 0
        self._client: Optional[httpx.Client] = None

    def connect(self) -> bool:
        if not self.url:
            return False
        self._client = httpx.Client(timeout=2.0)
        try:
            self.enabled = self._client.get(f"{self.url}/events/ping").status_code == 200
        except httpx.HTTPError:
            self.enabled = False
        return self.enabled

    def publish(self, event: dict) -> None:
        if not self.enabled or self._client is None:
            return
        try:
            self._client.post(f"{self.url}/events", json=event).raise_for_status()
            self.failures = 0
        except httpx.HTTPError:
            self.failures += 1
            if self.failures >= self.MAX_FAILURES:
                self.enabled = False
                print(f"!! feed ao vivo desligado: a API em {self.url} parou de responder (a coleta continua).")

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
