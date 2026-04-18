import traci
import time
import argparse

def send(sim_time,vid, pos, speed, accel, angle, vtype):
    print(f"  sim_time={sim_time:.1f} s, vid={vid}: pos={pos}, speed={speed:.2f} m/s, "
                f"accel={accel:.2f} m/s², angle={angle:.1f}°, type={vtype}")

def main(configpath: str) -> None:
    traci.start(["sumo", "-c", configpath])

    print("Connected to SUMO - starting simulation...")

    # Run simulation until no more vehicles are expected
    while traci.simulation.getMinExpectedNumber() > 0:
        traci.simulationStep()
        
        sim_time = traci.simulation.getTime()  # current simulation time
        # print(f"\n=== Frame (Time = {sim_time:.1f} s) ===")

        vehicle_ids = traci.vehicle.getIDList()  # all vehicle IDs on network
        vehicle_count = traci.vehicle.getIDCount()
        # print(f"Vehicle count: {vehicle_count}")

        # at every timestep get position of vehicles
        for vid in vehicle_ids:

            pos = traci.vehicle.getPosition(vid)        # (x, y) coordinates (m)
            pos = traci.simulation.convertGeo(pos[0], pos[1])
            speed = traci.vehicle.getSpeed(vid)         # speed (m/s)
            angle = traci.vehicle.getAngle(vid)         # heading angle (°)
            accel = traci.vehicle.getAcceleration(vid)  # acceleration (m/s²)
            vtype = traci.vehicle.getTypeID(vid)

            send(sim_time, vid, pos, speed, accel, angle, vtype)
        time.sleep(1)

    traci.close()
    print("\nSimulation finished.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser("Script for running sumo with TraCI.")
    parser.add_argument("-c", "--config-path", default="osm.sumocfg")

    args = parser.parse_args()

    main(args.config_path)