#
# ditto_client.py
# websocket client for ditto
# listens to car updates and extracts info
#
from __future__ import annotations
import base64
import json
import logging
import time
from websocket import WebSocketApp
from typing import Callable, Optional

logger = logging.getLogger(__name__)


class DittoWSClient:
    def __init__(
        self,
        ws_url: str,
        username: str,
        password: str,
        on_gps_update: Callable[[str, float, float], None],
    ):
        self.ws_url = ws_url
        self.username = username
        self.password = password
        self.on_gps_update = on_gps_update
        self.ws: Optional[WebSocketApp] = None
        self._should_run = True

    def _on_open(self, ws):
        logger.info("Connected to Ditto WebSocket")
        # only receive updates where a GPS feature exists
        ws.send("START-SEND-EVENTS?filter=exists(features/gps)")
        logger.info("Subscribed to GPS feature updates")

    def _on_message(self, ws, message):
        # ignore pings / empty
        if not message or message.startswith(":"):
            return

        try:
            data = json.loads(message)
        except json.JSONDecodeError:
            return

        # extract car_id
        car_id = None
        thing_id = data.get("thingId")
        topic = data.get("topic")
        if thing_id:
            parts = thing_id.split(":")
            car_id = parts[-1]
        elif topic:
            parts = topic.split("/")
            if len(parts) >= 2:
                car_id = parts[1]

        if not car_id:
            return

        value = data.get("value")
        if not isinstance(value, dict):
            return

        gps_feature = value.get("gps", {})
        props = gps_feature.get("properties", {}) if isinstance(gps_feature, dict) else {}

        lat = props.get("latitude")
        lon = props.get("longitude")

        if lat is None or lon is None:
            return

        # callback with raw GPS
        try:
            self.on_gps_update(car_id, float(lat), float(lon))
        except Exception as e:
            logger.error("Error in on_gps_update callback: %s", e)

    def _on_error(self, ws, error):
        logger.error("Ditto WebSocket error: %s", error)

    def _on_close(self, ws, code, msg):
        logger.warning("Ditto WebSocket closed: %s %s", code, msg)

    def run_forever(self):
        auth_header = base64.b64encode(
            f"{self.username}:{self.password}".encode()
        ).decode()
        headers = [f"Authorization: Basic {auth_header}"]

        self._should_run = True

        while self._should_run:
            try:
                logger.info(f"Connecting to Ditto WS at {self.ws_url} ...")
                self.ws = WebSocketApp(
                    self.ws_url,
                    header=headers,
                    on_open=self._on_open,
                    on_message=self._on_message,
                    on_error=self._on_error,
                    on_close=self._on_close,
                )
                self.ws.run_forever()
            except Exception as e:
                logger.error("DittoWSClient crashed: %s", e)

            logger.info("Reconnecting to Ditto in 5 seconds...")
            time.sleep(5)

    def stop(self):
        self._should_run = False
        if self.ws:
            try:
                self.ws.close()
            except Exception:
                pass
