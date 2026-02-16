"""
Tile-cached Overpass speed-limit resolver.

Pre-fetches all driveable roads in a ~1.7 km × 1.3 km area on the first
cache miss, then resolves every subsequent lookup with a local
point-to-polyline distance calculation (~0.3 ms per call).
"""

from __future__ import annotations

import logging
import math
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

import requests

logger = logging.getLogger(__name__)

OVERPASS_API_URL = "https://overpass-api.de/api/interpreter"
DEFAULT_SPEED_LIMIT_KMH: float = 50.0

ROAD_TYPE_SPEED_LIMITS: Dict[str, float] = {
    "motorway": 120, "motorway_link": 80,
    "trunk": 100,    "trunk_link": 60,
    "primary": 90,   "primary_link": 50,
    "secondary": 70, "secondary_link": 50,
    "tertiary": 50,  "tertiary_link": 40,
    "unclassified": 50, "residential": 50,
    "living_street": 20, "service": 30, "track": 40,
}

_DRIVEABLE_HIGHWAY_TYPES: Set[str] = set(ROAD_TYPE_SPEED_LIMITS.keys())

# Priority tiers for road matching (lower = preferred when distances are close)
_HIGHWAY_PRIORITY: Dict[str, int] = {
    "motorway": 0, "trunk": 0,
    "motorway_link": 1, "trunk_link": 1,
    "primary": 2, "primary_link": 2,
    "secondary": 3, "secondary_link": 3,
    "tertiary": 4, "tertiary_link": 4,
    "unclassified": 5, "residential": 5,
    "living_street": 6, "service": 7, "track": 8,
}

_TILE_SIZE: float = 0.005          # ~555 m lat × ~425 m lon at 40°N
_TILE_TTL: float = 600.0           # 10 min
_MAX_MATCH_DISTANCE_M: float = 50.0
_PRIORITY_MARGIN_M: float = 15.0   # prefer major road if within this margin
_MIN_API_INTERVAL: float = 2.0
_FAIL_RETRY_AFTER: float = 30.0
_MAX_RETRIES: int = 3
_RETRY_BACKOFF_BASE: float = 3.0

TileKey = Tuple[int, int]


@dataclass(frozen=True, slots=True)
class _RoadSegment:
    way_id: int
    speed_limit_kmh: float
    highway_type: str
    geometry: Tuple[Tuple[float, float], ...]   # [(lat, lon), …]


@dataclass(slots=True)
class _TileData:
    segments: List[_RoadSegment] = field(default_factory=list)
    fetched_at: float = 0.0
    _way_ids: Set[int] = field(default_factory=set)

    def add(self, seg: _RoadSegment) -> None:
        if seg.way_id not in self._way_ids:
            self._way_ids.add(seg.way_id)
            self.segments.append(seg)


_tile_cache: Dict[TileKey, _TileData] = {}
_cache_lock = threading.Lock()
_last_api_time: float = 0.0


def _tile_key(lat: float, lon: float) -> TileKey:
    return int(math.floor(lat / _TILE_SIZE)), int(math.floor(lon / _TILE_SIZE))


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
            plat, plon,
            geometry[i][0], geometry[i][1],
            geometry[i + 1][0], geometry[i + 1][1],
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


