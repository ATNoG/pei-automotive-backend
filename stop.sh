#!/usr/bin/env bash
set -e

# Set KUBECONFIG
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml

NAMESPACE="cloud2edge"
RELEASE="c2e"

echo "Stopping and cleaning Eclipse Ditto/Hono..."

if helm status -n "$NAMESPACE" "$RELEASE" >/dev/null 2>&1; then
    echo "Removing Helm release '$RELEASE'..."
    helm uninstall -n "$NAMESPACE" "$RELEASE"
else
    echo "Helm release '$RELEASE' não encontrado."
fi

if kubectl get namespace "$NAMESPACE" >/dev/null 2>&1; then
    echo "Deleting namespace '$NAMESPACE'..."
    kubectl delete namespace "$NAMESPACE" --grace-period=0 --force
else
    echo "Namespace '$NAMESPACE' já não existe."
fi

echo "Stopping k3s..."
sudo systemctl stop k3s || true
