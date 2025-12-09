from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse, urlunparse

from dotenv import load_dotenv

# load environment variables from .env file
load_dotenv()

@dataclass
class AppConfig:
    # Ditto / WebSocket
    ditto_ws_url: str
    ditto_username: str
    ditto_password: str

    # MQTT Broker
    broker_host: str
    broker_port: int
    broker_user: Optional[str]
    broker_password: Optional[str]

    # Topics
    car_updates_topic: str

    # Global speed configuration,
    # this will change in the future with
    # specific road's limits
    speed_limit_kmh: float


def _derive_ws_url_from_http(http_url: str) -> str:
    # convert http to websocket for ditto
    parsed = urlparse(http_url)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    return urlunparse((
        scheme,
        parsed.netloc,
        "/ws/2",
        "", "", ""
    ))


def _get_env(name: str, default: str | None = None, required: bool = False) -> str | None:
    value = os.getenv(name, default)
    if required and (value is None or value == ""):
        raise RuntimeError(f"Environment variable {name} is required but not set.")
    return value


def load_config() -> AppConfig:
    ditto_ws = os.getenv("DITTO_WS_URL")
    if not ditto_ws:
        http_url = os.getenv("DITTO_API_URL")
        ditto_ws = _derive_ws_url_from_http(http_url)

    # Ditto credentials (required)
    ditto_user = _get_env("DITTO_USER", required=True)
    ditto_pass = _get_env("DITTO_PASS", required=True)

    # MQTT basic config
    broker_host = _get_env("MQTT_BROKER_HOST", required=True)
    broker_port_str = _get_env("MQTT_BROKER_PORT", default="1883")
    try:
        broker_port = int(broker_port_str)
    except (TypeError, ValueError):
        raise RuntimeError(f"Invalid MQTT_BROKER_PORT value: {broker_port_str!r}")

    broker_user = _get_env("MQTT_BROKER_USER", default=None)
    broker_password = _get_env("MQTT_BROKER_PASSWORD", default=None)

    # Core topic: normalized car updates
    car_updates_topic = _get_env("MQTT_CAR_UPDATES_TOPIC", default="cars/updates")

    return AppConfig(
        # Ditto
        ditto_ws_url=ditto_ws,
        ditto_username=ditto_user,
        ditto_password=ditto_pass,

        # MQTT
        broker_host=broker_host,
        broker_port=broker_port,
        broker_user=broker_user,
        broker_password=broker_password,

        # Topics
        car_updates_topic=car_updates_topic,

        # Speed detection
        speed_limit_kmh=float(20),
    )
