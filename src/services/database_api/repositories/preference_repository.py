import asyncpg
from uuid import UUID
from datetime import datetime, timezone
from models.preference import (
    PreferencesResponse,
    UpdatePreferencesRequest,
    AppearancePreferences,
    AlertPreferences,
    AccidentAlertPreferences,
    SpeedingAlertPreferences,
    WeatherAlertPreferences,
    OvertakingAlertPreferences,
    EmergencyVehicleAlertPreferences,
    NavigationAlertPreferences,
    TrafficAlertPreferences,
    ManeuverAlertPreferences,
    WeatherPreferences,
)


class PreferenceRepository:
    def __init__(self, conn: asyncpg.Connection):
        self._conn = conn

    async def get(self, user_id: UUID) -> PreferencesResponse | None:
        row = await self._conn.fetchrow(
            "SELECT * FROM user_preferences WHERE user_id = $1",
            user_id,
        )
        if row is None:
            return None
        return self._map(row)

    async def get_or_create_defaults(self, user_id: UUID) -> PreferencesResponse:
        """Returns existing prefs or inserts defaults — useful on first login."""
        existing = await self.get(user_id)
        if existing:
            return existing

        row = await self._conn.fetchrow(
            """
            INSERT INTO user_preferences (user_id)
            VALUES ($1)
            RETURNING *
            """,
            user_id,
        )
        return self._map(row)

    async def update(
        self, user_id: UUID, req: UpdatePreferencesRequest
    ) -> PreferencesResponse:
        """
        Partial update — only touches the sections the client sent.
        Builds the SET clause dynamically but safely (no string interpolation on values).
        """
        fields: dict[str, bool | datetime] = {}

        if req.appearance is not None:
            fields["dark_mode"] = req.appearance.dark_mode
            fields["colorblind_enabled"] = req.appearance.colorblind_enabled

        if req.alerts is not None:
            fields["alert_accident"] = req.alerts.accident.alert
            fields["alert_accident_audio"] = req.alerts.accident.audio
            fields["alert_speeding"] = req.alerts.speeding.alert
            fields["alert_speeding_audio"] = req.alerts.speeding.audio
            fields["alert_weather"] = req.alerts.weather.alert
            fields["alert_weather_audio"] = req.alerts.weather.audio
            fields["alert_overtaking"] = req.alerts.overtaking.alert
            fields["alert_overtaking_audio"] = req.alerts.overtaking.audio
            fields["alert_emergency_vehicle"] = req.alerts.emergency_vehicle.alert
            fields["alert_emergency_vehicle_audio"] = req.alerts.emergency_vehicle.audio
            fields["alert_navigation"] = req.alerts.navigation.alert
            fields["alert_navigation_audio"] = req.alerts.navigation.audio
            fields["alert_traffic"] = req.alerts.traffic.alert
            fields["alert_traffic_audio"] = req.alerts.traffic.audio
            fields["alert_maneuver"] = req.alerts.maneuver.alert
            fields["alert_maneuver_audio"] = req.alerts.maneuver.audio

        if req.weather is not None:
            fields["weather_feels_like"] = req.weather.feels_like
            fields["weather_wind"] = req.weather.wind
            fields["weather_humidity"] = req.weather.humidity
            fields["weather_pressure"] = req.weather.pressure
            fields["weather_visibility"] = req.weather.visibility
            fields["weather_uv_index"] = req.weather.uv_index

        if not fields:
            # Nothing to update, return current state
            return await self.get_or_create_defaults(user_id)

        fields["updated_at"] = datetime.now(timezone.utc)

        set_clause = ", ".join(
            f"{col} = ${i + 2}" for i, col in enumerate(fields.keys())
        )
        values = list(fields.values())

        row = await self._conn.fetchrow(
            f"""
            UPDATE user_preferences
            SET {set_clause}
            WHERE user_id = $1
            RETURNING *
            """,
            user_id,
            *values,
        )
        return self._map(row)

    @staticmethod
    def _map(row: asyncpg.Record) -> PreferencesResponse:
        return PreferencesResponse(
            user_id=row["user_id"],
            appearance=AppearancePreferences(
                dark_mode=row["dark_mode"],
                colorblind_enabled=row["colorblind_enabled"],
            ),
            alerts=AlertPreferences(
                accident=AccidentAlertPreferences(
                    alert=row["alert_accident"],
                    audio=row["alert_accident_audio"],
                ),
                speeding=SpeedingAlertPreferences(
                    alert=row["alert_speeding"],
                    audio=row["alert_speeding_audio"],
                ),
                weather=WeatherAlertPreferences(
                    alert=row["alert_weather"],
                    audio=row["alert_weather_audio"],
                ),
                overtaking=OvertakingAlertPreferences(
                    alert=row["alert_overtaking"],
                    audio=row["alert_overtaking_audio"],
                ),
                emergency_vehicle=EmergencyVehicleAlertPreferences(
                    alert=row["alert_emergency_vehicle"],
                    audio=row["alert_emergency_vehicle_audio"],
                ),
                navigation=NavigationAlertPreferences(
                    alert=row["alert_navigation"],
                    audio=row["alert_navigation_audio"],
                ),
                traffic=TrafficAlertPreferences(
                    alert=row["alert_traffic"],
                    audio=row["alert_traffic_audio"],
                ),
                maneuver=ManeuverAlertPreferences(
                    alert=row["alert_maneuver"],
                    audio=row["alert_maneuver_audio"],
                ),
            ),
            weather=WeatherPreferences(
                feels_like=row["weather_feels_like"],
                wind=row["weather_wind"],
                humidity=row["weather_humidity"],
                pressure=row["weather_pressure"],
                visibility=row["weather_visibility"],
                uv_index=row["weather_uv_index"],
            ),
            updated_at=row["updated_at"],
        )