#!/usr/bin/env python3
import argparse
import json
import os
import re
import secrets
import sys
from pathlib import Path
from dotenv import load_dotenv

import requests
import urllib3
import certifi

urllib3.disable_warnings()

load_dotenv()
DITTO_API = os.getenv("DITTO_API_URL")
DITTO_AUTH = (os.getenv("DITTO_USER"), os.getenv("DITTO_PASS"))
HONO_API = os.getenv("HONO_API_URL")
HONO_AUTH = (os.getenv("HONO_USER"), os.getenv("HONO_PASS"))
HONO_TENANT = os.getenv("HONO_TENANT")

# Use CERT from env if it exists and is accessible, otherwise use certifi
_CERT_ENV = os.getenv("CERT")
DEFAULT_CERT = _CERT_ENV if _CERT_ENV and Path(_CERT_ENV).exists() else certifi.where()
REGISTRY_DIR = (Path(__file__).resolve().parent / "devices").resolve()


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9\-]", "-", name.lower())


def ensure(resp, ok=(200, 201, 202, 204, 409)):
    if resp.status_code not in ok:
        raise SystemExit(f"{resp.request.method} {resp.url} -> {resp.status_code}: {resp.text}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Provision Ditto + Hono for a car.")
    parser.add_argument("car_name")
    parser.add_argument("--password", help="Optional device password")
    parser.add_argument("--cert", default=DEFAULT_CERT, help="CA certificate path hint to store alongside metadata")
    args = parser.parse_args()

    REGISTRY_DIR.mkdir(parents=True, exist_ok=True)

    car_slug = slugify(args.car_name)
    if not car_slug:
        sys.exit("Invalid car name.")
    meta_path = REGISTRY_DIR / f"{car_slug}.json"
    if meta_path.exists():
        sys.exit(f"Metadata for '{car_slug}' already exists: {meta_path}")

    thing_id = f"org.acme:{car_slug}"
    auth_id = f"{car_slug}-auth"
    password = args.password or secrets.token_urlsafe(12)

    policy_payload = {
        "policyId": thing_id,
        "entries": {
            "DEFAULT": {
                "subjects": {"nginx:ditto": {"type": "generated"}},
                "resources": {
                    "thing:/": {"grant": ["READ", "WRITE"], "revoke": []},
                    "policy:/": {"grant": ["READ", "WRITE"], "revoke": []},
                    "message:/": {"grant": ["READ", "WRITE"], "revoke": []},
                },
                "importable": "implicit",
            },
            "HONO": { # tenant level -> tiago suggestion
                "subjects": {
                    f"pre-authenticated:hono-connection-{HONO_TENANT}": {
                        "type": "Connection to Eclipse Hono"
                    }
                },
                "resources": {
                    "thing:/": {"grant": ["READ", "WRITE"], "revoke": []},
                    "message:/": {"grant": ["READ", "WRITE"], "revoke": []},
                },
            },
        },
    }

    ensure(
        requests.put(
            f"{DITTO_API}/api/2/policies/{thing_id}",
            auth=DITTO_AUTH,
            json=policy_payload,
            timeout=10,
        )
    )

    thing_payload = {
        "policyId": thing_id,
        "features": {"gps": {"properties": {"latitude": 0, "longitude": 0}}},
    }
    ensure(
        requests.put(
            f"{DITTO_API}/api/2/things/{thing_id}",
            auth=DITTO_AUTH,
            json=thing_payload,
            timeout=10,
        )
    )

    ensure(
        requests.post(
            f"{HONO_API}/v1/devices/{HONO_TENANT}/{thing_id}",
            auth=HONO_AUTH,
            json={"enabled": True},
            timeout=10,
            verify=False,
        )
    )

    cred_payload = [
        {
            "type": "hashed-password",
            "auth-id": auth_id,
            "secrets": [{"hash-function": "sha-256", "pwd-plain": password}],
        }
    ]
    ensure(
        requests.put(
            f"{HONO_API}/v1/credentials/{HONO_TENANT}/{thing_id}",
            auth=HONO_AUTH,
            json=cred_payload,
            timeout=10,
            verify=False,
        )
    )

    meta = {
        "car": car_slug,
        "thing_id": thing_id,
        "hono_tenant": HONO_TENANT,
        "auth_id": auth_id,
        "password": password,
        "ca_cert": args.cert,
    }
    meta_path.write_text(json.dumps(meta, indent=2))
    print(f"Provisioned {thing_id}. Metadata stored at {meta_path}")
    ditto_check = requests.get(
        f"{DITTO_API}/api/2/things/{thing_id}",
        auth=DITTO_AUTH,
        timeout=10,
    )
    ensure(ditto_check)
    hono_check = requests.get(
        f"{HONO_API}/v1/devices/{HONO_TENANT}/{thing_id}",
        auth=HONO_AUTH,
        timeout=10,
        verify=False,
    )
    ensure(hono_check)
    print("Verification succeeded.")


if __name__ == "__main__":
    main()