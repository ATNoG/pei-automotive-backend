#!/usr/bin/env bash
# Deploy Eclipse Ditto
set -e

echo "Initializing K3s..."
sudo systemctl start k3s

echo "Creating namespace cloud2edge"
kubectl create namespace cloud2edge --dry-run=client -o yaml | kubectl apply -f -

echo "Instaling Helm Chart..."
helm install -n cloud2edge -f valuesTest.yaml c2e https://atnog.github.io/ditto-helm-chart/cloud2edge || \
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