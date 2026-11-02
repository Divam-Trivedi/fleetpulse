-- Per-robot, per-minute features for predictive maintenance (event-time windows).
-- Every robot is compared with ITS OWN commissioning baseline (minutes 3-10 of the run), because
-- absolute temperature and current differ by zone and payload. Run as ACCOUNTADMIN once.
USE ROLE ACCOUNTADMIN;
USE DATABASE FLEETPULSE;
USE WAREHOUSE FLEETPULSE_WH;
CREATE SCHEMA IF NOT EXISTS ML;

CREATE OR REPLACE DYNAMIC TABLE ML.FEATURES_1MIN
  TARGET_LAG = '5 minutes'
  WAREHOUSE  = FLEETPULSE_WH
AS
WITH run_bounds AS (
  SELECT run_id, MIN(event_ts) AS run_start
  FROM STAGING.STG_TELEMETRY
  GROUP BY run_id
),
robot_base AS (
  SELECT s.run_id, s.robot_id,
         AVG(CASE WHEN s.speed > 0.5 THEN s.motor_current END) AS base_current,
         AVG(s.motor_temp)                                     AS base_temp
  FROM STAGING.STG_TELEMETRY s
  JOIN run_bounds r ON s.run_id = r.run_id
  WHERE s.event_ts >= r.run_start + INTERVAL '3 minutes'     -- skip thermal warm-up
    AND s.event_ts <  r.run_start + INTERVAL '10 minutes'
  GROUP BY s.run_id, s.robot_id
),
per_minute AS (
  SELECT s.run_id, s.robot_id,
         DATE_TRUNC('minute', s.event_ts)                                         AS window_start,
         -- warm-up + baseline period is minutes 0-10; models only score from minute 15 (see 09 and 10)
         DATEDIFF('minute', MAX(r.run_start), DATE_TRUNC('minute', s.event_ts))   AS minutes_since_start,
         COUNT(*)                                                                 AS n_records,
         AVG(s.motor_temp)                                                        AS avg_temp,
         -- 1.0 = same current as this robot drew when new; 1.4 = 40% more
         AVG(CASE WHEN s.speed > 0.5 THEN s.motor_current END) / MAX(b.base_current) AS current_rel,
         AVG(s.motor_temp) - MAX(b.base_temp)                                     AS temp_rise,
         COUNT_IF(s.error_code IN (101, 102))                                     AS fault_count,
         -- share of the minute spent returning to / sitting on the charger (a thermal disturbance)
         AVG(CASE WHEN s.status IN ('charging', 'returning') THEN 1.0 ELSE 0.0 END) AS charge_share,
         MIN(CASE WHEN s.error_code = 201 THEN s.event_ts END)                    AS first_overtemp_in_window
  FROM STAGING.STG_TELEMETRY s
  JOIN robot_base b ON s.run_id = b.run_id AND s.robot_id = b.robot_id
  JOIN run_bounds r ON s.run_id = r.run_id
  GROUP BY s.run_id, s.robot_id, DATE_TRUNC('minute', s.event_ts)
),
rolled AS (
  SELECT p.*,
    AVG(current_rel) OVER (PARTITION BY run_id, robot_id ORDER BY window_start
                           ROWS BETWEEN 4 PRECEDING AND CURRENT ROW) AS current_rel_5m,
    AVG(temp_rise)   OVER (PARTITION BY run_id, robot_id ORDER BY window_start
                           ROWS BETWEEN 4 PRECEDING AND CURRENT ROW) AS temp_rise_5m,
    SUM(fault_count) OVER (PARTITION BY run_id, robot_id ORDER BY window_start
                           ROWS BETWEEN 9 PRECEDING AND CURRENT ROW) AS fault_count_10m,
    -- >0 means a charging stop happened in the last 15 minutes: cool-down and re-warm look like degradation
    MAX(charge_share) OVER (PARTITION BY run_id, robot_id ORDER BY window_start
                           ROWS BETWEEN 14 PRECEDING AND CURRENT ROW) AS charge_share_15m,
    -- has this robot already hit overtemp (error 201) in or before this window?
    MAX(first_overtemp_in_window) OVER (PARTITION BY run_id, robot_id ORDER BY window_start
                           ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) IS NOT NULL AS tripped_so_far
  FROM per_minute p
)
-- Deltas compare with the window exactly 5 minutes earlier (NULL if that window is missing),
-- so gaps in the data cannot silently stretch the comparison period.
SELECT r.*,
  r.current_rel_5m - prev.current_rel_5m AS current_rel_delta_5m,
  r.temp_rise_5m   - prev.temp_rise_5m   AS temp_rise_delta_5m
FROM rolled r
LEFT JOIN rolled prev
  ON prev.run_id = r.run_id AND prev.robot_id = r.robot_id
 AND prev.window_start = r.window_start - INTERVAL '5 minutes';

GRANT SELECT ON DYNAMIC TABLE ML.FEATURES_1MIN TO ROLE FLEETPULSE_DEV;
