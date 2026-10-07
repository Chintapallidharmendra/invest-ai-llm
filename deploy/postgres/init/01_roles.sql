-- invest-ai-llm database roles (Story 1.3; ADR-005, ADR-023).
-- Runs once, as the superuser, when /data/postgres is empty (docker-entrypoint-initdb.d).
--
--   app_migrator  owns the schema; runs Alembic DDL.
--   app_rw        DML only. NOBYPASSRLS and never a table owner, so RLS always applies.
--
-- Passwords come from Compose secrets; psql reads them with backtick \set.

\set ON_ERROR_STOP on
\set migrator_password `cat /run/secrets/db_migrator_password`
\set rw_password `cat /run/secrets/db_app_rw_password`

CREATE ROLE app_migrator LOGIN PASSWORD :'migrator_password'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
CREATE ROLE app_rw LOGIN PASSWORD :'rw_password'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS NOINHERIT;

\unset migrator_password
\unset rw_password

-- Database: only the two app roles may connect; no TEMP for PUBLIC.
REVOKE ALL ON DATABASE invest_ai FROM PUBLIC;
GRANT CONNECT ON DATABASE invest_ai TO app_migrator, app_rw;

\connect invest_ai

-- Extensions that need superuser (pgvector is not a trusted extension). The baseline
-- migration's CREATE EXTENSION IF NOT EXISTS is then a no-op.
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS citext;

-- Schema: owned by app_migrator; app_rw may only use it.
REVOKE ALL ON SCHEMA public FROM PUBLIC;
ALTER SCHEMA public OWNER TO app_migrator;
GRANT USAGE ON SCHEMA public TO app_rw;

-- Objects app_migrator creates get DML grants for app_rw. Tables that must be
-- append-only (audit_events, Story 7.1) REVOKE UPDATE, DELETE explicitly.
ALTER DEFAULT PRIVILEGES FOR ROLE app_migrator IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO app_rw;
ALTER DEFAULT PRIVILEGES FOR ROLE app_migrator IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO app_rw;
ALTER DEFAULT PRIVILEGES FOR ROLE app_migrator IN SCHEMA public
    GRANT EXECUTE ON FUNCTIONS TO app_rw;
