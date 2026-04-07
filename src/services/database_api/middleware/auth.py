import jwt
from jwt import InvalidTokenError, PyJWKClient
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from core.config import settings
from uuid import UUID

bearer_scheme = HTTPBearer()

jwks_client = PyJWKClient(settings.keycloak_jwks_url)

async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> dict:
    token = credentials.credentials
    try:
        signing_key = jwks_client.get_signing_key_from_jwt(token)
        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            options={"verify_aud": False},
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