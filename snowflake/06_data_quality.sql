USE ROLE ACCOUNTADMIN;
USE DATABASE FLEETPULSE;
USE WAREHOUSE FLEETPULSE_WH;

CREATE OR REPLACE VIEW MARTS.DQ_SUMMARY AS
SELECT 'raw_rows' AS check_name, COUNT(*) AS value, 'info' AS severity
  FROM RAW.TELEMETRY_RAW
UNION ALL
SELECT 'staging_rows', COUNT(*), 'info' FROM STAGING.STG_TELEMETRY
UNION ALL
SELECT 'raw_minus_staging_rows',
       (SELECT COUNT(*) FROM RAW.TELEMETRY_RAW) - COUNT(*), 'info'
  FROM STAGING.STG_TELEMETRY
UNION ALL
SELECT 'battery_out_of_range', COUNT_IF(battery_pct < 0 OR battery_pct > 100), 'error'
  FROM STAGING.STG_TELEMETRY
UNION ALL
SELECT 'negative_motor_current', COUNT_IF(motor_current < 0), 'error'
  FROM STAGING.STG_TELEMETRY
UNION ALL
SELECT 'unknown_status', COUNT_IF(status NOT IN ('moving','returning','charging','fault')), 'error'
  FROM STAGING.STG_TELEMETRY
UNION ALL
-- seq counts published records per robot, so gaps mean records lost in transit
SELECT 'missing_sequence_numbers', COALESCE(SUM(max_seq - cnt), 0), 'error'
  FROM (SELECT run_id, robot_id, MAX(seq) AS max_seq, COUNT(*) AS cnt
        FROM STAGING.STG_TELEMETRY GROUP BY run_id, robot_id);

SELECT * FROM MARTS.DQ_SUMMARY;
