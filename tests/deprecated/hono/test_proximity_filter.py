"""
Integration test: two simultaneous overtaking maneuvers in different tiles
(Aveiro vs. Ovar).

Verifies that:
  - Every car update reaching the detectors carries the injected
    ``tile_quadkey`` / ``tile_zoom`` fields.
  - Each pair triggers its own overtaking alert.
  - No alert ever pairs a car from one tile with a car from the other.
"""
import json
import sys
import time
from pathlib import Path
from threading import Thread

import paho.mqtt.client as mqtt

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from helpers import (  # noqa: E402
    MQTT_HOST, MQTT_PORT,
    ensure_car_exists, send_position, standalone_get_car_id,
)


ALERTS: list[dict] = []
CAR_UPDATES: list[dict] = []


def _on_overtake_message(_client, _userdata, msg):
    try:
        ALERTS.append(json.loads(msg.payload.decode()))
    except Exception:
        pass


def _on_car_update_message(_client, _userdata, msg):
    try:
        CAR_UPDATES.append(json.loads(msg.payload.decode()))
    except Exception:
        pass


def _drive_pair(slow_id: str, fast_id: str, lat: float, base_lon: float):
    """Drive an overtaking pair eastwards, triggering a single overtaking alert."""
    DLON_SLOW = 3e-5
    DLON_FAST = 8e-5
    INITIAL_GAP = 3e-4
    STEPS = 18

    for i in range(STEPS):
        slow_lon = base_lon + i * DLON_SLOW
        fast_lon = base_lon - INITIAL_GAP + i * DLON_FAST

        t_slow = Thread(target=send_position, args=(slow_id, lat, slow_lon))
        t_fast = Thread(target=send_position, args=(fast_id, lat, fast_lon))
        t_slow.start()
        t_fast.start()
        t_slow.join()
        t_fast.join()
        time.sleep(0.05)


def test_proximity_filter_end_to_end(get_car_id):
    """
    End-to-end check that the proximity_filter enriches cars/updates with tile
    metadata. Drives two simultaneous overtaking maneuvers in different tiles
    (Aveiro and Ovar) and verifies tile isolation.
    """
    a_slow = get_car_id("prox-aveiro-slow")
    a_fast = get_car_id("prox-aveiro-fast")
    b_slow = get_car_id("prox-ovar-slow")
    b_fast = get_car_id("prox-ovar-fast")

    ALERTS.clear()
    CAR_UPDATES.clear()

    for car in (a_slow, a_fast, b_slow, b_fast):
        ensure_car_exists(car)

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.message_callback_add("alerts/overtaking/+", _on_overtake_message)
    client.message_callback_add("cars/updates/+", _on_car_update_message)
    client.connect(MQTT_HOST, MQTT_PORT)
    client.subscribe([("alerts/overtaking/+", 1), ("cars/updates/+", 1)])
    client.loop_start()

    aveiro = Thread(target=_drive_pair, args=(a_slow, a_fast, 40.6405, -8.6538))
    ovar = Thread(target=_drive_pair, args=(b_slow, b_fast, 40.866, -8.627))
    aveiro.start()
    ovar.start()
    aveiro.join()
    ovar.join()

    time.sleep(2)
    client.loop_stop()
    client.disconnect()

    test_car_ids = {a_slow, a_fast, b_slow, b_fast}
    relevant_updates = [u for u in CAR_UPDATES if u.get("car_id") in test_car_ids]
    assert relevant_updates, "no car updates were observed on cars/updates"

    missing_tile = [
        u for u in relevant_updates
        if u.get("tile_quadkey") is None or u.get("tile_zoom") is None
    ]
    assert not missing_tile, (
        f"proximity_filter not enriching every update; "
        f"missing tile_quadkey/zoom on {len(missing_tile)} updates"
    )

    aveiro_qks = {u["tile_quadkey"] for u in relevant_updates if u["car_id"] in (a_slow, a_fast)}
    ovar_qks = {u["tile_quadkey"] for u in relevant_updates if u["car_id"] in (b_slow, b_fast)}
    assert aveiro_qks and ovar_qks
    assert aveiro_qks.isdisjoint(ovar_qks), (
        "Aveiro and Ovar updates share a tile_quadkey — routing zoom too coarse"
    )

    pair_a = {a_fast, a_slow}
    pair_b = {b_fast, b_slow}

    own_alerts = [
        a for a in ALERTS
        if a.get("overtaking_car_id") in test_car_ids
        and a.get("overtaken_car_id") in test_car_ids
    ]
    cross_tile_alerts = [
        a for a in own_alerts
        if {a.get("overtaking_car_id"), a.get("overtaken_car_id")} not in (pair_a, pair_b)
    ]
    a_alerts = [
        a for a in own_alerts
        if a.get("overtaking_car_id") == a_fast and a.get("overtaken_car_id") == a_slow
    ]
    b_alerts = [
        a for a in own_alerts
        if a.get("overtaking_car_id") == b_fast and a.get("overtaken_car_id") == b_slow
    ]

    assert not cross_tile_alerts, f"detector compared cars across tiles: {cross_tile_alerts}"
    assert a_alerts, "expected an overtaking alert for the Aveiro pair, got none"
    assert b_alerts, "expected an overtaking alert for the Ovar pair, got none"


if __name__ == "__main__":
    test_proximity_filter_end_to_end(standalone_get_car_id)
