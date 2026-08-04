\echo 'Initializing the disposable Fengmou PostgreSQL acceptance role'

CREATE ROLE fengmou_app
    LOGIN
    PASSWORD 'local-postgres-app-acceptance-only'
    NOSUPERUSER
    NOCREATEDB
    NOCREATEROLE
    NOREPLICATION
    NOBYPASSRLS;

ALTER DATABASE fengmou_acceptance OWNER TO fengmou_app;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
ALTER DATABASE fengmou_acceptance SET timezone TO 'UTC';
