import traci
import time
import argparse
from typing import Callable, Optional


def main(
    configpath: str,
    on_vehicle: Optional[Callable] = None,
    on_step_end: Optional[Callable] = None,
    gui: bool = False,
    extra_flags: Optional[list] = None,
) -> None:
    """Run a SUMO simulation via TraCI.

    on_vehicle(sim_time, vid, lat, lon, speed_ms, angle_deg, vtype) -> None
        Called for every active vehicle at every step.

    on_step_end(step_idx, sim_time, n_vehicles, step_start_t) -> bool | None
        Called once per step after all vehicles are processed.
        Return False to stop the simulation early.

    When no callbacks are provided the vehicle state is printed to stdout.
    """
    binary = "sumo-gui" if gui else "sumo"
    cmd = [binary, "-c", configpath] + (extra_flags or [])
    traci.start(cmd)

    step_idx = 0
    while traci.simulation.getMinExpectedNumber() > 0:
        step_start_t = time.time()
        traci.simulationStep()
        sim_time = traci.simulation.getTime()
        vehicle_ids = traci.vehicle.getIDList()

        for vid in vehicle_ids:
            x, y = traci.vehicle.getPosition(vid)
            lon, lat = traci.simulation.convertGeo(x, y)
            speed = traci.vehicle.getSpeed(vid)
            angle = traci.vehicle.getAngle(vid)
            vtype = traci.vehicle.getTypeID(vid)
            if on_vehicle:
                on_vehicle(sim_time, vid, lat, lon, speed, angle, vtype)
            else:
                accel = traci.vehicle.getAcceleration(vid)
                print(f"  sim_time={sim_time:.1f}s  {vid}: "
                      f"pos=({lon:.6f},{lat:.6f})  speed={speed:.2f}m/s  "
                      f"accel={accel:.2f}m/s²  angle={angle:.1f}°  type={vtype}")

        step_idx += 1
        if on_step_end and on_step_end(step_idx, sim_time, len(vehicle_ids), step_start_t) is False:
            break

    traci.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser("Run SUMO simulation via TraCI.")
    parser.add_argument("-c", "--config-path", default="osm.sumocfg")
    parser.add_argument("--gui", action="store_true")
    args = parser.parse_args()
    main(args.config_path, gui=args.gui)
    print("Simulation finished.")
