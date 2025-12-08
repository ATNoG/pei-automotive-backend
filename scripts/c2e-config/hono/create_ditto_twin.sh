#!/bin/bash

# Ensure environment variables are set
if [ -z "$DITTO_API_BASE_URL" ]; then
  echo "Environment variables are not set. Please run the set_hono_ditto_env.sh script first."
  exit 1
fi


curl -i -X PUT -u ditto:ditto -H 'Content-Type: application/json' --data '{
  "policyId": "org.acme:my-policy",
  "features": {
    "ModemStatus": {
      "properties": {
        "referenceTime": 3222087952,
        "referencePosition": {
          "latitude": 406331850,
          "longitude": -86811669,
          "positionConfidenceEllipse": {
            "semiMajorConfidence": 4095,
            "semiMinorConfidence": 4095,
            "semiMajorOrientation": 900
          },
          "altitude": {
            "altitudeValue": 0,
            "altitudeConfidence": "unavailable"
          }
        },
        "modemStatus": {
          "mcc": 3,
          "mnc": 268,
          "ratMode": 8,
          "nr": {
            "rsrq": -32768,
            "rsrp": -32768,
            "snr": -32768,
            "pci": 0
          },
          "lte": {
            "rsrq": -109,
            "rsrp": -15,
            "rssi": -76,
            "snr": 78,
            "pci": 297
          }
        }
      }
    },
    "MessageDelay": {
      "properties": {
        "referenceTime": 664219746700,
        "delay": 4
      }
    },
    "NetworkThroughput": {
      "properties": {
        "referenceTime": 664216448665,
        "rx_bytes": 0,
        "tx_bytes": 0
      }
    }
  }
}' ${DITTO_API_BASE_URL:?}/api/2/things/org.acme:my-device-1