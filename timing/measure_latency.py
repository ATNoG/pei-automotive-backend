#!/usr/bin/env python3
"""
Timing Measurement Script for PEI Automotive Backend

Measures latency at each stage of the data pipeline:
1. Client → Hono MQTT (send to Hono ACK)
2. Hono → Ditto (Hono to Ditto twin update confirmed)
3. Ditto → Position Processor (Ditto WS event received by processor)
4. Position Processor → MQTT Broker (car/updates published)

Usage:
    python3 measure_latency.py <car_name>
"""

import argparse
import json
import os
import sys
import time
import threading
from pathlib import Path
from typing import Dict, List
import statistics

# Add src directory to path to use common modules
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import paho.mqtt.client as mqtt
import requests
import ssl
import urllib3
import certifi
from dotenv import load_dotenv

# Use existing common modules
from common.config import load_config
from common.ditto_client import DittoWSClient
from common.mqtt_client import MQTTClient
from common.logging_config import setup_logging

urllib3.disable_warnings()
load_dotenv()

# Hono configuration (for sending positions)
MQTT_HOST = os.getenv("MQTT_ADAPTER_IP")
MQTT_PORT = int(os.getenv("MQTT_ADAPTER_PORT_MQTTS", "8883"))
DITTO_API = os.getenv("DITTO_API_URL")
DITTO_AUTH = (os.getenv("DITTO_USER"), os.getenv("DITTO_PASS"))

REGISTRY_DIR = (Path(__file__).resolve().parent.parent / "simulations" / "devices").resolve()


def get_cert_path(cert_hint: str) -> str:
    """Get valid certificate path for Hono connection."""
    if cert_hint and Path(cert_hint).exists():
        return cert_hint
    return certifi.where()


def load_metadata(name: str) -> dict:
    """Load device metadata from simulations registry."""
    meta_file = REGISTRY_DIR / f"{name}.json"
    if not meta_file.exists():
        sys.exit(f"Metadata file missing: {meta_file}")
    return json.loads(meta_file.read_text())


