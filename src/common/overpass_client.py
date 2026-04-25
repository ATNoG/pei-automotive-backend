#
# overpass_client.py
# snapshot-first speed-limit resolver.
#
# at import time we load data/offline_roads/offline_roads.json (built
# incrementally by scripts/build_offline_roads.py + the daily cron) into a flat
# list of road
# segments. lookups scan that list and return the nearest road's maxspeed.
#
# if a point falls outside the snapshot's covered tiles we make a single live
# Overpass call as a fallback. that result is kept only in-memory (not
# persisted) — the cron is expected to fill in the snapshot over time.
#
from __future__ import annotations

import json
import logging
import math
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import requests

logger = logging.getLogger(__name__)

OVERPASS_API_URL = "https://overpass-api.de/api/interpreter"
DEFAULT_SPEED_LIMIT_KMH: float = 50.0

ROAD_TYPE_SPEED_LIMITS: Dict[str, float] = {
    "motorway": 120,
    "motorway_link": 80,
    "trunk": 100,
    "trunk_link": 60,
    "primary": 90,
    "primary_link": 50,
    "secondary": 70,
    "secondary_link": 50,
    "tertiary": 50,
    "tertiary_link": 40,
    "unclassified": 50,
    "residential": 50,
    "living_street": 20,
    "service": 30,
    "track": 40,
}

_DRIVEABLE_HIGHWAY_TYPES: Set[str] = set(ROAD_TYPE_SPEED_LIMITS.keys())

_MAX_MATCH_DISTANCE_M: float = 25.0
_LIVE_FETCH_HALFSIDE_DEG: float = 0.003  # ~330 m half-side bbox around the point
_SNAPSHOT_PATH: Path = (
    Path(__file__).resolve().parent.parent.parent
    / "data"
    / "offline_roads"
    / "offline_roads.json"
)


@dataclass(frozen=True, slots=True)
class _RoadSegment:
    way_id: int
    speed_limit_kmh: float
    highway_type: str
    geometry: Tuple[Tuple[float, float], ...]  # [(lat, lon), …]


_segments: List[_RoadSegment] = []
_snapshot_bboxes: List[Tuple[float, float, float, float]] = []  # (s, w, n, e)
_live_fetched_keys: Set[Tuple[int, int]] = set()
_known_way_ids: Set[int] = set()
_lock = threading.Lock()


# Geometry
def _point_to_segment_dist_m(plat, plon, alat, alon, blat, blon) -> float:
    """Distance in metres from point P to line segment A-B."""
    cos_lat = math.cos(math.radians((plat + alat + blat) / 3.0))
    bx = (blon - alon) * 111_320.0 * cos_lat
    by = (blat - alat) * 110_540.0
    px = (plon - alon) * 111_320.0 * cos_lat
    py = (plat - alat) * 110_540.0

    seg_len_sq = bx * bx + by * by
    if seg_len_sq < 1e-12:
        return math.sqrt(px * px + py * py)

    t = max(0.0, min(1.0, (px * bx + py * by) / seg_len_sq))
    ex, ey = px - t * bx, py - t * by
    return math.sqrt(ex * ex + ey * ey)


def _point_to_polyline_dist_m(plat, plon, geometry) -> float:
    best = float("inf")
    for i in range(len(geometry) - 1):
        d = _point_to_segment_dist_m(
            plat,
            plon,
            geometry[i][0],
            geometry[i][1],
            geometry[i + 1][0],
            geometry[i + 1][1],
        )
        if d < best:
            best = d
            if d < 1.0:
                break
    return best


def _parse_maxspeed(tags: Dict) -> Optional[float]:
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


def _add_segments(new_segments: List[_RoadSegment]) -> None:
    for seg in new_segments:
        if seg.way_id not in _known_way_ids:
            _known_way_ids.add(seg.way_id)
            _segments.append(seg)


