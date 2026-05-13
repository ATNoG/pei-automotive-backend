"""Overtaking controller for SUMO simulations.

Uses TraCI to implement autonomous overtaking manoeuvres:
  1. Detect slow vehicles ahead in the same lane.
  2. Check left lane for a safe gap (front and rear clearance).
  3. Issue a lane-change command to lane 1 (left / overtaking lane).
  4. Monitor completion of the pass.
  5. Check right lane for a safe gap, then return to lane 0.

Design decisions
----------------
- All decisions are made via TraCI each simulation step; SUMO's built-in
  lane-change model is deliberately overridden (lanechange.duration=0) so
  that the controller has full authority.
- Safety margins are expressed as time-to-collision (TTC) thresholds rather
  than fixed distances, making them speed-independent.
- The ego vehicle ID and the set of "managed" vehicles are supplied at
  construction time so the same controller class can be reused across
  different scenarios.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional

import traci

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tuning constants
# ---------------------------------------------------------------------------
#: Ego must be slower than its max speed by at least this fraction before we
#: consider the vehicle ahead "slow" (avoids spurious detections at startup).
SLOW_RATIO_THRESHOLD: float = 0.85

#: Minimum gap (m) to a vehicle ahead in the same lane before triggering
#: an overtaking attempt.
FOLLOW_TRIGGER_GAP_M: float = 60.0

#: Minimum Time-To-Collision (s) with any vehicle in the target lane required
#: before issuing a lane change.  TTC = gap / relative_speed.
MIN_TTC_FOR_LANE_CHANGE_S: float = 4.0

#: How far ahead and behind (m) we scan the target lane for conflicts.
SCAN_FRONT_M: float = 150.0
SCAN_REAR_M:  float = 40.0

#: Gap above which the ego stops tracking a slow vehicle (hysteresis
#: to prevent FOLLOWING/CRUISING oscillation at the boundary).
FOLLOW_EXIT_GAP_M: float = 150.0

#: Minimum clearance (m) ahead of the slow vehicle before we consider the
#: overtake complete and attempt to return to lane 0.
RETURN_CLEARANCE_M: float = 20.0

#: How far ahead we scan for slow vehicles (wider than FOLLOW_TRIGGER_GAP_M
#: so that the controller detects the target early and evaluates the gap).
SLOW_SCAN_AHEAD_M: float = 200.0

#: Lane index constants.
LANE_RIGHT: int = 0   # main / slow lane
LANE_LEFT:  int = 1   # overtaking lane


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------
class OvertakeState(Enum):
    FOLLOWING   = auto()   # trailing a slow vehicle, evaluating safety
    CHANGING_L  = auto()   # lane-change command to lane 1 issued, awaiting completion
    OVERTAKING  = auto()   # in lane 1, passing the slow vehicle
    CHANGING_R  = auto()   # lane-change command to lane 0 issued, awaiting completion
    CRUISING    = auto()   # no slow vehicle detected; free flow


@dataclass
class OvertakeController:
    """Per-ego-vehicle overtaking state machine.

    Parameters
    ----------
    ego_id:
        TraCI vehicle ID of the ego vehicle.
    slow_ids:
        Set of TraCI vehicle IDs that are potential slow targets.
        If empty, the controller discovers slow vehicles dynamically.
    """

    ego_id: str
    slow_ids: set[str] = field(default_factory=set)

    _state: OvertakeState = field(default=OvertakeState.CRUISING, init=False)
    _target_id: Optional[str] = field(default=None, init=False)
    _state_steps: int = field(default=0, init=False)

    # ------------------------------------------------------------------
    def step(self) -> None:
        """Called once per simulation step.  Must be called after traci.simulationStep()."""
        if self.ego_id not in traci.vehicle.getIDList():
            return

        self._state_steps += 1

        if self._state == OvertakeState.CRUISING:
            self._handle_cruising()
        elif self._state == OvertakeState.FOLLOWING:
            self._handle_following()
        elif self._state == OvertakeState.CHANGING_L:
            self._handle_changing_left()
        elif self._state == OvertakeState.OVERTAKING:
            self._handle_overtaking()
        elif self._state == OvertakeState.CHANGING_R:
            self._handle_changing_right()

    # ------------------------------------------------------------------
    # State handlers
    # ------------------------------------------------------------------

    def _handle_cruising(self) -> None:
        target = self._find_slow_ahead()
        if target is not None:
            self._target_id = target
            self._transition(OvertakeState.FOLLOWING)

    def _handle_following(self) -> None:
        # Re-validate target still exists.
        if self._target_id not in traci.vehicle.getIDList():
            self._target_id = None
            self._transition(OvertakeState.CRUISING)
            return

        gap = self._gap_to(self._target_id)
        if gap is None or gap > FOLLOW_EXIT_GAP_M:
            # Hysteresis: only exit FOLLOWING when the gap grows well beyond
            # the trigger threshold, preventing CRUISING/FOLLOWING oscillation.
            self._target_id = None
            self._transition(OvertakeState.CRUISING)
            return

        if gap <= FOLLOW_TRIGGER_GAP_M and self._left_lane_is_safe():
            _log.info("[%s] Initiating lane change to left (gap=%.1f m)", self.ego_id, gap)
            traci.vehicle.changeLane(self.ego_id, LANE_LEFT, duration=2.0)
            self._transition(OvertakeState.CHANGING_L)

    def _handle_changing_left(self) -> None:
        current_lane = traci.vehicle.getLaneIndex(self.ego_id)
        if current_lane == LANE_LEFT:
            _log.info("[%s] Now in left lane — overtaking", self.ego_id)
            self._transition(OvertakeState.OVERTAKING)
        elif self._state_steps > 50:
            # Safety timeout: re-evaluate rather than getting stuck.
            _log.warning("[%s] Lane-change-left timed out; returning to FOLLOWING", self.ego_id)
            self._transition(OvertakeState.FOLLOWING)

    def _handle_overtaking(self) -> None:
        # Ensure we stay in the left lane during the pass.
        current_lane = traci.vehicle.getLaneIndex(self.ego_id)
        if current_lane != LANE_LEFT:
            traci.vehicle.changeLane(self.ego_id, LANE_LEFT, duration=1.0)

        if self._target_id not in traci.vehicle.getIDList():
            # Target vehicle left the simulation.
            self._transition(OvertakeState.CHANGING_R)
            return

        # Check whether ego has cleared the slow vehicle.
        ego_pos   = traci.vehicle.getLanePosition(self.ego_id)
        target_pos = traci.vehicle.getLanePosition(self._target_id)
        target_len = traci.vehicle.getLength(self._target_id)

        cleared = ego_pos > target_pos + target_len + RETURN_CLEARANCE_M

        # Also verify the right lane is clear before returning.
        if cleared and self._right_lane_is_safe():
            _log.info("[%s] Overtake complete — returning to right lane", self.ego_id)
            traci.vehicle.changeLane(self.ego_id, LANE_RIGHT, duration=2.0)
            self._transition(OvertakeState.CHANGING_R)

    def _handle_changing_right(self) -> None:
        current_lane = traci.vehicle.getLaneIndex(self.ego_id)
        if current_lane == LANE_RIGHT:
            _log.info("[%s] Back in right lane — cruising", self.ego_id)
            self._target_id = None
            self._transition(OvertakeState.CRUISING)
        elif self._state_steps > 50:
            _log.warning("[%s] Lane-change-right timed out; retrying", self.ego_id)
            traci.vehicle.changeLane(self.ego_id, LANE_RIGHT, duration=2.0)
            self._state_steps = 0

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _transition(self, new_state: OvertakeState) -> None:
        _log.debug("[%s] %s → %s", self.ego_id, self._state.name, new_state.name)
        self._state = new_state
        self._state_steps = 0

    def _find_slow_ahead(self) -> Optional[str]:
        """Return the ID of the closest slow vehicle in the same lane, or None."""
        ego_lane  = traci.vehicle.getLaneIndex(self.ego_id)
        ego_pos   = traci.vehicle.getLanePosition(self.ego_id)
        ego_max   = traci.vehicle.getMaxSpeed(self.ego_id)

        best_id:  Optional[str] = None
        best_gap: float         = float("inf")

        candidates = set(traci.vehicle.getIDList()) - {self.ego_id}

        for vid in candidates:
            if traci.vehicle.getLaneIndex(vid) != ego_lane:
                continue
            vpos = traci.vehicle.getLanePosition(vid)
            gap  = vpos - ego_pos - traci.vehicle.getLength(vid)
            if gap < 0 or gap > SLOW_SCAN_AHEAD_M:
                continue
            v_max = traci.vehicle.getMaxSpeed(vid)
            # Vehicle is considered slow if its max speed is below our desired speed.
            if v_max < ego_max * SLOW_RATIO_THRESHOLD:
                if gap < best_gap:
                    best_gap = gap
                    best_id  = vid

        return best_id

    def _gap_to(self, target_id: str) -> Optional[float]:
        """Return bumper-to-bumper gap (m) to *target_id*, or None if unavailable."""
        if target_id not in traci.vehicle.getIDList():
            return None
        ego_pos    = traci.vehicle.getLanePosition(self.ego_id)
        target_pos = traci.vehicle.getLanePosition(target_id)
        target_len = traci.vehicle.getLength(target_id)
        return target_pos - ego_pos - target_len

    def _left_lane_is_safe(self) -> bool:
        """True when lane 1 has sufficient TTC clearance front and rear."""
        return self._lane_is_safe(LANE_LEFT)

    def _right_lane_is_safe(self) -> bool:
        """True when lane 0 has sufficient TTC clearance front and rear."""
        return self._lane_is_safe(LANE_RIGHT)

    def _lane_is_safe(self, target_lane: int) -> bool:
        """Check TTC with all vehicles in *target_lane* near the ego position."""
        ego_pos   = traci.vehicle.getLanePosition(self.ego_id)
        ego_speed = traci.vehicle.getSpeed(self.ego_id)
        ego_edge  = traci.vehicle.getRoadID(self.ego_id)

        for vid in traci.vehicle.getIDList():
            if vid == self.ego_id:
                continue
            if traci.vehicle.getLaneIndex(vid) != target_lane:
                continue
            # Only check vehicles on the same edge.
            if traci.vehicle.getRoadID(vid) != ego_edge:
                continue

            v_pos   = traci.vehicle.getLanePosition(vid)
            v_speed = traci.vehicle.getSpeed(vid)
            v_len   = traci.vehicle.getLength(vid)

            # Relative position.
            delta_front = v_pos - ego_pos              # positive → ahead of ego
            delta_rear  = ego_pos - (v_pos + v_len)   # positive → ego is ahead

            # --- Front conflict: vehicle ahead in target lane ---
            if 0 < delta_front < SCAN_FRONT_M:
                rel_speed = ego_speed - v_speed  # positive = closing
                if rel_speed > 0.5:
                    ttc = delta_front / rel_speed
                    if ttc < MIN_TTC_FOR_LANE_CHANGE_S:
                        _log.debug(
                            "[%s] Front conflict in lane %d with %s: TTC=%.1fs",
                            self.ego_id, target_lane, vid, ttc,
                        )
                        return False

            # --- Rear conflict: vehicle behind in target lane closing fast ---
            if 0 < delta_rear < SCAN_REAR_M:
                rel_speed = v_speed - ego_speed  # positive = closing from behind
                if rel_speed > 0.5:
                    ttc = delta_rear / rel_speed
                    if ttc < MIN_TTC_FOR_LANE_CHANGE_S:
                        _log.debug(
                            "[%s] Rear conflict in lane %d with %s: TTC=%.1fs",
                            self.ego_id, target_lane, vid, ttc,
                        )
                        return False

        return True

    # ------------------------------------------------------------------
    @property
    def state(self) -> OvertakeState:
        """Current state (read-only)."""
        return self._state
