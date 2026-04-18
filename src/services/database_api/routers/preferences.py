from fastapi import APIRouter, Depends
from models.preference import PreferencesResponse, UpdatePreferencesRequest
from repositories.preference_repository import PreferenceRepository
from core.database import get_connection
from middleware.auth import get_current_user, ensure_user_exists
from uuid import UUID

router = APIRouter(prefix="/api/preferences", tags=["Preferences"])


@router.get("/", response_model=PreferencesResponse)
async def get_preferences(
    current_user: dict = Depends(get_current_user),
):
    """
    Returns the user's preferences.
    Creates with defaults on first call.
    """
    async with get_connection() as conn:
        user_id = UUID(current_user["id"])
        await ensure_user_exists(
            conn,
            user_id,
            current_user.get("email") or "",
            current_user.get("username") or "",
        )
        repo = PreferenceRepository(conn)
        return await repo.get_or_create_defaults(user_id)


@router.patch("/", response_model=PreferencesResponse)
async def update_preferences(
    body: UpdatePreferencesRequest,
    current_user: dict = Depends(get_current_user),
):
    """
    Partial update — Only needs to send the changed section.
    Example body (only updating appearance):
    { "appearance": { "dark_mode": true, "colorblind_enabled": false, "language": "en" } }
    """
    async with get_connection() as conn:
        user_id = UUID(current_user["id"])
        await ensure_user_exists(
            conn,
            user_id,
            current_user.get("email") or "",
            current_user.get("username") or "",
        )
        repo = PreferenceRepository(conn)

        # Ensure the user row exists before updating preferences
        exists = await conn.fetchval(
            "SELECT 1 FROM user_preferences WHERE user_id = $1",
            user_id,
        )
        if not exists:
            await repo.get_or_create_defaults(user_id)

        return await repo.update(user_id, body)