#
# build_offline_roads.py
#
# Resumable builder for the offline road snapshot from operator-provided tile
# selections in data/offline_roads/selection/*.json. The result is written to
# data/offline_roads/offline_roads.json, which overpass_client.py loads at
# import time.
#
# How it works:
#   1. Reads all tile-selection JSON files under data/offline_roads/selection/.
#   2. Builds/updates the manifest tile list from those selections.
#   3. Processes all pending/stale tiles (no per-run tile cap), querying
#      Overpass for driveable highways inside each tile bbox.
#   4. Stops on the first API error, saving progress for resume.
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
OUTPUT_PATH = REPO_ROOT / "data" / "offline_roads" / "offline_roads.json"
MANIFEST_PATH = REPO_ROOT / "data" / "offline_roads" / "offline_roads_manifest.json"
SELECTION_DIR = REPO_ROOT / "data" / "offline_roads" / "selection"
LEGACY_SELECTION_DIR = REPO_ROOT / "data" / "offline_roads" / "selections"

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
USER_AGENT = "pei-automotive-backend/offline-roads-builder"

SNAPSHOT_VERSION = 2
MANIFEST_VERSION = 2

TILE_SIZE_DEG = 0.04  # ~4 km square tiles, uniform (no edge clipping)
SLEEP_BETWEEN_CALLS_S = 4.0
REFRESH_AFTER_DAYS = 7  # re-fetch tiles older than this
PER_QUERY_TIMEOUT = 60  # Overpass [timeout:N]

