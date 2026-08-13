"""Keycloak token provider for Ditto (direct-grant password flow).

Ditto instances behind Keycloak (e.g. tomastest) authenticate with a JWT
access token obtained from the token endpoint instead of HTTP Basic
credentials.
"""
from __future__ import annotations

import logging
import threading
import time

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger(__name__)


class TokenProvider:
    """Fetch and cache a Keycloak access token via the password grant.

    The token is cached and refreshed shortly before it expires so callers
    can always read a valid token.
    """

    def __init__(
        self,
        token_url: str,
        client_id: str,
        username: str,
        password: str,
        verify_tls: bool = False,
    ) -> None:
        self._token_url = token_url
        self._client_id = client_id
        self._username = username
        self._password = password
        self._verify_tls = verify_tls
        self._lock = threading.Lock()
        self._token: str | None = None
        self._expires_at: float = 0.0

    def _fetch_token(self) -> dict:
        response = requests.post(
            self._token_url,
            data={
                "grant_type": "password",
                "username": self._username,
                "password": self._password,
                "client_id": self._client_id,
            },
            timeout=15,
            verify=self._verify_tls,
        )
        response.raise_for_status()
        return response.json()

    def get_token(self) -> str:
        """Return a valid access token, refreshing it before expiry."""
        now = time.time()
        with self._lock:
            if self._token is None or now >= self._expires_at - 60:
                data = self._fetch_token()
                self._token = data["access_token"]
                self._expires_at = now + int(data.get("expires_in", 300))
                logger.info("Refreshed Keycloak access token")
            return self._token
