#
# build_offline_roads.py
#
# fetches driveable roads for the test areas from Overpass once and
# dumps them to src/common/offline_roads.json, which overpass_client.py
# loads at import time so tests never hit Overpass.
#
# run manually when OSM data for the test regions changes:
#   python3 scripts/utils/build_offline_roads.py
#
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
OUTPUT_PATH = REPO_ROOT / "src" / "common" / "offline_roads.json"

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
SNAPSHOT_VERSION = 1

# (name, south, west, north, east) — covers all test road files with a buffer.
# aveiro_west   -> right_lane, left_lane, right_lane_speeding, highway, entering
# ponte_barra   -> ponte_barra_{accident,ahead,behind}
# aveiro_east   -> route.json
BBOXES: List[Tuple[str, float, float, float, float]] = [
    ("aveiro_west",  40.622, -8.750, 40.637, -8.724),
    ("ponte_barra",  40.600, -8.690, 40.613, -8.673),
    ("aveiro_east",  40.620, -8.660, 40.638, -8.644),
]

# keep in sync with overpass_client.ROAD_TYPE_SPEED_LIMITS
DRIVEABLE_HIGHWAY_TYPES = {
    "motorway", "motorway_link", "trunk", "trunk_link",
    "primary", "primary_link", "secondary", "secondary_link",
    "tertiary", "tertiary_link", "unclassified", "residential",
    "living_street", "service", "track",
}


def parse_maxspeed(tags: Dict) -> float | None:
    for key in ("maxspeed", "maxspeed:forward", "maxspeed:backward"):
        raw = tags.get(key)
        if not raw:
            continue
        digits = "".join(c for c in raw if c.isdigit() or c == ".")
        if not digits:
            continue
        try:
            value = float(digits)
        except ValueError:
            continue
        if "mph" in raw.lower():
            value *= 1.60934
        if 0 < value <= 300:
            return value
    return None


def fetch_bbox(south: float, west: float, north: float, east: float) -> List[dict]:
    query = (
        f"[out:json][timeout:60];"
        f'way["highway"]({south},{west},{north},{east});'
        f"out body geom;"
    )
    for attempt in range(4):
        try:
            resp = requests.get(
                OVERPASS_URL,
                params={"data": query},
                timeout=60,
                headers={"User-Agent": "pei-automotive-backend/offline-roads-builder"},
            )
            if resp.status_code == 429:
                wait = 15 * (attempt + 1)
                print(f"  rate limited, sleeping {wait}s...", file=sys.stderr)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json().get("elements", [])
        except requests.exceptions.RequestException as exc:
            wait = 5 * (attempt + 1)
            print(f"  fetch failed ({exc}), retrying in {wait}s...", file=sys.stderr)
            time.sleep(wait)
    raise RuntimeError("overpass fetch failed after retries")


def extract_segments(elements: List[dict]) -> List[dict]:
    segments: List[dict] = []
    for el in elements:
        if el.get("type") != "way":
            continue
        tags = el.get("tags", {})
        hw = tags.get("highway", "")
        if hw not in DRIVEABLE_HIGHWAY_TYPES:
            continue
        raw_geom = el.get("geometry", [])
        if len(raw_geom) < 2:
            continue
        maxspeed = parse_maxspeed(tags)  # None -> overpass_client fills default from hw type
        segments.append({
            "id": el.get("id", 0),
            "maxspeed": maxspeed,
            "highway": hw,
            "geom": [[round(p["lat"], 6), round(p["lon"], 6)] for p in raw_geom],
        })
    return segments


def main() -> None:
    parser = argparse.ArgumentParser(description="Build offline road snapshot for tests.")
    parser.add_argument(
        "--output", default=str(OUTPUT_PATH),
        help=f"output path (default: {OUTPUT_PATH})",
    )
    args = parser.parse_args()

    all_segments: Dict[int, dict] = {}
    serialized_bboxes: List[List[float]] = []

    for name, s, w, n, e in BBOXES:
        print(f"fetching {name}: ({s},{w},{n},{e})")
        elements = fetch_bbox(s, w, n, e)
        segments = extract_segments(elements)
        print(f"  got {len(segments)} driveable ways")
        for seg in segments:
            all_segments[seg["id"]] = seg  # dedupe overlapping ways
        serialized_bboxes.append([s, w, n, e])
        time.sleep(2)  # be polite between bbox calls

    payload = {
        "version": SNAPSHOT_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "bboxes": serialized_bboxes,
        "segments": sorted(all_segments.values(), key=lambda s: s["id"]),
    }

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w") as f:
        json.dump(payload, f, separators=(",", ":"))

    size_kb = out.stat().st_size / 1024
    print(f"wrote {len(payload['segments'])} segments -> {out} ({size_kb:.1f} KB)")


if __name__ == "__main__":
    main()
