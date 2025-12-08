#!/usr/bin/env bash
set -e

NAMESPACE="cloud2edge"
RELEASE="c2e"

echo "Stopping and cleaning Eclipse Ditto/Hono..."

# Remover o Helm chart (se existir)
if helm status -n "$NAMESPACE" "$RELEASE" >/dev/null 2>&1; then
    echo "Removing Helm release '$RELEASE'..."
    helm uninstall -n "$NAMESPACE" "$RELEASE"
else
    echo "Helm release '$RELEASE' not found."
fi

# Apagar o namespace (força remoção de pods, PVCs, services, etc.)
if kubectl get namespace "$NAMESPACE" >/dev/null 2>&1; then
    echo "Deleting namespace '$NAMESPACE'..."
    kubectl delete namespace "$NAMESPACE" --grace-period=0 --force
else
    echo "Namespace '$NAMESPACE' doesn't exist."
fi

# Parar o servidor K3s
echo "Stopping k3s..."
sudo systemctl stop k3s || true