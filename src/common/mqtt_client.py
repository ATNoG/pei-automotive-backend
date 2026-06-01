#
# mqtt_client.py
# everything MQTT broker related
#
import paho.mqtt.client as mqtt
import logging
from typing import Callable, Optional

logger = logging.getLogger(__name__)


class MQTTClient:
    def __init__(
        self,
        host: str,
        port: int = 1883,
        username: Optional[str] = None,
        password: Optional[str] = None,
        client_id: str = "mqtt-client",
    ):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.client_id = client_id

        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
        if username and password:
            self.client.username_pw_set(username, password)

        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message

        self.connected = False

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            logger.error(f"Failed to connect to MQTT broker: {reason_code}")
        else:
            self.connected = True
            logger.info(f"Connected to MQTT broker at {self.host}:{self.port}")

    def _on_disconnect(self, client, userdata, flags, reason_code, properties):
        self.connected = False
        if reason_code.is_failure:
            logger.warning(f"Unexpected disconnection from MQTT broker: {reason_code}")

    def _on_message(self, client, userdata, msg):
        # Default catch-all: only used when no per-topic callback was
        # registered via message_callback_add. With message_callback_add
        # paho dispatches wildcard subscriptions (e.g. "cars/updates/+")
        # correctly without us reimplementing the matching rules.
        logger.debug(f"Unhandled message on {msg.topic}")

    def connect(self):
        try:
            self.client.connect(self.host, self.port, keepalive=60)
        except KeyboardInterrupt:
            raise
        except Exception as e:
            logger.error(f"Error connecting to MQTT broker: {e}")
            raise

    def start_loop(self):
        self.client.loop_start()

    def loop_forever(self):
        self.client.loop_forever()

    def disconnect(self):
        self.client.loop_stop()
        self.client.disconnect()
        self.connected = False

    def publish(self, topic: str, payload: str, qos: int = 1, retain: bool = False):
        try:
            logger.info(f"Publishing to {topic}: {payload[:500]}")
            result = self.client.publish(topic, payload, qos=qos, retain=retain)
            if result.rc != mqtt.MQTT_ERR_SUCCESS:
                logger.error(f"Failed to publish to {topic}: {result.rc}")
            else:
                logger.info(f"Published to {topic}")
        except Exception as e:
            logger.error(f"Error publishing to {topic}: {e}")

    def subscribe(self, topic: str, callback: Callable[[str], None], qos: int = 1):
        # message_callback_add registers a topic-pattern callback that paho
        # invokes only for messages matching the pattern (with full + / #
        # wildcard support).
        def _wrapper(client, userdata, msg, _cb=callback):
            try:
                _cb(msg.payload.decode("utf-8"))
            except Exception as e:
                logger.error(f"Error processing message on {msg.topic}: {e}")

        self.client.message_callback_add(topic, _wrapper)
        self.client.subscribe(topic, qos=qos)
        logger.info(f"Subscribed to {topic}")

    def is_connected(self) -> bool:
        return self.connected
