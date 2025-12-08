#!/bin/bash

# Ensure environment variables are set
if [ -z "$REGISTRY_BASE_URL" ] || [ -z "$DITTO_API_BASE_URL" ]; then
  echo "Environment variables are not set. Please run the set_hono_ditto_env.sh script first."
  exit 1
fi

HONO_TENANT=my-tenant
RELEASE=c2e
NS=cloud2edge
DITTO_DEVOPS_PWD=$(kubectl --namespace ${NS} get secret ${RELEASE}-ditto-gateway-secret -o jsonpath="{.data.devops-password}" | base64 --decode)

curl -i -X PUT -u devops:${DITTO_DEVOPS_PWD} -H 'Content-Type: application/json' --data '{
  "name": "[Hono/AMQP1.0] '"${HONO_TENANT}"'",
  "connectionType": "amqp-10",
  "connectionStatus": "open",
  "uri": "amqp://consumer%40HONO:verysecret@'"${RELEASE}"'-hono-dispatch-router-ext:15672",
  "failoverEnabled": true,
  "sources": [
    {
      "addresses": [
        "telemetry/'"${HONO_TENANT}"'",
        "event/'"${HONO_TENANT}"'"
      ],
      "authorizationContext": [
        "pre-authenticated:hono-connection-'"${HONO_TENANT}"'"
      ],
      "enforcement": {
        "input": "{{ header:device_id }}",
        "filters": [
          "{{ entity:id }}"
        ]
      },
      "headerMapping": {
        "hono-device-id": "{{ header:device_id }}",
        "content-type": "{{ header:content-type }}"
      },
      "replyTarget": {
        "enabled": true,
        "address": "{{ header:reply-to }}",
        "headerMapping": {
          "to": "command/'"${HONO_TENANT}"'/{{ header:hono-device-id }}",
          "subject": "{{ header:subject | fn:default(topic:action-subject) | fn:default(topic:criterion) }}-response",
          "correlation-id": "{{ header:correlation-id }}",
          "content-type": "{{ header:content-type | fn:default('"'"'application/vnd.eclipse.ditto+json'"'"') }}"
        },
        "expectedResponseTypes": [
          "response",
          "error"
        ]
      },
      "acknowledgementRequests": {
        "includes": [],
        "filter": "fn:filter(header:qos,'"'"'ne'"'"','"'"'0'"'"')"
      }
    },
    {
      "addresses": [
        "command_response/'"${HONO_TENANT}"'/replies"
      ],
      "authorizationContext": [
        "pre-authenticated:hono-connection-'"${HONO_TENANT}"'"
      ],
      "headerMapping": {
        "content-type": "{{ header:content-type }}",
        "correlation-id": "{{ header:correlation-id }}",
        "status": "{{ header:status }}"
      },
      "replyTarget": {
        "enabled": false,
        "expectedResponseTypes": [
          "response",
          "error"
        ]
      }
    }
  ],
  "targets": [
    {
      "address": "command/'"${HONO_TENANT}"'",
      "authorizationContext": [
        "pre-authenticated:hono-connection-'"${HONO_TENANT}"'"
      ],
      "topics": [
        "_/_/things/live/commands",
        "_/_/things/live/messages"
      ],
      "headerMapping": {
        "to": "command/'"${HONO_TENANT}"'/{{ thing:id }}",
        "subject": "{{ header:subject | fn:default(topic:action-subject) }}",
        "content-type": "{{ header:content-type | fn:default('"'"'application/vnd.eclipse.ditto+json'"'"') }}",
        "correlation-id": "{{ header:correlation-id }}",
        "reply-to": "{{ fn:default('"'"'command_response/'"${HONO_TENANT}"'/replies'"'"') | fn:filter(header:response-required,'"'"'ne'"'"','"'"'false'"'"') }}"
      }
    },
    {
      "address": "command/'"${HONO_TENANT}"'",
      "authorizationContext": [
        "pre-authenticated:hono-connection-'"${HONO_TENANT}"'"
      ],
      "topics": [
        "_/_/things/twin/events",
        "_/_/things/live/events"
      ],
      "headerMapping": {
        "to": "command/'"${HONO_TENANT}"'/{{ thing:id }}",
        "subject": "{{ header:subject | fn:default(topic:action-subject) }}",
        "content-type": "{{ header:content-type | fn:default('"'"'application/vnd.eclipse.ditto+json'"'"') }}",
        "correlation-id": "{{ header:correlation-id }}"
      }
    }
  ]
}' ${DITTO_API_BASE_URL:?}/api/2/connections/hono-amqp-connection-for-${HONO_TENANT//./_}