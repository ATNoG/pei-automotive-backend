#!/bin/bash

# Ensure environment variables are set
if [ -z "$REGISTRY_BASE_URL" ]; then
  echo "Environment variables are not set. Please run the set_hono_ditto_env.sh script first."
  exit 1
fi

# Update device credentials
curl -i -k -X PUT -H "Content-Type: application/json" --data '[
{
  "type": "hashed-password",
  "auth-id": "my-auth-id-1",
  "secrets": [{
    "pwd-plain": "my-password"
  }]
}]' ${REGISTRY_BASE_URL:?}/v1/credentials/my-tenant/org.acme:my-device-1
