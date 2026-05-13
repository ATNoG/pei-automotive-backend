#!/usr/bin/env python3
"""Overtaking scenario runner.

Runs a single overtaking scenario in SUMO (GUI or headless) via TraCI.
The OvertakeController is instantiated for the ego vehicle and stepped
every simulation tick.

Usage
-----
    python run_overtaking.py --scenario 01 [--gui] [--log-level DEBUG]
    python run_overtaking.py --scenario 02 --gui

All five scenarios share the same binary; only the route file and ego ID
change between them.

SUMO binary resolution order
-----------------------------
1. Executable found on PATH (system SUMO install).
2. eclipse-sumo pip package  (``pip install eclipse-sumo``).
3. FileNotFoundError with a helpful message.
"""
from __future__ import annotations

import argparse
import logging
import shutil
import sys
import time
from pathlib import Path

# Allow running from any CWD.
_PACK_DIR = Path(__file__).resolve().parent

# Bootstrap path for traci when eclipse-sumo is installed as a package.
try:
    import traci  # noqa: F401 (check availability first)
except ModuleNotFoundError:
    sys.exit(
        "traci not found.  Install with:\n"
        "  pip install eclipse-sumo traci sumolib\n"
        "or activate the project virtual environment."
    )

import traci
from overtaking_controller import OvertakeController, OvertakeState
from pack import SCENARIOS, SUMOCFG, ScenarioSpec


def _resolve_sumo_binary(gui: bool) -> str:
    """Return the absolute path to sumo or sumo-gui.

    Checks PATH first, then falls back to the eclipse-sumo pip package
    which ships the binaries under ``<site-packages>/sumo/bin/``.
    """
    name = "sumo-gui" if gui else "sumo"
    exe_name = name + (".exe" if sys.platform == "win32" else "")

    # 1. System PATH.
    found = shutil.which(name)
    if found:
        return found

    # 2. eclipse-sumo pip package.
    try:
        import sumo as _sumo_pkg  # type: ignore[import-untyped]
        candidate = Path(_sumo_pkg.__file__).parent / "bin" / exe_name
        if candidate.exists():
            return str(candidate)
    except ModuleNotFoundError:
        pass

    raise FileNotFoundError(
        f"SUMO binary '{name}' not found on PATH and eclipse-sumo package "
        "is missing.  Run: pip install eclipse-sumo"
    )


def _build_cmd(scenario: ScenarioSpec, gui: bool, step_length: float) -> list[str]:
    """Build the SUMO command list for TraCI.start()."""
    binary = _resolve_sumo_binary(gui)
    return [
        binary,
        "-c", str(SUMOCFG),
        "--route-files", str(scenario.route_file),
        "--step-length", str(step_length),
        "--no-warnings", "true",
        "--no-step-log", "true",
        "--collision.action", "warn",
    ]


def run_scenario(
    scenario: ScenarioSpec,
    *,
    gui: bool = False,
    step_length: float = 0.1,
    max_steps: int = 1200,
) -> dict:
    """Run one overtaking scenario and return a result summary dict.

    Parameters
    ----------
    scenario:
        ScenarioSpec from pack.SCENARIOS.
    gui:
        Open sumo-gui instead of headless sumo.
    step_length:
        Simulation step in seconds.
    max_steps:
        Hard-stop after this many steps (120 s / 0.1 s = 1200 default).

    Returns
    -------
    dict with keys: scenario_id, ego_id, final_state, steps, collisions, success.
    """
    cmd = _build_cmd(scenario, gui, step_length)
    log = logging.getLogger(__name__)
    log.info("Starting scenario %s  ego=%s  gui=%s", scenario.scenario_id, scenario.ego_id, gui)
    log.debug("CMD: %s", " ".join(cmd))

    traci.start(cmd)

    controller: OvertakeController | None = None
    steps = 0
    collisions = 0
    final_state = OvertakeState.CRUISING

    try:
        while traci.simulation.getMinExpectedNumber() > 0 and steps < max_steps:
            traci.simulationStep()
            steps += 1
            sim_time = traci.simulation.getTime()

            # Detect collisions reported by SUMO.
            collisions += len(traci.simulation.getCollisions())

            active_ids = traci.vehicle.getIDList()

            # Instantiate controller as soon as the ego vehicle appears.
            if controller is None and scenario.ego_id in active_ids:
                controller = OvertakeController(ego_id=scenario.ego_id)
                # Force ego to lane 0 immediately on insertion.
                # SUMO's own model may migrate faster vehicles to lane 1;
                # we override that here so the controller starts correctly.
                traci.vehicle.changeLane(scenario.ego_id, 0, duration=0.0)
                log.info("[%.1fs] Ego %s inserted - controller activated", sim_time, scenario.ego_id)

            if controller is not None:
                controller.step()

                # Emit a periodic log line for visibility.
                if steps % 50 == 0:
                    ego_in_sim = scenario.ego_id in active_ids
                    speed = (
                        traci.vehicle.getSpeed(scenario.ego_id) if ego_in_sim else 0.0
                    )
                    lane = (
                        traci.vehicle.getLaneIndex(scenario.ego_id) if ego_in_sim else -1
                    )
                    log.info(
                        "[%.1fs] state=%-12s  lane=%d  speed=%.1f m/s  collisions=%d",
                        sim_time,
                        controller.state.name,
                        lane,
                        speed,
                        collisions,
                    )
                final_state = controller.state

    finally:
        traci.close()

    success = collisions == 0 and final_state in (
        OvertakeState.CRUISING,
        OvertakeState.FOLLOWING,   # acceptable if road ends before return completes
    )

    result = {
        "scenario_id":    scenario.scenario_id,
        "ego_id":         scenario.ego_id,
        "final_state":    final_state.name,
        "steps":          steps,
        "sim_time_s":     round(steps * step_length, 1),
        "collisions":     collisions,
        "success":        success,
    }
    log.info("Result: %s", result)
    return result


def main() -> None:
    """CLI entry-point."""
    parser = argparse.ArgumentParser(
        description="Run a SUMO overtaking scenario via TraCI.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Available scenarios: " + ", ".join(sorted(SCENARIOS)),
    )
    parser.add_argument(
        "--scenario", "-s",
        default="01",
        choices=sorted(SCENARIOS),
        help="Scenario ID to run (default: 01).",
    )
    parser.add_argument(
        "--gui",
        action="store_true",
        help="Open SUMO GUI instead of headless.",
    )
    parser.add_argument(
        "--step-length",
        type=float,
        default=0.1,
        metavar="S",
        help="Simulation step length in seconds (default: 0.1).",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Python logging level (default: INFO).",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%H:%M:%S",
    )

    scenario = SCENARIOS[args.scenario]
    result = run_scenario(scenario, gui=args.gui, step_length=args.step_length)

    print("\n" + "=" * 60)
    outcome = "SUCCESS" if result["success"] else "FAILED"
    print(f"  Scenario {result['scenario_id']}  --  {outcome}")
    print(f"  Final state : {result['final_state']}")
    print(f"  Sim time    : {result['sim_time_s']} s  ({result['steps']} steps)")
    print(f"  Collisions  : {result['collisions']}")
    print("=" * 60)


    sys.exit(0 if result["success"] else 1)


if __name__ == "__main__":
    main()
