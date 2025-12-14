#!/usr/bin/env bash
# This script automates the complete deployment and installation of:
# - K3s Kubernetes cluster
# - Helm package manager
# - Eclipse Cloud2Edge (Ditto + Hono)
# - Environment configuration for Docker Compose services
# ============================================================================

set -e

print_info() {
    echo -e "[INFO] $1"
}

print_error() {
    echo -e "[ERROR] $1"
}

print_warning() {
    echo -e "[WARNING] $1"
}

print_step() {
    echo -e "[STEP] $1"
}


# Check if running from correct directory
if [ ! -f "requirements.txt" ]; then
    print_error "This script must be run from the project root directory"
    exit 1
fi

# STEP 1: Install K3s
print_step "1/10 - Checking K3s installation..."
if command -v k3s >/dev/null 2>&1; then
    print_info "K3s is already installed"
    if ! sudo systemctl is-active --quiet k3s; then
        print_info "Starting K3s..."
        sudo systemctl start k3s
        sleep 10
    fi
else
    print_info "Installing K3s..."
    curl -sfL https://get.k3s.io | sudo sh -s - --write-kubeconfig-mode 644
    print_info "K3s installed successfully"
    sleep 10
fi

# Set KUBECONFIG
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml

# Wait for k3s to be ready
print_info "Waiting for K3s to become ready..."
for i in {1..30}; do
    if sudo kubectl cluster-info >/dev/null 2>&1; then
        print_info "K3s is ready!"
        break
    fi
    echo "Waiting... ($i/30)"
    sleep 2
done

# STEP 2: Install Helm
print_step "2/10 - Checking Helm installation..."

if command -v helm >/dev/null 2>&1; then
    print_info "Helm is already installed ($(helm version --short))"
else
    print_info "Installing Helm..."
    curl -fsSL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash
    print_info "Helm installed successfully"
fi

# STEP 3: Create Kubernetes namespace
print_step "3/10 - Creating namespace cloud2edge..."

sudo kubectl create namespace cloud2edge --dry-run=client -o yaml | sudo kubectl apply -f -

# STEP 4: Clone repositories
print_step "4/10 - Cloning required repositories..."

if [ ! -d "cloud2edge-repo" ]; then
    print_info "Cloning Eclipse packages repository..."
    git clone https://github.com/eclipse/packages.git cloud2edge-repo
else
    print_info "cloud2edge-repo already exists, skipping clone"
fi

# STEP 5: Update Cloud2Edge versions
print_step "5/10 - Updating Cloud2Edge Chart versions..."

# Update Chart.yaml to use latest versions
sed -i 's/version: ~2\.6\.[0-9]*/version: ~2.6.6/' cloud2edge-repo/packages/cloud2edge/Chart.yaml
sed -i 's/version: ~3\.[0-9]*\.[0-9]*/version: ~3.8.9/' cloud2edge-repo/packages/cloud2edge/Chart.yaml

print_info "Updated Hono to 2.6.6 and Ditto to 3.8.9"

print_info "Updating Helm dependencies..."
cd cloud2edge-repo/packages/cloud2edge
helm dependency update
cd ../../..

# STEP 6: Create custom values file
print_step "6/10 - Creating custom values file..."

cat > cloud2edge-custom-values.yaml << 'EOF'
# Custom values to increase memory for all Ditto and Hono services to avoid OOMKilled
ditto:
  thingsSearch:
    resources:
      cpu: 0.2
      memoryMi: 1536
  gateway:
    resources:
      cpu: 0.2
      memoryMi: 1024
  policies:
    resources:
      cpu: 0.2
      memoryMi: 1024
  things:
    resources:
      cpu: 0.2
      memoryMi: 1024
  connectivity:
    resources:
      cpu: 0.2
      memoryMi: 1024

# Hono services also need more memory
hono:
  authServer:
    resources:
      requests:
        cpu: "200m"
        memory: "512Mi"
      limits:
        cpu: "1"
        memory: "512Mi"
  deviceRegistryExample:
    mongoDBBasedDeviceRegistry:
      resources:
        requests:
          cpu: 200m
          memory: "512Mi"
        limits:
          cpu: 500m
          memory: "768Mi"
  adapters:
    amqp:
      resources:
        requests:
          cpu: 200m
          memory: "512Mi"
        limits:
          cpu: 500m
          memory: "768Mi"
    http:
      resources:
        requests:
          cpu: 200m
          memory: "512Mi"
        limits:
          cpu: 500m
          memory: "768Mi"
    mqtt:
      resources:
        requests:
          cpu: 200m
          memory: "512Mi"
        limits:
          cpu: 500m
          memory: "768Mi"
EOF

# STEP 7: Deploy Cloud2Edge
print_step "7/10 - Deploying Cloud2Edge with Helm..."

