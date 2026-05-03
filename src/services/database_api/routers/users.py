from fastapi import APIRouter, Depends, HTTPException, status
from core.database import get_connection
from middleware.auth import get_current_user
from core.config import settings
import httpx
from uuid import UUID

router = APIRouter(prefix="/api/users", tags=["Users"])

async def get_keycloak_admin_token() -> str:
    """Retrieve an admin token from Keycloak using credentials from settings."""
    url = f"{settings.keycloak_url}/realms/master/protocol/openid-connect/token"
    payload = {
        "client_id": "admin-cli",
        "username": settings.keycloak_admin,
        "password": settings.keycloak_admin_password,
        "grant_type": "password"
    }
    
    async with httpx.AsyncClient() as client:
        response = await client.post(url, data=payload)
        if response.status_code != 200:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to authenticate with Keycloak Admin: {response.text}"
            )
        return response.json()["access_token"]


@router.delete("/me", status_code=status.HTTP_204_NO_CONTENT)
async def delete_current_user(current_user: dict = Depends(get_current_user)):
    """
    Deletes the current user's account entirely.
    This will purge all data from the database (via cascade) and delete the user from Keycloak.
    """
    user_id = current_user["id"]
    
    # delete from PostgreSQL Database
    async with get_connection() as conn:
        await conn.execute("DELETE FROM users WHERE id = $1", UUID(user_id))
        
    # delete from Keycloak
    try:
        admin_token = await get_keycloak_admin_token()
        kc_users_url = f"{settings.keycloak_url}/admin/realms/{settings.keycloak_realm}/users/{user_id}"
        
        async with httpx.AsyncClient() as client:
            resp = await client.delete(
                kc_users_url,
                headers={"Authorization": f"Bearer {admin_token}"}
            )
            
            if resp.status_code not in (200, 204, 404): # 404 - already deleted
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
                    detail=f"Failed to delete user from Keycloak: {resp.text}"
                )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error deleting user from Keycloak: {str(e)}"
        )
