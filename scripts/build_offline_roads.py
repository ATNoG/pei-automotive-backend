#
# build_offline_roads.py
#
# Resumable builder for the offline road snapshot covering the entirety of
# Distrito de Aveiro (admin_level=6 in OSM). The result is written to
# src/common/offline_roads.json, which overpass_client.py loads at import time.
#
# Each run does as much as Overpass lets us before sleeping. Progress is kept
# in scripts/offline_roads_manifest.json so the next invocation resumes
# exactly where this one stopped. A daily cron drives this — see
# .github/workflows/refresh-offline-roads.yml.
#
# Usage (manual):
#   python3 scripts/build_offline_roads.py
#
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_PATH = REPO_ROOT / "src" / "common" / "offline_roads.json"
MANIFEST_PATH = REPO_ROOT / "scripts" / "offline_roads_manifest.json"

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
USER_AGENT = "pei-automotive-backend/offline-roads-builder"

DISTRICT_NAME = "Aveiro"
DISTRICT_ADMIN_LEVEL = "6"  # Portugal: admin_level=6 == distrito

SNAPSHOT_VERSION = 2
MANIFEST_VERSION = 1

TILE_SIZE_DEG = 0.04          # ~4 km square tiles
MAX_TILES_PER_RUN = 30        # soft cap so one run never burns all rate limit
SLEEP_BETWEEN_CALLS_S = 4.0
REFRESH_AFTER_DAYS = 7        # re-fetch tiles older than this
PER_QUERY_TIMEOUT = 60        # Overpass [timeout:N]

# keep in sync with overpass_client.ROAD_TYPE_SPEED_LIMITS
DRIVEABLE_HIGHWAY_TYPES = {
    "motorway", "motorway_link", "trunk", "trunk_link",
    "primary", "primary_link", "secondary", "secondary_link",
    "tertiary", "tertiary_link", "unclassified", "residential",
    "living_street", "service", "track",
}


# Sentinel raised when Overpass tells us to back off. The driver catches it,
# saves progress, and exits cleanly — the next cron run resumes.
class RateLimited(Exception):
    pass


def parse_maxspeed(tags: Dict) -> Optional[float]:
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


def overpass_get(query: str) -> dict:
    resp = requests.get(
        OVERPASS_URL,
        params={"data": query},
        timeout=PER_QUERY_TIMEOUT + 10,
        headers={"User-Agent": USER_AGENT},
    )
    if resp.status_code in (429, 504):
        raise RateLimited(f"HTTP {resp.status_code}")
    resp.raise_for_status()
    return resp.json()


# District lookup
def fetch_district_info() -> Tuple[int, Tuple[float, float, float, float]]:
    """Return (relation_id, (south, west, north, east)) for Distrito de Aveiro."""
    query = (
        f"[out:json][timeout:{PER_QUERY_TIMEOUT}];"
        f'relation["boundary"="administrative"]'
        f'["admin_level"="{DISTRICT_ADMIN_LEVEL}"]'
        f'["name"="{DISTRICT_NAME}"];'
        f"out bb tags;"
    )
    data = overpass_get(query)
    elements = [el for el in data.get("elements", []) if el.get("type") == "relation"]
    if not elements:
        raise RuntimeError(
            f"Could not find Distrito de {DISTRICT_NAME} (admin_level={DISTRICT_ADMIN_LEVEL})"
        )
    # Disambiguate: Portugal districts have ISO3166-2 like "PT-01". Prefer those.
    pt_districts = [
        el for el in elements
        if str(el.get("tags", {}).get("ISO3166-2", "")).startswith("PT")
    ]
    chosen = (pt_districts or elements)[0]
    rel_id = int(chosen["id"])
    bb = chosen.get("bounds")
    if not bb:
        raise RuntimeError("District relation returned without bounds")
    return rel_id, (
        float(bb["minlat"]), float(bb["minlon"]),
        float(bb["maxlat"]), float(bb["maxlon"]),
    )


# Tile grid
def build_tile_grid(
    bbox: Tuple[float, float, float, float], size_deg: float
) -> Dict[str, Dict]:
    south, west, north, east = bbox
    tiles: Dict[str, Dict] = {}
    rows = int((north - south) / size_deg) + 1
    cols = int((east - west) / size_deg) + 1
    for r in range(rows):
        for c in range(cols):
            s = south + r * size_deg
            w = west + c * size_deg
            n = min(south + (r + 1) * size_deg, north)
            e = min(west + (c + 1) * size_deg, east)
            if n <= s or e <= w:
                continue
            tiles[f"{r}_{c}"] = {
                "bbox": [round(s, 6), round(w, 6), round(n, 6), round(e, 6)],
                "status": "pending",
                "last_fetched_at": None,
                "road_count": 0,
                "error": None,
            }
    return tiles


# Per-tile fetch
def fetch_tile_roads(area_id: int, tile_bbox: List[float]) -> List[dict]:
    s, w, n, e = tile_bbox
    query = (
        f"[out:json][timeout:{PER_QUERY_TIMEOUT}];"
        f"area({area_id})->.aveiro;"
        f'way(area.aveiro)["highway"]({s},{w},{n},{e});'
        f"out body geom;"
    )
    data = overpass_get(query)
    return data.get("elements", [])


def extract_segments(elements: List[dict]) -> List[dict]:
    out: List[dict] = []
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
        out.append({
            "id": el.get("id", 0),
            "maxspeed": parse_maxspeed(tags),
            "highway": hw,
            "geom": [[round(p["lat"], 6), round(p["lon"], 6)] for p in raw_geom],
        })
    return out


# Manifest + snapshot persistence
def load_manifest() -> Optional[dict]:
    if not MANIFEST_PATH.exists():
        return None
    try:
        with MANIFEST_PATH.open() as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"warning: failed to read manifest ({exc}), starting fresh", file=sys.stderr)
        return None


