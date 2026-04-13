from pydantic import BaseModel, Field
from datetime import datetime
from uuid import UUID


class AccidentAlertPreferences(BaseModel):
    alert: bool = True
    audio: bool = True


class SpeedingAlertPreferences(BaseModel):
    alert: bool = True
    audio: bool = True


class WeatherAlertPreferences(BaseModel):
    alert: bool = True
    audio: bool = True


class OvertakingAlertPreferences(BaseModel):
    alert: bool = True
    audio: bool = True


class EmergencyVehicleAlertPreferences(BaseModel):
    alert: bool = True
    audio: bool = True


class NavigationAlertPreferences(BaseModel):
    alert: bool = True
    audio: bool = True


class TrafficAlertPreferences(BaseModel):
    alert: bool = True
    audio: bool = True


class ManeuverAlertPreferences(BaseModel):
    alert: bool = True
    audio: bool = True


class AlertPreferences(BaseModel):
    accident: AccidentAlertPreferences = Field(default_factory=AccidentAlertPreferences)
    speeding: SpeedingAlertPreferences = Field(default_factory=SpeedingAlertPreferences)
    weather: WeatherAlertPreferences = Field(default_factory=WeatherAlertPreferences)
    overtaking: OvertakingAlertPreferences = Field(default_factory=OvertakingAlertPreferences)
    emergency_vehicle: EmergencyVehicleAlertPreferences = Field(default_factory=EmergencyVehicleAlertPreferences)
    navigation: NavigationAlertPreferences = Field(default_factory=NavigationAlertPreferences)
    traffic: TrafficAlertPreferences = Field(default_factory=TrafficAlertPreferences)
    maneuver: ManeuverAlertPreferences = Field(default_factory=ManeuverAlertPreferences)


class WeatherPreferences(BaseModel):
    feels_like: bool = False
    wind: bool = False
    humidity: bool = False
    pressure: bool = False
    visibility: bool = False
    uv_index: bool = False


class AppearancePreferences(BaseModel):
    dark_mode: bool = True
    colorblind_enabled: bool = False
    language: str = "en"


# What the Frontend sends on update (all fields optional — partial update)
class UpdatePreferencesRequest(BaseModel):
    appearance: AppearancePreferences | None = None
    alerts: AlertPreferences | None = None
    weather: WeatherPreferences | None = None


# What the API returns (always the full preference state)
class PreferencesResponse(BaseModel):
    user_id: UUID
    appearance: AppearancePreferences
    alerts: AlertPreferences
    weather: WeatherPreferences
    updated_at: datetime