# SUMO Simulation — Ponte da Barra

## What it is

`simulations/barraSUMOfinal/` is a [SUMO](https://sumo.dlr.de/) traffic simulation of the Ponte da Barra area in Aveiro, exported from OpenStreetMap. It generates realistic, continuous traffic (~1 000 vehicles/hour, mixed types) across the real road network — used to feed the backend pipeline instead of the hand-scripted test routes.

## Folder layout

```
simulations/barraSUMOfinal/
├── osm.sumocfg             ← main SUMO config (net + routes, 0–3600 s)
├── simulation.py           ← standalone TraCI example (print only)
├── networkfile/
│   └── barraOSM.net.xml    ← road graph from OSM
├── routes/
│   ├── routes.rou.xml      ← routed trips (output of duarouter)
│   ├── trips.trips.xml     ← random demand at 1 000 veh/h
│   ├── vehicledist.rou.xml ← type mix: 90% car, 7% moto, 2% bus, 1% truck
│   └── random.sh           ← regenerate demand with randomTrips.py
└── config/
    ├── osm.view.xml        ← sumo-gui visual settings
    └── output.add.xml      ← edge-data output collector
```

## Bridge (`scripts/bridge.py`)

`bridge.py` connects the simulation to the backend. It runs SUMO via TraCI and at every step PUTs each vehicle's GPS position directly to Eclipse Ditto via HTTP — bypassing Hono, which would be too slow for many concurrent vehicles (each `send_position.py` call costs 2–4 s due to subprocess startup + TLS handshake). A persistent `requests.Session` with a thread pool brings that down to ~200–600 ms per PUT.

`position_processor` listens to Ditto's WebSocket and fires the normal pipeline regardless of whether updates came through Hono or directly — so all detectors work as usual.

Each SUMO vehicle `vid` becomes `org.acme:sumo-{vid}` in Ditto. One shared policy is created on startup. `sumo-0` is the designated "user vehicle" on the frontend (camera follows it).

## Setup (one-time)

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Requires the backend `.env` at the repo root.

## Running

```bash
# From repo root:
python scripts/bridge.py \
    --max-steps 120 \
    --max-vehicles 30 \
    --real-time \
    --workers 24 \
    --cleanup
```

Always use `--real-time` (prevents flooding Ditto) and `--cleanup` (deletes things on exit — stale stopped vehicles get flagged as accidents by the detector on the next run).

## Flags

| Flag                   | Default | Description                           |
| ---------------------- | ------- | ------------------------------------- |
| `--max-vehicles N`     | —       | Cap vehicles published per step       |
| `--max-steps N`        | —       | Stop after N steps                    |
| `--workers N`          | 16      | HTTP thread pool size                 |
| `--real-time`          | off     | Pace sim to wall-clock time           |
| `--cleanup`            | off     | Delete all Ditto things on exit       |
| `--gui`                | off     | Launch `sumo-gui` instead of headless |
| `--metrics-interval S` | 2.0     | Seconds between log lines             |

## Regenerating traffic demand

```bash
cd simulations/barraSUMOfinal
./routes/random.sh  # requires SUMO tools on PATH
```