class TimingMeasurer:
    """Measures timing through the entire pipeline."""

    def __init__(self, car_name: str):
        self.car_name = car_name
        self.metadata = load_metadata(car_name)
        self.config = load_config()

        # Override broker host for external access
        if self.config.broker_host == "mosquitto_broker":
            self.config.broker_host = "localhost"
            self.config.broker_port = 1884  # Exposed port

        self.measurements: List[Dict] = []
        self.lock = threading.Lock()

        # Components
        self.mqtt_listener = None
        self.ditto_listener = None

    def start_listeners(self):
        """Start Ditto WS and MQTT listeners using common modules."""
        print("Starting listeners...")

        # 1. Start Ditto WebSocket listener
        def on_ditto_gps(car_id: str, lat: float, lon: float):
            """Called when Ditto WS receives GPS update."""
            if car_id != self.car_name:
                return

            t_ditto_ws = time.time()
            with self.lock:
                for m in self.measurements:
                    if (abs(m.get("lat", 0) - lat) < 0.0001 and
                        abs(m.get("lon", 0) - lon) < 0.0001 and
                        "t_ditto_ws" not in m):
                        m["t_ditto_ws"] = t_ditto_ws
                        print(f"  ✓ Ditto WS event received for position {m['idx']}")
                        break

        self.ditto_listener = DittoWSClient(
            ws_url=self.config.ditto_ws_url,
            username=self.config.ditto_username,
            password=self.config.ditto_password,
            on_gps_update=on_ditto_gps
        )

        # Run Ditto WS in background thread
        ditto_thread = threading.Thread(target=self.ditto_listener.run_forever, daemon=True)
        ditto_thread.start()
        time.sleep(1)  # Let it connect

        # 2. Start local MQTT listener for car updates
        def on_car_update(payload_str: str):
            """Called when position processor publishes car update."""
            try:
                payload = json.loads(payload_str)
                car_id = payload.get("car_id")

                if car_id != self.car_name:
                    return

                t_mqtt_out = time.time()
                lat = payload.get("latitude")
                lon = payload.get("longitude")

                with self.lock:
                    for m in self.measurements:
                        if (abs(m.get("lat", 0) - lat) < 0.0001 and
                            abs(m.get("lon", 0) - lon) < 0.0001 and
                            "t_mqtt_out" not in m):
                            m["t_mqtt_out"] = t_mqtt_out
                            m["speed_kmh"] = payload.get("speed_kmh")
                            m["heading_deg"] = payload.get("heading_deg")
                            print(f"  ✓ Processor output received for position {m['idx']}")
                            break
            except Exception as e:
                print(f"Error processing MQTT message: {e}")

        self.mqtt_listener = MQTTClient(
            host=self.config.broker_host,
            port=self.config.broker_port,
            username=self.config.broker_user,
            password=self.config.broker_password,
            client_id="timing-measurer"
        )

        self.mqtt_listener.connect()
        self.mqtt_listener.subscribe(self.config.car_updates_topic, on_car_update)
        self.mqtt_listener.start_loop()
        time.sleep(0.5)  # Let it connect

        print("✓ Listeners started\n")

    def send_position_with_measurement(self, measurement: Dict, lat: float, lon: float):
        """Send position to Hono and update measurement with timing."""
        measurement["t_send"] = time.time()

        cert_file = get_cert_path(self.metadata.get("ca_cert"))
        client = mqtt.Client(protocol=mqtt.MQTTv311)
        client.username_pw_set(
            f"{self.metadata['auth_id']}@{self.metadata['hono_tenant']}",
            self.metadata["password"]
        )
        client.tls_set(
            ca_certs=cert_file,
            certfile=None,
            keyfile=None,
            cert_reqs=ssl.CERT_NONE,
            tls_version=ssl.PROTOCOL_TLSv1_2,
        )
        client.tls_insecure_set(True)

        # Track connection and publish
        connected = False
        published = False

        def on_connect(cl, userdata, flags, rc):
            nonlocal connected
            connected = (rc == 0)

        def on_publish(cl, userdata, mid):
            nonlocal published
            published = True
            measurement["t_hono_ack"] = time.time()

        client.on_connect = on_connect
        client.on_publish = on_publish

        # Connect
        client.connect(MQTT_HOST, MQTT_PORT, 60)
        client.loop_start()

        # Wait for connection
        timeout = time.time() + 5
        while not connected and time.time() < timeout:
            time.sleep(0.05)

        if not connected:
            print(f"  ✗ Failed to connect to Hono")
            client.loop_stop()
            client.disconnect()
            return measurement

        # Build and send payload
        feature_value = {
            "gps": {"properties": {"latitude": lat, "longitude": lon}},
        }
        payload = {
            "topic": f"{self.metadata['thing_id'].replace(':', '/')}/things/twin/commands/modify",
            "headers": {},
            "path": "/features/",
            "value": feature_value,
        }

        client.publish("telemetry", json.dumps(payload), qos=1)

        # Wait for publish ACK
        timeout = time.time() + 5
        while not published and time.time() < timeout:
            time.sleep(0.05)

        client.loop_stop()
        client.disconnect()

        if not published:
            print(f"  ✗ Failed to get Hono ACK")
            return measurement

        # Verify position arrived in Ditto via HTTP
        try:
            api_url = DITTO_API.rstrip('/')
            if not api_url.endswith('/api/2'):
                api_url = f"{api_url}/api/2"

            resp = requests.get(
                f"{api_url}/things/{self.metadata['thing_id']}",
                auth=DITTO_AUTH,
                timeout=5,
                verify=False
            )

            if resp.status_code == 200:
                gps = resp.json().get("features", {}).get("gps", {}).get("properties", {})
                if gps.get("latitude") == lat and gps.get("longitude") == lon:
                    measurement["t_ditto_http"] = time.time()
                    print(f"  ✓ Confirmed in Ditto")
            else:
                print(f"  ⚠ Ditto query failed: {resp.status_code}")
        except Exception as e:
            print(f"  ⚠ Error checking Ditto: {e}")

    def run(self, num_positions: int = 5, delay: float = 2.0):
        """Execute timing measurement."""
        print("\n" + "="*70)
        print(f"Timing Measurement: {self.car_name}")
        print("="*70)
        print(f"Positions: {num_positions} | Delay: {delay}s")
        print("="*70 + "\n")

        self.start_listeners()

        # Test positions
        positions = [
            (40.6316, -8.6579),
            (40.6320, -8.6575),
            (40.6325, -8.6570),
            (40.6330, -8.6565),
            (40.6335, -8.6560),
        ]

        try:
            for i in range(num_positions):
                lat, lon = positions[i % len(positions)]
                print(f"Position {i+1}/{num_positions}: ({lat}, {lon})")

                # Create measurement entry and add to list BEFORE sending
                # to avoid race condition with fast processor
                measurement = {
                    "idx": i+1,
                    "lat": lat,
                    "lon": lon,
                    "t_send": 0  # Will be set when sending
                }

                with self.lock:
                    self.measurements.append(measurement)

                # Now send the position
                self.send_position_with_measurement(measurement, lat, lon)

                if i < num_positions - 1:
                    print(f"  Waiting {delay}s...\n")
                    time.sleep(delay)

            # Wait for all updates to propagate
            print("\n" + "="*70)
            print("Waiting for position processor updates...")
            print("="*70 + "\n")
            time.sleep(10)

        finally:
            # Cleanup
            if self.mqtt_listener:
                self.mqtt_listener.disconnect()
            if self.ditto_listener:
                self.ditto_listener.stop()

        self.print_statistics()

    def print_statistics(self):
        """Print timing statistics."""
        print("\n" + "="*70)
        print("TIMING STATISTICS")
        print("="*70 + "\n")

        # Calculate latencies
        stage1 = []  # Send → Hono ACK
        stage2 = []  # Hono ACK → Ditto HTTP confirmed
        stage3 = []  # Ditto HTTP → Ditto WS event
        stage4 = []  # Ditto WS → Processor MQTT output
        total = []   # Send → Processor output

        for m in self.measurements:
            if "t_send" in m and "t_hono_ack" in m:
                stage1.append((m["t_hono_ack"] - m["t_send"]) * 1000)

            if "t_hono_ack" in m and "t_ditto_http" in m:
                stage2.append((m["t_ditto_http"] - m["t_hono_ack"]) * 1000)

            if "t_ditto_http" in m and "t_ditto_ws" in m:
                stage3.append((m["t_ditto_ws"] - m["t_ditto_http"]) * 1000)

            if "t_ditto_ws" in m and "t_mqtt_out" in m:
                stage4.append((m["t_mqtt_out"] - m["t_ditto_ws"]) * 1000)

            if "t_send" in m and "t_mqtt_out" in m:
                total.append((m["t_mqtt_out"] - m["t_send"]) * 1000)

        def print_stats(name: str, values: List[float]):
            if not values:
                print(f"{name}: No data collected\n")
                return
            print(f"{name}:")
            print(f"  Count:  {len(values)}")
            print(f"  Min:    {min(values):7.2f} ms")
            print(f"  Max:    {max(values):7.2f} ms")
            print(f"  Mean:   {statistics.mean(values):7.2f} ms")
            if len(values) > 1:
                print(f"  Median: {statistics.median(values):7.2f} ms")
                print(f"  Stdev:  {statistics.stdev(values):7.2f} ms")
            print()

        print_stats("1. Client → Hono ACK", stage1)
        print_stats("2. Hono ACK → Ditto Confirmed (HTTP)", stage2)
        print_stats("3. Ditto WS → Position Processor Output", stage4)
        print_stats("END-TO-END (Client → Processor Output)", total)

        print("="*70)
        print(f"Measurements sent: {len(self.measurements)}")
        print(f"Complete E2E:      {len(total)}")
        print("="*70 + "\n")

        # Save detailed results
        self.save_results()

    def save_results(self):
        """Save detailed results to JSON file."""
        import datetime
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        output_file = Path(__file__).parent / f"timing_results_{self.car_name}_{timestamp}.json"

        results = {
            "car_name": self.car_name,
            "timestamp": timestamp,
            "measurements": []
        }

        for m in self.measurements:
            entry = {
                "position": m["idx"],
                "latitude": m["lat"],
                "longitude": m["lon"],
                "timestamps": {}
            }

            if "t_send" in m:
                entry["timestamps"]["t_send"] = m["t_send"]
            if "t_hono_ack" in m:
                entry["timestamps"]["t_hono_ack"] = m["t_hono_ack"]
            if "t_ditto_http" in m:
                entry["timestamps"]["t_ditto_http"] = m["t_ditto_http"]
            if "t_ditto_ws" in m:
                entry["timestamps"]["t_ditto_ws"] = m["t_ditto_ws"]
            if "t_mqtt_out" in m:
                entry["timestamps"]["t_mqtt_out"] = m["t_mqtt_out"]

            # Calculate latencies
            latencies = {}
            if "t_send" in m and "t_hono_ack" in m:
                latencies["client_to_hono_ms"] = (m["t_hono_ack"] - m["t_send"]) * 1000
            if "t_hono_ack" in m and "t_ditto_http" in m:
                latencies["hono_to_ditto_ms"] = (m["t_ditto_http"] - m["t_hono_ack"]) * 1000
            if "t_ditto_ws" in m and "t_mqtt_out" in m:
                latencies["ditto_ws_to_processor_ms"] = (m["t_mqtt_out"] - m["t_ditto_ws"]) * 1000
            if "t_send" in m and "t_mqtt_out" in m:
                latencies["total_e2e_ms"] = (m["t_mqtt_out"] - m["t_send"]) * 1000

            entry["latencies"] = latencies

            if "speed_kmh" in m:
                entry["speed_kmh"] = m["speed_kmh"]
            if "heading_deg" in m:
                entry["heading_deg"] = m["heading_deg"]

            results["measurements"].append(entry)

        with open(output_file, 'w') as f:
            json.dump(results, f, indent=2)

        print(f"Detailed results saved to: {output_file}\n")


def main():
    parser = argparse.ArgumentParser(
        description="Measure latency through the automotive backend pipeline"
    )
    parser.add_argument("car_name", help="Car name from create_car.py")
    parser.add_argument(
        "--positions",
        type=int,
        default=5,
        help="Number of positions to send (default: 5)"
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=2.0,
        help="Delay between positions in seconds (default: 2.0)"
    )

    args = parser.parse_args()

    # Setup logging
    setup_logging("timing-measurer")

    measurer = TimingMeasurer(args.car_name)
    measurer.run(num_positions=args.positions, delay=args.delay)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nInterrupted by user")
        sys.exit(0)
    except Exception as e:
        print(f"\n\nError: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
