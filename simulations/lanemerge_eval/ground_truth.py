"""
Ground truth registry for the lane merge evaluation.

Each scenario has a known expected outcome derived from the physics of
the setup (speeds and initial positions).  The SUMO runner reproduces
the scenario; the detector's output is compared against this label to
compute precision / recall / F1.
"""
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ScenarioSpec:
    scenario_id: str
    description: str
    expected_outcome: str # "safe" | "unsafe"
    merging_speed_kmh: float
    main_lane_speed_kmh: Optional[float] # None when no main-lane car
    # How far the main-lane car is from the merge point when the
    # merging car starts approaching (+ = behind merge, - = past merge).
    main_lane_offset_m: Optional[float]


SCENARIOS: dict[str, ScenarioSpec] = {
    "01": ScenarioSpec(
        scenario_id="01",
        description="No main-lane traffic - merging car enters freely",
        expected_outcome="safe",
        merging_speed_kmh=60.0,
        main_lane_speed_kmh=None,
        main_lane_offset_m=None,
    ),
    "02": ScenarioSpec(
        scenario_id="02",
        description="Main-lane car far ahead (160 m past merge) - safe gap",
        expected_outcome="safe",
        merging_speed_kmh=60.0,
        main_lane_speed_kmh=80.0,
        main_lane_offset_m=-160.0,
    ),
    "03": ScenarioSpec(
        scenario_id="03",
        description="Main-lane car just outside entry zone (110 m past merge)",
        expected_outcome="safe",
        merging_speed_kmh=60.0,
        main_lane_speed_kmh=90.0,
        main_lane_offset_m=-110.0,
    ),
    "04": ScenarioSpec(
        scenario_id="04",
        description="Merging car very slow (20 km/h) - main-lane car passes first",
        expected_outcome="safe",
        merging_speed_kmh=20.0,
        main_lane_speed_kmh=90.0,
        main_lane_offset_m=55.0,
    ),
    "05": ScenarioSpec(
        scenario_id="05",
        description="Large time gap (200 m behind merge) - both at 60 km/h",
        expected_outcome="safe",
        merging_speed_kmh=60.0,
        main_lane_speed_kmh=60.0,
        main_lane_offset_m=200.0,
    ),
    "06": ScenarioSpec(
        scenario_id="06",
        description="Same speed (80 km/h), small gap (30 m) - collision course",
        expected_outcome="unsafe",
        merging_speed_kmh=80.0,
        main_lane_speed_kmh=80.0,
        main_lane_offset_m=30.0,
    ),
    "07": ScenarioSpec(
        scenario_id="07",
        description="Merging car fast (100 km/h) onto slow main lane (30 km/h)",
        expected_outcome="unsafe",
        merging_speed_kmh=100.0,
        main_lane_speed_kmh=30.0,
        main_lane_offset_m=35.0,
    ),
    "08": ScenarioSpec(
        scenario_id="08",
        description="Main-lane car right at merge point (5 m) - immediate conflict",
        expected_outcome="unsafe",
        merging_speed_kmh=60.0,
        main_lane_speed_kmh=60.0,
        main_lane_offset_m=5.0,
    ),
    "09": ScenarioSpec(
        scenario_id="09",
        description="Merging car slow (20 km/h), fast main-lane car (120 km/h) approaching",
        expected_outcome="unsafe",
        merging_speed_kmh=20.0,
        main_lane_speed_kmh=120.0,
        main_lane_offset_m=55.0,
    ),
    "10": ScenarioSpec(
        scenario_id="10",
        description="Two main-lane cars: first passes safely, second too close",
        expected_outcome="unsafe",
        merging_speed_kmh=60.0,
        main_lane_speed_kmh=80.0,
        main_lane_offset_m=30.0,
    ),
    # Known false negative case
    "11": ScenarioSpec(
        scenario_id="11",
        description="Fast main-lane car (120 km/h) 140 m out - beyond ENTRY_ZONE_M cutoff",
        expected_outcome="unsafe",
        merging_speed_kmh=60.0,
        main_lane_speed_kmh=120.0,
        main_lane_offset_m=140.0,
    )
}
