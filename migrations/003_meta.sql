-- 003_meta.sql — small key/value store for one-off flags (e.g. the readiness alert).
-- Applied in order after 002. Never edit a committed migration; add a new one.

CREATE TABLE IF NOT EXISTS meta (
    key        TEXT        PRIMARY KEY,
    value      TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
