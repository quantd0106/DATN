#!/bin/sh
set -eu

: "${PGUSER:?Set PGUSER}" "${DOCUMENT_DB_USER:?Set DOCUMENT_DB_USER}" \
    "${DOCUMENT_DB_PASSWORD:?Set DOCUMENT_DB_PASSWORD}"

exec psql -X -v ON_ERROR_STOP=1 \
    -v doc_user="$DOCUMENT_DB_USER" -v doc_pass="$DOCUMENT_DB_PASSWORD" <<'SQL'
BEGIN;

-- A dedicated login must never change the bootstrap role or be able to assume another role.
SELECT :'doc_user' = current_user::text
    OR octet_length(:'doc_user') > current_setting('max_identifier_length')::integer
    OR EXISTS (
        SELECT 1
        FROM pg_roles AS r
        WHERE r.rolname = :'doc_user'
          AND (r.rolsuper OR EXISTS (SELECT 1 FROM pg_auth_members WHERE member = r.oid))
    ) AS invalid_document_role
\gset
\if :invalid_document_role
DO $$ BEGIN
    RAISE EXCEPTION 'DOCUMENT_DB_USER must identify a separate, unprivileged role without memberships';
END $$;
\endif

SELECT format('%s ROLE %I WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD %L',
    CASE WHEN EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'doc_user')
         THEN 'ALTER' ELSE 'CREATE' END,
    :'doc_user', :'doc_pass')
\gexec

-- PostgreSQL checks database CREATE even when Alembic's CREATE SCHEMA IF NOT EXISTS is a no-op.
SELECT format('GRANT CONNECT, CREATE ON DATABASE %I TO %I', current_database(), :'doc_user'),
       format('CREATE SCHEMA IF NOT EXISTS document AUTHORIZATION %I', :'doc_user'),
       format('ALTER SCHEMA document OWNER TO %I', :'doc_user'),
       format('GRANT USAGE, CREATE ON SCHEMA document TO %I', :'doc_user'),
       format('ALTER ROLE %I SET search_path = document', :'doc_user'),
       format('REVOKE ALL ON SCHEMA public FROM %I', :'doc_user')
\gexec

SELECT EXISTS (
    SELECT 1 FROM information_schema.schemata WHERE schema_name = 'core'
) AS core_schema_exists
\gset
\if :core_schema_exists
SELECT format('REVOKE ALL ON SCHEMA core FROM %I', :'doc_user'),
       format('REVOKE ALL ON ALL TABLES IN SCHEMA core FROM %I', :'doc_user'),
       format('REVOKE ALL ON ALL SEQUENCES IN SCHEMA core FROM %I', :'doc_user'),
       format('ALTER DEFAULT PRIVILEGES IN SCHEMA core REVOKE ALL ON TABLES FROM %I', :'doc_user')
\gexec

-- Fail on residual access (for example, a legacy grant to PUBLIC).
SELECT has_schema_privilege(r.oid, n.oid, 'USAGE, CREATE') OR n.nspowner = r.oid
    AS document_has_core_access
FROM pg_roles AS r CROSS JOIN pg_namespace AS n
WHERE r.rolname = :'doc_user' AND n.nspname = 'core'
\gset
\if :document_has_core_access
DO $$ BEGIN
    RAISE EXCEPTION 'Document role still has privileges on the Core schema';
END $$;
\endif
\endif

COMMIT;
SQL
