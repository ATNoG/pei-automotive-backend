import httpx
import jwt
from jwt import InvalidTokenError
from jwt.algorithms import RSAAlgorithm
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from core.config import settings
import json
from uuid import UUID

bearer_scheme = HTTPBearer()

# Cache das chaves públicas do Keycloak
_jwks_cache: dict | None = None

async def _get_jwks() -> dict:
    global _jwks_cache
    if _jwks_cache is None:
        async with httpx.AsyncClient() as client:
            resp = await client.get(settings.keycloak_jwks_url)
            resp.raise_for_status()
            _jwks_cache = resp.json()
    return _jwks_cache

async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> dict:
    token = credentials.credentials
    try:
        headers = jwt.get_unverified_header(token)
        kid = headers.get("kid")
        if kid is None:
            raise InvalidTokenError("Token is missing 'kid' header")

        jwks = await _get_jwks()
        matching_key = next(
            (jwk for jwk in jwks.get("keys", []) if jwk.get("kid") == kid),
            None,
        )
        if matching_key is None:
            raise InvalidTokenError("Unable to find a matching JWK for token")

        public_key = RSAAlgorithm.from_jwk(json.dumps(matching_key))
        payload = jwt.decode(
            token,
            public_key,
            algorithms=["RS256"],
            options={"verify_aud": False}       # ATENÇÃO: Audience não está configurada, não há validação do issuer
        )

        email = payload.get("email") or ""
        username = payload.get("preferred_username") or email or payload["sub"]
        return {
            "id": payload["sub"],              # Keycloak user UUID
            "email": email,
            "username": username,
            "roles": payload.get("realm_access", {}).get("roles", []),
        }
    except InvalidTokenError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {e}"
        )
    except KeyError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Token missing required claim: {e}"
        )


async def ensure_user_exists(conn, user_id: UUID, email: str, username: str):
    await conn.execute(
        """
        INSERT INTO users (id, username, email)
        VALUES ($1, $2, $3)
        ON CONFLICT (id) DO UPDATE
        SET username = EXCLUDED.username,
            email = EXCLUDED.email
        """,
        user_id,
        username,
        email,
    )