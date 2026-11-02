USE ROLE ACCOUNTADMIN;
USE DATABASE FLEETPULSE;
USE WAREHOUSE FLEETPULSE_WH;

-- Latest state per robot per run, with silence detection measured in event time.
CREATE OR REPLACE DYNAMIC TABLE MARTS.ROBOT_STATUS
  TARGET_LAG = '1 minute'
  WAREHOUSE  = FLEETPULSE_WH
AS
WITH ranked AS (
  SELECT *,
    ROW_NUMBER() OVER (PARTITION BY run_id, robot_id ORDER BY event_ts DESC, seq DESC) AS rn,
    MAX(event_ts) OVER (PARTITION BY run_id)                                           AS run_latest_ts,
    COUNT(*)      OVER (PARTITION BY run_id, robot_id)                                 AS records
  FROM STAGING.STG_TELEMETRY
)
SELECT
  run_id, robot_id,
  event_ts                                        AS last_seen_ts,
  run_latest_ts,
  DATEDIFF('second', event_ts, run_latest_ts)     AS silent_for_s,
  DATEDIFF('second', event_ts, run_latest_ts) > 30 AS is_silent,   -- threshold: 30 event-time seconds
  battery_pct, motor_current, motor_temp, status, error_code, records
FROM ranked
WHERE rn = 1;

-- Fleet-level metrics per minute of event time.
CREATE OR REPLACE DYNAMIC TABLE MARTS.FLEET_MINUTELY
  TARGET_LAG = '1 minute'
  WAREHOUSE  = FLEETPULSE_WH
AS
SELECT
  run_id,
  DATE_TRUNC('minute', event_ts)               AS minute_ts,
  COUNT(*)                                     AS records,
  COUNT(DISTINCT robot_id)                     AS reporting_robots,
  AVG(battery_pct)                             AS avg_battery_pct,
  AVG(motor_temp)                              AS avg_motor_temp,
  MAX(motor_temp)                              AS max_motor_temp,
  COUNT_IF(error_code <> 0)                    AS error_records,
  AVG(IFF(status = 'moving', 1, 0))            AS share_moving,
  AVG(IFF(status = 'charging', 1, 0))          AS share_charging
FROM STAGING.STG_TELEMETRY
GROUP BY run_id, DATE_TRUNC('minute', event_ts);

SHOW DYNAMIC TABLES IN SCHEMA MARTS;
SELECT * FROM MARTS.ROBOT_STATUS WHERE is_silent ORDER BY silent_for_s DESC;
SELECT * FROM MARTS.FLEET_MINUTELY ORDER BY run_id, minute_ts LIMIT 20;