# Offline snapshot loader
def _load_offline_snapshot(path: Path = _SNAPSHOT_PATH) -> None:
    if not path.exists():
        logger.warning("Offline roads snapshot not found at %s", path)
        return

    try:
        with path.open() as f:
            payload = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Failed to load offline roads snapshot %s: %s", path, exc)
        return

    new_segments: List[_RoadSegment] = []
    for s in payload.get("segments", []):
        hw = s.get("highway", "")
        if hw not in _DRIVEABLE_HIGHWAY_TYPES:
            continue
        geom_raw = s.get("geom", [])
        if len(geom_raw) < 2:
            continue
        speed = s.get("maxspeed")
        if speed is None:
            speed = ROAD_TYPE_SPEED_LIMITS.get(hw, DEFAULT_SPEED_LIMIT_KMH)
        new_segments.append(
            _RoadSegment(
                way_id=s.get("id", 0),
                speed_limit_kmh=float(speed),
                highway_type=hw,
                geometry=tuple((float(pt[0]), float(pt[1])) for pt in geom_raw),
            )
        )

    with _lock:
        _add_segments(new_segments)
        for bbox in payload.get("bboxes", []):
            _snapshot_bboxes.append(tuple(bbox))  # type: ignore[arg-type]

    logger.info(
        "Loaded offline roads snapshot: %d segments, %d covered bboxes",
        len(new_segments),
        len(_snapshot_bboxes),
    )


def _point_in_snapshot(lat: float, lon: float) -> bool:
    for s, w, n, e in _snapshot_bboxes:
        if s <= lat <= n and w <= lon <= e:
            return True
    return False


# Live Overpass fallback (in-memory only, see module header)
def _live_key(lat: float, lon: float) -> Tuple[int, int]:
    """coarse grid so we don't refetch the same ~330 m bbox for every point."""
    step = _LIVE_FETCH_HALFSIDE_DEG
    return int(math.floor(lat / step)), int(math.floor(lon / step))


def _fetch_live_around(lat: float, lon: float) -> None:
    h = _LIVE_FETCH_HALFSIDE_DEG
    south, west, north, east = lat - h, lon - h, lat + h, lon + h
    query = (
        f"[out:json][timeout:30];"
        f'way["highway"]({south},{west},{north},{east});'
        f"out body geom;"
    )
    try:
        resp = requests.get(OVERPASS_API_URL, params={"data": query}, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except requests.exceptions.RequestException as exc:
        logger.warning(
            "Live Overpass fetch failed near (%.5f, %.5f): %s", lat, lon, exc
        )
        return

    new_segments: List[_RoadSegment] = []
    for el in data.get("elements", []):
        if el.get("type") != "way":
            continue
        tags = el.get("tags", {})
        hw = tags.get("highway", "")
        if hw not in _DRIVEABLE_HIGHWAY_TYPES:
            continue
        raw_geom = el.get("geometry", [])
        if len(raw_geom) < 2:
            continue
        speed = _parse_maxspeed(tags)
        if speed is None:
            speed = ROAD_TYPE_SPEED_LIMITS.get(hw, DEFAULT_SPEED_LIMIT_KMH)
        new_segments.append(
            _RoadSegment(
                way_id=el.get("id", 0),
                speed_limit_kmh=speed,
                highway_type=hw,
                geometry=tuple((pt["lat"], pt["lon"]) for pt in raw_geom),
            )
        )

    with _lock:
        _add_segments(new_segments)
    logger.info(
        "Live Overpass fetched %d segments around (%.5f, %.5f)",
        len(new_segments),
        lat,
        lon,
    )


# Lookup
def _find_nearest_road(lat: float, lon: float) -> Optional[_RoadSegment]:
    with _lock:
        candidates = list(_segments)

    best_seg: Optional[_RoadSegment] = None
    best_dist: float = _MAX_MATCH_DISTANCE_M

    for seg in candidates:
        d = _point_to_polyline_dist_m(lat, lon, seg.geometry)
        if d < best_dist:
            best_dist = d
            best_seg = seg

    return best_seg


def _ensure_live_if_outside_snapshot(lat: float, lon: float) -> None:
    if _point_in_snapshot(lat, lon):
        return
    key = _live_key(lat, lon)
    with _lock:
        if key in _live_fetched_keys:
            return
        _live_fetched_keys.add(key)
    _fetch_live_around(lat, lon)


def get_speed_limit(lat: float, lon: float) -> float:
    """Speed limit in km/h for the nearest road, or DEFAULT_SPEED_LIMIT_KMH."""
    _ensure_live_if_outside_snapshot(lat, lon)
    seg = _find_nearest_road(lat, lon)
    return seg.speed_limit_kmh if seg else DEFAULT_SPEED_LIMIT_KMH


def get_road_info(lat: float, lon: float) -> Tuple[float, Optional[int], Optional[str]]:
    """Returns (speed_limit_kmh, way_id or None, highway_type or None)."""
    _ensure_live_if_outside_snapshot(lat, lon)
    seg = _find_nearest_road(lat, lon)
    if seg:
        return seg.speed_limit_kmh, seg.way_id, seg.highway_type
    return DEFAULT_SPEED_LIMIT_KMH, None, None


_load_offline_snapshot()
