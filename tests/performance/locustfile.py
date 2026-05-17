"""
Locust performance test suite for database-api
"""
import os
import random
import time

import requests
from locust import HttpUser, between, task
from locust.exception import StopUser


KC_URL    = os.getenv("KC_URL", "http://localhost:8081")
KC_REALM  = os.getenv("KC_REALM", "automotive-app")
KC_CLIENT = os.getenv("KC_CLIENT", "automotive-app")
KC_USER   = os.getenv("KC_USER", "driver")
KC_PASS   = os.getenv("KC_PASS", "driver123")

_TOKEN_ENDPOINT = f"{KC_URL}/realms/{KC_REALM}/protocol/openid-connect/token"


def _post_token(payload: dict) -> dict | None:
    for attempt in range(3):
        try:
            r = requests.post(_TOKEN_ENDPOINT, data=payload, timeout=10)
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
        time.sleep(2 ** attempt)
    return None


class _TokenManager:
    """Per-VU Keycloak token with jitter so VUs don't all refresh at once."""

    def __init__(self) -> None:
        self._token: str | None = None
        self._refresh: str | None = None
        self._expires_at: float = 0.0

    def acquire(self) -> None:
        data = _post_token({
            "grant_type": "password",
            "client_id":  KC_CLIENT,
            "username":   KC_USER,
            "password":   KC_PASS,
            "scope":      "openid",
        })
        if not data:
            raise StopUser()
        self._store(data)

    def _try_refresh(self) -> None:
        data = _post_token({
            "grant_type":    "refresh_token",
            "client_id":     KC_CLIENT,
            "refresh_token": self._refresh,
        })
        if not data:
            self.acquire()
            return
        self._store(data)

    def _store(self, data: dict) -> None:
        self._token      = data["access_token"]
        self._refresh    = data.get("refresh_token")
        ttl              = data.get("expires_in", 300)
        self._expires_at = time.monotonic() + ttl - random.uniform(0, 60)

    @property
    def headers(self) -> dict:
        if time.monotonic() >= self._expires_at - 30:
            self._try_refresh() if self._refresh else self.acquire()
        return {"Authorization": f"Bearer {self._token}"}


class PreferencesUser(HttpUser):
    """
    Authenticated user exercising GET and PATCH /api/preferences/.
    3:1 read-to-write ratio mirrors real app behaviour.

    Scenarios:
        Load   → -u 300  -r 20  --run-time 4m
        Stress → -u 1000 -r 50  --run-time 5m
    """
    wait_time = between(0.3, 0.7)

    def on_start(self) -> None:
        self._tok = _TokenManager()
        self._tok.acquire()

    @task(3)
    def get_preferences(self) -> None:
        self.client.get(
            "/api/preferences/",
            headers=self._tok.headers,
            name="GET /api/preferences/",
        )

    @task(1)
    def patch_preferences(self) -> None:
        self.client.patch(
            "/api/preferences/",
            json={"appearance": {"dark_mode": random.choice([True, False])}},
            headers=self._tok.headers,
            name="PATCH /api/preferences/",
        )


class HealthUser(HttpUser):
    """
    No-auth baseline. Use alone to measure raw service floor latency.

    Scenario:
        Baseline → -u 50 -r 10 --run-time 1m
    """
    wait_time = between(0.05, 0.1)

    @task
    def health(self) -> None:
        self.client.get("/health", name="GET /health")
