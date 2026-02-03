#!/usr/bin/env bash
# This script creates the .env file needed for Docker Compose services
# This logic is copied from deploy.sh, so, you probably dont need to run this.
# This script is only used in test.yml

set -e

# Setup kubeconfig - try ~/.kube/config first, then system default
if [ -f "$HOME/.kube/config" ]; then
    export KUBECONFIG="$HOME/.kube/config"
    echo "[INFO] Using kubeconfig: $KUBECONFIG"
else
    echo "[ERROR] No kubeconfig found at ~/.kube/config"
    exit 1
fi

# Check if running from correct directory
if [ ! -f "requirements.txt" ]; then
    echo "[ERROR] This script must be run from the project root directory"
    exit 1
fi

echo -e "[INFO] Detecting service ports..."

# Get NodePorts for services
DITTO_PORT=$(kubectl get svc -n cloud2edge c2e-ditto-nginx -o jsonpath='{.spec.ports[0].nodePort}' 2>/dev/null || echo "N/A")
HONO_HTTP_PORT=$(kubectl get svc -n cloud2edge c2e-hono-adapter-http -o jsonpath='{.spec.ports[0].nodePort}' 2>/dev/null || echo "N/A")
HONO_MQTT_PORT=$(kubectl get svc -n cloud2edge c2e-hono-adapter-mqtt -o jsonpath='{.spec.ports[0].nodePort}' 2>/dev/null || echo "N/A")
HONO_AMQP_PORT=$(kubectl get svc -n cloud2edge c2e-hono-adapter-amqp -o jsonpath='{.spec.ports[0].nodePort}' 2>/dev/null || echo "N/A")
HONO_REG_PORT=$(kubectl get svc -n cloud2edge c2e-hono-service-device-registry-ext -o jsonpath='{.spec.ports[0].nodePort}' 2>/dev/null || echo "N/A")


# Get the certificate file path
CERT_PATH="$(pwd)/cloud2edge-repo/charts/hono/example/certs/trusted-certs.pem"

# Get host IP (exclude docker and kubernetes interfaces)
HOST_IP=$(ip -4 addr show | grep -oP '(?<=inet\s)\d+(\.\d+){3}' | grep -v '127.0.0.1' | grep -v '172\.' | grep -v '10\.42\.' | head -1)

# Create .env file
echo -e "[INFO] Creating .env file..."
cat > .env << EOF
# Ditto Configuration
# Note: Using host IP (${HOST_IP}) for Docker containers to access K3s services
DITTO_WS_URL=ws://${HOST_IP}:${DITTO_PORT}/ws/2
DITTO_API_URL=http://${HOST_IP}:${DITTO_PORT}
DITTO_USER=ditto
DITTO_PASS=ditto

# Hono Configuration
HONO_API_URL=https://${HOST_IP}:${HONO_REG_PORT}
HONO_USER=hono-admin
HONO_PASS=hono-admin
HONO_TENANT=org.eclipse.packages.c2e
CERT=${CERT_PATH}

# MQTT Broker (Internal Docker network)
MQTT_BROKER_HOST=mosquitto_broker
MQTT_BROKER_PORT=1883
MQTT_BROKER_USER=
MQTT_BROKER_PASSWORD=

# MQTT Topics
MQTT_CAR_UPDATES_TOPIC=cars/updates

# Hono MQTT Adapter (External access from host or containers)
MQTT_ADAPTER_IP=${HOST_IP}
MQTT_ADAPTER_PORT_MQTTS=${HONO_MQTT_PORT}
EOF

echo -e "[INFO] .env file created successfully!"
