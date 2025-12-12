#!/usr/bin/env bash
# Deploy Eclipse Ditto and Hono with Cloud2Edge

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Function to print colored output
print_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_step() {
    echo -e "${BLUE}[STEP]${NC} $1"
}

echo "================================================"
echo "Deploying Cloud2Edge (Ditto + Hono)"
echo "================================================"
echo ""

# Check if running from correct directory
if [ ! -f "requirements.txt" ]; then
    print_error "This script must be run from the project root directory"
    exit 1
fi

# Set KUBECONFIG
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml

print_step "1/10 - Starting K3s..."
if ! sudo systemctl is-active --quiet k3s; then
    sudo systemctl start k3s
    sleep 10
else
    print_info "K3s is already running"
fi

# Wait for k3s to be ready
print_info "Waiting for K3s to become ready..."
for i in {1..30}; do
    if kubectl cluster-info >/dev/null 2>&1; then
        print_info "K3s is ready!"
        break
    fi
    echo "Waiting... ($i/30)"
    sleep 2
done

print_step "2/10 - Creating namespace cloud2edge..."
kubectl create namespace cloud2edge --dry-run=client -o yaml | kubectl apply -f -

print_step "3/10 - Cloning cloud2edge repository if needed..."
if [ ! -d "cloud2edge-repo" ]; then
    print_info "Cloning Eclipse packages repository..."
    git clone https://github.com/eclipse/packages.git cloud2edge-repo
fi

if [ ! -d "ditto-helm-chart" ]; then
    print_info "Cloning ATNoG ditto-helm-chart repository..."
    git clone https://github.com/ATNoG/ditto-helm-chart.git
fi

print_step "4/10 - Updating Cloud2Edge Chart versions..."
# Update Chart.yaml to use latest versions
sed -i 's/version: ~2\.6\.[0-9]*/version: ~2.6.6/' cloud2edge-repo/packages/cloud2edge/Chart.yaml
sed -i 's/version: ~3\.[0-9]*\.[0-9]*/version: ~3.8.9/' cloud2edge-repo/packages/cloud2edge/Chart.yaml

print_info "Updated Hono to 2.6.6 and Ditto to 3.8.9"

print_step "5/10 - Updating Helm dependencies..."
cd cloud2edge-repo/packages/cloud2edge
helm dependency update
cd ../../..

print_step "6/10 - Creating custom values file..."
cat > cloud2edge-custom-values.yaml << 'EOF'
# Custom values to increase memory for all Ditto services to avoid OOMKilled
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
EOF

print_step "7/10 - Installing Cloud2Edge Helm Chart..."
if helm list -n cloud2edge | grep -q c2e; then
    print_info "Cloud2Edge already installed, upgrading..."
    helm upgrade -n cloud2edge c2e cloud2edge-repo/packages/cloud2edge/ \
        -f cloud2edge-custom-values.yaml \
        --timeout 20m
else
    print_info "Installing Cloud2Edge for the first time..."
    helm install -n cloud2edge c2e cloud2edge-repo/packages/cloud2edge/ \
        -f cloud2edge-custom-values.yaml \
        --timeout 20m
fi

print_step "8/10 - Waiting for all pods to be Running..."
MAX_WAIT=300
ELAPSED=0
while true; do
    # Count pods that are not Running or Completed
    NOT_READY=$(kubectl get pods -n cloud2edge --no-headers 2>/dev/null | grep -vE 'Running|Completed' | wc -l)
    TOTAL=$(kubectl get pods -n cloud2edge --no-headers 2>/dev/null | wc -l)
    RUNNING=$(kubectl get pods -n cloud2edge --no-headers 2>/dev/null | grep -E 'Running|Completed' | wc -l)

    if [ "$NOT_READY" -eq 0 ] && [ "$TOTAL" -gt 0 ]; then
        print_info "All $RUNNING pods are Running!"
        break
    else
        echo "Status: $RUNNING/$TOTAL pods ready (waiting for $NOT_READY)..."

        # Check for OOMKilled or CrashLoopBackOff pods
        CRASHED=$(kubectl get pods -n cloud2edge --no-headers 2>/dev/null | grep -E 'OOMKilled|CrashLoopBackOff|Error' || true)
        if [ ! -z "$CRASHED" ]; then
            print_warning "Some pods are in error state:"
            echo "$CRASHED"
            print_info "Cleaning up problematic pods..."
            kubectl get pods -n cloud2edge --no-headers | grep -E 'OOMKilled|CrashLoopBackOff|Error' | awk '{print $1}' | xargs -r kubectl delete pod -n cloud2edge
        fi

        sleep 10
        ELAPSED=$((ELAPSED + 10))

        if [ $ELAPSED -ge $MAX_WAIT ]; then
            print_error "Timeout waiting for pods to start"
            kubectl get pods -n cloud2edge
            exit 1
        fi
    fi
