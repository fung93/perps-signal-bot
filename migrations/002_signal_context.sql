-- 002_signal_context.sql — store each signal's rationale + input snapshot inline, so a
-- logged signal is self-describing for later review (no features join needed).
-- Applied in order after 001_init.sql. Never edit a committed migration; add a new one.

ALTER TABLE signals ADD COLUMN IF NOT EXISTS context JSONB;
