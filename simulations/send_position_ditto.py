#!/usr/bin/env python3
"""Send a GPS position update directly to Ditto REST API, bypassing Hono.

Faster than send_position.py: no Hono MQTT adapter, TLS handshake,
post-connect sleep, or readback GET.

Usage: python simulations/send_position_ditto.py <car_name> <lat> <lon>
"""
import argparse
import json
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

DEVICES_DIR = Path(__file__).resolve().parent / "devices"


def put_features(
    session: requests.Session,
    api_url: str,
    thing_id: str,
    lat: float,
    lon: float,
    emergency: bool,
) -> None:
    """PUT gps + info features to a Ditto thing."""
    body = {
        "gps": {"properties": {"latitude": lat, "longitude": lon}},
        "info": {"properties": {"emergency": emergency}},
    }
    r = session.put(f"{api_url}/api/2/things/{thing_id}/features", json=body, timeout=10)
    if r.status_code not in (200, 204):
        raise RuntimeError(f"Ditto PUT failed for {thing_id}: {r.status_code} {r.text[:120]}")


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Send GPS update directly to Ditto (bypasses Hono).")
    parser.add_argument("car_name", help="Slug created with create_car.py")
    parser.add_argument("latitude", type=float)
    parser.add_argument("longitude", type=float)
    args = parser.parse_args()

    meta_file = DEVICES_DIR / f"{args.car_name}.json"
    if not meta_file.exists():
        sys.exit(f"Metadata file missing: {meta_file}")
    meta = json.loads(meta_file.read_text())

    api_url = os.getenv("DITTO_API_URL", "").rstrip("/")
    if not api_url:
        sys.exit("DITTO_API_URL is not set.")

    session = requests.Session()
    session.auth = (os.getenv("DITTO_USER", ""), os.getenv("DITTO_PASS", ""))

    put_features(session, api_url, meta["thing_id"], args.latitude, args.longitude, meta.get("emergency", False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
