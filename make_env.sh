#!/usr/bin/env bash
# This script creates the .env file needed by Docker Compose services
# It queries the Cloud2Edge K3s cluster for service ports and generates the configuration

set -e

# Ensure KUBECONFIG is set
export KUBECONFIG=${KUBECONFIG:-/etc/rancher/k3s/k3s.yaml}

echo "[INFO] Using KUBECONFIG: $KUBECONFIG"

# Check if running from correct directory
if [ ! -f "requirements.txt" ]; then
    echo "[ERROR] This script must be run from the project root directory"
    exit 1
fi

# Verify kubectl is accessible
echo "[INFO] Verifying kubectl access..."
if ! kubectl cluster-info >/dev/null 2>&1; then
    echo "[ERROR] kubectl not accessible. Check KUBECONFIG or K3s status."
    exit 1
fi

# Verify cloud2edge namespace exists
echo "[INFO] Checking cloud2edge namespace..."
if ! kubectl get namespace cloud2edge >/dev/null 2>&1; then
    echo "[ERROR] cloud2edge namespace not found"
    exit 1
fi

echo "[INFO] Detecting service ports..."

# Get DITTO_PORT (critical - must exist)
DITTO_PORT=$(kubectl get svc -n cloud2edge c2e-ditto-nginx -o jsonpath='{.spec.ports[0].nodePort}' 2>/dev/null || echo "")
if [ -z "$DITTO_PORT" ]; then
    echo "[ERROR] Could not detect Ditto service port"
    kubectl get svc -n cloud2edge
    exit 1
fi
echo "[INFO] Ditto port: $DITTO_PORT"

# Get other ports (optional - use empty string if not found, not N/A)
HONO_HTTP_PORT=$(kubectl get svc -n cloud2edge c2e-hono-adapter-http -o jsonpath='{.spec.ports[0].nodePort}' 2>/dev/null || echo "")
HONO_MQTT_PORT=$(kubectl get svc -n cloud2edge c2e-hono-adapter-mqtt -o jsonpath='{.spec.ports[0].nodePort}' 2>/dev/null || echo "")
HONO_AMQP_PORT=$(kubectl get svc -n cloud2edge c2e-hono-adapter-amqp -o jsonpath='{.spec.ports[0].nodePort}' 2>/dev/null || echo "")
HONO_REG_PORT=$(kubectl get svc -n cloud2edge c2e-hono-service-device-registry-ext -o jsonpath='{.spec.ports[0].nodePort}' 2>/dev/null || echo "")

echo "[INFO] Hono HTTP port: ${HONO_HTTP_PORT:-N/A}"
echo "[INFO] Hono MQTT port: ${HONO_MQTT_PORT:-N/A}"
echo "[INFO] Hono Registry port: ${HONO_REG_PORT:-N/A}"

# Get the certificate file path
CERT_PATH="$(pwd)/cloud2edge-repo/charts/hono/example/certs/trusted-certs.pem"
if [ ! -f "$CERT_PATH" ]; then
    echo "[WARNING] Certificate not found at $CERT_PATH"
    CERT_PATH=$(find . -path "*/cloud2edge-repo/charts/hono/example/certs/trusted-certs.pem" 2>/dev/null | head -1)
    if [ -z "$CERT_PATH" ]; then
        echo "[WARNING] Certificate not found anywhere, using default path"
        CERT_PATH="$(pwd)/cloud2edge-repo/charts/hono/example/certs/trusted-certs.pem"
    fi
fi

# Get host IP (exclude docker and kubernetes interfaces)
HOST_IP=$(ip -4 addr show 2>/dev/null | grep -oP '(?<=inet\s)\d+(\.\d+){3}' | grep -v '127.0.0.1' | grep -v '172\.' | grep -v '10\.42\.' | head -1)

if [ -z "$HOST_IP" ]; then
    echo "[WARNING] Could not detect host IP, using localhost"
    HOST_IP="localhost"
else
    echo "[INFO] Using host IP: $HOST_IP"
fi

# Create .env file
echo "[INFO] Creating .env file..."
cat > .env << EOF
# Ditto Configuration
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

# Hono MQTT Adapter
MQTT_ADAPTER_IP=${HOST_IP}
MQTT_ADAPTER_PORT_MQTTS=${HONO_MQTT_PORT}
EOF

if [ ! -f .env ]; then
    echo "[ERROR] Failed to create .env file"
    exit 1
fi

echo "[INFO] .env file created successfully"
cat .env