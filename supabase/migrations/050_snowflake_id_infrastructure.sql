-- 050_snowflake_id_infrastructure.sql
-- Snowflake ID generator for business tables
-- 53-bit structure: timestamp(41) | sequence(12)
-- Fits within JavaScript Number.MAX_SAFE_INTEGER (2^53 - 1 = 9007199254740991)
-- Custom epoch: 2024-01-01 00:00:00 UTC (1704067200000 ms)
-- Yields ~69 years of unique IDs from epoch, 4096 IDs per ms

-- ============================================================================
-- Part 1: Sequence for the 12-bit counter (0-4095)
-- ============================================================================

CREATE SEQUENCE IF NOT EXISTS snowflake_seq
  CYCLE
  MINVALUE 0
  MAXVALUE 4095;

-- ============================================================================
-- Part 2: Snowflake ID generator function (53-bit safe)
-- ============================================================================

CREATE OR REPLACE FUNCTION generate_snowflake_id()
RETURNS BIGINT AS $$
DECLARE
  epoch BIGINT := 1704067200000;  -- 2024-01-01 00:00:00 UTC in ms
  now_ms BIGINT;
  seq INT;
  result BIGINT;
BEGIN
  now_ms := (EXTRACT(EPOCH FROM clock_timestamp()) * 1000)::BIGINT - epoch;
  seq := nextval('snowflake_seq') % 4096;
  result := (now_ms << 12) | seq;
  RETURN result;
END;
$$ LANGUAGE plpgsql;

-- ============================================================================
-- Verify:
--   SELECT generate_snowflake_id();  -- should return BIGINT <= 9007199254740991
--   SELECT generate_snowflake_id() != generate_snowflake_id();  -- true (unique)
--   SELECT generate_snowflake_id() <= 9007199254740991;  -- true (JS safe)
-- ============================================================================
