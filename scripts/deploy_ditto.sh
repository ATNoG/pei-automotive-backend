#!/usr/bin/env bash
# Deploy Eclipse Ditto
set -e

is_wsl() {
    if grep -qEi "(Microsoft|WSL)" /proc/version &> /dev/null; then
        return 0
    else
        return 1
    fi
}

echo "Detected environment check..."
if is_wsl; then
    echo "WSL detected automatically."
    USE_K3D=true
else
    echo "Native Linux detected."
    USE_K3D=false
fi

echo "Which Kubernetes setup do you want to use?"
echo "1) K3s (native Linux - systemd)"
echo "2) K3d (WSL2 or Docker-based)"
read -p "Enter your choice (1 or 2): " choice

case $choice in
    1)
        USE_K3D=false
        ;;
    2)
        USE_K3D=true
        ;;
    *)
        echo "Invalid choice. Defaulting based on environment detection."
        ;;
esac

# Initialize Kubernetes based on choice
if [ "$USE_K3D" = true ]; then
    echo "Initializing K3d cluster..."
    # Check if cluster exists, if not create it
    if ! k3d cluster list | grep -q mycluster; then
        echo "Creating K3d cluster..."
        k3d cluster create mycluster \
  	--api-port 6443 \
  	-p "8080:8080@loadbalancer" \
  	-p "8443:8443@loadbalancer" \
  	-p "5671:5671@loadbalancer" \
  	-p "8883:8883@loadbalancer" \
  	-p "15671:15671@loadbalancer" \
  	-p "15672:15672@loadbalancer" \
  	-p "28443:28443@loadbalancer"
    else
        echo "Starting existing K3d cluster..."
        k3d cluster start mycluster || echo "Cluster already running"
    fi
    
    # Wait for cluster to be ready
    echo "Waiting for cluster to be ready..."
    sleep 5
else
    echo "Initializing K3s..."
    sudo systemctl start k3s
    
    # Wait for K3s to be ready
    echo "Waiting for K3s to be ready..."
    sleep 10
fi

echo "Creating namespace cloud2edge"
kubectl create namespace cloud2edge --dry-run=client -o yaml | kubectl apply -f -

echo "Instaling Helm Chart..."
helm install -n cloud2edge -f valuesTest.yaml --timeout 10m --wait c2e https://atnog.github.io/ditto-helm-chart/cloud2edge || \
helm upgrade -n cloud2edge -f valuesTest.yaml c2e https://atnog.github.io/ditto-helm-chart/cloud2edge

echo "Waiting for all pods to be Running..."
while true; do
    NOT_READY=$(kubectl get pods -n cloud2edge --no-headers 2>/dev/null | grep -vE 'Running|Completed' | wc -l)
    if [ "$NOT_READY" -eq 0 ]; then
        echo "All pods are Running!"
        break
    else
        echo "$NOT_READY pods are still not ready..."
        sleep 20
    fi
done

echo "System ready!"
