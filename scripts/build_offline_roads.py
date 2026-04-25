#
# build_offline_roads.py
#
# Resumable builder for the offline road snapshot covering the entirety of
# Distrito de Aveiro (admin_level=6 in OSM). The result is written to
# data/offline_roads/offline_roads.json, which overpass_client.py loads at
# import time.
#
# How it works:
#   1. On first run we fetch the Aveiro district relation + its full polygon
#      from Overpass and use that to:
#        a. compute the bounding box,
#        b. prefilter the tile grid so only tiles that actually overlap the
#           district polygon are scheduled (no wasted Overpass calls on the
#           Atlantic, the Coimbra/Viseu sides of the bbox, etc.).
#   2. Each run picks a small batch of pending/stale tiles (deterministic
#      pseudo-random order, so successive runs spread coverage across the
#      district instead of crawling row-by-row), queries Overpass for the
#      driveable highways inside each tile that are also inside the district
#      area, and writes the merged snapshot + manifest.
#   3. Bails out cleanly on Overpass rate limits — the next cron run resumes.
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

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
USER_AGENT = "pei-automotive-backend/offline-roads-builder"

DISTRICT_NAME = "Aveiro"
DISTRICT_ADMIN_LEVEL = "6"  # Portugal: admin_level=6 == distrito

SNAPSHOT_VERSION = 2
MANIFEST_VERSION = 2

TILE_SIZE_DEG = 0.04  # ~4 km square tiles, uniform (no edge clipping)
MAX_TILES_PER_RUN = 30  # soft cap so one run never burns all rate limit
SLEEP_BETWEEN_CALLS_S = 4.0
REFRESH_AFTER_DAYS = 7  # re-fetch tiles older than this
PER_QUERY_TIMEOUT = 60  # Overpass [timeout:N]

# keep in sync with overpass_client.ROAD_TYPE_SPEED_LIMITS
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
    "service",
    "track",
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


# District polygon
def fetch_district_relation(rel_query_extra: str = "") -> dict:
    query = (
        f"[out:json][timeout:{PER_QUERY_TIMEOUT}];"
        f'relation["boundary"="administrative"]'
        f'["admin_level"="{DISTRICT_ADMIN_LEVEL}"]'
        f'["name"="{DISTRICT_NAME}"]{rel_query_extra};'
        f"out geom;"
    )
    data = overpass_get(query)
    rels = [el for el in data.get("elements", []) if el.get("type") == "relation"]
    if not rels:
        raise RuntimeError(
            f"Could not find Distrito de {DISTRICT_NAME} (admin_level={DISTRICT_ADMIN_LEVEL})"
        )
    pt = [
        r for r in rels if str(r.get("tags", {}).get("ISO3166-2", "")).startswith("PT")
    ]
    return (pt or rels)[0]


def assemble_rings(
    lines: List[List[Tuple[float, float]]],
) -> List[List[Tuple[float, float]]]:
    """Stitch unordered line segments into closed rings by matching endpoints."""
    rings: List[List[Tuple[float, float]]] = []
    remaining = [list(line) for line in lines]
    while remaining:
        current = remaining.pop(0)
        progress = True
        while progress and current[0] != current[-1] and remaining:
            progress = False
            for i, line in enumerate(remaining):
                if current[-1] == line[0]:
                    current.extend(line[1:])
                    remaining.pop(i)
                    progress = True
                    break
                if current[-1] == line[-1]:
                    current.extend(reversed(line[:-1]))
                    remaining.pop(i)
                    progress = True
                    break
                if current[0] == line[-1]:
                    current = line[:-1] + current
                    remaining.pop(i)
                    progress = True
                    break
                if current[0] == line[0]:
                    current = list(reversed(line))[1:] + current
                    remaining.pop(i)
                    progress = True
                    break
        if len(current) >= 3:
            rings.append(current)
    return rings


def extract_outer_rings(relation: dict) -> List[List[Tuple[float, float]]]:
    outer_lines: List[List[Tuple[float, float]]] = []
    for m in relation.get("members", []):
        if m.get("type") != "way":
            continue
        if m.get("role") not in ("outer", ""):
            continue
        geom = m.get("geometry") or []
        if len(geom) >= 2:
            outer_lines.append([(p["lat"], p["lon"]) for p in geom])
    return assemble_rings(outer_lines)


# Geometry: point-in-polygon, tile-vs-polygon
def point_in_rings(
    lat: float, lon: float, rings: List[List[Tuple[float, float]]]
) -> bool:
    """Even-odd ray casting across the union of rings (handles multipolygon holes)."""
    inside = False
    for ring in rings:
        c = False
        n = len(ring)
        if n < 3:
            continue
        j = n - 1
        for i in range(n):
            yi, xi = ring[i]
            yj, xj = ring[j]
            if (yi > lat) != (yj > lat):
                denom = (yj - yi) or 1e-12
                x_at = (xj - xi) * (lat - yi) / denom + xi
                if lon < x_at:
                    c = not c
            j = i
        if c:
            inside = not inside
    return inside


def tile_overlaps_rings(
    s: float, w: float, n: float, e: float, rings: List[List[Tuple[float, float]]]
) -> bool:
    """Sample a 4x4 grid of points inside the tile; True if any is in the polygon."""
    for i in range(4):
        for j in range(4):
            lat = s + (n - s) * i / 3
            lon = w + (e - w) * j / 3
            if point_in_rings(lat, lon, rings):
                return True
    return False


