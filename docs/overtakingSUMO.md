# Overtaking Detector - Evaluation

## SUMO

There are 2 vehicles per scenario, each with a known speed and position. The point of this test is not realism - it is controlled testing: we know exactly what should happen in each scenario, and we check if the detector agrees.
SUMO produces real GPS positions that the detector processes exactly as it would in production.

## The 11 scenarios

Scenarios 01–03 are **overtaking events** (one vehicle passes another). Scenarios 04–06, 08, and 11 are **safe scenarios** (no overtaking detected). Scenarios 07 and 10 are known failure cases:
- Scenario 07: **false negative** - extreme speed difference with short gap
- Scenario 10: **false positive** - side-by-side vehicles at same speed

Each has a known expected outcome that we compare against what the detector actually outputs.

## Known issues

### False negative - extreme speed difference (scenario 07)

Scenario 07 presents a 120 km/h vehicle passing a 40 km/h vehicle over a 20 m gap (~0.9 seconds of relative motion). The overtaking event is too brief relative to the detector's sampling interval and state tracking, causing it to be missed. The vehicle pair completes the maneuver too quickly for the detector to capture the longitudinal relationship flip that indicates overtaking.

**Fix:** Review the detector's relative position tracking window and sample rate to ensure rapid overtaking maneuvers are not dropped.

**Benefits:** catches high-speed overtaking scenarios with minimal gap; more comprehensive detection coverage.
**Losses:** may require lower thresholds or more frequent state updates, increasing CPU cost per message.

### False positive - side-by-side same speed (scenario 10)

Scenario 10 places two vehicles side-by-side at equal speed (96.5 km/h). The detector incorrectly reports an overtaking event despite the vehicles maintaining parallel motion with no longitudinal relationship change. This suggests the detector is too sensitive to lateral separation or is misinterpreting parallel motion as a position flip.

**Fix:** strengthen the longitudinal relationship validation to ensure a clear position flip occurs (vehicle A ahead → vehicle B ahead) rather than triggering on lateral proximity alone.

**Benefits:** reduces false positives in heavy traffic or multi-lane scenarios with parallel vehicles.
**Losses:** may require additional state history or a stricter transition matrix, with modest CPU overhead.

## Scenario breakdown

| ID | Description | Expected | Actual |
|---|---|---|---|
| 01 | Classic: 100 km/h overtakes 40 km/h, 30 m gap | overtaking | overtaking |
| 02 | Delayed: 100 km/h starts 40 m behind 50 km/h | overtaking | overtaking | 
| 03 | Sequential: 120 km/h overtakes 40 km/h then 60 km/h | overtaking | overtaking |
| 04 | Equal speed: 80 km/h, 30 m gap | no_event | no_event |
| 05 | Fast car already ahead, 30 m gap | no_event | no_event |
| 06 | Same lane, lane-change blocked | no_event | no_event |
| 07 | Extreme diff: 120 km/h passes 40 km/h, 20 m gap | **overtaking** | no_event |
| 08 | Merging car departs 10 s late at same speed | no_event | no_event |
| 09 | Near-threshold: 70 km/h passes 60 km/h, slow pass | overtaking | overtaking |
| 10 | Side-by-side same speed | **no_event** | overtaking |
| 11 | Oncoming: 2 cars in opposite directions | no_event | no_event |

## Current metrics

- **Precision:** 0.8 (4 TP, 1 FP)
- **Recall:** 0.8 (4 TP, 1 FN)
- **F1 Score:** 0.8
- **Accuracy:** 0.8182 (9 correct out of 11)

## How to run the evaluation

Make sure the backend is running (Ditto/Hono stack + MQTT broker + `overtaking_detector` service), then:

```bash
python3 scripts/SUMO/eval.py --pack overtaking
```

This runs all 11 scenarios and prints a results table with precision, recall, F1, and accuracy.

To run only specific scenarios:

```bash
python3 scripts/SUMO/eval.py --pack overtaking --scenarios 7 10
```

If you want to run the scenarios and view the SUMO GUI, do:

```bash
python3 scripts/SUMO/eval.py --pack overtaking --gui
python3 scripts/SUMO/eval.py --pack overtaking --scenarios 7 10 --gui
```

The pack lives at `simulations/SUMO/scenarios/overtaking/` — see `pack.py` there for the scenario list, MQTT topic, timings and the per-scenario detector cleanup hook. New scenario packs can be added as sibling folders with their own `pack.py`.
