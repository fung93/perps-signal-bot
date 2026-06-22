-- 004_features_adx.sql — add ADX(14) trend strength to the features table.
-- Backtest showed an ADX>=30 gate turns the break-even rules into a fee-surviving,
-- out-of-sample-positive edge. Applied in order after 003. Never edit a committed migration.

ALTER TABLE features ADD COLUMN IF NOT EXISTS adx NUMERIC(10,4);
