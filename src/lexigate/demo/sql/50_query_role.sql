-- SPDX-License-Identifier: Apache-2.0
-- Copyright 2026 LexiGate contributors
--
-- Low-privilege role for model-generated SQL (path 2). NOLOGIN: the service switches to it with
-- SET LOCAL ROLE inside a read-only transaction. It may only call the sem.* functions; it has no
-- privilege on schema demo (raw tables), so even a query that slipped past the SQL guard cannot read rows.
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'lexigate_query') THEN
    CREATE ROLE lexigate_query NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
  END IF;
END $$;
REVOKE ALL ON SCHEMA demo FROM lexigate_query;
REVOKE ALL ON ALL TABLES IN SCHEMA demo FROM lexigate_query;
GRANT USAGE ON SCHEMA sem TO lexigate_query;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA sem TO lexigate_query;
