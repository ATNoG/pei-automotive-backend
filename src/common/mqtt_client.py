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

        self.client = mqtt.Client(client_id=client_id)
        if username and password:
            self.client.username_pw_set(username, password)

        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message

        self._message_callbacks = {}
        self.connected = False

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            self.connected = True
            logger.info(f"Connected to MQTT broker at {self.host}:{self.port}")
        else:
            logger.error(f"Failed to connect to MQTT broker: {rc}")

    def _on_disconnect(self, client, userdata, rc):
        self.connected = False
        if rc != 0:
            logger.warning(f"Unexpected disconnection from MQTT broker: {rc}")

    def _on_message(self, client, userdata, msg):
        topic = msg.topic
        payload = msg.payload.decode('utf-8')

        if topic in self._message_callbacks:
            try:
                self._message_callbacks[topic](payload)
            except Exception as e:
                logger.error(f"Error processing message on {topic}: {e}")

    def connect(self):
        try:
            self.client.connect(self.host, self.port, keepalive=60)
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
        self._message_callbacks[topic] = callback
        self.client.subscribe(topic, qos=qos)
        logger.info(f"Subscribed to {topic}")

    def is_connected(self) -> bool:
        return self.connected