# Overpass API
def _fetch_tile_area(center: TileKey) -> List[_RoadSegment]:
    """Fetch all driveable roads in a 3×3 tile area, with retries."""
    global _last_api_time

    south = (center[0] - 1) * _TILE_SIZE
    west  = (center[1] - 1) * _TILE_SIZE
    north = (center[0] + 2) * _TILE_SIZE
    east  = (center[1] + 2) * _TILE_SIZE

    query = (
        f'[out:json][timeout:30];'
        f'way["highway"]({south},{west},{north},{east});'
        f'out body geom;'
    )

    last_exc: Optional[Exception] = None
    data: Optional[dict] = None

    for attempt in range(_MAX_RETRIES):
        now = time.time()
        wait = _MIN_API_INTERVAL - (now - _last_api_time)
        if wait > 0:
            time.sleep(wait)
        _last_api_time = time.time()

        try:
            resp = requests.get(OVERPASS_API_URL, params={"data": query}, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            break
        except (requests.exceptions.HTTPError,
                requests.exceptions.ConnectionError,
                requests.exceptions.Timeout) as exc:
            last_exc = exc
            status = getattr(getattr(exc, "response", None), "status_code", 0)
            retryable = isinstance(exc, (requests.exceptions.ConnectionError,
                                         requests.exceptions.Timeout)) \
                        or status in (429, 500, 502, 503, 504)
            if retryable and attempt < _MAX_RETRIES - 1:
                backoff = _RETRY_BACKOFF_BASE * (2 ** attempt)
                logger.warning("Overpass error for tile %s, retry %d/%d in %.0fs: %s",
                               center, attempt + 1, _MAX_RETRIES, backoff, exc)
                time.sleep(backoff)
                continue
            raise

    if data is None:
        raise last_exc  # type: ignore[misc]

    segments: List[_RoadSegment] = []
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

        segments.append(_RoadSegment(
            way_id=el.get("id", 0),
            speed_limit_kmh=speed,
            highway_type=hw,
            geometry=tuple((pt["lat"], pt["lon"]) for pt in raw_geom),
        ))
    return segments


# Tile cache management
def _store_segments(segments: List[_RoadSegment], center: TileKey) -> None:
    now = time.time()
    for di in range(-1, 2):
        for dj in range(-1, 2):
            tk = (center[0] + di, center[1] + dj)
            if tk not in _tile_cache or (now - _tile_cache[tk].fetched_at > _TILE_TTL):
                _tile_cache[tk] = _TileData(fetched_at=now)

    for seg in segments:
        tiles_hit: Set[TileKey] = {_tile_key(lat, lon) for lat, lon in seg.geometry}
        for tk in tiles_hit:
            td = _tile_cache.get(tk)
            if td is not None:
                td.add(seg)


def _mark_tiles_failed(center: TileKey) -> None:
    retry_at = time.time() - _TILE_TTL + _FAIL_RETRY_AFTER
    for di in range(-1, 2):
        for dj in range(-1, 2):
            tk = (center[0] + di, center[1] + dj)
            if tk not in _tile_cache:
                _tile_cache[tk] = _TileData(fetched_at=retry_at)


def _ensure_tiles(lat: float, lon: float) -> bool:
    tk = _tile_key(lat, lon)
    with _cache_lock:
        td = _tile_cache.get(tk)
        if td is not None and (time.time() - td.fetched_at < _TILE_TTL):
            return True
    try:
        segments = _fetch_tile_area(tk)
        with _cache_lock:
            _store_segments(segments, tk)
        logger.info("Fetched %d road segments for tile %s", len(segments), tk)
        return True
    except Exception:
        logger.warning("Overpass fetch failed for tile %s", tk, exc_info=True)
        with _cache_lock:
            _mark_tiles_failed(tk)
        return False


def _find_nearest_road(lat: float, lon: float) -> Optional[_RoadSegment]:
    tk = _tile_key(lat, lon)

    seen: Set[int] = set()
    candidates: List[_RoadSegment] = []
    with _cache_lock:
        for di in range(-1, 2):
            for dj in range(-1, 2):
                td = _tile_cache.get((tk[0] + di, tk[1] + dj))
                if td is None:
                    continue
                for seg in td.segments:
                    if seg.way_id not in seen:
                        seen.add(seg.way_id)
                        candidates.append(seg)

    if not candidates:
        return None

    best_seg: Optional[_RoadSegment] = None
    best_dist: float = _MAX_MATCH_DISTANCE_M
    scored: List[Tuple[_RoadSegment, float]] = []

    for seg in candidates:
        d = _point_to_polyline_dist_m(lat, lon, seg.geometry)
        if d < _MAX_MATCH_DISTANCE_M:
            scored.append((seg, d))
        if d < best_dist:
            best_dist = d
            best_seg = seg

    if best_seg is None:
        return None

    # Among roads within _PRIORITY_MARGIN_M of the closest, prefer major ones
    threshold = best_dist + _PRIORITY_MARGIN_M
    winner = best_seg
    winner_prio = _HIGHWAY_PRIORITY.get(best_seg.highway_type, 99)
    winner_dist = best_dist

    for seg, d in scored:
        if d > threshold:
            continue
        prio = _HIGHWAY_PRIORITY.get(seg.highway_type, 99)
        if prio < winner_prio or (prio == winner_prio and d < winner_dist):
            winner, winner_prio, winner_dist = seg, prio, d

    return winner


def get_speed_limit(lat: float, lon: float) -> float:
    """Speed limit in km/h for the nearest road, or DEFAULT_SPEED_LIMIT_KMH."""
    _ensure_tiles(lat, lon)
    seg = _find_nearest_road(lat, lon)
    return seg.speed_limit_kmh if seg else DEFAULT_SPEED_LIMIT_KMH


def get_road_info(lat: float, lon: float) -> Tuple[float, Optional[int], Optional[str]]:
    """Returns (speed_limit_kmh, way_id or None, highway_type or None)."""
    _ensure_tiles(lat, lon)
    seg = _find_nearest_road(lat, lon)
    if seg:
        return seg.speed_limit_kmh, seg.way_id, seg.highway_type
    return DEFAULT_SPEED_LIMIT_KMH, None, None
