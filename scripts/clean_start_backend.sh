#!/usr/bin/env bash
set -euo pipefail

print_info() {
    echo "[INFO] $1"
}

print_error() {
    echo "[ERROR] $1"
}

if [ ! -f "docker-compose.yml" ]; then
    print_error "Run this script from the project root (where docker-compose.yml exists)."
    exit 1
fi

if ! command -v docker >/dev/null 2>&1; then
    print_error "Docker is not installed or not available in PATH."
    exit 1
fi

if [ "${1:-}" != "--yes" ]; then
    echo "This will remove Docker Compose containers and named volumes for this project."
    echo "It is intended for a one-time clean reset when DB state is inconsistent."
    read -r -p "Continue? (yes/no): " answer
    if [ "$answer" != "yes" ]; then
        print_info "Aborted by user."
        exit 0
    fi
fi

print_info "Stopping stack and removing compose volumes..."
docker compose down -v --remove-orphans

print_info "Starting backend stack with fresh database initialization..."
docker compose up -d --build

print_info "Current service status:"
docker compose ps

print_info "Tip: verify API health with: curl http://localhost:${DATABASE_API_PORT:-8082}/health"
