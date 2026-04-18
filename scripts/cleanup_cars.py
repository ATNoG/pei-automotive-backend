import argparse
import json
import os
import time
from pathlib import Path

import paho.mqtt.client as mqtt

# Configuration
MQTT_HOST = os.getenv("MQTT_HOST", "localhost")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1884"))


def broadcast_cleanup_all():
    """
    Broadcasts a global cleanup message to all services via MQTT
    to instruct them to clear all their internal states (all cars).
    """
    print("\n[CLEANUP] Broadcasting global cleanup signal to all services...")

    try:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        client.connect(MQTT_HOST, MQTT_PORT, keepalive=5)

        cleanup_msg = json.dumps({"action": "cleanup_all", "timestamp": time.time()})

        # Publish to a global cleanup topic that all services should subscribe to
        client.publish(
            "service/cleanup", cleanup_msg, qos=2
        )  # Use QoS 2 for "exactly once"

        print("[CLEANUP] Signal sent. Services should now be clearing their states.")
        client.disconnect()

    except Exception as e:
        print(f"[CLEANUP] ERROR: MQTT broadcast failed: {e}")


def delete_device_files():
    """Deletes all car device files from the simulations directory."""

    # Get the absolute path to the backend directory
    backend_dir = Path(__file__).resolve().parent.parent
    devices_dir = backend_dir / "simulations" / "devices"

    if not devices_dir.exists():
        print(
            f"\nWarning: Devices directory not found at {devices_dir}, skipping file deletion."
        )
        return

    device_files = list(devices_dir.glob("*.json"))

    if not device_files:
        print("\nNo device files found to delete.")
        return

    print(f"\nFound {len(device_files)} device files to delete...")
    for f in device_files:
        try:
            f.unlink()
            print(f"  - Deleted {f.name}")
        except Exception as e:
            print(f"  - ERROR: Failed to delete {f.name}: {e}")
    print("Device file deletion complete.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Clean up cars from the backend services and simulation files."
    )
    parser.add_argument(
        "--files-only",
        action="store_true",
        help="Only delete the local .json device files, do not send MQTT cleanup signal.",
    )
    parser.add_argument(
        "--signal-only",
        action="store_true",
        help="Only send MQTT cleanup signal, do not delete local .json device files.",
    )
    args = parser.parse_args()

    if args.files_only and args.signal_only:
        print("Error: --files-only and --signal-only cannot be used together.")
        exit(1)

    print("Starting cleanup process...")

    if not args.files_only:
        broadcast_cleanup_all()

    if not args.signal_only:
        delete_device_files()

    print("\nCleanup process finished.")
    print("Note: You may need to refresh your frontend to see the changes.")
