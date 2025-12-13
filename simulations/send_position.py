#!/usr/bin/env python3
import argparse
import json
import os
import sys
import time
from pathlib import Path

import paho.mqtt.client as mqtt
import requests
import ssl
import urllib3
import certifi
from dotenv import load_dotenv

urllib3.disable_warnings()

load_dotenv()
MQTT_HOST = os.getenv("MQTT_ADAPTER_IP")
MQTT_PORT = int(os.getenv("MQTT_ADAPTER_PORT_MQTTS"))
DITTO_API = os.getenv("DITTO_API_URL")
DITTO_AUTH = (os.getenv("DITTO_USER"), os.getenv("DITTO_PASS"))
REGISTRY_DIR = (Path(__file__).resolve().parent / "devices").resolve()


def get_cert_path(cert_hint: str) -> str:
    """Get a valid certificate path, using fallback if needed."""
    if cert_hint and Path(cert_hint).exists():
        return cert_hint
    return certifi.where()


def load_metadata(name: str) -> dict:
    meta_file = REGISTRY_DIR / f"{name}.json"
    if not meta_file.exists():
        sys.exit(f"Metadata file missing: {meta_file}")
    return json.loads(meta_file.read_text())


def validate_coordinates(lat: float, lon: float) -> None:
    if not (-90 <= lat <= 90):
        sys.exit("Latitude must be between -90 and 90.")
    if not (-180 <= lon <= 180):
        sys.exit("Longitude must be between -180 and 180.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Send GPS updates for a car device.")
    parser.add_argument("car_name", help="Slug created with create_car.py")
    parser.add_argument("latitude", type=float)
    parser.add_argument("longitude", type=float)
    args = parser.parse_args()

    validate_coordinates(args.latitude, args.longitude)
    meta = load_metadata(args.car_name)
    cert_hint = meta.get("ca_cert")
    cert_file = get_cert_path(cert_hint)

    # Build the feature value
    feature_value = {
        "gps": {"properties": {"latitude": args.latitude, "longitude": args.longitude}},
    }

    # Create Ditto command payload for twin update
    payload = {
        "topic": f"{meta['thing_id'].replace(':', '/')}/things/twin/commands/modify",
        "headers": {},
        "path": "/features/",
        "value": feature_value,
    }

    client = mqtt.Client(protocol=mqtt.MQTTv311)
    client.username_pw_set(f"{meta['auth_id']}@{meta['hono_tenant']}", meta["password"])
    client.tls_set(
        ca_certs=cert_file,
        certfile=None,
        keyfile=None,
        cert_reqs=ssl.CERT_NONE,
        tls_version=ssl.PROTOCOL_TLSv1_2,
    )
    client.tls_insecure_set(True)

    def on_connect(cl, userdata, flags, rc):
        if rc != 0:
            sys.exit(f"MQTT connect failed: {rc}")
        print("Connected to Hono MQTT.")

    statuses = []

    def on_publish(cl, userdata, mid):
        statuses.append(mid)
        print(f"Message {mid} published.")

    client.on_connect = on_connect
    client.on_publish = on_publish

    print(f"Connecting to {MQTT_HOST}:{MQTT_PORT}")
    client.connect(MQTT_HOST, MQTT_PORT, 60)
    client.loop_start()
    time.sleep(0.25)

    result = client.publish("telemetry", json.dumps(payload), qos=1)
    if result.rc != mqtt.MQTT_ERR_SUCCESS:
        sys.exit(f"Publish failed: rc={result.rc}")
    timeout = time.time() + 5
    while not statuses and time.time() < timeout:
        time.sleep(0.1)

    client.loop_stop()
    client.disconnect()

    resp = requests.get(
        f"{DITTO_API}/api/2/things/{meta['thing_id']}",
        auth=DITTO_AUTH,
        timeout=10,
    )
    if resp.status_code != 200:
        sys.exit(f"Ditto readback failed: {resp.status_code} {resp.text}")
    gps = resp.json().get("features", {}).get("gps", {}).get("properties", {})
    if gps.get("latitude") == args.latitude and gps.get("longitude") == args.longitude:
        print("Twin updated successfully.")
    else:
        print(f"Twin mismatch: {gps}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(1)
