#!/bin/bash

# Ensure environment variables are set
if [ -z "$DITTO_API_BASE_URL" ]; then
  echo "Environment variables are not set. Please run the set_hono_ditto_env.sh script first."
  exit 1
fi

HONO_TENANT=my-tenant

# Create the policy in Ditto
curl -i -X PUT -u ditto:ditto -H 'Content-Type: application/json' --data '{
  "entries": {
    "DEFAULT": {
      "subjects": {
        "{{ request:subjectId }}": {
           "type": "Ditto user authenticated via nginx"
        }
      },
      "resources": {
        "thing:/": {
          "grant": ["READ", "WRITE"],
          "revoke": []
        },
        "policy:/": {
          "grant": ["READ", "WRITE"],
          "revoke": []
        },
        "message:/": {
          "grant": ["READ", "WRITE"],
          "revoke": []
        }
      }
    },
    "HONO": {
      "subjects": {
        "pre-authenticated:hono-connection-'"${HONO_TENANT}"'": {
          "type": "Connection to Eclipse Hono"
        }
      },
      "resources": {
        "thing:/": {
          "grant": ["READ", "WRITE"],
          "revoke": []
        },
        "message:/": {
          "grant": ["READ", "WRITE"],
          "revoke": []
        }
      }
    }
  }
}' ${DITTO_API_BASE_URL:?}/api/2/policies/org.acme:my-policy