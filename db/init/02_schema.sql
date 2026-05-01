\connect automotive_os

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY,
    username VARCHAR(100) NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS vehicles (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    plate VARCHAR(20) UNIQUE NOT NULL,
    alias VARCHAR(100)                          -- "My Car", "Work Van"
);

CREATE INDEX idx_vehicles_user_id ON vehicles(user_id);

CREATE TABLE IF NOT EXISTS user_preferences (
    user_id UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,

    -- Appearance
    dark_mode BOOLEAN NOT NULL DEFAULT TRUE,
    colorblind_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    language VARCHAR(10) NOT NULL DEFAULT 'en',

    -- Alert toggles
    alert_accident BOOLEAN NOT NULL DEFAULT TRUE,
    alert_accident_audio BOOLEAN NOT NULL DEFAULT TRUE,
    alert_speeding BOOLEAN NOT NULL DEFAULT TRUE,
    alert_speeding_audio BOOLEAN NOT NULL DEFAULT TRUE,
    alert_weather BOOLEAN NOT NULL DEFAULT TRUE,
    alert_weather_audio BOOLEAN NOT NULL DEFAULT TRUE,
    alert_overtaking BOOLEAN NOT NULL DEFAULT TRUE,
    alert_overtaking_audio BOOLEAN NOT NULL DEFAULT TRUE,
    alert_emergency_vehicle BOOLEAN NOT NULL DEFAULT TRUE,
    alert_emergency_vehicle_audio BOOLEAN NOT NULL DEFAULT TRUE,
    alert_navigation BOOLEAN NOT NULL DEFAULT TRUE,
    alert_navigation_audio BOOLEAN NOT NULL DEFAULT TRUE,
    alert_traffic BOOLEAN NOT NULL DEFAULT TRUE,
    alert_traffic_audio BOOLEAN NOT NULL DEFAULT TRUE,
    alert_maneuver BOOLEAN NOT NULL DEFAULT TRUE,
    alert_maneuver_audio BOOLEAN NOT NULL DEFAULT TRUE,

    -- Weather display toggles
    weather_feels_like BOOLEAN NOT NULL DEFAULT FALSE,
    weather_wind BOOLEAN NOT NULL DEFAULT FALSE,
    weather_humidity BOOLEAN NOT NULL DEFAULT FALSE,
    weather_pressure BOOLEAN NOT NULL DEFAULT FALSE,
    weather_visibility BOOLEAN NOT NULL DEFAULT FALSE,
    weather_uv_index BOOLEAN NOT NULL DEFAULT FALSE,

    updated_at TIMESTAMPTZ DEFAULT NOW()
);

GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO automotive_app;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO automotive_app;
\getenv app_db_user APP_DB_USER

SELECT format('GRANT USAGE ON SCHEMA public TO %I;', :'app_db_user')
\gexec

SELECT format(
    'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO %I;',
    :'app_db_user'
)
\gexec

SELECT format('GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO %I;', :'app_db_user')
\gexec

SELECT format(
    'ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO %I;',
    :'app_db_user'
)
\gexec

SELECT format(
    'ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO %I;',
    :'app_db_user'
)
\gexec