if sudo KUBECONFIG=/etc/rancher/k3s/k3s.yaml helm list -n cloud2edge | grep -q c2e; then
    print_info "Cloud2Edge already installed, upgrading..."
    sudo KUBECONFIG=/etc/rancher/k3s/k3s.yaml helm upgrade -n cloud2edge c2e \
        cloud2edge-repo/packages/cloud2edge/ \
        -f cloud2edge-custom-values.yaml \
        --timeout 15m
else
    print_info "Installing Cloud2Edge for the first time..."
    sudo KUBECONFIG=/etc/rancher/k3s/k3s.yaml helm install -n cloud2edge c2e \
        cloud2edge-repo/packages/cloud2edge/ \
        -f cloud2edge-custom-values.yaml \
        --timeout 15m
fi

# STEP 8: Wait for pods and create .env file
print_step "8/10 - Waiting for all pods to be ready..."

MAX_WAIT=300
ELAPSED=0
while true; do
    NOT_READY=$(sudo kubectl get pods -n cloud2edge --no-headers 2>/dev/null | grep -vE 'Running|Completed' | wc -l)
    TOTAL=$(sudo kubectl get pods -n cloud2edge --no-headers 2>/dev/null | wc -l)
    RUNNING=$(sudo kubectl get pods -n cloud2edge --no-headers 2>/dev/null | grep -E 'Running|Completed' | wc -l)

    if [ "$NOT_READY" -eq 0 ] && [ "$TOTAL" -gt 0 ]; then
        print_info "All $RUNNING pods are Running or Completed!"
        break
    else
        echo "Status: $RUNNING/$TOTAL pods ready (waiting for $NOT_READY)..."
        sleep 10
        ELAPSED=$((ELAPSED + 10))

        if [ $ELAPSED -ge $MAX_WAIT ]; then
            print_error "Timeout waiting for pods to start"
            sudo kubectl get pods -n cloud2edge
            exit 1
        fi
    fi
done

# Give pods extra time to fully stabilize
print_info "Giving pods time to fully stabilize..."
sleep 20

print_info "Verifying deployment..."
sudo kubectl get pods -n cloud2edge

# STEP 9: Creating .env file
print_step "9/10 - Creating .env file for Docker Compose..."
# Get NodePorts for services
print_info "Detecting service ports..."

DITTO_PORT=$(sudo kubectl get svc -n cloud2edge c2e-ditto-nginx -o jsonpath='{.spec.ports[0].nodePort}' 2>/dev/null || echo "N/A")
HONO_HTTP_PORT=$(sudo kubectl get svc -n cloud2edge c2e-hono-adapter-http -o jsonpath='{.spec.ports[0].nodePort}' 2>/dev/null || echo "N/A")
HONO_MQTT_PORT=$(sudo kubectl get svc -n cloud2edge c2e-hono-adapter-mqtt -o jsonpath='{.spec.ports[0].nodePort}' 2>/dev/null || echo "N/A")
HONO_AMQP_PORT=$(sudo kubectl get svc -n cloud2edge c2e-hono-adapter-amqp -o jsonpath='{.spec.ports[0].nodePort}' 2>/dev/null || echo "N/A")
HONO_REG_PORT=$(sudo kubectl get svc -n cloud2edge c2e-hono-service-device-registry-ext -o jsonpath='{.spec.ports[0].nodePort}' 2>/dev/null || echo "N/A")

# Get the certificate file path
CERT_PATH="$(pwd)/cloud2edge-repo/charts/hono/example/certs/trusted-certs.pem"

# Get host IP (exclude docker and kubernetes interfaces)
HOST_IP=$(ip -4 addr show | grep -oP '(?<=inet\s)\d+(\.\d+){3}' | grep -v '127.0.0.1' | grep -v '172\.' | grep -v '10\.42\.' | head -1)

# Fallback to localhost if no IP found
if [ -z "$HOST_IP" ]; then
    print_warning "Could not detect host IP, using localhost"
    HOST_IP="localhost"
else
    print_info "Using host IP: $HOST_IP"
fi

# Create .env file
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

print_info ".env file created successfully"

# Verify Ditto accessibility
print_info "Verifying Ditto gateway accessibility..."
for i in {1..30}; do
    if curl -s -u ditto:ditto http://localhost:$DITTO_PORT/api/2/things >/dev/null 2>&1; then
        print_info "Ditto gateway is accessible!"
        break
    fi
    if [ $i -eq 30 ]; then
        print_warning "Gateway not accessible yet, but continuing..."
    else
        echo "Waiting for gateway... ($i/30)"
        sleep 2
    fi
done

# STEP 10: Clean up
print_step "10/10 - Cleaning up no longer needed files"
rm -rf ditto-helm-chart
rm -rf cloud2edge*
print_info "No longer needed files removed"

print_info "CONGRATS!! Cloud2Edge has been deploy in this machine!!"
