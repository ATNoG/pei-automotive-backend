# Lane Merge Detector - Evaluation

## SUMO

There are 1 or 2 vehicles per scenario, each with a known speed and position. The point of this test is not realism - it is controlled testing: we know exactly what should happen in each scenario, and we check if the detector agrees.
SUMO produces real GPS positions that the detector processes exactly as it would in production.
If SUMO is not installed, the runner falls back to pure Python: it interpolates positions along those same route files and publishes the same MQTT messages. The detector cannot tell the difference.

## The 10 scenarios

Scenarios 01–05 are **safe** (enough gap, no traffic, slow merging car). Scenarios 06–10 are **unsafe** (small gap, speed mismatch, car right at the merge point). Each has a known expected outcome that we compare against what the detector actually outputs.

## Known false negatives

### False negative 1 - entry zone cutoff (scenario 11)

The detector only checks main-lane cars that are within `ENTRY_ZONE_M = 100 m` of the merge point. A car doing 120 km/h (33.3 m/s) at 140 m from the merge arrives in ~4.2 s - a real collision risk if the merging car is also approaching the merge point at the same time, because both vehicles converge on the same point within the detector's 5-second prediction window but the main-lane car is never even considered.

**Fix:** increase `ENTRY_ZONE_M` to at least 150 m.

**Benefits:** catches fast-approaching cars that are outside the current cutoff but still within the collision prediction window; fewer missed unsafe merges at highway speeds.
**Losses:** more cars are evaluated per update cycle, which slightly increases CPU cost per message. More importantly, a larger zone increases the chance of false positives - a car at 150 m may look close on paper but will clearly pass the merge before the merging car even arrives, yet the prediction might still flag it as unsafe depending on relative speeds. If `ENTRY_ZONE_M` is increased, the prediction logic should be reviewed alongside it.

## How to run the evaluation

Make sure the backend is running (MQTT broker + `lane_merge_detector` service), then:

```bash
python3 scripts/eval_lanemerge.py
```

This runs all 11 scenarios and prints a results table with precision, recall, F1, and accuracy. Scenario 11 is expected to fail until the fix above is applied.
To run only specific scenarios:

```bash
python3 scripts/eval_lanemerge.py --scenarios 9 10 11
```

## Folder layout

```
simulations/lanemerge_eval/
├── ground_truth.py       - 11 scenarios with expected outcomes
├── runner.py             - runs SUMO via TraCI and publishes to MQTT (kinematic fallback if no SUMO)
├── network/
│   ├── gen_network.py    - generates the synthetic 3-edge SUMO network from real GPS coords
│   ├── lanemerge.net.xml - the generated network
│   └── lanemerge.sumocfg - SUMO config (route file injected per scenario at runtime)
└── scenarios/
    ├── scenario_01.rou.xml  - no main-lane traffic (safe)
    ├── ...
    ├── scenario_10.rou.xml  - two main-lane cars, second too close (unsafe)
    ├── scenario_11.rou.xml  - fast car at 140 m, outside entry zone (false negative)
scripts/
└── eval_lanemerge.py     - run this to evaluate
```
