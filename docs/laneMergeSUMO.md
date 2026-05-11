# Lane Merge Detector - Evaluation

## SUMO

There are 1 or 2 vehicles per scenario, each with a known speed and position. The point of this test is not realism - it is controlled testing: we know exactly what should happen in each scenario, and we check if the detector agrees.
SUMO produces real GPS positions that the detector processes exactly as it would in production.
If SUMO is not installed, the runner falls back to pure Python: it interpolates positions along those same route files and publishes the same MQTT messages. The detector cannot tell the difference.

## The 10 scenarios

Scenarios 01–05 are **safe** (enough gap, no traffic, slow merging car). Scenarios 06–09 are **unsafe** (insufficient gap at the moment the detector triggers). Scenario 10 is an **expected failure** that demonstrates a known false negative. Each has a known expected outcome that we compare against what the detector actually outputs.

The detector triggers when the merging car is within 20 m of the merge point. Scenario offsets are designed so the main-lane car is still 10–30 m from the merge *at that moment* - not at simulation start. A car starting 30 m away at 80 km/h covers those 30 m in 1.35 s; the trigger fires after ~1.95 s, so the initial offset must be ~73 m to leave 30 m at trigger time.

## Known false negatives

### False negative 1 - entry zone cutoff (scenario 10)

The detector only checks main-lane cars that are within `ENTRY_ZONE_M = 100 m` of the merge point. A car doing 120 km/h (33.3 m/s) at 140 m from the merge arrives in ~4.2 s - a real collision risk if the merging car is also approaching the merge point at the same time, because both vehicles converge on the same point within the detector's 5-second prediction window but the main-lane car is never even considered.

Scenario 10 reproduces this with SUMO: the merging car departs near the end of the entering ramp so the trigger fires immediately, and the highway car is exactly 140 m away - just outside the 100 m entry zone. The detector outputs "safe" even though a collision is imminent. **The kinematic fallback cannot reproduce this false negative** because the longer entering.json route (73.4 m vs SUMO's 63.4 m) delays the trigger by 3.2 s, by which time the fast highway car has closed to ~33 m - inside the zone - and the collision is detected. SUMO is required.

**Fix:** increase `ENTRY_ZONE_M` to at least 150 m.

**Benefits:** catches fast-approaching cars that are outside the current cutoff but still within the collision prediction window; fewer missed unsafe merges at highway speeds.
**Losses:** more cars are evaluated per update cycle, which slightly increases CPU cost per message. More importantly, a larger zone increases the chance of false positives - a car at 150 m may look close on paper but will clearly pass the merge before the merging car even arrives, yet the prediction might still flag it as unsafe depending on relative speeds. If `ENTRY_ZONE_M` is increased, the prediction logic should be reviewed alongside it.

## How to run the evaluation

Make sure the backend is running (MQTT broker + `lane_merge_detector` service), then:

```bash
python3 scripts/eval_lanemerge.py
```

This runs all 10 scenarios and prints a results table with precision, recall, F1, and accuracy. Scenario 10 is expected to fail until the fix above is applied.
To run only specific scenarios:

```bash
python3 scripts/eval_lanemerge.py --scenarios 9 10
```

## Folder layout

```
simulations/lanemerge_eval/
├── ground_truth.py       - 10 scenarios with expected outcomes
├── runner.py             - runs SUMO via TraCI and publishes to MQTT (kinematic fallback if no SUMO)
├── network/
│   ├── gen_network.py    - generates the synthetic 3-edge SUMO network from real GPS coords
│   ├── lanemerge.net.xml - the generated network
│   └── lanemerge.sumocfg - SUMO config (route file injected per scenario at runtime)
└── scenarios/
    ├── scenario_01.rou.xml  - no main-lane traffic (safe)
    ├── ...
    ├── scenario_09.rou.xml  - two main-lane cars, second too close (unsafe)
    ├── scenario_10.rou.xml  - fast car at 140 m, outside entry zone (false negative)
scripts/
└── eval_lanemerge.py     - run this to evaluate
```