# Tile grid (uniform tiles, polygon-prefiltered, deterministic spread order)
def build_tile_grid(
    bbox: Tuple[float, float, float, float],
    size_deg: float,
    rings: List[List[Tuple[float, float]]],
) -> Dict[str, Dict]:
    south, west, north, east = bbox
    rows = int((north - south) / size_deg) + 1
    cols = int((east - west) / size_deg) + 1

    keys: List[Tuple[str, List[float]]] = []
    for r in range(rows):
        for c in range(cols):
            s = round(south + r * size_deg, 6)
            w = round(west + c * size_deg, 6)
            n = round(s + size_deg, 6)
            e = round(w + size_deg, 6)
            if not tile_overlaps_rings(s, w, n, e, rings):
                continue
            keys.append((f"{r}_{c}", [s, w, n, e]))

    # Bayer-style bit-reversal of the flat tile index: each successive tile
    # lands in a different sub-region, so the first 30 tiles cover the whole
    # district instead of one row.
    n_bits = max(1, (len(keys) - 1).bit_length())

    def _bit_reverse(idx: int) -> int:
        out = 0
        for _ in range(n_bits):
            out = (out << 1) | (idx & 1)
            idx >>= 1
        return out

    keys = [keys[i] for i in sorted(range(len(keys)), key=_bit_reverse)]

    return {
        k: {
            "bbox": bbox,
            "status": "pending",
            "last_fetched_at": None,
            "road_count": 0,
            "error": None,
        }
        for k, bbox in keys
    }


# Per-tile road fetch
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


def pick_tiles_to_fetch(manifest: dict, now: datetime, cap: int) -> List[str]:
    """Never-fetched tiles first (in manifest insertion order, which is already
    a deterministic pseudo-random spread). Then stale ones, oldest first."""
    pending: List[Tuple[str, str]] = []
    for key, tile in manifest["tiles"].items():
        if tile["status"] != "done" or is_stale(tile, now):
            pending.append((key, tile.get("last_fetched_at") or ""))
    pending.sort(key=lambda kv: kv[1])  # "" < any iso timestamp
    return [k for k, _ in pending[:cap]]


# Driver
def bootstrap_manifest(now: datetime, sleep: float) -> dict:
    print("Bootstrapping manifest: fetching Aveiro district relation + polygon...")
    relation = fetch_district_relation()
    rel_id = int(relation["id"])
    bb = relation.get("bounds")
    if not bb:
        raise RuntimeError("District relation returned without bounds")
    district_bbox = (
        float(bb["minlat"]),
        float(bb["minlon"]),
        float(bb["maxlat"]),
        float(bb["maxlon"]),
    )
    rings = extract_outer_rings(relation)
    if not rings:
        raise RuntimeError("Could not assemble district polygon from relation members")
    print(f"  relation_id={rel_id}, bbox={district_bbox}, outer_rings={len(rings)}")

    time.sleep(sleep)
    tiles = build_tile_grid(district_bbox, TILE_SIZE_DEG, rings)
    print(f"  scheduled {len(tiles)} tiles overlapping the district polygon")
    return {
        "version": MANIFEST_VERSION,
        "district_relation_id": rel_id,
        "district_bbox": list(district_bbox),
        "tile_size_deg": TILE_SIZE_DEG,
        "created_at": now.isoformat(),
        "tiles": tiles,
    }


def apply_user_tile_selection(manifest: dict, selection_path: Path) -> dict:
    """Replace the manifest's tile list with a user-curated selection.

    Expected JSON shape (matches what the viewer's "Export selected tiles"
    button produces):
        { "tiles": [ { "key": "12_5", "bbox": [s, w, n, e] }, ... ] }
    or a bare list of those entries.
    """
    with selection_path.open() as f:
        data = json.load(f)
    entries = data.get("tiles", data) if isinstance(data, dict) else data
    if not isinstance(entries, list) or not entries:
        raise RuntimeError(f"{selection_path} contains no tile entries")

    existing = manifest.get("tiles", {})
    new_tiles: Dict[str, Dict] = {}
    for entry in entries:
        key = entry["key"]
        bbox = entry["bbox"]
        prev = existing.get(key, {})
        new_tiles[key] = {
            "bbox": bbox,
            "status": prev.get("status", "pending"),
            "last_fetched_at": prev.get("last_fetched_at"),
            "road_count": prev.get("road_count", 0),
            "error": prev.get("error"),
        }
    manifest["tiles"] = new_tiles
    return manifest


def run() -> int:
    parser = argparse.ArgumentParser(
        description="Resumable Aveiro-district road snapshot builder."
    )
    parser.add_argument(
        "--max-tiles",
        type=int,
        default=MAX_TILES_PER_RUN,
        help=f"max tiles to process this run (default: {MAX_TILES_PER_RUN})",
    )
    parser.add_argument(
        "--use-tiles",
        type=str,
        default=None,
        help="path to a tile-selection JSON exported by roads_viewer.html. "
        "Replaces the manifest's tile list with the user's selection.",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=SLEEP_BETWEEN_CALLS_S,
        help=f"seconds to sleep between Overpass calls (default: {SLEEP_BETWEEN_CALLS_S})",
    )
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    manifest = load_manifest()

    if manifest is None or manifest.get("version") != MANIFEST_VERSION:
        try:
            manifest = bootstrap_manifest(now, args.sleep)
        except RateLimited as exc:
            print(
                f"rate limited during bootstrap ({exc}); try again later",
                file=sys.stderr,
            )
            return 0
        save_manifest(manifest)

    if args.use_tiles:
        manifest = apply_user_tile_selection(manifest, Path(args.use_tiles))
        save_manifest(manifest)
        print(
            f"Applied user tile selection from {args.use_tiles}: {len(manifest['tiles'])} tiles"
        )

    area_id = 3_600_000_000 + int(manifest["district_relation_id"])
    segments = load_snapshot_segments()
    initial_segment_count = len(segments)

    tile_keys = pick_tiles_to_fetch(manifest, now, args.max_tiles)
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
            elements = fetch_tile_roads(area_id, bbox)
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
