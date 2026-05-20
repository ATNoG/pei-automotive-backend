# ModemStatus Ditto Message Structure

This is the payload sent to Ditto when a vehicle reports its position and cellular modem state.
The message targets the path `/features/ModemStatus/properties` on the vehicle's digital twin.

---

## Top-level fields

| Field | Type | Description |
|---|---|---|
| `referenceTime` | ISO 8601 string | UTC timestamp of when the measurement was taken |
| `referencePosition` | object | Geographic position of the vehicle at `referenceTime` |
| `modemStatus` | object | Cellular network signal and identity information |

---

## `referencePosition`

The vehicle's location and associated accuracy metadata.

| Field | Type | Description |
|---|---|---|
| `latitude` | float | WGS-84 latitude in decimal degrees (−90 to +90) |
| `longitude` | float | WGS-84 longitude in decimal degrees (−180 to +180) |
| `positionConfidenceEllipse` | object | Describes the uncertainty ellipse around the reported position |
| `altitude` | object | Vertical position above the WGS-84 ellipsoid |

### `positionConfidenceEllipse`

Represents the 95th-percentile error ellipse of the GPS fix.
Values follow the ETSI ITS standard (unit: 0.01 m; `4095` = unavailable).

| Field | Type | Description |
|---|---|---|
| `semiMajorConfidence` | int | Length of the ellipse's semi-major axis (accuracy along worst direction) |
| `semiMinorConfidence` | int | Length of the ellipse's semi-minor axis (accuracy along best direction) |
| `semiMajorOrientation` | int | Heading of the semi-major axis in units of 0.1° from North (0–3600; `900` = East) |

### `altitude`

| Field | Type | Description |
|---|---|---|
| `altitudeValue` | float | Altitude in metres above the WGS-84 ellipsoid |
| `altitudeConfidence` | string | Accuracy class of the altitude reading; `"unavailable"` when GPS has no fix |

---

## `modemStatus`

Cellular network identity and signal quality at the time of the report.

| Field | Type | Description |
|---|---|---|
| `mcc` | int | Mobile Country Code — identifies the country of the serving network (e.g. `268` = Portugal) |
| `mnc` | int | Mobile Network Code — identifies the operator within the country (e.g. `1` = Vodafone PT) |
| `ratMode` | string | Radio Access Technology currently in use: `"NR"` (5G), `"LTE"` (4G), etc. |
| `nr` | object | 5G NR signal measurements (populated when `ratMode` is `"NR"`) |
| `lte` | object | LTE signal measurements (populated when `ratMode` is `"LTE"`, or as secondary cell) |

### `nr` — 5G New Radio

| Field | Unit | Description |
|---|---|---|
| `rsrq` | dB | Reference Signal Received Quality — ratio of signal to total received power (higher = better) |
| `rsrp` | dBm | Reference Signal Received Power — strength of the pilot signal from the base station |
| `snr` | dB | Signal-to-Noise Ratio — margin of signal over background noise |
| `pci` | int | Physical Cell Identity — identifier of the serving 5G cell (0–1007) |

### `lte` — 4G Long-Term Evolution

| Field | Unit | Description |
|---|---|---|
| `rsrq` | dB | Reference Signal Received Quality |
| `rsrp` | dBm | Reference Signal Received Power |
| `rssi` | dBm | Received Signal Strength Indicator — total power including interference and noise |
| `snr` | dB | Signal-to-Noise Ratio |
| `pci` | int | Physical Cell Identity (0–503) |

> **Note:** `rssi` exists only in LTE because NR dropped it in favour of the more precise `rsrp`/`rsrq` pair.

---

## Testing with curl

Replace `<CAR_SLUG>` with the car name you registered (e.g. `my-car`).

---

### Old structure (pre-migration reference)

This is what the backend used to send — two flat features (`gps` and `info`) at the root of `/features`.

```bash
curl -X PUT "https://automotive-app.ddns.net/api/2/things/org.acme:car-test/features" \
  -u "ditto:ditto" \
  -H "Content-Type: application/json" \
  -d '{
    "gps": {
      "properties": {
        "latitude": 40.6331,
        "longitude": -8.6594
      }
    },
    "info": {
      "properties": {
        "emergency": false
      }
    }
  }'
```

Read it back:

```bash
curl -u "ditto:ditto" \
  "https://automotive-app.ddns.net/api/2/things/org.acme:car-test/features/gps/properties" \
  | jq
```

---

### New structure (ModemStatus)

Targets `/features/ModemStatus/properties` directly — the value is the properties object.

```bash
curl -X PUT "https://automotive-app.ddns.net/api/2/things/org.acme:car-test/features/ModemStatus/properties" \
  -u "ditto:ditto" \
  -H "Content-Type: application/json" \
  -d '{
    "referenceTime": "2026-05-20T14:32:00.000Z",
    "referencePosition": {
      "latitude": 40.6331,
      "longitude": -8.6594,
      "positionConfidenceEllipse": {
        "semiMajorConfidence": 4095,
        "semiMinorConfidence": 4095,
        "semiMajorOrientation": 900
      },
      "altitude": {
        "altitudeValue": 15.0,
        "altitudeConfidence": "unavailable"
      }
    },
    "modemStatus": {
      "mcc": 268,
      "mnc": 1,
      "ratMode": "NR",
      "nr": {
        "rsrq": -10,
        "rsrp": -85,
        "snr": 20,
        "pci": 42
      },
      "lte": {
        "rsrq": -12,
        "rsrp": -90,
        "rssi": -75,
        "snr": 15,
        "pci": 101
      }
    }
  }' | jq
```

Read it back:

```bash
curl -u "ditto:ditto" \
  "https://automotive-app.ddns.net/api/2/things/org.acme:car-test/features/ModemStatus/properties" \
  | jq
```

A `200 OK` with the body you sent means Ditto accepted and stored it. The backend WebSocket
client (`ditto_client.py`) will fire immediately after, extract `referencePosition.latitude/longitude`,
and push the update through the MQTT pipeline (`cars/raw_updates/<CAR_SLUG>`).

---

## Full example

```json
{
  "referenceTime": "2026-05-20T14:32:00.000Z",
  "referencePosition": {
    "latitude": 40.6331,
    "longitude": -8.6594,
    "positionConfidenceEllipse": {
      "semiMajorConfidence": 4095,
      "semiMinorConfidence": 4095,
      "semiMajorOrientation": 900
    },
    "altitude": {
      "altitudeValue": 15.0,
      "altitudeConfidence": "unavailable"
    }
  },
  "modemStatus": {
    "mcc": 268,
    "mnc": 1,
    "ratMode": "NR",
    "nr": {
      "rsrq": -10,
      "rsrp": -85,
      "snr": 20,
      "pci": 42
    },
    "lte": {
      "rsrq": -12,
      "rsrp": -90,
      "rssi": -75,
      "snr": 15,
      "pci": 101
    }
  }
}
```
