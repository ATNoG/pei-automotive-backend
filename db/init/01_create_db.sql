\set ON_ERROR_STOP on
\getenv app_db_user APP_DB_USER
\getenv app_db_password APP_DB_PASSWORD

SELECT format(
    'CREATE ROLE %I LOGIN PASSWORD %L',
    :'app_db_user',
    :'app_db_password'
)
WHERE NOT EXISTS (
    SELECT 1 FROM pg_roles WHERE rolname = :'app_db_user'
)
\gexec

SELECT format('CREATE DATABASE %I OWNER %I', 'automotive_os', :'app_db_user')
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'automotive_os')
\gexec