# keep in sync with overpass_client._DRIVEABLE_HIGHWAY_TYPES
DRIVEABLE_HIGHWAY_TYPES = {
    "motorway",
    "motorway_link",
    "trunk",
    "trunk_link",
    "primary",
    "primary_link",
    "secondary",
    "secondary_link",
    "tertiary",
    "tertiary_link",
    "unclassified",
    "residential",
    "living_street",
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
    body = resp.json()
    # Overpass occasionally returns 200 with {"remark": "rate_limited..."}
    remark = (body.get("remark") or "").lower()
    if "rate_limited" in remark or "too many requests" in remark:
        raise RateLimited(remark)
    return body


def load_selection_entries(selection_dir: Path = SELECTION_DIR) -> List[dict]:
    selection_dirs = [selection_dir]
    if LEGACY_SELECTION_DIR != selection_dir:
        selection_dirs.append(LEGACY_SELECTION_DIR)

    files: List[Path] = []
    for d in selection_dirs:
        if d.exists():
            files.extend(sorted(d.glob("*.json")))

    if not files:
        raise RuntimeError(
            "No selection JSON files found in "
            f"{selection_dir} (or legacy {LEGACY_SELECTION_DIR})"
        )

    entries: List[dict] = []
    for path in files:
        with path.open() as f:
            data = json.load(f)
        rows = data.get("tiles", data) if isinstance(data, dict) else data
        if not isinstance(rows, list):
            raise RuntimeError(f"{path} must contain a tile list or a 'tiles' list")
        for entry in rows:
            key = entry.get("key")
            bbox = entry.get("bbox")
            if not key or not isinstance(bbox, list) or len(bbox) != 4:
                raise RuntimeError(f"Invalid tile entry in {path}: {entry}")
            entries.append({"key": str(key), "bbox": [float(v) for v in bbox]})
    return entries


def build_manifest_tiles_from_selections(entries: List[dict]) -> Dict[str, Dict]:
    ordered_unique: Dict[str, List[float]] = {}
    for entry in entries:
        ordered_unique[entry["key"]] = entry["bbox"]

    return {
        key: {
            "bbox": bbox,
            "status": "pending",
            "last_fetched_at": None,
            "road_count": 0,
            "error": None,
        }
        for key, bbox in ordered_unique.items()
    }


# Per-tile road fetch
def fetch_tile_roads(tile_bbox: List[float]) -> List[dict]:
    s, w, n, e = tile_bbox
    query = (
        f"[out:json][timeout:{PER_QUERY_TIMEOUT}];"
        f'way["highway"]({s},{w},{n},{e});'
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
        out.append(
            {
                "id": el.get("id", 0),
                "maxspeed": parse_maxspeed(tags),
                "highway": hw,
                "geom": [[round(p["lat"], 6), round(p["lon"], 6)] for p in raw_geom],
            }
        )
    return out


# Manifest + snapshot persistence
def load_manifest() -> Optional[dict]:
    if not MANIFEST_PATH.exists():
        return None
    try:
        with MANIFEST_PATH.open() as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        print(
            f"warning: failed to read manifest ({exc}), starting fresh", file=sys.stderr
        )
        return None


def save_manifest(manifest: dict) -> None:
    """Preserves tile insertion order so the spatial-spread tile sequence
    survives reloads — do NOT use sort_keys here."""
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    with MANIFEST_PATH.open("w") as f:
        json.dump(manifest, f, indent=2)


def load_snapshot_segments() -> Dict[int, dict]:
    if not OUTPUT_PATH.exists():
        return {}
    try:
        with OUTPUT_PATH.open() as f:
            payload = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    return {
        int(s["id"]): s
        for s in payload.get("segments", [])
        if "id" in s and s.get("highway") in DRIVEABLE_HIGHWAY_TYPES
    }


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
    return [t["bbox"] for t in manifest["tiles"].values() if t["status"] == "done"]


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


def pick_tiles_to_fetch(manifest: dict, now: datetime) -> List[str]:
    """Never-fetched tiles first (in manifest insertion order, which is already
    a deterministic pseudo-random spread). Then stale ones, oldest first."""
    pending: List[Tuple[str, str]] = []
    for key, tile in manifest["tiles"].items():
        if tile["status"] != "done" or is_stale(tile, now):
            pending.append((key, tile.get("last_fetched_at") or ""))
    pending.sort(key=lambda kv: kv[1])  # "" < any iso timestamp
    return [k for k, _ in pending]


# Driver
def bootstrap_manifest(now: datetime, entries: List[dict]) -> dict:
    tiles = build_manifest_tiles_from_selections(entries)
    print(f"Bootstrapping manifest from selections: {len(tiles)} tiles")
    return {
        "version": MANIFEST_VERSION,
        "selection_dir": str(SELECTION_DIR.relative_to(REPO_ROOT)),
        "district_relation_id": None,
        "district_bbox": None,
        "tile_size_deg": TILE_SIZE_DEG,
        "created_at": now.isoformat(),
        "tiles": tiles,
    }


def sync_manifest_tiles(manifest: dict, entries: List[dict]) -> dict:
    existing = manifest.get("tiles", {})
    refreshed = build_manifest_tiles_from_selections(entries)
    for key, tile in refreshed.items():
        prev = existing.get(key, {})
        tile["status"] = prev.get("status", "pending")
        tile["last_fetched_at"] = prev.get("last_fetched_at")
        tile["road_count"] = prev.get("road_count", 0)
        tile["error"] = prev.get("error")
    manifest["tiles"] = refreshed
    manifest["selection_dir"] = str(SELECTION_DIR.relative_to(REPO_ROOT))
    return manifest


def run() -> int:
    parser = argparse.ArgumentParser(
        description="Build offline road snapshot from data/offline_roads/selection/*.json"
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=SLEEP_BETWEEN_CALLS_S,
        help=f"seconds to sleep between Overpass calls (default: {SLEEP_BETWEEN_CALLS_S})",
    )
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    entries = load_selection_entries(SELECTION_DIR)
    manifest = load_manifest()

    if manifest is None or manifest.get("version") != MANIFEST_VERSION:
        manifest = bootstrap_manifest(now, entries)
        save_manifest(manifest)
    else:
        manifest = sync_manifest_tiles(manifest, entries)
        save_manifest(manifest)
        print(f"Synced manifest from selections: {len(manifest['tiles'])} tiles")

    segments = load_snapshot_segments()
    initial_segment_count = len(segments)

    tile_keys = pick_tiles_to_fetch(manifest, now)
    total_pending = sum(
        1
        for t in manifest["tiles"].values()
        if t["status"] != "done" or is_stale(t, now)
    )
    print(f"this run: {len(tile_keys)} tiles (of {total_pending} pending/stale)")

    processed = 0
    for key in tile_keys:
        tile = manifest["tiles"][key]
        bbox = tile["bbox"]
        try:
            elements = fetch_tile_roads(bbox)
        except RateLimited as exc:
            print(
                f"  rate limited at tile {key} ({exc}); saving and exiting",
                file=sys.stderr,
            )
            tile["status"] = "rate_limited"
            tile["error"] = str(exc)
            break
        except requests.exceptions.RequestException as exc:
            print(f"  tile {key} failed: {exc}", file=sys.stderr)
            tile["status"] = "failed"
            tile["error"] = str(exc)
            tile["last_fetched_at"] = now.isoformat()
            break
        except Exception as exc:  # noqa: BLE001
            print(f"  tile {key} unexpected error: {exc}", file=sys.stderr)
            tile["status"] = "failed"
            tile["error"] = str(exc)
            tile["last_fetched_at"] = now.isoformat()
            break

        new_segments = extract_segments(elements)
        for seg in new_segments:
            segments[seg["id"]] = seg
        tile["status"] = "done"
        tile["error"] = None
        tile["last_fetched_at"] = now.isoformat()
        tile["road_count"] = len(new_segments)
        processed += 1
        print(
            f"  {key} {bbox} -> {len(new_segments)} ways (total cached: {len(segments)})"
        )

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
