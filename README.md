# PEI Automotive Backend

Backend system for the [**Automotive App Project**](https://github.com/ATNoG/pei-automotive).

This repo has the code of our microservices that will process vehicle's telemetry data, detect all the events and send them to a message broker, to later be shown in the [frontend](https://github.com/ATNoG/pei-automotive-frontend).

We also have scripts to deploy the [cloud2edge](https://eclipse.dev/packages/packages/cloud2edge/) environment as well as tests (via pytest) to serve as simulations, so that the entirety of our project can be replicated and tested locally.

## Pre-Requisites

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

### 3. Start Docker Containers

```bash
docker compose up --build
```

This will start:

- **Position Processor**
- **Mosquitto MQTT Broker** (message broker)
- And all our multiple **Event Detection Services**

And that's it! The cloud2edge is deployed and the backend services are running. You can now start using the system with tests and/or even build the [frontend](https://github.com/ATNoG/pei-automotive-frontend).

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

Install the required dependencies:

```bash
pip install -r requirements.txt
```

Run the test suite:

```bash
# Test speed detection
pytest tests/test_speeding.py

# Test overtaking detection
pytest tests/test_overtaking.py

# Test route with curves
pytest tests/test_curved_route.py

# ...
# You can run all the other tests
```

## Third-Party Data and Component Attribution

### Weather Data
This application utilizes weather data provided by **OpenWeatherMap**, which is made available under the **Open Database License (ODbL)**. To ensure compliance with ODbL requirements, all weather information is properly credited to the provider. The application maintains data integrity by keeping proprietary sensor data distinct from OpenWeatherMap data through a user interface toggle, which prevents unintended data mingling and satisfies Share-Alike clauses. And link to the **OpenWeatherMap** website can be accessed below.

[OpenWeatherMap](https://openweathermap.org/)

### Map  Display
This application utilizes a map provided by **MapTiler**, which is made available under the **Open Database License (ODbL)**. To ensure compliance with ODbL requirements, the map displayed is properly credited to the provider. A link to the **MapTiler** website can be accessed below.

[MapTiler](https://www.maptiler.com/)


### Open Source Libraries & Compatibility
In addition to the core application code, this project incorporates several open-source libraries to ensure a robust and interoperable architecture:
* **Eclipse Suite (SUMO, Ditto, Hono, Mosquitto):** Utilized under the **Eclipse Public License 2.0 (EPL 2.0)**.
* **Identity Management (KeyCloak):** Provided under the **Apache 2.0** license.
* **Cached Database (PostgreSQL):** Provided under Postgre's own **PostgreSQL License**.

These libraries are integrated as separate components, ensuring there are no license conflicts with the core MIT-licensed application logic or potential App Store deployment.

## Conclusion
The project is released under the MIT License to facilitate future academic iteration and research within the **Aveiro Telecommunications and Networking Group (ATNoG)**. This structure ensures the app does not violate the rules of any utilized tools, remains compatible with App Store deployment, and is ready for further future development.

## License

See [LICENSE](LICENSE) file for details.

## Project Links

- [**Microsite**](https://atnog.github.io/pei-automotive-microsite/)
- [**Main Repo**](https://github.com/ATNoG/pei-automotive)
