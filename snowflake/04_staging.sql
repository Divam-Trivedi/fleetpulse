-- Typed, deduplicated layer on top of the VARIANT landing table.
USE ROLE ACCOUNTADMIN;
USE DATABASE FLEETPULSE;
USE WAREHOUSE FLEETPULSE_WH;
CREATE SCHEMA IF NOT EXISTS STAGING;
CREATE SCHEMA IF NOT EXISTS MARTS;

-- Dev setting: 1 minute lag. Raise to 5+ minutes (or pause) to save credits.
CREATE OR REPLACE DYNAMIC TABLE STAGING.STG_TELEMETRY
  TARGET_LAG = '1 minute'
  WAREHOUSE  = FLEETPULSE_WH
AS
SELECT
  record:run_id::STRING                      AS run_id,
  record:robot_id::STRING                    AS robot_id,
  record:seq::NUMBER                         AS seq,
  record:ts::TIMESTAMP_TZ::TIMESTAMP_NTZ     AS event_ts,      -- UTC event time
  record:x::FLOAT                            AS x,
  record:y::FLOAT                            AS y,
  record:heading_deg::FLOAT                  AS heading_deg,
  record:speed::FLOAT                        AS speed,
  record:battery_pct::FLOAT                  AS battery_pct,
  record:motor_current::FLOAT                AS motor_current,
  record:motor_temp::FLOAT                   AS motor_temp,
  record:error_code::NUMBER                  AS error_code,
  record:status::STRING                      AS status,
  loaded_at,
  source_file
FROM RAW.TELEMETRY_RAW
WHERE record:robot_id IS NOT NULL AND record:run_id IS NOT NULL
QUALIFY ROW_NUMBER() OVER (PARTITION BY run_id, robot_id, seq ORDER BY loaded_at) = 1;

SHOW DYNAMIC TABLES IN SCHEMA STAGING;
SELECT COUNT(*) AS staged_rows FROM STAGING.STG_TELEMETRY;