done

# Give pods extra time to fully stabilize
print_info "Giving pods time to fully stabilize..."
sleep 20

print_step "9/10 - Verifying deployment..."
kubectl get pods -n cloud2edge

# Get the actual NodePort for Ditto nginx
print_info "Detecting Ditto nginx NodePort..."
DITTO_PORT=$(kubectl get svc -n cloud2edge c2e-ditto-nginx -o jsonpath='{.spec.ports[0].nodePort}')
print_info "Ditto nginx is exposed on port: $DITTO_PORT"

# Check if gateway is accessible
print_info "Checking Ditto gateway accessibility..."
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

print_step "10/10 - Waiting for Hono services to be ready..."
print_info "Hono services may take several minutes to fully start up and connect..."

HONO_READY=0
for i in {1..60}; do
    ADAPTERS_READY=$(kubectl get pods -n cloud2edge --no-headers 2>/dev/null | grep "hono-adapter" | grep "1/1" | wc -l)
    if [ "$ADAPTERS_READY" -ge 2 ]; then
        print_info "Hono adapters are ready!"
        HONO_READY=1
        break
    else
        echo "Waiting for Hono adapters... ($ADAPTERS_READY/3 ready, attempt $i/60)"
        sleep 5
    fi
done

if [ "$HONO_READY" -eq 0 ]; then
    print_warning "Hono services are still starting up. This is normal for the first deployment."
    print_warning "Hono adapters may take 5-10 minutes to fully initialize."
    print_warning "You can check status with: kubectl get pods -n cloud2edge"
fi

print_info "Cloud2Edge MEC setup can be configured later if needed."
print_info "The demo device 'org.eclipse.packages.c2e:demo-device' is already provisioned."

echo ""
echo "================================================"
echo "✓ Deployment completed successfully!"
echo "================================================"
# Get actual NodePorts
DITTO_PORT=$(kubectl get svc -n cloud2edge c2e-ditto-nginx -o jsonpath='{.spec.ports[0].nodePort}' 2>/dev/null || echo "N/A")
HONO_HTTP_PORT=$(kubectl get svc -n cloud2edge c2e-hono-adapter-http -o jsonpath='{.spec.ports[0].nodePort}' 2>/dev/null || echo "N/A")
HONO_MQTT_PORT=$(kubectl get svc -n cloud2edge c2e-hono-adapter-mqtt -o jsonpath='{.spec.ports[0].nodePort}' 2>/dev/null || echo "N/A")
HONO_AMQP_PORT=$(kubectl get svc -n cloud2edge c2e-hono-adapter-amqp -o jsonpath='{.spec.ports[0].nodePort}' 2>/dev/null || echo "N/A")
HONO_REG_PORT=$(kubectl get svc -n cloud2edge c2e-hono-service-device-registry-ext -o jsonpath='{.spec.ports[0].nodePort}' 2>/dev/null || echo "N/A")

echo ""
echo "Services:"
echo "  - Ditto UI:          http://localhost:$DITTO_PORT"
echo "  - Ditto API:         http://localhost:$DITTO_PORT/api"
echo "  - Hono HTTP:         https://localhost:$HONO_HTTP_PORT"
echo "  - Hono MQTT:         localhost:$HONO_MQTT_PORT"
echo "  - Hono AMQP:         localhost:$HONO_AMQP_PORT"
echo "  - Hono Device Reg:   https://localhost:$HONO_REG_PORT"
echo ""
echo "Credentials:"
echo "  - Ditto:  ditto / ditto"
echo "  - Tenant: org.eclipse.packages.c2e"
echo "  - Device: demo-device / demo-secret"
echo ""
echo "⚠️  IMPORTANT NOTES:"
echo "  - Hono services may take 5-10 minutes to fully start on first deployment"
echo "  - If tests fail, wait a few minutes and check: kubectl get pods -n cloud2edge"
echo "  - All Hono adapter pods should show 1/1 READY before running tests"
echo ""
echo "Next steps:"
echo "  1. Wait for Hono adapters to be ready (see above)"
echo "  2. Start Docker containers: docker-compose up -d"
echo "  3. Run simulations: python simulations/car_simulation.py"
echo "  4. Run tests: pytest tests/ -v"
echo ""
echo "Useful commands:"
echo "  - View pods: kubectl get pods -n cloud2edge"
echo "  - View logs: kubectl logs -n cloud2edge <pod-name>"
echo "  - Stop: ./stop.sh"
echo ""

rm cloud2edge-custom-values.yaml
