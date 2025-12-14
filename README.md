# PEI Automotive Backend

Backend system for the [**Automotive App Project**](https://github.com/ATNoG/pei-automotive).

This repo has the code of our microservices that will process vehicle's telemetry data, detect all the events and send them to a message broker, to later be shown in the [frontend](https://github.com/ATNoG/pei-automotive-frontend).

We also have scripts to deploy the [cloud2edge](https://eclipse.dev/packages/packages/cloud2edge/) environment as well as tests (via pytest) to serve as simulations, so that the entirety of our project can be replicated and tested locally.

## Prerequisites

Even though our script will install all the necessary tools in case they aren't installed, if you prefer to install them manually, please refer to Eclipse's [pre-requisites](https://eclipse.dev/packages/prereqs/) page with the tools required to deploy the cloud2edge environment.

Be sure that the machine where you will deploy this meets the necessary hardware [requirements](https://eclipse.dev/packages/packages/cloud2edge/installation/).

## Build

If you follow instructions, you will be able to deploy the cloud2edge environment, start the services and run tests locally.

**Warnings:** 

Due to the fact that some images on c2e are outdated (see [issue](https://github.com/eclipse/packages/issues/553)) we had to manually update them and automate this process. And even though it worked on our machines, you might encounter some new issues when deploying the cloud2edge environment, if that happens, please refer to the [issues](https://github.com/eclipse/packages/issues) page.
 
### 1. Clone this repo

```bash
git clone https://github.com/ATNoG/pei-automotive-backend.git
cd pei-automotive-backend
```

### 2. Run the deployment script

This script will:
 - Install all the necessary tools ([k3s](https://k3s.io/) and [helm](https://helm.sh/)) in case they aren't installed.
 - Clone the cloud2edge repo and update it's Chart referenced versions. 
 - Create the custom values.yaml file.
 - Install the cloud2edge package with the custom versions and the custom values.yaml file, using helm.
 - Confirm all the pods are running like it's supposed to.
 - Make a .env file with the necessary variables for the backend services.

```bash
chmod +x deploy.sh
./deploy.sh
```

If you want to stop the containers running and remove the namespace, you can run the `stop.sh` script.

### 3. Start docker containers

```bash
docker compose up --build
```

This will start:
- **Position Processor**
- **Speed Detector** service
- **Overtaking Detector** service
- **Mosquitto MQTT Broker** (message broker)

## Using the System

### Creating a Vehicle

Create a new vehicle digital twin and register it with Hono:

```bash
cd simulations
python3 create_car.py <car_name>
```

This will:
 - Register the device in Eclipse Hono
 - Create a digital twin in Eclipse Ditto
 - Generate metadata file in `simulations/devices/<car_name>.json`

### Sending Position Updates

Send GPS position updates for a vehicle:

```bash
python3 send_position.py <car_name> <latitude> <longitude>
```

### Testing

Run the test suite:

```bash
# Test speed detection
pytest tests/test_speeding.py

# Test overtaking detection
pytest tests/test_overtaking.py

# Test route with curves
pytest tests/test_curved_route.py
```

Check latency on our system:
```bash
cd timing
python3 measure_latency.py <car_name>
```

## License

See [LICENSE](LICENSE) file for details.

## Project Links

- [**Microsite:**](https://atnog.github.io/pei-automotive-microsite/)
- [**Main Repo:**](https://github.com/ATNoG/pei-automotive)
