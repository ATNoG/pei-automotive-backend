# PEI Automotive Backend

Backend system for the Automotive App Project - A microservices architecture for processing vehicle telemetry data, detecting speeding violations, and monitoring overtaking maneuvers using Eclipse Ditto and Eclipse Hono.

## Overview

This system provides real-time vehicle monitoring capabilities:

- **Position Processing**: Processes GPS coordinates and updates digital twins
- **Speed Detection**: Monitors vehicle speed and detects violations
- **Overtaking Detection**: Identifies unsafe overtaking maneuvers
- **Digital Twin Management**: Maintains synchronized vehicle state using Eclipse Ditto
- **IoT Communication**: Handles device communication via Eclipse Hono and MQTT

## Prerequisites

- **Kubernetes**, like [K3s](https://k3s.io/)
- [**Helm**](https://helm.sh/)
- **Docker**
- **Python**

## Installation

After verifying you have all the prerequisites, you can start the installation process.

### 1. Clone this repo

```bash
git clone https://github.com/ATNoG/pei-automotive-backend.git
cd pei-automotive-backend
```

### 2. Install Eclipse Ditto and Hono

Clone Eclipse Ditto and Eclipse Hono using the Helm Chart repo:

```bash
git clone --recurse-submodules -j8 git@github.com:ATNoG/ditto-helm-chart.git
```

For detailed installation instructions, refer to the [Helm Chart documentation](https://github.com/ATNoG/ditto-helm-chart).

### 2. Run script to deploy Ditto and Hono

```bash
source scripts/run.sh
```

If you want to stop the containers running, you can run the `scripts/stop.sh` script.

### 3. Setup Environment Variables

Create a `.env` file in the project root with some variables.

You can check them with:

```bash
kubectl get pods -n cloud2edge
kubectl get svc -n cloud2edge
```

```bash
# Eclipse Ditto Configuration
DITTO_API_URL=https://<your-ditto-host>/api/2
DITTO_USER=ditto
DITTO_PASS=ditto
DITTO_WS_URL=wss://<your-ditto-host>/ws/2

# Eclipse Hono Configuration
HONO_API_URL=https://<your-hono-host>
HONO_USER=<your-hono-user>
HONO_PASS=<your-hono-password>
HONO_TENANT=org.eclipse.packages.c2e

# MQTT Adapter Configuration
MQTT_ADAPTER_IP=<your-mqtt-adapter-host>
MQTT_ADAPTER_PORT_MQTTS=8883
```

**Note:** Replace placeholder values with your actual deployment endpoints.

### 4. Install Python Dependencies

```bash
# For running services
cd backend
pip install -r requirements.txt
```

## Running the System

### Start All Services with Docker Compose

From the `backend` directory:

```bash
cd backend
docker compose up --build
```

This will start:
- **Mosquitto MQTT Broker** (port 1884)
- **Position Processor** service
- **Speed Detector** service
- **Overtaking Detector** service

## Using the System

### Creating a Vehicle

Create a new vehicle digital twin and register it with Hono:

```bash
cd simulations
python3 create_car.py <car_name> [--password <device_password>]
```

**Example:**

```bash
python3 create_car.py my-test-car --password secret123
```

This will:
1. Create a digital twin in Eclipse Ditto
2. Register the device in Eclipse Hono
3. Generate metadata file in `simulations/devices/<car_name>.json`

### Sending Position Updates

Send GPS position updates for a vehicle:

```bash
python3 send_position.py <car_name> <latitude> <longitude>
```

**Example:**

```bash
# Send position update
python3 send_position.py my-test-car 40.6316 -8.6579
```

**Using pre-defined routes:**

The system includes sample road data in `simulations/roads/`:
- `left_lane.json` - Left lane coordinates
- `right_lane.json` - Right lane coordinates

You can script position updates using these files.

## Services

### Position Processor

- **Purpose**: Processes incoming GPS coordinates
- **Input**: MQTT messages with latitude/longitude
- **Output**: Updates digital twin position features
- **Location**: `backend/services/position_processor/`

### Speed Detector

- **Purpose**: Calculates vehicle speed and detects violations
- **Input**: Position updates from digital twins
- **Output**: Speed calculations and violation alerts
- **Configuration**: Set `SPEED_LIMIT` in `.env`
- **Location**: `backend/services/speed_detector/`

### Overtaking Detector

- **Purpose**: Monitors multi-vehicle interactions for unsafe overtaking
- **Input**: Position data from multiple vehicles
- **Output**: Overtaking event notifications
- **Location**: `backend/services/overtaking_detector/`

## Testing

Run the test suite:

```bash
# Test speed detection
pytest tests/test_speeding.py

# Test overtaking detection
pytest tests/test_overtaking.py
```

## API Reference

### Eclipse Ditto API

- **Get Thing:** `GET /things/{thingId}`
- **Update Thing:** `PUT /things/{thingId}`
- **Update Feature:** `PUT /things/{thingId}/features/{featureId}`

[Full Ditto API Documentation](https://eclipse.dev/ditto/httpapi-overview.html)

### Eclipse Hono Management API

- **Register Device:** `POST /v1/devices/{tenantId}/{deviceId}`
- **Get Device:** `GET /v1/devices/{tenantId}/{deviceId}`

[Full Hono API Documentation](https://eclipse.dev/hono/docs/api/management/)

## License

See [LICENSE](LICENSE) file for details.

## Project Links

- **Project Documentation:** [Microsite - PEI Automotive App](https://atnog.github.io/pei-automotive-microsite/)
