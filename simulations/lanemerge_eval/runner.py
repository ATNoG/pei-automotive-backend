"""
TraCI → MQTT runner for lane merge evaluation.

For each scenario, this module:
  1. Starts SUMO via TraCI with the scenario route file.
  2. Every simulation step, extracts each vehicle's GPS position,
     speed (from TraCI — no need to diff consecutive positions) and
     heading from SUMO.
  3. Publishes a CarUpdate JSON message to MQTT cars/updates so the
     LaneMergeDetector processes it exactly as it would in production.

The runner publishes to MQTT directly (bypassing Hono/Ditto) — the same
pattern used by the other evaluation test suites.

Requirements
------------
- SUMO must be installed and accessible (sumo binary on PATH or via the
  eclipse-sumo Python wheel's bundled binary).
- The MQTT broker must be running on MQTT_HOST:MQTT_PORT.
- The LaneMergeDetector service must be running and subscribed to
  cars/updates.

Usage
-----
Import run_scenario() from test_lanemerge_eval.py, or call directly:
    python runner.py 06
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Optional

import paho.mqtt.client as mqtt

try:
    import traci
    TRACI_AVAILABLE = True
except ImportError:
    TRACI_AVAILABLE = False

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))

from common.models import CarUpdate           # noqa: E402

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
MQTT_HOST = os.getenv("MQTT_HOST", "localhost")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1884"))
CAR_UPDATES_TOPIC = "cars/updates"

EVAL_DIR    = Path(__file__).parent
NET_DIR     = EVAL_DIR / "network"
SCENARIO_DIR = EVAL_DIR / "scenarios"

NET_FILE    = NET_DIR / "lanemerge.net.xml"
BASE_CFG    = NET_DIR / "lanemerge.sumocfg"

SUMO_STEP_LENGTH = 0.1   # seconds
SPEED_LIMIT_KMH  = 100.0 # reported to detector (unused for detection but required in CarUpdate)

# Per-scenario simulation end time (generous; SUMO stops when all
# vehicles have left the network anyway).
SIM_END_S = 120.0

# IDs used inside SUMO route files (must match .rou.xml vehicle id="...")
MERGING_CAR_ID    = "merging_car"
MAIN_LANE_CAR_IDS = ["main_car", "main_car_2"]   # second only used in scenario 10


# ---------------------------------------------------------------------------
# MQTT helpers
# ---------------------------------------------------------------------------
def _make_mqtt_client() -> mqtt.Client:
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.connect(MQTT_HOST, MQTT_PORT, keepalive=30)
    client.loop_start()
    return client


def _publish_update(client: mqtt.Client, car_id: str,
                    lat: float, lon: float,
                    speed_kmh: float, heading_deg: float,
                    speed_limit_kmh: float = SPEED_LIMIT_KMH) -> None:
    update = CarUpdate(
        car_id=car_id,
        latitude=lat,
        longitude=lon,
        speed_kmh=speed_kmh,
        heading_deg=heading_deg,
        speed_limit_kmh=speed_limit_kmh,
        emergency=False,
        timestamp=time.time(),
    )
    client.publish(CAR_UPDATES_TOPIC, update.to_json(), qos=1)


def _cleanup(client: mqtt.Client, *car_ids: str) -> None:
    """Send cleanup signals for all cars used in the scenario."""
    cleanup = {
        "car_id": None,
        "latitude": 0.0, "longitude": 0.0,
        "speed_kmh": None, "heading_deg": None,
        "speed_limit_kmh": 50.0, "emergency": False,
        "timestamp": time.time(),
        "_test_cleanup": True,
    }
    for car_id in car_ids:
        cleanup["car_id"] = car_id
        client.publish(CAR_UPDATES_TOPIC, json.dumps(cleanup), qos=1)
    time.sleep(0.3)


# ---------------------------------------------------------------------------
# SUMO runner
# ---------------------------------------------------------------------------
def _find_sumo_binary() -> Optional[str]:
    """Try to locate the 'sumo' binary."""
    import shutil
    # 1. PATH
    found = shutil.which("sumo")
    if found:
        return found
    # 2. eclipse-sumo wheel bundles sumo next to the Python scripts
    venv_bin = Path(sys.executable).parent / "sumo"
    if venv_bin.exists():
        return str(venv_bin)
    venv_bin_exe = Path(sys.executable).parent / "sumo.exe"
    if venv_bin_exe.exists():
        return str(venv_bin_exe)
    return None


def _run_with_traci(scenario_id: str, mqtt_client: mqtt.Client) -> bool:
    """
    Run the SUMO simulation for *scenario_id* via TraCI.
    Publishes CarUpdate messages to MQTT at each simulation step.
    Returns True if the simulation completed successfully.
    """
    rou_file = SCENARIO_DIR / f"scenario_{scenario_id}.rou.xml"
    if not rou_file.exists():
        logger.error(f"Route file not found: {rou_file}")
        return False

    sumo_bin = _find_sumo_binary()
    if sumo_bin is None:
        logger.warning("SUMO binary not found — falling back to kinematic simulation")
        return False

    cmd = [
        sumo_bin,
        "-c", str(BASE_CFG),
        "--route-files", str(rou_file),
        "--step-length", str(SUMO_STEP_LENGTH),
        "--end", str(SIM_END_S),
        "--no-warnings",
        "--no-step-log",
    ]

    try:
        traci.start(cmd)
    except Exception as e:
        logger.error(f"Failed to start SUMO: {e}")
        return False

    try:
        while traci.simulation.getMinExpectedNumber() > 0:
            traci.simulationStep()

            for vid in traci.vehicle.getIDList():
                x, y = traci.vehicle.getPosition(vid)
                lon, lat = traci.simulation.convertGeo(x, y)
                speed_ms = traci.vehicle.getSpeed(vid)
                heading  = traci.vehicle.getAngle(vid)   # SUMO: 0=N, 90=E (clockwise)

                _publish_update(
                    mqtt_client, vid,
                    lat, lon,
                    speed_kmh=speed_ms * 3.6,
                    heading_deg=heading,
                )

            time.sleep(SUMO_STEP_LENGTH * 0.5)   # pace publishing slightly

        return True
    except Exception as e:
        logger.error(f"SUMO simulation error: {e}")
        return False
    finally:
        try:
            traci.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Kinematic fallback (no SUMO binary required)
# ---------------------------------------------------------------------------
def _load_route_coords(route_name: str) -> list[tuple[float, float]]:
    roads_dir = Path(__file__).resolve().parent.parent / "roads"
    route_file = roads_dir / f"{route_name}.json"
    with open(route_file) as f:
        data = json.load(f)
    coords = data["features"][0]["geometry"]["coordinates"]
    return [(lat, lon) for lat, lon in coords]


def _interpolate_along_route(
    coords: list[tuple[float, float]],
    start_m: float,
    end_m: float,
    speed_kmh: float,
    dt: float = SUMO_STEP_LENGTH,
) -> list[tuple[float, float, float]]:
    """
    Generate (lat, lon, heading_deg) samples along *coords* from
    *start_m* to *end_m* at *speed_kmh* with step *dt*.

    Returns a list of (lat, lon, heading_deg) positions.
    """
    import math

    def haversine(lat1, lon1, lat2, lon2) -> float:
        R = 6_371_000.0
        dlat = math.radians(lat2 - lat1)
        dlon = math.radians(lon2 - lon1)
        a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
        return 2 * R * math.asin(math.sqrt(a))

    def bearing(lat1, lon1, lat2, lon2) -> float:
        dlat = math.radians(lat2 - lat1)
        dlon = math.radians(lon2 - lon1)
        x = math.sin(dlon) * math.cos(math.radians(lat2))
        y = math.cos(math.radians(lat1)) * math.sin(math.radians(lat2)) - \
            math.sin(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.cos(dlon)
        return (math.degrees(math.atan2(x, y)) + 360) % 360

    # Build cumulative distance table
    cum_dist = [0.0]
    for i in range(1, len(coords)):
        d = haversine(*coords[i-1], *coords[i])
        cum_dist.append(cum_dist[-1] + d)

    total_length = cum_dist[-1]
    speed_ms = speed_kmh / 3.6

    samples = []
    pos = start_m
    while pos <= min(end_m, total_length):
        # Find segment
        idx = 0
        for i in range(len(cum_dist) - 1):
            if cum_dist[i] <= pos <= cum_dist[i+1]:
                idx = i
                break
        else:
            idx = len(coords) - 2

        seg_len = cum_dist[idx+1] - cum_dist[idx]
        t = (pos - cum_dist[idx]) / seg_len if seg_len > 0 else 0.0
        lat1, lon1 = coords[idx]
        lat2, lon2 = coords[idx+1]
        lat = lat1 + t * (lat2 - lat1)
        lon = lon1 + t * (lon2 - lon1)
        hdg = bearing(lat1, lon1, lat2, lon2)
        samples.append((lat, lon, hdg))
        pos += speed_ms * dt

    return samples


def _run_kinematic(scenario_id: str, spec, mqtt_client: mqtt.Client) -> None:
    """
    Kinematic fallback: interpolate positions along the route JSON files
    and publish CarUpdate messages to MQTT, mirroring what SUMO would do.
    """
    from ground_truth import ScenarioSpec  # noqa: PLC0415

    merging_coords   = _load_route_coords("entering")
    main_lane_coords = _load_route_coords("highway")

    import math

    def haversine(lat1, lon1, lat2, lon2) -> float:
        R = 6_371_000.0
        dlat = math.radians(lat2 - lat1)
        dlon = math.radians(lon2 - lon1)
        a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
        return 2 * R * math.asin(math.sqrt(a))

    def route_length(coords):
        return sum(haversine(*coords[i], *coords[i+1]) for i in range(len(coords)-1))

    entering_len   = route_length(merging_coords)
    highway_len    = route_length(main_lane_coords)

    # Position the merging car starting from the beginning of the entering ramp
    merging_samples = _interpolate_along_route(
        merging_coords, 0.0, entering_len, spec.merging_speed_kmh
    )

    # Position the main-lane car relative to the merge point
    # offset_m > 0 means the car is that many metres before the merge on the highway.
    # offset_m < 0 means it has already passed the merge by |offset_m|.
    main_samples: list = []
    if spec.main_lane_speed_kmh is not None and spec.main_lane_offset_m is not None:
        # Find where the merge point sits on the highway route (last point of merging_coords)
        merge_lat, merge_lon = merging_coords[-1]
        # Walk the highway coords to find the cumulative distance to the point
        # nearest to the merge
        cum = [0.0]
        for i in range(1, len(main_lane_coords)):
            cum.append(cum[-1] + haversine(*main_lane_coords[i-1], *main_lane_coords[i]))
        # Find closest point
        best_d = float("inf")
        best_pos = 0.0
        for i, (lat, lon) in enumerate(main_lane_coords):
            d = haversine(lat, lon, merge_lat, merge_lon)
            if d < best_d:
                best_d = d
                best_pos = cum[i]

        # The main car starts offset_m before the merge point (positive = before)
        start_pos = max(0.0, best_pos - spec.main_lane_offset_m)
        main_samples = _interpolate_along_route(
            main_lane_coords, start_pos, highway_len, spec.main_lane_speed_kmh
        )

        # Scenario 10: second main-lane car 130m further behind
        main_samples_2: list = []
        if scenario_id == "10":
            start_pos_2 = max(0.0, best_pos - spec.main_lane_offset_m - 130.0)
            main_samples_2 = _interpolate_along_route(
                main_lane_coords, start_pos_2, highway_len, spec.main_lane_speed_kmh
            )

    # Publish step by step
    n_steps = max(len(merging_samples), len(main_samples))
    for i in range(n_steps):
        if i < len(merging_samples):
            lat, lon, hdg = merging_samples[i]
            _publish_update(mqtt_client, MERGING_CAR_ID, lat, lon,
                            spec.merging_speed_kmh, hdg)

        if i < len(main_samples):
            lat, lon, hdg = main_samples[i]
            _publish_update(mqtt_client, MAIN_LANE_CAR_IDS[0], lat, lon,
                            spec.main_lane_speed_kmh, hdg)

        if scenario_id == "10" and i < len(main_samples_2):
            lat, lon, hdg = main_samples_2[i]
            _publish_update(mqtt_client, MAIN_LANE_CAR_IDS[1], lat, lon,
                            spec.main_lane_speed_kmh, hdg)

        time.sleep(SUMO_STEP_LENGTH)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def run_scenario(scenario_id: str, spec) -> None:
    """
    Run one evaluation scenario end-to-end.
    Tries SUMO/TraCI first; falls back to kinematic simulation if SUMO
    is not installed or the binary cannot be found.

    The function blocks until the scenario is complete (all vehicles have
    left the network or the kinematic replay is done).  The caller is
    responsible for collecting MQTT alerts.
    """
    logging.basicConfig(level=logging.INFO)

    mqtt_client = _make_mqtt_client()
    time.sleep(0.2)   # let broker accept the connection

    try:
        used_traci = False
        if TRACI_AVAILABLE:
            used_traci = _run_with_traci(scenario_id, mqtt_client)

        if not used_traci:
            logger.info(f"[scenario {scenario_id}] Running kinematic simulation")
            _run_kinematic(scenario_id, spec, mqtt_client)

        # Give the detector time to process the final position updates
        time.sleep(2.0)
    finally:
        car_ids = [MERGING_CAR_ID, MAIN_LANE_CAR_IDS[0]]
        if scenario_id == "10":
            car_ids.append(MAIN_LANE_CAR_IDS[1])
        _cleanup(mqtt_client, *car_ids)
        mqtt_client.loop_stop()
        mqtt_client.disconnect()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys as _sys
    from ground_truth import SCENARIOS

    if len(_sys.argv) < 2:
        print("Usage: python runner.py <scenario_id>")
        print("  scenario_id: 01..10")
        _sys.exit(1)

    sid = _sys.argv[1].zfill(2)
    if sid not in SCENARIOS:
        print(f"Unknown scenario: {sid}. Available: {sorted(SCENARIOS)}")
        _sys.exit(1)

    run_scenario(sid, SCENARIOS[sid])
