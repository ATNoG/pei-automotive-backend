# Overtaking Detector - Evaluation

## SUMO

There are 2 vehicles per scenario (3 in scenario 03), each with a known speed, lane, and starting position. The point of this test is not realism - it is controlled testing: we know exactly what should happen in each scenario, and we check if the detector agrees.
SUMO produces real GPS positions that the detector processes exactly as it would in production.

### Why a dedicated 2-lane network is required

The overtaking detector fires on a **projection sign flip**: car B is overtaken when the dot product of the vector from A to B and A's heading direction goes from positive (B ahead of A) to negative (B behind A). On a single-lane road SUMO's car-following model prevents the faster vehicle from physically passing - it decelerates to maintain `minGap` behind the leader indefinitely, so the positions never cross and the sign never flips. All true-positive scenarios would time out as false negatives if run on a single-lane network.

The evaluation uses a dedicated 2-lane straight highway (`network/overtaking.net.xml`, ~508 m, heading east ~90°). SUMO applies no car-following constraint between vehicles in different lanes, so the fast car in lane 1 (passing lane) freely passes the slow car in lane 0 (travel lane), producing a real GPS sign flip that reaches the detector.

## The 10 scenarios

Scenarios 01, 02, 03, 07, and 09 are **true positive** cases where an overtaking event must be detected. Scenarios 04, 05, 06, 08, and 10 are **true negative** cases where no alert must fire. Each scenario has a known expected outcome that we compare against what the detector actually outputs.

### Scenario 01 - Classic overtaking (TP)

100 km/h car (lane 1) starts 30 m behind a 40 km/h car (lane 0). Closing rate is 16.67 m/s → the pass completes in ~1.8 s at roughly 50 m from the start. Both cars remain within `PROXIMITY_M = 50 m` during approach, the sign flips from +1 to -1, and the alert fires.

### Scenario 02 - Delayed pass (TP)

100 km/h car (lane 1) starts 40 m behind a 50 km/h car (lane 0). The larger initial gap and smaller speed differential (13.89 m/s closing rate) mean the pass happens further along the road (~90 m). Tests that the detector fires even when the overtake is not immediate.

### Scenario 03 - Sequential overtaking (TP)

120 km/h car (lane 1) starts behind two slower cars: a 40 km/h car at pos=20 m and a 60 km/h car at pos=40 m (both lane 0). The fast car passes both in succession, producing two independent sign flips. The detector must fire at least once; both the `(merging, main)` and `(merging, main-2)` tracking pairs are exercised.

### Scenario 04 - Equal speed (TN)

Both cars travel at 80 km/h with a 30 m initial gap (main-car lane=0/pos=30, merging-car lane=1/pos=0). The longitudinal gap is constant - neither car closes on the other - so the sign never changes. Tests the base case: matching speeds must never produce a false alert.

### Scenario 05 - Fast car already ahead (TN / FP probe)

Fast car (100 km/h, lane 1) starts 30 m ahead of the slow car (40 km/h, lane 0). When the detector first sees both cars, the projection from the slow car's perspective already has the fast car ahead (sign = -1 for the `(fast, slow)` pair). There is no prior +1 state on record, so no +1 → -1 flip is possible. Tests that the detector does not fire when a faster car was never overtaken in the first place.

### Scenario 06 - Same lane, lane-change blocked (TN / FP probe)

Both cars start in lane 0. The fast car (100 km/h) uses a vType with all lane-change parameters disabled (`lcStrategic="0" lcSpeedGain="0" lcCooperative="0"`), preventing it from moving to the passing lane. SUMO's car-following model then holds the fast car behind the slow car indefinitely. The positions never cross, so no sign flip occurs. Tests that the detector is not fooled by a car that is tailgating but not actually overtaking.

### Scenario 07 - Extreme speed differential (TP)

120 km/h car (lane 1) starts only 20 m behind a 40 km/h car (lane 0). Closing rate is 22.22 m/s → the pass completes in ~0.9 s, making the detection window very short. Tests that the detector catches a high-speed pass with minimal time in proximity.

### Scenario 08 - Late departure at equal speed (TN / FN probe)

main-car departs at t=0 (80 km/h, lane 0); merging-car departs 10 s later (80 km/h, lane 1). By the time merging-car enters, main-car is already ~222 m ahead - well beyond `PROXIMITY_M = 50 m`. They never come within range, so the detector never records a sign and never fires. Tests that the proximity gate correctly suppresses unrelated vehicles that happen to share a road.

### Scenario 09 - Near-threshold speed differential (TP)

70 km/h car (lane 1) starts 30 m behind a 60 km/h car (lane 0). Closing rate is only 2.77 m/s → the pass takes ~10.8 s at roughly 210 m from the start. Both cars remain within 50 m throughout because their speeds are close. Tests that the detector catches a slow, gradual overtake and not just high-speed passes.

### Scenario 10 - Parallel lanes, identical speed (TN / FP probe)

Both cars depart simultaneously (80 km/h), main-car in lane 0 at pos=0, merging-car in lane 1 at pos=0. They travel in perfect parallel with zero longitudinal offset. The projection dot product is 0 at all times (tie-breaks to +1 by the `>= 0` convention in `_projection_sign`), so the sign stays permanently at +1 and never flips. Tests that side-by-side adjacent-lane traffic does not produce a false overtaking alert.

## Known false positives / detector edge cases

### Edge case 1 - Sign initialises at +1 on first observation (scenarios 04, 10)

`_projection_sign` returns `+1` whenever `dot >= 0`. Two cars side-by-side (dot = 0) therefore start with sign = +1. If anything momentarily pushes the dot below zero - a GPS jitter, a slight positional noise, or a brief lane-wander - the detector would fire an alert even though no overtake occurred. Scenario 10 verifies this does not happen in the clean SUMO case, but real GPS noise could trigger it.

**Mitigation:** add a small negative threshold (e.g., `dot < -0.5 m`) before counting a transition as -1.

### Edge case 2 - No heading tolerance check between pair updates (scenario 06 probe context)

The heading check (`HEADING_TOLERANCE_DEG = 30°`) is applied only when comparing car A's heading to car B's position vector. On a perfectly straight road this always passes; on a curved road or at an intersection two cars can briefly be within 50 m with headings that differ by more than 30° - one turning, the other going straight - and the detector correctly ignores them. The same check would incorrectly suppress a valid overtake if the overtaking car momentarily changes heading mid-pass. No scenario reproduces this but it is worth noting.

## How to run the evaluation

Make sure the backend is running (Ditto/Hono stack + MQTT broker + `overtaking_detector` service), then:

```bash
python3 scripts/eval.py --pack overtaking
```

This runs all 10 scenarios and prints a results table with precision, recall, F1, and accuracy.
To run only specific scenarios:

```bash
python3 scripts/eval.py --pack overtaking --scenarios 7 9
```

If you want to run the scenarios and view the SUMO GUI, do:

```bash
python3 scripts/eval.py --pack overtaking --gui
python3 scripts/eval.py --pack overtaking --scenarios 7 9 --gui
```

The network can be regenerated from source files if needed:

```bash
bash simulations/SUMO/scenarios/overtaking/network/generate.sh
```

The pack lives at `simulations/SUMO/scenarios/overtaking/` - see `pack.py` there for the scenario list, MQTT topic, timings, and the per-scenario detector cleanup hook. New scenario packs can be added as sibling folders with their own `pack.py`.