def save_manifest(manifest: dict) -> None:
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    with MANIFEST_PATH.open("w") as f:
        json.dump(manifest, f, indent=2, sort_keys=True)


def load_snapshot_segments() -> Dict[int, dict]:
    if not OUTPUT_PATH.exists():
        return {}
    try:
        with OUTPUT_PATH.open() as f:
            payload = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    return {int(s["id"]): s for s in payload.get("segments", []) if "id" in s}


def save_snapshot(segments: Dict[int, dict], covered_bboxes: List[List[float]]) -> None:
    payload = {
        "version": SNAPSHOT_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "bboxes": covered_bboxes,
        "segments": sorted(segments.values(), key=lambda s: s["id"]),
    }
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_PATH.open("w") as f:
        json.dump(payload, f, separators=(",", ":"))


def covered_bboxes(manifest: dict) -> List[List[float]]:
    return [
        t["bbox"] for t in manifest["tiles"].values()
        if t["status"] == "done"
    ]


# Tile selection
def is_stale(tile: dict, now: datetime) -> bool:
    ts = tile.get("last_fetched_at")
    if not ts:
        return True
    try:
        last = datetime.fromisoformat(ts)
    except ValueError:
        return True
    return (now - last) > timedelta(days=REFRESH_AFTER_DAYS)


def pick_tiles_to_fetch(manifest: dict, now: datetime, cap: int) -> List[str]:
    pending: List[Tuple[str, str]] = []  # (key, last_fetched_at or "")
    for key, tile in manifest["tiles"].items():
        if tile["status"] != "done" or is_stale(tile, now):
            pending.append((key, tile.get("last_fetched_at") or ""))
    # oldest first (empty string sorts before any ISO timestamp -> never-fetched first)
    pending.sort(key=lambda kv: kv[1])
    return [k for k, _ in pending[:cap]]


# Driver
def run() -> int:
    parser = argparse.ArgumentParser(description="Resumable Aveiro-district road snapshot builder.")
    parser.add_argument("--max-tiles", type=int, default=MAX_TILES_PER_RUN,
                        help=f"max tiles to process this run (default: {MAX_TILES_PER_RUN})")
    parser.add_argument("--sleep", type=float, default=SLEEP_BETWEEN_CALLS_S,
                        help=f"seconds to sleep between Overpass calls (default: {SLEEP_BETWEEN_CALLS_S})")
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    manifest = load_manifest()

    if manifest is None or manifest.get("version") != MANIFEST_VERSION:
        print("Bootstrapping manifest: fetching Aveiro district boundary...")
        rel_id, district_bbox = fetch_district_info()
        time.sleep(args.sleep)
        tiles = build_tile_grid(district_bbox, TILE_SIZE_DEG)
        manifest = {
            "version": MANIFEST_VERSION,
            "district_relation_id": rel_id,
            "district_bbox": list(district_bbox),
            "tile_size_deg": TILE_SIZE_DEG,
            "created_at": now.isoformat(),
            "tiles": tiles,
        }
        save_manifest(manifest)
        print(f"  relation_id={rel_id}, bbox={district_bbox}, tiles={len(tiles)}")

    area_id = 3_600_000_000 + int(manifest["district_relation_id"])
    segments = load_snapshot_segments()
    initial_segment_count = len(segments)

    tile_keys = pick_tiles_to_fetch(manifest, now, args.max_tiles)
    total_pending = sum(
        1 for t in manifest["tiles"].values()
        if t["status"] != "done" or is_stale(t, now)
    )
    print(f"this run: {len(tile_keys)} tiles (of {total_pending} pending/stale)")

    processed = 0
    for key in tile_keys:
        tile = manifest["tiles"][key]
        bbox = tile["bbox"]
        try:
            elements = fetch_tile_roads(area_id, bbox)
        except RateLimited as exc:
            print(f"  rate limited at tile {key} ({exc}); saving and exiting", file=sys.stderr)
            tile["status"] = "rate_limited"
            tile["error"] = str(exc)
            break
        except requests.exceptions.RequestException as exc:
            print(f"  tile {key} failed: {exc}", file=sys.stderr)
            tile["status"] = "failed"
            tile["error"] = str(exc)
            tile["last_fetched_at"] = now.isoformat()
            time.sleep(args.sleep)
            continue
        except Exception as exc:  # noqa: BLE001
            print(f"  tile {key} unexpected error: {exc}", file=sys.stderr)
            tile["status"] = "failed"
            tile["error"] = str(exc)
            tile["last_fetched_at"] = now.isoformat()
            time.sleep(args.sleep)
            continue

        new_segments = extract_segments(elements)
        for seg in new_segments:
            segments[seg["id"]] = seg
        tile["status"] = "done"
        tile["error"] = None
        tile["last_fetched_at"] = now.isoformat()
        tile["road_count"] = len(new_segments)
        processed += 1
        print(f"  {key} {bbox} -> {len(new_segments)} ways (total cached: {len(segments)})")

        if processed < len(tile_keys):
            time.sleep(args.sleep)

    save_manifest(manifest)
    save_snapshot(segments, covered_bboxes(manifest))

    done_count = sum(1 for t in manifest["tiles"].values() if t["status"] == "done")
    total_count = len(manifest["tiles"])
    new_segments_count = len(segments) - initial_segment_count
    size_kb = OUTPUT_PATH.stat().st_size / 1024
    print(
        f"done: processed {processed} tiles, +{new_segments_count} new roads. "
        f"Coverage: {done_count}/{total_count} tiles. Snapshot {size_kb:.1f} KB."
    )
    return 0


if __name__ == "__main__":
    sys.exit(run())
