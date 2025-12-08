#!/bin/bash

# Ensure environment variables are set
if [ -z "$REGISTRY_BASE_URL" ]; then
  echo "Environment variables are not set. Please run the set_hono_ditto_env.sh script first."
  exit 1
fi

# Register a device
curl -i -k -X POST ${REGISTRY_BASE_URL:?}/v1/devices/my-tenant/org.acme:my-device-1
