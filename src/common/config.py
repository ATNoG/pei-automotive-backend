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

    # Weather API (for meteo data)
    weather_api_url: str
    weather_username: str
    weather_password: str

    # MQTT Broker
    broker_host: str
    broker_port: int
    broker_user: Optional[str]
    broker_password: Optional[str]

    # Topics
    car_updates_topic: str
    raw_car_updates_topic: str
    meteo_updates_topic: str
    station_assignment_topic_base: str

    # Keycloak token endpoint for Ditto behind Keycloak (e.g. tomastest).
    # When set, Bearer token auth is used instead of HTTP Basic.
    ditto_auth_url: Optional[str] = None
    ditto_auth_client_id: str = "ditto"
    ditto_verify_tls: bool = False

    # Keycloak token endpoint for the weather Ditto behind Keycloak (e.g. tomastest).
    # When set, Bearer token auth is used instead of HTTP Basic.
    weather_auth_url: Optional[str] = None
    weather_auth_client_id: str = "ditto"
    weather_verify_tls: bool = False


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

    # Keycloak token endpoint for Ditto behind Keycloak (e.g. tomastest)
    ditto_auth_url = os.getenv("DITTO_AUTH_URL")
    ditto_auth_client_id = os.getenv("DITTO_AUTH_CLIENT_ID", "ditto")
    ditto_verify_tls = os.getenv("DITTO_VERIFY_TLS", "false").lower() == "true"

    # Weather API credentials (required)
    weather_api_url = _get_env("WEATHER_API_URL", required=True)
    weather_user = _get_env("WEATHER_USER", required=True)
    weather_pass = _get_env("WEATHER_PASS", required=True)

    # Keycloak token endpoint for the weather Ditto (e.g. tomastest)
    weather_auth_url = os.getenv("WEATHER_AUTH_URL")
    weather_auth_client_id = os.getenv("WEATHER_AUTH_CLIENT_ID", "ditto")
    weather_verify_tls = os.getenv("WEATHER_VERIFY_TLS", "false").lower() == "true"

    # MQTT basic config
    broker_host = _get_env("MQTT_BROKER_HOST", required=True)
    broker_port_str = _get_env("MQTT_BROKER_PORT", default="1883")
    try:
        broker_port = int(broker_port_str)
    except (TypeError, ValueError):
        raise RuntimeError(f"Invalid MQTT_BROKER_PORT value: {broker_port_str!r}")

    broker_user = _get_env("MQTT_BROKER_USER", default=None)
    broker_password = _get_env("MQTT_BROKER_PASSWORD", default=None)

    # Core topic: normalized car updates that detectors subscribe to.
    car_updates_topic = _get_env("MQTT_CAR_UPDATES_TOPIC", default="cars/updates")
    # Private topic where position_processor publishes raw updates;
    # the proximity_filter consumes this and republishes (enriched with
    # tile metadata) to car_updates_topic so all detectors stay agnostic.
    raw_car_updates_topic = _get_env("MQTT_RAW_CAR_UPDATES_TOPIC", default="cars/raw_updates")
    meteo_updates_topic = _get_env("MQTT_METEO_UPDATES_TOPIC", default="meteo/updates")
    station_assignment_topic_base = _get_env("MQTT_STATION_ASSIGNMENT_TOPIC", default="cars/station")

    return AppConfig(
        # Ditto
        ditto_ws_url=ditto_ws,
        ditto_username=ditto_user,
        ditto_password=ditto_pass,
        ditto_auth_url=ditto_auth_url,
        ditto_auth_client_id=ditto_auth_client_id,
        ditto_verify_tls=ditto_verify_tls,

        # Weather API (for meteo)
        weather_api_url=weather_api_url,
        weather_username=weather_user,
        weather_password=weather_pass,
        weather_auth_url=weather_auth_url,
        weather_auth_client_id=weather_auth_client_id,
        weather_verify_tls=weather_verify_tls,

        # MQTT
        broker_host=broker_host,
        broker_port=broker_port,
        broker_user=broker_user,
        broker_password=broker_password,

        # Topics
        car_updates_topic=car_updates_topic,
        raw_car_updates_topic=raw_car_updates_topic,
        meteo_updates_topic=meteo_updates_topic,
        station_assignment_topic_base=station_assignment_topic_base,
    )